"""python3 test_make_tiles.py — stdlib asserts, no network."""
from make_tiles import smallest_region, cells_in, clip, rooftop, seating
from make_tiles import smallest_region, cells_in, clip, riga_venue_name, toronto_name, permit_items

def square(s, w, n, e): return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
def region(id, parent, geometry): return {"properties": {"id": id, "parent": parent, "urls": {"pbf": id}}, "geometry": geometry}

# Two regions at the same depth hold the point: the smaller box wins, whatever the list order.
index = [region("world", None, square(-90, -180, 90, 180)),
         region("big", "world", square(0, 0, 10, 10)),
         region("small", "world", square(0, 0, 2, 2))]
assert smallest_region(index, 1, 1)["properties"]["id"] == "small"
assert smallest_region(index[::-1], 1, 1)["properties"]["id"] == "small"
# Depth still comes first: a deeper region beats a smaller shallower one.
index.append(region("deep", "big", square(0.5, 0.5, 5, 5)))
assert smallest_region(index, 1, 1)["properties"]["id"] == "deep"

# The boundary clip keeps the cells whose centre is inside, and a hole's cells are out.
box = (0, 0, 0.02, 0.02)
holed = {"type": "Polygon", "coordinates": [square(0, 0, 0.01, 0.02)["coordinates"][0],
                                             square(0, 0, 0.004, 0.004)["coordinates"][0]]}
all_cells = cells_in(*box)
kept = clip(all_cells, holed)
assert 0 < len(kept) < len(all_cells)
assert all(c.lat < 0.01 for c in kept) and not any(c.lat < 0.004 and c.lon < 0.004 for c in kept)

# Rooftop tags: the three that mean a roof, and the two that do not.
assert rooftop({"location": "roof"}) and rooftop({"roof_terrace": "yes"}) and rooftop({"terrace": "roof"})
assert rooftop({"terrace": "garden;roof"})
assert not rooftop({"rooftop": "yes"}) and not rooftop({"terrace": "roofed"}) and not rooftop({"roof_terrace": "no"})
assert not rooftop({})
assert seating(None, True) == "roof" and seating("yes", True) == "roof" and seating("no", True) == "roof"
assert seating("roof;terrace", True) == "roof;terrace" and seating("terrace", True) == "roof"
assert seating("yes") is True and seating("no") is False and seating(None) is False and seating("patio") == "patio"
# Riga's holder loses its legal form, either side; CaféTO's placeholders name nothing.
assert riga_venue_name("Muca Bistro Bar SIA") == "Muca Bistro Bar" and riga_venue_name("SIA Piga Avotu") == "Piga Avotu"
assert riga_venue_name("Kalve Coffee AS") == "Kalve Coffee" and riga_venue_name("Vira V SIA ") == "Vira V"
assert toronto_name("None") is None and toronto_name("PUBLIC PARKLET") is None and toronto_name("BARBURRITO") == "BARBURRITO"
# A one-point MultiPoint (CaféTO) lands on its point.
toronto = {"name": "OPERATOR_NAME", "kinds": [], "clean": "toronto"}
point = {"geometry": {"type": "MultiPoint", "coordinates": [[-79.41, 43.69]]}, "properties": {"OPERATOR_NAME": "None"}}
assert permit_items([point], toronto) == [{"kind": "TERRASSE", "coordinate": {"latitude": 43.69, "longitude": -79.41}}]

# San Sebastián's terraces (Donostia's MapServer, FID from 0): the paging must not skip FID 0.
import make_tiles, urllib.parse
from make_tiles import gipuzkoa_buildings, permits, PERMIT_CITIES
asked = []
def fake_get(url, data=None, parse=None, **k):
    asked.append(urllib.parse.unquote(url))
    return {"features": [{"geometry": {"type": "Point", "coordinates": [-1.958171550804368, 43.312719267068616]},
                          "properties": {"FID": 0, "IzenTe": "ADI TABERNA"}}]} if len(asked) == 1 else {"features": []}
make_tiles.get, real_get = fake_get, make_tiles.get
donostia = next(c for c in PERMIT_CITIES if c["city"] == "San Sebastián")
assert permits(donostia, (43.31, -1.96, 43.32, -1.95)) == [
    {"kind": "TERRASSE", "coordinate": {"latitude": 43.312719267068616, "longitude": -1.958171550804368}, "name": "ADI TABERNA"}]
assert "FID>-1" in asked[0] and "outFields=FID,IzenTe" in asked[0]
make_tiles.get = real_get
# Gipuzkoa's INSPIRE GML (the feed's shape, trimmed): heightAboveGround, else floors × 3 m when it reads 0, else 15 m.
def member(height, floors, ring):
    return f"""<wfs:member><bu-ext2d:Building xmlns:bu-ext2d="http://inspire.ec.europa.eu/schemas/bu-ext2d/4.0">
<bu-base:heightAboveGround xmlns:bu-base="http://inspire.ec.europa.eu/schemas/bu-base/4.0"><bu-base:HeightAboveGround>
<bu-base:value uom="m">{height}</bu-base:value></bu-base:HeightAboveGround></bu-base:heightAboveGround>
<bu-base:numberOfFloorsAboveGround xmlns:bu-base="http://inspire.ec.europa.eu/schemas/bu-base/4.0">{floors}</bu-base:numberOfFloorsAboveGround>
<bu-core2d:geometry2D xmlns:bu-core2d="http://inspire.ec.europa.eu/schemas/bu-core2d/4.0"><gml:Polygon><gml:exterior><gml:LinearRing>
<gml:posList>{ring}</gml:posList></gml:LinearRing></gml:exterior><gml:interior><gml:LinearRing><gml:posList>0 0 0 1 1 1 0 0</gml:posList>
</gml:LinearRing></gml:interior></gml:Polygon></bu-core2d:geometry2D></bu-ext2d:Building></wfs:member>"""
ring = "43.323921 -1.986071 43.323889 -1.986158 43.323834 -1.986123 43.323921 -1.986071"
gml = ('<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2">'
       + member(20, 5, ring) + member(0, 4, ring) + member(0, "", ring) + "</wfs:FeatureCollection>").encode()
built = gipuzkoa_buildings(gml)
assert [b["height"] for b in built] == [20.0, 12.0, 15.0]
assert [b.get("guessed") for b in built] == [None, None, True]   # only the 15 m guess carries the flag
assert built[0]["outline"][0] == {"latitude": 43.323921, "longitude": -1.986071} and len(built[0]["outline"]) == 4
# Johannesburg's ELEVATION is already metres above ground (Carlton Centre 204.1); San Sebastián's cells are Gipuzkoa's, not Catastro's.
from make_tiles import building_source, CITY_BUILDINGS
joburg = next(b for b in CITY_BUILDINGS if b["city"] == "Johannesburg")["fetch"]
real_arcgis = make_tiles.arcgis
make_tiles.arcgis = lambda *a, **k: [{"geometry": {"type": "Polygon", "coordinates": [[[28.047, -26.206], [28.048, -26.206],
    [28.048, -26.205], [28.047, -26.206]]]}, "properties": {"ELEVATION": 204.1}}]
assert [b["height"] for b in joburg((0, 0, 1, 1))] == [204.1]
make_tiles.arcgis = real_arcgis
at = lambda lat, lon: building_source(cells_in(lat, lon, lat + 0.0002, lon + 0.0002)[0])
assert at(43.3236, -1.9849) == "San Sebastián" and at(-26.2058, 28.0471) == "Johannesburg" and at(-26.1063, 28.0542) == "Johannesburg"

# 2026-10-02 rows. UTM forward and back agree (Düsseldorf's Dreischeibenhaus).
from make_tiles import wgs84_to_utm, utm_to_wgs84, mercator_to_wgs84, citygml_buildings, citygml_tiles, beoland, liguria
x, y = wgs84_to_utm(51.2282, 6.7826, 32)
assert 345178 < x < 345179 and 5677538 < y < 5677539
assert all(abs(a - b) < 1e-7 for a, b in zip(utm_to_wgs84(x, y, 32), (51.2282, 6.7826)))
# NRW's CityGML (a real Altstadt building, trimmed): each part's ground surface with its own measuredHeight
# (0 → 15 m); the parent, made only of parts, adds nothing; an interior ring is not a building.
part = lambda h, ring: f"""<bldg:consistsOfBuildingPart><bldg:BuildingPart><bldg:measuredHeight uom="urn:adv:uom:m">{h}</bldg:measuredHeight>
<bldg:boundedBy><bldg:GroundSurface><bldg:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing>
<gml:posList srsDimension="3">{ring}</gml:posList></gml:LinearRing></gml:exterior><gml:interior><gml:LinearRing><gml:posList srsDimension="3">0 0 0 1 1 1 2 2 2</gml:posList>
</gml:LinearRing></gml:interior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></bldg:lod2MultiSurface></bldg:GroundSurface></bldg:boundedBy>
</bldg:BuildingPart></bldg:consistsOfBuildingPart>"""
ring = ("344635.253 5677213.394 35.455 344635.015 5677213.366 35.455 344631.737 5677212.759 35.455 344631.273 5677216.688 35.455 "
        "344631.103 5677218.188 35.455 344634.681 5677218.845 35.455 344635.253 5677213.394 35.455")
gml = ('<core:CityModel xmlns:core="http://www.opengis.net/citygml/1.0" xmlns:bldg="http://www.opengis.net/citygml/building/1.0" '
       'xmlns:gml="http://www.opengis.net/gml"><core:cityObjectMember><bldg:Building>'
       + part(8.934, ring) + part(0, ring) + "</bldg:Building></core:cityObjectMember></core:CityModel>").encode()
built = citygml_buildings(gml)
assert [b["height"] for b in built] == [8.9, 15.0] and len(built[0]["outline"]) == 7
assert [b.get("guessed") for b in built] == [None, True]
assert abs(built[0]["outline"][0]["latitude"] - 51.2251) < 0.0001 and abs(built[0]["outline"][0]["longitude"] - 6.7750) < 0.001
# The tiles a box touches, by their south-west corner in km (Bavaria: 2 km, even); a missing tile (404) is empty.
asked = []
def fake_get(url, data=None, parse=None, **k):
    asked.append(url)
    if "654_" in url: raise make_tiles.SourceError("download1.bayernwolke.de: HTTP 404")
    return gml
make_tiles.get = fake_get
fetch = citygml_tiles("https://t/{e}_{n}.gml", 2)
assert fetch((49.4315, 11.119, 49.4345, 11.124)) == [] and sorted(asked) == ["https://t/652_5476.gml", "https://t/654_5476.gml"]
assert len(citygml_tiles("https://u/{e}_{n}.gml", 1)((51.2250, 6.7747, 51.2253, 6.7752))) == 2   # the fixture, inside
# Belgrade's multipatch: the footprint's clockwise ring (a counter-clockwise one is a hole), height = extent top − bottom.
def fake_get(url, data=None, parse=None, **k):
    cw = [[20.4588, 44.8167], [20.4594, 44.8167], [20.4594, 44.8163], [20.4588, 44.8167]]
    if "multipatchOption=extent" in url:
        return {"features": [{"attributes": {"objectid": 41}, "geometry": {"rings": [[[20.45886, 44.81628, 113.75],
                [20.45944, 44.81669, 140.277], [20.45886, 44.81628, 113.75]]]}}]}
    return {"features": [{"attributes": {"objectid": 41}, "geometry": {"rings": [cw, cw[::-1]]}},
                         {"attributes": {"objectid": 42}, "geometry": {"rings": [cw]}}]}
make_tiles.get = fake_get
assert [(b["height"], b.get("guessed")) for b in beoland((44.81, 20.45, 44.82, 20.46))] == [(26.5, None), (15.0, True)]
# Genoa: eave elevation minus the nearest foot spot height (0301) within 40 m; an eave spot (0302) is no foot.
real_wfs = make_tiles.wfs
make_tiles.wfs = lambda url, params, rect, **k: ([{"geometry": {"type": "Point", "coordinates": [8.93235, 44.40763, 18.11]},
        "properties": {"pt_quo_q": 18.11, "pt_quo_sed": "0301"}},
    {"geometry": {"type": "Point", "coordinates": [8.93236, 44.40763, 69.11]}, "properties": {"pt_quo_q": 69.11, "pt_quo_sed": "0302"}}]
    if params["TYPENAMES"] == "M2052:L6911" else
    [{"geometry": {"type": "Polygon", "coordinates": [[[8.9322, 44.4076, 69.11], [8.9324, 44.4076, 69.11], [8.9324, 44.4077, 69.11],
        [8.9322, 44.4076, 69.11]]]}}, {"geometry": {"type": "Polygon", "coordinates": [[[8.95, 44.42, 50], [8.951, 44.42, 50],
        [8.951, 44.421, 50], [8.95, 44.42, 50]]]}}])
assert [(b["height"], b.get("guessed")) for b in liguria((44.40, 8.93, 44.41, 8.94))] == [(51.0, None), (15.0, True)]
make_tiles.wfs = real_wfs
# Thessaloníki's permits come in Web Mercator (its degrees are rounded to ~100 m), filtered on a permit number.
lat, lon = mercator_to_wgs84(2553970.46, 4958174.064)
assert abs(lat - 40.631739) < 1e-6 and abs(lon - 22.942707) < 1e-6
asked.clear()
def fake_get(url, data=None, parse=None, **k):
    asked.append(urllib.parse.unquote_plus(url))
    return {"features": [{"geometry": {"type": "MultiPoint", "coordinates": [[2553970.46, 4958174.064]]}, "properties": {"eponymia": "BUTLER"}}]}
make_tiles.get = fake_get
thess = next(c for c in PERMIT_CITIES if c["city"] == "Thessaloníki")
t, = permits(thess, (40.63, 22.94, 40.637, 22.948))
assert t["name"] == "BUTLER" and abs(t["coordinate"]["latitude"] - 40.631739) < 1e-6
assert "srsName=EPSG:3857" in asked[0] and "adeiestrap IS NOT NULL AND BBOX(geom,22.94,40.63,22.948,40.637" in asked[0]
make_tiles.get = real_get
# The ArcGIS rows' height rules: Zagreb metres, San José floors × 3.5 m on the ground-floor slab, San Jose feet.
rule = lambda city: [b["height"] for b in next(b for b in CITY_BUILDINGS if b["city"] == city)["fetch"]((0, 0, 1, 1))]
make_tiles.arcgis = lambda url, r, fields, where="1=1", oid="OBJECTID": [{"geometry": {"type": "Polygon", "coordinates": [[[0, 0],
    [0, 1], [1, 1], [0, 0]]]}, "properties": {"Z_Delta": 103.5, "CantPisos": 19, "Building_H": 285.62}}]
assert rule("Zagreb") == [103.5] and rule("San José") == [66.5] and rule("San Jose") == [87.1]
make_tiles.arcgis = real_arcgis
assert at(51.2263, 6.7727) == "Düsseldorf" and at(49.4539, 11.0775) == "Nuremberg" and at(45.8131, 15.9772) == "Zagreb"
assert at(44.8160, 20.4600) == "Belgrade" and at(44.7900, 20.4600) == "OSM" and at(9.9334, -84.0770) == "San José"
assert at(37.3377, -121.8855) == "San Jose" and at(44.4072, 8.9339) == "Genoa"
# Sofia: sgr_text's leading number is floors × 3 m, none one floor (real codes: Park Hotel Moskva, the NDK,
# a massive building with no count, an underground one).
make_tiles.arcgis = lambda url, r, fields, where="1=1", oid="OBJECTID": [{"geometry": {"type": "Polygon", "coordinates": [[[0, 0],
    [0, 1], [1, 1], [0, 0]]]}, "properties": {"sgr_text": t}} for t in ("26МСБЖ", "5МСБЖ", "МС", "-1МС", None)]
assert rule("Sofia") == [78.0, 15.0, 3.0, 3.0, 3.0]
flagged = lambda city: [b.get("guessed") for b in next(b for b in CITY_BUILDINGS if b["city"] == city)["fetch"]((0, 0, 1, 1))]
assert flagged("Sofia") == [None] * 5   # 5 floors is a measured 15 m; "none is one floor" is the row's rule, not the guess
make_tiles.arcgis = real_arcgis
assert at(42.6967, 23.3215) == "Sofia" and at(48.1437, 17.1088) == "Bratislava" and at(48.3500, 17.1088) == "OSM"
assert at(37.8053, -122.2724) == "Oakland"   # its cells are credited "Oakland+OSM" or "OSM": test_combine.py

# sources/: each layer writes its own field and keeps the other's; a cell's building source is what answered.
import tempfile, make_tiles
from make_tiles import note_sources, read, path, do_buildings
with tempfile.TemporaryDirectory() as out:
    cell = cells_in(51.219, 4.402, 51.2192, 4.4022)[0]  # Antwerp: no city feed, not France
    note_sources(out, cell, permits=None)
    assert read(path(out, "sources", cell)) == [{"permits": None}]
    make_tiles.FETCH_BUILDINGS["OSM"] = lambda r: [{"height": 9, "outline": [{"latitude": cell.lat, "longitude": cell.lon}] * 3}]
    do_buildings([cell], out, [])
    assert read(path(out, "sources", cell)) == [{"permits": None, "buildings": "OSM"}]
    note_sources(out, cell, permits="Paris")
    assert read(path(out, "sources", cell)) == [{"permits": "Paris", "buildings": "OSM"}]
    # The 15 m guess reaches the tile flagged, a measured height bare (2026-10-04); the key order is the tile's.
    from make_tiles import building, footprints, osm_height, DEFAULT_HEIGHT
    ring = [{"latitude": cell.lat, "longitude": cell.lon}] * 3
    assert building(ring) == {"outline": ring, "height": 15.0, "guessed": True} and DEFAULT_HEIGHT == 15.0
    assert building(ring, 15) == {"outline": ring, "height": 15.0}   # a measured 15 m is not a guess
    assert osm_height({}) is None and osm_height({"building:levels": "4"}) == 12.0 and osm_height({"height": "15 m"}) == 15.0
    make_tiles.FETCH_BUILDINGS["OSM"] = lambda r: [building(ring, 9), building(ring)]
    do_buildings([cell], out, [])
    assert read(path(out, "buildings", cell)) == [{"outline": ring, "height": 9.0}, {"outline": ring, "height": 15.0, "guessed": True}]
# footprints(): a feature its rule reads nothing from is the flagged guess, or left out with guess=False.
feature = lambda h: {"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}, "properties": {"h": h}}
assert [(b["height"], b.get("guessed")) for b in footprints([feature(30), feature(None), feature(0)], lambda p: p.get("h"))] == \
    [(30.0, None), (15.0, True), (15.0, True)]
assert [b["height"] for b in footprints([feature(30), feature(None)], lambda p: p.get("h"), guess=False)] == [30.0]
# combined(): a guessed OSM footprint takes the city's height and drops the flag; one the city lacks keeps it.
from make_tiles import combined
sq = lambda s, w, n, e: [{"latitude": a / 1e4, "longitude": o / 1e4} for a, o in ((s, w), (s, e), (n, e), (n, w))]
osm_guess, osm_far = building(sq(0, 0, 1, 1)), building(sq(5, 5, 6, 6))
assert combined([osm_guess, osm_far], [building(sq(0, 0, 1, 1), 33)]) == \
    [{"outline": sq(0, 0, 1, 1), "height": 33.0, "city": True}, osm_far]
assert combined([osm_guess], [building(sq(0, 0, 1, 1), 33)], metres=True)[0].get("guessed") is None

# A building source is held to its municipal boundary: Fitzroy sits in Melbourne's box but not in
# the City of Melbourne, Bitsaron (Tel Aviv) in Ramat Gan's box but not in Ramat Gan.
from make_tiles import building_source, hamburg, CITY_BUILDINGS
at = lambda lat, lon: building_source(cells_in(lat, lon, lat + 0.0002, lon + 0.0002)[0])
assert at(-37.8150, 144.9660) == "Melbourne" and at(-37.7990, 144.9790) == "OSM"
assert at(32.0830, 34.8130) == "Ramat Gan" and at(32.0760, 34.8000) == "Tel Aviv"
assert at(32.0150, 34.7800) == "Holon" and at(32.1650, 34.8400) == "Herzliya" and at(53.5500, 10.0000) == "Hamburg"
# Floors arrive as strings in Holon and Herzliya: × 3 m, blank → the 15 m default.
floors = next(b for b in CITY_BUILDINGS if b["city"] == "Holon")["fetch"]
ring = {"type": "Polygon", "coordinates": [[[34.78, 32.01], [34.781, 32.01], [34.781, 32.011], [34.78, 32.01]]]}
make_tiles.arcgis = lambda *a, **k: [{"geometry": ring, "properties": {"NUM_FLOORS": "4"}}, {"geometry": ring, "properties": {"NUM_FLOORS": " "}}]
assert [(b["height"], b.get("guessed")) for b in floors((0, 0, 1, 1))] == [(12.0, None), (15.0, True)]
# Hamburg's CityJSON: the ground surface in UTM 32N, measuredHeight.
make_tiles.get_json = lambda url: {"transform": {"scale": [1, 1, 1], "translate": [566000, 5935000, 0]},
    "vertices": [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 0, 20]],
    "CityObjects": {"a": {"attributes": {"measuredHeight": 20.5}, "geometry": [{"boundaries": [[[[0, 1, 2]], [[3, 1, 2]]]],
        "semantics": {"surfaces": [{"type": "GroundSurface"}, {"type": "RoofSurface"}], "values": [[0, 1]]}}]}}}
b, = hamburg((53.5, 10.0, 53.6, 10.1))
assert b["height"] == 20.5 and len(b["outline"]) == 3 and abs(b["outline"][0]["latitude"] - 53.55) < 0.01
# La Rochelle: one whole CSV (real rows, trimmed), the point its "lat,lon" column; only the three terrace kinds,
# furniture and untyped rows dropped, a terrace with no sign kept unnamed.
make_tiles.get = lambda url, **k: open("fixtures/la-rochelle.csv", "rb").read()
rochelle = next(c for c in PERMIT_CITIES if c["city"] == "La Rochelle")
got = permits(rochelle, (46.14, -1.22, 46.18, -1.12))
assert [t["kind"] for t in got] == ["Terrasse - extension saisonnière", "Terrasse couverte", "Terrasse", "Terrasse"]
assert [t.get("name") for t in got] == ["LE RECIF", "LE BISTROT DE MEME", "LA CORNICHE", None]
assert abs(got[0]["coordinate"]["latitude"] - 46.14127796782068) < 1e-9 and abs(got[0]["coordinate"]["longitude"] + 1.1708002829565918) < 1e-9
assert permits(rochelle, (46.15, -1.18, 46.16, -1.17)) == [got[2]]   # the file is read once, then cut to the cell
make_tiles.get = real_get
# Barcelona: the newest CSV resource (listed first) read through CKAN's datastore API, paged by offset to the
# total (its download URL answers a bot-detection page); a row without a point is dropped.
asked.clear()
def fake_get_json(url, **k):
    asked.append(url)
    if "package_show" in url:
        return {"result": {"resources": [{"id": "pdf", "format": "PDF"}, {"id": "new", "format": "CSV"}, {"id": "old", "format": "CSV"}]}}
    rows = [{"LATITUD": "41.4083712701906", "LONGITUD": "2.17453673339805"}, {"LATITUD": "", "LONGITUD": ""},
            {"LATITUD": "41.39", "LONGITUD": "2.16"}]
    offset = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["offset"][0])
    return {"result": {"total": 3, "records": rows[offset:offset + 2]}}
make_tiles.get_json, real_get_json = fake_get_json, make_tiles.get_json
bcn = next(c for c in PERMIT_CITIES if c["city"] == "Barcelona")
got = permits(bcn, (41.32, 2.05, 41.47, 2.23))
assert [t["coordinate"] for t in got] == [{"latitude": 41.4083712701906, "longitude": 2.17453673339805},
                                          {"latitude": 41.39, "longitude": 2.16}] and all(t["kind"] == "TERRASSE" for t in got)
assert [urllib.parse.parse_qs(urllib.parse.urlsplit(u).query).get("resource_id") for u in asked[1:]] == [["new"], ["new"]]
make_tiles.get_json = real_get_json

# 2026-10-03 rows. Vaughan: NRCan's GTA GeoPackage (three real rows at VMC, the tallest Transit City tower and two
# low buildings, with their R-tree), unzipped once and read through the R-tree: heightmax, metres, as the height.
import json, os, shutil
vaughan = next(b for b in CITY_BUILDINGS if b["city"] == "Vaughan")["fetch"]
fixture_zip, here, real_download, fetched = os.path.abspath("fixtures/vaughan-gpkg.zip"), os.getcwd(), make_tiles.download, []
def fake_download(url, folder, max_days=6):
    fetched.append(url)
    os.makedirs(folder, exist_ok=True)
    return shutil.copy(fixture_zip, os.path.join(folder, url.rsplit("/", 1)[1]))
with tempfile.TemporaryDirectory() as d:
    os.chdir(d)
    make_tiles.download = fake_download
    try:
        got = vaughan((43.7965, -79.5305, 43.7990, -79.5265))
        assert sorted(b["height"] for b in got) == [6.2, 8.4, 183.6]
        assert max(got, key=lambda b: b["height"])["outline"][0] == {"latitude": 43.797692, "longitude": -79.528824}
        assert len(vaughan((43.7972, -79.5286, 43.7973, -79.5285))) == 1 and vaughan((43.70, -79.40, 43.71, -79.39)) == []
        assert fetched == [make_tiles.NRCAN_GTA]   # downloaded once a run
    finally:
        os.chdir(here)
        make_tiles.download = real_download
        for db, *_ in make_tiles._geopackages.values(): db.close()
        make_tiles._geopackages.clear()
# Edmonton: Rooflines (real Socrata features, numbers as strings: the 146.84 m roof and two low ones), asked by intersects.
asked.clear()
def fake_get_json(url, data=None, waits=None):
    asked.append(urllib.parse.unquote_plus(url))
    with open("fixtures/edmonton.json") as f: return {"features": json.load(f)["features"]}
make_tiles.get_json = fake_get_json
got = next(b for b in CITY_BUILDINGS if b["city"] == "Edmonton")["fetch"]((53.54, -113.50, 53.55, -113.48))
assert [b["height"] for b in got] == [146.8, 8.4, 8.2]
assert got[0]["outline"][0] == {"latitude": 53.541894, "longitude": -113.493936}
assert asked[0].startswith("https://data.edmonton.ca/resource/jpxi-a9a5.geojson?") and "intersects(the_geom, 'POLYGON((-113.5 53.54" in asked[0]
# Each held to its boundary: York University (Toronto, its own row since 2026-10-05) and Brampton sit in Vaughan's
# box, St. Albert and Sherwood Park in Edmonton's.
assert at(43.8540, -79.5084) == "Vaughan" and at(43.7970, -79.5290) == "Vaughan"
assert at(43.7735, -79.5019) == "Toronto" and at(43.7600, -79.6900) == "OSM"
assert at(53.5444, -113.4909) == "Edmonton" and at(53.6305, -113.6256) == "OSM" and at(53.5400, -113.2950) == "OSM"

# 2026-10-03: Berlin, New York and Istanbul tile the whole city (Istanbul its built-up area). Each
# cities.json box holds its boundary, and Berlin's and New York's own feeds reach the boundary's every
# vertex, so no outer cell falls back to OSM: Spandau and Marzahn; Staten Island, the Bronx, eastern Queens.
from make_tiles import bounds, in_box, permit_city
areas = {e["city"]: e for e in json.load(open("cities.json")) if e["city"] in ("Berlin", "New York", "Istanbul")}
for city, e in areas.items():
    with open(e["boundary"]) as f: shape = json.load(f)
    s, w, n, e_ = bounds(shape)
    assert e["box"][0] <= s and e["box"][1] <= w and n <= e["box"][2] and e_ <= e["box"][3], city
    vertices = [v for p in ([shape["coordinates"]] if shape["type"] == "Polygon" else shape["coordinates"]) for r in p for v in r]
    feeds = [b for b in CITY_BUILDINGS if b["city"] == city] + [c for c in PERMIT_CITIES if c["city"] == city]
    assert len(feeds) == {"Berlin": 1, "New York": 2, "Istanbul": 0}[city], city
    assert all(in_box(b, lat, lon) for b in feeds for lon, lat in vertices), city
spot = lambda lat, lon: cells_in(lat, lon, lat + 0.0002, lon + 0.0002)[0]
assert at(52.5365, 13.2040) == "Berlin" and at(52.5445, 13.5655) == "Berlin"
for lat, lon in ((40.5110, -74.2470), (40.8610, -73.8900), (40.7440, -73.7140)):
    assert at(lat, lon) == "New York" and permit_city(spot(lat, lon), None)["city"] == "New York"
assert at(41.0080, 28.9780) == "OSM" and permit_city(spot(41.0080, 28.9780), None) is None
# A permit feed is held to its boundary too (2026-10-03): a Vaughan cell (the one at 43.857, -79.515, credited
# "Toronto" before) sits in Toronto's box but not in the City of Toronto; York University and the Islands are in
# it. Marylebone (Westminster) and Highbury and Angel (Islington) sit in Camden's box but not in the borough;
# Camden Town, Bloomsbury and Kilburn are in it. A row without a boundary is still picked by box alone.
from make_tiles import permit_city
feed = lambda lat, lon: (permit_city(cells_in(lat, lon, lat + 0.0002, lon + 0.0002)[0], None) or {}).get("city")
assert feed(43.6500, -79.3800) == "Toronto" and feed(43.7735, -79.5019) == "Toronto" and feed(43.6200, -79.3800) == "Toronto"
assert feed(43.8570, -79.5150) is None and feed(43.8000, -79.4200) is None
assert feed(51.5390, -0.1430) == "Camden" and feed(51.5220, -0.1250) == "Camden" and feed(51.5470, -0.1950) == "Camden"
assert feed(51.5226, -0.1571) is None and feed(51.5460, -0.1040) is None and feed(51.5322, -0.1058) is None
assert feed(40.7580, -73.9855) == "New York" and feed(43.3127, -1.9582) == "San Sebastián"
# 2026-10-04 rows: the terrace re-survey's six registers, each on a trimmed real answer. Helsinki: the kind filter folds
# into BBOX() on singlegeom; each polygon lands on its mean vertex, the name less "Terassialue" and a size, a bare
# "Talviterassi" unnamed (door rule).
from make_tiles import arcgis_filter, helsinki_venue_name, seattle_venue_name, not_applicable, unique
import datetime
# Hamburg's and Edmonton's get_json fakes above were left in place (and real_get_json is Hamburg's): the real one again.
make_tiles.get_json = lambda url, data=None, waits=make_tiles.WAITS: make_tiles.get(url, data, parse=json.loads, waits=waits)
make_tiles.arcgis = real_arcgis   # Holon's fake above was left in place too
asked.clear()
def fake_get(url, data=None, parse=None, **k):
    asked.append(urllib.parse.unquote_plus(url))
    with open("fixtures/helsinki.json") as f: return json.load(f)
make_tiles.get = fake_get
helsinki = next(c for c in PERMIT_CITIES if c["city"] == "Helsinki")
got = permits(helsinki, (60.16, 24.93, 60.19, 24.99))
assert [t.get("name") for t in got] == ["On The Rocks", "Chaos Bar", "Oluthuone Haavi", None] and all(t["kind"] == "TERRASSE" for t in got)
assert abs(got[0]["coordinate"]["latitude"] - 60.17109383) < 1e-6 and abs(got[0]["coordinate"]["longitude"] - 24.9453338) < 1e-6
assert "hakemuksen_laji IN ('Kesäterassi','Talviterassi') AND BBOX(singlegeom,24.93,60.16,24.99,60.19,'EPSG:4326')" in asked[0]
assert "srsName=EPSG:4326" in asked[0] and "typeName=avoindata:Lyhyt_maanvuokraus_alue" in asked[0]
assert helsinki_venue_name("Kesäterassi 12 m2 Milli Miglia -ravintolan edustalla") == "Milli Miglia -ravintolan edustalla"
assert helsinki_venue_name("Terassi alue Cafe Berry") == "Cafe Berry" and helsinki_venue_name("Bar Llamas") == "Bar Llamas"
assert helsinki_venue_name('Terassialue "Mon Vietnam"') == "Mon Vietnam" and helsinki_venue_name("Kesäterassi") is None
# Stockholm: a WFS behind a key in the URL path, on a fixture shaped by hand (not the live feed: no key here). The
# uteservering kind of ärendekategori, unexpired, kept; a latitude-first point turned round; Bangolf, a building site
# and last summer's lease dropped. Without the key the row is left out (OSM only) and nothing is asked; the key never
# reaches a log.
import os
from make_tiles import Cell, permit_city, redact
stockholm = next(c for c in PERMIT_CITIES if c["city"] == "Stockholm")
os.environ.pop("STOCKHOLM_API_KEY", None)
asked.clear()
assert permit_city(Cell(29667, 9037), None) is None and asked == []
os.environ["STOCKHOLM_API_KEY"] = "s3cr3t-key"
def fake_get(url, data=None, parse=None, **k):
    asked.append(urllib.parse.unquote_plus(url))
    with open("fixtures/stockholm.json") as f: return json.load(f)
make_tiles.get = fake_get
assert permit_city(Cell(29667, 9037), None) is stockholm
got = make_tiles.stockholm(stockholm, (59.33, 18.06, 59.34, 18.08), today="2026-10-05")
assert [t["kind"] for t in got] == ["TERRASSE"] * 3 and not any("name" in t for t in got)
assert [round(t["coordinate"]["latitude"], 4) for t in got] == [59.3356, 59.3349, 59.3358]
assert [round(t["coordinate"]["longitude"], 4) for t in got] == [18.0742, 18.0731, 18.0738]
assert asked[0].startswith("https://openstreetgs.stockholm.se/geoservice/api/s3cr3t-key/wfs?")
assert "typeName=od_gis:Markupplatelse_Punkt" in asked[0] and "bbox=18.06,59.33,18.08,59.34,EPSG:4326" in asked[0]
assert redact("GET https://openstreetgs.stockholm.se/geoservice/api/s3cr3t-key/wfs") == \
    "GET https://openstreetgs.stockholm.se/geoservice/api/***/wfs"
os.environ.pop("STOCKHOLM_API_KEY")
# Gothenburg: one ';' CSV with a BOM, read whole; a terrace where Serveringstyper lists Uteservering (alone or among
# catering and tastings) and the public is served (alone or with closed companies); no outdoor serving, a closed
# company and a row without a point dropped.
make_tiles.get = lambda url, **k: open("fixtures/gothenburg.csv", "rb").read()
gothenburg = next(c for c in PERMIT_CITIES if c["city"] == "Gothenburg")
got = permits(gothenburg, (57.57, 11.76, 57.80, 12.06))
assert [t["name"] for t in got] == ["Werners Bistro", "DINÉ Burgers Drottninggatan", "Beerbliotek Brewery"]
assert all(t["kind"] == "TERRASSE" for t in got) and abs(got[0]["coordinate"]["latitude"] - 57.70404059127321) < 1e-9
assert permits(gothenburg, (57.70, 11.97, 57.71, 11.98)) == [got[0]]   # read once, cut to the cell
# Washington DC: issued sidewalk cafés and streateries, enclosed as TERRASSE FERMEE; only OBJECTID, ApplicantCompany and
# EventTypeDescription are asked for — never OwnerName or PermitteeName, which hold people's names — and "N/A" names
# nobody. A renewal repeats its point: unique() (gather's) keeps one.
asked.clear()
def fake_get(url, data=None, parse=None, **k):
    asked.append(urllib.parse.unquote_plus(url))
    with open("fixtures/washington-dc.json") as f: return json.load(f)
make_tiles.get = fake_get
dc = next(c for c in PERMIT_CITIES if c["city"] == "Washington DC")
got = permits(dc, (38.89, -77.03, 38.91, -77.00))
assert [t["kind"] for t in got] == ["TERRASSE OUVERTE", "TERRASSE OUVERTE", "TERRASSE FERMEE", "TERRASSE OUVERTE"]
assert [t.get("name") for t in got] == [None, None, "Gobind LLC", None] and len(unique(got)) == 3
assert "outFields=OBJECTID,ApplicantCompany,EventTypeDescription" in asked[0] and "OwnerName" not in asked[0] and "PermitteeName" not in asked[0]
assert "(EventTypeDescription LIKE 'New Sidewalk Cafe%' OR EventTypeDescription LIKE 'Streatery%') AND Status = 'ISSUED'" in asked[0]
with open("fixtures/washington-dc.json") as f: assert "OwnerName" not in f.read()
assert not_applicable("N/A") is None and not_applicable("521597097") is None and not_applicable(" Compass Coffee ") == "Compass Coffee"
# Boston: the OID field is ObjectId (a FeatureServer's), both licence types open air.
asked.clear()
def fake_get(url, data=None, parse=None, **k):
    asked.append(urllib.parse.unquote_plus(url))
    with open("fixtures/boston.json") as f: return json.load(f)
make_tiles.get = fake_get
boston = next(c for c in PERMIT_CITIES if c["city"] == "Boston")
got = permits(boston, (42.35, -71.08, 42.36, -71.05))
assert [t["name"] for t in got] == ["Lily's Pizza", "75 Chestnut"] and all(t["kind"] == "TERRASSE" for t in got)
assert "ObjectId>-1" in asked[0] and "outFields=ObjectId,doing_business_as" in asked[0] and "orderByFields=ObjectId" in asked[0]
# Seattle: the description searched, issued in the last fifteen months (the date resolved at run time); the name only
# from the "Venue | kind | street" form, a kind-first or free-text name dropped.
asked.clear()
def fake_get(url, data=None, parse=None, **k):
    asked.append(urllib.parse.unquote_plus(url))
    with open("fixtures/seattle.json") as f: return json.load(f)
make_tiles.get = fake_get
seattle = next(c for c in PERMIT_CITIES if c["city"] == "Seattle")
got = permits(seattle, (47.60, -122.36, 47.68, -122.31))
assert [t.get("name") for t in got] == ["Mecca", None, None, None] and all(t["kind"] == "TERRASSE" for t in got)
ago = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=457)).isoformat()
assert f"PERMIT_STATUS = 'Issued' AND LAST_ISSUED_DATE >= DATE '{ago}'" in asked[0] and "{fifteenMonthsAgo}" not in asked[0]
assert arcgis_filter("a >= DATE '{today}'") == f"a >= DATE '{datetime.datetime.now(datetime.timezone.utc).date().isoformat()}'"
assert seattle_venue_name("Till Dawn | Sidewalk Cafe | On California Ave SW") == "Till Dawn"
assert seattle_venue_name("Fenced Sidewalk Cafe transfer ownership | 1st Ave") is None and seattle_venue_name("SIDEWALK CAFE") is None
# Kensington and Chelsea: unexpired licences only, held to the borough — Abingdon Road, Earl's Court and Portobello are
# in it, Hyde Park (Westminster), Shepherd's Bush (Hammersmith and Fulham) and Marylebone sit in the box and are not.
asked.clear()
def fake_get(url, data=None, parse=None, **k):
    asked.append(urllib.parse.unquote_plus(url))
    with open("fixtures/kensington-and-chelsea.json") as f: return json.load(f)
make_tiles.get = fake_get
rbkc = next(c for c in PERMIT_CITIES if c["city"] == "Kensington and Chelsea")
got = permits(rbkc, (51.49, -0.20, 51.51, -0.19))
assert [t["name"] for t in got] == ["Nouvelle Delicatessen", "The Abingdon", "Amorino"] and all(t["kind"] == "TERRASSE" for t in got)
assert "(ExpiryDate >= CURRENT_TIMESTAMP) AND OBJECTID>-1" in asked[0] and "outFields=OBJECTID,TradingName" in asked[0]
make_tiles.get = real_get
assert feed(51.4977, -0.1958) == "Kensington and Chelsea" and feed(51.4913, -0.1950) == "Kensington and Chelsea" and feed(51.5150, -0.2050) == "Kensington and Chelsea"
assert feed(51.5074, -0.1657) is None and feed(51.5046, -0.2187) is None and feed(51.5226, -0.1571) is None
assert feed(60.1711, 24.9453) == "Helsinki" and feed(57.7040, 11.9782) == "Gothenburg" and feed(38.9016, -77.0204) == "Washington DC"
assert feed(42.3574, -71.0541) == "Boston" and feed(47.6241, -122.3564) == "Seattle"
print("ok")
# 2026-10-05 rows. Toronto: the GTA GeoPackage Vaughan reads, downloaded once for both rows (four real rows: the CN Tower,
# the tallest King West footprint and two low ones); Montréal: NRCan's VILLE_MONTREAL file (three real rows at Berri-UQAM
# and one of its 17 footprints at heightmax -1, the flagged guess).
toronto, montreal = (next(b for b in CITY_BUILDINGS if b["city"] == c)["fetch"] for c in ("Toronto", "Montréal"))
fixtures = {make_tiles.NRCAN_GTA: os.path.abspath("fixtures/toronto-gpkg.zip"), make_tiles.NRCAN_MONTREAL: os.path.abspath("fixtures/montreal-gpkg.zip")}
def fake_download(url, folder, max_days=6):
    fetched.append(url)
    os.makedirs(folder, exist_ok=True)
    return shutil.copy(fixtures[url], os.path.join(folder, url.rsplit("/", 1)[1]))
fetched.clear()
with tempfile.TemporaryDirectory() as d:
    os.chdir(d)
    make_tiles.download = fake_download
    try:
        got = toronto((43.644, -79.396, 43.646, -79.394))
        assert sorted(b["height"] for b in got) == [5.5, 17.5, 125.8] and not any("guessed" in b for b in got)
        assert [b["height"] for b in toronto((43.642, -79.389, 43.643, -79.388))] == [537.0]   # the CN Tower, lidar to its tip
        assert vaughan((43.7965, -79.5305, 43.7990, -79.5265)) == [] and fetched == [make_tiles.NRCAN_GTA]   # one file, one download
        got = montreal((45.514, -73.566, 45.516, -73.564))
        assert sorted(b["height"] for b in got) == [5.8, 17.4, 39.5] and not any("guessed" in b for b in got)
        assert [(b["height"], b.get("guessed")) for b in montreal((45.533, -73.699, 45.534, -73.698))] == [(15.0, True)]
        assert fetched == [make_tiles.NRCAN_GTA, make_tiles.NRCAN_MONTREAL]
    finally:
        os.chdir(here)
        make_tiles.download = real_download
        for db, *_ in make_tiles._geopackages.values(): db.close()
        make_tiles._geopackages.clear()
# Bogotá: IDECA's Construcción (three real footprints at Zona T), CONNPISOS × 3 m, 0 floors the flagged guess; paged on OBJECTID.
asked.clear()
def fake_get(url, data=None, parse=None, **k):
    asked.append(urllib.parse.unquote_plus(url))
    with open("fixtures/bogota.json") as f: return {"type": "FeatureCollection", "features": json.load(f)["features"]}
make_tiles.get = fake_get
got = next(b for b in CITY_BUILDINGS if b["city"] == "Bogotá")["fetch"]((4.666, -74.056, 4.668, -74.054))
assert [(b["height"], b.get("guessed")) for b in got] == [(21.0, None), (3.0, None), (15.0, True)]
assert got[0]["outline"][0] == {"latitude": 4.667134, "longitude": -74.055497}
assert asked[0].startswith("https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/catastro/construccion/MapServer/0/query?")
assert "OBJECTID>-1" in asked[0] and "outFields=OBJECTID,CONNPISOS" in asked[0] and "geometry=-74.056,4.666,-74.054,4.668" in asked[0]
make_tiles.get = real_get
# Each held to its boundary. Toronto: King West and York University are in, Mississauga sits in the box and is not, the
# VMC stays Vaughan's (its row comes first). Montréal: Berri-UQAM and Westmount (a ville liée the file covers) are in,
# Pont-Viau (Laval, in the file and the box) and Longueuil are not. Bogotá: Zona T.
assert at(43.6450, -79.3950) == "Toronto" and at(43.7735, -79.5019) == "Toronto" and at(43.5940, -79.6430) == "OSM"
assert at(43.7970, -79.5290) == "Vaughan"
assert at(45.5150, -73.5650) == "Montréal" and at(45.4840, -73.5960) == "Montréal"
assert at(45.5670, -73.6750) == "OSM" and at(45.5350, -73.5100) == "OSM"
assert at(4.6670, -74.0550) == "Bogotá"
print("ok (2026-10-05 rows)")

# Los Angeles: the 2021 Al Fresco snapshot, kept only where a venue of today (the published venues/ tiles) stands within
# 60 m under a matching name. The fixture is nine real rows of the feed over seven real tile cuts (2026-10-05): Dino's
# Burgers (a curly apostrophe against OSM's straight one), California Kabob Kitchen (the same name) and Little Joy
# Cocktails LLC (OSM's "Little Joy": the core words) survive; Cafe Esquinita has no venue within 60 m, Tilda's neighbour
# is "Tila" (another name), Spring Street Smokehouse's is "Thai", and the unnamed row cannot be matched. The feed repeats
# Tilda and Spring Street Smokehouse: unique() drops a repeat, as a build does.
from make_tiles import normalised_name, names_match, venue_tiles_around, surviving
asked.clear()
def fake_get(url, data=None, parse=None, **k):
    asked.append(urllib.parse.unquote_plus(url))
    with open("fixtures/los-angeles.json") as f: return json.load(f)
with open("fixtures/los-angeles-venues.json") as f: venue_fixture = json.load(f)
read_tiles = []
def fake_published_items(layer, key, bust=False):
    read_tiles.append((layer, key, bust))
    return venue_fixture.get(key, [])
make_tiles.get, make_tiles.published_items, real_published_items = fake_get, fake_published_items, make_tiles.published_items
make_tiles._venue_tiles.clear()
los_angeles = next(c for c in PERMIT_CITIES if c["city"] == "Los Angeles")
got = unique(permits(los_angeles, (34.03, -118.29, 34.09, -118.21)))
assert [t["name"] for t in got] == ["Dino’s Burgers", "California Kabob Kitchen", "Little Joy Cocktails LLC"], got
assert all(t["kind"] == "TERRASSE" for t in got)
assert "OBJECTID>-1" in asked[0] and "outFields=OBJECTID,Business_Name" in asked[0]
assert read_tiles and all(layer == "venues" and bust for layer, _, bust in read_tiles)
assert len(unique(make_tiles.fetched_permits(los_angeles, (34.03, -118.29, 34.09, -118.21)))) == 7   # 9 rows, 2 repeats, before the match
# A venue tile the store did not give fails the feed rather than dropping its permits silently.
make_tiles._venue_tiles.clear()
make_tiles.published_items = lambda layer, key, bust=False: make_tiles.SourceError("store: HTTP 503")
try: surviving([{"kind": "TERRASSE", "name": "Tilda", "coordinate": {"latitude": 34.08, "longitude": -118.255}}], "Los Angeles"); raise AssertionError
except make_tiles.SourceError as e: assert "Los Angeles: venues/" in str(e) and "HTTP 503" in str(e), e
make_tiles.published_items = real_published_items
make_tiles._venue_tiles.clear()
# The name rule is the app's (TerraceNames.swift): accents and punctuation set aside, one name in the other, the first
# two words shared, or the shorter's core words in the longer — one short shared word is not a match.
assert normalised_name("Dino’s Burgers") == "DINO S BURGERS" and normalised_name("Café Oz - The Australian Bar") == "CAFE OZ THE AUSTRALIAN BAR"
assert normalised_name(None) == "" and normalised_name("  ") == ""
assert names_match(normalised_name("Bar Ama"), normalised_name("Bar Amá")) and names_match("CAFE OZ", "CAFE OZ THE AUSTRALIAN BAR")
assert names_match(normalised_name("Little Joy Cocktails LLC"), "LITTLE JOY") and names_match("LA FONTANA", "LA FONTANA DE ORO")
assert names_match(normalised_name("The Window; Lowboy; Bar Flores"), "LOWBOY") and names_match("BAR POSTAS", "POSTAS 15")
assert not names_match("TILDA", "TILA") and not names_match("SOL 12", "BAR SOL Y SOMBRA") and not names_match("", "TILDA")
# A permit's venue can sit in the next tile: the keys of the tiles its 60 m reach touches, one to four.
assert venue_tiles_around(34.0660, -118.2122) == ["1703,-5910"]
assert venue_tiles_around(34.0801, -118.2554) == ["1703,-5912", "1704,-5912"]   # Tilda, 11 m above a tile edge
print("ok (Los Angeles)")
# 2026-10-05 rows, PLATEAU (Tokyo, Osaka): a few real buildings cut from three 2025 mesh files, gzipped as the store serves
# them. Shibuya's 53394505 (LoD1: the roof edge is the outline; a 66.2 m tower, a 14.2 m house, one at -9999: the flagged
# guess), Chiyoda's 53394509 (LoD2: the ground surface; 6 m, and -9999), Osaka's 51357309 (the LoD0 footprint; 9.3, 6.5, -9999).
from make_tiles import mesh_code, mesh_codes, plateau_buildings
assert mesh_code(35.6684, 139.6889) == "53394505" and mesh_code(35.6742, 139.7406) == "53394509" and mesh_code(34.5915, 135.4991) == "51357309"
assert mesh_code(35.6895, 139.6917) == "53394525"   # the Tokyo Metropolitan Government Building
assert mesh_codes((35.6684, 139.6889, 35.6684, 139.6889)) == ["53394505"]
assert mesh_codes((35.6745, 139.6995, 35.6752, 139.7005)) == ["53394505", "53394506", "53394515", "53394516"]   # a mesh corner
# The outline's precedence: the ground surface, else the footprint, else the roof edge; a part's own.
gml = lambda inner: f'<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gml="http://www.opengis.net/gml"><core:cityObjectMember>{inner}</core:cityObjectMember></core:CityModel>'.encode()
ring = lambda tag, lat: f"<bldg:{tag}><gml:MultiSurface><gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing><gml:posList>{lat} 139.7 0 {lat} 139.701 0 {lat + 0.001} 139.701 0 {lat} 139.7 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></bldg:{tag}>"
ground = lambda lat: f"<bldg:boundedBy><bldg:GroundSurface>{ring('lod2MultiSurface', lat)}</bldg:GroundSurface></bldg:boundedBy>"
got = plateau_buildings(gml(f'<bldg:Building><bldg:measuredHeight uom="m">12.5</bldg:measuredHeight>{ring("lod0RoofEdge", 35.1)}{ring("lod0FootPrint", 35.2)}{ground(35.3)}</bldg:Building>'))
assert [(b["height"], b["outline"][0]["latitude"]) for b in got] == [(12.5, 35.3)]
got = plateau_buildings(gml(f'<bldg:Building><bldg:measuredHeight uom="m">12.5</bldg:measuredHeight>{ring("lod0RoofEdge", 35.1)}{ring("lod0FootPrint", 35.2)}</bldg:Building>'))
assert [(b["height"], b["outline"][0]["latitude"]) for b in got] == [(12.5, 35.2)]
got = plateau_buildings(gml(f'<bldg:Building><bldg:measuredHeight uom="m">0</bldg:measuredHeight>{ring("lod0RoofEdge", 35.1)}'
                            f'<bldg:consistsOfBuildingPart><bldg:BuildingPart><bldg:measuredHeight uom="m">30</bldg:measuredHeight>{ring("lod0RoofEdge", 35.4)}</bldg:BuildingPart></bldg:consistsOfBuildingPart></bldg:Building>'))
assert [(b["height"], b.get("guessed"), b["outline"][0]["latitude"]) for b in got] == [(15.0, True, 35.1), (30.0, None, 35.4)]
# The rows: the catalogue read once a run per city code (Tokyo's 23 wards, Osaka's one), each mesh file downloaded once
# (gzip asked for, kept a year under its city code), a mesh no city lists empty.
tokyo, osaka = (next(b for b in CITY_BUILDINGS if b["city"] == c)["fetch"] for c in ("Tokyo", "Osaka"))
plateau_files = {"13113": {"53394505": "https://assets.example/13113/udx/bldg/53394505_bldg_6697_op.gml"},
                 "13101": {"53394509": "https://assets.example/13101/udx/bldg/53394509_bldg_6697_op.gml"},
                 "27100": {"51357309": "https://assets.example/27100/udx/bldg/51357309_bldg_6697_op.gml"}}
fixtures = {url: os.path.abspath(f"fixtures/plateau-{'osaka' if code == '27100' else 'tokyo'}-{mesh}.gml.gz")
            for code, files in plateau_files.items() for mesh, url in files.items()}
asked.clear(); fetched.clear()
def fake_get_json(url, data=None, waits=None):
    asked.append(url)
    code = url.rsplit("/", 1)[1]
    return {"cities": [{"cityCode": code, "year": 2025, "files": {"bldg": [{"code": m, "url": u} for m, u in plateau_files.get(code, {}).items()]}}]}
def fake_download(url, folder, max_days=6, headers=None):
    fetched.append((url, folder, max_days, (headers or {}).get("Accept-Encoding")))
    os.makedirs(folder, exist_ok=True)
    return shutil.copy(fixtures[url], os.path.join(folder, url.rsplit("/", 1)[1]))
with tempfile.TemporaryDirectory() as d:
    os.chdir(d)
    make_tiles.get_json, make_tiles.download = fake_get_json, fake_download
    try:
        got = tokyo((35.668, 139.688, 35.6685, 139.6885))
        assert [(b["height"], len(b["outline"]), b["outline"][0]) for b in got] == [(66.2, 18, {"latitude": 35.668379, "longitude": 139.688166})]
        assert asked == [make_tiles.PLATEAU_INDEX.format(f"131{k:02d}") for k in range(1, 24)]
        assert fetched == [(plateau_files["13113"]["53394505"], os.path.join("extracts", "plateau", "13113"), 365, "gzip")]
        assert [(b["height"], b.get("guessed")) for b in tokyo((35.6745, 139.695, 35.6752, 139.697))] == [(14.2, None), (15.0, True)]
        assert [(b["height"], b.get("guessed"), len(b["outline"])) for b in tokyo((35.667, 139.738, 35.6745, 139.741))] == [(6.0, None, 4), (15.0, True, 5)]
        assert tokyo((35.70, 139.77, 35.701, 139.771)) == []   # a mesh no ward lists
        assert len(asked) == 23 and [f[0].rsplit("/", 1)[1][:8] for f in fetched] == ["53394505", "53394509"]   # once a run each
        got = osaka((34.5915, 135.4989, 34.5916, 135.4992))
        assert [(b["height"], b["outline"][0]) for b in got] == [(9.3, {"latitude": 34.591532, "longitude": 135.499073})]
        assert [(b["height"], b.get("guessed")) for b in osaka((34.5912, 135.4997, 34.5914, 135.4999))] == [(6.5, None), (15.0, True)]
        assert asked[23:] == [make_tiles.PLATEAU_INDEX.format("27100")] and fetched[2][1:] == (os.path.join("extracts", "plateau", "27100"), 365, "gzip")
    finally:
        os.chdir(here)
        make_tiles.get_json, make_tiles.download = real_get_json, real_download
        make_tiles._plateau.clear(); make_tiles._plateau_index.clear()
# Each box is its city's tiled area; east of Osaka's city line (Higashiōsaka) the row answers nothing and the cell falls to OSM.
assert at(35.7015, 139.7395) == "Tokyo" and at(35.6938, 139.7034) == "Tokyo" and at(35.75, 139.70) == "OSM"
assert at(34.7050, 135.4970) == "Osaka" and at(34.6664, 135.5003) == "Osaka" and at(34.70, 135.62) == "OSM"
print("ok (PLATEAU rows)")
