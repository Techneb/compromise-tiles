"""Binary and archive readers: GeoTIFF, WKB and GeoPackage, Shapefile, 3DS meshes, XLSX, Atom feeds."""
import array, io, math, os, re, shutil, sqlite3, struct, sys, time, xml.etree.ElementTree as ET, zlib
from builder.cells import M
from builder.net import download, get, log, RANGE_CHUNK, Ranged, SourceError
from builder.projections import from_tm, to_tm, wkt_projection
from builder.shapes import building, number, vertex

def _atom_entries(url):
    """(title, href, [s, w, n, e], crs) per entry of a cadastre ATOM feed (regex: the feeds are not always well-formed XML)."""
    raw = get(url)
    declared = re.match(rb'<\?xml[^>]*encoding="([^"]+)"', raw)   # the province feeds are ISO-8859-1 (Carreño, Logroño)
    text = raw.decode(declared.group(1).decode() if declared else "utf-8", "replace")
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", text, re.S):
        title = re.search(r"<title>([^<]*)", entry); href = re.search(r'href="([^"]+)"', entry)
        poly = re.search(r"<georss:polygon>([^<]*)", entry); crs = re.search(r'term="[^"]*EPSG/0/(\d+)"', entry)
        if not (title and href and poly): continue
        v = [float(x) for x in poly.group(1).split()]
        lats, lons = v[0::2], v[1::2]
        out.append((title.group(1).strip(), href.group(1).strip(), (min(lats), min(lons), max(lats), max(lons)), crs.group(1) if crs else "25830"))
    return out
_geopackages = {}  # zip URL → (its GeoPackage opened read-only, table, geometry column), unpacked once a run

def wkb_polygons(raw, at=0):
    """The polygons of an ISO WKB Polygon or MultiPolygon as [[ring of (x, y)]] (a Z or M ignored), and where it ends."""
    order = "<" if raw[at] == 1 else ">"
    kind, = struct.unpack_from(order + "I", raw, at + 1)
    dims, kind, at = (2, 3, 3, 4)[kind // 1000], kind % 1000, at + 5
    count, = struct.unpack_from(order + "I", raw, at)
    at += 4
    if kind == 6:
        out = []
        for _ in range(count):
            polygons, at = wkb_polygons(raw, at)
            out += polygons
        return out, at
    if kind != 3: raise ValueError(f"WKB type {kind}: not a polygon")
    rings = []
    for _ in range(count):
        points, = struct.unpack_from(order + "I", raw, at)
        v = struct.unpack_from(f"{order}{points * dims}d", raw, at + 4)
        rings.append([(v[i], v[i + 1]) for i in range(0, len(v), dims)])
        at += 4 + 8 * points * dims
    return [rings], at

def gpkg_polygons(blob):
    """A GeoPackage geometry: the "GP" header (version, flags, SRS id, an envelope of 0, 4, 6 or 8 doubles), then WKB."""
    return wkb_polygons(blob, 8 + (0, 32, 48, 48, 64)[(blob[3] >> 1) & 7])[0]

def geopackage(url, height):
    """A fetch over one zipped GeoPackage of footprints in lon/lat (NRCan's are NAD83(CSRS), WGS84 to a metre),
    downloaded once (kept a year) and queried through its R-tree: each footprint whose box meets the rect,
    its `height` column (metres above ground) as the height, the flagged 15 m guess when it reads none."""
    def fetch(rect):
        if url not in _geopackages:
            import zipfile
            folder = os.path.join("extracts", "geopackage")
            archive = download(url, folder, max_days=365)
            if not zipfile.is_zipfile(archive):
                os.remove(archive)
                raise SourceError(f"{url}: not a zip")
            with zipfile.ZipFile(archive) as z:
                member = next(m for m in z.namelist() if m.endswith(".gpkg"))
                file = os.path.join(folder, os.path.basename(member))
                if not os.path.exists(file) or os.path.getmtime(file) < os.path.getmtime(archive):
                    with z.open(member) as src, open(file + ".tmp", "wb") as dst: shutil.copyfileobj(src, dst, 1 << 20)
                    os.replace(file + ".tmp", file)
            db = sqlite3.connect(f"file:{file}?mode=ro", uri=True)
            # The file's own names go into SQL below as quoted identifiers: a `"` inside one is doubled (Strix, 2026-10-26).
            _geopackages[url] = (db, *(name.replace('"', '""') for name in
                                       db.execute("SELECT table_name, column_name FROM gpkg_geometry_columns").fetchone()))
        db, table, column = _geopackages[url]
        s, w, n, e = rect
        rows = db.execute(f'SELECT t."{column}", t."{height}" FROM "{table}" t JOIN "rtree_{table}_{column}" r ON t.rowid = r.id'
                          " WHERE r.minx <= ? AND r.maxx >= ? AND r.miny <= ? AND r.maxy >= ?", (e, w, n, s))
        return [building([vertex(y, x) for x, y in polygon[0]], h if h and h > 0 else None)
                for blob, h in rows if blob for polygon in gpkg_polygons(blob) if polygon and len(polygon[0]) >= 3]
    return fetch
SHAPE_BUCKET = 250      # metres (or 1/400°) a side of a Shapefile's in-memory index of record boxes
MIN_RING = 1            # m²: a Shapefile ring under this is a sliver, left out
_shapefiles = {}        # (url, layer) → its Shapefile, opened once a run

def zip_layer(url, layer, folder, headers=None):
    """The path (without extension) of one Shapefile layer of a remote zip, its members fetched by range once a year:
    the members named `layer` (any folder inside the zip) with extension .shp, .shx, .dbf, .prj or .cpg."""
    folder = os.path.join("extracts", "shapefile", folder)
    base = os.path.join(folder, layer)
    if all(os.path.exists(base + x) and time.time() - os.path.getmtime(base + x) < 365 * 86_400 for x in (".shp", ".shx", ".dbf", ".prj")):
        return base
    import zipfile
    os.makedirs(folder, exist_ok=True)
    log(f"  fetching {layer} from {url}")
    with zipfile.ZipFile(io.BufferedReader(Ranged(url, headers() if callable(headers) else headers or {}), RANGE_CHUNK)) as z:
        members = [m for m in z.namelist() if os.path.splitext(m.rsplit("/", 1)[-1]) in
                   ((layer, x) for x in (".shp", ".shx", ".dbf", ".prj", ".cpg"))]
        if len({os.path.splitext(m)[1] for m in members} - {".cpg"}) < 4: raise SourceError(f"{url}: no complete layer {layer}")
        for m in members:
            file = base + os.path.splitext(m)[1]
            with z.open(m) as src, open(file + ".tmp", "wb") as dst: shutil.copyfileobj(src, dst, 1 << 20)
            os.replace(file + ".tmp", file)
    return base

class Dbf:
    """A layer's attribute table (.dbf, its .cpg's encoding), read in place."""
    def __init__(self, base):
        encoding = "latin-1"
        if os.path.exists(base + ".cpg"):
            with open(base + ".cpg", encoding="ascii") as f: encoding = {"utf-8": "utf-8", "utf8": "utf-8"}.get(f.read().strip().lower(), "latin-1")
        self.encoding, self.dbf = encoding, open(base + ".dbf", "rb")
        head = self.dbf.read(32)
        self.count, self.header, self.length = struct.unpack_from("<IHH", head, 4)
        self.fields, at = {}, 1
        for k in range((self.header - 33) // 32):
            f = self.dbf.read(32)
            if f[0] == 0x0D: break
            self.fields[f[:11].split(b"\0")[0].decode("latin-1")] = (at, f[16], chr(f[11]))
            at += f[16]

    def attributes(self, k, names):
        """Record k's fields `names` from the .dbf: numbers as floats (blank: None), text stripped; None if deleted."""
        self.dbf.seek(self.header + k * self.length)
        raw = self.dbf.read(self.length)
        if raw[:1] == b"*": return None
        out = {}
        for name in names:
            at, size, kind = self.fields[name]
            text = raw[at:at + size].decode(self.encoding, "replace").strip()
            out[name] = (number(text) if text else None) if kind in "NF" else text
        return out

    def table(self, names):
        """Every record's fields `names`, in order (a deleted one as None)."""
        return [self.attributes(k, names) for k in range(self.count)]

class Shapefile(Dbf):
    """One polygon layer (.shp, .shx, .dbf, .prj; Polygon, PolygonZ or PolygonM, a Z or M ignored), read in place.
    Opening it reads every record's box once (from the .shp at each .shx offset) into a grid of SHAPE_BUCKET cells."""
    def __init__(self, base):
        with open(base + ".prj", encoding="latin-1") as f: self.tm = wkt_projection(f.read())
        super().__init__(base)
        self.shp = open(base + ".shp", "rb")
        with open(base + ".shx", "rb") as f: shx = f.read()
        self.offsets = array.array("q", (2 * struct.unpack_from(">i", shx, 100 + 8 * k)[0] for k in range((len(shx) - 100) // 8)))
        self.size = 1 / 400 if self.tm is None else SHAPE_BUCKET
        self.boxes, self.grid = array.array("d"), {}
        for k, offset in enumerate(self.offsets):
            self.shp.seek(offset + 8)
            kind, *box = struct.unpack("<i4d", self.shp.read(36))
            self.boxes.extend(box if kind else (0, 0, -1, -1))
            if not kind: continue
            for gx in range(math.floor(box[0] / self.size), math.floor(box[2] / self.size) + 1):
                for gy in range(math.floor(box[1] / self.size), math.floor(box[3] / self.size) + 1):
                    self.grid.setdefault((gx, gy), []).append(k)

    def box(self, rect):
        """A lat/lon rect in the file's coordinates: (x0, y0, x1, y1) around its corners and edge midpoints."""
        s, w, n, e = rect
        if self.tm is None: return w, s, e, n
        points = [to_tm(self.tm, lat, lon) for lat in (s, (s + n) / 2, n) for lon in (w, (w + e) / 2, e)]
        return min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points), max(y for _, y in points)

    def rings(self, k):
        """Record k's outer rings (clockwise, the Shapefile's rule; holes left out) as [(latitude, longitude)]. A ring
        under MIN_RING m² is left out: a sliver (half the volumes in Buenos Aires' densest cell, Barrio Padre Mugica)."""
        self.shp.seek(self.offsets[k] + 8)
        kind, = struct.unpack("<i", self.shp.read(4))
        if kind not in (5, 15, 25): return []
        body = self.shp.read(32 + 8)
        parts, points = struct.unpack_from("<ii", body, 32)
        starts = struct.unpack(f"<{parts}i", self.shp.read(4 * parts)) + (points,)
        xy = struct.unpack(f"<{2 * points}d", self.shp.read(16 * points))
        out = []
        for a, b in zip(starts, starts[1:]):
            ring = [(xy[2 * i], xy[2 * i + 1]) for i in range(a, b)]
            twice = -sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(ring, ring[1:])) if len(ring) >= 4 else 0
            if self.tm is None: twice *= M * M * math.cos(math.radians(ring[0][1]))
            if twice < 2 * MIN_RING: continue
            out.append([(y, x) if self.tm is None else from_tm(self.tm, x, y) for x, y in ring[:-1]])
        return out

    def records(self, rect, names):
        """(attributes, rings) for every record whose box meets the lat/lon rect."""
        x0, y0, x1, y1 = self.box(rect)
        seen = sorted({k for gx in range(math.floor(x0 / self.size), math.floor(x1 / self.size) + 1)
                       for gy in range(math.floor(y0 / self.size), math.floor(y1 / self.size) + 1) for k in self.grid.get((gx, gy), ())})
        for k in seen:
            bx0, by0, bx1, by1 = self.boxes[4 * k:4 * k + 4]
            if bx0 > x1 or bx1 < x0 or by0 > y1 or by1 < y0: continue
            p = self.attributes(k, names)
            if p is not None: yield p, self.rings(k)

def shapefile(url, layer, folder, names, height, headers=None):
    """A fetch over one zipped Shapefile layer (zip_layer(), opened once a run): each record whose box meets the rect,
    `height(attributes, shapefile)` as its height (None: the flagged 15 m guess; False: left out), one building
    per outer ring."""
    def fetch(rect):
        if (url, layer) not in _shapefiles: _shapefiles[(url, layer)] = Shapefile(zip_layer(url, layer, folder, headers))
        f = _shapefiles[(url, layer)]
        out = []
        for p, rings in f.records(rect, names):
            h = height(p, f)
            if h is False: continue
            out += [building([vertex(lat, lon) for lat, lon in ring], h if h and h > 0 else None) for ring in rings]
        return out
    return fetch

def geotiff(raw):
    """A one-band float32 GeoTIFF, tiled or in strips, uncompressed or DEFLATE, no predictor (what the WCS sends):
    (west, north, width, height, array('f') row by row from the north)."""
    order = {b"II": "<", b"MM": ">"}.get(raw[:2])
    if not order or struct.unpack_from(order + "H", raw, 2)[0] != 42: raise SourceError("not a classic TIFF")
    at, = struct.unpack_from(order + "I", raw, 4)
    count, = struct.unpack_from(order + "H", raw, at)
    tags = {}
    for k in range(count):
        tag, kind, n, value = struct.unpack_from(order + "HHI4s", raw, at + 2 + 12 * k)
        code, size = {1: ("B", 1), 3: ("H", 2), 4: ("I", 4), 12: ("d", 8)}.get(kind, (None, 0))
        if code is None: continue
        data = value if n * size <= 4 else raw[struct.unpack(order + "I", value)[0]:][:n * size]
        tags[tag] = struct.unpack(order + code * n, data[:n * size])
    width, height = tags[256][0], tags[257][0]
    if tags.get(258, (32,))[0] != 32 or tags.get(339, (1,))[0] != 3 or tags.get(317, (1,))[0] != 1 or tags.get(277, (1,))[0] != 1:
        raise SourceError("not a one-band float32 GeoTIFF without predictor")
    compression = tags.get(259, (1,))[0]
    if compression not in (1, 8, 32946): raise SourceError(f"TIFF compression {compression}")
    if 34264 in tags: west, north = tags[34264][3], tags[34264][7]          # ModelTransformation
    else: west, north = tags[33922][3], tags[33922][4]                     # ModelTiepoint, pixel (0, 0)
    if 322 in tags: tw, th, offsets, sizes = tags[322][0], tags[323][0], tags[324], tags[325]
    else: tw, th, offsets, sizes = width, tags.get(278, (height,))[0], tags[273], tags[279]
    across = -(-width // tw)
    out = array.array("f", bytes(4 * width * height))
    for k, (start, size) in enumerate(zip(offsets, sizes)):
        block = raw[start:start + size]
        if compression != 1: block = zlib.decompress(block)
        values = array.array("f", block)
        if order != ("<" if sys.byteorder == "little" else ">"): values.byteswap()
        x0, y0 = (k % across) * tw, (k // across) * th
        w = min(tw, width - x0)
        for r in range(min(th, height - y0, len(values) // tw)):
            out[(y0 + r) * width + x0:(y0 + r) * width + x0 + w] = values[r * tw:r * tw + w]
    return west, north, width, height, out

def mesh_faces(raw, a=0, b=None):
    """Every triangle of a 3DS file, as three (x, y, z) vertices: the main, editor, object and mesh chunks walked,
    each mesh's vertex list (0x4110) indexed by its face list (0x4120)."""
    b, vertices = len(raw) if b is None else b, ()
    while a + 6 <= b:
        kind, size = struct.unpack_from("<HI", raw, a)
        if size < 6: return
        if kind in (0x4D4D, 0x3D3D, 0x4100): yield from mesh_faces(raw, a + 6, a + size)
        elif kind == 0x4000: yield from mesh_faces(raw, raw.index(b"\0", a + 6) + 1, a + size)
        elif kind == 0x4110:
            n, = struct.unpack_from("<H", raw, a + 6)
            v = struct.unpack_from(f"<{3 * n}f", raw, a + 8)
            vertices = [v[i:i + 3] for i in range(0, 3 * n, 3)]
        elif kind == 0x4120:
            n, = struct.unpack_from("<H", raw, a + 6)
            f = struct.unpack_from(f"<{4 * n}H", raw, a + 8)
            yield from ((vertices[f[i]], vertices[f[i + 1]], vertices[f[i + 2]]) for i in range(0, 4 * n, 4))
        a += size

def _zip_members(raw):
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(raw))
    return {m: z.read(m) for m in z.namelist() if not m.endswith("/")}

def xlsx_rows(raw):
    """The first sheet of an .xlsx as rows of strings (shared strings resolved), standard library only."""
    members, ns = _zip_members(raw), {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    shared = [("".join(t.itertext())) for t in ET.fromstring(members["xl/sharedStrings.xml"]).findall("m:si", ns)] if "xl/sharedStrings.xml" in members else []
    out = []
    for r in ET.fromstring(members["xl/worksheets/sheet1.xml"]).iter(f"{{{ns['m']}}}row"):
        cells = []
        for c in r.findall("m:c", ns):
            v = c.find("m:v", ns)
            cells.append(shared[int(v.text)] if c.get("t") == "s" and v is not None else v.text if v is not None else "")
        out.append(cells)
    return out
