#!/usr/bin/env python3
"""The tiles no area builds that hold no data: what delete-empty-orphans.yml deletes (owner, 2026-10-12).

  python3 empty_orphans.py select listing.txt --cities cities.json --candidates candidates.txt
  (each candidate fetched to fetched/<key>)
  python3 empty_orphans.py report listing.txt --cities cities.json --fetched fetched --delete delete.txt \
      --write empty-orphans.json --summary empty-orphans.md

An orphan is storage.orphans(): a tile of a read layer whose cell no area of cities.json builds. It is
empty when it holds the empty array: make_tiles.write() dumps `[]` and tiles.yml gzips it with -9n, 22 bytes
at rest. Only orphans of EMPTY_MAX bytes or fewer can be that, so only those are fetched (`select`), and each
is read before it is listed for deletion (`report`): a small tile with data, or one that could not be fetched,
is kept. Orphans with data stay (an area may own them again: the councils around Jerusalem). Each orphan is
counted under the nearest area's city, the area it was most likely built for.
"""
import argparse, gzip, json, math, os
from make_tiles import COARSE
from storage import LAYER_SCALE, TILE, areas, gb, orphans, owners, parse

EMPTY_MAX = 64  # bytes: `[]` is 2 bytes plain, 22 gzipped with -9n, under 40 gzipped with a file name


def empty(raw):
    """True when a tile's bytes (gzipped or not) are the empty array; anything else, unreadable included, is data."""
    try:
        if raw[:2] == b"\x1f\x8b": raw = gzip.decompress(raw)
        return json.loads(raw) == []
    except (OSError, EOFError, ValueError):
        return False


def nearest(key, built):
    """The city of the area whose box is nearest the tile's cell centre (degrees, longitude scaled by latitude)."""
    m = TILE.match(key)
    scale = LAYER_SCALE.get(m[1], COARSE)
    lat, lon = (int(m[2]) + 0.5) / scale, (int(m[3]) + 0.5) / scale
    def distance(box):
        s, w, n, e = box
        return math.hypot(max(s - lat, 0, lat - n), max(w - lon, 0, lon - e) * math.cos(math.radians(lat)))
    return min(built, key=lambda b: distance(b[0]["box"]))[0]["city"]


def classify(objects, cells, read):
    """(empty, kept): the orphans as (key, size), split by `read(key)`, the fetched bytes or None. Only the
    orphans of EMPTY_MAX bytes or fewer are read; a larger one, or one `read` has no bytes for, is kept."""
    size = dict(objects)
    out, kept = [], []
    for key in orphans(objects, cells):
        raw = read(key) if size[key] <= EMPTY_MAX else None
        (out if raw is not None and empty(raw) else kept).append((key, size[key]))
    return out, kept


def totals(tiles, built):
    """Objects and bytes per layer and per nearest city."""
    layer, city = {}, {}
    for key, size in tiles:
        for d, k in ((layer, key.split("/")[1]), (city, nearest(key, built))):
            v = d.setdefault(k, {"objects": 0, "bytes": 0})
            v["objects"] += 1; v["bytes"] += size
    by_count = lambda d: dict(sorted(d.items(), key=lambda kv: (-kv[1]["objects"], kv[0])))
    return {"objects": len(tiles), "bytes": sum(s for _, s in tiles), "layers": by_count(layer), "cities": by_count(city)}


def summary(t):
    def table(title, d):
        return ["", f"#### {title}", "", "| | Objects | Bytes |", "|---|---:|---:|"] + \
               [f"| {k} | {v['objects']:,} | {v['bytes']:,} |" for k, v in d.items()]
    e, k = t["empty"], t["kept"]
    lines = ["# Empty orphan tiles", "",
             f"**{e['objects']:,} empty tiles** no area builds, {e['bytes']:,} bytes ({gb(e['bytes'])} GB): delete.txt.",
             f"Kept: {k['objects']:,} orphans with data ({gb(k['bytes'])} GB), "
             f"{t['unfetched']:,} of them small but not fetched.", "", "## Empty (delete.txt)"]
    lines += table("Per layer", e["layers"]) + table("Per nearest city", e["cities"])
    lines += ["", "## Kept (orphans with data)"] + table("Per layer", k["layers"]) + table("Per nearest city", k["cities"])
    return "\n".join(lines) + "\n"


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("step", choices=["select", "report"])
    a.add_argument("listing", help="the output of `aws s3 ls s3://<bucket> --recursive`")
    a.add_argument("--cities", default="cities.json")
    a.add_argument("--candidates", default="candidates.txt", help="select: the small orphans to fetch, one key a line")
    a.add_argument("--fetched", default="fetched", help="report: where each candidate was fetched, at its key")
    a.add_argument("--delete", default="delete.txt", help="report: the empty orphans, one key a line")
    a.add_argument("--kept", default="kept.txt", help="report: the orphans with data, one key a line")
    a.add_argument("--write", default="empty-orphans.json")
    a.add_argument("--summary", default="empty-orphans.md")
    args = a.parse_args()
    with open(args.listing) as f: objects = list(parse(f))
    built = areas(args.cities)
    cells = owners(args.cities, built=built)
    if args.step == "select":
        size = dict(objects)
        small = [k for k in orphans(objects, cells) if size[k] <= EMPTY_MAX]
        with open(args.candidates, "w") as f: f.writelines(k + "\n" for k in small)
        print(f"{len(small)} orphans of {EMPTY_MAX} bytes or fewer -> {args.candidates}")
        return
    def read(key):
        try:
            with open(os.path.join(args.fetched, key), "rb") as f: return f.read()
        except OSError:
            return None
    out, kept = classify(objects, cells, read)
    unfetched = sum(1 for k, s in kept if s <= EMPTY_MAX and read(k) is None)
    t = {"empty": totals(out, built), "kept": totals(kept, built), "unfetched": unfetched}
    with open(args.delete, "w") as f: f.writelines(k + "\n" for k, _ in out)
    with open(args.kept, "w") as f: f.writelines(k + "\n" for k, _ in kept)
    with open(args.write, "w") as f: json.dump(t, f, ensure_ascii=False, indent=1)
    with open(args.summary, "w") as f: f.write(summary(t))
    print(f"{len(out)} empty orphans ({t['empty']['bytes']} bytes) -> {args.delete}; {len(kept)} kept")


if __name__ == "__main__":
    main()
