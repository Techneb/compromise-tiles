"""python3 test_stats.py — the store's numbers on a made-up bucket served on localhost, no R2 or GitHub needed."""
import datetime, json, os, tempfile, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from stats import Bucket, builds, cuts, list_all, sign, stats

# The signature: AWS's own S3 examples (GET Object with a Range, and ListObjects), same keys and time.
KEY, SECRET = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
when = datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc)
h = sign("GET", "https://examplebucket.s3.amazonaws.com/test.txt", KEY, SECRET, "us-east-1", when, {"Range": "bytes=0-9"})
assert h["authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"), h
h = sign("GET", "https://examplebucket.s3.amazonaws.com/?max-keys=2&prefix=J", KEY, SECRET, "us-east-1", when)
assert h["authorization"].endswith("Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"), h

# A bucket: tiles in rows of two cities and outside them, a key on a cut, root files; pages of 3 keys.
d = tempfile.mkdtemp()
cities_path = os.path.join(d, "cities.json")
cities = [{"city": "Paris", "box": [48.85, 2.33, 48.86, 2.34], "streets": True}, {"city": "Sydney", "box": [-33.9, 151.2, -33.85, 151.25]}]
json.dump(cities, open(cities_path, "w"))
keys = {"config.json": 50, "coverage.json": 70, "stats.json": 5,
        "tiles/buildings/24427,1167.json": 1000, "tiles/buildings/24428,1168.json": 1100, "tiles/buildings/24400": 3,
        "tiles/buildings/-16950,75600.json": 2000, "tiles/buildings/1,1.json": 7,
        "tiles/terraces-v2/24427,1167.json": 400, "tiles/venues/2442,116.json": 300, "tiles/streets/24427,1167.json": 90}
keys.update({f"tiles/sources/{24400 + i},1167.json": 10 for i in range(20)})


class S3(BaseHTTPRequestHandler):
    """ListObjectsV2 over `keys`: prefix, delimiter, start-after, continuation-token, max-keys."""
    def do_GET(self):
        assert self.headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=id/")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        assert urllib.parse.urlsplit(self.path).path == "/bucket" and q["list-type"] == "2"
        after, prefix, delim, n = q.get("continuation-token") or q.get("start-after", ""), q.get("prefix", ""), q.get("delimiter"), int(q["max-keys"])
        hits = [k for k in sorted(keys) if k > after and k.startswith(prefix)]
        common = sorted({prefix + k[len(prefix):].split(delim)[0] + delim for k in hits if delim and delim in k[len(prefix):]})
        hits = [k for k in hits if not (delim and delim in k[len(prefix):])]
        page, more = hits[:n], len(hits) > n
        body = ('<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                + "".join(f"<Contents><Key>{k}</Key><Size>{keys[k]}</Size></Contents>" for k in page)
                + "".join(f"<CommonPrefixes><Prefix>{p}</Prefix></CommonPrefixes>" for p in common)
                + f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>"
                + (f"<NextContinuationToken>{page[-1]}</NextContinuationToken>" if more else "") + "</ListBucketResult>").encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


server = HTTPServer(("127.0.0.1", 0), S3)
threading.Thread(target=server.serve_forever, daemon=True).start()
bucket = Bucket(f"http://127.0.0.1:{server.server_port}", "bucket", "id", "secret", page=3)

b = cuts(["buildings", "venues"], cities)
assert "tiles/buildings/24400" in b and "tiles/buildings/-17000" in b and "tiles/venues/2440" in b, b
objects = list_all(bucket, cities, workers=4)
assert sorted(objects) == sorted(keys.items()), sorted(set(keys.items()) ^ set(objects))   # each key once, the cut's own too

jobs = [{"name": "build (Paris)", "started_at": "2026-10-05T03:20:00Z", "completed_at": "2026-10-05T04:05:30Z", "conclusion": "success"},
        {"name": "build (New York)", "started_at": "2026-10-05T03:20:00Z", "completed_at": "2026-10-05T03:21:00Z", "conclusion": "failure"},
        {"name": "build (Rome)", "started_at": "2026-10-05T03:20:00Z", "completed_at": None, "conclusion": None},   # still running: kept as before
        {"name": "plan", "started_at": "2026-10-05T03:17:00Z", "completed_at": "2026-10-05T03:17:10Z", "conclusion": "success"}]
prev = {"Paris": {"seconds": 1, "result": "success", "date": "2026-09-28", "run": 1}, "Rome": {"seconds": 60, "result": "success", "date": "2026-09-28", "run": 1}}
bl = builds(jobs, 9, "2026-10-05", prev)
assert bl["Paris"] == {"seconds": 2730, "result": "success", "date": "2026-10-05", "run": 9}, bl
assert bl["New York"]["result"] == "failure" and bl["Rome"] == prev["Rome"] and "plan" not in bl, bl

s = stats(objects, cities, cities_path, bl, 9, "2026-10-05T04:10:00Z")
assert (s["bytes"], s["objects"]) == (sum(keys.values()), len(keys)) and s["freeBytes"] == 10 ** 10, s
assert s["layers"]["buildings"] == {"bytes": 4110, "objects": 5} and s["layers"]["sources"] == {"bytes": 200, "objects": 20}, s["layers"]
assert s["other"] == {"bytes": 125, "objects": 3}, s["other"]
assert set(s["streets"]) == {"Paris"} and s["streets"]["Paris"]["buildings"] == {"bytes": 2100, "objects": 2}, s["streets"]   # Paris's two cells, not Sydney's
assert s["streets"]["Paris"]["venues"]["bytes"] == 300 and s["streets"]["Paris"]["streets"]["bytes"] == 90, s["streets"]
assert stats(objects, [{"city": "Paris", "box": [48.85, 2.33, 48.86, 2.34]}], cities_path, {}, 9, "")["streets"] == {}   # no flag yet
json.dumps(s)
server.shutdown()
print("ok")
