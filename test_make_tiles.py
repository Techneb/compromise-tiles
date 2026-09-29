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
print("ok")
