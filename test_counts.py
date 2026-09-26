"""python3 test_counts.py — the disk count on made-up points, no extract needed."""
from counts import count, city_box

box = city_box({"lat": 48.87, "lon": 2.33})
ten = [(48.87, 2.33, True, True)] * 10
going, terrace, named, outdoor = count(ten, box)
assert going > 0 and terrace == 0 and named == 10 and outdoor == 10, (going, terrace)
assert count(ten[:9], box)[0] == 0                                    # one short of the floor
assert count([(48.87, 2.33, True, True)] * 20, box)[1] == going       # every such cell has terraces
assert count([(10.0, 10.0, True, True)] * 10, box)[0] == 0            # outside the box
print("ok")
