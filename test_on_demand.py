"""python3 test_on_demand.py — the on-demand checks on a fixed cities.json (fixtures/) and a temporary copy, no network."""
import json, os, shutil, tempfile
from on_demand import add, plan

# Toulouse's entry, its boundary cut to a square inside its box: the live cities.json changes (#184 added the towns below).
CITIES = "fixtures/on-demand-cities.json"


def refused(*args):
    try: plan(*args)
    except SystemExit as x: return str(x).startswith("refused")
    return False


assert refused("Lyon, France", "FR", "45.76,4.83", CITIES)               # a comma would split tiles.yml's `only`
assert refused("Lyon", "fr", "45.76,4.83", CITIES)
assert refused("Lyon", "FR", "45.765,4.83", CITIES)                       # finer than 0.01°
assert refused("Lyon", "FR", "", CITIES)
assert refused("$(id)", "FR", "45.76,4.83", CITIES)
entry, why = plan("Toulouse", "FR", "43.60,1.44", CITIES)        # inside Toulouse's boundary
assert entry is None and "already" in why, why
# Toulouse's box is wider than its commune: L'Union and Quint-Fonsegrives sit in the box, outside the boundary.
for town, cell in (("L'Union", "43.67,1.48"), ("Quint-Fonsegrives", "43.58,1.51")):
    entry, why = plan(town, "FR", cell, CITIES)
    assert entry and entry["city"] == town, why
# Mid-Atlantic, nowhere tiled: one cell gives a box of about 3 km a side; a cell 50 km off is dropped.
entry, why = plan("Testville", "PT", "40.00,-30.00 40.45,-30.00", CITIES)
s, w, n, e = entry["box"]
assert entry["city"] == "Testville" and 0.02 < n - s < 0.03 and n < 40.1, entry
entry, _ = plan("Toulouse", "PT", "40.00,-30.00", CITIES)        # name taken elsewhere: with its code
assert entry["city"] == "Toulouse (PT)", entry

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "cities.json")
    shutil.copy(CITIES, path)
    request = plan("Testville", "PT", "40.00,-30.00", CITIES)[0]
    assert add(request, {"cities": [{"city": "Testville", "goingOutCells": 2}]}, path)[0] is False   # too thin
    assert add(request, {"cities": []}, path)[0] is False
    assert add(request, {"cities": [{"city": "Testville", "goingOutCells": 9}]}, path)[0] is True
    after = json.load(open(path))
    assert after[-1] == {"city": "Testville", "district": "Testville (centre)", "box": request["box"]}
    assert after[:-1] == json.load(open(CITIES))
    assert add(request, {"cities": [{"city": "Testville", "goingOutCells": 9}]}, path)[0] is False   # once
print("ok")
