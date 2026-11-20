#!/usr/bin/env python3
"""Pre-cut city-data tiles for Compromise's sunny filter and venue list.

Writes one JSON array per ~200 m
cell, keyed "<int(lat*500)>,<int(lon*500)>", under buildings/,
terraces-v2/, streets/ and communes/ (French cells: the build and the coverage feed
read them, the upload leaves them out), and one per ~2 km cell, keyed
"<int(lat*50)>,<int(lon*50)>", under venues/. Beside the first two,
sources/ names what built each cell ([{"permits": city|null,
"buildings": city|"IGN"|"OSM"|city+"+OSM", "boundaries"?: source}]) — what the app credits.
streets/ is built only for the areas whose entry says "streets": true: each
item is one OpenStreetMap street cut at the cell's edges, a flat array
[kind, y0, x0, dy1, dx1, …] (kind 1 main road, 2 street, 3 pedestrian),
with the way's name after the kind where it has one, [kind, "name", y0, …] (2026-10-08),
coordinates in 1e-5° steps, the first from the key's corner (key / 500),
each next from the one before. numbers/ (2026-10-10), built with streets/ on
its cells: [["name", lowest, highest], …] sorted by name, one row per named
street of the cell's streets/ tile with at least two distinct house numbers
inside the cell — OpenStreetMap's addr:housenumber (its leading digits:
"12bis" is 12) on nodes, buildings and along addr:interpolation ways, whose
addr:street, else associatedStreet relation's name, matches the name without
case, accents or apostrophes; no file for a cell with none. streets-main/ is the same on ~4 km cells
(1/25°, keyed "<int(lat*25)>,<int(lon*25)>"): main roads only, 1e-4° steps
from key / 25. places/, on the same cells: [{"name", "kind" (suburb,
quarter, neighbourhood), "coordinate", "admin_level"?}]: "admin_level" (an
integer, OSM's) only when the name comes from or matches an administrative
boundary relation (Paris: 9 the arrondissements, 10 the quartiers
administratifs), absent otherwise, so a reader of the first format reads
it unchanged (2026-10-06); "name_en" (OSM's name:en) the same way, only
where it exists and differs from "name": Tokyo's, Seoul's or Kyiv's names
are in their own scripts (2026-10-09). Both only where streets/ is.
boundaries/ (2026-10-10), on places/'s cells and only where streets/ is: [{"name", "level", "source",
"polygons"}], each neighbourhood's outline whole in the cell of its label point (the places/ place of
the same name inside it, whose name is then written; else a point inside it), not cut at the cell's
edges. "level" is the source's word for the unit ("quartier", "Ortsteil") or OSM's admin_level,
"source" the key sources/ names; "polygons" [[ring, …], …], per polygon its outer ring
(counter-clockwise) then its holes (clockwise), each ring [y0, x0, dy1, dx1, …] open, in 1e-5° steps,
the first from the key's corner (key / 25), each next from the one before. Simplified at 5 m with
shapely's coverage_simplify over each source's set, so neighbours keep sharing an edge, then rounded
on the 1e-5° grid. One source per city (AREA_SOURCES: the city's portal, else a national agency,
else OpenStreetMap's boundaries or place polygons from the extract), fetched once per run; sources/
names it ("boundaries") in each ~200 m cell whose centre one of its polygons holds.
buildings-v2/ is what the store holds (buildings/ stays local, read by coverage.py; 2026-10-08):
the same footprints as
[height or null, y0, x0, dy1, dx1, …], 1e-5° steps from key / 500, heights in
0.5 m steps (BUILDINGS.md).
transit/ (2026-10-13), on places/'s cells and only where streets/ is: [{"mode" (subway, tram, train, light_rail, bus, trolleybus, aerialway),
"ref", "colour"? ("#RRGGBB"), "network"?, "operator"?, "stops": [name, …], "points": [y0, x0, dy1, dx1, …], "shape": [[y0, x0, …], …]}],
OpenStreetMap's route relations (the route_master's ref, colour, network and operator where the route has none), each line
whole in every cell one of its stops falls in; points one per stop, in 1e-5° steps, the first from the key's corner
(key / 25), each next from the one before; shape the track's runs coded the same way, each from the corner,
simplified at 10 m. Long-distance trains, coaches, night and tourist lines are left out; one direction kept where the other stops at the
same names.

    make_tiles.py --city Paris --lat 48.8719 --lon 2.3316 --half-km 0.5 --out ./tiles
    make_tiles.py --cities cities.json --out ./tiles --layers terraces-v2,venues
    make_tiles.py --cities cities.json --out ./tiles --layers sources --store https://tiles.alephb.uk/tiles/

With --store (the published tiles' base URL), a sources/ tile keeps the field a run does not build:
a run building terraces keeps the published tile's "buildings", one building buildings keeps its
"permits". The `sources` layer writes nothing else: it finds each cell's building source the way the
buildings layer does (its tiles not written) for the published sources tiles that lack it.

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

Needs Python 3 and osmium-tool on the PATH; the standard library only, but for boundaries/:
shapely ≥ 2.1, pyproj and pyshp (requirements.txt).
Derived tiles that include OpenStreetMap data are ODbL: publish them under
ODbL with the credit "© OpenStreetMap contributors".
"""
import argparse, concurrent.futures, csv, datetime, io, json, math, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request, zoneinfo
from builder import extract, store
from builder.cells import area, blocks, bounds, cells_in, clip, COARSE, contains, in_feed, in_france, index, M, MAIN, metres, padded, union
from builder.net import download, get, get_json, keyed_host, log, redact, SECRET_ENVS, SourceError
from builder.projections import mercator_to_wgs84, rd_new_to_wgs84, utm_to_wgs84
from builder.shapes import compact_buildings, estimated, number, unique
from builder.formats import _zip_members, xlsx_rows
from builder.names import folded, MATCH_REACH, names_match, normalised_name
from builder.streets import MAIN_UNIT, number_rows, street_tiles
from builder.transit import transit_tiles
from builder.osmium import buckets, geofabrik_pbf
from builder.places import place_key
from builder.overture import OVERTURE_CITIES, overture_facts, overture_places
from builder.extract import OSM, osm_addresses, osm_main_streets, osm_places, osm_streets, osm_terraces, osm_venues
from builder.apis import arcgis, arcgis_filter, in_bbox, opendatasoft, socrata, socrata_filter, wfs
from builder.buildings_table import building_source, CITY_BUILDINGS, COMBINE, FETCH_BUILDINGS, next_source
from builder.store import done, in_store, LAYERS, note_sources, path, published_items, published_sources, read, STORE_THREADS, STREET_LAYERS, write
from builder.permit_table import PERMIT_CITIES

# The scripts and tests that import names from make_tiles still find them here while they move to builder/
# (rebound state excepted: builder.store.STORE, builder.store.LISTED, builder.extract._osm).
import builder.cells, builder.net, builder.projections, builder.shapes, builder.formats, builder.names, builder.streets, builder.transit, builder.osmium, builder.places, builder.venues, builder.overture, builder.extract, builder.apis, builder.buildings_citygml, builder.buildings_measured, builder.buildings_vector, builder.buildings_table, builder.store, builder.permit_table
for _m in [builder.cells, builder.net, builder.projections, builder.shapes, builder.formats, builder.names, builder.streets, builder.transit, builder.osmium, builder.places, builder.venues, builder.overture, builder.extract, builder.apis, builder.buildings_citygml, builder.buildings_measured, builder.buildings_vector, builder.buildings_table, builder.store, builder.permit_table]:
    globals().update((k, v) for k, v in vars(_m).items() if not k.startswith("__") and k not in builder.REBOUND)

TERRACE_RADIUS = 400          # metres around the cell's centre: the radius the density gate is calibrated on
# Every footprint whose box comes within this of the cell: the app's reach
# plus a margin (its docs; Opéra, 2026-09-24: 690 KB → 280 KB a cell against
# a 400 m reach). Raise it if that reach grows.
BUILDING_REACH = 200
BUILDING_PAD = 250            # fetched around a block: some feeds select by a footprint's centre, not its outline



def permit_items(features, c, point="geo_point_2d"):
    """GeoJSON permits → terraces."""
    out = []
    for f in features:
        p = f.get("properties") or {}
        g = f.get("geometry") or {}
        if g.get("type") == "Point": lon, lat = g["coordinates"][:2]
        elif g.get("type") == "MultiPoint" and g.get("coordinates"): lon, lat = g["coordinates"][0][:2]  # Toronto
        elif isinstance(p.get(point), dict): lat, lon = p[point]["lat"], p[point]["lon"]
        elif isinstance(p.get("geo_point_2d"), dict): lat, lon = p["geo_point_2d"]["lat"], p["geo_point_2d"]["lon"]
        elif g.get("type") in ("Polygon", "MultiPolygon"):  # Vilnius: the mean vertex
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
        # Washington DC: the kind text says enclosed ("New Sidewalk Cafe Enclosed", not "Un-Enclosed") or not.
        elif c.get("enclosed"): kind = "TERRASSE FERMEE" if c["enclosed"] in kind else "TERRASSE OUVERTE"
        item = {"kind": kind, "coordinate": {"latitude": lat, "longitude": lon}}
        name = p.get(c["name"]) if c["name"] else None
        if isinstance(name, str) and c.get("clean"): name = CLEAN[c["clean"]](name)
        if name: item["name"] = name
        out.append(item)
    return out

def cut_before_first_digit(text):
    """Camden's name is the full address: "Goodfare Italian Restaurant  26 - 28 Parkway…" ."""
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

def riga_venue_name(text):
    """Riga's nosaukums is the holder's company: "Muca Bistro Bar SIA", "SIA Piga Avotu" — less its legal form."""
    name = re.sub(r"^(SIA|AS|IK)\s+|\s+(SIA|AS|IK)$", "", text.strip()).strip(" \"'“”„")
    return name or None

def toronto_name(text):
    """CaféTO's OPERATOR_NAME: "None" and "PUBLIC PARKLET" name no venue."""
    return None if text.strip().upper() in ("NONE", "PUBLIC PARKLET") else text.strip()

def helsinki_venue_name(text):
    """Helsinki's nimi: "Terassialue: On The Rocks", "Terassialue  25m2 Chaos Bar", "Terassialueet: Oluthuone Haavi",
    "Kesäterassi Kallionhovi" — the venue after the area word and any size; a bare "Talviterassi" names none."""
    name = re.sub(r"^\s*(Terassi\s?alue(et)?|Terassalue|Kesäterassi|Talviterassi)\s*:?\s*(\d+\s*m2\s*)?", "", text, flags=re.I)
    return name.strip(" \t\"'“”„:") or None

def seattle_venue_name(text):
    """Seattle's PROJECT_NAME: "Mecca | Sidewalk Cafe on Queen Anne Ave N" names the venue before the pipe. The free-text
    forms ("SIDEWALK CAFE", "HOPVINE PUB SIGNAGE AND SIDEWALK CAFE") and a first part that is a kind ("Outdoor Dining |…")
    name none — a wrong name would block the door rule."""
    if "|" not in text: return None
    venue = text.split("|", 1)[0].strip(" \t\"'")
    if not re.search(r"[A-Za-z]", venue) or re.search(r"cafe|dining|sign|streatery|transfer|ownership|street use", venue, re.I): return None
    return venue

def formerly_cut(text):
    """Orlando's project_name: "The Cask (formerly Slyfox Game Room)" is The Cask."""
    return re.sub(r"\s*\((?:formerly|fka)\b[^)]*\)", "", text, flags=re.I).strip() or None

def not_applicable(text):
    """"N/A", a blank or a bare number names no company (Washington DC's ApplicantCompany on most rows)."""
    name = text.strip()
    return None if name.upper() in ("N/A", "NA", "NONE") or not re.search(r"[A-Za-z]", name) else name

CLEAN = {"digit": cut_before_first_digit, "boulevard": boulevard_name, "vilnius": vilnius_venue_name,
         "riga": riga_venue_name, "toronto": toronto_name, "helsinki": helsinki_venue_name, "seattle": seattle_venue_name,
         "n/a": not_applicable, "formerly": formerly_cut}
_venue_tiles = {}   # {key: items} read once per run

def venue_tiles_around(lat, lon):
    """The keys of the published venue tiles a venue within MATCH_REACH of the point can sit in (one to four)."""
    s, w, n, e = padded((lat, lon, lat, lon), MATCH_REACH)
    return sorted({f"{index(a, COARSE)},{index(b, COARSE)}" for a in (s, n) for b in (w, e)})

def venues_near(lat, lon, city):
    """The published venue tiles' items a venue within MATCH_REACH of the point can be among. A venue tile that could not
    be read fails the feed (half an answer is not a tile), a missing one holds no venue."""
    venues = []
    for key in venue_tiles_around(lat, lon):
        if key not in _venue_tiles: _venue_tiles[key] = published_items("venues", key, bust=True)
        if isinstance(_venue_tiles[key], SourceError): raise SourceError(f"{city}: venues/{key}: {_venue_tiles[key]}")
        venues += _venue_tiles[key]
    return venues

def surviving(items, city):
    """The permits a venue of today stands under: a named one with a venue within MATCH_REACH under a matching name."""
    out = []
    for t in items:
        wanted = normalised_name(t.get("name"))
        if not wanted: continue
        lat, lon = t["coordinate"]["latitude"], t["coordinate"]["longitude"]
        if any(metres(lat, lon, v["coordinate"]["latitude"], v["coordinate"]["longitude"]) <= MATCH_REACH
               and names_match(normalised_name(v.get("name")), wanted) for v in venues_near(lat, lon, city)):
            out.append(t)
    return out

# Riga's permits name the holder's company ("Funny Fox SIA" for the bar Jautrā lapsa), and the feed has no trading-name
# field (GEO RĪGA's two terrace services carry the same nosaukums, 2026-11-11). A `match="trading"` row writes, as the
# permit's name, the name of the venue at its spot: the nearest named venue of the published venues/ tiles (OpenStreetMap's)
# within TRADING_REACH, when the next one stands at least TRADING_MARGIN farther. A holder's name that already matches a
# venue within NAME_REACH (the reach a venue's terrace is looked for in) is kept, and so is an unclear spot's.
NAME_REACH, TRADING_REACH, TRADING_MARGIN = 30, 15, 5

def trading_name(t, venues):
    """The permit `t` under the trading name of the venue at its spot, or as it came (see above)."""
    lat, lon = t["coordinate"]["latitude"], t["coordinate"]["longitude"]
    near = sorted(((metres(lat, lon, v["coordinate"]["latitude"], v["coordinate"]["longitude"]), v["name"])
                   for v in venues if v.get("name")), key=lambda x: x[0])
    wanted = normalised_name(t.get("name"))
    if wanted and any(d <= NAME_REACH and names_match(normalised_name(n), wanted) for d, n in near): return t
    if not near or near[0][0] > TRADING_REACH: return t
    if len(near) > 1 and near[1][0] - near[0][0] < TRADING_MARGIN: return t
    return dict(t, name=near[0][1])

def trading_names(items, city):
    return [trading_name(t, venues_near(t["coordinate"]["latitude"], t["coordinate"]["longitude"], city)) for t in items]

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

_whole = {}  # whole-file feeds, fetched once per run

def madrid_all(c):
    """Madrid CSV: ';', quoted, a BOM; "Abierta" only; any of four light-structure flags means enclosed."""
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
    """Seville GeoJSON: a frontage line at its mean vertex; a permit past its end date dropped."""
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
    """Barcelona: CKAN's newest CSV resource, WGS84 LATITUD/LONGITUD, no name. Read through the datastore API:
    the CSV's download URL answers a bot-detection page since 2026-10 (302 to /challenge)."""
    api = f"https://{c['host']}/data/api/3/action/"
    package = get_json(api + f"package_show?id={c['dataset']}")
    resource = next(r["id"] for r in package["result"]["resources"] if (r.get("format") or "").upper() == "CSV")
    rows = []
    while True:
        page = get_json(api + "datastore_search?" + urllib.parse.urlencode(
            {"resource_id": resource, "limit": "10000", "offset": str(len(rows))}))["result"]
        rows += page["records"]
        if not page["records"] or len(rows) >= page["total"]: break
    out = []
    for r in rows:
        lat, lon = number(r.get("LATITUD")), number(r.get("LONGITUD"))
        if lat is None or lon is None: continue
        out.append({"kind": "TERRASSE", "coordinate": {"latitude": lat, "longitude": lon}})
    if not out: raise SourceError("Barcelona: no terrace parsed")  # as for Madrid
    return out

def sf_register(host, licence_type):
    """Every issued licence of one type on a council's "sf/control" public register (Southwark, Tower Hamlets): the
    page hands out a session (in its links, ;jsessionid=…) and a CSRF token, which the register's search needs."""
    page = get(f"https://{host}/sf/control/publicregister").decode("utf-8", "replace")
    session = re.search(r";jsessionid=[\w.]+", page)
    token = re.search(r'name="csrf-token" content="([^"]+)"', page)
    if not (session and token): raise SourceError(f"{host}: no session or CSRF token on the register's page")
    data = urllib.parse.urlencode({"licenceType": licence_type, "licenceStatus": "licence", "keyword": " ", "type": "licence"})
    answer = get(f"https://{host}/sf/control/searchPublicRegister{session.group(0)}", data=data.encode(), parse=json.loads,
                 headers={"X-CSRF-Token": token.group(1)})
    return json.loads(answer["results"])

def southwark_all(c, today=None):
    """Southwark's pavement licences: approved, unexpired on the build's day (approvedEndDate dd/mm/yyyy), at the
    register's own "lat,lon", one terrace a point (a renewal and a temporary licence, or Borough Market's traders, share
    one); no name (the register names the holder, never kept)."""
    today = today or datetime.date.today()
    out, seen = [], set()
    for r in sf_register(c["host"], "Pavement"):
        if r.get("status") != "APP_APPROVED": continue
        try: end = datetime.datetime.strptime(r.get("approvedEndDate") or "", "%d/%m/%Y").date()
        except ValueError: continue
        lat, _, lon = (r.get("latLng") or "").partition(",")
        lat, lon = number(lat.strip()), number(lon.strip())
        if end < today or lat is None or lon is None or (lat, lon) in seen: continue
        seen.add((lat, lon))
        out.append({"kind": "TERRASSE", "coordinate": {"latitude": lat, "longitude": lon}})
    return out

def csv_all(c):
    """A whole CSV (La Rochelle, San Diego): comma-separated, quoted, a BOM; the point a "lat,lon" column, or a
    (lat, lon) pair of columns, the kind a column kept only for the values in `keep`, those in `covered` written
    TERRASSE FERMEE, and `as_kind` naming the kind written for every kept row; `where` keeps a row only when
    each of its columns holds one of the listed values; no `name` column, no name (the door rule)."""
    text = get(f"https://{c['host']}/{c['dataset']}").decode("utf-8-sig")
    kind, out = c["kinds"][0], []
    for r in csv.DictReader(io.StringIO(text)):
        if r[kind] not in c["keep"]: continue
        if any(r.get(k) not in v for k, v in c.get("where", {}).items()): continue
        if isinstance(c["point"], tuple): lat, lon = (r[k] or "" for k in c["point"])
        else: lat, _, lon = (r[c["point"]] or "").partition(",")
        lat, lon = number(lat.strip()), number(lon.strip())
        if lat is None or lon is None: continue
        if c.get("grid"): lat, lon = c["grid"](lon, lat)  # a projected pair, given (northing, easting): Orlando
        item = {"kind": "TERRASSE FERMEE" if r[kind] in c.get("covered", []) else c.get("as_kind", r[kind]),
                "coordinate": {"latitude": lat, "longitude": lon}}
        name = r[c["name"]].strip() if c["name"] and r[c["name"]] else None
        if name and c.get("clean"): name = CLEAN[c["clean"]](name)
        if name: item["name"] = name
        out.append(item)
    if not out: raise SourceError(f"{c['city']}: no terrace parsed")  # as for Madrid
    return out

def gothenburg_all(c):
    """Gothenburg's serving licences: one ';'-separated CSV with a BOM, read whole. A terrace where Serveringstyper lists
    Uteservering and the premises serve the public (ServeringTill names Allmänheten, alone or with closed companies);
    the point the WGS84 lat/long columns (a few rows have none), the name Namn (the premises, never the holder)."""
    text = get(f"https://{c['host']}/{c['dataset']}").decode("utf-8-sig")
    out = []
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        if "Uteservering" not in [v.strip() for v in (r.get("Serveringstyper") or "").split(",")]: continue
        if "Allmänheten" not in (r.get("ServeringTill") or ""): continue
        lat, lon = number(r.get("lat")), number(r.get("long"))
        if lat is None or lon is None: continue
        item = {"kind": "TERRASSE", "coordinate": {"latitude": lat, "longitude": lon}}
        if (r.get(c["name"]) or "").strip(): item["name"] = r[c["name"]].strip()
        out.append(item)
    if not out: raise SourceError("Gothenburg: no terrace parsed")  # as for Madrid
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

_fields_logged = set()

def stockholm(c, rect, today=None):
    """Stockholm's markupplåtelser (WFS 1.1.0 behind a key in the URL path): the uteservering kind of ärendekategori,
    unexpired. The key's absence never reaches here (permit_city leaves the row out); it is checked again for check_data."""
    host = keyed_host(c)
    if host is None: raise SourceError(f"{c['city']}: {c['key']} is not set")
    s, w, n, e = rect
    params = {"service": "WFS", "version": "1.1.0", "request": "GetFeature", "typeName": c["dataset"],
              "outputFormat": "application/json", "srsName": "EPSG:4326", "bbox": f"{w},{s},{e},{n},EPSG:4326"}
    try:
        features = get_json(f"https://{host}?" + urllib.parse.urlencode(params)).get("features") or []
    except SourceError as error:
        raise SourceError(redact(str(error))) from None
    if features and c["city"] not in _fields_logged:  # field names only, never values: the schema the row guessed at
        _fields_logged.add(c["city"])
        log(f"    {c['city']} permits: fields {sorted((features[0].get('properties') or {}).keys())}")
    today = today or datetime.date.today().isoformat()
    kept = []
    for f in features:
        p = {folded(k): v for k, v in (f.get("properties") or {}).items()}
        if "uteservering" not in folded(str(p.get("arendekategori") or "")): continue
        # Expired: an end date (slutdatum, tilldatum, giltig_till, datum_tom…) before today.
        ends = [str(v)[:10] for k, v in p.items() if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v)
                and re.search(r"slut|till|tom$|upphor", k)]
        if any(d < today for d in ends): continue
        g = f.get("geometry") or {}
        if g.get("type") == "Point" and len(g.get("coordinates") or []) >= 2 and g["coordinates"][0] > 45:
            g = dict(g, coordinates=g["coordinates"][1::-1])  # latitude first (59, 18): an EPSG:4326 axis order
        kept.append(dict(f, geometry=g))
    return [t for t in permit_items(kept, c)
            if s <= t["coordinate"]["latitude"] <= n and w <= t["coordinate"]["longitude"] <= e]

def oslo(c, rect):
    """Oslo: latitude-first bbox, UTM 32N answers, outdoor hours only; the holder never asked."""
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
    """A feed's terraces in the rect; a `match="venues"` row keeps only those a venue of today stands under, a
    `match="trading"` row names each after the venue at its spot."""
    items = fetched_permits(c, rect)
    if c.get("match") == "venues": return surviving(items, c["city"])
    return trading_names(items, c["city"]) if c.get("match") == "trading" else items

def fetched_permits(c, rect):
    s, w, n, e = rect or (None,) * 4
    if c.get("shape") == "oslo": return oslo(c, rect)
    if c.get("shape") == "stockholm": return stockholm(c, rect)
    if c.get("shape") == "amsterdam": return amsterdam(c, rect)
    if c.get("shape") == "madrid": return whole_file(c, rect, madrid_all)
    if c.get("shape") == "seville": return whole_file(c, rect, seville_all)
    if c.get("shape") == "barcelona": return whole_file(c, rect, barcelona_all)
    if c.get("shape") == "csv": return whole_file(c, rect, csv_all)
    if c.get("shape") == "southwark": return whole_file(c, rect, southwark_all)
    if c.get("shape") == "gothenburg": return whole_file(c, rect, gothenburg_all)
    if c.get("shape") == "geojson":  # a static GeoJSON of points, fetched whole once per run (Toronto)
        return whole_file(c, rect, lambda c: permit_items(get_json(f"https://{c['host']}/{c['dataset']}").get("features") or [], c))
    if c.get("shape") == "socrata":
        where = f"within_box({c['point']}, {n}, {w}, {s}, {e})" + (f" AND {socrata_filter(c['filter'])}" if c.get("filter") else "")
        return permit_items(socrata(c["host"], c["dataset"], where, ",".join(f for f in [c["name"], c["point"]] if f)), c)
    if c.get("shape") == "wfs" and c.get("whole") and rect:  # a small layer, read whole once per run (Vienna)
        return whole_file(c, rect, lambda c: fetched_permits(c, None))
    if c.get("shape") == "wfs":
        # WFS 1.1.0: longitude first, and the
        # bbox needs its own trailing CRS or the server silently answers zero.
        params = {"service": "WFS", "version": "1.1.0", "request": "GetFeature",
                  "typeName": c["dataset"], "outputFormat": "json", "srsName": c.get("srs", "EPSG:4326")}
        if rect is None:  # the whole layer
            if c.get("filter"): params["CQL_FILTER"] = c["filter"]
        elif c.get("filter"):
            # Copenhagen's GeoServer refuses bbox alongside CQL_FILTER.
            params["CQL_FILTER"] = f"{c['filter']} AND BBOX({c.get('geom', 'wkb_geometry')},{w},{s},{e},{n},'EPSG:4326')"
        else:
            params["bbox"] = f"{w},{s},{e},{n},EPSG:4326"
        features = get_json(f"https://{c['host']}?" + urllib.parse.urlencode(params)).get("features") or []
        if c.get("srs") == "EPSG:3857":  # Thessaloníki: metres to the millimetre, where its degrees are rounded to ~100 m
            for f in features:
                g = f.get("geometry") or {}
                if g.get("type") in ("Point", "MultiPoint"):
                    pts = g["coordinates"] if g["type"] == "MultiPoint" else [g["coordinates"]]
                    g.update(type="MultiPoint", coordinates=[mercator_to_wgs84(*p[:2])[::-1] for p in pts])
        return permit_items(features, c)   # polygons land on their mean vertex
    if c.get("shape") == "arcgis":
        fields = [f for f in [c["name"]] + c["kinds"] if f]
        return permit_items(arcgis(f"https://{c['host']}/{c['dataset']}", rect, fields, arcgis_filter(c.get("filter") or "1=1"),
                                   c.get("oid", "OBJECTID")), c)
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



# boundaries/ (2026-10-10): each neighbourhood's real outline, on places/'s 1/25° cells. A polygon is written once and
# whole in the cell of its label point: the places/ point of the same name inside it (that place's name is then
# written), else a point inside it. Simplified over the whole set of a source, so neighbours keep sharing their
# edge, then rounded on the absolute 1e-5° grid; rings as buildings-v2/'s, from the cell's corner (key / 25).
# Needs shapely ≥ 2.1 (coverage_simplify), pyproj and pyshp (requirements.txt); the other layers need none.
BOUNDARY_TOLERANCE = 5        # metres
BOUNDARY_UNIT = 100_000       # 1e-5° steps

def area_row(city, source, fetch, name, level, keep=None, dissolve=None, key=None):
    """One source of a city's neighbourhoods. `fetch(rect)` → [(properties, shapely geometry in lon/lat)] for the
    tiled area's box; `name` a field or a function of the properties (a falsy name leaves the feature out);
    `level` the source's own word for the unit, or a function of the properties; `keep` a filter; `dissolve` a
    function of the properties whose equal values are merged into one feature (True: by name); `key` the
    environment variable holding the source's API key (unset: the city gets no boundaries, said once)."""
    if key: SECRET_ENVS.append(key)
    return dict(city=city, source=source, fetch=fetch, name=name, level=level, keep=keep, dissolve=dissolve, key=key)

def _crs_transform(geoms, crs):
    """lon/lat geometries from `crs` (anything pyproj reads: "EPSG:3857", a .prj's WKT); WGS84 and its kin as they are."""
    if crs is None: return geoms
    import numpy, pyproj, shapely
    source = pyproj.CRS.from_user_input(crs)
    code = source.to_epsg(min_confidence=70)
    if code in (4326, 4258, 6668, 4612): return geoms   # WGS84, ETRS89, JGD2011, JGD2000: within a metre
    # An ESRI .prj names no datum shift (Turin's Monte Mario): its EPSG code brings PROJ's.
    t = pyproj.Transformer.from_crs(f"EPSG:{code}" if code else source, "EPSG:4326", always_xy=True)
    return [shapely.transform(g, lambda xy: numpy.column_stack(t.transform(xy[:, 0], xy[:, 1]))) for g in geoms]

def _feature_crs(collection):
    name = ((collection.get("crs") or {}).get("properties") or {}).get("name") or ""
    code = ((collection.get("crs") or {}).get("properties") or {}).get("code")
    if code: name = f"EPSG:{code}"
    return None if not name or re.search(r"CRS84|EPSG:+(4326|4258)$", name) else name

def _shaped(features, crs=None):
    """GeoJSON features → [(properties, shapely geometry)], polygons only, in lon/lat."""
    import shapely.geometry
    out = [(f.get("properties") or {}, shapely.geometry.shape(f["geometry"])) for f in features
           if (f.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon")]
    return list(zip([p for p, _ in out], _crs_transform([g for _, g in out], crs)))

def geojson_source(url, crs=None):
    """One GET of a FeatureCollection (an Opendatasoft export, a WFS, Socrata, a plain file); its own "crs" read."""
    def fetch(rect):
        root = get_json(url)
        return _shaped(root.get("features") or [], crs or _feature_crs(root))
    return fetch

def ogc_source(url):
    """OGC API Features items, its "next" links followed."""
    def fetch(rect):
        features, at = [], url
        while at:
            page = get_json(at)
            features += page.get("features") or []
            at = next((l["href"] for l in page.get("links") or [] if l.get("rel") == "next" and page.get("features")), None)
        return _shaped(features)
    return fetch

def arcgis_source(url, fields, where="1=1", oid="OBJECTID"):
    """An ArcGIS REST layer over the area's box, paged on its OID field (arcgis())."""
    return lambda rect: _shaped(arcgis(url, rect, fields, where, oid))

def wfs_source(url, type_name, key=None):
    """A WFS 2.0 layer over the area's box, as GeoJSON in EPSG:4326 (wfs()); `key` the environment variable whose
    value fills the URL's {key}."""
    def fetch(rect):
        at = url.replace("{key}", urllib.parse.quote(os.environ.get(key, "").strip(), safe="")) if key else url
        return _shaped(wfs(at, {"TYPENAMES": type_name, "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326"}, rect))
    return fetch

def _shapefile_features(members, encoding=None, crs=None):
    """Every polygon record of each Shapefile in a zip's members, holes and parts kept (pyshp), in lon/lat; `crs`
    for a Shapefile that comes without its .prj (Palermo's)."""
    import shapefile, shapely.geometry
    out = []
    for shp in (m for m in members if m.lower().endswith(".shp")):
        part = lambda x: next((io.BytesIO(members[m]) for m in members if m.lower() == shp[:-4].lower() + x), None)
        cpg, prj = part(".cpg"), part(".prj")
        code = encoding or (cpg.read().decode("ascii", "replace").strip() if cpg else "latin-1")
        reader = shapefile.Reader(shp=part(".shp"), shx=part(".shx"), dbf=part(".dbf"), encoding=code)
        records = [(r.record.as_dict(), shapely.geometry.shape(r.shape.__geo_interface__)) for r in reader.iterShapeRecords()
                   if r.shape.shapeType in (5, 15, 25) and r.shape.points]
        geoms = _crs_transform([g for _, g in records], prj.read().decode("latin-1") if prj else crs)
        out += list(zip([p for p, _ in records], geoms))
    return out

def shapefile_source(*urls, encoding=None, crs=None):
    """Zipped Shapefiles, each fetched whole (a few hundred KB)."""
    return lambda rect: [f for url in urls for f in _shapefile_features(_zip_members(get(url)), encoding, crs)]

def posted_shapefile_source(url, form, encoding=None):
    """A zipped Shapefile answered to a form POST (Seoul's portal)."""
    return lambda rect: _shapefile_features(_zip_members(get(url, urllib.parse.urlencode(form).encode())), encoding)

def ckan_wkt_source(url, field):
    """A CKAN DataStore table whose `field` holds WKT (Athens)."""
    def fetch(rect):
        import shapely.wkt
        records = get_json(url)["result"]["records"]
        return [(r, shapely.wkt.loads(r[field])) for r in records if r.get(field)]
    return fetch

def csv_geojson_source(url, field="geo_shape"):
    """A CSV file whose `field` holds each row's GeoJSON geometry (Grenoble's: its data4citizen GeoJSON export
    answers an empty collection one request in two)."""
    def fetch(rect):
        rows = csv.DictReader(io.StringIO(get(url).decode("utf-8-sig")))
        return _shaped([{"properties": r, "geometry": json.loads(r[field])} for r in rows if r.get(field)])
    return fetch

def datagovsg_source(dataset):
    """data.gov.sg: poll-download answers a signed URL, valid an hour, for the GeoJSON."""
    def fetch(rect):
        link = get_json(f"https://api-open.data.gov.sg/v1/public/api/datasets/{dataset}/poll-download")["data"]["url"]
        return _shaped(get_json(link).get("features") or [])
    return fetch

def oslo_source(rect):
    """Oslo's 98 delbydeler: Geonorge's grunnkretser, each named by Oslo kommune's key (an XLSX: grunnkrets → delbydel)."""
    members = _zip_members(get("https://nedlasting.geonorge.no/geonorge/Basisdata/Grunnkretser/GeoJSON/Basisdata_03_Oslo_4258_Grunnkretser_GeoJSON.zip"))
    root = json.loads(next(v for k, v in members.items() if k.endswith("Grunnkrets_GeoJSON.geojson")).decode("utf-8-sig"))
    rows = xlsx_rows(get("https://www.oslo.kommune.no/get-file/2563152/7433a6d80e58a5128319f5e16325d3b94bda0c8e26d023d3fb19108c5c21040c"))
    head = rows[0]
    delbydel = {str(r[head.index("Grunnkrets_2017_nr")]).zfill(4): r[head.index("Delbydel_2017_navn")] for r in rows[1:]
                if len(r) == len(head) and r[head.index("Grunnkrets_2017_nr")]}
    for f in root["features"]: f["properties"]["delbydel"] = delbydel.get(str(f["properties"].get("grunnkretsnummer", ""))[4:])
    return _shaped(root["features"])

def estat_source(prefecture, wards):
    """e-Stat's 2020 census small areas (chōme), one zipped Shapefile per ward (JGD2011, cp932)."""
    url = "https://www.e-stat.go.jp/gis/statmap-search/data?dlserveyId=A002005212020&code={}{}&coordSys=1&format=shape&downloadType=5&datum=2011"
    return shapefile_source(*(url.format(prefecture, w) for w in wards), encoding="cp932")

def iris_head(name):
    """An IRIS name's part before its first hyphen between two names: "Ariane-Monzie" Ariane, "Saint-Roch-Ricolfi"
    Saint-Roch (a "Saint-", "Sainte-", "Notre-" or compass "Nord-Est" kept whole), "Cimiez" Cimiez. A numbered or
    "partie" piece is its whole: "Encagnane 2" Encagnane, "Arbois Partie Est" Arbois (Aix's); else a compass tail
    is dropped: "Murigny Sud-Est", "Saint-Rémi Nord Est et Sud", "Cernay-Centre" (Reims's), not "Croix du Sud"'s."""
    whole = re.sub(r"\s+(?:\d+|[Pp]artie\b.*)$", "", name)
    tail = whole == name and re.match(r"^(.*?)((?:[\s-]+(?:Nord|Sud|Est|Ouest|Centre|et))+)$", name)
    if tail and tail[1]:
        whole = tail[1]
        if whole.split()[-1] in ("du", "de", "des"): whole += re.match(r"[\s-]+\w+", tail[2])[0]
    name = whole
    m = re.match(r"^(.+?(?<!\bSaint)(?<!\bSainte)(?<!\bNotre)(?<!\bNord)(?<!\bSud))-(?=[A-ZÉÈ])", name)
    return m[1].strip() if m else name

def iris_source(insee):
    """IGN's IRIS of a commune (Géoplateforme WFS), each with "quartier": the head of its name (iris_head) where two
    IRIS or more of the commune share it ("Ariane-Monzie" and "Ariane-Ripert" are Ariane, "Cimiez" and "Cimiez-Valrose"
    Cimiez), else its own name. Dissolved by it, for a city that publishes no quartiers of its own (Nice, Ajaccio,
    Annecy, Aix-en-Provence, Biarritz, Rouen, Reims, Clermont-Ferrand, Caen, Amiens, Le Mans, Orléans)."""
    def fetch(rect):
        found = geojson_source(IGN_WFS + "?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES=STATISTICALUNITS.IRIS:contours_iris"
                               "&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326&CQL_FILTER=" + urllib.parse.quote(f"code_insee='{insee}'"))(rect)
        heads = [iris_head(str(p.get("nom_iris") or "")) for p, _ in found]
        for (p, _), head in zip(found, heads): p["quartier"] = head if heads.count(head) > 1 else p.get("nom_iris")
        return found
    return fetch

def osm_area_source(levels):
    """OpenStreetMap from the area's extract: administrative boundaries at the admin_levels (integers) or place
    polygons of the kinds (strings) in `levels`, in order: a later level's polygon is kept only where its inner
    point lies in no polygon kept before (Manila's districts, then the barangays of the cities around)."""
    def fetch(rect):
        import shapely, shapely.geometry
        found, (boundaries, places) = {}, extract._osm.areas(rect)
        for b in boundaries:
            if b["admin_level"] in levels and b["geometry"]:
                found.setdefault(b["admin_level"], []).append(({"name": b["name"], "level": b["admin_level"]}, shapely.geometry.shape(b["geometry"])))
        for tags, geometry in places:
            name = str(tags.get("name", "")).strip()
            if tags.get("place") in levels and name and tags.get("boundary") != "administrative":
                found.setdefault(tags["place"], []).append(({"name": name, "level": tags["place"]}, shapely.geometry.shape(geometry)))
        out = []
        for level in levels:
            kept = shapely.union_all([g for _, g in out]) if out else None
            for p, g in found.get(level, ()):
                if kept is not None and kept.contains(g.representative_point()): continue
                # One area mapped twice (a way and a relation of one name, Jerusalem's Har Tsiyon): the first kept.
                if any(q["name"] == p["name"] and h.intersects(g.representative_point()) for q, h in out): continue
                out.append((p, g))
        return out
    return fetch

def roman_part(name):
    """Athens's parts: "ΠΑΓΚΡΑΤΙ ΙΙ", "ΚΑΤΩ ΠΑΤΗΣΙΑ II", "ΠΡΟΜΠΟΝΑ2" are Pangrati, Kato Patisia, Prompona."""
    return re.sub(r"\s*(?:\b[IΙ]{1,3}|\d+)$", "", name).strip()

def logical_hebrew(name):
    """Tel Aviv's names keep a closing mark where right-to-left display shows it, at the start: "'נאות אפקה א" is
    Neot Afeka Alef, "נאות אפקה א'", and "(יפו ד' (גבעת התמרים" is "יפו ד' (גבעת התמרים)"."""
    lead = re.match(r"^[\"'(]+", name)
    if not lead: return name
    return name[lead.end():].strip() + lead[0][::-1].replace("(", ")")

def machi(p):
    """A Japanese chōme's town: 丸の内一丁目 → 丸の内."""
    return re.sub(r"[一二三四五六七八九十〇]+丁目$", "", p["S_NAME"] or "").strip()

ABS_SAL = "https://geo.abs.gov.au/arcgis/rest/services/ASGS2021/SAL/MapServer/0"
IGN_WFS = "https://data.geopf.fr/wfs/ows"
LINZ_WFS = "https://data.linz.govt.nz/services;key={key}/wfs"

# One row per source, from compromise's docs/research/neighbourhood_boundaries.csv (2026-10-10): the city's portal
# at the level people name, else a national statistics or mapping agency, else OpenStreetMap from the extract.
# Owner, 2026-10-10: every surveyed source is used (Chicago, São Paulo's CC BY-SA, Geneva included); Perth from
# ABS, not Landgate; Auckland from LINZ only with LINZ_API_KEY. The source key is what sources/ and each item name.
AREA_SOURCES = [
    area_row("Paris", "Paris", geojson_source("https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/quartier_paris/exports/geojson"),
             "l_qu", "quartier"),
    # The petite couronne: people name the commune (Montreuil, Vincennes), not its quartiers.
    area_row("Paris", "IGN", geojson_source(IGN_WFS + "?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES=ADMINEXPRESS-COG-CARTO.LATEST:commune"
             "&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326&CQL_FILTER=" + urllib.parse.quote("code_insee_du_departement IN ('92','93','94')")),
             "nom_officiel", "commune"),
    area_row("Lyon", "Lyon", geojson_source("https://data.grandlyon.com/geoserver/ville-de-lyon/ows?SERVICE=WFS&VERSION=2.0.0&request=GetFeature"
             "&typename=ville-de-lyon:vdl_vie_citoyenne.perimetre_de_quartier&outputFormat=application/json&SRSNAME=EPSG:4326"), "nom", "quartier"),
    area_row("Marseille", "Marseille", geojson_source("https://data.ampmetropole.fr/api/explore/v2.1/catalog/datasets/a7104f3c-e487-4af3-82ad-6197cedfaeb1/exports/geojson"),
             "nom", "quartier"),
    area_row("Toulouse", "Toulouse", geojson_source("https://data.toulouse-metropole.fr/api/explore/v2.1/catalog/datasets/"
             "recensement-population-2020-grands-quartiers-familles/exports/geojson?select=grd_quart,lib_grd_quart,geo_shape"),
             "lib_grd_quart", "grand quartier"),
    # Neither the city nor the Métropole (data.ampmetropole.fr has Marseille's quartiers only) publishes Aix's: its 54
    # IRIS grouped into 39 (iris_source; Encagnane 1 to 4, Jas 1 to 6, Couronne Est, Nord, Sud are one each).
    area_row("Aix-en-Provence", "Aix-en-Provence", iris_source("13001"), "quartier", "IRIS", dissolve=True),
    # Neither the city nor the CAPA publishes quartiers, and OpenStreetMap maps none: its 24 IRIS (iris_source).
    area_row("Ajaccio", "Ajaccio", iris_source("2A004"), "quartier", "IRIS", dissolve=True),
    # Amiens Métropole's "quartiers de la ville d'Amiens" WFS (geodata.amiens-metropole.com) was not reached (2026-10-11): its 58 IRIS
    # grouped into 23 (iris_source; Étouvie's 4, Saint-Honoré's 5), "Amiens-Val d'Avre 1" to "3" named Val d'Avre.
    area_row("Amiens", "Amiens", iris_source("80021"), lambda p: "Val d'Avre" if p["quartier"] == "Amiens" else p["quartier"], "IRIS", dissolve=True),
    # Without the filter the API also answers every past version of each wijk.
    area_row("Amsterdam", "Amsterdam", geojson_source("https://api.data.amsterdam.nl/v1/gebieden/wijken/?_format=geojson&_pageSize=2000&eindGeldigheid[isnull]=true"),
             "naam", "wijk"),
    # The Ville d'Angers's 10 quartiers, those of its conseils de quartier (Belle-Beille, Lac de Maine, Doutre - Saint-Jacques - Nazareth).
    area_row("Angers", "Angers", geojson_source("https://data.angers.fr/api/explore/v2.1/catalog/datasets/ccq_007/exports/geojson?select=nom_cq,geo_shape"),
             "nom_cq", "quartier"),
    # The commune nouvelle (Annecy-le-Vieux, Cran-Gevrier, Meythet, Pringy, Seynod since 2017) publishes no quartiers:
    # its 51 IRIS grouped into 47 (iris_source; the three Novel are one, the three Meythet one).
    area_row("Annecy", "Annecy", iris_source("74010"), "quartier", "IRIS", dissolve=True),
    area_row("Antwerp", "Antwerp", geojson_source("https://geodata.antwerpen.be/arcgissql/rest/services/P_Portal/portal_publiek2/MapServer/97/query"
             "?where=1%3D1&outFields=*&outSR=4326&f=geojson"),
             lambda p: re.sub(r"^Antwerpen - ", "", (p.get("wijknaam") or "").strip()), "wijk", keep=lambda p: (p.get("wijknaam") or "").strip() != "Schelde"),
    # Parts of one neighbourhood ("ΠΑΓΚΡΑΤΙ Ι", "ΠΑΓΚΡΑΤΙ ΙΙ") merged; friendly_name is filled for 25 of 144, some wrongly.
    area_row("Athens", "Athens", ckan_wkt_source("https://opendata.cityofathens.gr/api/3/action/datastore_search?resource_id=0283af02-7783-4688-897b-7153da7ed2fd&limit=500",
             "polygon_geo"), lambda p: roman_part(p.get("name") or ""), "neighbourhood", keep=lambda p: str(p.get("status")) == "1", dissolve=True),
    area_row("Auckland", "Auckland", wfs_source(LINZ_WFS, "layer-113764", "LINZ_API_KEY"), "name", "suburb", key="LINZ_API_KEY"),
    # The Ville d'Avignon's 9 mairies de quartier, on DataSud; named as it names them ("Avignon Est", "Avignon Montfavet").
    area_row("Avignon", "Avignon", geojson_source("https://www.datasud.fr/geoserver/avignon/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=avignon:avignon_mairie_quartier&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"), "libmairie", "mairie de quartier"),
    area_row("Barcelona", "OSM", osm_area_source((10,)), "name", None),   # the portal is behind a bot check; OSM's barris are the city's own (2018)
    area_row("Basel", "Basel", geojson_source("https://data.bs.ch/api/explore/v2.1/catalog/datasets/100042/exports/geojson"), "wov_name", "Wohnviertel"),
    # Bayonne's 26 quartiers officiels (Petit Bayonne, Saint-Esprit, Marracq); its 4 conseils de quartier are too coarse.
    area_row("Bayonne", "Bayonne", geojson_source("https://www.bayonne.fr/fileadmin/open-data/geojson/za_quartier.geojson"), "nom", "quartier"),
    area_row("Belgrade", "OSM", osm_area_source((8,)), "name", None),
    area_row("Berlin", "Berlin", geojson_source("https://gdi.berlin.de/services/wfs/alkis_ortsteile?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=alkis_ortsteile:ortsteile&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"), "nam", "Ortsteil"),
    # The Ville de Besançon's 13 quartiers (Battant, Chaprais - Cras, Planoise), on Grand Besançon Métropole's map server; no licence
    # stated. Its open data portal lists the streets by quartier only.
    area_row("Besançon", "Besançon", geojson_source("https://sig-portail.grandbesancon.fr/server/rest/services/Visionneuse/Limites_administratives/MapServer/8"
             "/query?where=1%3D1&outFields=NOM&outSR=4326&f=geojson"), "NOM", "quartier"),
    # Neither the city nor the Pays Basque agglomeration publishes quartiers: its 12 IRIS (iris_source), none merged.
    area_row("Biarritz", "Biarritz", iris_source("64122"), "quartier", "IRIS", dissolve=True),
    # GeoBilbao's barrios (Deustu's, Abando's; the open data catalogue has them as a CSV without outlines); each distrito's
    # rural rest, "DISEMINADO", left out: 40. Without srsName it answers in UTM.
    area_row("Bilbao", "Bilbao", geojson_source("https://api.bilbao.eus/maps?gsservice=ls&gsrequest=wfs&service=WFS&version=1.0.0&request=GetFeature"
             "&idservice=poblacion_territorio&typeName=poblacion_territorio:Barrios&outputFormat=application/json&srsName=EPSG:4326&maxFeatures=100000"),
             "Nombre", "barrio", keep=lambda p: (p.get("Nombre") or "").strip() != "DISEMINADO"),
    area_row("Bogotá", "Bogotá", geojson_source("https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/ordenamientoterritorial/unidadplaneamientozonal"
             "/MapServer/0/query?where=1%3D1&outFields=CODIGO_UPZ,NOMBRE&outSR=4326&f=geojson"), "NOMBRE", "UPZ"),
    # The Comune's 90 aree statistiche (Bolognina's Arcoveggio, Cirenaica, Irnerio), "GALVANI-1" and "GALVANI-2" one.
    area_row("Bologna", "Bologna", geojson_source("https://opendata.comune.bologna.it/api/explore/v2.1/catalog/datasets/aree-statistiche/exports/geojson"),
             lambda p: re.sub(r"-\d$", "", (p.get("area_statistica") or "").strip()), "area statistica", dissolve=True),
    area_row("Bordeaux", "Bordeaux", geojson_source("https://opendata.bordeaux-metropole.fr/api/explore/v2.1/catalog/datasets/se_quartvie_s/exports/geojson"
             "?select=libelle,geo_shape"), "libelle", "secteur de vie"),
    # The Planning Department's 26 neighborhoods ("not official neighborhood boundaries", it says): the ones people name.
    # Its data.boston.gov file redirects to S3 with ":443" in the URL, which urllib signs into the Host: 403.
    area_row("Boston", "Boston", geojson_source("https://gis.bostonplans.org/hosting/rest/services/Hosted/Boston_Neighborhood_Boundaries"
             "/FeatureServer/5/query?where=1%3D1&outFields=name&outSR=4326&f=geojson"), "name", "neighborhood"),
    area_row("Bratislava", "Bratislava", geojson_source("https://data.bratislava.sk/api/download/v1/items/f1daafc79a9247e8b93241aa005fbea3/geojson?layers=0"),
             "NAZOV_UTJ", "mestská časť"),
    # Brest métropole's 7 quartiers administratifs of Brest (its mairies de quartier: Bellevue, Europe, Saint-Marc); its TRIRIS are
    # unnamed. outFields=* answers nothing.
    area_row("Brest", "Brest", geojson_source("https://geo.brest-metropole.fr/arcgis/rest/services/public/GPB_LIM/MapServer/1610008/query"
             "?where=1%3D1&outFields=QUARTIERS&outSR=4326&f=geojson"), "QUARTIERS", "quartier"),
    area_row("Brisbane", "Brisbane", geojson_source("https://data.brisbane.qld.gov.au/api/explore/v2.1/catalog/datasets/suburb-boundaries/exports/geojson"),
             "suburb_name", "suburb"),
    area_row("Brussels", "Brussels", geojson_source("https://opendata.bruxelles.be/api/explore/v2.1/catalog/datasets/"
             "quartiers-du-monitoring-des-quartiers-ibsa-perspective-rbc/exports/geojson"), "namefre", "quartier"),
    area_row("Budapest", "OSM", osm_area_source((10,)), "name", None),
    area_row("Buenos Aires", "Buenos Aires", geojson_source("https://cdn.buenosaires.gob.ar/datosabiertos/datasets/innovacion-transformacion-digital/barrios/barrios.geojson"),
             "nombre", "barrio"),
    # Neither the city nor Caen la mer publishes quartiers: its 51 IRIS grouped into 45 (iris_source; IGN's "La Grace de Dieu Ouest"
    # joins La Grâce de Dieu).
    area_row("Caen", "Caen", iris_source("14118"), lambda p: p["quartier"].replace("La Grace de Dieu Ouest", "La Grâce de Dieu"), "IRIS", dissolve=True),
    # The Mairie's 12 quartiers ("quartiers politiques", not the QPV); its 63 secteurs de proximité are often a street's name.
    area_row("Cannes", "Cannes", geojson_source("https://data.cannes.fr/api/explore/v2.1/catalog/datasets/quartiers-politiques/exports/geojson"
             "?select=nom_quartier,geo_shape"), "nom_quartier", "quartier"),
    area_row("Cape Town", "Cape Town", geojson_source("https://gis.westerncape.gov.za/server2/rest/services/SpatialDataWarehouse/CoCT_Management_Boundaries"
             "/MapServer/15/query?where=1%3D1&outFields=SBRB_NAME&outSR=4326&f=geojson"), "SBRB_NAME", "major suburb"),
    area_row("Chicago", "Chicago", geojson_source("https://data.cityofchicago.org/resource/y6yq-dbs2.geojson?$limit=1000"), "pri_neigh", "neighborhood"),
    # Neither the city nor Clermont Auvergne Métropole publishes quartiers (its "Quartiers 2021" are 5 territoires): its 42 IRIS
    # (iris_source), none merged.
    area_row("Clermont-Ferrand", "Clermont-Ferrand", iris_source("63113"), "quartier", "IRIS", dissolve=True),
    area_row("Copenhagen", "Copenhagen", geojson_source("https://wfs-kbhkort.kk.dk/k101/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=k101:kvarter"
             "&outputFormat=json&SRSNAME=EPSG:4326"), "kvarternavn", "kvarter"),
    area_row("Delhi", "OSM", osm_area_source((10,)), "name", None),
    # Dijon's 9 quartiers of its ateliers de quartier; the portal left Opendatasoft (2025), its old export is a 404.
    area_row("Dijon", "Dijon", geojson_source("https://data.metropole-dijon.fr/sites/default/files/dataset/2025/06/20/8e39eea1-603a-4ad5-a937-8cc592b840a7"
             "/query.geojson"), "NOM", "quartier"),
    # The 41 Dhaka Metropolitan thanas (Gulshan, Dhanmondi, Motijheel) of BBS and OCHA ROAP's 2015 admin 3, as
    # geoBoundaries keeps it (125 MB, pinned): OCHA's current COD-AB stops at the two City Corporations, OSM maps no
    # thanas and names in Bengali. The box's edges take in 7 upazilas around (Savar, Keraniganj).
    area_row("Dhaka", "Dhaka", geojson_source("https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/BGD/ADM3"
             "/geoBoundaries-BGD-ADM3.geojson"), "shapeName", "thana"),
    # Electoral divisions: "Beaumont A" to "Beaumont F" are Beaumont.
    area_row("Dublin", "Dublin", geojson_source("https://data.smartdublin.ie/dataset/2afc02f8-314b-40eb-91f0-2955f84bb912/resource/a19c6a3e-bbc8-4f20-b178-65de8e6950f1"
             "/download/electoral-divisions_cso2.shp.geojson"), lambda p: re.sub(r" [A-F]$", "", (p.get("Pllng_D") or "").strip()), "electoral division", dissolve=True),
    area_row("Edinburgh", "Edinburgh", geojson_source("https://edinburghcouncilmaps.info/arcgis/rest/services/Misc/INSPIRE/MapServer/27/query"
             "?where=1%3D1&outFields=NATURALCOM&outSR=4326&f=geojson"), "NATURALCOM", "natural neighbourhood"),
    # ISTAT's sub-municipal areas (Rifredi, Campo di Marte); the 1995 rioni are older and misspelt. In EPSG:3003.
    area_row("Florence", "Florence", geojson_source("https://datigis.comune.fi.it/json/aree_elementari_2021.json"), "denae2021", "area elementare"),
    # The 46 Stadtteile (Bahnhofsviertel, Sachsenhausen-Nord, Bornheim); the server refuses "application/json".
    area_row("Frankfurt", "Frankfurt", geojson_source("https://geowebdienste.frankfurt.de/WFS_Stadtgebietsgliederung?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=Stadtgebietsgliederung:Stadtteile&SRSNAME=EPSG:4326&OUTPUTFORMAT=GEOJSON"), "STT_NAME", "Stadtteil"),
    area_row("Geneva", "Geneva", geojson_source("https://vector.sitg.ge.ch/arcgis/rest/services/Hosted/OCS_SECTEURS_STATISTIQUES/MapServer/0/query"
             "?where=no_com_federal%3D6621&outFields=nom_secteur&outSR=4326&f=geojson"), "nom_secteur", "secteur statistique"),
    area_row("Ghent", "Ghent", geojson_source("https://data.stad.gent/api/explore/v2.1/catalog/datasets/stadswijken-gent/exports/geojson"), "wijk", "stadswijk"),
    # Two "Shared …" rows are areas two councils share, over others.
    area_row("Glasgow", "Glasgow", geojson_source("https://www.mapping.glasgow.gov.uk/arcgis_web/rest/services/OPEN_DATA/Community_Councils/MapServer/0/query"
             "?where=1%3D1&outFields=AREANAME&outSR=4326&f=geojson"), "AREANAME", "community council area",
             keep=lambda p: not (p.get("AREANAME") or "").strip().startswith("Shared")),
    area_row("Gothenburg", "Gothenburg", geojson_source("https://geodata.scb.se/geoserver/stat/wfs?service=WFS&request=GetFeature&version=2.0.0&typeNames=stat:RegSO_2025"
             "&outputFormat=application/json&srsName=EPSG:4326&CQL_FILTER=" + urllib.parse.quote("kommunkod='1480'")), "regsonamn", "RegSO"),
    # The Ville de Grenoble's 22 unions de quartier (its 6 secteurs are too coarse), from the dataset's CSV.
    area_row("Grenoble", "Grenoble", csv_geojson_source("https://data.metropolegrenoble.fr/sites/default/files/dataset/2023/02/22/0dcfa54a-6dad-4a31-af19-d2baaef4320e"
             "/csv_gen_0dcfa54a_6dad_4a31_af19_d2baaef4320e_63f63698555a9.csv"), "sdec_libel", "union de quartier"),
    area_row("Hamburg", "Hamburg", ogc_source("https://api.hamburg.de/datasets/v1/verwaltungsgrenzen/collections/stadtteile/items?f=json&limit=1000"),
             "stadtteil_name", "Stadtteil"),
    area_row("Helsinki", "Helsinki", geojson_source("https://kartta.hel.fi/ws/geoserver/avoindata/wfs?service=WFS&version=2.0.0&request=GetFeature"
             "&typeNames=avoindata:Piirijako_osaalue&outputFormat=application/json&srsName=EPSG:4326"), "nimi_fi", "osa-alue"),
    area_row("Istanbul", "OSM", osm_area_source((8,)), "name", None),   # İBB publishes the mahalle as points only
    area_row("Jerusalem", "OSM", osm_area_source(("neighbourhood", "quarter", "suburb")), "name", None),   # CBS's statistical areas carry no names
    area_row("Johannesburg", "Johannesburg", arcgis_source("https://services3.arcgis.com/GMycIhSIBQnnjV35/arcgis/rest/services/Census_2011_Sub_Places_of_South_Africa/FeatureServer/0",
             ["SP_NAME"], "MN_NAME='City of Johannesburg'"), "SP_NAME", "sub place"),
    area_row("Kraków", "OSM", osm_area_source((11,)), "name", None),
    area_row("Kuala Lumpur", "OSM", osm_area_source(("neighbourhood", "quarter", "suburb")), "name", None),
    area_row("Kyiv", "OSM", osm_area_source((7, 10)), "name", None),
    # The Ville de La Rochelle's 19 comités de quartier (its 2012 cut; Port-Neuf, Les Minimes), on the Agglomération's portal;
    # its 4 conseils de secteur are too coarse.
    area_row("La Rochelle", "La Rochelle", geojson_source("https://opendata.agglo-larochelle.fr/d4c/api/records/2.0/downloadfile/"
             "format=geojson&resource_id=b55cda71-088c-4ea4-8df8-69fc80c7b9c8"), "cq_nom", "comité de quartier"),
    # The Ayuntamiento's barrios (Vegueta, Triana, Guanarteme); each distrito's rural rest, "DISEMINADO", left out: 112.
    area_row("Las Palmas", "Las Palmas", geojson_source("https://sit.laspalmasgc.es/server/rest/services/opendata/barrios/MapServer/0/query"
             "?where=1%3D1&outFields=NUCL_DS_NOMBRE&outSR=4326&f=geojson"), "NUCL_DS_NOMBRE", "barrio",
             keep=lambda p: (p.get("NUCL_DS_NOMBRE") or "").strip() != "DISEMINADO"),
    # The Ville du Havre's 20 conseils de quartier (Sanvic, Caucriauville, Danton); the port, "Hors conseil de quartier", is left out.
    # Every format of the portal is a zip; the shapefile's is a zip in it.
    area_row("Le Havre", "Le Havre", lambda rect: _shapefile_features(_zip_members(_zip_members(get(
             "https://data.lehavreseinemetropole.fr/api/v1/file/data/90/CONSEIL_QUARTIER/shp"))["OD.CONSEIL_QUARTIER.shp.zip"])),
             "NOM", "conseil de quartier", keep=lambda p: p.get("NUM") != 0),
    # Le Mans Métropole publishes 8 conseils de quartier ("Nord Est", "Sud Ouest") and its IRIS in capitals with codes: IGN's 71 IRIS,
    # grouped into 70 (iris_source).
    area_row("Le Mans", "Le Mans", iris_source("72181"), "quartier", "IRIS", dissolve=True),
    area_row("Lille", "Lille", geojson_source("https://data.lillemetropole.fr/geoserver/ville_lille/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=ville_lille:limite_des_quartiers_de_lille_et_de_ses_communes_associees&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"),
             "quartier", "quartier"),   # Lille's 10 quartiers, and Lomme and Hellemmes whole
    # The Ville de Limoges's 12 quartiers (Carnot, Louyat - Vigenal, La Bastide - La Brégère; IRIS aggregated for its housing
    # policy), on Limoges Métropole's map server; no licence stated. Its 57 secteurs CIL take in other communes.
    area_row("Limoges", "Limoges", geojson_source("https://sig.limoges-metropole.fr/servernf1/rest/services/_TER/ter_secteurs_habitat_consult/MapServer/4"
             "/query?where=1%3D1&outFields=quart_vdl&outSR=4326&f=geojson"), "quart_vdl", "quartier"),
    area_row("Lisbon", "Lisbon", geojson_source("https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services/Limite_Cartografia/FeatureServer/0/query"
             "?outFields=NOME&where=1%3D1&outSR=4326&f=geojson"), "NOME", "freguesia"),
    # The council's 64 wards of 2023 (Anfield, Toxteth, Penny Lane), the same as ONS's December 2025 wards.
    area_row("Liverpool", "Liverpool", wfs_source("https://geoserver.liverpool.gov.uk/geoserver/OpenData/ows", "OpenData:Wards"), "wardname", "ward"),
    area_row("London", "London", arcgis_source("https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Wards_December_2024_Boundaries_UK_BGC/FeatureServer/0",
             ["WD24NM"], "LAD24CD LIKE 'E09%'", "FID"), "WD24NM", "ward"),
    # Lorient Agglomération's 12 quartiers of Lorient (Kerentrech, Moustoir, Keryado); no licence stated. The Ville's own 5 (2020)
    # group several.
    area_row("Lorient", "Lorient", geojson_source("https://carto.lorient-agglo.bzh/server/services/Service_WMS_ISOGEO/MapServer/WFSServer?SERVICE=WFS&VERSION=2.0.0"
             "&REQUEST=GetFeature&TYPENAMES=Service_WFS_ISOGEO:Quartiers&OUTPUTFORMAT=GEOJSON&SRSNAME=EPSG:4326"), "nom", "quartier",
             keep=lambda p: p.get("insee") == "56121"),
    area_row("Los Angeles", "Los Angeles", geojson_source("https://services5.arcgis.com/7nsPwEMP38bSkCjy/arcgis/rest/services/LA_Times_Neighborhoods/FeatureServer/0/query"
             "?where=1%3D1&outFields=name&outSR=4326&f=geojson"), "name", "neighborhood"),
    area_row("Madrid", "Madrid", shapefile_source("https://geoportal.madrid.es/fsdescargas/IDEAM_WBGEOPORTAL/LIMITES_ADMINISTRATIVOS/Barrios/Barrios.zip"),
             "NOMBRE", "barrio"),
    # The Ayuntamiento's 419 barrios (El Perchel, La Trinidad, El Palo); its 11 distritos are too coarse.
    area_row("Málaga", "Málaga", geojson_source("https://datosabiertos.malaga.eu/recursos/urbanismoEInfraestructura/planimetria/callejero"
             "/da_cartografiaBarrio-4326.geojson"), "NOMBARRIO", "barrio"),
    # The City of Manila's 16 districts, then the barangays of the cities around it (Manila's own are numbered).
    area_row("Manila", "OSM", osm_area_source((8, 10)), "name", None),
    area_row("Melbourne", "Melbourne", arcgis_source(ABS_SAL, ["sal_name_2021"], "state_code_2021='2'", "objectid"),
             lambda p: re.sub(r" \(Vic\.\)$", "", p.get("sal_name_2021") or ""), "suburb"),
    # The Eurométropole de Metz's 14 quartiers of Metz (Ancienne Ville, Sablon, Borny); the same layer's 5 secteurs of
    # Montigny-lès-Metz left out.
    area_row("Metz", "Metz", geojson_source("https://maps.eurometropolemetz.eu/wfs?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES=public:fdp_adm_quar"
             "&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"), "lib", "quartier", keep=lambda p: p.get("_commid") == "57463"),
    # Two neighborhoods mapped in two parts each (Baypoint, Fair Isle) merged; one feature has no label.
    area_row("Miami", "Miami", geojson_source("https://services1.arcgis.com/CvuPhqcTQpZPT9qY/arcgis/rest/services/Miami_Neighborhoods_Shapefile"
             "/FeatureServer/0/query?where=1%3D1&outFields=LABEL&outSR=4326&f=geojson"), "LABEL", "neighborhood", dissolve=True),
    area_row("Milan", "Milan", geojson_source("https://dati.comune.milano.it/dataset/e8e765fc-d882-40b8-95d8-16ff3d39eb7c/resource/9c4e0776-56fc-4f3d-8a90-f4992a3be426"
             "/download/ds964_nil_wm.geojson"), "NIL", "NIL"),
    area_row("Montpellier", "Montpellier", geojson_source("https://data.montpellier3m.fr/sites/default/files/ressources/VilleMTP_MTP_SousQuartiers.json"),
             "name", "sous-quartier"),
    area_row("Montréal", "Montréal", geojson_source("https://donnees.montreal.ca/dataset/f38c91a1-e33f-4475-a112-3b84b1c60c1e/resource/a80e611f-5336-4306-ba2a-fd657f0f00fa"
             "/download/quartierreferencehabitation.geojson"), "nom_qr", "quartier de référence"),
    area_row("Munich", "Munich", geojson_source("https://geoportal.muenchen.de/geoserver/gsm_wfs/ows?service=WFS&version=1.0.0&request=GetFeature"
             "&typeName=gsm_wfs:vablock_stadtbezirk&outputFormat=application/json&srsName=EPSG:4326"), "sb_name", "Stadtbezirk", dissolve=True),
    # Nancy's 11 quartiers (its ateliers de vie: Plateau de Haye, Rives de Meurthe), on the Métropole du Grand Nancy's map server;
    # no licence stated. Its 5 territoires d'élus are too coarse.
    area_row("Nancy", "Nancy", geojson_source("https://geo.grandnancy.eu/server/rest/services/ADMINISTRATIF_Limites/MapServer/4/query"
             "?where=CCOCOM%3D%27395%27&outFields=NOM&outSR=4326&f=geojson"), "NOM", "quartier"),
    area_row("Nantes", "Nantes", geojson_source("https://data.nantesmetropole.fr/api/explore/v2.1/catalog/datasets/244400404_quartiers-de-la-ville-de-nantes"
             "/exports/geojson?select=nom,geo_shape"), "nom", "quartier"),
    # The 30 quartieri (Chiaia, Vomero, Posillipo) on the 2001 census sections, the newest the Comune publishes as a file.
    # Its server answers Actions' runners 403 (run 38180000936), so the file is read from a copy in this repository
    # (`areas/naples.zip`, unchanged, IODL 2.0), taken on 2026-10-12 from trasparenza.comune.napoli.it's
    # ServeAttachment.php/L/IT/D/8%252Fa%252Fe%252FD.8fbafa14e575ac594a1c/P/BLOB%3AID%3D26532/E/zip.
    area_row("Naples", "Naples", shapefile_source("https://raw.githubusercontent.com/Techneb/compromise-tiles/master/areas/naples.zip"),
             "QUART", "quartiere"),
    # ntatype 0: the residential NTAs; 5 to 9 are parks, cemeteries, airports.
    area_row("New York", "New York", geojson_source("https://data.cityofnewyork.us/resource/9nt8-h7nd.geojson?$limit=1000"), "ntaname", "NTA",
             keep=lambda p: str(p.get("ntatype")) == "0"),
    # The city publishes 6 territoires only; its 146 IRIS grouped into 131 quartiers (iris_source).
    area_row("Nice", "Nice", iris_source("06088"), "quartier", "IRIS", dissolve=True),
    # Nîmes's 56 comités de quartier (Tour Magne, Écusson's Feuchères, Pissevin), on Nîmes Métropole's ArcGIS Online, unaccented;
    # its 6 "non attribué" areas left out, its 7 conseils de quartier too coarse. One name is in mixed case, hence upper().
    area_row("Nîmes", "Nîmes", geojson_source("https://services2.arcgis.com/ghqRCL5wdHSQIHfv/arcgis/rest/services/Comit%C3%A9s_de_Quartiers/FeatureServer/1/query"
             "?where=1%3D1&outFields=COMITE_QUARTIER&outSR=4326&f=geojson"), lambda p: (p.get("COMITE_QUARTIER") or "").upper(), "comité de quartier",
             keep=lambda p: not (p.get("COMITE_QUARTIER") or "").startswith("NON ATTRIBUE")),
    # Orléans Métropole publishes the city's 6 quartiers (its mairies de proximité) only: its 45 IRIS grouped into 36 (iris_source;
    # Bourgogne's 5, Bannier's 3).
    area_row("Orléans", "Orléans", iris_source("45234"), "quartier", "IRIS", dissolve=True),
    area_row("Osaka", "Osaka", estat_source("27", [w for w in range(102, 129) if w not in (105, 110, 112)]), machi, "machi",
             dissolve=lambda p: (p["CITY"], machi(p))),
    area_row("Oslo", "Oslo", oslo_source, "delbydel", "delbydel", dissolve=True),
    # The Ottawa Neighbourhood Study's Gen 3 (116, the city's open data), in capitals; Gatineau, across the river, has none.
    area_row("Ottawa", "Ottawa", geojson_source("https://services.arcgis.com/G6F8XLCl5KtAlZ2G/arcgis/rest/services/GEN3_OTT_1_3_3/FeatureServer/0/query"
             "?where=1%3D1&outFields=ONS_Name&outSR=4326&f=geojson"), "ONS_Name", "neighbourhood"),
    # The 25 quartieri (2013; Politeama, Zisa, Tribunali-Castellammare), in Gauss-Boaga fuso Est with no .prj; "LIBERTA'"
    # is Libertà, "SFRRACAVALLO" Sferracavallo.
    area_row("Palermo", "Palermo", shapefile_source("https://opendata.comune.palermo.it/js/server/uploads/dataset/shp/_10012018113135.zip", crs="EPSG:3004"),
             lambda p: re.sub(r"A'(?=\s|-|$)", "À", (p.get("DENOMINAZI") or "").strip()).replace("SFRRACAVALLO", "SFERRACAVALLO"), "quartiere"),
    area_row("Perth", "Perth", arcgis_source(ABS_SAL, ["sal_name_2021"], "state_code_2021='5'", "objectid"),
             lambda p: re.sub(r" \(WA\)$", "", p.get("sal_name_2021") or ""), "suburb"),
    area_row("Poznań", "Poznań", geojson_source("https://www.poznan.pl/mim/plan/map_service.html?mtype=local_gov&co=osiedla"), "name", "osiedle"),
    area_row("Prague", "Prague", geojson_source("https://lkod-iprpraha.hub.arcgis.com/api/download/v1/items/35ede955057f4815b522ea91b488792a/geojson?layers=0"),
             "nazev_1", "městská část"),
    # Neither the city nor the Grand Reims publishes quartiers: its 85 IRIS grouped into 50 (iris_source; Murigny's 5, Laon's 4).
    area_row("Reims", "Reims", iris_source("51454"), "quartier", "IRIS", dissolve=True),
    # Rennes's 45 sous-quartiers (Villejean, Bréquigny, Thabor - Paris), under its 12 quartiers' compound names.
    area_row("Rennes", "Rennes", geojson_source("https://data.rennesmetropole.fr/api/explore/v2.1/catalog/datasets/perimetres-des-45-sous-quartiers-de-la-ville-de-rennes"
             "/exports/geojson?select=nomsquart,geo_shape"), "nomsquart", "sous-quartier"),
    # The 58 apkaimes (Āgenskalns, Grīziņkalns), in LKS-92 with an ISO-8859-13 .cpg.
    area_row("Riga", "Riga", shapefile_source("https://data.gov.lv/dati/dataset/ddc24ef1-4db4-46ce-a035-258b6ed69d91/resource/5409870d-287f-49e0-b336-d3c4ce936f69"
             "/download/apkaimes.zip"), "apkaime", "apkaime"),
    area_row("Rome", "Rome", geojson_source("https://geoportale.comune.roma.it/geoserver/ows?service=WFS&version=2.0.0&request=GetFeature"
             "&typeNames=DIPSER:SuddivisioniToponomastica&outputFormat=application/json&srsName=EPSG:4326"),
             "DESCRIZIONE", lambda p: (p.get("TIPOLOGIA") or "").lower() or "suddivisione"),
    area_row("Rotterdam", "Rotterdam", ogc_source("https://api.pdok.nl/cbs/wijken-en-buurten-2025/ogc/v1/collections/buurten/items?f=json&limit=1000&gemeentecode=GM0599"),
             "buurtnaam", "buurt", keep=lambda p: p.get("buurtnaam") != "Buitenwater"),
    # Neither the city nor the Métropole Rouen Normandie publishes quartiers: its 42 IRIS grouped into 40 (iris_source).
    area_row("Rouen", "Rouen", iris_source("76540"), "quartier", "IRIS", dissolve=True),
    # The Ville de Saint-Étienne's 47 quartiers (Badouillère, Crêt de Roc, Fauriel), its SIG's WFS, unaccented; its 21 conseils
    # de quartier bundle them.
    area_row("Saint-Étienne", "Saint-Étienne", geojson_source("https://extranet.saint-etienne.fr/extrasig/geoserver/wfs?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=vse:GENSECT1&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"), "nom", "quartier"),
    # SF Find's 117 neighborhoods (Hayes Valley, Noe Valley), not the 41 Analysis Neighborhoods; data.sfgov.org moved here.
    area_row("San Francisco", "San Francisco", geojson_source("https://data.sf.gov/resource/gfpk-269f.geojson?$limit=1000"), "name", "neighborhood"),
    # San José, Costa Rica: its barrios (admin_level 10); the Municipalidad's own layer is "para uso exclusivo de la
    # Municipalidad de San José", and IGN's finest are the distritos.
    area_row("San José", "OSM", osm_area_source((10,)), "name", None),
    # The City Clerk's Neighborhood Map Atlas, its 94 neighborhoods (Ballard, Belltown, Capitol Hill's Broadway):
    # "not intended to represent any 'official' City of Seattle neighborhood map", the ones people name.
    # The 19 auzoak (Gros, Egia, Antigua); Erdialdea's second part is Santa Clara island.
    area_row("San Sebastián", "San Sebastián", geojson_source("https://www.donostia.eus/geozerbitzuak/rest/services/ext/LIMITES_ADMINISTRATIVOS/MapServer/2/query"
             "?where=1%3D1&outFields=IzenAuzo&outSR=4326&f=geojson"), "IzenAuzo", "auzoa", dissolve=True),
    area_row("Seattle", "Seattle", geojson_source("https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/nma_nhoods_sub/FeatureServer/0/query"
             "?where=1%3D1&outFields=S_HOOD&outSR=4326&f=geojson"), "S_HOOD", "neighborhood"),
    area_row("Seoul", "Seoul", posted_shapefile_source("https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false",
             {"infId": "OA-22160", "seq": "1", "infSeq": "3"}), "ADSTRD_NM", "행정동"),
    # IDE.Sevilla's barrios (Triana's, Nervión's); "VILLEGAS" is mapped in two parts.
    area_row("Seville", "Seville", geojson_source("https://services1.arcgis.com/hcmP7kr0Cx3AcTJk/arcgis/rest/services/Barrios_de_Sevilla/FeatureServer/0/query"
             "?where=1%3D1&outFields=Barrio&outSR=4326&f=geojson"), "Barrio", "barrio", dissolve=True),
    area_row("Singapore", "Singapore", datagovsg_source("d_4765db0e87b9c86336792efe8a1f7a66"), "PLN_AREA_N", "planning area"),
    # The ж.к. (housing estates) and кв. (quarters); villages, villa zones and industrial areas left out.
    area_row("Sofia", "Sofia", geojson_source("https://api.sofiaplan.bg/datasets/297"),
             lambda p: re.sub(r"^(ЖК|КВ)\.\s*", "", (p.get("kvname") or "").strip()), lambda p: (p.get("prefname") or "").lower(),
             keep=lambda p: p.get("prefname") in ("ЖК.", "КВ.")),
    area_row("Stockholm", "Stockholm", geojson_source("https://geodata.scb.se/geoserver/stat/wfs?service=WFS&request=GetFeature&version=2.0.0&typeNames=stat:RegSO_2025"
             "&outputFormat=application/json&srsName=EPSG:4326&CQL_FILTER=" + urllib.parse.quote("kommunkod='0180'")), "regsonamn", "RegSO"),
    # The finest of the city's cuts (10, 14, 23, 28 quartiers), and the one with whole names.
    area_row("Strasbourg", "Strasbourg", geojson_source("https://data.strasbourg.eu/api/explore/v2.1/catalog/datasets/strasbourg-en-28-quartiers/exports/geojson"
             "?select=nom,geo_shape"), "nom", "quartier"),
    area_row("Sydney", "Sydney", arcgis_source(ABS_SAL, ["sal_name_2021"], "state_code_2021='1'", "objectid"),
             lambda p: re.sub(r" \(NSW\)$", "", p.get("sal_name_2021") or ""), "suburb"),
    area_row("São Paulo", "São Paulo", geojson_source("https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/wfs?service=WFS&version=2.0.0&request=GetFeature"
             "&typeNames=geoportal:distrito_municipal&outputFormat=application/json&srsName=EPSG:4326"), "nm_distrito_municipal", "distrito"),
    area_row("Tel Aviv", "Tel Aviv", geojson_source("https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2/MapServer/511/query"
             "?where=1%3D1&outFields=shem_shchuna&outSR=4326&f=geojson"), lambda p: logical_hebrew((p.get("shem_shchuna") or "").strip()), "shchuna"),
    area_row("The Hague", "The Hague", geojson_source("https://den-haag-opendata.opendatasoft.com/api/explore/v2.1/catalog/datasets/wijken/exports/geojson"),
             "wijknaam", "wijk"),
    area_row("Thessaloníki", "Thessaloníki", geojson_source("https://maps.thessaloniki.gr/server/rest/services/DataMapImage/MapServer/111/query"
             "?where=1%3D1&outFields=name&outSR=4326&f=geojson"), "name", "δημοτική κοινότητα"),
    # Chōme merged into their machi, ward by ward.
    area_row("Tokyo", "Tokyo", estat_source("13", range(101, 124)), machi, "machi", dissolve=lambda p: (p["CITY"], machi(p))),
    area_row("Toronto", "Toronto", geojson_source("https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/fc443770-ef0a-4025-9c2c-2cb558bfab00"
             "/resource/0719053b-28b7-48ea-b863-068823a93aaa/download/neighbourhoods-4326.geojson"), "AREA_NAME", "neighbourhood"),
    # The Mairie's 38 comités d'intérêt local (Mourillon, Le Jonquet, Saint-Jean du Var), on the Métropole TPM's map server; no
    # licence stated. The Faron, the Arsenal and the port lie in none; the layer's CIL of Le Pradet left out.
    area_row("Toulon", "Toulon", geojson_source("https://portailsig.metropoletpm.fr/ags2/rest/services/Metiers/MET_ADMINISTRATION/MapServer/6/query"
             "?where=commune%3D%27TOULON%27&outFields=nom&outSR=4326&f=geojson"), "nom", "comité d'intérêt local"),
    # The Ville de Tours's 22 quartiers (Sanitas-Rotonde, Deux Lions Gloriette; its IRIS aggregated); its 8 conseils de quartier are coarser.
    area_row("Tours", "Tours", geojson_source("https://data.tours-metropole.fr/api/explore/v2.1/catalog/datasets/quartiers-ville-de-tours"
             "/exports/geojson?select=quartier,geo_shape"), "quartier", "quartier"),
    area_row("Turin", "Turin", shapefile_source("http://geoportale.comune.torino.it/geodati/zip/zone_statistiche_geo.zip"), "DENOM", "zona statistica"),  # http only
    # The gemeente's 111 buurten (Lombok-oost, Wittevrouwen, Oog in Al), as the city spells them; its 10 wijken are coarse.
    area_row("Utrecht", "Utrecht", geojson_source("https://geodata.utrecht.nl/geoserver/UtrechtOpen/wfs?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=UtrechtOpen:GIS_BUURT&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"), "NAAM_BUURT", "buurt"),
    area_row("Valencia", "Valencia", geojson_source("https://geoportal.valencia.es/server/rest/services/OPENDATA/UrbanismoEInfraestructuras/MapServer/224/query"
             "?where=1%3D1&outFields=nombre&outSR=4326&f=geojson"), "nombre", "barri"),
    area_row("Vancouver", "Vancouver", geojson_source("https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/local-area-boundary/exports/geojson"),
             "name", "local area"),
    # Versailles Grand Parc's 8 quartiers municipaux of Versailles (the urbanisme department's, 2004).
    area_row("Versailles", "Versailles", geojson_source("https://services2.arcgis.com/YECJCCLQCtaylXWh/arcgis/rest/services/VersaillesQuartiersMunicipaux"
             "/FeatureServer/0/query?where=1%3D1&outFields=NOM&outSR=4326&f=geojson"), "NOM", "quartier"),
    area_row("Vienna", "Vienna", geojson_source("https://data.wien.gv.at/daten/geo?service=WFS&request=GetFeature&version=1.1.0&typeName=ogdwien:BEZIRKSGRENZEOGD"
             "&srsName=EPSG:4326&outputFormat=json"), "NAMEK", "Bezirk"),
    # The Métropole de Lyon's quartiers of its communes (201, no commune field): Villeurbanne's 8, those of its conseils de
    # quartier; La Doua's campus lies in none. The Ville publishes none of its own.
    area_row("Villeurbanne", "Villeurbanne", geojson_source("https://data.grandlyon.com/geoserver/metropole-de-lyon/ows?SERVICE=WFS&VERSION=2.0.0&request=GetFeature"
             "&typename=metropole-de-lyon:adr_voie_lieu.adrquartier&outputFormat=application/json&SRSNAME=EPSG:4326"),
             lambda p: (p.get("nom") or "").removeprefix("Quartier "), "quartier",
             keep=lambda p: (p.get("nom") or "").removeprefix("Quartier ") in {"Buers Croix-Luizet", "Charpennes Tonkin", "Cusset Bonnevay",
             "Cyprian Les Brosses", "Ferrandière Maisons Neuves", "Gratte-Ciel Dedieu Charmettes", "Perralière Grandclément", "Saint-Jean"}),
    area_row("Vilnius", "Vilnius", geojson_source("https://zemelapiai.vplanas.lt/arcgis/rest/services/Open_Data/Vilniaus_miesto_ribos/MapServer/1/query"
             "?where=1%3D1&outFields=SENIUNIJA&outSR=4326&f=geojson"), "SENIUNIJA", "seniūnija"),
    area_row("Warsaw", "OSM", osm_area_source((10,)), "name", None),   # the city's WFS was not reached (2026-10-10)
    # Each cluster named by its neighborhoods ("Kalorama Heights, Adams Morgan, Lanier Heights"; NAME is "Cluster 1");
    # the 7 "Additional" clusters are parks, the Mall, bases and campuses. Finer names are points only.
    area_row("Washington DC", "Washington DC", geojson_source("https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Administrative_Other_Boundaries_WebMercator"
             "/MapServer/17/query?where=1%3D1&outFields=NAME,NBH_NAMES,TYPE&outSR=4326&f=geojson"), "NBH_NAMES", "neighborhood cluster",
             keep=lambda p: p.get("TYPE") == "Original"),
    # The 48 osiedla of the city's 2016 resolution (Stare Miasto, Nadodrze, Ołbin), in Poland CS2000 zone 6.
    area_row("Wrocław", "Wrocław", shapefile_source("https://geoportal.wroclaw.pl/www/pliki/osiedla/granice-osiedli.zip"), "NAZWAOSIED", "osiedle"),
    area_row("Zagreb", "Zagreb", shapefile_source("https://data.zagreb.hr/dataset/37fa6630-0a87-4084-b62d-ff5edab3610b/resource/482b7289-d397-4c38-9929-5d0410ad0e16"
             "/download/rpj_mo.zip", encoding="cp1250"), "JMS_IME", "mjesni odbor"),
    area_row("Zurich", "Zurich", geojson_source("https://www.ogd.stadt-zuerich.ch/wfs/geoportal/Statistische_Quartiere?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature"
             "&TYPENAME=adm_statistische_quartiere_map&OUTPUTFORMAT=GeoJSON"), "qname", "statistisches Quartier"),
]

TITLE_SMALL = {"de", "des", "du", "la", "le", "les", "del", "della", "delle", "degli", "dei", "di", "da", "do", "dos", "das", "sul", "y", "et",
               "e", "i", "and", "of", "the",
               "van", "der", "den", "het", "von", "am", "im"}

def titled(name):
    """A name written in capitals only ("LA CREU COBERTA", "ΚΟΛΩΝΑΚΙ") in title case, its articles and particles after
    the first word in lower case and a Roman numeral kept ("Avenue Léopold III"); any other name as it is."""
    if not any(ch.isalpha() for ch in name) or name != name.upper(): return name
    def word(m):
        w = m[0]
        if re.fullmatch(r"[IVX]+", w) and m.start() > 0: return w
        if w.lower() in TITLE_SMALL and m.start() > 0 and name[m.start() - 1] == " ": return w.lower()
        return w[0] + w[1:].lower()
    return re.sub(r"[^\W\d_]+", word, name)

def area_features(row, rect):
    """[{"name", "level", "source", "geometry"}] of one row: kept, named, dissolved, made valid, polygons only."""
    import shapely
    out, groups = [], {}
    for p, g in row["fetch"](rect):
        if row["keep"] and not row["keep"](p): continue
        name = row["name"](p) if callable(row["name"]) else p.get(row["name"])
        name = " ".join(str(name or "").split())
        if len(name) > 2 and name[0] == name[-1] == '"': name = name[1:-1].strip()   # Zagreb's units named after people
        if not any(ch.isalnum() for ch in name): continue   # e-Stat's "‐", an unnamed area
        level = row["level"](p) if callable(row["level"]) else row["level"] if row["level"] is not None else p.get("level")
        g = shapely.make_valid(g)
        g = shapely.union_all([part for part in shapely.get_parts(g) if part.geom_type in ("Polygon", "MultiPolygon")] or [shapely.Polygon()])
        if g.is_empty: continue
        group = None if not row["dissolve"] else name if row["dissolve"] is True else row["dissolve"](p)
        if group is not None and group in groups:
            groups[group]["geometry"] = shapely.union(groups[group]["geometry"], g); continue
        out.append({"name": name, "level": level, "source": row["source"], "geometry": g})
        if group is not None: groups[group] = out[-1]
    return out

def simplified_coverage(geoms, tolerance=BOUNDARY_TOLERANCE):
    """The geometries (lon/lat) simplified together by `tolerance` metres, in a local equirectangular frame:
    an edge two of them share is simplified once, so it stays shared. Then rounded on the 1e-5° grid,
    vertex by vertex, so the shared vertices stay equal."""
    import shapely, numpy
    if not geoms: return []
    lat0 = numpy.mean([g.centroid.y for g in geoms])
    k = math.cos(math.radians(lat0))
    there = shapely.transform(numpy.array(geoms, dtype=object), lambda xy: xy * [M * k, M])
    back = shapely.transform(shapely.coverage_simplify(there, tolerance), lambda xy: xy / [M * k, M])
    return list(shapely.set_precision(back, 1 / BOUNDARY_UNIT, mode="pointwise"))

def area_rings(geometry, cell_key):
    """boundaries/'s "polygons" for a geometry: per polygon its outer ring (counter-clockwise) then its holes
    (clockwise), each open, in BOUNDARY_UNIT steps, the first vertex from the key's corner (key / 25), each next
    from the one before. A ring of under 3 distinct vertices is left out, and a polygon whose outer ring is."""
    import shapely
    ky, kx = map(int, cell_key.split(","))
    per = BOUNDARY_UNIT // MAIN
    out = []
    for polygon in shapely.get_parts(shapely.orient_polygons(geometry)):
        rings = []
        for ring in [polygon.exterior, *polygon.interiors]:
            steps = []
            for lon, lat in ring.coords:
                q = (round(lat * BOUNDARY_UNIT), round(lon * BOUNDARY_UNIT))
                if not steps or q != steps[-1]: steps.append(q)
            if len(steps) > 1 and steps[0] == steps[-1]: steps.pop()
            if len(steps) < 3:
                if not rings: break
                continue
            flat, (y, x) = [steps[0][0] - ky * per, steps[0][1] - kx * per], steps[0]
            for qy, qx in steps[1:]:
                flat += [qy - y, qx - x]
                y, x = qy, qx
            rings.append(flat)
        if rings: out.append(rings)
    return out

def label_point(feature, places):
    """(name, lat, lon): the places/ place inside the polygon under the same place_key() (its name and point),
    else the polygon's name, in title case if written in capitals, at a point inside it."""
    import shapely
    key = place_key(feature["name"])
    for p in places:
        lat, lon = p["coordinate"]["latitude"], p["coordinate"]["longitude"]
        if place_key(p["name"]) == key and feature["geometry"].contains(shapely.Point(lon, lat)): return p["name"], lat, lon
    at = feature["geometry"].representative_point()
    return titled(feature["name"]), at.y, at.x

def area_tiles(features, places):
    """{MAIN key: [item, …] sorted by name}: each feature whole in its label point's cell."""
    tiles = {}
    for f in features:
        name, lat, lon = label_point(f, places)
        key = f"{index(lat, MAIN)},{index(lon, MAIN)}"
        polygons = area_rings(f["geometry"], key)
        if not polygons: continue
        tiles.setdefault(key, []).append({"name": name, "level": f["level"], "source": f["source"], "polygons": polygons})
    for items in tiles.values(): items.sort(key=lambda i: (i["name"], i["polygons"][0][0][:2]))
    return tiles

def area_region(entries, half_km=0.5):
    """The city's tiled area: each entry's box, cut to its boundary where it has one, together."""
    import shapely, shapely.geometry
    parts = []
    for e in entries:
        s, w, n, e_ = area(e, half_km)
        part = shapely.box(w, s, e_, n)
        if e.get("_boundary"):   # a ring of under 4 positions (Marseille's file holds one) is left out
            g = e["_boundary"]
            polygons = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            shape = shapely.MultiPolygon([shapely.Polygon(p[0], [r for r in p[1:] if len(r) >= 4]) for p in polygons if p and len(p[0]) >= 4])
            part = part.intersection(shapely.make_valid(shape))
        parts.append(part)
    return shapely.union_all(parts)

_areas = {}   # city → (features, tiles) or SourceError: built once a run, at the city's first entry

def city_areas(city, entries):
    """The city's boundaries/ features (simplified) and tiles, from every row of the city: a row's features meeting
    the tiled area, simplified as one set. A row whose key is not set gives nothing (said once); a row that
    fails fails the city (its published tiles stay)."""
    import shapely
    if city in _areas: return _areas[city]
    features = []
    try:
        region = area_region(entries)
        s, w, n, e = region.bounds[1], region.bounds[0], region.bounds[3], region.bounds[2]
        for row in (r for r in AREA_SOURCES if r["city"] == city):
            if row["key"] and not os.environ.get(row["key"], "").strip():
                log(f"  {city} boundaries: skipped, {row['key']} is not set"); continue
            found = [f for f in area_features(row, (s, w, n, e))
                     if f["geometry"].intersects(region) and shapely.area(shapely.intersection(f["geometry"], region)) > 0]
            for f, g in zip(found, simplified_coverage([f["geometry"] for f in found])): f["geometry"] = g
            features += [f for f in found if not f["geometry"].is_empty]
            log(f"  {city} boundaries: {len(found)} from {row['source']}")
        _areas[city] = features, area_tiles(features, extract._osm.places() if extract._osm else [])
    except SourceError as error:
        _areas[city] = error
    except Exception as error:  # noqa: BLE001 — a malformed answer (bad zip, missing field) fails the city, not the run
        _areas[city] = SourceError(f"{type(error).__name__}: {error}"[:300])
    return _areas[city]

def do_boundaries(city, entries, cells, out, failures, published=None):
    """The city's boundaries/ tiles (written at its first entry; an earlier run's tile in a cell of the city's area
    that holds none now is removed), and in sources/ each of this entry's ~200 m cells whose centre lies in a
    polygon names its source ("boundaries")."""
    first = city not in _areas
    answer = city_areas(city, entries)
    wide = {c.key for e in entries for c in cells_in(*area(e, 0.5), scale=MAIN)}
    if isinstance(answer, SourceError):
        if first: failures += [(k, "boundaries", str(answer)) for k in sorted(wide)]
        return
    features, tiles = answer
    if first:
        for key, items in tiles.items(): write(os.path.join(out, "boundaries", key + ".json"), items)
        for key in wide - set(tiles):
            if os.path.exists(os.path.join(out, "boundaries", key + ".json")): os.remove(os.path.join(out, "boundaries", key + ".json"))
    if not features or not cells: return
    import shapely
    tree = shapely.STRtree([f["geometry"] for f in features])
    at, hit = tree.query(shapely.points([(c.lon, c.lat) for c in cells]), predicate="intersects")
    source = {}
    for i, j in zip(at, hit): source.setdefault(cells[i].key, features[j]["source"])
    noted = [c for c in cells if c.key in source]
    published = published if published is not None else published_sources(out, noted)
    for c in noted: note_sources(out, c, published, boundaries=source[c.key])

UNSHADED = None  # --unshaded: the file the weekly run lists the cells no venue or terrace reaches and no small place holds in

def build_order(cells, block, layers):
    """The blocks, those with a tile missing from the store first, each part in key order."""
    return sorted(blocks(cells, block), key=lambda g: all(in_store(c, layers) for c in g))

_cell_by_cell = set()  # sources whose blocks failed where single cells answered: blocks too big for a proxy

def gather(cells, fetch, pad, source):
    """{key: items or SourceError}: the block asked once, and each cell alone if the block failed.
    A source whose block failed while its cells answered is asked cell by cell for the rest of the
    run (Overpass from a cloud proxy, 2026-09-24: 3×3 blocks reset every time, single cells went through).
    Not when the block got an HTTP status: the server answered, so only that block goes cell by cell
    (one IGN 400 in Paris, 2026-09-28, sent 4,700 cells one at a time and ran past the job's 6 hours)."""
    if len(cells) > 1 and source not in _cell_by_cell:
        try:
            items = unique(fetch(padded(union(cells), pad)))
            return {c.key: items for c in cells}
        except SourceError as e:
            log(f"    {source}: block failed ({e}); asking cell by cell")
            block_failed = "HTTP " not in str(e)
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

# A buildings tile is written only for a cell within SHADE_MARGIN of a venue (a bar, pub, beer garden, café or restaurant
# of the extract, named or not) or of a terrace (terraces-v2/) (owner, 2026-10-13; BUILDINGS.md). Each tile already holds
# every footprint within BUILDING_REACH of its cell, so a kept cell needs no neighbour.
SHADE_MARGIN = 100

def shade_keys(points, margin=SHADE_MARGIN):
    """The keys of the ~200 m cells whose rect comes within `margin` metres of a (lat, lon) of `points`."""
    keys = set()
    for lat, lon in points:
        k = M * math.cos(math.radians(lat))
        for c in cells_in(*padded((lat, lon, lat, lon), margin)):
            if math.hypot(max(c.s - lat, 0, lat - c.n) * M, max(c.w - lon, 0, lon - c.e) * k) <= margin: keys.add(c.key)
    return keys

def shade_points(cells, out):
    """(lat, lon) of the extract's venues around the cells and of the terraces of their terraces-v2/ tiles (this run's,
    else the published one: each holds the terraces within TERRACE_RADIUS, so together they hold every terrace near
    the cells). None when a published tile could not be read: the caller then keeps every cell."""
    points = {(v["coordinate"]["latitude"], v["coordinate"]["longitude"])
              for v in extract._osm.near(extract._osm.venues, padded(union(cells), SHADE_MARGIN))}
    todo = [c for c in cells if read(path(out, "terraces-v2", c)) is None]
    with concurrent.futures.ThreadPoolExecutor(STORE_THREADS) as pool:
        published = dict(zip((c.key for c in todo), pool.map(lambda c: published_items("terraces-v2", c.key), todo)))
    for c in cells:
        items = published[c.key] if c.key in published else read(path(out, "terraces-v2", c))
        if isinstance(items, SourceError):
            log(f"    {c.key}: terraces-v2/ not read ({items}); every cell keeps its buildings"); return None
        points.update((t["coordinate"]["latitude"], t["coordinate"]["longitude"]) for t in items)
    return points

# A small place keeps every buildings tile (owner, 2026-10-15): a commune (OSM admin_level 8) holding fewer than SMALL_PLACE_VENUES
# of the extract's venues (the same as SHADE_MARGIN's), so a venue OSM lacks there (Périgny, 2026-10-14) still has its shade.
SMALL_PLACE_LEVEL, SMALL_PLACE_VENUES = 8, 5

def small_place_keys(cells, rect):
    """The keys of the cells whose centre lies in a small commune meeting the rect, None when the communes could not be read."""
    by_key, held = {c.key: c for c in cells}, []
    try:
        for b in extract._osm.areas(rect)[0]:
            if b["admin_level"] != SMALL_PLACE_LEVEL or not (g := b["geometry"]): continue
            keys = {c.key for k in cells_in(*bounds(g)) if (c := by_key.get(k.key)) and contains(g, c.lat, c.lon)}
            if keys: held.append((g, keys))
        # A commune reaching past the area's box (Saint-Germain-en-Laye past the petite couronne's) has venues the box left
        # out: those communes' venues are read again from the whole extract, at once.
        s, w, n, e = extract._osm.box
        far = [g for g, _ in held if not (s <= (x := bounds(g))[0] and w <= x[1] and x[2] <= n and x[3] <= e)]
        far_venues = {}
        if far:
            boxes = [bounds(g) for g in far]
            for v in extract._osm.venues_in((min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes))):
                lat, lon = v["coordinate"]["latitude"], v["coordinate"]["longitude"]
                far_venues.setdefault(buckets(lat, lon, lat, lon)[0], []).append(v)
    except SourceError as e:
        log(f"    communes not read ({e}); every cell keeps its buildings"); return None
    small = set()
    for g, keys in held:
        table = far_venues if any(g is f for f in far) else extract._osm.venues
        inside = (v for v in extract._osm.near(table, bounds(g)) if contains(g, v["coordinate"]["latitude"], v["coordinate"]["longitude"]))
        if sum(1 for _ in zip(range(SMALL_PLACE_VENUES), inside)) < SMALL_PLACE_VENUES: small |= keys
    return small

def shaded_keys(cells, out, rect):
    """The cells a buildings tile is written for: near a venue or terrace (shade_keys), or in a small place. None: every cell."""
    points = shade_points(cells, out)
    small = small_place_keys(cells, rect) if points is not None else None
    return None if small is None else shade_keys(points) | small

def do_buildings(cells, out, failures, published=None, tiles=True):
    """The building tiles (with tiles=False, only each cell's source in sources/), by source."""
    published = published if published is not None else published_sources(out, cells)
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
            name = source
            if source in COMBINE:  # "<city>+OSM" where the city gave a height, else OSM's alone
                name = f"{source}+OSM" if any(b.get("city") for b in items) else "OSM"
                items = [{k: b[k] for k in ("outline", "height", "guessed") if k in b} for b in items]
            items = estimated(items, answer)
            if tiles: write(path(out, "buildings", c), items)
            if tiles: write(path(out, "buildings-v2", c), compact_buildings(items, c.key))
            note_sources(out, c, published, buildings=name)

def permit_city(cell, communes):
    """The permit feed for a cell: by INSEE code when a commune answered, else by box (and boundary, when the row has one)."""
    if communes: return next((c for c in PERMIT_CITIES if c["insee"] == communes[0]["code"]), None)
    feed = next((c for c in PERMIT_CITIES if in_feed(c, cell.lat, cell.lon)), None)
    if feed and keyed_host(feed) is None:  # a keyed row without its key (a local run, a fork): OSM only, said once
        if feed["city"] not in _keyless:
            _keyless.add(feed["city"])
            log(f"  {feed['city']} permits: skipped, {feed['key']} is not set (OSM terraces only)")
        return None
    return feed

_keyless = set()

def do_terraces(cells, communes, out, failures):
    osm = gather(cells, osm_terraces, TERRACE_RADIUS, "OSM terraces")
    feeds, feed_of = {}, {}
    for c in cells:
        feed = feed_of[c.key] = permit_city(c, communes.get(c.key))
        if feed: feeds.setdefault(id(feed), (feed, []))[1].append(c)
    found, published = {}, published_sources(out, cells)
    for feed, group in feeds.values():
        found.update(gather(group, lambda r, feed=feed: permits(feed, r), TERRACE_RADIUS, feed["city"] + " permits"))
    for c in cells:
        answers = [osm[c.key]] + ([found[c.key]] if c.key in found else [])
        errors = [a for a in answers if isinstance(a, SourceError)]
        if errors:  # half an answer is not a tile
            failures.append((c.key, "terraces-v2", "; ".join(map(str, errors)))); continue
        write(path(out, "terraces-v2", c), near_terraces(c, [t for a in answers[1:] for t in a] + answers[0]))
        note_sources(out, c, published, permits=feed_of[c.key] and feed_of[c.key]["city"])

def do_streets(cells, out, failures):
    """The street tiles of a block: OpenStreetMap's ways and house numbers asked once, cut cell by cell."""
    try: streets, (points, interpolations) = osm_streets(union(cells)), osm_addresses(union(cells))
    except SourceError as e:
        failures += [(c.key, "streets", str(e)) for c in cells]; return
    tiles = street_tiles(cells, streets)
    numbers = number_rows(tiles, points, interpolations)
    for c in cells:
        write(path(out, "streets", c), tiles[c.key])
        if numbers[c.key]: write(path(out, "numbers", c), numbers[c.key])
        elif os.path.exists(path(out, "numbers", c)): os.remove(path(out, "numbers", c))   # an earlier run's, now stale

def do_main_streets(cells, out, failures):
    """The streets-main/ tiles, one ~4 km cell at a time."""
    for c in cells:
        try: lines = street_tiles([c], osm_main_streets(c.rect), MAIN, MAIN_UNIT, named=False)[c.key]
        except SourceError as e:
            failures.append((c.key, "streets-main", str(e))); continue
        write(path(out, "streets-main", c), lines)

def do_places(cells, out, failures):
    try:
        for c in cells: write(path(out, "places", c), osm_places(c))
    except SourceError as e: failures += [(c.key, "places", str(e)) for c in cells]

def do_transit(cells, out, failures):
    """The transit/ tiles of the area's ~4 km cells; a cell no line stops in has no file (an earlier run's is removed)."""
    try: tiles = transit_tiles(cells, extract._osm.transit())
    except SourceError as e:
        failures += [(c.key, "transit", str(e)) for c in cells]; return
    for c in cells:
        if c.key in tiles: write(path(out, "transit", c), tiles[c.key])
        elif os.path.exists(path(out, "transit", c)): os.remove(path(out, "transit", c))

def do_sources(cells, out, failures):
    """The `sources` layer: the published sources/ tiles that lack a building source get it, found as
    the buildings layer finds it (the city feed, IGN, then OSM), its tiles not written. A cell without
    a published tile was never built and is left alone; one whose tile already names its source too."""
    if not store.STORE:
        failures += [(c.key, "sources", "no --store to read the published tiles from") for c in cells]; return
    published = published_sources(out, cells)
    for c in cells:
        if isinstance(published.get(c.key), SourceError): failures.append((c.key, "sources", str(published[c.key])))
    need = [c for c in cells if isinstance(published.get(c.key), dict) and published[c.key] and "buildings" not in published[c.key]]
    if need: do_buildings(need, out, failures, published, tiles=False)

def run(label, rect, out, block, layers, extracts, departements=None, boundary=None, city=None, city_entries=()):
    started = time.monotonic()
    cells, coarse, wide = cells_in(*rect), cells_in(*rect, scale=COARSE), cells_in(*rect, scale=MAIN)
    if boundary:  # a box around a municipality keeps only the cells inside its boundary, and the venue cells over them
        cells = clip(cells, boundary)
        kept = {f"{index(c.lat, COARSE)},{index(c.lon, COARSE)}" for c in cells}
        coarse = [c for c in coarse if c.key in kept]
        kept = {f"{index(c.lat, MAIN)},{index(c.lon, MAIN)}" for c in cells}
        wide = [c for c in wide if c.key in kept]
    failures, skipped, outside = [], 0, set()
    log(f"{label}: {len(cells)} cells, {len(coarse)} venue cells, blocks of {block}×{block}")
    lat, lon = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    try:  # the box every OSM question can reach: venue cells whole, buildings and terraces past the edge
        pbf = geofabrik_pbf(lat, lon)
        reach = union(coarse), padded(rect, 600)
        extract._osm = OSM(download(pbf, extracts), (min(r[0] for r in reach), min(r[1] for r in reach),
                                             max(r[2] for r in reach), max(r[3] for r in reach)))
    except SourceError as e:
        log(f"{label}: OpenStreetMap unavailable ({e})")
        return len(cells), [(c.key, "all", str(e)) for c in cells]
    if city in OVERTURE_CITIES and "venues" in layers:
        try:
            counts = overture_facts(extract._osm, overture_places(extract._osm.box))
            log(f"{label}: Overture " + ", ".join(f"{k} {sum(c.values()) if k == 'dropped' else dict(c)}" for k, c in sorted(counts.items())))
        except Exception as e:   # the venues still go up, without the kinds, and the log says why
            log(f"{label}: Overture unavailable ({str(e)[:200]})")
    # A French extract means French communes; elsewhere the geo API would answer [] a cell at a time.
    french = "/europe/france" in pbf
    sources_only = set(layers) <= {"sources", "venues", "streets-main", "places", "boundaries", "transit"}
    shaded = None   # the cells a buildings tile is written for (shade_keys); None: every cell
    if "buildings" in layers and (shaded := shaded_keys(cells, out, rect)) is not None:
        log(f"{label}: buildings for {sum(c.key in shaded for c in cells)} of {len(cells)} cells, those near a venue or terrace or in a small place")
    if "venues" in layers:
        for c in coarse:
            if not done(out, "venues", c): write(path(out, "venues", c), osm_venues(c))
    if "streets-main" in layers: do_main_streets([c for c in wide if not done(out, "streets-main", c)], out, failures)
    if "places" in layers: do_places([c for c in wide if not done(out, "places", c)], out, failures)
    if "transit" in layers: do_transit(wide, out, failures)
    for group in build_order(cells, block, layers):
        communes, need_terraces, need_buildings, need_sources, need_streets = {}, [], [], [], []
        for c in group:
            if "sources" in layers: need_sources.append(c)
            if sources_only: continue  # no commune needed: a cell outside the départements has no published tile
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
            if "buildings" in layers and (shaded is None or c.key in shaded) \
                    and not (done(out, "buildings", c) and done(out, "buildings-v2", c)):
                need_buildings.append(c)
            if "streets" in layers and not done(out, "streets", c): need_streets.append(c)
            if "terraces-v2" in layers and not done(out, "terraces-v2", c):
                if french and communes.get(c.key) is None:
                    failures.append((c.key, "terraces-v2", "commune unknown, so the permit feed is too")); continue
                need_terraces.append(c)
        skipped += sum(1 for c in group if c not in need_buildings and c not in need_terraces and c not in need_sources
                       and c not in need_streets and c.key not in outside)
        if need_buildings: do_buildings(need_buildings, out, failures)
        if need_streets: do_streets(need_streets, out, failures)
        if need_terraces: do_terraces(need_terraces, communes, out, failures)
        if need_sources: do_sources(need_sources, out, failures)
    # The weekly run: a venue or terrace new since the yearly buildings run gets its cells' buildings now. The store's
    # listing (with buildings-v2/, tiles.yml) says which cells lack them; without one nothing is added.
    # ponytail: a new area's first weekly run builds all its cells' buildings here, inside the same 290 minutes.
    if "terraces-v2" in layers and "buildings" not in layers and store.LISTED is not None:
        mine = [c for c in cells if c.key not in outside]
        keys = shaded_keys(mine, out, rect) if mine else None
        if keys is not None:
            top = [c for c in mine if c.key in keys and f"tiles/buildings-v2/{c.key}.json" not in store.LISTED
                   and not done(out, "buildings-v2", c)]
            log(f"{label}: buildings for {len(top)} cells a venue or terrace newly reaches or a small place holds")
            for group in blocks(top, block): do_buildings(group, out, failures)
            if UNSHADED:  # the cells no venue or terrace reaches: what tiles.yml's prune_buildings deletes from the store
                with open(UNSHADED, "a") as f: f.writelines(c.key + "\n" for c in mine if c.key not in keys)
    # After the blocks, so a cell's sources/ tile written by this run's buildings or terraces is the one noted.
    if "boundaries" in layers: do_boundaries(city or label, city_entries or [{"box": rect, "_boundary": boundary}], [c for c in cells if c.key not in outside], out, failures)
    failed = {k for k, _, _ in failures}
    log(f"{label}: {len(cells) - len(outside) - len(failed)} of {len(cells) - len(outside)} cells complete ({skipped} already there), "
        f"{len(failed)} failed, {time.monotonic() - started:.0f} s")
    return len(cells) - len(outside), failures

def entry_layers(entry, layers):
    """The layers built for one area: streets/, streets-main/, places/ and boundaries/ only where its entry says
    "streets": true (owner, 2026-10-05: a few cities a country, chosen by hand), every other layer as asked."""
    return [l for l in layers if l not in STREET_LAYERS or entry.get("streets") is True]

def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("--cities", help="a cities.json: [{city, district?, lat, lon, half_km}] or [{city, district?, box: [s, w, n, e], departements?, boundary?, streets?}]"
                   " (boundary: a GeoJSON Polygon or MultiPolygon file, relative to the cities.json, whose cells alone are built;"
                   " streets: true for an area whose streets/ tiles are built, none otherwise)")
    a.add_argument("--city", help="a label: feeds are picked per cell, by commune or box"); a.add_argument("--lat", type=float); a.add_argument("--lon", type=float)
    a.add_argument("--half-km", type=float, default=0.5)
    a.add_argument("--block", type=int, default=3, help="cells per side asked of a source at once (default 3)")
    a.add_argument("--out", default="tiles")
    a.add_argument("--layers", default=",".join(LAYERS), help="which to write (default all): " + ", ".join(LAYERS)
                   + "; or sources, the building source of the published sources/ tiles that lack it (needs --store)")
    a.add_argument("--store", help="the published tiles' base URL (https://tiles.alephb.uk/tiles/): sources/ tiles keep its fields")
    a.add_argument("--listing", help="the store's keys (changed.py's remote.tsv): blocks with a tile missing there are built first")
    a.add_argument("--unshaded", help="a terraces-v2 run with --listing appends here the keys of the cells no venue or terrace reaches")
    a.add_argument("--extracts", default="extracts", help="where Geofabrik extracts are kept between runs (default ./extracts)")
    args = a.parse_args()
    unknown = [l for l in args.layers.split(",") if l not in LAYERS + ("sources",)]   # numbers/ is written beside streets/
    if unknown: a.error(f"unknown layer {', '.join(map(repr, unknown))}: give {', '.join(LAYERS)} or sources")
    global UNSHADED
    store.STORE = args.store and args.store.rstrip("/") + "/"
    UNSHADED = args.unshaded
    if args.listing and os.path.exists(args.listing):
        with open(args.listing) as f: store.LISTED = {line.split("\t")[0] for line in f}
    if args.cities:
        with open(args.cities) as f: entries = json.load(f)
    elif args.city and args.lat is not None and args.lon is not None:
        entries = [{"city": args.city, "lat": args.lat, "lon": args.lon, "half_km": args.half_km}]
    else:
        a.error("give --cities, or --city with --lat and --lon")
    total, failures = 0, []
    for entry in entries:
        entry["_boundary"] = None
        if entry.get("boundary"):
            with open(os.path.join(os.path.dirname(os.path.abspath(args.cities)), entry["boundary"])) as f: boundary = json.load(f)
            entry["_boundary"] = boundary.get("geometry", boundary)
        entry.setdefault("half_km", args.half_km)
    for entry in entries:
        label = entry["city"] + (f" ({entry['district']})" if entry.get("district") else "")
        layers = entry_layers(entry, args.layers.split(","))
        if not layers:
            log(f"{label}: no streets/ for this area (not flagged), nothing else asked"); continue
        # boundaries/ is the city's, built from all its flagged entries' areas at the first.
        city_entries = [e for e in entries if e["city"] == entry["city"] and "boundaries" in entry_layers(e, ["boundaries"])]
        n, f = run(label, area(entry, args.half_km), args.out, args.block, layers, args.extracts,
                   entry.get("departements"), entry["_boundary"], entry["city"], city_entries)
        total += n
        failures += [(label,) + x for x in f]
    failed = {(x[0], x[1]) for x in failures}
    if failures:
        log(f"\nFailures ({len(failed)} cells of {total}):")
        for label, key, layer, reason in failures: log(f"  {label} {key} {layer}: {reason}")
    if short(len(failed), total):
        log(f"More than {SHORT:.0%} of cells failed ({len(failed)} of {total})."); sys.exit(1)

SHORT = 0.05   # the share of failed cells that fails the run: the tiles still go up, the job turns red (tiles.yml)

def short(failed, total):
    """Too many cells failed for the run to pass (20 % until 2026-10-07, when Gijón's 1,536 of 1,536 and Genoa's
    2,472 of 3,204 failed and stayed green: the workflow's `|| true` dropped the exit code; it now turns the job red)."""
    return bool(total) and failed / total > SHORT

if __name__ == "__main__":
    main()
