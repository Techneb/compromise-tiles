#!/usr/bin/env python3
"""Counts per city from OpenStreetMap extracts; writes no tiles.

  python3 counts.py --cities counts-cities.json --extracts ./extracts --write counts.json [--only Paris,Budapest] [--drop]

For each city ({"city", "country", "lat", "lon"} or {"city", "country", "box": [s, w, n, e]}):
  goingOutCells  200 m cells, keyed int(lat*500), int(lon*500), whose 400 m disk (around the
                 cell's centre) holds at least GOING_OUT named bars, pubs, beer gardens, cafés
                 and restaurants
  terraceShare   the share of those cells whose 400 m disk holds outdoor_seating=yes venues,
                 named or not, at least TERRACE_RATIO of its named ones and TERRACE_MINIMUM (the
                 app's rule for a cell whose terraces are known)
  venues, terraces  the named venues and the outdoor-seating ones inside the city's extent
  extent         "municipality" where --boxes (cities.json) holds a box for the city, with its
                 boundary polygon when it has one; else the 2 × HALF_KM square around the point

A city given as a point is taken as a box of HALF_KM a side around it unless cities.json tiles
it as a municipality. A city without a point is listed under
"noPoint" and skipped (nothing is geocoded). OpenStreetMap comes from the smallest Geofabrik
extract holding the point, filtered with osmium-tool, never from Overpass.

Scope: every city of the survey list. Next: every city over 300,000 people in Europe, North
America and Oceania, added as rows of counts-cities.json.

Also writes the same rows as CSV beside --write, sorted by going-out cells, most first.
Needs Python 3 (standard library only) and osmium-tool on the PATH.
"""
import argparse, csv, datetime, json, math, os, sys, time
from make_tiles import (AMENITIES, Cell, SourceError, contains, download, features, geofabrik_pbf, index,
                        metres, osmium, outer_rings, padded)

GOING_OUT = 10   # named venues in the disk for a going-out cell
TERRACE_RATIO, TERRACE_MINIMUM = 0.30, 5   # a terrace cell: outdoor-seating venues vs named ones in the disk (the app's ratio)
RADIUS = 400     # metres
HALF_KM = 12     # half-side of the box around a city's point


def venues(pbf):
    """(lat, lon, named, outdoor) for every bar, pub, beer garden, café and restaurant of the extract."""
    kept = pbf + ".counts.pbf"
    try:
        osmium("tags-filter", pbf, "nwr/amenity=" + ",".join(AMENITIES), "-o", kept, "--overwrite")
        out = []
        for f in features(kept):
            tags, g = f["properties"], f["geometry"]
            if tags.get("amenity") not in AMENITIES: continue
            if g["type"] == "Point": lon, lat = g["coordinates"][:2]
            else:  # an area at its mean vertex
                ring = next(outer_rings(g), None)
                if not ring: continue
                lat, lon = (sum(v[k] for v in ring) / len(ring) for k in ("latitude", "longitude"))
            out.append((lat, lon, bool(str(tags.get("name", "")).strip()), tags.get("outdoor_seating") == "yes"))
        return out
    finally:
        if os.path.exists(kept): os.remove(kept)


def city_box(e):
    if e.get("box"): return tuple(e["box"])
    return padded((e["lat"], e["lon"], e["lat"], e["lon"]), HALF_KM * 1000)


def municipal(cities_json):
    """city name -> (box, boundary or None) from a cities.json: its first plain box entry per city (not a
    departements one), with its boundary polygon when it has one — the municipality where it is tiled that way."""
    if not cities_json or not os.path.exists(cities_json): return {}
    out = {}
    for e in json.load(open(cities_json)):
        if not e.get("box") or e.get("departements") or e["city"] in out: continue
        boundary = None
        if e.get("boundary"):
            with open(os.path.join(os.path.dirname(os.path.abspath(cities_json)), e["boundary"])) as f: boundary = json.load(f)
            boundary = boundary.get("geometry", boundary)
        out[e["city"]] = (tuple(e["box"]), boundary)
    return out


def count(points, box, boundary=None):
    """Going-out cells, terrace cells among them, named venues and terraces in the box (inside the boundary when given)."""
    within = lambda r, lat, lon: r[0] <= lat <= r[2] and r[1] <= lon <= r[3]
    inside = lambda lat, lon: within(box, lat, lon) and (boundary is None or contains(boundary, lat, lon))
    reach = padded(box, RADIUS)
    near = {}  # cell key -> [named, outdoor] within RADIUS of its centre
    dlat = RADIUS / 111_320
    for lat, lon, named, outdoor in points:
        if not (named or outdoor) or not within(reach, lat, lon): continue
        dlon = dlat / math.cos(math.radians(lat))
        for ky in range(index(lat - dlat) - 1, index(lat + dlat) + 2):
            for kx in range(index(lon - dlon) - 1, index(lon + dlon) + 2):
                c = Cell(ky, kx)
                if not inside(c.lat, c.lon) or metres(c.lat, c.lon, lat, lon) > RADIUS: continue
                v = near.setdefault((ky, kx), [0, 0])
                v[0] += named
                v[1] += outdoor
    going = [v for v in near.values() if v[0] >= GOING_OUT]
    in_box = [p for p in points if inside(p[0], p[1])]
    terrace = lambda v: v[1] >= max(TERRACE_MINIMUM, TERRACE_RATIO * v[0])
    return len(going), sum(terrace(v) for v in going), sum(p[2] for p in in_box), sum(p[3] for p in in_box)


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--cities", default="counts-cities.json")
    a.add_argument("--extracts", required=True, help="folder the Geofabrik extracts are kept in")
    a.add_argument("--write", default="counts.json")
    a.add_argument("--only", default="", help="comma-separated city names")
    a.add_argument("--boxes", default="cities.json", help="a cities.json whose municipal boxes and boundaries replace the 12 km square")
    a.add_argument("--drop", action="store_true", help="delete each extract once its cities are counted (small disks)")
    a.add_argument("--previous", default="", help="the published counts.json (URL or path): its rows for cities not counted this run are kept")
    args = a.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    cities = [e for e in json.load(open(args.cities)) if not only or e["city"] in only]
    no_point = [f"{e['city']}, {e['country']}" for e in cities if "lat" not in e and not e.get("box")]

    # A city tiled as a municipality is counted over that box and boundary; the others over the 12 km square.
    munis = municipal(args.boxes)
    extent = lambda e: munis.get(e["city"], (city_box(e), None))
    groups, failed = {}, []  # extract URL -> cities, so each extract is read once
    for e in cities:
        if "lat" not in e and not e.get("box"): continue
        s, w, n, east = extent(e)[0]
        try: groups.setdefault(geofabrik_pbf((s + n) / 2, (w + east) / 2), []).append(e)
        except SourceError as x: failed.append(f"{e['city']}: {x}")

    rows = []
    for url, members in groups.items():
        t = time.monotonic()
        try:
            pbf = download(url, args.extracts)
            points = venues(pbf)
        except SourceError as x:
            failed += [f"{e['city']}: {x}" for e in members]
            continue
        print(f"{url.rsplit('/', 1)[1]}: {len(points)} venues read in {time.monotonic() - t:.0f} s", file=sys.stderr)
        for e in members:
            t = time.monotonic()
            box, boundary = extent(e)
            going, terrace, named, outdoor = count(points, box, boundary)
            rows.append({"city": e["city"], "country": e["country"], "goingOutCells": going,
                         "terraceShare": round(terrace / going, 3) if going else None, "venues": named, "terraces": outdoor,
                         "extent": "municipality" if e["city"] in munis else f"{2 * HALF_KM} km square"})
            print(f"  {e['city']}: {going} going-out cells, {terrace} with terraces, {named} venues, {outdoor} terraces ({time.monotonic() - t:.1f} s)", file=sys.stderr)
        if args.drop: os.remove(pbf)

    # A partial run (--only) must not shrink the published file: the other cities keep their last rows.
    if args.previous:
        try:
            import urllib.request
            raw = urllib.request.urlopen(urllib.request.Request(args.previous, headers={"User-Agent": "compromise-tiles"})).read() \
                if "://" in args.previous else open(args.previous, "rb").read()
            counted = {r["city"] for r in rows}
            rows += [r for r in json.loads(raw).get("cities", []) if r["city"] not in counted]
        except Exception as x:  # noqa: BLE001 — a missing or unreadable previous file is not a reason to fail the run
            print(f"previous counts not merged: {x}", file=sys.stderr)
    rows.sort(key=lambda r: -r["goingOutCells"])
    with open(args.write, "w") as f:
        json.dump({"updated": datetime.date.today().isoformat(), "cities": rows, "noPoint": no_point, "failed": failed},
                  f, ensure_ascii=False, indent=1)
    with open(os.path.splitext(args.write)[0] + ".csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["city", "country", "goingOutCells", "terraceShare", "venues", "terraces", "extent"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} cities, {len(no_point)} without a point, {len(failed)} failed -> {args.write}", file=sys.stderr)
    for x in failed: print(f"  failed {x}", file=sys.stderr)


if __name__ == "__main__":
    main()
