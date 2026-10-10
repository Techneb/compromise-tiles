"""python3 test_changed.py — the upload plan on made-up files and a made-up listing, no bucket needed."""
import hashlib, json, os, subprocess, sys, tempfile
from changed import etags, local, plan, state, summary

h = lambda b: hashlib.md5(b).hexdigest()
listing = f"""tiles/venues/1,1.json\t"{h(b'same')}"
tiles/venues/1,2.json\t"{h(b'old')}"
tiles/terraces-v2/5,5.json\t"{h(b'big')}-3"
None
""".splitlines(True)
remote = etags(listing)
assert remote["tiles/venues/1,1.json"] == h(b"same") and len(remote) == 3, remote

assert state(h(b"x"), None) == "new"
assert state(h(b"x"), h(b"x")) == "unchanged"
assert state(h(b"x"), h(b"y")) == "changed"
assert state(h(b"x"), h(b"x") + "-2") == "changed"  # multipart: not an MD5

d = tempfile.mkdtemp()
for rel, body in {"venues/1,1.json": b"same", "venues/1,2.json": b"new body", "venues/9,9.json": b"x",
                  "terraces-v2/5,5.json": b"big"}.items():
    os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
    open(os.path.join(d, rel), "wb").write(body)
files = sorted(local(d))
assert files[-1][0] == "tiles/venues/9,9.json" and files[-1][2:] == (h(b"x"), 1), files
rows, totals = plan(files, remote)
states = {k: s for k, _, s in rows}
assert states == {"tiles/venues/1,1.json": "unchanged", "tiles/venues/1,2.json": "changed",
                  "tiles/venues/9,9.json": "new", "tiles/terraces-v2/5,5.json": "changed"}, states
assert totals["venues"] == {"new": [1, 1], "changed": [1, 8], "unchanged": [1, 4]}, totals
assert "| `venues` | 1 | 1 | 1 | 0 |" in summary(totals)

# Without --prune nothing is touched; with it only the unchanged file goes.
listing_file = os.path.join(tempfile.mkdtemp(), "remote.tsv")
open(listing_file, "w").writelines(listing)
run = lambda *a: subprocess.run([sys.executable, "changed.py", "--listing", listing_file, "--out", d, *a], check=True, capture_output=True)
run()
assert len(list(local(d))) == 4
run("--prune")
assert sorted(k for k, *_ in local(d)) == ["tiles/terraces-v2/5,5.json", "tiles/venues/1,2.json", "tiles/venues/9,9.json"]
# No listing: everything new.
_, t = plan(local(d), {})
assert t["venues"]["new"][0] == 2
# --stale (#76): the store's numbers/ and boundaries/ keys this run emptied, in the city's own cells only.
from changed import stale
d = tempfile.mkdtemp()
cities = [{"city": "A", "box": [10.0, 10.0, 10.1, 10.1], "streets": True},
          {"city": "D", "box": [10.09, 10.09, 10.1, 10.1], "streets": True},   # a flagged neighbour: its cells are left alone
          {"city": "C", "box": [10.0, 10.0, 10.1, 10.1]}]                       # not flagged: builds no street layer
for rel, body in {"streets/5000,5000.json": b"[]", "streets/5001,5001.json": b"[]", "streets/5046,5046.json": b"[]",
                  "numbers/5001,5001.json": b"[1]", "boundaries/250,251.json": b"[2]"}.items():
    os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
    open(os.path.join(d, rel), "wb").write(body)
store = {f"tiles/{k}.json": h(b"[1]") for k in ("numbers/5000,5000", "numbers/5001,5001", "numbers/5002,5002", "numbers/5046,5046",
         "boundaries/250,250", "boundaries/250,251", "boundaries/251,251", "boundaries/252,252", "boundaries/300,300", "streets/5000,5000")}
# numbers/: 5000 emptied; 5001 still has a range; 5002's streets/ were not built (a failed cell); 5046 is D's too.
# boundaries/: 250,250 emptied; 250,251 rebuilt; 252,252 D's, 251,251 within a cell of it; 300,300 outside A.
assert stale(store, d, cities, "A") == ["tiles/boundaries/250,250.json", "tiles/numbers/5000,5000.json"], stale(store, d, cities, "A")
os.remove(os.path.join(d, "boundaries/250,251.json"))   # no outline written (a failed source): no boundaries/ key goes
assert stale(store, d, cities, "A") == ["tiles/numbers/5000,5000.json"]
open(os.path.join(d, "boundaries/250,251.json"), "wb").write(b"[2]")
cities_file, stale_file = os.path.join(d, "cities.json"), os.path.join(tempfile.mkdtemp(), "stale.txt")
json.dump(cities, open(cities_file, "w"))
open(listing_file, "w").writelines(f'{k}\t"{v}"\n' for k, v in store.items())
# With --prune, the unchanged numbers/5001 file is pruned after the stale keys are found, so it is not one of them.
out = run("--prune", "--cities", cities_file, "--city", "A", "--stale", stale_file).stdout.decode()
assert open(stale_file).read() == "tiles/boundaries/250,250.json\ntiles/numbers/5000,5000.json\n", open(stale_file).read()
assert "deleted from the store after the upload (not in a dry run): `numbers` 1, `boundaries` 1" in out, out
assert not os.path.exists(os.path.join(d, "numbers/5001,5001.json"))
os.remove(stale_file)
run("--cities", cities_file, "--city", "D", "--stale", stale_file)   # nothing emptied: no file, so the workflow deletes nothing
assert not os.path.exists(stale_file)
print("ok")
