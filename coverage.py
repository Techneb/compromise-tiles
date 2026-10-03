#!/usr/bin/env python3
"""Coverage feed: per-cell counts and per-area totals, merged into the previous feed.

  python3 coverage.py --out out --cities cities.json --previous <url or path> --write coverage.json

Reads the tiles a build just wrote (one JSON array per cell), replaces the records of the
cells it finds, keeps every other record (per layer, so a run that builds only terraces and
venues keeps the building counts), then totals each area of cities.json. The sunny filter
is available where a 200 m cell holds enough buildings (`passing`); where it also holds enough
terraces (`terracesKnown`) the filter judges terraces, elsewhere the side of the street.

Output (compact JSON):
  updated     ISO date of this run
  dates       build dates referenced by index below
  cells       [latKey, lonKey, terraces, buildings, withHeight, terracesDate, buildingsDate, communes, venues, withSeating]
              200 m cells keyed int(lat*500), int(lon*500); a date is an index into `dates`
              (-1: layer never built); communes: space-separated INSEE codes, "" outside France,
              from the build's own communes/ files (never uploaded; a cell keeps its codes in the feed)
  communes    {INSEE code: name}
  venueCells  [latKey50, lonKey50, venues, withTerrace, date]  2 km cells keyed int(lat*50), int(lon*50)
  areas       [{name, city, box: [s, w, n, e], tiled, passing, places, seats, terracesKnown, km2, goingOut, goingOutShare, lastBuilt}]
              (a départements entry gives one area per commune, never an aggregate)
              passing: cells with enough buildings; terracesKnown: cells with enough terraces;
              km2: area of the passing cells; goingOut: going-out cells; goingOutShare: share of the going-out cells
              holding enough terraces and buildings both
"""
import argparse, datetime, json, math, os, re, urllib.error, urllib.request

FLOOR = 20        # buildings a cell needs for the filter; terraces too where no venue tile gives a denominator
TERRACE_RATIO, TERRACE_MINIMUM = 0.30, 5   # a terrace cell: terraces vs going-out venues nearby, the reader's rule
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
    a.add_argument("--out", help="a tiles folder to scan (this run's build)")
    a.add_argument("--cities", required=True)
    a.add_argument("--previous", default="")
    a.add_argument("--write", help="the feed to write")
    a.add_argument("--fragment", help="write only the records this --out touched (one build job's share), no areas")
    a.add_argument("--fragments", help="a folder of fragments from parallel build jobs to overlay on --previous before the areas")
    args = a.parse_args()
    if not (args.out or args.fragments): a.error("--out or --fragments")
    if not (args.write or args.fragment): a.error("--write or --fragment")
    today = datetime.date.today().isoformat()

    prev = load_previous(args.previous) if args.previous else {}
    pd = prev.get("dates", [])
    day = lambda i: pd[i] if 0 <= i < len(pd) else None
    # key -> [terraces, buildings, withHeight, terracesDate, buildingsDate, communes, venues, withSeating]
    cells = {f"{r[0]},{r[1]}": [r[2], r[3], r[4], day(r[5]), day(r[6]), r[7], *(r[8:10] if len(r) > 9 else (0, 0))]
             for r in prev.get("cells", [])}
    venues = {f"{r[0]},{r[1]}": [r[2], r[3], day(r[4])] for r in prev.get("venueCells", [])}
    names = dict(prev.get("communes", {}))

    touched, touched_venues, new_names = set(), set(), {}
    # Fragments from parallel build jobs: their records overlay the previous feed (the last one wins).
    for folder, _, files in os.walk(args.fragments or "/nonexistent"):
        for name in sorted(files):
            if not name.endswith(".json"): continue
            frag = json.load(open(os.path.join(folder, name)))
            cells.update(frag.get("cells", {})); venues.update(frag.get("venues", {})); names.update(frag.get("communes", {}))
    # Counting keys in the bytes is enough: names never hold these quoted keys.
    for k, raw in tiles(args.out, "terraces-v2") if args.out else []:
        c = cells.setdefault(k, [0, 0, 0, None, None, ""])
        c[0], c[3] = raw.count(b'"coordinate"'), today; touched.add(k)
    for k, raw in tiles(args.out, "buildings") if args.out else []:
        c = cells.setdefault(k, [0, 0, 0, None, None, ""])
        n = raw.count(b'"outline"')
        c[1], c[2], c[4] = n, n - len(FALLBACK_HEIGHT.findall(raw)), today; touched.add(k)
    for k, raw in tiles(args.out, "communes") if args.out else []:
        if k in cells:
            found = json.loads(raw)
            names.update((x["code"], x["nom"]) for x in found); new_names.update((x["code"], x["nom"]) for x in found)
            cells[k][5] = " ".join(x["code"] for x in found); touched.add(k)
    for k, raw in tiles(args.out, "venues") if args.out else []:
        venues[k] = [raw.count(b'"name"'), len(re.findall(rb'"outdoor_seating":\s*(?:true|"[^"]+")', raw)), today]; touched_venues.add(k)
        # Places per 200 m block, for the blocks that hold data: c[6] venues, c[7] with outdoor seating.
        # A rebuilt venue tile recounts its blocks from zero first.
        # ponytail: a scan of every cell per venue tile (~10 M steps for 400 tiles); index cells by tile if it drags.
        ty, tx = map(int, k.split(","))
        for ck, c in cells.items():
            cy, cx = map(int, ck.split(","))
            if int(cy / 10) == ty and int(cx / 10) == tx and len(c) > 7: c[6] = c[7] = 0; touched.add(ck)
        for p in json.loads(raw):
            ck = f"{int(p['coordinate']['latitude'] * 500)},{int(p['coordinate']['longitude'] * 500)}"
            c = cells.get(ck)
            if c is None: continue
            while len(c) < 8: c.append(0)
            c[6] += 1
            c[7] += 1 if p.get("outdoor_seating") else 0
            touched.add(ck)

    if args.fragment:
        json.dump({"cells": {k: cells[k] for k in touched}, "venues": {k: venues[k] for k in touched_venues}, "communes": new_names},
                  open(args.fragment, "w"), ensure_ascii=False, separators=(",", ":"))
        print(f"fragment: {len(touched)} cells, {len(touched_venues)} venue cells -> {args.fragment}")
        return

    # Going-out cells. The owner's rule: at least GOING_OUT bars, cafés and restaurants in
    # the 400 m around a cell. Approximated from the 2 km venue cells: each spreads its
    # venues evenly over its 100 cells of 200 m, and a cell sums its 3x3 neighbourhood
    # (600 m square). Coarse near a venue cell's edge and blind to a cluster inside it.
    def density(ky, kx):
        v = venues.get(f"{int(ky / 10)},{int(kx / 10)}")
        return v[0] / 100 if v else 0

    def venues_near(ky, kx):
        """Venues around a cell (the 600 m square), None where no venue tile covers it."""
        near = [venues.get(f"{int((ky + i) / 10)},{int((kx + j) / 10)}") for i in (-1, 0, 1) for j in (-1, 0, 1)]
        return sum(v[0] / 100 for v in near if v) if any(near) else None

    def going_out(ky, kx):
        return (venues_near(ky, kx) or 0) >= GOING_OUT

    # ponytail: the reader counts the venue tiles' points within 400 m; here the same 600 m
    # square estimate as going_out stands in — parse the points if the two disagree on a city.
    def terraces_known(ky, kx, c):
        v = venues_near(ky, kx)
        if v is None: return c[0] >= FLOOR
        return c[0] >= TERRACE_MINIMUM and c[0] >= TERRACE_RATIO * v

    passes = lambda c: c[1] >= FLOOR
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
        def summary(keys, name, city, box):
            ok = [(ky, kx) for ky, kx, c in keys if passes(c)]
            out_cells = [(ky, kx, c) for ky, kx, c in keys if going_out(ky, kx)]
            dates = [d for _, _, c in keys for d in c[3:5] if d]
            return {
                "name": name, "city": city,
                "box": [round(x, 5) for x in box], "tiled": len(keys), "passing": len(ok),
                "places": sum(c[6] for _, _, c in keys if len(c) > 7), "seats": sum(c[7] for _, _, c in keys if len(c) > 7),
                "terracesKnown": sum(terraces_known(ky, kx, c) for ky, kx, c in keys),
                "km2": round(sum(cell_km2(ky) for ky, _ in ok), 2),
                "goingOut": len(out_cells),
                "goingOutShare": round(sum(terraces_known(ky, kx, c) and passes(c) for ky, kx, c in out_cells) / len(out_cells), 3)
                if out_cells else None,
                "lastBuilt": max(dates) if dates else None,
            }
        if e.get("departements"):
            # No aggregate for a départements entry (owner, 2026-09-27): each commune is its own
            # area, named from the commune tiles, boxed by its own cells.
            by_commune = {}
            for ky, kx, c in keys:
                code = next(x for x in c[5].split() if x[:2] in e["departements"])
                by_commune.setdefault(code, []).append((ky, kx, c))
            for code, group in sorted(by_commune.items()):
                lats = [ky / 500 for ky, _, _ in group]; lons = [kx / 500 for _, kx, _ in group]
                cbox = (min(lats), min(lons), max(lats) + 1 / 500, max(lons) + 1 / 500)
                areas.append(summary(group, names.get(code, code), names.get(code, code), cbox))
            continue
        areas.append(summary(keys, e.get("district") or e["city"], e["city"], box))

    dl = sorted({d for c in cells.values() for d in c[3:5] if d} | {v[2] for v in venues.values() if v[2]})
    ix = {d: i for i, d in enumerate(dl)}
    idx = lambda d: ix.get(d, -1)
    split = lambda k: list(map(int, k.split(",")))
    feed = {
        "updated": today, "dates": dl,
        "cells": [[*split(k), c[0], c[1], c[2], idx(c[3]), idx(c[4]), c[5], *((c[6], c[7]) if len(c) > 7 else (0, 0))]
                  for k, c in sorted(cells.items())],
        "communes": {k: names[k] for k in sorted({x for c in cells.values() for x in c[5].split()}) if k in names},
        "venueCells": [[*split(k), v[0], v[1], idx(v[2])] for k, v in sorted(venues.items())],
        "areas": areas,
    }
    with open(args.write, "w") as f:
        json.dump(feed, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(cells)} cells, {len(venues)} venue cells, {len(areas)} areas -> {args.write}")


if __name__ == "__main__":
    main()
