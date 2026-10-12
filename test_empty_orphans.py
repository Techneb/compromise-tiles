"""python3 test_empty_orphans.py — which orphan tiles are empty, on a made-up listing, no bucket needed."""
import gzip, json, os, subprocess, sys, tempfile
from empty_orphans import EMPTY_MAX, classify, empty, nearest, summary, totals
from storage import areas, owners, parse

# The empty check: the empty array, as make_tiles.write() dumps it and tiles.yml gzips it (-9n), or plain.
d = tempfile.mkdtemp()
with open(os.path.join(d, "e.json"), "w") as f: json.dump([], f, separators=(",", ":"))
subprocess.run(["gzip", "-9n", os.path.join(d, "e.json")], check=True)
at_rest = open(os.path.join(d, "e.json.gz"), "rb").read()
assert len(at_rest) == 22 <= EMPTY_MAX, len(at_rest)    # the fixed size of an empty tile at rest
assert empty(at_rest) and empty(b"[]") and empty(gzip.compress(b"[]")) and empty(b" [ ]\n")
assert not empty(gzip.compress(b'[{"permits":null,"buildings":"OSM"}]'))   # a water cell's sources/ tile: data
assert not empty(b"[[2,1,2,3,4]]") and not empty(b"{}") and not empty(b"null") and not empty(b"")
assert not empty(b"\x1f\x8b\x08\x00broken") and not empty(b"[")             # unreadable: kept

# The orphans split: only the small ones are read, and only those holding [] go.
cities = os.path.join(d, "cities.json")
json.dump([{"city": "Paris", "box": [48.85, 2.33, 48.86, 2.34]},
           {"city": "Lisbon", "box": [38.70, -9.20, 38.80, -9.10]}], open(cities, "w"))
listing = """2026-09-28 03:12:45         22 tiles/terraces-v2/24427,1167.json
2026-09-28 03:12:45         22 tiles/terraces-v2/19300,-4700.json
2026-09-28 03:12:45         22 tiles/streets/19300,-4701.json
2026-09-28 03:12:45         40 tiles/sources/19300,-4700.json
2026-09-28 03:12:45       5000 tiles/buildings-v2/19300,-4700.json
2026-09-28 03:12:45         22 tiles/numbers/19300,-4702.json
2026-09-28 03:12:45         22 tiles/communes/19300,-4700.json
""".splitlines(True)
fetched = {"tiles/terraces-v2/19300,-4700.json": at_rest, "tiles/sources/19300,-4700.json": gzip.compress(b'[{"permits":null}]'),
           "tiles/terraces-v2/24427,1167.json": at_rest}
asked = []
def read(key):
    asked.append(key)
    return fetched.get(key)
objects = list(parse(listing))
built = areas(cities)
out, kept = classify(objects, owners(cities, built=built), read)
assert out == [("tiles/terraces-v2/19300,-4700.json", 22)], out           # Paris's own empty tile is no orphan
assert {k for k, _ in kept} == {"tiles/streets/19300,-4701.json",           # not fetched: kept
                                "tiles/sources/19300,-4700.json",           # small, with data
                                "tiles/buildings-v2/19300,-4700.json",      # large: never read
                                "tiles/numbers/19300,-4702.json"}, kept     # communes/: not a read layer, not an orphan
assert "tiles/buildings-v2/19300,-4700.json" not in asked and "tiles/terraces-v2/24427,1167.json" not in asked, asked

# Counted under the nearest area, per layer and per city.
assert nearest("tiles/terraces-v2/19300,-4700.json", built) == "Lisbon"     # 38.60 N, 9.40 W: the sea off Lisbon
assert nearest("tiles/venues/2442,116.json", built) == "Paris"              # venues/ on its 1/50° cells
t = {"empty": totals(out, built), "kept": totals(kept, built), "unfetched": 2}
assert t["empty"] == {"objects": 1, "bytes": 22, "layers": {"terraces-v2": {"objects": 1, "bytes": 22}},
                      "cities": {"Lisbon": {"objects": 1, "bytes": 22}}}, t
assert t["kept"]["layers"]["buildings-v2"] == {"objects": 1, "bytes": 5000} and t["kept"]["objects"] == 4
md = summary(t)
assert "**1 empty tiles**" in md and "| terraces-v2 | 1 | 22 |" in md and "| Lisbon | 1 | 22 |" in md, md

# The two steps end to end, as the workflow runs them.
with open(os.path.join(d, "listing.txt"), "w") as f: f.writelines(listing)
here = os.path.dirname(os.path.abspath(__file__))
run = lambda *a: subprocess.run([sys.executable, os.path.join(here, "empty_orphans.py"), *a, os.path.join(d, "listing.txt"),
                                 "--cities", cities], cwd=d, check=True, capture_output=True)
run("select")
assert open(os.path.join(d, "candidates.txt")).read().split() == [
    "tiles/terraces-v2/19300,-4700.json", "tiles/streets/19300,-4701.json", "tiles/sources/19300,-4700.json",
    "tiles/numbers/19300,-4702.json"]
for key, raw in fetched.items():
    os.makedirs(os.path.dirname(os.path.join(d, "fetched", key)), exist_ok=True)
    open(os.path.join(d, "fetched", key), "wb").write(raw)
run("report")
assert open(os.path.join(d, "delete.txt")).read() == "tiles/terraces-v2/19300,-4700.json\n"
assert json.load(open(os.path.join(d, "empty-orphans.json")))["unfetched"] == 2
print("ok")
