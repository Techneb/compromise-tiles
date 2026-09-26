#!/usr/bin/env python3
"""Counts per city from OpenStreetMap extracts; writes no tiles.

  python3 counts.py --cities counts-cities.json --extracts ./extracts --write counts.json [--only Paris,Budapest] [--drop]

For each city ({"city", "country", "lat", "lon"} or {"city", "country", "box": [s, w, n, e]}):
  goingOutCells  200 m cells, keyed int(lat*500), int(lon*500), whose 400 m disk (around the
                 cell's centre) holds at least GOING_OUT named bars, pubs, beer gardens, cafés
                 and restaurants
  terraceShare   the share of those cells whose 400 m disk holds at least TERRACES of them
                 tagged outdoor_seating=yes, named or not (a flat floor until a ratio is set)
  venues, terraces  the named venues and the outdoor-seating ones inside the city's box

A city given as a point is taken as a box of HALF_KM a side around it — a rough stand-in for
the municipality's extent in this first version. A city without a point is listed under
"noPoint" and skipped (nothing is geocoded). OpenStreetMap comes from the smallest Geofabrik
extract holding the point, filtered with osmium-tool, never from Overpass.

Scope: every city of the survey list. Next: every city over 300,000 people in Europe, North
America and Oceania, added as rows of counts-cities.json.

Also writes the same rows as CSV beside --write, sorted by going-out cells, most first.
Needs Python 3 (standard library only) and osmium-tool on the PATH.
"""
import argparse, csv, datetime, json, math, os, sys, time
from make_tiles import (AMENITIES, Cell, SourceError, download, features, geofabrik_pbf, index,
                        metres, osmium, outer_rings, padded)

GOING_OUT = 10   # named venues in the disk for a going-out cell
TERRACES = 20    # outdoor-seating venues in the disk for a terrace cell
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


def count(points, box):
    """Going-out cells, terrace cells among them, named venues and terraces in the box."""
    within = lambda r, lat, lon: r[0] <= lat <= r[2] and r[1] <= lon <= r[3]
    inside = lambda lat, lon: within(box, lat, lon)
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
    return len(going), sum(v[1] >= TERRACES for v in going), sum(p[2] for p in in_box), sum(p[3] for p in in_box)


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--cities", default="counts-cities.json")
    a.add_argument("--extracts", required=True, help="folder the Geofabrik extracts are kept in")
    a.add_argument("--write", default="counts.json")
    a.add_argument("--only", default="", help="comma-separated city names")
    a.add_argument("--drop", action="store_true", help="delete each extract once its cities are counted (small disks)")
    args = a.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    cities = [e for e in json.load(open(args.cities)) if not only or e["city"] in only]
    no_point = [f"{e['city']}, {e['country']}" for e in cities if "lat" not in e and not e.get("box")]

    groups, failed = {}, []  # extract URL -> cities, so each extract is read once
    for e in cities:
        if "lat" not in e and not e.get("box"): continue
        s, w, n, east = city_box(e)
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
            box = city_box(e)
            going, terrace, named, outdoor = count(points, box)
            rows.append({"city": e["city"], "country": e["country"], "goingOutCells": going,
                         "terraceShare": round(terrace / going, 3) if going else None, "venues": named, "terraces": outdoor})
            print(f"  {e['city']}: {going} going-out cells, {terrace} with terraces, {named} venues, {outdoor} terraces ({time.monotonic() - t:.1f} s)", file=sys.stderr)
        if args.drop: os.remove(pbf)

    rows.sort(key=lambda r: -r["goingOutCells"])
    with open(args.write, "w") as f:
        json.dump({"updated": datetime.date.today().isoformat(), "cities": rows, "noPoint": no_point, "failed": failed},
                  f, ensure_ascii=False, indent=1)
    with open(os.path.splitext(args.write)[0] + ".csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["city", "country", "goingOutCells", "terraceShare", "venues", "terraces"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} cities, {len(no_point)} without a point, {len(failed)} failed -> {args.write}", file=sys.stderr)
    for x in failed: print(f"  failed {x}", file=sys.stderr)


if __name__ == "__main__":
    main()
