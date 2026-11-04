"""python3 smoke.py RUN out/LAYER/x,y.json … — read the uploaded tiles back through tiles.alephb.uk as the app reads them.

A tile passes when it comes back gzipped and holds the JSON that was built. An empty tile ([]) is a real tile:
a cell with no terrace or venue is uploaded empty to replace last week's (run 41869356307 read five as failures)."""
import gzip, json, sys, urllib.request

def verdict(local, encoding, body):
    """None when the served tile is the one built, else what is wrong with it."""
    if encoding != "gzip": return f"Content-Encoding {encoding}"
    try: served = json.loads(gzip.decompress(body))
    except Exception as e: return f"unreadable: {e}"
    return None if served == json.loads(gzip.decompress(local)) else "not the tile built (stale or cut short)"

def main(run, files):
    if not files: return print("Nothing uploaded: no smoke read.")
    bad = []
    for f in files:
        # The query string makes the edge fetch from R2, past a cached 404; the host refuses urllib's own User-Agent.
        url = f"https://tiles.alephb.uk/tiles/{f.removeprefix('out/')}?smoke={run}"
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                url, headers={"Accept-Encoding": "gzip", "User-Agent": "compromise-tiles"}), timeout=30)
            why = verdict(open(f, "rb").read(), r.headers.get("Content-Encoding"), r.read())
        except Exception as e: why = str(e)
        if why: bad.append(f"{url}: {why}")
        else: print(f"ok {url}")
    for b in bad: print(f"::error title=Smoke read failed::{b}")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
