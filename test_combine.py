"""python3 test_combine.py — combine rows (Bratislava, Oakland, London) on samples of the real feeds; no network.

fixtures/<city>.json: OpenStreetMap's buildings (osm_buildings(rect, tagged=True)) and the city layer's raw
features, fetched 2026-10-02 in three small boxes each: Bratislava's Old Town Hall, the Manderla block,
Nivy Tower; Oakland's City Hall, the Laconia Apartments, the Ordway Building. fixtures/london.json: OSM's buildings
in one 64 m lidar chunk of a Walthamstow terrace, beside the WCS's answers for it and for 30 St Mary Axe."""
import json, os, tempfile
import make_tiles as mt
from make_tiles import cells_in, path, read

def fixture(city):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", city + ".json")) as f: return json.load(f)

def centre(b): return (sum(p["latitude"] for p in b["outline"]) / len(b["outline"]),
                       sum(p["longitude"] for p in b["outline"]) / len(b["outline"]))

def by_place(buildings, lat, lon):
    """The building whose centre is nearest the point."""
    return min(buildings, key=lambda b: mt.metres(lat, lon, *centre(b)))

def run(city, data):
    """The row's own fetch over the fixture's features, combined as FETCH_BUILDINGS combines it."""
    mt.arcgis = mt.socrata = lambda *a, **k: data["city"]
    mt.osm_buildings = lambda rect, tagged=False: data["osm"]
    return mt.FETCH_BUILDINGS[city]((0, 0, 1, 1))

# Bratislava (Vyska, metres): the Manderla block, residential, has no OSM height and takes the city's;
# Nivy Tower is not in the residential layer and keeps OSM's 125 m; Old Town Hall, in neither height
# source, keeps OSM's 15 m; a building with only levels gives way to the city's metres.
bratislava = run("Bratislava", fixture("bratislava"))
assert len(bratislava) == len(fixture("bratislava")["osm"])   # OSM's footprints, no city one added
manderla = by_place(bratislava, 48.14451, 17.11222)
assert manderla["height"] == 47.7 and manderla["city"], manderla
nivy = by_place(bratislava, 48.14620, 17.12975)
assert nivy["height"] == 125.0 and "city" not in nivy
town_hall = by_place(bratislava, 48.14370, 17.10880)
assert town_hall["height"] >= 6 and "city" not in town_hall
levels = [{"outline": manderla["outline"], "height": 21.0, "tagged": "levels"}]
assert mt.combined(levels, [manderla], metres=True)[0]["height"] == 47.7
assert mt.combined(levels, [manderla], metres=False)[0]["height"] == 21.0

# Oakland (nostory × 3 m): the Laconia Apartments take the city's 4 floors; the Ordway Building is in the
# layer (28 floors, 84 m) but keeps OSM's height tag, 123 m; City Hall is tagged 98 m.
oakland = run("Oakland", fixture("oakland"))
laconia = by_place(oakland, 37.80493, -122.26610)
assert laconia["height"] == 12.0 and laconia["city"], laconia
ordway = by_place(oakland, 37.80995, -122.26414)
assert ordway["height"] == 123.0 and "city" not in ordway
assert by_place(oakland, 37.80538, -122.27259)["height"] == 98.0
# The layer's 20-floor houses are left out; a footprint without a floor count is too (OSM's height stays).
house = {"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}}
mt.socrata = lambda *a, **k: [dict(house, properties={"nostory": "20.0", "shape_area": "2182.48"}),
                              dict(house, properties={"nostory": "20.0", "shape_area": "17415.88"}),
                              dict(house, properties={"nostory": "", "shape_area": "900"})]
assert [b["height"] for b in mt.oakland((0, 0, 1, 1))] == [60.0]

# Each footprint must hold the other's centre: a small OSM annex inside a city office block's outline
# keeps its own height; an OSM outline drawn round two city buildings takes the one under its centre.
square = lambda s, w, n, e: [{"latitude": a / 1e4, "longitude": o / 1e4} for a, o in ((s, w), (s, e), (n, e), (n, w))]  # ~10 m
annex, office = {"outline": square(0, 0, 1, 1), "height": 15.0}, {"outline": square(0, 0, 4, 4), "height": 60.0}
assert mt.combined([annex], [office]) == [annex]
block = {"outline": square(0, 0, 1, 2), "height": 15.0}
halves = [{"outline": square(0, 0, 1, 1.2), "height": 30.0}, {"outline": square(0, 1.2, 1, 2), "height": 9.0}]
assert mt.combined([block], halves)[0]["height"] == 30.0

# do_buildings: a cell the city gave a height to is credited "<city>+OSM", one it gave none "OSM";
# the tile holds only outline and height, as every other row's.
run("Bratislava", fixture("bratislava"))   # the fetches answer Bratislava's sample again
mt.near_buildings = lambda cell, items: [b for b in items if mt.metres(cell.lat, cell.lon, *centre(b)) < 300]
mt.STORE = None
with tempfile.TemporaryDirectory() as out:
    at_manderla, at_nivy = cells_in(48.1445, 17.1122, 48.1446, 17.1123)[0], cells_in(48.1462, 17.1297, 48.1463, 17.1298)[0]
    assert mt.building_source(at_manderla) == "Bratislava" == mt.building_source(at_nivy)
    mt.do_buildings([at_manderla, at_nivy], out, failures := [])
    assert failures == []
    assert read(path(out, "sources", at_manderla)) == [{"buildings": "Bratislava+OSM"}]
    assert read(path(out, "sources", at_nivy)) == [{"buildings": "OSM"}]
    tile = read(path(out, "buildings", at_manderla))
    assert all(list(b) == ["outline", "height"] for b in tile) and 47.7 in [b["height"] for b in tile]
# London (the Environment Agency's lidar on OSM's footprints, chunks cut to 64 m to keep the fixtures small): the
# Walthamstow terrace's 19 houses, untagged so 15 m in OSM, take 8.2 to 9.1 m from one DSM and one DTM request.
asked = []
def lidar_tiff(kind, e0, n0, e1, n1):
    asked.append((kind, e0, n0, e1, n1))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", f"london-{e0}-{n0}-{kind}.tif"), "rb") as f:
        return mt.geotiff(f.read())
mt.LIDAR_CHUNK, mt.lidar_tiff = 64, lidar_tiff
london = fixture("london")
terrace = run("London", london)
assert sorted(b["height"] for b in terrace) == [8.2, 8.3, 8.3, 8.4, 8.5, 8.5, 8.6, 8.6, 8.7, 8.7, 8.8, 8.8, 8.8, 8.8, 8.9, 8.9,
                                                9.0, 9.0, 9.1] and all(b["city"] for b in terrace)
assert sorted(asked) == [("dsm", 537280, 188864, 537344, 188928), ("dtm", 537280, 188864, 537344, 188928)]  # once a run
# A height tag stays; building:levels gives way to the lidar's metres.
by_height = dict(london["osm"][0], height=12.0, tagged="height")
by_levels = dict(london["osm"][1], height=6.0, tagged="levels")
mt.osm_buildings = lambda rect, tagged=False: [by_height, by_levels]
assert [b["height"] for b in mt.FETCH_BUILDINGS["London"]((0, 0, 1, 1))] == [12.0, 8.4]
# 30 St Mary Axe: dark glass the composite filled from the DTM. A footprint inside the void reads no height (OSM's
# stays), though the chunk holds the tower's 180.6 m top.
inside = [mt.vertex(51.51468 + a, -0.08045 + o) for a, o in ((-7e-5, -1e-4), (-7e-5, 1e-4), (7e-5, 1e-4), (7e-5, -1e-4))]
assert mt.lidar_height(inside) is None and max(mt.lidar_chunk(533248, 181248)) == 1806
# The same outline on both sides matches whatever its shape: an L whose centre lies outside it.
ell = {"outline": [{"latitude": a / 1e4, "longitude": o / 1e4} for a, o in ((0, 0), (0, 3), (1, 3), (1, 1), (3, 1), (3, 0))],
       "height": 15.0}
assert not mt.contains({"type": "Polygon", "coordinates": [[[p["longitude"], p["latitude"]] for p in ell["outline"]]]}, *centre(ell))
assert mt.combined([ell], [dict(ell, height=30.0)])[0]["height"] == 30.0
# Held to Greater London's boundary: Croydon and the Thames at Westminster Bridge in, Watford (inside the box) out.
at = lambda lat, lon: mt.building_source(cells_in(lat, lon, lat, lon)[0])
assert at(51.3762, -0.0982) == "London" == at(51.5008, -0.1218) and at(51.6565, -0.3960) == "OSM"
# British National Grid, stdlib: the Shard within 0.4 m of OSTN15's 532901.41 E, 180131.53 N.
east, north = mt.bng(51.5045, -0.0865)
assert abs(east - 532901.41) < 0.4 and abs(north - 180131.53) < 0.4
# The five other lidar cities (2026-10-04), each the same fetch and combine as London's, held to its Local Authority
# District: the centre in; a tiled cell outside the district (Bristol's box reaches North Somerset at Long Ashton,
# Liverpool's the Mersey off Garston, Sheffield's Rotherham's edge) stays OSM, and Solihull and Bradford, inside
# the Birmingham and Leeds row boxes, too.
for city, lat, lon in (("Birmingham", 52.4797, -1.9027), ("Bristol", 51.4545, -2.5879), ("Leeds", 53.7997, -1.5492),
                       ("Liverpool", 53.4084, -2.9916), ("Sheffield", 53.3811, -1.4701)):
    assert at(lat, lon) == city, (city, at(lat, lon))
    row = next(b for b in mt.CITY_BUILDINGS if b["city"] == city)
    assert row["fetch"] is mt.lidar and row["combine"] == "metres" and city in mt.COMBINE
assert at(51.419, -2.655) == "OSM" and at(53.311, -3.019) == "OSM" and at(53.335, -1.445) == "OSM"
assert at(52.4130, -1.7780) == "OSM" and at(53.7940, -1.7520) == "OSM"
# Each city's centre within 0.4 m of OSTN15 (PROJ's grid, 2026-10-04): the Helmert's own offset there reaches 2 m north.
for lat, lon, e, n in ((52.4797, -1.9027, 406704.95, 286866.63), (51.4545, -2.5879, 359247.32, 172999.69), (53.7997, -1.5492, 429790.07, 433803.12),
                       (53.4084, -2.9916, 334179.37, 390632.47), (53.3811, -1.4701, 435346.13, 387267.63)):
    east, north = mt.bng(lat, lon)
    assert abs(east - e) < 0.4 and abs(north - n) < 0.4, (lat, lon, east - e, north - n)
print("ok")
