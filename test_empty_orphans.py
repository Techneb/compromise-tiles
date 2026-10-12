"""python3 test_empty_orphans.py — which orphan tiles are empty, on a made-up listing, no bucket needed."""
import gzip, json, os, random, subprocess, sys, tempfile
from empty_orphans import EMPTY_MAX, EMPTY_SIZE, OTHER_LIMIT, SAMPLE, check, classify, empty, nearest, pick, summary, totals
from storage import areas, owners, parse

# The empty check: the empty array, as make_tiles.write() dumps it and tiles.yml gzips it (-9n), or plain.
d = tempfile.mkdtemp()
with open(os.path.join(d, "e.json"), "w") as f: json.dump([], f, separators=(",", ":"))
subprocess.run(["gzip", "-9n", os.path.join(d, "e.json")], check=True)
at_rest = open(os.path.join(d, "e.json.gz"), "rb").read()
assert len(at_rest) == EMPTY_SIZE == 22 <= EMPTY_MAX, len(at_rest)    # the fixed size of an empty tile at rest
assert empty(at_rest) and empty(b"[]") and empty(gzip.compress(b"[]")) and empty(b" [ ]\n")
assert not empty(gzip.compress(b'[{"permits":null,"buildings":"OSM"}]'))   # a water cell's sources/ tile: data
assert not empty(b"[[2,1,2,3,4]]") and not empty(b"{}") and not empty(b"null") and not empty(b"")
assert not empty(b"\x1f\x8b\x08\x00broken") and not empty(b"[")             # unreadable: kept

# The orphans split: 22 bytes is empty by the listing alone, another small size only when read and [].
cities = os.path.join(d, "cities.json")
json.dump([{"city": "Paris", "box": [48.85, 2.33, 48.86, 2.34]},
           {"city": "Lisbon", "box": [38.70, -9.20, 38.80, -9.10]}], open(cities, "w"))
listing = """2026-09-28 03:12:45         22 tiles/terraces-v2/24427,1167.json
2026-09-28 03:12:45         22 tiles/terraces-v2/19300,-4700.json
2026-09-28 03:12:45         22 tiles/streets/19300,-4701.json
2026-09-28 03:12:45         40 tiles/sources/19300,-4700.json
2026-09-28 03:12:45         30 tiles/streets/19300,-4703.json
2026-09-28 03:12:45         35 tiles/streets/19300,-4704.json
2026-09-28 03:12:45       5000 tiles/buildings-v2/19300,-4700.json
2026-09-28 03:12:45         22 tiles/communes/19300,-4700.json
""".splitlines(True)
fetched = {"tiles/terraces-v2/19300,-4700.json": at_rest, "tiles/streets/19300,-4701.json": at_rest,
           "tiles/sources/19300,-4700.json": gzip.compress(b'[{"permits":null}]'),
           "tiles/streets/19300,-4703.json": gzip.compress(b"[]"), "tiles/terraces-v2/24427,1167.json": at_rest}
asked = []
def read(key):
    asked.append(key)
    return fetched.get(key)
objects = list(parse(listing))
built = areas(cities)
cells = owners(cities, built=built)
out, kept, unread = classify(objects, cells, read)
assert out == [("tiles/terraces-v2/19300,-4700.json", 22), ("tiles/streets/19300,-4701.json", 22),   # by size
               ("tiles/streets/19300,-4703.json", 30)], out                  # read: [] gzipped with a header
assert {k for k, _ in kept} == {"tiles/sources/19300,-4700.json",           # small, read, with data
                                "tiles/streets/19300,-4704.json",           # small, not fetched: kept unread
                                "tiles/buildings-v2/19300,-4700.json"}, kept  # large: never read
assert unread == 1, unread                                                  # communes/: not a read layer, not an orphan
assert set(asked) == {"tiles/sources/19300,-4700.json", "tiles/streets/19300,-4703.json",
                      "tiles/streets/19300,-4704.json"}, asked              # the 22-byte ones are never read here

# The sample: SAMPLE of the 22-byte orphans at random, all of them when fewer; any not [] or not fetched fails.
sample, other = pick(objects, cells, random.Random(1))
assert sorted(sample) == ["tiles/streets/19300,-4701.json", "tiles/terraces-v2/19300,-4700.json"], sample
assert other == ["tiles/sources/19300,-4700.json", "tiles/streets/19300,-4703.json", "tiles/streets/19300,-4704.json"], other
assert check(sample, fetched.get) == []
assert check(sample + ["tiles/sources/19300,-4700.json", "tiles/x/1,1.json"], fetched.get) == [
    "tiles/sources/19300,-4700.json", "tiles/x/1,1.json"]                    # data, and not fetched
many = [f"2026-09-28 03:12:45         {s} tiles/streets/{i},{-5000 - s}.json\n" for i in range(OTHER_LIMIT) for s in (22, 30)]
sample, other = pick(list(parse(many)), cells, random.Random(1))
assert len(sample) == SAMPLE == len(set(sample)) and all(k in {l.split()[3] for l in many if " 22 " in l} for k in sample)
assert other == []                                                          # OTHER_LIMIT of another size: none read
asked.clear()
out_many, kept_many, unread_many = classify(list(parse(many)), cells, read)
assert len(out_many) == OTHER_LIMIT and len(kept_many) == unread_many == OTHER_LIMIT and asked == []
_, other = pick(list(parse(many[:-2])), cells, random.Random(1))
assert len(other) == OTHER_LIMIT - 1                                        # one fewer: all read

# Counted under the nearest area, per layer and per city.
assert nearest("tiles/terraces-v2/19300,-4700.json", built) == "Lisbon"     # 38.60 N, 9.40 W: the sea off Lisbon
assert nearest("tiles/venues/2442,116.json", built) == "Paris"              # venues/ on its 1/50° cells
t = {"empty": totals(out, built), "kept": totals(kept, built), "unread": 1, "other": 3, "sample": {"checked": 2, "failed": []}}
assert t["empty"] == {"objects": 3, "bytes": 74, "layers": {"streets": {"objects": 2, "bytes": 52},
                      "terraces-v2": {"objects": 1, "bytes": 22}}, "cities": {"Lisbon": {"objects": 3, "bytes": 74}}}, t
assert t["kept"]["layers"]["buildings-v2"] == {"objects": 1, "bytes": 5000} and t["kept"]["objects"] == 3
md = summary(t)
assert "**3 empty tiles**" in md and "| terraces-v2 | 1 | 22 |" in md and "| Lisbon | 3 | 74 |" in md, md
assert "Sample: 2 of 2" in md and "failed" not in md, md
assert "**Sample check failed**: 1 of 2" in summary({**t, "sample": {"checked": 2, "failed": ["tiles/a/1,1.json"]}})

# The two steps end to end, as the workflow runs them.
with open(os.path.join(d, "listing.txt"), "w") as f: f.writelines(listing)
here = os.path.dirname(os.path.abspath(__file__))
run = lambda *a: subprocess.run([sys.executable, os.path.join(here, "empty_orphans.py"), *a, os.path.join(d, "listing.txt"),
                                 "--cities", cities], cwd=d, capture_output=True)
assert run("select").returncode == 0
assert sorted(open(os.path.join(d, "sample.txt")).read().split()) == [
    "tiles/streets/19300,-4701.json", "tiles/terraces-v2/19300,-4700.json"]
assert open(os.path.join(d, "candidates.txt")).read().split() == [
    "tiles/sources/19300,-4700.json", "tiles/streets/19300,-4703.json", "tiles/streets/19300,-4704.json"]
for key, raw in fetched.items():
    os.makedirs(os.path.dirname(os.path.join(d, "fetched", key)), exist_ok=True)
    open(os.path.join(d, "fetched", key), "wb").write(raw)
assert run("report").returncode == 0
assert open(os.path.join(d, "delete.txt")).read().split() == [
    "tiles/terraces-v2/19300,-4700.json", "tiles/streets/19300,-4701.json", "tiles/streets/19300,-4703.json"]
assert json.load(open(os.path.join(d, "empty-orphans.json")))["unread"] == 1
# A sample tile with data fails the step and lists nothing for deletion.
open(os.path.join(d, "fetched", "tiles/streets/19300,-4701.json"), "wb").write(gzip.compress(b"[[1,2]]"))
r = run("report")
assert r.returncode != 0 and b"sample check failed" in r.stderr, r
assert open(os.path.join(d, "delete.txt")).read() == ""
assert "**Sample check failed**" in open(os.path.join(d, "empty-orphans.md")).read()
assert json.load(open(os.path.join(d, "empty-orphans.json")))["kept"]["objects"] == 6
print("ok")
