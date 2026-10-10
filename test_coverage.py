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
print("ok")
