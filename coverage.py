#!/usr/bin/env python3
"""Coverage feed: per-cell counts and per-area totals, merged into the previous feed.

  python3 coverage.py --out out --cities cities.json --previous <url or path> --write coverage.json [--slim site]

Reads the tiles a build just wrote (one JSON array per cell), replaces the records of the
cells it finds, keeps every other record (per layer, so a run that builds only terraces and
venues keeps the building counts), then drops the records of the cells no area builds now and totals each area of cities.json over
the cells it builds (its box, inside its boundary when it has one, the first area winning a shared
cell: storage.py's owners, the generator's cut), never over its box. A 200 m cell is `passing`
with enough buildings, `terracesKnown` with enough terraces too.

Output (compact JSON):
  updated     ISO date of this run
  dates       build dates referenced by index below
  cells       [latKey, lonKey, terraces, buildings, withHeight, terracesDate, buildingsDate, communes, venues, withSeating]
              200 m cells keyed int(lat*500), int(lon*500); a date is an index into `dates`
              (-1: layer never built); communes: space-separated INSEE codes, "" outside France,
              from the build's own communes/ files (never uploaded; a cell keeps its codes in the feed)
  communes    {INSEE code: name}
  venueCells  [latKey50, lonKey50, venues, withTerrace, date]  2 km cells keyed int(lat*50), int(lon*50)
  areas       [{name, city, box: [s, w, n, e] (the entry's), tiled, passing, places, seats, terracesKnown, km2, goingOut, goingOutShare, lastBuilt,
              buildings, withHeight, layers}]
              (a départements entry gives one area per commune, never an aggregate)
              passing: cells with enough buildings; terracesKnown: cells with enough terraces;
              km2: area of the passing cells; goingOut: going-out cells; goingOutShare: share of the going-out cells
              holding enough terraces and buildings both; buildings, withHeight: the cells' sums (a tile also
              holds its neighbours' footprints, for their shade: a share, withHeight / buildings, not a count);
              layers {terraces-v2|buildings-v2|venues: {tiles, oldest, newest}}: the area's tiles of that layer with a build date
  built       {city: {layer: {tiles, date}}}: the tiles of each layer the city's last build of it wrote (the layers the cells
              above do not date: streets, streets-main, numbers, places, boundaries, sources, transit), from the build
              jobs' fragments (2026-10-15); a city a run does not rebuild keeps its entry, a layer it does not build too
  transit     {city: {modes: {mode: lines}, lines, tiles, bytes, built}} from transit/ (1/25° cells): a tile goes to the city
              of the first cities.json entry whose box spans its cell; a line counts once per city by
              (mode, ref, stops); bytes are the tiles' raw JSON; built is the run's date. A city a run does not
              rebuild keeps its entry.

--slim writes the site's slim copies of the feed into a folder (slim() below): coverage.json stays whole for its other readers.
With --cities it also writes summary.json there: one record per city of cities.json (summary() below).
"""
import argparse, datetime, json, math, os, re, urllib.error, urllib.request
from make_tiles import CITY_BUILDINGS, PERMIT_CITIES, cells_in
from storage import COARSE, FINE, MAIN, areas, owners

FLOOR = 20        # buildings a cell needs for the filter; terraces too where no venue tile gives a denominator
TERRACE_RATIO, TERRACE_MINIMUM = 0.30, 5   # a terrace cell: terraces vs going-out venues nearby, the reader's rule
GOING_OUT = 10    # bars, cafés and restaurants within about 400 m


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
    except (FileNotFoundError, json.JSONDecodeError) as e:  # a local file absent or unreadable: start empty
        if src.startswith(("http://", "https://")): raise  # a 200 with a challenge page or a cut body is not an absent feed
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


def transit_summary(found, cities_path, today, city=None):
    """{city: {modes, lines, tiles, bytes, built}} over the transit/ tiles `found` yields as (key, raw). A build job
    builds one city (`city`, its CITY): every tile it wrote is that city's, edge cells included (a box's rule gave
    Paris's edge to Versailles, Tel Aviv's to Bat Yam)."""
    # The generator's 1/25° cells of each box, the first area winning a shared cell. Boundaries left out: storage.py's
    # owners clips every area's 200 m cells, minutes of work a fragment job would pay for a handful of tiles.
    own = {}
    for e in json.load(open(cities_path)):
        for c in cells_in(*area_box(e), scale=MAIN): own.setdefault(c.key, e["city"])
    out, seen, only = {}, {}, city
    for k, raw in found:
        city = only or own.get(k)
        if city is None: continue
        t = out.setdefault(city, {"modes": {}, "lines": 0, "tiles": 0, "bytes": 0, "built": today})
        t["tiles"] += 1; t["bytes"] += len(raw)
        for line in json.loads(raw):
            if (sig := (line["mode"], line["ref"], tuple(line["stops"]))) not in seen.setdefault(city, set()):
                seen[city].add(sig); t["modes"][line["mode"]] = t["modes"].get(line["mode"], 0) + 1; t["lines"] += 1
    return out


# The layers a fragment counts (built): those the cells do not date. communes/ and buildings/ are never uploaded.
COUNTED = ("streets", "streets-main", "numbers", "places", "boundaries", "sources", "transit")


def layer_counts(out, today):
    """{layer: {tiles, date}} for the COUNTED layers this build wrote a tile of."""
    found = {layer: sum(1 for _ in tiles(out, layer)) for layer in COUNTED}
    return {layer: {"tiles": n, "date": today} for layer, n in found.items() if n}


def span(dates):
    """{tiles, oldest, newest} over the build dates given (None: never built), None when none is dated."""
    ds = [d for d in dates if d]
    return {"tiles": len(ds), "oldest": min(ds), "newest": max(ds)} if ds else None


def merge_span(a, b):
    if not (a and b): return a or b
    return {"tiles": a["tiles"] + b["tiles"], "oldest": min(a["oldest"], b["oldest"]), "newest": max(a["newest"], b["newest"])}


SUMS = ("tiled", "passing", "places", "seats", "terracesKnown", "goingOut", "buildings", "withHeight")


def summary(feed, entries):
    """{updated, cities: {city: record}}: per city of cities.json or of an area, its areas' totals (SUMS, km2), their names
    and the box holding them, the newest
    lastBuilt, whether the build reads a permit feed or a city heights feed for it, whether an entry has the street
    layers or a municipal boundary, and its transit lines (absent when none was built). Since 2026-10-15 also: goingOutCovered
    (the going-out cells holding enough terraces and buildings both), permitFeed (its permit feed's host, null for none),
    layers {layer: {tiles, oldest, newest}} (the cells' layers summed over its areas, the counted ones from the feed's
    built) and transitModes."""
    permits, heights = {r["city"] for r in PERMIT_CITIES}, {r["city"] for r in CITY_BUILDINGS}
    hosts = {r["city"]: r["host"] for r in PERMIT_CITIES}
    out = {}
    new = lambda city: out.setdefault(city, {"areas": [], "box": None, **{k: 0 for k in SUMS}, "km2": 0.0, "built": "",
                                             "permits": city in permits, "cityHeights": city in heights, "streets": False, "outline": False,
                                             "goingOutCovered": 0, "permitFeed": hosts.get(city), "layers": {}})
    for e in entries:
        c = new(e["city"])
        c["streets"] |= e.get("streets") is True
        c["outline"] |= bool(e.get("boundary"))
    named = {e["city"] for e in entries}
    for a in feed["areas"]:   # a départements entry's areas are its communes, each a record of its own
        c = new(a["city"])
        # A commune of a départements entry is held to its commune: the build keeps a cell by its INSEE code.
        c["outline"] |= a["city"] not in named
        c["areas"].append(a["name"])
        if a.get("box"):
            b = c["box"] or a["box"]
            c["box"] = [min(b[0], a["box"][0]), min(b[1], a["box"][1]), max(b[2], a["box"][2]), max(b[3], a["box"][3])]
        for k in SUMS: c[k] += a.get(k) or 0
        c["km2"] += a.get("km2") or 0
        c["built"] = max(c["built"], a.get("lastBuilt") or "")
        c["goingOutCovered"] += round((a.get("goingOutShare") or 0) * (a.get("goingOut") or 0))
        for layer, sp in (a.get("layers") or {}).items(): c["layers"][layer] = merge_span(c["layers"].get(layer), sp)
    for city, t in feed.get("transit", {}).items():
        if city in out: out[city]["transit"], out[city]["transitModes"] = t["lines"], t.get("modes", {})
    for city, layers in feed.get("built", {}).items():
        if city in out:
            for layer, b in layers.items(): out[city]["layers"][layer] = {"tiles": b["tiles"], "oldest": b["date"], "newest": b["date"]}
    for c in out.values():
        c["km2"] = round(c["km2"], 1)
        c["layers"] = {k: v for k, v in sorted(c["layers"].items()) if v}
    return {"updated": feed["updated"], "cities": out}


def slim(feed, folder, entries=None):
    """The site's pages read these instead of the whole feed (tens of MB). areas.json: updated, areas, transit, and
    blocks {square: cells}, the index of coverage/<square>.json: the cells of one 1° square, keyed int(latKey / 500),
    int(lonKey / 500) truncated as the cells' own keys are, with the dates and commune names they use and the venue
    cells one 2 km cell past the square's edge (the map's 600 m sums cross it)."""
    sq = lambda k, n: f"{int(k[0] / n)},{int(k[1] / n)}"
    blocks, venues = {}, {}
    for c in feed["cells"]: blocks.setdefault(sq(c, 500), []).append(c)
    for v in feed["venueCells"]:
        for k in {sq((v[0] + i, v[1] + j), 50) for i in (-1, 0, 1) for j in (-1, 0, 1)}: venues.setdefault(k, []).append(v)
    os.makedirs(os.path.join(folder, "coverage"), exist_ok=True)
    def dump(o, *path):
        with open(os.path.join(folder, *path), "w") as f: json.dump(o, f, ensure_ascii=False, separators=(",", ":"))
    for k, cells in blocks.items():
        codes = {x for c in cells for x in c[7].split()}
        dump({"updated": feed["updated"], "dates": feed["dates"], "communes": {x: n for x, n in feed["communes"].items() if x in codes},
              "venueCells": venues.get(k, []), "cells": cells}, "coverage", k + ".json")
    dump({"updated": feed["updated"], "areas": feed["areas"], "transit": feed["transit"], "blocks": {k: len(v) for k, v in blocks.items()}}, "areas.json")
    if entries is not None: dump(summary(feed, entries), "summary.json")


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--out", help="a tiles folder to scan (this run's build)")
    a.add_argument("--cities", required=True)
    a.add_argument("--previous", default="")
    a.add_argument("--write", help="the feed to write")
    a.add_argument("--slim", help="with --write: a folder for the site's slim copies (areas.json, coverage/)")
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
    transit = dict(prev.get("transit", {}))

    touched, touched_venues, new_names, fragments = set(), set(), {}, []
    # Fragments from parallel build jobs: their records overlay the previous feed (the last one wins).
    for folder, _, files in os.walk(args.fragments or "/nonexistent"):
        for name in sorted(files):
            if not name.endswith(".json"): continue
            frag = json.load(open(os.path.join(folder, name)))
            fragments.append(frag)
            cells.update(frag.get("cells", {})); venues.update(frag.get("venues", {})); names.update(frag.get("communes", {}))
            transit.update(frag.get("transit", {}))
    # Counting keys in the bytes is enough: names never hold these quoted keys.
    for k, raw in tiles(args.out, "terraces-v2") if args.out else []:
        c = cells.setdefault(k, [0, 0, 0, None, None, ""])
        c[0], c[3] = raw.count(b'"coordinate"'), today; touched.add(k)
    # buildings-v2/, what the store and the app hold: one row a footprint, a null height for the guess.
    for k, raw in tiles(args.out, "buildings-v2") if args.out else []:
        c = cells.setdefault(k, [0, 0, 0, None, None, ""])
        rows = json.loads(raw)
        c[1], c[2], c[4] = len(rows), sum(1 for r in rows if r[0] is not None), today; touched.add(k)
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

    new_transit = transit_summary(tiles(args.out, "transit"), args.cities, today, os.environ.get("CITY")) if args.out else {}
    transit.update(new_transit)
    built = {c: dict(v) for c, v in prev.get("built", {}).items()}
    for frag in fragments:
        for city, layers in frag.get("built", {}).items(): built.setdefault(city, {}).update(layers)
    new_built = {os.environ["CITY"]: layer_counts(args.out, today)} if args.out and os.environ.get("CITY") else {}
    for city, layers in new_built.items(): built.setdefault(city, {}).update(layers)

    if args.fragment:
        json.dump({"cells": {k: cells[k] for k in touched}, "venues": {k: venues[k] for k in touched_venues}, "communes": new_names,
                   "transit": new_transit, "built": new_built},
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

    # A record of a cell no area builds now is a tile of an area dropped, moved or cut to its boundary since:
    # deleted from the store or left there unread, it is no coverage, so it leaves the feed (the map draws
    # every record). The same cut as storage.py's, so the feed and the store's deletion list agree.
    area_cells = areas(args.cities)
    own = owners(args.cities, built=area_cells)
    key = lambda k: tuple(map(int, k.split(",")))
    kept = {k: c for k, c in cells.items() if key(k) in own[FINE]}
    kept_venues = {k: v for k, v in venues.items() if key(k) in own[COARSE]}
    print(f"dropped {len(cells) - len(kept)} cells and {len(venues) - len(kept_venues)} venue cells no area builds")
    cells, venues = kept, kept_venues

    passes = lambda c: c[1] >= FLOOR
    result, taken = [], set()
    for e, its_cells in area_cells:
        box = area_box(e)
        keys = []
        for cell in its_cells:  # the area's own cells, a cell another area counted first left to it
            c = cells.get(cell.key)
            if c is None or cell.key in taken:
                continue
            codes = c[5].split()
            if codes:  # boxes overlap their neighbours: the commune decides (Boulogne's cells in Paris's box are the petite couronne's)
                if e.get("departements"):
                    if not any(x[:2] in e["departements"] for x in codes):
                        continue
                elif not any(names.get(x) == e["city"] for x in codes):
                    continue
            taken.add(cell.key)
            keys.append((*key(cell.key), c))
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
                "buildings": sum(c[1] for _, _, c in keys),
                "withHeight": sum(c[2] for _, _, c in keys),
                "layers": {"terraces-v2": span(c[3] for _, _, c in keys), "buildings-v2": span(c[4] for _, _, c in keys),
                           "venues": span((venues.get(k) or [0, 0, None])[2] for k in {f"{int(ky / 10)},{int(kx / 10)}" for ky, kx, _ in keys})},
            }
        if e.get("departements"):
            # No aggregate for a départements entry (owner, 2026-09-27): each commune is its own
            # area, named from the commune tiles, boxed by its own cells.
            by_commune = {}
            for ky, kx, c in keys:
                code = next((x for x in c[5].split() if x[:2] in e["departements"]), None)
                if code is None: continue  # no commune code recorded (its commune call failed): no area to give it to
                by_commune.setdefault(code, []).append((ky, kx, c))
            for code, group in sorted(by_commune.items()):
                lats = [ky / 500 for ky, _, _ in group]; lons = [kx / 500 for _, kx, _ in group]
                cbox = (min(lats), min(lons), max(lats) + 1 / 500, max(lons) + 1 / 500)
                result.append(summary(group, names.get(code, code), names.get(code, code), cbox))
            continue
        result.append(summary(keys, e.get("district") or e["city"], e["city"], box))

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
        "areas": result,
        # transit/ is built only where an entry says "streets": true: another city's record is an edge cell miscounted.
        "transit": {k: transit[k] for k in sorted(transit) if k in {e["city"] for e in json.load(open(args.cities)) if e.get("streets")}},
        "built": {k: built[k] for k in sorted(built) if k in {e["city"] for e in json.load(open(args.cities))}},
    }
    with open(args.write, "w") as f:
        json.dump(feed, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(cells)} cells, {len(venues)} venue cells, {len(result)} areas -> {args.write}")
    if args.slim: slim(feed, args.slim, json.load(open(args.cities)))


if __name__ == "__main__":
    main()
