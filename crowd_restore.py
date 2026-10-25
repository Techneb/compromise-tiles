#!/usr/bin/env python3
"""Rebuilds the public database's Correction and SunReport records from one day of the private archive.

  python3 crowd_restore.py archive/2026-10-26                      # dry run: what would be written, per type
  python3 crowd_restore.py archive/2026-10-26 --apply              # create the records (an existing name fails, and is listed)
  python3 crowd_restore.py archive/2026-10-26 --apply --replace    # overwrite the records of the same name

Dry run unless --apply. Each archived row becomes a record of its type and recordName with its
fields; CLOUDKIT_KEY_ID and CLOUDKIT_PRIVATE_KEY as crowd.py (CLOUDKIT_ENVIRONMENT=development to try
it there first). CloudKit sets a record's creator and dates itself: the restored records are the
key's, dated the day of the restore (the archive keeps the original creator hashes and dates).
"""
import argparse, gzip, json, os, sys
from crowd import FIELD_TYPES, TYPES, cloudkit_from_env

BATCH = 200   # operations per records/modify request


def read_archive(folder):
    """The records of an archive day (corrections.jsonl.gz, sun.jsonl.gz), in that order."""
    out = []
    for name in ("corrections.jsonl.gz", "sun.jsonl.gz"):
        with gzip.open(os.path.join(folder, name), "rt") as f: out += [json.loads(l) for l in f if l.strip()]
    return out


def operations(rows, replace=False):
    """records/modify operations for the archived records, in the archive's order; other types are skipped."""
    out = []
    for r in rows:
        if r.get("recordType") not in TYPES: continue
        fields = {k: {"value": v, "type": FIELD_TYPES.get(k, "STRING")} for k, v in r["fields"].items() if k in TYPES[r["recordType"]]}
        out.append({"operationType": "forceReplace" if replace else "create",
                    "record": {"recordType": r["recordType"], "recordName": r["recordName"], "fields": fields}})
    return out


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("archive", help="an archive day folder, downloaded from the private bucket")
    a.add_argument("--apply", action="store_true", help="write to CloudKit (default: dry run)")
    a.add_argument("--replace", action="store_true", help="forceReplace records of the same name instead of failing on them")
    args = a.parse_args()
    ops = operations(read_archive(args.archive), args.replace)
    per_type = {}
    for o in ops: per_type[o["record"]["recordType"]] = per_type.get(o["record"]["recordType"], 0) + 1
    print(f"{len(ops)} records ({', '.join(f'{n} {t}' for t, n in sorted(per_type.items())) or 'none'}), "
          f"{'forceReplace' if args.replace else 'create'}")
    if not args.apply:
        print("Dry run: nothing written (--apply to write).")
        return
    ck = cloudkit_from_env()
    if ck is None: sys.exit("CLOUDKIT_KEY_ID or CLOUDKIT_PRIVATE_KEY is not set.")
    failed = 0
    for i in range(0, len(ops), BATCH):
        r = ck.post("records/modify", {"operations": ops[i:i + BATCH], "atomic": False})
        for rec in r.get("records", []):
            if "serverErrorCode" in rec:
                failed += 1
                print(f"  {rec.get('recordName')}: {rec['serverErrorCode']} {rec.get('reason', '')}")
    print(f"{len(ops) - failed} written, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
