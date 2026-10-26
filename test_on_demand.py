"""python3 test_on_demand.py — the on-demand checks on the real cities.json and a temporary copy, no network."""
import json, os, shutil, tempfile
from on_demand import add, plan


def refused(*args):
    try: plan(*args)
    except SystemExit as x: return str(x).startswith("refused")
    return False


assert refused("Lyon, France", "FR", "45.76,4.83", "cities.json")      # a comma would split tiles.yml's `only`
assert refused("Lyon", "fr", "45.76,4.83", "cities.json")
assert refused("Lyon", "FR", "45.765,4.83", "cities.json")              # finer than 0.01°
assert refused("Lyon", "FR", "", "cities.json")
assert refused("$(id)", "FR", "45.76,4.83", "cities.json")
entry, why = plan("Paris", "FR", "48.87,2.33", "cities.json")           # inside Paris's area
assert entry is None and "already" in why, why
# Mid-Atlantic, nowhere tiled: one cell gives a box of about 3 km a side; a cell 50 km off is dropped.
entry, why = plan("Testville", "PT", "40.00,-30.00 40.45,-30.00", "cities.json")
s, w, n, e = entry["box"]
assert entry["city"] == "Testville" and 0.02 < n - s < 0.03 and n < 40.1, entry
entry, _ = plan("Paris", "PT", "40.00,-30.00", "cities.json")           # name taken elsewhere: with its code
assert entry["city"] == "Paris (PT)", entry

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "cities.json")
    shutil.copy("cities.json", path)
    request = plan("Testville", "PT", "40.00,-30.00", "cities.json")[0]
    assert add(request, {"cities": [{"city": "Testville", "goingOutCells": 2}]}, path)[0] is False   # too thin
    assert add(request, {"cities": []}, path)[0] is False
    assert add(request, {"cities": [{"city": "Testville", "goingOutCells": 9}]}, path)[0] is True
    after = json.load(open(path))
    assert after[-1] == {"city": "Testville", "district": "Testville (centre)", "box": request["box"]}
    assert after[:-1] == json.load(open("cities.json"))
    assert add(request, {"cities": [{"city": "Testville", "goingOutCells": 9}]}, path)[0] is False   # once
print("ok")
