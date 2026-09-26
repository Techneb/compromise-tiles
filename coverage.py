#!/usr/bin/env python3
"""Coverage feed: per-cell counts and per-area totals, merged into the previous feed.

  python3 coverage.py --out out --cities cities.json --previous <url or path> --write coverage.json

Reads the tiles a build just wrote (one JSON array per cell), replaces the records of the
cells it finds, keeps every other record (per layer, so a run that builds only terraces and
venues keeps the building counts), then totals each area of cities.json. The sunny filter
is available where a 200 m cell holds enough terraces and buildings.

Output (compact JSON):
  updated     ISO date of this run
  dates       build dates referenced by index below
  cells       [latKey, lonKey, terraces, buildings, withHeight, terracesDate, buildingsDate, communes]
              200 m cells keyed int(lat*500), int(lon*500); a date is an index into `dates`
              (-1: layer never built); communes: space-separated INSEE codes, "" outside France
  communes    {INSEE code: name}
  venueCells  [latKey50, lonKey50, venues, withTerrace, date]  2 km cells keyed int(lat*50), int(lon*50)
  areas       [{name, city, box: [s, w, n, e], tiled, passing, km2, goingOutShare, lastBuilt}]
"""
import argparse, datetime, json, math, os, re, urllib.error, urllib.request

FLOOR = 20        # terraces and buildings a cell needs for the filter
GOING_OUT = 10    # bars, cafés and restaurants within about 400 m
FALLBACK_HEIGHT = re.compile(rb'"height":\s*15(?:\.0+)?[,}]')  # written when a footprint has no height


def tiles(out, layer):
    d = os.path.join(out, layer)
    for name in os.listdir(d) if os.path.isdir(d) else []:
        if name.endswith(".json"):
            with open(os.path.join(d, name), "rb") as f:
                yield name[:-5], f.read()


def load_previous(src):
    try:
        if src.startswith(("http://", "https://")):
            # The store's host refuses urllib's default User-Agent with a 403, file or no file.
            req = urllib.request.Request(src, headers={"User-Agent": "compromise-tiles-coverage"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        with open(src) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:  # absent or unreadable: start empty
        print(f"previous feed not read ({e}); starting empty")
        return {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"previous feed absent ({e.code}); starting empty")
            return {}
        raise  # a store that did not answer must not reset every record to this run's cells


def cell_km2(ky):
    side = 0.002 * 111.32  # a cell is 0.002 degrees a side
    return side * side * math.cos(math.radians(ky / 500))


def area_box(e):
    if e.get("box"):
        return e["box"]
    dlat = e.get("half_km", 0.5) / 111.32
    dlon = dlat / math.cos(math.radians(e["lat"]))
    return [e["lat"] - dlat, e["lon"] - dlon, e["lat"] + dlat, e["lon"] + dlon]


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--out", required=True)
    a.add_argument("--cities", required=True)
    a.add_argument("--previous", default="")
    a.add_argument("--write", required=True)
    args = a.parse_args()
    today = datetime.date.today().isoformat()

    prev = load_previous(args.previous) if args.previous else {}
    pd = prev.get("dates", [])
    day = lambda i: pd[i] if 0 <= i < len(pd) else None
    # key -> [terraces, buildings, withHeight, terracesDate, buildingsDate, communes]
    cells = {f"{r[0]},{r[1]}": [r[2], r[3], r[4], day(r[5]), day(r[6]), r[7]] for r in prev.get("cells", [])}
    venues = {f"{r[0]},{r[1]}": [r[2], r[3], day(r[4])] for r in prev.get("venueCells", [])}
    names = dict(prev.get("communes", {}))

    # Counting keys in the bytes is enough: names never hold these quoted keys.
    for k, raw in tiles(args.out, "terraces-v2"):
        c = cells.setdefault(k, [0, 0, 0, None, None, ""])
        c[0], c[3] = raw.count(b'"coordinate"'), today
    for k, raw in tiles(args.out, "buildings"):
        c = cells.setdefault(k, [0, 0, 0, None, None, ""])
        n = raw.count(b'"outline"')
        c[1], c[2], c[4] = n, n - len(FALLBACK_HEIGHT.findall(raw)), today
    for k, raw in tiles(args.out, "communes"):
        if k in cells:
            found = json.loads(raw)
            names.update((x["code"], x["nom"]) for x in found)
            cells[k][5] = " ".join(x["code"] for x in found)
    for k, raw in tiles(args.out, "venues"):
        venues[k] = [raw.count(b'"name"'), len(re.findall(rb'"outdoor_seating":\s*true', raw)), today]

    # Going-out cells. The owner's rule: at least GOING_OUT bars, cafés and restaurants in
    # the 400 m around a cell. Approximated from the 2 km venue cells: each spreads its
    # venues evenly over its 100 cells of 200 m, and a cell sums its 3x3 neighbourhood
    # (600 m square). Coarse near a venue cell's edge and blind to a cluster inside it.
    def density(ky, kx):
        v = venues.get(f"{int(ky / 10)},{int(kx / 10)}")
        return v[0] / 100 if v else 0

    def going_out(ky, kx):
        return sum(density(ky + i, kx + j) for i in (-1, 0, 1) for j in (-1, 0, 1)) >= GOING_OUT

    passes = lambda c: c[0] >= FLOOR and c[1] >= FLOOR
    areas = []
    for e in json.load(open(args.cities)):
        s, w, n, east = box = area_box(e)
        keys = []
        for ky in range(int(s * 500), int(n * 500) + 1):
            for kx in range(int(w * 500), int(east * 500) + 1):
                c = cells.get(f"{ky},{kx}")
                if c is None:
                    continue
                codes = c[5].split()
                if e.get("box") and codes:  # boxes overlap their neighbours: the commune decides
                    if e.get("departements"):
                        if not any(x[:2] in e["departements"] for x in codes):
                            continue
                    elif not any(names.get(x) == e["city"] for x in codes):
                        continue
                keys.append((ky, kx, c))
        ok = [(ky, kx) for ky, kx, c in keys if passes(c)]
        out_cells = [(ky, kx, c) for ky, kx, c in keys if going_out(ky, kx)]
        dates = [d for _, _, c in keys for d in c[3:5] if d]
        areas.append({
            "name": e.get("district") or e["city"], "city": e["city"],
            "box": [round(x, 5) for x in box], "tiled": len(keys), "passing": len(ok),
            "km2": round(sum(cell_km2(ky) for ky, _ in ok), 2),
            "goingOutShare": round(sum(passes(c) for *_, c in out_cells) / len(out_cells), 3) if out_cells else None,
            "lastBuilt": max(dates) if dates else None,
        })

    dl = sorted({d for c in cells.values() for d in c[3:5] if d} | {v[2] for v in venues.values() if v[2]})
    ix = {d: i for i, d in enumerate(dl)}
    idx = lambda d: ix.get(d, -1)
    split = lambda k: list(map(int, k.split(",")))
    feed = {
        "updated": today, "dates": dl,
        "cells": [[*split(k), c[0], c[1], c[2], idx(c[3]), idx(c[4]), c[5]] for k, c in sorted(cells.items())],
        "communes": {k: names[k] for k in sorted({x for c in cells.values() for x in c[5].split()}) if k in names},
        "venueCells": [[*split(k), v[0], v[1], idx(v[2])] for k, v in sorted(venues.items())],
        "areas": areas,
    }
    with open(args.write, "w") as f:
        json.dump(feed, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(cells)} cells, {len(venues)} venue cells, {len(areas)} areas -> {args.write}")


if __name__ == "__main__":
    main()
