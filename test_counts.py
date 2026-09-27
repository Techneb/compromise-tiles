"""python3 test_counts.py — the disk count on made-up points, no extract needed."""
from counts import count, city_box, core, core_cells, dense, km2

box = city_box({"lat": 48.87, "lon": 2.33})
ten = [(48.87, 2.33, True, True)] * 10
going, terrace, named, outdoor, centres = count(ten, box)
assert going > 0 and terrace == going and named == 10 and outdoor == 10, (going, terrace)   # 10 of 10 outdoor: >= 30 %, >= 5
assert count(ten[:9], box)[0] == 0                                    # one short of the floor
assert count([(48.87, 2.33, True, False)] * 10, box)[1] == 0          # no outdoor seating, no terrace cell
assert count([(10.0, 10.0, True, True)] * 10, box)[0] == 0            # outside the box
cb, area = core(centres)                                              # the cells' box padded by 2 km
assert cb[0] < 48.87 - 0.017 and cb[2] > 48.87 + 0.017 and 14 < area < 26, (cb, area)
assert core([]) == (None, 0)
far = [(48.87, 2.33)] * 5 + [(48.95, 2.33)] * 50                      # a dense cluster 9 km north of the point
d = dense(far, 48.87, 2.33)
assert abs(km2(d) - 150) < 1 and d[0] <= 48.87 <= d[2] and d[0] <= 48.95 <= d[2], d   # holds both
d = dense([(48.87, 2.33)] * 5 + [(49.2, 2.33)] * 50, 48.87, 2.33)
assert d[0] <= 48.87 <= d[2] and not d[0] <= 49.2 <= d[2]             # out of reach: the point still held
# A landmark 5 km east: its cell joins the core with 6 named venues around it, not with 2.
mark = [(48.87, 2.40, "Museum")]
going, cells = core_cells(ten + [(48.87, 2.40, True, False)] * 6, mark, box)
assert list(cells.values()) == [["Museum"]] and core(going + [(48.87, 2.40)])[0][3] > 2.40 > core(going)[0][3], cells
assert core_cells(ten + [(48.87, 2.40, True, False)] * 2, mark, box)[1] == {}
assert core_cells(ten + [(48.87, 2.40, True, False)] * 6, [(10.0, 10.0, "Far")], box)[1] == {}   # outside the box
print("ok")
