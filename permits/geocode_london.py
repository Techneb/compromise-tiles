#!/usr/bin/env python3
"""London boroughs' granted pavement licences → permits/<borough>.geojson, hand-refreshed snapshots.

A few boroughs publish their granted pavement licences only as a web page (premises name and address, no
dates, no coordinates, no stated licence). Like Edinburgh's, each page is read and geocoded ONCE, here
(geocode_edinburgh.py's place(): OpenStreetMap Nominatim at the house, else the postcode's centre from
postcodes.io), into a static GeoJSON that the tile build reads whole through the `geojson` shape.

    python3 permits/geocode_london.py waltham-forest|redbridge [--html file] [--cache file]

Refresh by hand when the page changes; `_source.downloaded` records the day.
"""

import argparse
import datetime
import html
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geocode_edinburgh as geo  # noqa: E402


def waltham_forest(page):
    """The "Approved pavement licences" section: an <h3> per premises, its address on the next line."""
    start = page.find("Approved pavement licences")
    if start < 0: raise SystemExit("Waltham Forest: no 'Approved pavement licences' section")
    section = page[start:]
    end = re.search(r"<h2", section[30:])
    section = section[:30 + end.start()] if end else section
    out = []
    for name, body in re.findall(r"<h3[^>]*>(.*?)</h3>(.*?)(?=<h3|$)", section, re.S):
        text = html.unescape(re.sub(r"<[^>]+>", "\n", body))
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        name = html.unescape(re.sub(r"<[^>]+>", "", name)).strip()
        if name and lines: out.append((name, lines[0]))
    return out


def day(text):
    """"10 Dec 2026", "30 Sept 2027", "02 January 2027": the page mixes them; None for anything else."""
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})$", text.strip())
    if not m: return None
    try: return datetime.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date()
    except ValueError: return None


def redbridge(page, today=None):
    """The "Applications approved (by Ward)" tables: unexpired rows' "Name and location" ("Nova Cafe, 5 Rex Parade,
    …"), the name before its first comma; the applicant column is never read."""
    today = today or datetime.date.today()
    start, end = page.find("Applications approved"), page.find("Applications denied")
    if start < 0: raise SystemExit("Redbridge: no 'Applications approved' section")
    out = []
    for tr in re.findall(r"<tr.*?</tr>", page[start:end if end > start else None], re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 3 or "," not in cells[2]: continue
        expiry = day(cells[1])
        if expiry is None or expiry < today: continue
        name, line = cells[2].split(",", 1)
        out.append((name.strip(), line.strip()))
    return out


BOROUGHS = {
    "waltham-forest": dict(
        publisher="London Borough of Waltham Forest", parse=waltham_forest,
        page="https://www.walthamforest.gov.uk/businesses/licences-and-street-trading/alcohol-and-entertainment-licences/pavement-licence",
        kept="the page's granted pavement licences (no dates on the page); premises name only"),
    "redbridge": dict(
        publisher="London Borough of Redbridge", parse=redbridge,
        page="https://www.redbridge.gov.uk/business/licences-and-permits/tables-and-chairs-licence-register/",
        kept="approved licences unexpired on the day of the snapshot; premises name only, never the applicant"),
}


def address(text):
    """"8 to 9 Albert Crescent" reads as a range, as "8-9" does."""
    return re.sub(r"(\d+[A-Za-z]?)\s+to\s+(\d+)", r"\1-\2", text)


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("borough", choices=sorted(BOROUGHS))
    a.add_argument("--html", help="the page, saved; downloaded when absent")
    a.add_argument("--out")
    a.add_argument("--cache", help="a JSON file of every answer, so a re-run asks nothing twice")
    args = a.parse_args()
    b = BOROUGHS[args.borough]
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), args.borough + ".geojson")
    if args.cache and os.path.exists(args.cache):
        with open(args.cache) as f: geo._cache.update(json.load(f))
    if args.html:
        with open(args.html, encoding="utf-8") as f: page = f.read()
    else:
        with urllib.request.urlopen(urllib.request.Request(b["page"], headers={"User-Agent": geo.AGENT}), timeout=60) as r:
            page = r.read().decode("utf-8")
    rows = b["parse"](page)
    print(f"{len(rows)} licences on the page", file=sys.stderr)
    seen, features, later, unplaced = set(), [], [], []
    try:
        for name, line in rows:
            key = (geo.clean(name), geo.clean(line))
            if key in seen: continue
            seen.add(key)
            found = geo.place(name, address(line), city="London")
            if found: features.append((name, *found))
            else: later.append((name, line, geo.parse(line, "London")[2]))
        centres = geo.centroids([pc for _, _, pc in later if pc])
        for name, line, pc in later:
            if pc in centres: features.append((name, *centres[pc], "postcode"))
            else: unplaced.append((name, line))
    finally:
        if args.cache:
            with open(args.cache, "w") as f: json.dump(geo._cache, f)
    # A point outside the borough (ONS boundary, boundaries/<borough>.geojson) is a wrong match: dropped.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from make_tiles import boundary, contains
    inside = boundary(args.borough)
    for f in [f for f in features if not contains(inside, f[1], f[2])]:
        features.remove(f); unplaced.append((f[0], "placed outside the borough"))
    houses = sum(1 for f in features if f[3] == "house")
    print(f"{len(seen)} distinct premises: {houses} at the house, {len(features) - houses} at the postcode's centre, "
          f"{len(unplaced)} not placed", file=sys.stderr)
    for name, line in unplaced: print(f"  not placed: {name} | {line}", file=sys.stderr)
    collection = {
        "type": "FeatureCollection",
        "_source": {
            "dataset": b["page"], "publisher": b["publisher"],
            "licence": f"not stated by the publisher; credited to the {b['publisher']}",
            "downloaded": datetime.date.today().isoformat(), "kept": b["kept"],
            "geocoder": "OpenStreetMap Nominatim (ODbL), house number and road checked; else the postcode's centre "
                        "from postcodes.io (ONS Postcode Directory, OGL), marked placed=postcode",
            "refresh": f"by hand, when the page changes: permits/geocode_london.py {args.borough}",
        },
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                      "properties": {"name": name, "kind": "TERRASSE", "placed": how}}
                     for name, lat, lon, how in sorted(features, key=lambda f: (f[0].lower(), f[1], f[2]))],
    }
    with open(out, "w") as f: json.dump(collection, f, ensure_ascii=False)
    print(f"{len(collection['features'])} points → {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
