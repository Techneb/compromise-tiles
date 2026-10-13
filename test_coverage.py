"""python3 test_coverage.py — the coverage feed's reading of the previous feed, on a local HTTP server; no network."""
import http.server, json, os, tempfile, threading
from coverage import load_previous

class Store(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = {"/feed.json": b'{"cells": []}', "/challenge.json": b"<html>Just a moment...</html>"}.get(self.path)
        self.send_response(200 if body else 404); self.end_headers(); self.wfile.write(body or b"")
    def log_message(self, *a): pass

server = http.server.HTTPServer(("127.0.0.1", 0), Store)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_port}"

assert load_previous(url + "/feed.json") == {"cells": []}
assert load_previous(url + "/absent.json") == {}   # 404: no feed yet, start empty
try:  # a 200 that is not the feed (a challenge page, a cut body) must not reset every record (#76)
    load_previous(url + "/challenge.json"); raise AssertionError("an unreadable feed from the store started empty")
except json.JSONDecodeError: pass
d = tempfile.mkdtemp()
assert load_previous(os.path.join(d, "none.json")) == {}   # a local file: absent or unreadable still starts empty
open(os.path.join(d, "bad.json"), "w").write("{")
assert load_previous(os.path.join(d, "bad.json")) == {}
server.shutdown()

# A départements entry's cell whose record holds no commune code (its commune call failed) is left out of the
# per-commune areas; it used to raise StopIteration and stop the feed job (#76).
import subprocess, sys
cities, prev, feed = (os.path.join(d, n) for n in ("cities.json", "prev.json", "feed.json"))
json.dump([{"city": "Petite couronne", "box": [48.80, 2.20, 48.82, 2.24], "departements": ["92"]}], open(cities, "w"))
json.dump({"dates": ["2026-10-05"], "communes": {"92012": "Boulogne-Billancourt"},
           "cells": [[24401, 1101, 0, 30, 30, -1, 0, ""], [24402, 1102, 0, 30, 30, -1, 0, "92012"]]}, open(prev, "w"))
subprocess.run([sys.executable, "coverage.py", "--out", os.path.join(d, "none"), "--cities", cities, "--previous", prev, "--write", feed],
               check=True, capture_output=True)
areas = json.load(open(feed))["areas"]
assert [(a["name"], a["tiled"]) for a in areas] == [("Boulogne-Billancourt", 1)], areas

# A record of a cell no area builds (outside its boundary, or of an area moved since) leaves the feed, and an area
# totals the cells it builds, a cell another area took first left to it, never its box (coherence review, 2026-10-11).
json.dump({"type": "Polygon", "coordinates": [[[2.20, 48.80], [2.21, 48.80], [2.21, 48.81], [2.20, 48.81], [2.20, 48.80]]]},
          open(os.path.join(d, "west.geojson"), "w"))
json.dump([{"city": "West", "box": [48.80, 2.20, 48.81, 2.22], "boundary": "west.geojson"},
           {"city": "Overlap", "box": [48.80, 2.20, 48.81, 2.22]}], open(cities, "w"))
json.dump({"dates": ["2026-10-05"], "communes": {},
           "cells": [[24401, 1101, 0, 30, 30, -1, 0, ""],     # inside West's boundary: West's
                     [24401, 1107, 0, 30, 30, -1, 0, ""],     # past it, in both boxes: Overlap's
                     [25000, 1500, 0, 30, 30, -1, 0, ""]],    # no area builds it
           "venueCells": [[2440, 110, 12, 0, 0], [2500, 150, 12, 0, 0]]}, open(prev, "w"))
subprocess.run([sys.executable, "coverage.py", "--out", os.path.join(d, "none"), "--cities", cities, "--previous", prev, "--write", feed],
               check=True, capture_output=True)
f = json.load(open(feed))
assert [c[:2] for c in f["cells"]] == [[24401, 1101], [24401, 1107]], f["cells"]
assert [v[:2] for v in f["venueCells"]] == [[2440, 110]], f["venueCells"]
assert [(a["city"], a["tiled"], a["passing"]) for a in f["areas"]] == [("West", 1, 1), ("Overlap", 1, 1)], f["areas"]

# A cell the first area's box holds but whose commune is a later area's is that area's (Boulogne in Paris's box).
json.dump([{"city": "Paris", "box": [48.80, 2.20, 48.82, 2.24]},
           {"city": "Petite couronne", "box": [48.80, 2.20, 48.82, 2.24], "departements": ["92"]}], open(cities, "w"))
json.dump({"dates": [], "communes": {"75056": "Paris", "92012": "Boulogne-Billancourt"},
           "cells": [[24401, 1101, 0, 30, 30, -1, 0, "75056"], [24402, 1102, 0, 30, 30, -1, 0, "92012"]]}, open(prev, "w"))
subprocess.run([sys.executable, "coverage.py", "--out", os.path.join(d, "none"), "--cities", cities, "--previous", prev, "--write", feed],
               check=True, capture_output=True)
areas = json.load(open(feed))["areas"]
assert [(a["name"], a["tiled"]) for a in areas] == [("Paris", 1), ("Boulogne-Billancourt", 1)], areas

# transit/: lines per city and mode, a line counted once however many cells it is written in; a fragment carries it,
# and the feed keeps the previous run's entry of a city this run did not rebuild.
json.dump([{"city": "Paris", "box": [48.80, 2.20, 48.90, 2.48]}, {"city": "Montréal", "box": [45.41, -73.95, 45.70, -73.47]}], open(cities, "w"))
os.makedirs(os.path.join(d, "out", "transit"))
m1 = {"mode": "subway", "ref": "1", "stops": ["A", "B"], "points": [], "shape": []}
for k, lines in {"1222,57": [m1], "1222,58": [m1, {**m1, "mode": "tram", "ref": "T3a"}],
                 "1135,-1846": [{**m1, "ref": "2"}], "0,0": [m1]}.items():   # Montréal's cell (negative key); 0,0 no city's
    json.dump(lines, open(os.path.join(d, "out", "transit", k + ".json"), "w"))
frag = os.path.join(d, "frag", "fragment.json"); os.makedirs(os.path.dirname(frag))
json.dump({"dates": [], "cells": [], "transit": {"London": {"modes": {"subway": 11}, "lines": 11, "tiles": 40, "bytes": 1, "built": "2026-10-01"},
                                                  "Paris": {"modes": {}, "lines": 0, "tiles": 0, "bytes": 0, "built": "2026-10-01"}}}, open(prev, "w"))
subprocess.run([sys.executable, "coverage.py", "--out", os.path.join(d, "out"), "--cities", cities, "--previous", prev, "--fragment", frag],
               check=True, capture_output=True)
subprocess.run([sys.executable, "coverage.py", "--fragments", os.path.dirname(frag), "--cities", cities, "--previous", prev, "--write", feed],
               check=True, capture_output=True)
t = json.load(open(feed))["transit"]
assert sorted(t) == ["London", "Montréal", "Paris"], t
assert t["London"]["lines"] == 11 and t["London"]["built"] == "2026-10-01", t
assert (t["Paris"]["modes"], t["Paris"]["lines"], t["Paris"]["tiles"]) == ({"subway": 1, "tram": 1}, 2, 2), t
assert (t["Montréal"]["modes"], t["Montréal"]["tiles"]) == ({"subway": 1}, 1), t
assert t["Paris"]["bytes"] == sum(os.path.getsize(os.path.join(d, "out", "transit", k + ".json")) for k in ("1222,57", "1222,58"))
print("ok")
