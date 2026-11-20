"""Building feeds in CityGML and its relatives (Hamburg, Gipuzkoa, Espoo, Estonia, PLATEAU)."""
import gzip, io, math, os, urllib.error, urllib.parse, urllib.request, xml.etree.ElementTree as ET, zipfile
from builder.net import download, get, get_json, SourceError
from builder.projections import cs92_to_wgs84, GK25, lcc3301_to_wgs84, tm_to_wgs84, utm_to_wgs84, wgs84_to_tm, wgs84_to_utm
from builder.shapes import building, from_floors, number, triangles_outline, vertex

def hamburg(rect):
    """LGV Hamburg's LoD2 model (OGC API, CityJSON only, and only in a 3D CRS: ETRS89 / UTM 32N + height,
    whole metres): each building's ground surface as its outline, measuredHeight as its height."""
    s, w, n, e = rect
    q = urllib.parse.urlencode({"f": "cityjson", "bbox": f"{w},{s},{e},{n}", "limit": "10000",
                                "crs": "http://www.opengis.net/def/crs/EPSG/0/5555"})
    root = get_json("https://api.hamburg.de/datasets/v1/lod2_hamburg/collections/building/items?" + q)
    objects = root.get("CityObjects") or {}
    if len(objects) >= 10000:
        ms, me = (s + n) / 2, (w + e) / 2
        return [b for part in ((s, w, ms, me), (s, me, ms, e), (ms, w, n, me), (ms, me, n, e)) for b in hamburg(part)]
    (sx, sy, _), (tx, ty, _) = root["transform"]["scale"], root["transform"]["translate"]
    vertices = root.get("vertices") or []
    out = []
    for o in objects.values():
        for g in o.get("geometry") or []:
            surfaces = (g.get("semantics") or {}).get("surfaces") or []
            for shell, values in zip(g.get("boundaries") or [], (g.get("semantics") or {}).get("values") or []):
                for face, v in zip(shell, values):
                    if v is None or surfaces[v].get("type") != "GroundSurface": continue
                    ring = [vertex(*utm_to_wgs84(vertices[i][0] * sx + tx, vertices[i][1] * sy + ty, 32)) for i in face[0]]
                    if len(ring) >= 3: out.append(building(ring, (o.get("attributes") or {}).get("measuredHeight") or None))
    return out

GIPUZKOA_NS = {"gml": "http://www.opengis.net/gml/3.2", "wfs": "http://www.opengis.net/wfs/2.0",
               "bu-base": "http://inspire.ec.europa.eu/schemas/bu-base/4.0",
               "bu-core2d": "http://inspire.ec.europa.eu/schemas/bu-core2d/4.0"}

def gipuzkoa_buildings(raw):
    """INSPIRE BU GML 3.2 (the only format served), lat lon: heightAboveGround (estimated), else floors × 3 m, else the guess."""
    out = []
    for b in ET.fromstring(raw).iterfind("wfs:member/*", GIPUZKOA_NS):
        h = number(b.findtext("bu-base:heightAboveGround/bu-base:HeightAboveGround/bu-base:value", namespaces=GIPUZKOA_NS))
        if not h or h <= 0:
            floors = number(b.findtext("bu-base:numberOfFloorsAboveGround", namespaces=GIPUZKOA_NS))
            h = from_floors(floors)
        for ring in b.iterfind("bu-core2d:geometry2D//gml:exterior//gml:posList", GIPUZKOA_NS):
            v = [float(x) for x in ring.text.split()]
            if len(v) >= 6: out.append(building([vertex(v[i], v[i + 1]) for i in range(0, len(v) - 1, 2)], h))
    return out

def gipuzkoa(rect, cap=2000):
    """Gipuzkoa Provincial Council's INSPIRE buildings WFS (~13 KB a building: addresses ride along); a full page is split in four."""
    s, w, n, e = rect
    q = urllib.parse.urlencode({"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "bu-ext2d:Building",
                                "COUNT": str(cap), "SRSNAME": "urn:ogc:def:crs:EPSG::4326",
                                "BBOX": f"{s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326"})
    raw = get("https://b5m.gipuzkoa.eus/inspire/wfs/gipuzkoa_wfs_bu?" + q)
    if raw.count(b"<wfs:member") >= cap:
        ms, me = (s + n) / 2, (w + e) / 2
        return [b for part in ((s, w, ms, me), (s, me, ms, e), (ms, w, n, me), (ms, me, n, e)) for b in gipuzkoa(part, cap)]
    return gipuzkoa_buildings(raw)

ESPOO_NS = {"gml": "http://www.opengis.net/gml", "wfs": "http://www.opengis.net/wfs", "GIS": "http://www.tekla.com/schemas/GIS"}

def espoo_buildings(raw):
    """Espoo's Rakennukset (Tekla WFS 1.1.0, GML in ETRS-GK25, easting first): KERROSLUKU floors × 3 m."""
    out = []
    for b in ET.fromstring(raw).iterfind("gml:featureMember/GIS:Rakennukset", ESPOO_NS):
        floors = number(b.findtext("GIS:KERROSLUKU", namespaces=ESPOO_NS))
        for ring in b.iterfind("GIS:Geometry//gml:exterior/gml:LinearRing", ESPOO_NS):
            v = [float(x) for p in ring.iterfind("gml:pos", ESPOO_NS) for x in p.text.split()]
            v += [float(x) for p in ring.iterfind("gml:posList", ESPOO_NS) for x in p.text.split()]
            if len(v) >= 6:
                out.append(building([vertex(*tm_to_wgs84(v[i], v[i + 1], *GK25)) for i in range(0, len(v) - 1, 2)],
                                    from_floors(floors)))
    return out

def espoo(rect, cap=2000, depth=0):
    """The City of Espoo's buildings WFS over a box (its GK25 bounding box); a box that hits the cap is split in four."""
    s, w, n, e = rect
    xs, ys = zip(*(wgs84_to_tm(la, lo, *GK25) for la in (s, n) for lo in (w, e)))
    q = urllib.parse.urlencode({"service": "WFS", "version": "1.1.0", "request": "GetFeature", "typeName": "GIS:Rakennukset",
                                "maxFeatures": str(cap), "bbox": f"{min(xs):.1f},{min(ys):.1f},{max(xs):.1f},{max(ys):.1f}"})
    raw = get("https://kartat.espoo.fi/teklaogcweb/wfs.ashx?" + q)
    if raw.count(b"<gml:featureMember") >= cap and depth < 3:
        ms, me = (s + n) / 2, (w + e) / 2
        return [b for part in ((s, w, ms, me), (s, me, ms, e), (ms, w, n, me), (ms, me, n, e)) for b in espoo(part, cap, depth + 1)]
    return espoo_buildings(raw)

def citygml_buildings(raw, zone=32):
    """LoD2 CityGML in UTM (NRW's, Bavaria's): each Building's or BuildingPart's ground surface as an outline,
    its measuredHeight (metres, ground to roof) as the height; a parent made only of parts gives none itself."""
    out = []
    for _, el in ET.iterparse(io.BytesIO(raw)):
        if el.tag.rsplit("}", 1)[-1] != "Building": continue
        for obj in [el] + el.findall(".//{*}BuildingPart"):
            h = number(obj.findtext("{*}measuredHeight"))
            for pos in obj.findall("{*}boundedBy/{*}GroundSurface//{*}exterior//{*}posList"):
                v, d = [float(x) for x in pos.text.split()], int(pos.get("srsDimension") or 3)
                ring = [vertex(*utm_to_wgs84(v[i], v[i + 1], zone)) for i in range(0, len(v) - d + 1, d)]
                if len(ring) >= 3: out.append(building(ring, h if h and h > 0 else None))
        el.clear()
    return out

_citygml = {}  # tile URL → its buildings, each tile parsed once a run

def citygml_tiles(url, km, zone=32, odd_e=False):
    """A fetch over LoD2 CityGML tiles of `km` km in UTM `zone`N, named by their south-west corner in km: each
    tile a box touches is downloaded and parsed once a run, then served from memory. A tile the publisher
    does not have (404: no building there) is empty. A .zip tile (Saxony's, Baden-Württemberg's) is read member
    by member; `odd_e` for a grid whose eastings are odd kilometres (Baden-Württemberg's 2 km zips)."""
    def parse(raw):
        if not url.endswith(".zip"): return citygml_buildings(raw, zone)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return [b for m in z.namelist() if m.lower().endswith((".gml", ".xml")) for b in citygml_buildings(z.read(m), zone)]
    def fetch(rect):
        s, w, n, e = rect
        corners = [wgs84_to_utm(la, lo, zone) for la in (s, n) for lo in (w, e)]
        def span(i):
            lo, hi = int(min(c[i] for c in corners) // 1000), int(max(c[i] for c in corners) // 1000)
            start = (lo - 1) // km * km + 1 if (odd_e and i == 0) else lo // km * km
            return range(start, hi + 1, km)
        out = []
        for x in span(0):
            for y in span(1):
                u = url.format(e=x, n=y)
                if u not in _citygml:
                    try: _citygml[u] = parse(get(u))
                    except SourceError as err:
                        if "HTTP 404" not in str(err): raise
                        _citygml[u] = []
                out += [b for b in _citygml[u] if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"])]
        return out
    return fetch

def citygml_archive(url, name, km):
    """LoD2 CityGML tiles of `km` km in UTM 32N held in one archive (Bremen's: a zip holding a stored zip of
    the tiles), downloaded once a run (kept a month) and read tile by tile from inside it, the tile `name`
    ({e}, {n}: its south-west corner in km) matched at the end of a member's path."""
    def tiles():
        if url not in _citygml:
            outer = zipfile.ZipFile(download(url, os.path.join("extracts", "citygml"), max_days=30))
            inner = next(m for m in outer.namelist() if m.lower().endswith(".zip"))
            _citygml[url] = zipfile.ZipFile(outer.open(inner))
        return _citygml[url]
    def fetch(rect):
        s, w, n, e = rect
        z = tiles()
        corners = [wgs84_to_utm(la, lo, 32) for la in (s, n) for lo in (w, e)]
        span = lambda i: range(int(min(c[i] for c in corners) // 1000 // km * km), int(max(c[i] for c in corners) // 1000) + 1, km)
        out = []
        for x in span(0):
            for y in span(1):
                key = (url, x, y)
                if key not in _citygml:
                    member = next((m for m in z.namelist() if m.endswith(name.format(e=x, n=y))), None)
                    _citygml[key] = citygml_buildings(z.read(member)) if member else []
                out += [b for b in _citygml[key] if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"])]
        return out
    return fetch

def lod1_buildings(raw, to_wgs84=cs92_to_wgs84):
    """LoD1 CityGML (GUGiK's Modele 3D budynków): each Building's solid, its bottom face (every vertex at the
    solid's lowest z) as the outline, its measuredHeight as the height; positions easting first (as GUGiK writes them).
    `raw` is bytes or an open file (a sheet streamed out of its zip)."""
    out = []
    for _, el in ET.iterparse(raw if hasattr(raw, "read") else io.BytesIO(raw)):
        if el.tag.rsplit("}", 1)[-1] != "Building": continue
        h = number(el.findtext("{*}measuredHeight"))
        faces = [[float(x) for x in p.text.split()] for p in el.findall("{*}lod1Solid//{*}exterior//{*}posList")]
        faces = [v for v in faces if len(v) >= 9]
        if faces:
            low = min(min(v[2::3]) for v in faces)
            bottom = next((v for v in faces if max(v[2::3]) - low < 0.01), None)
            if bottom:
                out.append(building([vertex(*to_wgs84(bottom[i], bottom[i + 1])) for i in range(0, len(bottom) - 2, 3)],
                                    h if h and h > 0 else None))
        el.clear()
    return out

def estonia_lod1_buildings(raw):
    """Maa- ja Ruumiamet's LoD1 CityGML (a lod1MultiSurface of triangles, EPSG:3301 northing first): the triangles at
    the building's lowest z make its outline (triangles_outline), measuredHeight its height."""
    out = []
    for _, el in ET.iterparse(raw if hasattr(raw, "read") else io.BytesIO(raw)):
        if el.tag.rsplit("}", 1)[-1] != "Building": continue
        h = number(el.findtext("{*}measuredHeight"))
        faces = []
        for p in el.findall("{*}lod1MultiSurface//{*}exterior//{*}posList"):
            v = p.text.split()
            faces.append([(float(v[i]), float(v[i + 1]), float(v[i + 2])) for i in range(0, len(v) - 2, 3)])
        if faces:
            low = min(z for face in faces for *_, z in face)
            ring = triangles_outline([[(n, e) for n, e, z in face] for face in faces if all(z - low < 0.01 for *_, z in face)])
            if ring:
                out.append(building([vertex(*lcc3301_to_wgs84(e, n)) for n, e in ring], h if h and h > 0 else None))
        el.clear()
    return out

def lod1_archive(url, reader=lod1_buildings):
    """A fetch over one zip of LoD1 CityGML sheets (GUGiK's, one per powiat; Estonia's, one file per municipality),
    downloaded once a run (kept a month), streamed and parsed whole on first use."""
    def fetch(rect):
        s, w, n, e = rect
        if url not in _citygml:
            with zipfile.ZipFile(download(url, os.path.join("extracts", "citygml"), max_days=30)) as z:
                _citygml[url] = [b for m in z.namelist() if m.lower().endswith(".gml") for b in reader(z.open(m))]
        return [b for b in _citygml[url] if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"])]
    return fetch

# MLIT's Project PLATEAU (3D都市モデル): CityGML 2.0 per city, one building file per 1 km mesh, listed by the
# PLATEAU data catalogue's keyless API per city code (Osaka: 27100; Tokyo: each of the 23 wards, 13101–13123,
# a mesh on a ward border having a file per ward). The store holds the files gzipped and serves them so when
# asked (a 124 MB mesh is 16 MB on the wire).
PLATEAU_INDEX = "https://api.plateau.reearth.io/datacatalog/citygml/{}"
PLATEAU_KEPT = 40    # mesh files held parsed (a dense one ~3,000 buildings, ~10 MB): a row of 600 m blocks across
                     # a 13 km box touches ~30, so a sweep parses each once
_plateau_index = {}  # city code → {mesh code: [file URLs]}, read once a run
_plateau = {}        # file URL → its buildings, the PLATEAU_KEPT most recently used

def mesh_code(lat, lon):
    """The Japanese standard 3rd-level mesh (JIS X 0410: 30″ × 45″, ~1 km) holding a point, as its 8-digit code:
    the 1st mesh (lat × 1.5, lon − 100), its 8 × 8 cell, then that cell's 10 × 10 cell."""
    p, u = lat * 1.5, lon - 100
    q, v = (p % 1) * 8, (u % 1) * 8
    return f"{int(p):02d}{int(u):02d}{int(q)}{int(v)}{int((q % 1) * 10)}{int((v % 1) * 10)}"

def mesh_codes(rect):
    """The 3rd-level meshes a box touches, west to east then south to north."""
    s, w, n, e = rect
    return [mesh_code((i + 0.5) / 120, (j + 0.5) / 80)
            for i in range(math.floor(s * 120), math.floor(n * 120) + 1) for j in range(math.floor(w * 80), math.floor(e * 80) + 1)]

def plateau_buildings(raw):
    """A PLATEAU building file (gzipped or not): CityGML 2.0 in EPSG:6697, posLists "lat lon height" (three values
    a point, no srsDimension on them). Each Building's or BuildingPart's outline is its LoD2 ground surface where it
    has one (LoD2 covers 20 % of Tokyo's buildings, 2.6 % of Osaka's), else its LoD0 footprint (Osaka), else its
    LoD0 roof edge (Tokyo); its height is measuredHeight, metres (-9999 where unmeasured: the flagged guess)."""
    if raw[:2] == b"\x1f\x8b": raw = gzip.decompress(raw)
    out = []
    for _, el in ET.iterparse(io.BytesIO(raw)):
        if el.tag.rsplit("}", 1)[-1] != "Building": continue
        for obj in [el] + el.findall(".//{*}BuildingPart"):
            h = number(obj.findtext("{*}measuredHeight"))
            lists = next((found for where in ("{*}boundedBy/{*}GroundSurface", "{*}lod0FootPrint", "{*}lod0RoofEdge")
                          if (found := obj.findall(where + "//{*}exterior//{*}posList"))), [])
            for pos in lists:
                v, d = [float(x) for x in pos.text.split()], int(pos.get("srsDimension") or 3)
                ring = [vertex(v[i], v[i + 1]) for i in range(0, len(v) - d + 1, d)]
                if len(ring) >= 3: out.append(building(ring, h if h and h > 0 else None))
        el.clear()
    return out

def plateau(codes):
    """A fetch over PLATEAU's building files for the cities (or wards) of `codes`: their catalogues are read once a
    run, each mesh file a box touches is downloaded once (kept a year under extracts/plateau/<code>/, gzipped as
    served) and parsed once a run (the PLATEAU_KEPT most recent held). A mesh no city lists has no building."""
    def fetch(rect):
        files = {}
        for code in codes:
            if code not in _plateau_index:
                cities = sorted(get_json(PLATEAU_INDEX.format(code)).get("cities") or [], key=lambda c: c.get("year") or 0)
                index = {}
                for f in ((cities[-1].get("files") or {}).get("bldg") or []) if cities else []:
                    index.setdefault(f["code"], []).append(f["url"])
                _plateau_index[code] = index
            for mesh in mesh_codes(rect):
                for url in _plateau_index[code].get(mesh, ()): files[url] = code
        s, w, n, e = rect
        out = []
        for url, code in files.items():
            if url in _plateau:
                _plateau[url] = _plateau.pop(url)  # the most recent last
            else:
                file = download(url, os.path.join("extracts", "plateau", code), max_days=365, headers={"Accept-Encoding": "gzip"})
                with open(file, "rb") as f: _plateau[url] = plateau_buildings(f.read())
                while len(_plateau) > PLATEAU_KEPT: del _plateau[next(iter(_plateau))]
            out += [b for b in _plateau[url] if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"])]
        return out
    return fetch
