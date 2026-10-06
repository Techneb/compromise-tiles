#!/usr/bin/env python3
"""The tile store's numbers for the site's overview page, written by tiles.yml's feed job as stats.json.

  python3 stats.py --bucket "$R2_BUCKET" --endpoint "$ENDPOINT" --cities cities.json \
    --previous https://tiles.alephb.uk/stats.json --run "$GITHUB_RUN_ID" --repo "$GITHUB_REPOSITORY" --write stats.json

Lists the whole bucket over the S3 API (credentials from AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY,
read only). One listing page holds 1,000 keys and a page waits for the one before, so the 1.7 million
objects of 2026-10-03 took 18 minutes in one stream (storage.yml). Here the key space is cut into ranges
at the latitude rows the areas of cities.json build (a tile's key starts with its row) and the ranges are
listed in parallel: every key falls in exactly one range whatever the cuts, they only balance the work.

Build durations are read from the GitHub API, one entry per `build (<city>)` job of this run (GITHUB_TOKEN
when set); a city this run did not build keeps its entry from the previous stats.json.

Output:
  updated     ISO time of this run       run        the run's id
  freeBytes   R2's free storage (10 GB of 10^9 bytes, as R2 bills them)
  bytes, objects                         the whole bucket
  layers      {layer: {bytes, objects}}  under tiles/<layer>/
  other       {bytes, objects}           everything outside tiles/
  streets     {city: {layer: {bytes, objects}}}  the cells of the cities whose cities.json entry has "streets": true
  builds      {city: {seconds, result, date, run}}
"""
import argparse, datetime, hashlib, hmac, http.client, json, os, re, threading, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from make_tiles import COARSE, FINE
from storage import FREE_GB, LAYER_SCALE, TILE, owners, tally

ROWS = {FINE: 100, COARSE: 10}  # rows per range at each key scale: 0.2° of latitude either way
WORKERS = 64
AGENT = {"User-Agent": "compromise-tiles-stats"}  # the store's host refuses urllib's default User-Agent


def sign(method, url, key_id, secret, region="auto", now=None, headers=None):
    """The headers of an AWS Signature Version 4 request with an empty body (S3's own variant)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    u = urllib.parse.urlsplit(url)
    stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload = hashlib.sha256(b"").hexdigest()
    h = {"host": u.netloc, "x-amz-content-sha256": payload, "x-amz-date": stamp}
    h.update({k.lower(): v for k, v in (headers or {}).items()})
    q = sorted(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    query = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}" for k, v in q)
    names = sorted(h)
    canonical = "\n".join([method, urllib.parse.quote(u.path or "/", safe="/-_.~"), query,
                           "".join(f"{k}:{h[k].strip()}\n" for k in names), ";".join(names), payload])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = ("AWS4" + secret).encode()
    for part in (day, region, "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    h["authorization"] = f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"
    return h


class Bucket:
    def __init__(self, endpoint, bucket, key_id, secret, page=1000):
        self.base, self.key_id, self.secret, self.page = f"{endpoint.rstrip('/')}/{bucket}", key_id, secret, page
        self.requests, self._lock = 0, threading.Lock()

    def list(self, **params):
        """One ListObjectsV2 page: ([(key, size)], [common prefix], next token or None). Retried on 5xx and network errors."""
        params = {"list-type": "2", "max-keys": str(self.page), **{k: v for k, v in params.items() if v is not None}}
        url = self.base + "?" + urllib.parse.urlencode(sorted(params.items()), quote_via=lambda v, *_: urllib.parse.quote(v, safe="-_.~"))
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers=sign("GET", url, self.key_id, self.secret))
                with urllib.request.urlopen(req, timeout=60) as r: body = r.read()
                break
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as e:
                if attempt == 5 or (isinstance(e, urllib.error.HTTPError) and e.code < 500): raise
                time.sleep(2 ** attempt)
        with self._lock: self.requests += 1
        root = ET.fromstring(body)
        tag = lambda el: el.tag.rsplit("}", 1)[-1]
        objects, prefixes, token, truncated = [], [], None, False
        for el in root:
            t = tag(el)
            if t == "Contents":
                f = {tag(x): x.text for x in el}
                objects.append((f["Key"], int(f["Size"])))
            elif t == "CommonPrefixes": prefixes += [x.text for x in el if tag(x) == "Prefix"]
            elif t == "NextContinuationToken": token = el.text
            elif t == "IsTruncated": truncated = el.text == "true"
        return objects, prefixes, token if truncated else None

    def range(self, lo, hi):
        """Every (key, size) with lo < key <= hi (None: open end), in key order."""
        out, token = [], None
        while True:
            objects, _, token = self.list(**({"continuation-token": token} if token else {"start-after": lo or None}))
            for k, size in objects:
                if hi is not None and k > hi: return out
                out.append((k, size))
            if not token: return out


def cuts(layers, cities):
    """Range boundaries: the start of every 0.2° band of rows an area builds, in every layer, at the layer's key
    scale (both for a layer storage.py does not know; an empty range costs one request)."""
    rows = {scale: set() for scale in ROWS}
    for e in cities:
        s, _, n, _ = e["box"]
        for scale, step in ROWS.items():
            rows[scale] |= {k // step * step for k in range(int(s * scale), int(n * scale) + 1)}
    scales = lambda l: [LAYER_SCALE[l]] if l in LAYER_SCALE else list(ROWS)
    return sorted({f"tiles/{l}/{k}" for l in layers for scale in scales(l) for k in rows[scale]})


def list_all(bucket, cities, workers=WORKERS):
    """Every object of the bucket: the layers first (one request), then the ranges between the cuts in parallel."""
    _, prefixes, _ = bucket.list(prefix="tiles/", delimiter="/")
    b = cuts([p[len("tiles/"):].strip("/") for p in prefixes], cities)
    with ThreadPoolExecutor(workers) as pool:
        parts = pool.map(lambda r: bucket.range(*r), zip([None] + b, b + [None]))
        return [o for part in parts for o in part]


def builds(jobs, run, today, previous=None):
    """{city: {seconds, result, date, run}}: this run's `build (<city>)` jobs over the previous entries."""
    out = dict(previous or {})
    for j in jobs:
        m = re.fullmatch(r"build \((.+)\)", j.get("name", ""))
        if not m or not j.get("started_at") or not j.get("completed_at"): continue
        t = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        out[m[1]] = {"seconds": max(0, round((t(j["completed_at"]) - t(j["started_at"])).total_seconds())),
                     "result": j.get("conclusion"), "date": today, "run": run}
    return dict(sorted(out.items()))


def run_jobs(repo, run, token=None):
    """Every job of a workflow run (the API pages them by 100)."""
    jobs, page = [], 1
    while True:
        h = {"Accept": "application/vnd.github+json", **AGENT, **({"Authorization": f"Bearer {token}"} if token else {})}
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/runs/{run}/jobs?per_page=100&page={page}", headers=h)
        with urllib.request.urlopen(req, timeout=60) as r: got = json.load(r)["jobs"]
        jobs += got
        if len(got) < 100: return jobs
        page += 1


def load_previous(src):
    try:
        if src.startswith(("http://", "https://")):
            with urllib.request.urlopen(urllib.request.Request(src, headers=AGENT), timeout=60) as r: return json.load(r)
        with open(src) as f: return json.load(f)
    except Exception as e:  # absent (404, the first run) or unreadable: durations start from this run alone
        print(f"previous stats not read ({e})")
        return {}


def stats(objects, cities, cities_path, build_list, run, now):
    t = tally(objects, {FINE: {}, COARSE: {}})
    other = [v for k, v in t["prefixes"].items() if k != "tiles/"]
    # The streets cities' cells, in every layer; a layer storage.py does not know (streets/ itself) read at the fine scale.
    streets = {e["city"] for e in cities if e.get("streets") is True}
    cells, per_city = owners(cities_path, only=streets) if streets else {}, {}
    for key, size in objects if streets else []:
        m = TILE.match(key)
        city = m and cells[LAYER_SCALE.get(m[1], FINE)].get((int(m[2]), int(m[3])))
        if city:
            v = per_city.setdefault(city, {}).setdefault(m[1], {"bytes": 0, "objects": 0})
            v["bytes"] += size; v["objects"] += 1
    return {"updated": now, "run": run, "freeBytes": FREE_GB * 10 ** 9, "bytes": t["bytes"], "objects": t["objects"],
            "layers": t["layers"],
            "other": {"bytes": sum(v["bytes"] for v in other), "objects": sum(v["objects"] for v in other)},
            "streets": {c: dict(sorted(v.items())) for c, v in sorted(per_city.items())}, "builds": build_list}


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--bucket", required=True)
    a.add_argument("--endpoint", required=True)
    a.add_argument("--cities", default="cities.json")
    a.add_argument("--previous", default="")
    a.add_argument("--run", type=int)
    a.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    a.add_argument("--write", default="stats.json")
    args = a.parse_args()
    started = time.time()
    now = datetime.datetime.now(datetime.timezone.utc)
    cities = json.load(open(args.cities))

    bucket = Bucket(args.endpoint, args.bucket, os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"])
    objects = list_all(bucket, cities)
    print(f"listed {len(objects):,} objects in {bucket.requests:,} requests, {time.time() - started:.0f} s")

    prev = load_previous(args.previous) if args.previous else {}
    jobs = []
    if args.run and args.repo:
        try: jobs = run_jobs(args.repo, args.run, os.environ.get("GITHUB_TOKEN"))
        except Exception as e: print(f"jobs not read ({e}); durations kept from the previous stats")
    s = stats(objects, cities, args.cities, builds(jobs, args.run, now.date().isoformat(), prev.get("builds")),
              args.run, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    with open(args.write, "w") as f: json.dump(s, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{s['bytes'] / 1e9:.3f} GB in {s['objects']:,} objects, {len(s['layers'])} layers, "
          f"{len(s['builds'])} build durations -> {args.write} ({time.time() - started:.0f} s)")


if __name__ == "__main__":
    main()
