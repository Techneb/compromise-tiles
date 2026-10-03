#!/usr/bin/env python3
"""What the tile store holds: size and object count, per prefix, per layer and per layer × city.

  aws s3 ls "s3://$BUCKET" --recursive --endpoint-url ... > listing.txt
  python3 storage.py listing.txt --cities cities.json --write storage.json --summary storage.md

Reads the listing `aws s3 ls --recursive` prints (date, time, size in bytes, key). A tile's key,
tiles/<layer>/<latKey>,<lonKey>.json, names its cell; the cell is given to the first area of
cities.json that builds it (the generator's cells: its box, inside its boundary when it has one;
venues by the 1/50° cells over them). A tile no area builds is listed as NO_AREA: an area dropped
or moved since, a deletion candidate. Root files and layers no reader takes are flagged the same way.

Sizes are bytes at rest (the tiles are gzipped), in GB of 10^9 bytes as R2 bills them.
"""
import argparse, json, os, re
from make_tiles import COARSE, FINE, cells_in, clip, index

LAYER_SCALE = {"buildings": FINE, "terraces-v2": FINE, "communes": FINE, "sources": FINE, "venues": COARSE}
READ = {  # what a reader takes: the app's tile folders, then the files the app and the site read at the root
    "tiles": set(LAYER_SCALE) - {"communes"},  # communes/: no longer uploaded or read (2026-10-03), a deletion candidate
    "root": {"config.json", "coverage.json", "cities.json", "counts.json"},
}
NO_AREA = "(no area in cities.json)"
FREE_GB, PER_GB = 10, 0.015  # R2 Standard: free GB-months, then dollars per GB-month
TILE = re.compile(r"^tiles/([^/]+)/(-?\d+),(-?\d+)\.json$")


def parse(lines):
    """(key, size) for every object line of an `aws s3 ls --recursive` listing."""
    for line in lines:
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[2].isdigit(): yield parts[3].rstrip("\n"), int(parts[2])


def owners(cities_path):
    """{scale: {(ky, kx): city}}: the cells each area of cities.json builds, the first area winning."""
    out = {FINE: {}, COARSE: {}}
    for e in json.load(open(cities_path)):
        cells = cells_in(*e["box"])
        if e.get("boundary"):
            with open(os.path.join(os.path.dirname(os.path.abspath(cities_path)), e["boundary"])) as f: b = json.load(f)
            cells = clip(cells, b.get("geometry", b))
        for c in cells:
            out[FINE].setdefault(tuple(map(int, c.key.split(","))), e["city"])
            out[COARSE].setdefault((index(c.lat, COARSE), index(c.lon, COARSE)), e["city"])
    return out


def tally(objects, cells):
    """Bytes and objects: total, per top-level prefix, per tiles/ layer and per layer × city."""
    def add(d, k, size):
        v = d.setdefault(k, [0, 0])
        v[0] += size; v[1] += 1
    total, prefix, layer, city = [0, 0], {}, {}, {}
    for key, size in objects:
        total[0] += size; total[1] += 1
        add(prefix, key.split("/", 1)[0] + ("/" if "/" in key else ""), size)
        if key.startswith("tiles/"):
            name = key.split("/")[1]
            add(layer, name, size)
            m = TILE.match(key)
            owner = NO_AREA
            if m and name in LAYER_SCALE:
                owner = cells[LAYER_SCALE[name]].get((int(m[2]), int(m[3])), NO_AREA)
            elif not m:
                owner = "(not a cell key)"
            add(city, (name, owner), size)
    return {"bytes": total[0], "objects": total[1],
            "prefixes": {k: {"bytes": v[0], "objects": v[1]} for k, v in sorted(prefix.items())},
            "layers": {k: {"bytes": v[0], "objects": v[1]} for k, v in sorted(layer.items())},
            "cities": [{"layer": k[0], "city": k[1], "bytes": v[0], "objects": v[1]}
                       for k, v in sorted(city.items(), key=lambda kv: (kv[0][0], -kv[1][0]))]}


def unread(t):
    """Prefixes and layers no reader takes, with what they hold: the deletion candidates beside NO_AREA."""
    out = [(k, v) for k, v in t["prefixes"].items() if k != "tiles/" and k not in READ["root"]]
    out += [(f"tiles/{k}/", v) for k, v in t["layers"].items() if k not in READ["tiles"]]
    out += [(f"tiles/{c['layer']}/ outside every area", c) for c in t["cities"] if c["city"] == NO_AREA and c["layer"] in READ["tiles"]]
    return out


def gb(n): return f"{n / 1e9:.3f}"


def summary(t):
    cost = max(0, t["bytes"] / 1e9 - FREE_GB) * PER_GB
    lines = ["# Tile store", "",
             f"**{gb(t['bytes'])} GB** in **{t['objects']:,}** objects. "
             f"Storage at R2 Standard: ${cost:.2f} a month ({FREE_GB} GB-month free, then ${PER_GB}/GB-month); "
             "operations are not in a listing (see the bucket's metrics).", "",
             "## Per top-level prefix", "", "| Prefix | GB | Objects |", "|---|---:|---:|"]
    lines += [f"| `{k}` | {gb(v['bytes'])} | {v['objects']:,} |" for k, v in t["prefixes"].items()]
    lines += ["", "## Per layer (`tiles/`)", "", "| Layer | GB | Objects | Mean KB |", "|---|---:|---:|---:|"]
    lines += [f"| `{k}` | {gb(v['bytes'])} | {v['objects']:,} | {v['bytes'] / v['objects'] / 1000:.1f} |" for k, v in t["layers"].items()]
    lines += ["", "## Not read by the app or the site", ""]
    lines += [f"- `{k}`: {gb(v['bytes'])} GB, {v['objects']:,} objects" for k, v in unread(t)] or ["- nothing"]
    lines += ["", "## Per layer × city", "", "| Layer | City | GB | Objects |", "|---|---|---:|---:|"]
    lines += [f"| `{c['layer']}` | {c['city']} | {gb(c['bytes'])} | {c['objects']:,} |" for c in t["cities"]]
    return "\n".join(lines) + "\n"


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("listing", help="the output of `aws s3 ls s3://<bucket> --recursive`")
    a.add_argument("--cities", default="cities.json")
    a.add_argument("--write", default="storage.json")
    a.add_argument("--summary", default="storage.md")
    args = a.parse_args()
    with open(args.listing) as f: t = tally(parse(f), owners(args.cities))
    t["unread"] = [{"prefix": k, "bytes": v["bytes"], "objects": v["objects"]} for k, v in unread(t)]
    with open(args.write, "w") as f: json.dump(t, f, ensure_ascii=False, indent=1)
    with open(args.summary, "w") as f: f.write(summary(t))
    print(f"{gb(t['bytes'])} GB in {t['objects']} objects -> {args.write}, {args.summary}")


if __name__ == "__main__":
    main()
