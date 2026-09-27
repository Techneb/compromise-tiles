"""python3 test_counts.py — the disk count on made-up points, no extract needed."""
from counts import count, city_box, core, dense, km2

box = city_box({"lat": 48.87, "lon": 2.33})
ten = [(48.87, 2.33, True, True)] * 10
going, terrace, named, outdoor, centres = count(ten, box)
assert going > 0 and terrace == going and named == 10 and outdoor == 10, (going, terrace)   # 10 of 10 outdoor: >= 30 %, >= 5
assert count(ten[:9], box)[0] == 0                                    # one short of the floor
assert count([(48.87, 2.33, True, False)] * 10, box)[1] == 0          # no outdoor seating, no terrace cell
assert count([(10.0, 10.0, True, True)] * 10, box)[0] == 0            # outside the box
cb, area = core(centres)                                              # the cells' box padded by 1 km
assert cb[0] < 48.87 - 0.008 and cb[2] > 48.87 + 0.008 and 4 < area < 12, (cb, area)
assert core([]) == (None, 0)
far = [(48.87, 2.33)] * 5 + [(48.95, 2.33)] * 50                      # a dense cluster 9 km north of the point
d = dense(far, 48.87, 2.33)
assert abs(km2(d) - 150) < 1 and d[0] <= 48.87 <= d[2] and d[0] <= 48.95 <= d[2], d   # holds both
d = dense([(48.87, 2.33)] * 5 + [(49.2, 2.33)] * 50, 48.87, 2.33)
assert d[0] <= 48.87 <= d[2] and not d[0] <= 49.2 <= d[2]             # out of reach: the point still held
print("ok")
