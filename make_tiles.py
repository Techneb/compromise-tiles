#!/usr/bin/env python3
"""Pre-cut city-data tiles for Compromise's sunny filter and venue list.

Writes one JSON array per ~200 m
cell, keyed "<int(lat*500)>,<int(lon*500)>", under buildings/,
terraces-v2/ and communes/, and one per ~2 km cell, keyed
"<int(lat*50)>,<int(lon*50)>", under venues/.

    make_tiles.py --city Paris --lat 48.8719 --lon 2.3316 --half-km 0.5 --out ./tiles
    make_tiles.py --cities cities.json --out ./tiles --layers terraces-v2,venues

OpenStreetMap comes from a Geofabrik extract (the smallest region holding
each area, found in Geofabrik's index), cut and read with osmium-tool —
never from Overpass. City permit and height feeds, IGN and the commune
contours are downloaded in bulk at build time; the phone reads only tiles.
Every request is retried (10, 30, then 90 s), spaced per host (1 s) and
sent with a User-Agent naming the project. The run is resumable: a cell
whose file already parses is skipped while younger than its layer's
MAX_AGE_DAYS, by the file's mtime. A file is written only from a complete
answer — a source that failed leaves no file, so the next run asks again.
Failures are listed at the end; the exit status is non-zero only if more
than 20% of the cells failed.

Needs Python 3 (standard library only) and osmium-tool on the PATH.
Derived tiles that include OpenStreetMap data are ODbL: publish them under
ODbL with the credit "© OpenStreetMap contributors".
"""
import argparse, csv, datetime, gzip, json, math, os, re, shutil, ssl, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, xml.etree.ElementTree as ET, zoneinfo

UA = "compromise-tiles/2.0 (+https://github.com/Techneb/compromise; sunny-terrace tile generator)"
WAITS = (10, 30, 90)          # seconds before the 2nd, 3rd and 4th try
GAP = 1.0                     # seconds between two requests to one host
TIMEOUT = 60
DEAD_AFTER = 3                # a host that exhausted its retries this many times in a row is skipped for the run
TERRACE_RADIUS = 400          # metres around the cell's centre: the radius the density gate is calibrated on
# Every footprint whose box comes within this of the cell: a terrace is
# matched within 30 m of a venue and a shadow ray walks 150 m from it toward
# the sun — 180 m, plus a margin (Opéra, 2026-09-24: 690 KB → 280 KB a cell
# against a 400 m reach). Raise it if either reach grows.
BUILDING_REACH = 200
BUILDING_PAD = 250            # fetched around a block: some feeds select by a footprint's centre, not its outline

# The city permit feeds, one row each. A cell picks its feed by INSEE
# code when the commune answered (a French commune without a feed gets none, so Levallois inside
# Paris's box is OSM only), else the first box holding the cell's centre.
def row(name, insee, host, dataset, name_field, kinds, lat, lon, **extra):
    return dict(city=name, insee=insee, host=host, dataset=dataset, name=name_field, kinds=kinds, lat=lat, lon=lon, **extra)

PERMIT_CITIES = [
    row("Paris", "75056", "parisdata.opendatasoft.com", "terrasses-autorisations", "nom_enseigne", ["typologie"],
        (48.79, 48.95), (2.17, 2.53)),
    row("Toulouse", "31555", "data.toulouse-metropole.fr", "terrasses-autorisees-ville-de-toulouse", "etablissement",
        ["terrasse_ouverte", "extension_terrasse", "terrasse_fermee"], (43.55, 43.65), (1.37, 1.50)),
    row("Strasbourg", "67482", "data.strasbourg.eu", "terrasses-autorisees-en-{year}", "nom_enseigne", [],
        (48.54, 48.61), (7.68, 7.81)),
    row("Anglet", "64024", "anglet-opendatapaysbasque.opendatasoft.com", "autorisations-de-terrasses-a-anglet", "enseigne",
        ["surf_terr_ouverte_littoral", "surf_terr_sol_hors_littoral", "surf_terr_ouvr_fixe_hors_littoral",
         "surf_terr_fermee_littoral", "surf_terr_vol_ferme_hors_littoral"], (43.47, 43.54), (-1.56, -1.49)),
    row("Rouen", "76540", "data.metropole-rouen-normandie.fr", "rouen-terrasse-2021-dos", "enseigne", [],
        (49.40, 49.48), (1.03, 1.17), point="geolocalisation"),
    row("Lorient", "56121", "www.opendata56.fr", "liste-des-terrasses-autorisees-ville-de-lorient", "", ["terrasse_type"],
        (47.71, 47.78), (-3.40, -3.32)),
    row("Melbourne", "", "data.melbourne.vic.gov.au", "cafes-and-restaurants-with-seating-capacity", "trading_name", [],
        (-37.90, -37.75), (144.90, 145.02), point="location",
        filter="seating_type=\"Seats - Outdoor\" and census_year>=date'2023-01-01'"),
    # Socrata: `{twoYearsAgo}` and `{today}` are resolved at run time, as resolvedSocrataFilter does.
    # Camden's name is a full address, cut before its first digit (cutBeforeFirstDigit).
    row("Camden", "", "opendata.camden.gov.uk", "8ixc-jf73", "development_address", [], (51.52, 51.58), (-0.22, -0.10),
        shape="socrata", point="location", filter="decision_type = 'Granted' AND registered_date >= '{twoYearsAgo}'",
        clean="digit"),
    row("New York", "", "data.cityofnewyork.us", "fpeh-f7ci", "assumed_name_s", [], (40.49, 40.92), (-74.26, -73.68),
        shape="socrata", point="location", filter="license_expiration_date >= '{today}'"),
    row("Chicago", "", "data.cityofchicago.org", "nxj5-ix6z", "doing_business_as_name", [], (41.64, 42.02), (-87.94, -87.52),
        shape="socrata", point="location", filter="expiration_date >= '{today}'"),
    row("San Francisco", "", "data.sf.gov", "dpch-7nr4", "dbaname", [], (37.70, 37.83), (-122.52, -122.35),
        shape="socrata", point="point", filter="tablesandchairs = true"),
    # DSO REST, HAL JSON, points in RD New: a terrace is a licence whose terrasgeometrie is not null.
    row("Amsterdam", "", "api.data.amsterdam.nl", "horeca/exploitatievergunning", "zaaknaam", [], (52.28, 52.43), (4.73, 5.02),
        shape="amsterdam"),
    # WFS: NAME is the placement, not the café, so no name field — door rule only.
    row("Vienna", "", "data.wien.gv.at/daten/geo", "ogdwien:SCHANIGARTENOGD", "", [], (48.10, 48.35), (16.15, 16.58),
        shape="wfs"),
    # This GeoServer refuses bbox alongside CQL_FILTER: the box folds into BBOX().
    row("Copenhagen", "", "wfs-kbhkort.kk.dk/k101/ows", "k101:raaden_over_vej_events_anonym_aktuelt", "restaurantnavn", [],
        (55.60, 55.75), (12.40, 12.70), shape="wfs", filter="sagstype='Udeservering'"),
    # One 5 MB CSV, UTM zone 30N, no query API: fetched whole once per run.
    row("Madrid", "", "datos.madrid.es",
        "dataset/200085-0-censo-locales/resource/200085-6-censo-locales/download/200085-6-censo-locales.csv",
        "rotulo", [], (40.30, 40.56), (-3.90, -3.50), shape="madrid"),
    # The name sits inside a free-text description (boulevardName).
    row("Basel", "", "data.bs.ch", "100018", "bezeichng", [], (47.51, 47.60), (7.55, 7.70),
        filter='search(bezeichng, "Boulevard") and datum_bis >= now() and belestatbe = "bewilligt"', clean="boulevard"),
    # ArcGIS REST (SITG, "Accès libre"); a year in PERIODE means granted.
    row("Geneva", "", "vector.sitg.ge.ch/arcgis/rest/services", "VDG_TERRASSE_RESTO/FeatureServer/0", "NOM_CAFE", ["OBJET"],
        (46.17, 46.24), (6.10, 6.19), shape="arcgis", filter="PERIODE LIKE '%20%'"),
    # One static GeoJSON of frontage lines, fetched whole once per run.
    row("Seville", "", "map4.urbanismosevilla.org", "IDE.Sevilla/Visor_Veladores/Data_set/I_VM_TEX_45_03_OVF_JSON.geojson",
        "Nombre Del Establecimiento", [], (37.32, 37.45), (-6.05, -5.88), shape="seville"),
    # ArcGIS MapServer (Vilniaus planas, copyright only): polygons at their mean vertex, the venue
    # after the holder's slash, unexpired only. Only Imone is asked — the layer holds e-mails too.
    row("Vilnius", "", "gis.vplanas.lt/arcgis/rest/services", "Interaktyvus_zemelapis/Zalias_Vilnius/MapServer/76", "Imone", [],
        (54.57, 54.83), (25.02, 25.48), shape="arcgis", filter="Leid_galioj IS NULL OR Leid_galioj >= CURRENT_TIMESTAMP",
        clean="vilnius"),
    # MapServer WFS, UTM 32N only: serving licences, a terrace where outdoor hours (UTE_TID) are set.
    row("Oslo", "", "od2.pbe.oslo.kommune.no/cgi-bin/wms", "skjenkebevilling_punkt", "OBJEKTNAVN", [],
        (59.81, 60.00), (10.62, 10.95), shape="oslo"),
    # Open Data BCN (CKAN): the newest CSV of the dataset's resources, fetched whole once per run; no names.
    row("Barcelona", "", "opendata-ajuntament.barcelona.cat", "terrasses-comercos-vigents", "", [],
        (41.32, 41.47), (2.05, 2.23), shape="barcelona"),
]

def in_box(c, lat, lon): return c["lat"][0] <= lat <= c["lat"][1] and c["lon"][0] <= lon <= c["lon"][1]


# ---------------------------------------------------------------- network

class SourceError(Exception):
    """A source that did not answer completely, after every retry."""

_last, _dead = {}, {}

def log(message): print(message, file=sys.stderr, flush=True)

def get(url, data=None, parse=None, waits=WAITS, context=None):
    """One request, retried after each of `waits`: spaced per host, gzip asked
    for (smaller transfers are the ones the proxy lets through), the body
    parsed inside the retry so a truncated answer is asked again rather than
    trusted. A host that fails DEAD_AFTER requests in a row is skipped."""
    host = urllib.parse.urlsplit(url).hostname
    if _dead.get(host, 0) >= DEAD_AFTER:
        raise SourceError(f"{host}: skipped, failed {DEAD_AFTER} requests in a row")
    error = "?"
    for attempt in range(len(waits) + 1):
        wait = _last.get(host, 0) + GAP - time.monotonic()
        if wait > 0: time.sleep(wait)
        try:
            request = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip": body = gzip.decompress(body)
            result = parse(body) if parse else body
            _last[host], _dead[host] = time.monotonic(), 0
            return result
        except urllib.error.HTTPError as e:
            error = f"HTTP {e.code}"
            if parse:  # an answer under an error status (Catastro's "No records" report) is still an answer
                try:
                    result = parse(gzip.decompress(e.read()) if e.headers.get("Content-Encoding") == "gzip" else e.read())
                    _last[host], _dead[host] = time.monotonic(), 0
                    return result
                except Exception:  # noqa: BLE001 — not an answer, then
                    pass
            if e.code in (400, 401, 404, 405, 410, 414): break  # a wrong request stays wrong
        except Exception as e:  # noqa: BLE001 — IncompleteRead, resets, timeouts, bad JSON: all worth another try
            error = f"{type(e).__name__}: {e}"[:200]
        _last[host] = time.monotonic()
        if attempt < len(waits):
            log(f"    {host}: {error}; retry in {waits[attempt]} s")
            time.sleep(waits[attempt])
    _dead[host] = _dead.get(host, 0) + 1
    raise SourceError(f"{host}: {error}")

def get_json(url, data=None, waits=WAITS): return get(url, data, parse=json.loads, waits=waits)

# ---------------------------------------------------------------- cells

M = 111_320  # metres per degree of latitude

FINE, COARSE = 500, 50  # cells per degree: ~200 m (buildings, terraces, communes) and ~2 km (venues)

def index(x, scale=FINE): return int(x * scale)  # Swift's Int() truncates toward zero, as Python's int() does

def span(k, scale=FINE):
    """The coordinates a key covers: truncation makes 0 twice as wide and a negative key reach down."""
    if k > 0: return k / scale, (k + 1) / scale
    if k < 0: return (k - 1) / scale, k / scale
    return -1 / scale, 1 / scale

class Cell:
    def __init__(self, ky, kx, scale=FINE):
        self.key = f"{ky},{kx}"
        (self.s, self.n), (self.w, self.e) = span(ky, scale), span(kx, scale)
        self.lat, self.lon = (self.s + self.n) / 2, (self.w + self.e) / 2
    @property
    def rect(self): return self.s, self.w, self.n, self.e

def cells_in(s, w, n, e, scale=FINE):
    return [Cell(ky, kx, scale) for ky in range(index(s, scale), index(n, scale) + 1)
            for kx in range(index(w, scale), index(e, scale) + 1)]

def blocks(cells, size):
    """Cells grouped size × size by key, so a source is asked once per block."""
    groups = {}
    for c in cells:
        ky, kx = map(int, c.key.split(","))
        groups.setdefault((ky // size, kx // size), []).append(c)
    return [groups[k] for k in sorted(groups)]

def union(cells):
    return min(c.s for c in cells), min(c.w for c in cells), max(c.n for c in cells), max(c.e for c in cells)

def padded(rect, metres):
    s, w, n, e = rect
    dlat = metres / M
    dlon = metres / (M * math.cos(math.radians((s + n) / 2)))
    return s - dlat, w - dlon, n + dlat, e + dlon

def metres(lat1, lon1, lat2, lon2):
    return math.hypot((lat2 - lat1) * M, (lon2 - lon1) * M * math.cos(math.radians(lat1)))

def in_france(lat, lon): return 41.3 <= lat <= 51.1 and -5.2 <= lon <= 9.6

def contains(geometry, lat, lon):
    """Point in a GeoJSON Polygon or MultiPolygon, even-odd over every ring, so holes count."""
    if not geometry: return False
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    inside = False
    for ring in (r for p in polygons for r in p):
        for a, b in zip(ring, ring[1:] + ring[:1]):
            if (a[1] > lat) != (b[1] > lat) and lon < a[0] + (lat - a[1]) * (b[0] - a[0]) / (b[1] - a[1]): inside = not inside
    return inside

def bounds(geometry):
    points = [v for p in ([geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]) for r in p for v in r]
    return min(v[1] for v in points), min(v[0] for v in points), max(v[1] for v in points), max(v[0] for v in points)


# ---------------------------------------------------------------- OpenStreetMap, from an extract

GEOFABRIK = "https://download.geofabrik.de/index-v1.json"
AMENITIES = ("bar", "pub", "biergarten", "cafe", "restaurant")  # every venue type
GRID = 100  # the in-memory index's buckets: 1/100°, ~1 km
_regions = None

def geofabrik_pbf(lat, lon):
    """The smallest Geofabrik region holding the point — the deepest in its chain of parents."""
    global _regions
    if _regions is None: _regions = get_json(GEOFABRIK)["features"]
    parents = {f["properties"]["id"]: f["properties"].get("parent") for f in _regions}
    depth = lambda i: 0 if i is None else 1 + depth(parents.get(i))
    holding = [f for f in _regions if "pbf" in f["properties"].get("urls", {}) and contains(f["geometry"], lat, lon)]
    if not holding: raise SourceError(f"no Geofabrik extract holds {lat},{lon}")
    return max(holding, key=lambda f: depth(f["properties"]["id"]))["properties"]["urls"]["pbf"]

def download(url, folder, max_days=6):
    """A whole file, kept in `folder` and fetched again once older than `max_days` (Geofabrik updates daily)."""
    file = os.path.join(folder, url.rsplit("/", 1)[1])
    if os.path.exists(file) and time.time() - os.path.getmtime(file) < max_days * 86_400: return file
    os.makedirs(folder, exist_ok=True)
    log(f"  downloading {url}")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(file + ".tmp", "wb") as f:
            shutil.copyfileobj(response, f, 1 << 20)
    except Exception as e:  # noqa: BLE001 — the area's OSM layers fail, not the run
        raise SourceError(f"{url}: {type(e).__name__}: {e}") from e
    os.replace(file + ".tmp", file)
    return file

def osm_height(tags):
    """`height`, else `building:levels` × 3 m, else 15 m — parseOSMBuildings on the Swift side."""
    for key, scale in (("height", 1), ("building:levels", 3)):
        try: return float(str(tags[key]).replace(" m", "").strip()) * scale
        except (KeyError, ValueError): pass
    return 15.0

def buckets(s, w, n, e):
    return [(y, x) for y in range(math.floor(s * GRID), math.floor(n * GRID) + 1) for x in range(math.floor(w * GRID), math.floor(e * GRID) + 1)]

def osmium(*args):
    try: return subprocess.run(["osmium", *args], check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as error: raise SourceError(f"osmium: {error.stderr.strip()[:200]}") from error

def features(pbf):
    """An osmium file as GeoJSON features, points and areas only: a closed way also comes out as a
    line. Points include the nodes a way needed that carry tags of their own, so callers check tags."""
    export = osmium("export", pbf, "-f", "geojsonseq", "--geometry-types=point,polygon", "-o", "-")
    for line in export.split("\n"):  # not splitlines(): it breaks on the \x1e that opens each record
        if line.strip("\x1e"): yield json.loads(line.lstrip("\x1e"))

class OSM:
    """An extract around one area: its venues in memory, bucketed by ~1 km, and its buildings cut
    from disk a block at a time — a whole box of them does not fit in memory (Paris intra-muros
    peaked at 3.5 GB held; the petite couronne is ten times the area)."""
    def __init__(self, pbf, rect):
        s, w, n, e = rect
        self.venues, self.walls = {}, pbf + ".buildings.pbf"
        box, kept = pbf + ".box.pbf", pbf + ".venues.pbf"
        try:
            osmium("extract", "-b", f"{w},{s},{e},{n}", pbf, "-o", box, "--overwrite")
            osmium("tags-filter", box, "w/building", "-o", self.walls, "--overwrite")
            osmium("tags-filter", box, "nwr/amenity=" + ",".join(AMENITIES), "-o", kept, "--overwrite")
            for feature in features(kept):
                tags, geometry = feature["properties"], feature["geometry"]
                if tags.get("amenity") not in AMENITIES: continue
                if geometry["type"] == "Point": lon, lat = geometry["coordinates"][:2]
                else:  # an area at its mean vertex, as the terrace permits
                    ring = next(outer_rings(geometry), None)
                    if not ring: continue
                    lat, lon = (sum(v[k] for v in ring) / len(ring) for k in ("latitude", "longitude"))
                venue = {"name": str(tags.get("name", "")).strip(), "coordinate": {"latitude": round(lat, 6), "longitude": round(lon, 6)},
                         "amenity": tags["amenity"], "outdoor_seating": tags.get("outdoor_seating") == "yes", "source": "osm"}
                self.venues.setdefault(buckets(lat, lon, lat, lon)[0], []).append(venue)
        finally:
            for f in (box, kept):
                if os.path.exists(f): os.remove(f)

    def buildings(self, rect):
        """Building ways meeting the rect. osmium keeps a way with a node in the cut, whole; a big one
        (a station hall) can reach a cell with every node outside it, hence 500 m more — the
        in-memory index had them, and near_buildings trims the rest."""
        s, w, n, e = padded(rect, 500)
        cut = self.walls + ".cut.pbf"
        osmium("extract", "-b", f"{w},{s},{e},{n}", self.walls, "-o", cut, "--overwrite")
        return [building(ring, osm_height(f["properties"])) for f in features(cut)
                if "building" in f["properties"] and f["geometry"]["type"] != "Point" for ring in outer_rings(f["geometry"])]

    def near(self, table, rect):
        """Everything in the buckets the rect touches, once each: callers keep what is near enough."""
        seen = {}
        for k in buckets(*rect):
            for item in table.get(k, ()): seen[id(item)] = item
        return list(seen.values())

_osm = None  # the current area's OSM, set by run()

def osm_buildings(rect): return _osm.buildings(rect)

def osm_terraces(rect):
    """Outdoor seating on a bar, pub, beer garden, café or restaurant, named or not."""
    out = []
    for v in _osm.near(_osm.venues, rect):
        if not v["outdoor_seating"]: continue
        item = {"kind": "TERRASSE (OSM)", "coordinate": v["coordinate"]}
        if v["name"]: item["name"] = v["name"]
        out.append(item)
    return out

def osm_venues(cell):
    """The named venues of one ~2 km cell, the key's own truncation deciding the edge."""
    return [v for v in _osm.near(_osm.venues, cell.rect) if v["name"]
            and f"{index(v['coordinate']['latitude'], COARSE)},{index(v['coordinate']['longitude'], COARSE)}" == cell.key]


# ---------------------------------------------------------------- shapes

def vertex(lat, lon):
    """Six decimals is 0.1 m, finer than any footprint source is surveyed to; feeds send up to 15."""
    return {"latitude": round(lat, 6), "longitude": round(lon, 6)}

def building(ring, height):
    return {"outline": ring, "height": round(float(height), 1)}

def outer_rings(geometry):
    coordinates = (geometry or {}).get("coordinates") or []
    polygons = [coordinates] if (geometry or {}).get("type") == "Polygon" else coordinates
    for polygon in polygons:
        ring = [vertex(v[1], v[0]) for v in (polygon[0] if polygon else []) if len(v) >= 2]
        if len(ring) >= 3: yield ring

def footprints(features, height):
    """GeoJSON → a building: outer rings, the city's height rule, 15 m when it reads nothing sensible."""
    out = []
    for f in features:
        h = height(f.get("properties") or {})
        for ring in outer_rings(f.get("geometry")):
            out.append(building(ring, h if h and h > 0 else 15.0))
    return out

def unique(items):
    seen, out = set(), []
    for item in items:
        k = json.dumps(item, sort_keys=True)
        if k not in seen: seen.add(k); out.append(item)
    return out

def wfs(url, params, rect, cap=5000, depth=0):
    """A WFS 2.0 GetFeature over a box (latitude first, EPSG:4326 URN); a box that hits the cap is split in four."""
    s, w, n, e = rect
    q = dict(params, SERVICE="WFS", VERSION="2.0.0", REQUEST="GetFeature", COUNT=str(cap),
             BBOX=f"{s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326")
    features = get_json(url + "?" + urllib.parse.urlencode(q)).get("features", [])
    if len(features) < cap or depth >= 3: return features
    ms, me = (s + n) / 2, (w + e) / 2
    return [f for part in ((s, w, ms, me), (s, me, ms, e), (ms, w, n, me), (ms, me, n, e))
            for f in wfs(url, params, part, cap, depth + 1)]

def arcgis(url, rect, fields, where="1=1", oid="OBJECTID"):
    """An ArcGIS REST envelope query in WGS84, paged on the layer's OID field (resultOffset repeats and skips rows on SITG)."""
    s, w, n, e = rect
    def parse(raw):
        root = json.loads(raw)
        if "error" in root: raise ValueError(root["error"])
        return root
    out, last = [], 0
    while True:
        q = urllib.parse.urlencode({"where": f"({where}) AND {oid}>{last}", "geometry": f"{w},{s},{e},{n}",
                                    "geometryType": "esriGeometryEnvelope", "inSR": "4326", "outSR": "4326",
                                    "outFields": ",".join([oid] + fields), "f": "geojson", "orderByFields": oid})
        page = get(url + "/query?" + q, parse=parse)
        features = page.get("features") or []
        out += features
        # A FeatureServer flags truncation under "properties", a MapServer (Wrocław) at the top level.
        more = page.get("exceededTransferLimit") or (page.get("properties") or {}).get("exceededTransferLimit")
        if not features or not more: return out
        last = max(f["properties"][oid] for f in features)

def opendatasoft(host, dataset, where, select):
    q = urllib.parse.urlencode({"where": where, "select": select, "limit": "-1"})
    return get_json(f"https://{host}/api/explore/v2.1/catalog/datasets/{dataset}/exports/geojson?" + q).get("features") or []


# ---------------------------------------------------------------- buildings

def ign_buildings(rect):
    """IGN BD TOPO: hauteur, else floors × 3 m, else 15 m — parseBuildings on the Swift side."""
    # Only the three fields read: half the bytes of the full record, and far quicker to answer (measured 2026-09-24).
    features = wfs("https://data.geopf.fr/wfs/ows", {"TYPENAMES": "BDTOPO_V3:batiment", "OUTPUTFORMAT": "application/json",
                                                    "PROPERTYNAME": "geometrie,hauteur,nombre_d_etages"}, rect)
    out = []
    for f in features:
        p = f.get("properties") or {}
        h = p.get("hauteur")
        if not isinstance(h, (int, float)):
            floors = p.get("nombre_d_etages")
            h = floors * 3 if isinstance(floors, (int, float)) else 15
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

def in_bbox(point, rect):
    s, w, n, e = rect
    return f"in_bbox({point}, {s}, {w}, {n}, {e})"

def socrata(host, dataset, where, select):
    """A Socrata dataset as GeoJSON, paged: SoQL numbers come back as strings."""
    out, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"$where": where, "$select": select, "$limit": "2000", "$offset": str(offset), "$order": ":id"})
        page = get_json(f"https://{host}/resource/{dataset}.geojson?" + q).get("features") or []
        out += page
        if len(page) < 2000: return out
        offset += 2000

def number(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def socrata_buildings(host, dataset, geometry, field, height):
    def fetch(rect):
        s, w, n, e = rect
        box = f"'POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))'"
        return footprints(socrata(host, dataset, f"intersects({geometry}, {box})", f"{field},{geometry}"),
                          lambda p: height(number(p.get(field))))
    return fetch

def catastro(rect, step=0.0018):
    """Spain's cadastre, INSPIRE Buildings (bu:BuildingPart, GML, EPSG:4326 posList latitude first):
    floors above ground × 3 m, a 0-floor part (a basement) skipped, 15 m when the count is missing —
    parseCatastroParts on the Swift side. The server takes ~4 s for a 200 m box but 68 s and 19 MB for
    800 m (2026-09-25), and resets a third concurrent request, so the rect is asked ~200 m at a time,
    one after another; a part straddling two boxes comes back twice and `unique` drops the copy."""
    s, w, n, e = rect
    ns = {"gml": "http://www.opengis.net/gml/3.2", "bu": "http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0"}
    def parse(raw):
        root = ET.fromstring(raw)
        if not root.tag.endswith("FeatureCollection"):
            text = " ".join(root.itertext())
            if "No records" in text: return []  # an empty box is an answer, not a failure
            raise ValueError(text.strip()[:200])
        return root.iter("{%s}BuildingPart" % ns["bu"])
    out, lat = [], s
    while lat < n:
        lon = w
        while lon < e:
            q = urllib.parse.urlencode({"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "bu:BuildingPart",
                                        "SRSNAME": "urn:ogc:def:crs:EPSG::4326",
                                        "BBOX": f"{lat},{lon},{min(lat + step, n)},{min(lon + step, e)},urn:ogc:def:crs:EPSG::4326"})
            for part in get("https://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx?" + q, parse=lambda raw: list(parse(raw))):
                floors = number((part.findtext("bu:numberOfFloorsAboveGround", namespaces=ns) or "").strip())
                if floors == 0: continue
                for ring in part.iterfind(".//gml:exterior//gml:posList", ns):
                    v = [float(x) for x in ring.text.split()]
                    outline = [vertex(v[i], v[i + 1]) for i in range(0, len(v) - 1, 2)]
                    if len(outline) >= 3: out.append(building(outline, floors * 3 if floors and floors > 0 else 15.0))
            lon += step
        lat += step
    return out

def building_row(name, lat, lon, fetch, osm_on_error=False):
    return dict(city=name, lat=lat, lon=lon, fetch=fetch, osm_on_error=osm_on_error)

# The city height feeds, one row each. The first box holding the cell's centre is asked first,
# then IGN (France), then OpenStreetMap, each only when the one before answered nothing.
CITY_BUILDINGS = [
    building_row("Melbourne", (-37.90, -37.75), (144.90, 145.02), lambda r: footprints(opendatasoft(
        "data.melbourne.vic.gov.au", "2023-building-footprints", in_bbox("geo_point_2d", r) + ' and footprint_type != "Tunnel"',
        "structure_extrusion"), lambda p: p.get("structure_extrusion"))),
    building_row("Amsterdam", (52.28, 52.43), (4.73, 5.02), bag3d),
    building_row("Rotterdam", (51.86, 51.99), (4.37, 4.60), bag3d),
    building_row("The Hague", (52.00, 52.13), (4.18, 4.42), bag3d),
    building_row("Utrecht", (52.03, 52.14), (4.97, 5.20), bag3d),
    building_row("Berlin", (52.33, 52.68), (13.08, 13.77), lambda r: footprints(wfs(
        "https://gdi.berlin.de/services/wfs/ua_gebaeudehoehen", {"TYPENAMES": "ua_gebaeudehoehen:gebaeudehoehen",
        "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326", "PROPERTYNAME": "geom,hoehe"}, r), lambda p: p.get("hoehe"))),
    building_row("Geneva", (46.17, 46.24), (6.10, 6.19), lambda r: footprints(arcgis(
        "https://vector.sitg.ge.ch/arcgis/rest/services/CAD_BATIMENT_HORSOL/FeatureServer/0", r, ["HAUTEUR"]),
        lambda p: p.get("HAUTEUR"))),
    # BLDG_HEIGH is feet.
    building_row("Denver", (39.66, 39.80), (-105.05, -104.87), lambda r: footprints(arcgis(
        "https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_PROP_BUILDINGOUTLINES_A/FeatureServer/111",
        r, ["BLDG_HEIGH"]), lambda p: p["BLDG_HEIGH"] * 0.3048 if p.get("BLDG_HEIGH") else None)),
    building_row("Cape Town", (-34.20, -33.55), (18.30, 18.80), lambda r: footprints(arcgis(
        "https://esapqa.capetown.gov.za/agsext/rest/services/Theme_Based/ODP_SPLIT_6/FeatureServer/2", r, ["BLD_HGT"]),
        lambda p: p.get("BLD_HGT"))),
    building_row("São Paulo", (-23.65, -23.49), (-46.74, -46.59), lambda r: footprints(wfs(
        "https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/ows", {"TYPENAMES": "geoportal:edificacao",
        "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326", "PROPERTYNAME": "ge_poligono,qt_altura_edificacao"}, r),
        lambda p: p.get("qt_altura_edificacao"))),
    building_row("Wrocław", (51.08, 51.16), (16.95, 17.10), lambda r: footprints(arcgis(
        "https://gis.um.wroc.pl/portal_srv/rest/services/SMH_2022_Budynki/MapServer/0", r, ["HA"]), lambda p: p.get("HA"))),
    # Metres, else floors × 3 m. Answers only from Israel (HTTP 571 elsewhere): from the cloud every
    # Tel Aviv cell fails and no tile is written — never an OSM tile in its place.
    building_row("Tel Aviv", (32.04, 32.13), (34.74, 34.80), lambda r: footprints(arcgis(
        "https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2/MapServer/513", r, ["gova_simplex_2019", "ms_komot"],
        oid="oid_mivne"), lambda p: p.get("gova_simplex_2019") or (p["ms_komot"] * 3 if p.get("ms_komot") else None))),
    # Spain's cadastre (owner's decision, 2026-09-25): tiles hold derived footprints and heights, never
    # Catastro's GML, which its licence forbids spreading untransformed. A cell Catastro fails gets no tile
    # (the next run retries), never an OSM tile.
    building_row("Madrid", (40.31, 40.56), (-3.84, -3.52), catastro),
    building_row("Seville", (37.32, 37.45), (-6.03, -5.87), catastro),
    building_row("Barcelona", (41.32, 41.47), (2.05, 2.23), catastro),
    building_row("Valencia", (39.40, 39.52), (-0.43, -0.30), catastro),
    building_row("Zaragoza", (41.58, 41.72), (-0.98, -0.80), catastro),
    building_row("Málaga", (36.66, 36.78), (-4.55, -4.35), catastro),
    building_row("Palma", (39.53, 39.62), (2.58, 2.75), catastro),
    building_row("Las Palmas", (28.05, 28.18), (-15.47, -15.40), catastro),
    building_row("Murcia", (37.95, 38.03), (-1.18, -1.08), catastro),
    building_row("Alicante", (38.32, 38.40), (-0.53, -0.43), catastro),
    building_row("Córdoba", (37.85, 37.92), (-4.82, -4.73), catastro),
    building_row("Valladolid", (41.60, 41.70), (-4.78, -4.68), catastro),
    building_row("Vigo", (42.19, 42.26), (-8.78, -8.67), catastro),
    building_row("Gijón", (43.50, 43.56), (-5.72, -5.62), catastro),
    building_row("Bologna", (44.42, 44.56), (11.23, 11.44), lambda r: footprints(opendatasoft(
        "opendata.comune.bologna.it", "c_a944ctc_edifici_pl", in_bbox("geo_point_2d", r), "altezza_gr"),
        lambda p: p.get("altezza_gr"))),
    building_row("Turin", (45.00, 45.14), (7.57, 7.78), turin),
    # height_roof is feet; one row carries its own BIN as its height, hence the cap.
    building_row("New York", (40.49, 40.92), (-74.26, -73.68), socrata_buildings(
        "data.cityofnewyork.us", "5zhs-2jue", "the_geom", "height_roof", lambda h: h * 0.3048 if h and h < 2000 else None)),
    # No height at all: floors × 3 m; 0 floors (a third of the Loop) falls to 15 m.
    building_row("Chicago", (41.64, 42.02), (-87.94, -87.52), socrata_buildings(
        "data.cityofchicago.org", "syp8-uezg", "the_geom", "stories", lambda h: h * 3 if h and h > 0 else None)),
    building_row("San Francisco", (37.70, 37.83), (-122.52, -122.35), socrata_buildings(
        "data.sf.gov", "ynuv-fyni", "shape", "hgt_median_m", lambda h: h)),
]


# ---------------------------------------------------------------- terraces

def permit_items(features, c, point="geo_point_2d"):
    """GeoJSON permits → terraces."""
    out = []
    for f in features:
        p = f.get("properties") or {}
        g = f.get("geometry") or {}
        if g.get("type") == "Point": lon, lat = g["coordinates"][:2]
        elif isinstance(p.get(point), dict): lat, lon = p[point]["lat"], p[point]["lon"]
        elif isinstance(p.get("geo_point_2d"), dict): lat, lon = p["geo_point_2d"]["lat"], p["geo_point_2d"]["lon"]
        elif g.get("type") in ("Polygon", "MultiPolygon"):  # Vilnius: the mean vertex, as parseTerraces
            rings = g["coordinates"] if g["type"] == "Polygon" else [r for poly in g["coordinates"] for r in poly]
            points = [v for r in rings for v in r if len(v) >= 2]
            if not points: continue
            lat, lon = sum(v[1] for v in points) / len(points), sum(v[0] for v in points) / len(points)
        else: continue
        kind = ""
        for field in c["kinds"]:
            v = p.get(field)
            if isinstance(v, str): kind = v; break
            if isinstance(v, (int, float)) and v > 0: kind = "TERRASSE FERMEE" if "ferm" in field else "TERRASSE OUVERTE"; break
        if not c["kinds"]: kind = "TERRASSE"
        item = {"kind": kind, "coordinate": {"latitude": lat, "longitude": lon}}
        name = p.get(c["name"]) if c["name"] else None
        if isinstance(name, str) and c.get("clean"): name = CLEAN[c["clean"]](name)
        if name: item["name"] = name
        out.append(item)
    return out

def cut_before_first_digit(text):
    """Camden's name is the full address: "Goodfare Italian Restaurant  26 - 28 Parkway…" (cutBeforeFirstDigit)."""
    m = re.search(r"\d", text)
    if not m: return text
    return text[:m.start()].strip() or None

def boulevard_name(text):
    """Basel's permit description: "Boulevardrestaurant SCHNABEL  Fläche 57 m2…" (boulevardName)."""
    m = re.match(r"^Boulevard\S*\s*(?:-\s*)?(?:Restaurant\s+)?(.*?)(?:\s{2}|,|$)", text)
    return (m.group(1).strip(" \t\"'“”„«»?!.:;-") or None) if m else None

def vilnius_venue_name(text):
    """Vilnius's Imone: "UAB „holder“ / „venue“" — the venue, else the holder's quoted name, else the
    text less its legal form (vilniusVenueName)."""
    trim = lambda t: re.sub(r"^[\s\x00-\x1f\x7f-\x9f]+|[\s\x00-\x1f\x7f-\x9f]+$", "", t)
    quoted = re.search(r"„([^“]+)“", text)
    venue = text.split("/", 1)[1] if "/" in text else quoted.group(1) if quoted else text
    name = trim(re.sub(r"[„“”\"]", "", venue))
    return trim(re.sub(r"^(UAB|MB|AB|IĮ|VšĮ|ŽŪB|TŪB|KŪB)\s+", "", name)) or None

CLEAN = {"digit": cut_before_first_digit, "boulevard": boulevard_name, "vilnius": vilnius_venue_name}

def socrata_filter(text):
    """resolvedSocrataFilter: a floating timestamp, no zone."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    try: ago = now.replace(year=now.year - 2)
    except ValueError: ago = now.replace(year=now.year - 2, day=28)  # 29 February
    return text.replace("{twoYearsAgo}", ago.isoformat()).replace("{today}", now.isoformat())

def amsterdam(c, rect):
    """DSO REST: a circle in WGS84 (lon,lat,metres) around the box, HAL pages of 1,000, points in RD New."""
    s, w, n, e = rect
    lat, lon = (s + n) / 2, (w + e) / 2
    radius = math.ceil(metres(lat, lon, n, e)) + 1
    q = urllib.parse.urlencode({"locatie[within]": f"{lon},{lat},{radius}", "_pageSize": "1000"})
    url, out = f"https://{c['host']}/v1/{c['dataset']}/?" + q, []
    resource = c["dataset"].split("/")[-1]
    while url:
        root = get_json(url)
        for item in (root.get("_embedded") or {}).get(resource, []):
            point = (item.get("locatie") or {}).get("coordinates") or []
            if item.get("terrasgeometrie") is None or len(point) < 2: continue
            lat, lon = rd_new_to_wgs84(point[0], point[1])
            entry = {"kind": "TERRASSE", "coordinate": {"latitude": lat, "longitude": lon}}
            if item.get(c["name"]): entry["name"] = item[c["name"]]
            out.append(entry)
        url = ((root.get("_links") or {}).get("next") or {}).get("href")
    return out

def rd_new_to_wgs84(x, y):
    """RD New (EPSG:28992) → WGS84, the Kadaster approximation rdNewToWGS84 uses (1–2 m)."""
    dx, dy = (x - 155_000) * 1e-5, (y - 463_000) * 1e-5
    lat = 52.15517440 + (3235.65389 * dy - 32.58297 * dx**2 - 0.24750 * dy**2 - 0.84978 * dx**2 * dy
                         - 0.06550 * dy**3 - 0.01709 * dx**2 * dy**2 - 0.00738 * dx - 0.00530 * dx**4
                         + 0.00039 * dx**2 * dy**3 - 0.00033 * dx**4 * dy + 0.00012 * dx * dy - 0.00034 * dx**2) / 3600
    lon = 5.38720621 + (5260.52916 * dx + 105.94684 * dx * dy + 2.45656 * dx * dy**2 - 0.81885 * dx**3
                        + 0.05594 * dx * dy**3 - 0.05607 * dx**3 * dy + 0.01199 * dy - 0.00256 * dx**3 * dy**2
                        + 0.00128 * dx * dy**4 + 0.00022 * dy**2 - 0.00022 * dx**2 + 0.00026 * dx**5) / 3600
    return lat, lon

def utm_to_wgs84(easting, northing, zone=30):
    """ETRS89 / UTM north (Madrid 30, Oslo 32) → WGS84, Snyder's inverse transverse Mercator, as utmNorthToWGS84."""
    a, f, k0 = 6_378_137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f); ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    x, m = easting - 500_000, northing / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu) + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
            + (151 * e1**3 / 96) * math.sin(6 * mu) + (1097 * e1**4 / 512) * math.sin(8 * mu))
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1, t1 = ep2 * cos1**2, tan1**2
    n1 = a / math.sqrt(1 - e2 * sin1**2)
    r1 = a * (1 - e2) / (1 - e2 * sin1**2) ** 1.5
    d = x / (n1 * k0)
    phi = phi1 - (n1 * tan1 / r1) * (d**2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
                                    + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720)
    lon = math.radians(zone * 6 - 183) + (d - (1 + 2 * t1 + c1) * d**3 / 6
                              + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120) / cos1
    return math.degrees(phi), math.degrees(lon)

_whole = {}  # whole-file feeds, fetched once per run

def madrid_all(c):
    """parseMadridCSV: ';', quoted, a BOM; "Abierta" only; any of four light-structure flags means enclosed."""
    rows = [[cell.strip('"\ufeff') for cell in line.split(";")]
            for line in get(f"https://{c['host']}/{c['dataset']}").decode("utf-8").splitlines() if line]
    header, out = rows[0], []
    col = {name: k for k, name in enumerate(header)}
    enclosure = [col[n] for n in ("construccion_ligera_fachada_es", "construccion_ligera_bordillo_es",
                                  "construccion_ligera_fachada_ra", "construccion_ligera_bordillo_ra") if n in col]
    for r in rows[1:]:
        if len(r) != len(header) or r[col["desc_situacion_terraza"]] != "Abierta": continue
        x, y = number(r[col["coordenada_x_local"]]), number(r[col["coordenada_y_local"]])
        if x is None or y is None: continue
        lat, lon = utm_to_wgs84(x, y)
        item = {"kind": "TERRASSE FERMEE" if any(r[k] == "True" for k in enclosure) else "TERRASSE OUVERTE",
                "coordinate": {"latitude": lat, "longitude": lon}}
        if r[col["rotulo"]]: item["name"] = r[col["rotulo"]]
        out.append(item)
    if not out: raise SourceError("Madrid: no terrace parsed")  # a changed header must not read as "no terraces"
    return out

def fnmt_context():
    """Seville's server sends its certificate without the intermediate: add FNMT's own
    (fetched from the URL the certificate names) so verification can complete. Nothing is
    trusted that a browser following the AIA link would not trust."""
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=get("https://www.cert.fnmt.es/certs/ACCOMP.crt"))
    return context

def seville_all(c):
    """parseSevilleGeoJSON: a frontage line at its mean vertex; a permit past its end date dropped."""
    root = get(f"https://{c['host']}/{c['dataset']}", parse=json.loads, context=fnmt_context())
    today = datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Madrid")).date()
    out = []
    for f in root.get("features") or []:
        p = f.get("properties") or {}
        end = p.get("Final Periodo Autorizado")
        try:
            if end and datetime.datetime.strptime(end, "%d/%m/%Y").date() < today: continue
        except ValueError: pass
        g = f.get("geometry") or {}
        coords = g.get("coordinates") or []
        points = [v for line in coords for v in line] if g.get("type") == "MultiLineString" else coords
        points = [v for v in points if isinstance(v, list) and len(v) >= 2]
        if not points: continue
        item = {"kind": "TERRASSE", "coordinate": {"latitude": sum(v[1] for v in points) / len(points),
                                                   "longitude": sum(v[0] for v in points) / len(points)}}
        if p.get(c["name"]): item["name"] = p[c["name"]]
        out.append(item)
    if not out: raise SourceError("Seville: no terrace parsed")  # as for Madrid
    return out

def barcelona_all(c):
    """fetchBarcelonaTerraces + parseBarcelonaCSV: CKAN's newest CSV resource, WGS84 LATITUD/LONGITUD, no name."""
    package = get_json(f"https://{c['host']}/data/api/3/action/package_show?id={c['dataset']}")
    url = next(r["url"] for r in package["result"]["resources"] if (r.get("format") or "").upper() == "CSV")
    rows = [[cell.strip('"\ufeff') for cell in line.split(";")] for line in get(url).decode("utf-8").splitlines() if line]
    header, out = rows[0], []
    lat_col, lon_col = header.index("LATITUD"), header.index("LONGITUD")
    for r in rows[1:]:
        if len(r) != len(header): continue
        lat, lon = number(r[lat_col]), number(r[lon_col])
        if lat is None or lon is None: continue
        out.append({"kind": "TERRASSE", "coordinate": {"latitude": lat, "longitude": lon}})
    if not out: raise SourceError("Barcelona: no terrace parsed")  # as for Madrid
    return out

def whole_file(c, rect, load):
    if c["city"] not in _whole:
        try:
            _whole[c["city"]] = load(c)
        except SourceError:
            raise
        except Exception as e:  # noqa: BLE001 — a renamed column or a changed package fails this feed, not the run
            raise SourceError(f"{c['city']}: {type(e).__name__}: {e}") from e
    s, w, n, e = rect
    return [t for t in _whole[c["city"]] if s <= t["coordinate"]["latitude"] <= n and w <= t["coordinate"]["longitude"] <= e]

def oslo(c, rect):
    """osloURL + parseOsloTerraces: latitude-first bbox, UTM 32N answers, outdoor hours only; the holder never asked."""
    s, w, n, e = rect
    q = urllib.parse.urlencode({"map": "AAPNING", "service": "WFS", "version": "1.1.0", "request": "GetFeature",
                                "typename": c["dataset"], "propertyName": "OBJEKTNAVN,UTE_TID",
                                "outputFormat": "application/json; subtype=geojson; charset=UTF-8",
                                "bbox": f"{s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326"})
    out = []
    for f in get_json(f"https://{c['host']}?" + q).get("features") or []:
        p, point = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or []
        if not str(p.get("UTE_TID") or "").strip() or len(point) < 2: continue
        lat, lon = utm_to_wgs84(point[0], point[1], zone=32)
        item = {"kind": "TERRASSE", "coordinate": {"latitude": lat, "longitude": lon}}
        if p.get("OBJEKTNAVN"): item["name"] = p["OBJEKTNAVN"]
        out.append(item)
    return out

def permits(c, rect):
    s, w, n, e = rect
    if c.get("shape") == "oslo": return oslo(c, rect)
    if c.get("shape") == "amsterdam": return amsterdam(c, rect)
    if c.get("shape") == "madrid": return whole_file(c, rect, madrid_all)
    if c.get("shape") == "seville": return whole_file(c, rect, seville_all)
    if c.get("shape") == "barcelona": return whole_file(c, rect, barcelona_all)
    if c.get("shape") == "socrata":
        where = f"within_box({c['point']}, {n}, {w}, {s}, {e})" + (f" AND {socrata_filter(c['filter'])}" if c.get("filter") else "")
        return permit_items(socrata(c["host"], c["dataset"], where, ",".join(f for f in [c["name"], c["point"]] if f)), c)
    if c.get("shape") == "wfs":
        # WFS 1.1.0: longitude first, and the
        # bbox needs its own trailing CRS or the server silently answers zero.
        params = {"service": "WFS", "version": "1.1.0", "request": "GetFeature",
                  "typeName": c["dataset"], "outputFormat": "json", "srsName": "EPSG:4326"}
        if c.get("filter"):
            # Copenhagen's GeoServer refuses bbox alongside CQL_FILTER.
            params["CQL_FILTER"] = f"{c['filter']} AND BBOX(wkb_geometry,{w},{s},{e},{n},'EPSG:4326')"
        else:
            params["bbox"] = f"{w},{s},{e},{n},EPSG:4326"
        features = get_json(f"https://{c['host']}?" + urllib.parse.urlencode(params)).get("features") or []
        return permit_items(features, c)   # polygons land on their mean vertex, as parseTerraces
    if c.get("shape") == "arcgis":
        fields = [f for f in [c["name"]] + c["kinds"] if f]
        return permit_items(arcgis(f"https://{c['host']}/{c['dataset']}", rect, fields, c.get("filter") or "1=1"), c)
    point = c.get("point", "geo_point_2d")
    where = in_bbox(point, rect) + (f" and {c['filter']}" if c.get("filter") else "")
    # Empty name (Lorient) must not leave a leading comma in `select`: an ODSQLSyntaxError.
    select = ",".join(f for f in [c["name"], point] + c["kinds"] if f)
    year = time.gmtime().tm_year
    for y in (year, year - 1):
        try: features = opendatasoft(c["host"], c["dataset"].replace("{year}", str(y)), where, select)
        except SourceError:
            if "{year}" in c["dataset"] and y == year: continue  # this year's dataset may not exist yet
            raise
        if features or "{year}" not in c["dataset"]: break
    return permit_items(features, c, point)


# ---------------------------------------------------------------- communes

_communes = []  # [(bounds, commune, contour)] of every département loaded so far
_departements = set()

def commune(lat, lon):
    """INSEE communes from Etalab's contours, a département at a time: a point in a loaded contour
    costs nothing, else the geo API names its commune and the département's contours are loaded.
    IGN's apicarto (the same communes, Admin Express) when Etalab does not answer. [] is an answer:
    no commune."""
    for (s, w, n, e), found, contour in _communes:
        if s <= lat <= n and w <= lon <= e and contains(contour, lat, lon): return [found]
    q = urllib.parse.urlencode({"lat": lat, "lon": lon, "fields": "nom,code,codeDepartement", "format": "json"})
    try:  # once: apicarto is the retry
        answer = get_json("https://geo.api.gouv.fr/communes?" + q, waits=())
    except SourceError as first:
        try:
            q = urllib.parse.urlencode({"lat": lat, "lon": lon})
            root = get_json("https://apicarto.ign.fr/api/limites-administratives/commune?" + q)
        except SourceError as second:
            raise SourceError(f"{first}; {second}")
        return [{"nom": f["properties"]["nom_com"], "code": f["properties"]["insee_com"]} for f in root.get("features", [])]
    for c in answer:
        if c.get("codeDepartement") and c["codeDepartement"] not in _departements:
            _departements.add(c["codeDepartement"])
            q = urllib.parse.urlencode({"fields": "nom,code,contour", "format": "geojson", "geometry": "contour"})
            try: features = get_json(f"https://geo.api.gouv.fr/departements/{c['codeDepartement']}/communes?" + q)["features"]
            except SourceError as e: log(f"    communes of {c['codeDepartement']}: {e}; point by point"); continue
            _communes.extend((bounds(f["geometry"]), {"nom": f["properties"]["nom"], "code": f["properties"]["code"]}, f["geometry"])
                             for f in features if f.get("geometry"))
    return [{"nom": c["nom"], "code": c["code"]} for c in answer]


# ---------------------------------------------------------------- the run

LAYERS = ("communes", "buildings", "terraces-v2", "venues")
# Days before a finished tile is cut again. Resuming skips a cell only while
# its file is younger than this, so the weekly run does refresh permits
# (they lapse) while a same-day re-run after a
# failure still skips what is done. Buildings change yearly, communes never.
MAX_AGE_DAYS = {"communes": None, "buildings": 360, "terraces-v2": 6, "venues": 6}

def path(out, layer, cell): return os.path.join(out, layer, cell.key + ".json")

def read(file):
    try:
        with open(file) as f: items = json.load(f)
        return items if isinstance(items, list) else None
    except (OSError, ValueError):
        return None

def done(out, layer, cell):
    """A tile to keep: it parses, and is younger than its layer's MAX_AGE_DAYS."""
    file, limit = path(out, layer, cell), MAX_AGE_DAYS[layer]
    if read(file) is None: return False
    return limit is None or time.time() - os.path.getmtime(file) < limit * 86_400

def write(file, items):
    os.makedirs(os.path.dirname(file), exist_ok=True)
    with open(file + ".tmp", "w") as f: json.dump(items, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(file + ".tmp", file)

_cell_by_cell = set()  # sources whose blocks failed where single cells answered: blocks too big for a proxy

def gather(cells, fetch, pad, source):
    """{key: items or SourceError}: the block asked once, and each cell alone if the block failed.
    A source whose block failed while its cells answered is asked cell by cell for the rest of the
    run (Overpass from a cloud proxy, 2026-09-24: 3×3 blocks reset every time, single cells went through)."""
    if len(cells) > 1 and source not in _cell_by_cell:
        try:
            items = unique(fetch(padded(union(cells), pad)))
            return {c.key: items for c in cells}
        except SourceError as e:
            log(f"    {source}: block failed ({e}); asking cell by cell")
            block_failed = True
    else:
        block_failed = False
    answers = {}
    for c in cells:
        try: answers[c.key] = unique(fetch(padded(c.rect, pad)))
        except SourceError as e: answers[c.key] = e
    if block_failed and any(not isinstance(a, SourceError) for a in answers.values()):
        log(f"    {source}: cell by cell from now on")
        _cell_by_cell.add(source)
    return answers

def near_buildings(cell, buildings):
    s, w, n, e = padded(cell.rect, BUILDING_REACH)
    def meets(b):
        lats = [p["latitude"] for p in b["outline"]]
        lons = [p["longitude"] for p in b["outline"]]
        return min(lats) <= n and max(lats) >= s and min(lons) <= e and max(lons) >= w
    return [b for b in buildings if meets(b)]

def near_terraces(cell, terraces):
    return [t for t in terraces
            if metres(cell.lat, cell.lon, t["coordinate"]["latitude"], t["coordinate"]["longitude"]) <= TERRACE_RADIUS]

def building_source(cell):
    city = next((b for b in CITY_BUILDINGS if in_box(b, cell.lat, cell.lon)), None)
    return city["city"] if city else "IGN" if in_france(cell.lat, cell.lon) else "OSM"

FETCH_BUILDINGS = dict({b["city"]: b["fetch"] for b in CITY_BUILDINGS}, IGN=ign_buildings, OSM=osm_buildings)

def next_source(source, cell):
    """A city feed or IGN answering nothing for a cell hands it on; an error does not."""
    if source == "IGN": return "OSM"
    if source == "OSM": return None
    return "IGN" if in_france(cell.lat, cell.lon) else "OSM"

def do_buildings(cells, out, failures):
    groups = {}
    for c in cells: groups.setdefault(building_source(c), []).append(c)
    while groups:
        source, group = groups.popitem()
        answers = gather(group, FETCH_BUILDINGS[source], BUILDING_PAD, source)
        for c in group:
            answer = answers[c.key]
            if isinstance(answer, SourceError):
                city = next((b for b in CITY_BUILDINGS if b["city"] == source), None)
                if city and city["osm_on_error"]:
                    log(f"    {source}: {answer}; {c.key} from OSM")
                    groups.setdefault("OSM", []).append(c); continue
                failures.append((c.key, "buildings", str(answer))); continue
            items = near_buildings(c, answer)
            if not items and next_source(source, c):
                groups.setdefault(next_source(source, c), []).append(c); continue
            write(path(out, "buildings", c), items)

def permit_city(cell, communes):
    """The permit feed for a cell: by INSEE code when a commune answered, else by box."""
    if communes: return next((c for c in PERMIT_CITIES if c["insee"] == communes[0]["code"]), None)
    return next((c for c in PERMIT_CITIES if in_box(c, cell.lat, cell.lon)), None)

def do_terraces(cells, communes, out, failures):
    osm = gather(cells, osm_terraces, TERRACE_RADIUS, "OSM terraces")
    feeds = {}
    for c in cells:
        feed = permit_city(c, communes.get(c.key))
        if feed: feeds.setdefault(id(feed), (feed, []))[1].append(c)
    found = {}
    for feed, group in feeds.values():
        found.update(gather(group, lambda r, feed=feed: permits(feed, r), TERRACE_RADIUS, feed["city"] + " permits"))
    for c in cells:
        answers = [osm[c.key]] + ([found[c.key]] if c.key in found else [])
        errors = [a for a in answers if isinstance(a, SourceError)]
        if errors:  # half an answer is not a tile
            failures.append((c.key, "terraces-v2", "; ".join(map(str, errors)))); continue
        write(path(out, "terraces-v2", c), near_terraces(c, [t for a in answers[1:] for t in a] + answers[0]))

def area(entry, half_km):
    """An entry's box: its own `box` [s, w, n, e], else `half_km` around its point."""
    if entry.get("box"): return tuple(entry["box"])
    lat, lon, half_km = entry["lat"], entry["lon"], entry.get("half_km", half_km)
    dlat, dlon = half_km / 111.32, half_km / (111.32 * math.cos(math.radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon

def run(label, rect, out, block, layers, extracts, departements=None):
    global _osm
    started = time.monotonic()
    cells, coarse = cells_in(*rect), cells_in(*rect, scale=COARSE)
    failures, skipped, outside = [], 0, set()
    log(f"{label}: {len(cells)} cells, {len(coarse)} venue cells, blocks of {block}×{block}")
    lat, lon = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    try:  # the box every OSM question can reach: venue cells whole, buildings and terraces past the edge
        pbf = geofabrik_pbf(lat, lon)
        reach = union(coarse), padded(rect, 600)
        _osm = OSM(download(pbf, extracts), (min(r[0] for r in reach), min(r[1] for r in reach),
                                             max(r[2] for r in reach), max(r[3] for r in reach)))
    except SourceError as e:
        log(f"{label}: OpenStreetMap unavailable ({e})")
        return len(cells), [(c.key, "all", str(e)) for c in cells]
    # A French extract means French communes; elsewhere the geo API would answer [] a cell at a time.
    french = "/europe/france" in pbf
    if "venues" in layers:
        for c in coarse:
            if not done(out, "venues", c): write(path(out, "venues", c), osm_venues(c))
    for group in blocks(cells, block):
        communes, need_terraces, need_buildings = {}, [], []
        for c in group:
            if french and in_france(c.lat, c.lon):
                communes[c.key] = read(path(out, "communes", c))
                if communes[c.key] is None:
                    try:
                        communes[c.key] = commune(c.lat, c.lon)
                        write(path(out, "communes", c), communes[c.key])
                    except SourceError as e:
                        failures.append((c.key, "communes", str(e)))
            # A box of départements (the petite couronne) keeps only their cells.
            if departements and not any(x["code"][:2] in departements for x in communes.get(c.key) or []):
                outside.add(c.key); continue
            if "buildings" in layers and not done(out, "buildings", c): need_buildings.append(c)
            if "terraces-v2" in layers and not done(out, "terraces-v2", c):
                if french and communes.get(c.key) is None:
                    failures.append((c.key, "terraces-v2", "commune unknown, so the permit feed is too")); continue
                need_terraces.append(c)
        skipped += sum(1 for c in group if c not in need_buildings and c not in need_terraces and c.key not in outside)
        if need_buildings: do_buildings(need_buildings, out, failures)
        if need_terraces: do_terraces(need_terraces, communes, out, failures)
    failed = {k for k, _, _ in failures}
    log(f"{label}: {len(cells) - len(outside) - len(failed)} of {len(cells) - len(outside)} cells complete ({skipped} already there), "
        f"{len(failed)} failed, {time.monotonic() - started:.0f} s")
    return len(cells) - len(outside), failures

def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("--cities", help="a cities.json: [{city, district?, lat, lon, half_km}] or [{city, district?, box: [s, w, n, e], departements?}]")
    a.add_argument("--city", help="a label: feeds are picked per cell, by commune or box"); a.add_argument("--lat", type=float); a.add_argument("--lon", type=float)
    a.add_argument("--half-km", type=float, default=0.5)
    a.add_argument("--block", type=int, default=3, help="cells per side asked of a source at once (default 3)")
    a.add_argument("--out", default="tiles")
    a.add_argument("--layers", default=",".join(LAYERS), help="which to write (default all): " + ", ".join(LAYERS))
    a.add_argument("--extracts", default="extracts", help="where Geofabrik extracts are kept between runs (default ./extracts)")
    args = a.parse_args()
    if args.cities:
        with open(args.cities) as f: entries = json.load(f)
    elif args.city and args.lat is not None and args.lon is not None:
        entries = [{"city": args.city, "lat": args.lat, "lon": args.lon, "half_km": args.half_km}]
    else:
        a.error("give --cities, or --city with --lat and --lon")
    total, failures = 0, []
    for entry in entries:
        label = entry["city"] + (f" ({entry['district']})" if entry.get("district") else "")
        n, f = run(label, area(entry, args.half_km), args.out, args.block, args.layers.split(","), args.extracts, entry.get("departements"))
        total += n
        failures += [(label,) + x for x in f]
    failed = {(x[0], x[1]) for x in failures}
    if failures:
        log(f"\nFailures ({len(failed)} cells of {total}):")
        for label, key, layer, reason in failures: log(f"  {label} {key} {layer}: {reason}")
    if total and len(failed) / total > 0.2:
        log(f"More than 20% of cells failed ({len(failed)} of {total})."); sys.exit(1)

if __name__ == "__main__":
    main()
