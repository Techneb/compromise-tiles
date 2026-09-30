"""python3 test_counts.py — the disk count on made-up points, no extract needed."""
from counts import GAP, Cell, cluster, count, city_box, core, core_cells, dense, holds, is_whole, km2, merge_parts, part, same_city

box = city_box({"lat": 48.87, "lon": 2.33})
ten = [(48.87, 2.33, True, True)] * 10
going, terrace, named, outdoor, centres, _ = count(ten, box)
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
assert list(cells.values()) == [["Museum"]] and core([(Cell(*k).lat, Cell(*k).lon) for k in going] + [(48.87, 2.40)])[0][3] > 2.40 > core([(Cell(*k).lat, Cell(*k).lon) for k in going])[0][3], cells
assert core_cells(ten + [(48.87, 2.40, True, False)] * 2, mark, box)[1] == {}
assert core_cells(ten + [(48.87, 2.40, True, False)] * 6, [(10.0, 10.0, "Far")], box)[1] == {}   # outside the box
# The cluster around the point (cell keys, 200 m each; the point sits in cell (100, 100)).
assert GAP == 2
here = {(100, 100 + i) for i in range(5)}                             # five cells in a row from the point
suburb = {(140, 100 + i) for i in range(5)}                           # a second centre 40 cells (8 km) north
pt = Cell(100, 100)
got, _ = cluster(here | suburb, {}, pt.lat, pt.lon)
assert got == here, got                                               # the suburb stays out
got, _ = cluster(here | {(100, 106), (100, 107)}, {}, pt.lat, pt.lon)
assert (100, 107) in got                                              # one empty cell (100, 105) bridges
assert cluster(here | {(100, 107)}, {}, pt.lat, pt.lon)[0] == here   # two empty cells do not
got, _ = cluster(suburb, {}, pt.lat, pt.lon)
assert got == suburb                                                  # no cell at the point: the nearest cluster
marks = {(102, 104): ["Two away"], (104, 104): ["Past it"], (108, 100): ["Four past"], (100, 108): ["Four away"]}
_, joined = cluster(here, marks, pt.lat, pt.lon)
assert joined == {(102, 104), (104, 104)}, joined                     # two away joins, and the one beyond it; four away does not
assert cluster(set(), marks, pt.lat, pt.lon) == (set(), set())
# Which cities.json entries are the whole city (extent "municipality") and which a core ("tiled box").
assert is_whole({"city": "Paris", "district": "Intra-muros (inside the périphérique)"})
assert is_whole({"city": "Athens", "district": "Athens (whole municipality)", "boundary": "b.geojson"})
assert is_whole({"city": "Tel Aviv", "district": "Tel Aviv-Yafo (whole city)"})
assert is_whole({"city": "Oslo", "district": "Oslo"})
assert not is_whole({"city": "London", "district": "London (densest 150 km² of the core)"})
assert not is_whole({"city": "Zaragoza", "district": "Zaragoza (going-out core + 2 km)"})
assert not is_whole({"city": "Oslo", "district": "Oslo (centre, 5 km)"})
# --part: whole extract groups, each in exactly one share.
groups = {"a": [1, 2, 3], "b": [4], "c": [5, 6], "d": [7]}
shares = [dict(part(groups, f"{k}/3")) for k in (1, 2, 3)]
assert sorted(u for s in shares for u in s) == sorted(groups) and sum(len(s) for s in shares) == 4, shares
assert shares[0] == {"a": [1, 2, 3], "d": [7]} and shares[1] == {"c": [5, 6]}, shares   # biggest first, dealt round
assert dict(part(groups, "1/1")) == groups
# --merge: the parts' rows side by side, a city without a point named once.
import json, os, tempfile
d = tempfile.mkdtemp()
for i, (city, failed) in enumerate([("A", []), ("B", ["C: 404"])]):
    json.dump({"cities": [{"city": city, "goingOutCells": i}], "noPoint": ["Z, Nowhere"], "failed": failed}, open(os.path.join(d, f"{i}.json"), "w"))
rows, no_point, failed = merge_parts([os.path.join(d, f"{i}.json") for i in range(2)])
assert [r["city"] for r in rows] == ["A", "B"] and no_point == ["Z, Nowhere"] and failed == ["C: 404"], (rows, no_point, failed)
# Two cities of one name: London's box is not London, Canada's; a previous row matches by name, country and place.
london_box = ((51.417, -0.3219, 51.643, 0.0539), None, "tiled box")
assert holds(london_box, {"city": "London", "lat": 51.507, "lon": -0.128})
assert not holds(london_box, {"city": "London", "lat": 42.98, "lon": -81.25})
assert holds(london_box, {"city": "London", "box": [51.4, -0.3, 51.6, 0.0]}) and not holds(None, {"city": "London"})
uk, ca = {"city": "London", "country": "United Kingdom", "lat": 51.507, "lon": -0.128}, {"city": "London", "country": "Canada", "lat": 42.98, "lon": -81.25}
assert same_city(uk, dict(uk)) and not same_city(uk, ca) and not same_city(uk, dict(uk, lat=42.98, lon=-81.25))
assert same_city(uk, {"city": "London", "country": "United Kingdom"})   # a row published before rows carried a point
print("ok")
