"""python3 test_combine.py — combine rows (Bratislava, Oakland) on samples of the real feeds; no network.

fixtures/<city>.json: OpenStreetMap's buildings (osm_buildings(rect, tagged=True)) and the city layer's raw
features, fetched 2026-10-02 in three small boxes each: Bratislava's Old Town Hall, the Manderla block,
Nivy Tower; Oakland's City Hall, the Laconia Apartments, the Ordway Building."""
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
print("ok")
