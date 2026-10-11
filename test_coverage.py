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
print("ok")
