#!/usr/bin/env python3
"""Which built tiles differ from the store's: new, changed or unchanged, per layer.

  aws s3api list-objects-v2 --bucket "$BUCKET" --prefix tiles/venues/ \
    --query 'Contents[].[Key,ETag]' --output text --endpoint-url ... > remote.tsv
  python3 changed.py --listing remote.tsv --out out [--prune] [--summary summary.md]

Compares each file under --out, as it will be uploaded (gzipped), with the stored object of the
same key (tiles/<path under out>): a single-part upload's ETag is the MD5 of its body. An ETag
with a "-" (a multipart upload) is not an MD5, so that file counts as changed. --prune deletes the
unchanged files from --out, so uploading what is left uploads only new and changed tiles. A
missing listing counts every file as new.

With --city (and --cities), --stale lists the store's numbers/ and boundaries/ keys this run emptied, for
deletion: their builds write no file for a cell with nothing, so the upload alone would leave a stale range or
outline served. Only in the city's own cells (its flagged areas' boxes that no other flagged city's box reaches,
nor comes within a cell of for boundaries/): numbers/ where this run wrote the cell's streets/ tile, boundaries/
once it wrote any (a failed source writes none).

With --unshaded (make_tiles.py's list of the cells no venue or terrace reaches, a weekly run's), --stale also lists
the store's buildings-v2/ keys of those cells, for deletion (tiles.yml's prune_buildings): only cells no other city builds.
"""
import argparse, hashlib, json, os

STATES = ("new", "changed", "unchanged")
STALE = ("numbers", "boundaries")


def etags(lines):
    """{key: etag} from `--query 'Contents[].[Key,ETag]' --output text` lines (key, tab, quoted ETag)."""
    out = {}
    for line in lines:
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 2: out[parts[0]] = parts[1].strip('"')
    return out


def state(md5, etag):
    if etag is None: return "new"
    return "unchanged" if "-" not in etag and etag == md5 else "changed"


def local(out, prefix="tiles/"):
    """(key, path, md5, size) for every file under `out`, keyed as uploaded."""
    for root, _, files in os.walk(out):
        for name in files:
            path = os.path.join(root, name)
            with open(path, "rb") as f: body = f.read()
            yield prefix + os.path.relpath(path, out).replace(os.sep, "/"), path, hashlib.md5(body).hexdigest(), len(body)


def plan(files, remote):
    """[(key, path, state)] and {layer: {state: [count, bytes]}} for files = [(key, path, md5, size)]."""
    rows, totals = [], {}
    for key, path, md5, size in files:
        s = state(md5, remote.get(key))
        rows.append((key, path, s))
        layer = key.split("/")[1] if key.count("/") >= 2 else "(root)"
        t = totals.setdefault(layer, {k: [0, 0] for k in STATES})[s]
        t[0] += 1; t[1] += size
    return rows, totals


def stale(remote, out, cities, city):
    """The store's numbers/ and boundaries/ keys in the city's own cells that this run built and left with no tile."""
    from make_tiles import FINE, MAIN, area, cells_in
    flagged = [e for e in cities if e.get("streets") is True]
    keys = lambda scale, mine: {c.key for e in flagged if (e["city"] == city) == mine for c in cells_in(*area(e, 0.5), scale=scale)}
    # An outline's label point can lie past its own city's box: a margin of one 1/25° cell around the others' boxes.
    near = {f"{int(y) + i},{int(x) + j}" for y, x in (k.split(",") for k in keys(MAIN, False)) for i in (-1, 0, 1) for j in (-1, 0, 1)}
    own = {"numbers": keys(FINE, True) - keys(FINE, False), "boundaries": keys(MAIN, True) - near}
    has = lambda layer, k: os.path.exists(os.path.join(out, layer, k + ".json"))
    wrote = os.path.isdir(os.path.join(out, "boundaries")) and any(n.endswith(".json") for n in os.listdir(os.path.join(out, "boundaries")))
    built = {"numbers": lambda k: has("streets", k), "boundaries": lambda k: wrote}
    found = []
    for key in sorted(remote):
        parts = key.split("/")
        if len(parts) != 3 or parts[0] != "tiles" or parts[1] not in STALE or not parts[2].endswith(".json"): continue
        layer, k = parts[1], parts[2][:-5]
        if k in own[layer] and built[layer](k) and not has(layer, k): found.append(key)
    return found


def unshaded(remote, keys, cities_path, city):
    """The store's buildings-v2/ keys of the cells in `keys` that no area of another city builds."""
    from storage import areas
    others = {c.key for e, cells in areas(cities_path) if e["city"] != city for c in cells}
    return sorted(k for k in (f"tiles/buildings-v2/{k}.json" for k in set(keys) - others) if k in remote)


def summary(totals):
    lines = ["| Layer | New | Changed | Unchanged | Upload KB |", "|---|---:|---:|---:|---:|"]
    for layer, t in sorted(totals.items()):
        up = t["new"][1] + t["changed"][1]
        lines.append(f"| `{layer}` | {t['new'][0]:,} | {t['changed'][0]:,} | {t['unchanged'][0]:,} | {up / 1000:,.0f} |")
    return "\n".join(lines) + "\n"


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--listing", help="the store's keys and ETags (missing file: everything is new)")
    a.add_argument("--out", default="out")
    a.add_argument("--prune", action="store_true", help="delete the unchanged files from --out")
    a.add_argument("--summary", help="append the table to this file (such as $GITHUB_STEP_SUMMARY)")
    a.add_argument("--cities", default="cities.json", help="with --city: the whole list, to tell the city's own cells")
    a.add_argument("--city", help="the city this --out was built for: list its emptied numbers/ and boundaries/ keys")
    a.add_argument("--unshaded", help="with --city: make_tiles.py's --unshaded file; their buildings-v2/ keys join --stale")
    a.add_argument("--stale", default="stale.txt", help="with --city: where to write those keys, one a line (no file when none)")
    args = a.parse_args()
    remote = {}
    if args.listing and os.path.exists(args.listing):
        with open(args.listing) as f: remote = etags(f)
    # Before --prune, which removes the unchanged streets/ and numbers/ files this reads.
    gone = stale(remote, args.out, json.load(open(args.cities)), args.city) if args.city else []
    if args.city and args.unshaded and os.path.exists(args.unshaded):
        with open(args.unshaded) as f: gone += unshaded(remote, f.read().split(), args.cities, args.city)
    rows, totals = plan(local(args.out), remote)
    if args.prune:
        for _, path, s in rows:
            if s == "unchanged": os.remove(path)
    table = summary(totals)
    if gone:
        with open(args.stale, "w") as f: f.writelines(k + "\n" for k in gone)
        table += "\nEmptied this run, deleted from the store after the upload (not in a dry run): " + ", ".join(
            f"`{l}` {sum(k.split('/')[1] == l for k in gone):,}" for l in STALE + ("buildings-v2",)) + "\n"
        print("\n".join(k for k in gone if "/buildings-v2/" not in k))   # those can be tens of thousands: counted above
    print(table, end="")
    if args.summary:
        with open(args.summary, "a") as f: f.write(table)


if __name__ == "__main__":
    main()
