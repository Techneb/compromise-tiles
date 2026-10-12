#!/usr/bin/env python3
"""The tiles no area builds that hold no data: what delete-empty-orphans.yml deletes (owner, 2026-10-12).

  python3 empty_orphans.py select listing.txt --cities cities.json --sample sample.txt --candidates candidates.txt
  (each key of both fetched to fetched/<key>)
  python3 empty_orphans.py report listing.txt --cities cities.json --sample sample.txt --fetched fetched \
      --delete delete.txt --kept kept.txt --write empty-orphans.json --summary empty-orphans.md

An orphan is storage.orphans(): a tile of a read layer whose cell no area of cities.json builds. It is
empty when it holds the empty array: make_tiles.write() dumps `[]` and tiles.yml gzips it with -9n, always
EMPTY_SIZE (22) bytes at rest. Thousands of orphans are that size, too many to fetch one by one (the first dry
run hit the 120-minute timeout), so the listing's size picks them: `select` draws SAMPLE of them at random, and
`report` fails, listing nothing for deletion, unless every one was fetched and holds []. An orphan of another
size up to EMPTY_MAX is fetched and read only while there are fewer than OTHER_LIMIT of them; otherwise it is
kept unread. Orphans with data stay (an area may own them again: the councils around Jerusalem). Each orphan
is counted under the nearest area's city, the area it was most likely built for.
"""
import argparse, gzip, json, math, os, random, sys
from make_tiles import COARSE
from storage import LAYER_SCALE, TILE, areas, gb, orphans, owners, parse

EMPTY_SIZE = 22  # bytes: `[]` gzipped with -9n, as tiles.yml stores every tile
EMPTY_MAX = 64   # bytes: `[]` is 2 bytes plain, under 40 gzipped with a file name
SAMPLE = 50      # orphans of EMPTY_SIZE fetched to check the size says empty
OTHER_LIMIT = 500  # orphans of another size up to EMPTY_MAX are read only below this many


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


def small(objects, cells):
    """(sized, other): the orphans of exactly EMPTY_SIZE bytes, and those of another size up to EMPTY_MAX."""
    size = dict(objects)
    sized, other = [], []
    for key in orphans(objects, cells):
        if size[key] == EMPTY_SIZE: sized.append(key)
        elif size[key] <= EMPTY_MAX: other.append(key)
    return sized, other


def pick(objects, cells, rng=random):
    """(sample, candidates): SAMPLE orphans of EMPTY_SIZE drawn at random, and the other small orphans to read,
    none when there are OTHER_LIMIT or more of them."""
    sized, other = small(objects, cells)
    return rng.sample(sized, min(SAMPLE, len(sized))), other if len(other) < OTHER_LIMIT else []


def check(sample, read):
    """The sample's keys that were not fetched or do not hold []: any means the size cannot be trusted."""
    return [k for k in sample if (raw := read(k)) is None or not empty(raw)]


def classify(objects, cells, read):
    """(empty, kept, unread): the orphans as (key, size). One of EMPTY_SIZE is empty by its size; one of another
    size up to EMPTY_MAX is empty when `read(key)` (the fetched bytes or None) holds [], read only while there are
    fewer than OTHER_LIMIT of them. The rest is kept; `unread` counts the small ones kept without being read."""
    size = dict(objects)
    sized, other = small(objects, cells)
    sized, other = set(sized), set(other) if len(other) < OTHER_LIMIT else set()
    out, kept, unread = [], [], 0
    for key in orphans(objects, cells):
        raw = read(key) if key in other else None
        if key in sized or (raw is not None and empty(raw)): out.append((key, size[key]))
        else:
            kept.append((key, size[key]))
            unread += size[key] <= EMPTY_MAX and raw is None
    return out, kept, unread


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
    e, k, s = t["empty"], t["kept"], t["sample"]
    lines = ["# Empty orphan tiles", ""]
    if s["failed"]:
        lines += [f"**Sample check failed**: {len(s['failed'])} of {s['checked']} orphans of {EMPTY_SIZE} bytes "
                  "not fetched or not []; nothing listed for deletion.", ""] + [f"- `{x}`" for x in s["failed"][:20]] + [""]
    lines += [f"**{e['objects']:,} empty tiles** no area builds, {e['bytes']:,} bytes ({gb(e['bytes'])} GB): delete.txt.",
              f"Sample: {s['checked'] - len(s['failed'])} of {s['checked']} orphans of {EMPTY_SIZE} bytes fetched and [].",
              f"Kept: {k['objects']:,} orphans ({gb(k['bytes'])} GB), {t['unread']:,} of them small but not read "
              f"({t['other']:,} of another size up to {EMPTY_MAX} bytes: read when under {OTHER_LIMIT}).", "",
              "## Empty (delete.txt)"]
    lines += table("Per layer", e["layers"]) + table("Per nearest city", e["cities"])
    lines += ["", "## Kept"] + table("Per layer", k["layers"]) + table("Per nearest city", k["cities"])
    return "\n".join(lines) + "\n"


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("step", choices=["select", "report"])
    a.add_argument("listing", help="the output of `aws s3 ls s3://<bucket> --recursive`")
    a.add_argument("--cities", default="cities.json")
    a.add_argument("--sample", default="sample.txt", help=f"the orphans of {EMPTY_SIZE} bytes to fetch and check, one key a line")
    a.add_argument("--candidates", default="candidates.txt", help="select: the other small orphans to fetch, one key a line")
    a.add_argument("--fetched", default="fetched", help="report: where each candidate was fetched, at its key")
    a.add_argument("--delete", default="delete.txt", help="report: the empty orphans, one key a line")
    a.add_argument("--kept", default="kept.txt", help="report: the orphans kept, one key a line")
    a.add_argument("--write", default="empty-orphans.json")
    a.add_argument("--summary", default="empty-orphans.md")
    args = a.parse_args()
    with open(args.listing) as f: objects = list(parse(f))
    built = areas(args.cities)
    cells = owners(args.cities, built=built)
    if args.step == "select":
        sample, other = pick(objects, cells)
        with open(args.sample, "w") as f: f.writelines(k + "\n" for k in sample)
        with open(args.candidates, "w") as f: f.writelines(k + "\n" for k in other)
        print(f"{len(sample)} orphans of {EMPTY_SIZE} bytes -> {args.sample}; {len(other)} of another size up to "
              f"{EMPTY_MAX} -> {args.candidates}")
        return
    def read(key):
        try:
            with open(os.path.join(args.fetched, key), "rb") as f: return f.read()
        except OSError:
            return None
    with open(args.sample) as f: sample = f.read().split()
    failed = check(sample, read)
    out, kept, unread = classify(objects, cells, read)
    if failed: out, kept = [], sorted(out + kept)
    t = {"empty": totals(out, built), "kept": totals(kept, built), "unread": unread, "other": len(small(objects, cells)[1]),
         "sample": {"checked": len(sample), "failed": failed}}
    with open(args.delete, "w") as f: f.writelines(k + "\n" for k, _ in out)
    with open(args.kept, "w") as f: f.writelines(k + "\n" for k, _ in kept)
    with open(args.write, "w") as f: json.dump(t, f, ensure_ascii=False, indent=1)
    with open(args.summary, "w") as f: f.write(summary(t))
    print(f"{len(out)} empty orphans ({t['empty']['bytes']} bytes) -> {args.delete}; {len(kept)} kept")
    if failed: sys.exit(f"sample check failed: {len(failed)} of {len(sample)} orphans of {EMPTY_SIZE} bytes not []: {failed[:5]}")


if __name__ == "__main__":
    main()
