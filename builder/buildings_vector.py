"""Building feeds in vector formats: IGN, Turin, 3D BAG, Socrata, Catastro, Berlin, Liguria, Oakland, NRCan, DBT, UrbIS."""
import csv, json, math, os, re, struct, time, urllib.error, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from builder.cells import _overlaps, in_box, index, metres, padded
from builder.net import download, get, log, SourceError
from builder.projections import lambert72_to_wgs84, utm_to_wgs84, wgs84_to_lambert72
from builder.shapes import building, footprints, number, outer_rings, triangles_outline, vertex
from builder.formats import _atom_entries, zip_layer
from builder.apis import intersects, socrata, wfs

def ign_buildings(rect):
    """IGN BD TOPO: hauteur, else floors × 3 m, else the flagged 15 m guess."""
    # Only the three fields read: half the bytes of the full record, and far quicker to answer (measured 2026-09-24).
    features = wfs("https://data.geopf.fr/wfs/ows", {"TYPENAMES": "BDTOPO_V3:batiment", "OUTPUTFORMAT": "application/json",
                                                    "PROPERTYNAME": "geometrie,hauteur,nombre_d_etages"}, rect)
    out = []
    for f in features:
        p = f.get("properties") or {}
        h = p.get("hauteur")
        if not isinstance(h, (int, float)):
            floors = p.get("nombre_d_etages")
            h = floors * 3 if isinstance(floors, (int, float)) else None
        for ring in outer_rings(f.get("geometry")):
            out.append(building(ring, h))
    return out

def turin(rect):
    """BDTRE refuses GeoJSON: MapServer CSV, WKT in lon lat then the height, 1,000 per request."""
    s, w, n, e = rect
    q = urllib.parse.urlencode({"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "ms:un_vol",
                                "OUTPUTFORMAT": "text/csv", "SRSNAME": "urn:ogc:def:crs:EPSG::4326", "PROPERTYNAME": "un_vol_av",
                                "COUNT": "1000", "BBOX": f"{s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326"})
    rows = get("https://geoservices.csi.it/ms/wfs/taims/rp-01/taimswfs/bdtre_imm?" + q,
               parse=lambda raw: list(csv.reader(raw.decode("latin-1").splitlines()))[1:])
    if len(rows) >= 1000:
        ms, me = (s + n) / 2, (w + e) / 2
        return [b for part in ((s, w, ms, me), (s, me, ms, e), (ms, w, n, me), (ms, me, n, e)) for b in turin(part)]
    features = []
    for row in rows:
        # POLYGON ((x y,…), (hole)) or MULTIPOLYGON (((…)), ((…))): "((" opens each outer ring.
        rings = [[[float(c) for c in v.split()] for v in p.lstrip("(").split(")")[0].split(",")] for p in row[0].split("((")[1:]]
        try: h = float(row[1]) if len(row) > 1 and row[1] else None
        except ValueError: h = None
        features.append({"geometry": {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}, "properties": {"h": h}})
    return footprints(features, lambda p: p.get("h"))

def bag3d(rect):
    """3DBAG LoD1.2: NAP roof (70th percentile) minus NAP ground."""
    features = wfs("https://data.3dbag.nl/api/BAG3D/wfs", {"TYPENAMES": "BAG3D:lod12", "OUTPUTFORMAT": "application/json",
                   "SRSNAME": "EPSG:4326", "PROPERTYNAME": "geom,b3_h_70p,b3_h_maaiveld"}, rect)
    return footprints(features, lambda p: None if p.get("b3_h_70p") is None or p.get("b3_h_maaiveld") is None
                      else p["b3_h_70p"] - p["b3_h_maaiveld"])

def socrata_buildings(host, dataset, geometry, field, height):
    def fetch(rect):
        return footprints(socrata(host, dataset, intersects(geometry, rect), f"{field},{geometry}"),
                          lambda p: height(number(p.get(field))))
    return fetch

CATASTRO_FEED = "https://www.catastro.hacienda.gob.es/INSPIRE/buildings/ES.SDGC.bu.atom.xml"
_catastro = {"provinces": None, "feeds": {}, "loaded": set(), "grid": {}}

def catastro(rect):
    """Spain's cadastre, INSPIRE Buildings as one download per municipality (the ATOM feeds, since
    2026-09-27; the block-by-block WFS took 4 s a block and ran whole cities into the 5.5 h cap):
    floors above ground × 3 m, a 0-floor part (a basement) skipped, the flagged 15 m guess when the count is missing.
    A municipality is downloaded once (kept a year), its parts inside the city's box indexed by
    200 m cell; a rect is then served from memory. Coordinates come in the province's UTM zone."""
    from builder.buildings_table import CITY_BUILDINGS   # the table names this feed: imported here, once it is complete
    focus = next((padded((b["lat"][0], b["lon"][0], b["lat"][1], b["lon"][1]), 300) for b in CITY_BUILDINGS
                  if b["fetch"] is catastro and in_box(b, (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)), rect)
    if _catastro["provinces"] is None: _catastro["provinces"] = _atom_entries(CATASTRO_FEED)
    for _, feed, box, _ in _catastro["provinces"]:
        if not _overlaps(box, focus): continue
        if feed not in _catastro["feeds"]: _catastro["feeds"][feed] = _atom_entries(feed)
        for title, href, mbox, crs in _catastro["feeds"][feed]:
            if not _overlaps(mbox, focus) or href in _catastro["loaded"]: continue
            _catastro["loaded"].add(href)
            _load_catastro(href, crs, focus, title)
    s, w, n, e = rect
    out, seen = [], set()
    for ky in range(index(s) - 1, index(n) + 2):
        for kx in range(index(w) - 1, index(e) + 2):
            for b in _catastro["grid"].get((ky, kx), ()):
                if id(b) in seen: continue
                seen.add(id(b))
                if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"]): out.append(b)
    return out

def _load_catastro(href, crs, focus, title):
    """One municipality's building parts into the grid: streamed from its zip, UTM → WGS84, focus only."""
    import zipfile
    zone = int(crs[-2:])
    folder = os.path.join(_catastro.get("extracts", "extracts"), "catastro")
    file = None
    for encoding in ("utf-8", "latin-1"):  # the server takes the name percent-encoded either way, one of the two
        candidate = download(urllib.parse.quote(href, safe=":/", encoding=encoding), folder, max_days=365)
        if zipfile.is_zipfile(candidate): file = candidate; break
        os.remove(candidate)   # an HTML error page, not the package
    if file is None: raise SourceError(f"{title}: no package at {href}")
    ns = {"gml": "http://www.opengis.net/gml/3.2", "bu": "http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0"}
    started, kept = time.monotonic(), 0
    with zipfile.ZipFile(file) as z:
        member = next(m for m in z.namelist() if m.lower().endswith("buildingpart.gml"))
        with z.open(member) as f:
            for _, elem in ET.iterparse(f, events=("end",)):
                if elem.tag != "{%s}BuildingPart" % ns["bu"]: continue
                floors = number((elem.findtext("bu:numberOfFloorsAboveGround", namespaces=ns) or "").strip())
                if floors != 0:
                    for ring in elem.iterfind(".//gml:exterior//gml:posList", ns):
                        v = [float(x) for x in ring.text.split()]
                        pts = [utm_to_wgs84(v[i], v[i + 1], zone) for i in range(0, len(v) - 1, 2)]
                        if len(pts) < 3 or not any(focus[0] <= la <= focus[2] and focus[1] <= lo <= focus[3] for la, lo in pts): continue
                        b = building([vertex(la, lo) for la, lo in pts], floors * 3 if floors and floors > 0 else None)
                        for k in {(index(la), index(lo)) for la, lo in pts}:  # every cell a part touches
                            _catastro["grid"].setdefault(k, []).append(b)
                        kept += 1
                elem.clear()
    log(f"    catastro: {title}: {kept} parts inside the box in {time.monotonic() - started:.0f} s")

def beoland(rect):
    """Beoland's Belgrade LoD2 multipatch, which answers no GeoJSON and leaves measuredheight empty: the
    footprints' outer (clockwise) rings from one query, each height (the extent's top minus its bottom,
    metres) from a second, matched by objectid."""
    s, w, n, e = rect
    def features(option):
        def parse(raw):
            root = json.loads(raw)
            if "error" in root: raise ValueError(root["error"])
            return root
        out, last = [], -1
        while True:
            q = urllib.parse.urlencode({"where": f"objectid>{last}", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
                                        "inSR": "4326", "outSR": "4326", "outFields": "objectid", "returnZ": "true",
                                        "multipatchOption": option, "orderByFields": "objectid", "f": "json"})
            page = get("https://gis.beoland.com/server/rest/services/Hosted/Beograd_3D_WSL1/FeatureServer/2/query?" + q, parse=parse)
            out += page.get("features") or []
            if not page.get("features") or not page.get("exceededTransferLimit"): return out
            last = max(f["attributes"]["objectid"] for f in page["features"])
    heights = {}
    for f in features("extent"):
        z = [p[2] for r in (f.get("geometry") or {}).get("rings") or [] for p in r if len(p) > 2 and p[2] is not None]
        if z: heights[f["attributes"]["objectid"]] = max(z) - min(z)
    out = []
    for f in features("xyFootprint"):
        for r in (f.get("geometry") or {}).get("rings") or []:
            if len(r) >= 4 and sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(r, r[1:])) < 0:  # clockwise: outer
                h = heights.get(f["attributes"]["objectid"])
                out.append(building([vertex(p[1], p[0]) for p in r], h if h and h > 0 else None))
    return out

LIGURIA = "https://geoservizi.regione.liguria.it/geoserver/ows"

def liguria(rect):
    """Regione Liguria's NC5 3D footprints, every vertex at the eave's elevation: the height is that minus the
    nearest spot height measured at a building's foot ("al piede", 0301) within 40 m, else the flagged 15 m guess."""
    params = {"OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326"}
    feet = {}  # 0.001° buckets
    for f in wfs(LIGURIA, {**params, "TYPENAMES": "M2052:L6911", "PROPERTYNAME": "wkb_geometry,pt_quo_q,pt_quo_sed"}, padded(rect, 50)):
        p = f.get("properties") or {}
        if p.get("pt_quo_sed") == "0301" and f.get("geometry") and p.get("pt_quo_q") is not None:
            lon, lat = f["geometry"]["coordinates"][:2]
            feet.setdefault((round(lat, 3), round(lon, 3)), []).append((lat, lon, p["pt_quo_q"]))
    out = []
    for f in wfs(LIGURIA, {**params, "TYPENAMES": "M2052:L6871", "PROPERTYNAME": "wkb_geometry"}, rect):
        g = f.get("geometry") or {}
        for polygon in [g.get("coordinates")] if g.get("type") == "Polygon" else g.get("coordinates") or []:
            ring = [v for v in (polygon or [[]])[0] if len(v) >= 3]
            if len(ring) < 3: continue
            lat, lon = sum(v[1] for v in ring) / len(ring), sum(v[0] for v in ring) / len(ring)
            near = [q for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                    for q in feet.get((round(round(lat, 3) + dy / 1000, 3), round(round(lon, 3) + dx / 1000, 3)), ())]
            foot = min(near, key=lambda q: metres(lat, lon, q[0], q[1]), default=None)
            h = ring[0][2] - foot[2] if foot and metres(lat, lon, foot[0], foot[1]) <= 40 else None
            out.append(building([vertex(v[1], v[0]) for v in ring], h if h and h > 0 else None))
    return out

def oakland(rect):
    """Oakland's footprints: nostory × 3 m. A 20-floor footprint under 5,000 sq ft is left out: the six
    of them are houses, a placeholder, not a count."""
    return footprints([f for f in socrata("data.oaklandca.gov", "iqfp-6kz5", intersects("the_geom", rect), "nostory,shape_area,the_geom")
                       if not (number(f["properties"].get("nostory")) == 20 and (number(f["properties"].get("shape_area")) or 0) < 5000)],
                      lambda p: (number(p.get("nostory")) or 0) * 3.0, guess=False)

NRCAN_GTA = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_ON_GTA_2023_gpkg.zip"
NRCAN_MONTREAL = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_QC_VILLE_MONTREAL_gpkg.zip"
NRCAN_HAMILTON = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_ON_Hamilton_Niagara_2021_2_gpkg.zip"
NRCAN_LAKE_ERIE = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_ON_2017-LakeErie_gpkg.zip"
NRCAN_HALIFAX = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_NS_Halifax_County_Inland_2018_gpkg.zip"
NRCAN_WINNIPEG = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_MB_Winnipeg_2019_gpkg.zip"
NRCAN_QUEBEC = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_QC_Quebec_Levis_2017_gpkg.zip"

# The Italian DBT's unità volumetriche (Milan's Comune, Regione Lazio): UN_VOL_AV is a volume's own height above its
# base. For one al suolo (UN_VOL_POR 01) that is its height above ground. A raised one (02 overhang, 03 portico
# ceiling, 04 underpass ceiling, 05 loggia, 09 covered passage: a tenth of Milan's) starts above the ground, so its top
# is UN_VOL_QE, the extrusion's elevation, less its building's ground: the lowest base (QE - AV) of the building's
# ground volumes (CEDIUV), 3.9 m more than AV at the median in Milan. Underground volumes (08) are left out.
DBT_FIELDS = ("UN_VOL_AV", "UN_VOL_POR", "UN_VOL_QE", "CEDIUV")

def dbt_height(p, f):
    if p["UN_VOL_POR"] == "08": return False
    if p["UN_VOL_POR"] in ("", "01") or p["UN_VOL_QE"] is None: return p["UN_VOL_AV"]
    if not hasattr(f, "ground"):  # {building: its ground elevation}, read once a run
        ground = f.ground = {}
        for q in f.table(DBT_FIELDS):
            if q and q["UN_VOL_POR"] == "01" and q["UN_VOL_QE"] is not None and q["UN_VOL_AV"] is not None:
                ground[q["CEDIUV"]] = min(ground.get(q["CEDIUV"], math.inf), q["UN_VOL_QE"] - q["UN_VOL_AV"])
    base = f.ground.get(p["CEDIUV"])
    return p["UN_VOL_QE"] - base if base is not None and p["UN_VOL_QE"] - base > 0 else p["UN_VOL_AV"]

MILAN_DBT = "https://gisportal.comune.milano.it/download/area_download/SIT/DBT2020/DBT_2020.zip"
LAZIO_DBGT = "https://geoportale.regione.lazio.it/cartografia/api/raw/2020_DBGT_5K_SHP/Roma.zip"
BUENOS_AIRES_TEJIDO = "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/secretaria-de-desarrollo-urbano/tejido-urbano/tejido.zip"

def lazio_token():
    """Regione Lazio's file browser serves its files to an anonymous session: its login endpoint hands any caller a JWT."""
    token = get("https://geoportale.regione.lazio.it/cartografia/api/login", data=b"{}").decode().strip()
    if token.count(".") != 2: raise SourceError("Lazio file browser: no token")
    return {"X-Auth": token}

# Brussels: UrbIS-Buildings3D (paradigm.brussels, CC0), LoD2 solids in 1 km Shapefile tiles in Lambert 72, named by the
# south-west corner in km and dated (monthly); the INSPIRE ATOM feed lists every tile's every date.
URBIS_FEED = "https://urbisdownload.datastore.brussels/atomfeed/e9ec2aa4-cffd-11ee-bccc-00090ffe0001-en.xml"
_urbis = {}   # "feed" → {(km east, km north): the latest tile's URL}; a tile's URL → its buildings, parsed once a run

def urbis_solids(raw):
    """UrbIS-Buildings3D's BuildingSolids .shp (MultiPatch, one closed solid per record): the rings lying flat at the
    solid's lowest z make its outline (triangles_outline), its z range its height. Every part is a ring (FirstRing, on
    all 3,569 solids of tile 148170, 2026-10-03); a solid whose bottom is not flat (6 of them there) is left out."""
    out, at = [], 100
    while at + 8 <= len(raw):
        size = 2 * struct.unpack_from(">i", raw, at + 4)[0]
        body, at = raw[at + 8:at + 8 + size], at + 8 + size
        if len(body) < 44 or struct.unpack_from("<i", body)[0] != 31: continue
        parts, points = struct.unpack_from("<ii", body, 36)
        starts = struct.unpack_from(f"<{parts}i", body, 44) + (points,)
        o = 44 + 8 * parts
        xy = struct.unpack_from(f"<{2 * points}d", body, o)
        low, high = struct.unpack_from("<2d", body, o + 16 * points)
        z = struct.unpack_from(f"<{points}d", body, o + 16 * points + 16)
        ring = triangles_outline([[(xy[2 * i], xy[2 * i + 1]) for i in range(a, b)]
                                  for a, b in zip(starts, starts[1:]) if all(z[i] - low < 0.01 for i in range(a, b))])
        if ring: out.append(building([vertex(*lambert72_to_wgs84(x, y)) for x, y in ring], high - low if high > low else None))
    return out

def urbis(rect):
    """The UrbIS solids of every tile the rect meets (the feed read once a run; a tile's BuildingSolids layer fetched
    by range once a year, zip_layer), those with a vertex in the rect."""
    if "feed" not in _urbis:
        latest = {}
        for url, e, n, day in re.findall(r'href="([^"]*/SHP/UrbISBuildings3D_31370_SHP_(\d{3})(\d{3})_(\d{8})\.zip)"', get(URBIS_FEED).decode()):
            if day > latest.get((int(e), int(n)), ("", ""))[0]: latest[(int(e), int(n))] = (day, url)
        if not latest: raise SourceError("UrbIS: no SHP tile in the feed")
        _urbis["feed"] = {k: url for k, (_, url) in latest.items()}
    s, w, n, e = rect
    corners = [wgs84_to_lambert72(la, lo) for la in (s, n) for lo in (w, e)]
    out = []
    for x in range(int(min(c[0] for c in corners) // 1000), int(max(c[0] for c in corners) // 1000) + 1):
        for y in range(int(min(c[1] for c in corners) // 1000), int(max(c[1] for c in corners) // 1000) + 1):
            url = _urbis["feed"].get((x, y))
            if url is None: continue   # outside the Region
            if url not in _urbis:
                with open(zip_layer(url, f"UrbISBuildings3D_{x}{y}_BuildingSolids", "urbis") + ".shp", "rb") as f:
                    _urbis[url] = urbis_solids(f.read())
            out += [b for b in _urbis[url] if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"])]
    return out
