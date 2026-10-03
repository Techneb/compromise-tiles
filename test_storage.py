"""python3 test_storage.py — the store's tally on a made-up listing, no bucket needed."""
import json, os, tempfile
from storage import NO_AREA, orphans, owners, parse, summary, tally, unread

d = tempfile.mkdtemp()
cities = os.path.join(d, "cities.json")
json.dump([{"city": "Paris", "box": [48.85, 2.33, 48.86, 2.34]}], open(cities, "w"))
listing = """2026-09-28 03:12:45       1000 tiles/buildings/24427,1167.json
2026-09-28 03:12:45       2000 tiles/buildings/1,1.json
2026-09-28 03:12:45        300 tiles/venues/2442,116.json
2026-09-28 03:12:45        400 tiles/terraces/24427,1167.json
2026-09-28 03:12:45         10 tiles/communes/24427,1167.json
2026-09-28 03:12:45         50 config.json
2026-09-28 03:12:45         60 old.json
""".splitlines(True)
assert list(parse(listing))[0] == ("tiles/buildings/24427,1167.json", 1000)
t = tally(parse(listing), owners(cities))
assert (t["bytes"], t["objects"]) == (3820, 7), t
assert t["prefixes"]["tiles/"] == {"bytes": 3710, "objects": 5} and t["prefixes"]["config.json"]["objects"] == 1
assert t["layers"]["buildings"] == {"bytes": 3000, "objects": 2}
rows = {(c["layer"], c["city"]): c["bytes"] for c in t["cities"]}
assert rows[("buildings", "Paris")] == 1000 and rows[("buildings", NO_AREA)] == 2000, rows   # a cell no area builds
assert rows[("venues", "Paris")] == 300, rows                                                # venues by their 1/50° cell
flagged = {k for k, _ in unread(t)}
assert flagged == {"old.json", "tiles/terraces/", "tiles/communes/", "tiles/buildings/ outside every area"}, flagged
assert "| `buildings` | Paris |" in summary(t)
# Deletion candidates: read layers only, a cell no area builds (not the unread layers, deleted as prefixes).
assert orphans(parse(listing), owners(cities)) == ["tiles/buildings/1,1.json"]
print("ok")
