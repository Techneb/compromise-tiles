#!/usr/bin/env python3
"""Edinburgh's tables-and-chairs permits → permits/edinburgh.geojson, a hand-refreshed snapshot.

The City of Edinburgh Council publishes every tables-and-chairs permit as a fortnightly xlsx
(Reference, Premises, Address, Valid From, Valid To, Status), addresses only. Like Buenos Aires's
and Adelaide's, the addresses are geocoded ONCE, here, into a static GeoJSON that the tile build
reads whole through the `geojson` shape; the build itself never calls a geocoder.

Kept: issued permits ("Outdoor Area Permit Issued") valid on the day this runs, one point per
premises and address. Placed, in order: OpenStreetMap Nominatim (ODbL) at one request a second,
the house number and road checked against the answer; else the postcode's centre from
postcodes.io (ONS Postcode Directory, OGL) for a row with a postcode, marked "placed": "postcode".
No keyless official address point source answered in 2026-10: the council's open-data hub has no
gazetteer or address layer and OS Places needs a key.

    python3 permits/geocode_edinburgh.py [--xlsx file] [--out permits/edinburgh.geojson] [--cache file]

Refresh by hand when the feed changes: run it again (the cache makes a re-run cheap) and commit
the file; `_source.downloaded` records the day.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

PAGE = "https://www.edinburgh.gov.uk/licences-permits/view-existing-tables-chairs-permits"
FEED = "https://www.edinburgh.gov.uk/downloads/file/25969/tables-and-chairs-permits"
AGENT = "compromise-tiles/1.0 (https://github.com/Techneb/compromise-tiles; contact@alephb.uk)"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
POSTCODES = "https://api.postcodes.io/postcodes"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
POSTCODE = re.compile(r"\b(EH\d{1,2})\s*(\d[A-Z]{2})\b", re.I)
NUMBER = re.compile(r"^(?:(?:GF|G/F|Ground Floor|Basement|Unit\s*\w+|Flat\s*\w+)[\s,]+)?(\d+[A-Za-z]?)(?:\s*[-/–]\s*\d+[A-Za-z]?)*\s+(.+)$")

_cache = {}
_last = 0.0


def fetch(url, data=None, pause=0.0):
    """One request, cached by URL and body; `pause` seconds since the last one to the same host."""
    global _last
    key = url + ("|" + data if data else "")
    if key in _cache: return _cache[key]
    wait = _last + pause - time.monotonic()
    if wait > 0: time.sleep(wait)
    req = urllib.request.Request(url, data=data.encode() if data else None,
                                 headers={"User-Agent": AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r: body = r.read()
    _last = time.monotonic()
    _cache[key] = json.loads(body)
    return _cache[key]


def read_xlsx(path):
    """The sheet's rows as dicts keyed by the header, dates as dates, stdlib only."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(NS + "t")))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(NS + "row"):
        cells = {}
        for c in row.findall(NS + "c"):
            col = re.match(r"[A-Z]+", c.get("r")).group()
            v = c.find(NS + "v")
            if v is None:
                t = c.find(NS + "is")
                cells[col] = "".join(x.text or "" for x in t.iter(NS + "t")) if t is not None else None
                continue
            kind = c.get("t")
            if kind == "s": cells[col] = shared[int(v.text)]
            elif kind in ("str", "inlineStr"): cells[col] = v.text
            else: cells[col] = float(v.text)
        rows.append(cells)
    header = {col: str(name).strip() for col, name in rows[0].items() if name}
    out = []
    for cells in rows[1:]:
        r = {header[col]: value for col, value in cells.items() if col in header}
        for key in ("Valid From", "Valid To"):
            if isinstance(r.get(key), float): r[key] = datetime.date(1899, 12, 30) + datetime.timedelta(days=int(r[key]))
        out.append(r)
    return out


def current(rows, today):
    """Issued permits valid today; a junk row (a number in Status) is skipped."""
    kept = []
    for r in rows:
        status = r.get("Status")
        if not isinstance(status, str) or "Permit Issued" not in status: continue
        start, end = r.get("Valid From"), r.get("Valid To")
        if not isinstance(start, datetime.date) or not isinstance(end, datetime.date): continue
        if start <= today <= end and isinstance(r.get("Address"), str) and r["Address"].strip(): kept.append(r)
    return kept


def parse(address):
    """(number, road, postcode) from an address line; number and road None without a street number."""
    postcode = None
    m = POSTCODE.search(address)
    if m: postcode = f"{m.group(1).upper()} {m.group(2).upper()}"
    rest = POSTCODE.sub("", address)
    parts = [p.strip(" ,") for p in rest.split(",")]
    parts = [p for p in parts if p and p.lower() not in ("edinburgh", "scotland", "uk", "united kingdom")]
    for p in parts:
        m = NUMBER.match(p)
        if m: return m.group(1), m.group(2).strip(), postcode, parts
    return None, None, postcode, parts


def clean(s): return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def numbers(house):
    """Every number an OSM house_number names: "86,88,90", "86-88", "127a"."""
    out = set()
    for part in re.split(r"[,;/ ]+", (house or "").lower()):
        m = re.match(r"(\d+)([a-z]?)(?:-(\d+)([a-z]?))?$", part)
        if not m: continue
        out.add(m.group(1) + m.group(2))
        if m.group(3):
            for n in range(int(m.group(1)), int(m.group(3)) + 1): out.add(str(n))
    return out


def accept(result, number, road, postcode):
    """A Nominatim answer that names our house number on our road, in our postcode district if it says one."""
    a = result.get("address") or {}
    got = numbers(a.get("house_number"))
    n = number.lower()
    if n not in got and re.sub(r"[a-z]$", "", n) not in got: return False
    if postcode and a.get("postcode") and a["postcode"].split()[0].upper() != postcode.split()[0]: return False
    ours, theirs = clean(road), clean(a.get("road") or a.get("pedestrian") or a.get("footway"))
    return ours == theirs or ours in clean(result.get("display_name"))


def nominatim(params):
    q = urllib.parse.urlencode(dict(params, countrycodes="gb", format="jsonv2", limit=5, addressdetails=1))
    return fetch(f"{NOMINATIM}?{q}", pause=1.05)


def place(name, address):
    """(lat, lon, how) or None."""
    number, road, postcode, parts = parse(address)
    tries = []
    if number:
        tries.append({"street": f"{number} {road}", "city": "Edinburgh"})
        if postcode: tries.append({"street": f"{number} {road}", "city": "Edinburgh", "postalcode": postcode})
        tries.append({"q": f"{number} {road}, Edinburgh"})
    for params in tries:
        for r in nominatim(params):
            if accept(r, number, road, postcode): return float(r["lat"]), float(r["lon"]), "house"
    return None


def centroids(postcodes):
    """Postcode centres from postcodes.io, a hundred a call."""
    out = {}
    todo = sorted(set(postcodes))
    for i in range(0, len(todo), 100):
        batch = todo[i:i + 100]
        for r in fetch(POSTCODES, data=json.dumps({"postcodes": batch}), pause=0.2).get("result") or []:
            if r.get("result") and r["result"].get("latitude") is not None:
                out[r["query"].upper()] = (r["result"]["latitude"], r["result"]["longitude"])
    return out


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("--xlsx", help="the council's file; downloaded from the page's link when absent")
    a.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "edinburgh.geojson"))
    a.add_argument("--cache", help="a JSON file of every answer, so a re-run asks nothing twice")
    a.add_argument("--today", default=datetime.date.today().isoformat())
    args = a.parse_args()
    today = datetime.date.fromisoformat(args.today)
    if args.cache and os.path.exists(args.cache):
        with open(args.cache) as f: _cache.update(json.load(f))
    path = args.xlsx
    if not path:
        path = "/tmp/edinburgh-permits.xlsx"
        req = urllib.request.Request(FEED, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f: f.write(r.read())
    rows = read_xlsx(path)
    kept = current(rows, today)
    print(f"{len(rows)} rows, {len(kept)} issued and valid on {today}", file=sys.stderr)
    seen, features, unplaced, by_postcode = set(), [], [], []
    try:
        for r in kept:
            name, address = str(r.get("Premises") or "").strip(), r["Address"].strip()
            key = (clean(name), clean(address))
            if key in seen: continue
            seen.add(key)
            found = place(name, address)
            if found is None:
                by_postcode.append((name, address)); continue
            lat, lon, how = found
            features.append((name, lat, lon, how))
        with_pc = [(n, ad, parse(ad)[2]) for n, ad in by_postcode]
        centres = centroids([pc for _, _, pc in with_pc if pc])
        for name, address, pc in with_pc:
            if pc and pc in centres: features.append((name, centres[pc][0], centres[pc][1], "postcode"))
            else: unplaced.append((name, address))
    finally:
        if args.cache:
            with open(args.cache, "w") as f: json.dump(_cache, f)
    houses = sum(1 for f in features if f[3] == "house")
    print(f"{len(seen)} distinct premises: {houses} placed at the house by Nominatim, "
          f"{len(features) - houses} at the postcode's centre, {len(unplaced)} not placed", file=sys.stderr)
    for name, address in unplaced: print(f"  not placed: {name} | {address}", file=sys.stderr)
    collection = {
        "type": "FeatureCollection",
        "_source": {
            "feed": FEED, "dataset": PAGE, "publisher": "City of Edinburgh Council",
            "licence": "© City of Edinburgh Council (used at the owner's risk, DECISIONS.md)",
            "downloaded": today.isoformat(),
            "kept": "issued permits valid on the day of the snapshot; premises name only",
            "geocoder": "OpenStreetMap Nominatim (ODbL), house number and road checked; else the postcode's centre "
                        "from postcodes.io (ONS Postcode Directory, OGL), marked placed=postcode",
            "refresh": "by hand, when the feed changes: permits/geocode_edinburgh.py",
        },
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                      "properties": {"name": name, "kind": "TERRASSE", "placed": how}}
                     for name, lat, lon, how in sorted(features, key=lambda f: (f[0].lower(), f[1], f[2]))],
    }
    with open(args.out, "w") as f: json.dump(collection, f, ensure_ascii=False)
    print(f"{len(collection['features'])} points → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
