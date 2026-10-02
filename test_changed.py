"""python3 test_changed.py — the upload plan on made-up files and a made-up listing, no bucket needed."""
import hashlib, os, subprocess, sys, tempfile
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
print("ok")
