"""python3 test_make_tiles.py — stdlib asserts, no network."""
from make_tiles import smallest_region, cells_in, clip

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
print("ok")
