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
assert [b["height"] for b in beoland((44.81, 20.45, 44.82, 20.46))] == [26.5, 15.0]
# Genoa: eave elevation minus the nearest foot spot height (0301) within 40 m; an eave spot (0302) is no foot.
real_wfs = make_tiles.wfs
make_tiles.wfs = lambda url, params, rect, **k: ([{"geometry": {"type": "Point", "coordinates": [8.93235, 44.40763, 18.11]},
        "properties": {"pt_quo_q": 18.11, "pt_quo_sed": "0301"}},
    {"geometry": {"type": "Point", "coordinates": [8.93236, 44.40763, 69.11]}, "properties": {"pt_quo_q": 69.11, "pt_quo_sed": "0302"}}]
    if params["TYPENAMES"] == "M2052:L6911" else
    [{"geometry": {"type": "Polygon", "coordinates": [[[8.9322, 44.4076, 69.11], [8.9324, 44.4076, 69.11], [8.9324, 44.4077, 69.11],
        [8.9322, 44.4076, 69.11]]]}}, {"geometry": {"type": "Polygon", "coordinates": [[[8.95, 44.42, 50], [8.951, 44.42, 50],
        [8.951, 44.421, 50], [8.95, 44.42, 50]]]}}])
assert [b["height"] for b in liguria((44.40, 8.93, 44.41, 8.94))] == [51.0, 15.0]
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
assert [b["height"] for b in floors((0, 0, 1, 1))] == [12.0, 15.0]
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
# Each held to its boundary: York University (Toronto) and Brampton sit in Vaughan's box, St. Albert and
# Sherwood Park in Edmonton's.
assert at(43.8540, -79.5084) == "Vaughan" and at(43.7970, -79.5290) == "Vaughan"
assert at(43.7735, -79.5019) == "OSM" and at(43.7600, -79.6900) == "OSM"
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
print("ok")
