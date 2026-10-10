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
print("ok")
