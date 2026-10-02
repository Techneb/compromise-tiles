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
"""
import argparse, hashlib, os

STATES = ("new", "changed", "unchanged")


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
    args = a.parse_args()
    remote = {}
    if args.listing and os.path.exists(args.listing):
        with open(args.listing) as f: remote = etags(f)
    rows, totals = plan(local(args.out), remote)
    if args.prune:
        for _, path, s in rows:
            if s == "unchanged": os.remove(path)
    table = summary(totals)
    print(table, end="")
    if args.summary:
        with open(args.summary, "a") as f: f.write(table)


if __name__ == "__main__":
    main()
