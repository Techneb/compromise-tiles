#!/usr/bin/env python3
"""Writes nsi_brands.json, the chain brands make_tiles.py recognises (subtitle S5, owner 2026-10-24), from one pinned release
of the name-suggestion-index (BSD-3-Clause; its notice rides in the file). Run by hand when NSI is to be bumped, never by a build:
python3 nsi_table.py [version]."""
import json, sys, urllib.request
from make_tiles import normalised_name

VERSION = sys.argv[1] if len(sys.argv) > 1 else "8.0.20260918"
URL = f"https://cdn.jsdelivr.net/npm/name-suggestion-index@{VERSION}/dist/json/nsi.min.json"
KINDS = ("restaurant", "cafe", "fast_food", "bar", "pub", "ice_cream")

def table(nsi):
    """{brand:wikidata: [brand, cuisine or "", amenity, [folded names]]} for the amenities the tiles keep."""
    out = {}
    for kind in KINDS:
        for item in nsi[f"brands/amenity/{kind}"]["items"]:
            tags = item["tags"]; qid = tags.get("brand:wikidata")
            if not qid: continue
            brand = tags.get("brand") or tags.get("name") or item["displayName"]
            names = {normalised_name(n) for n in [tags.get("name"), brand, item["displayName"], *item.get("matchNames", [])] if n}
            cuisine = ";".join(v.strip().lower() for v in str(tags.get("cuisine") or "").split(";") if v.strip())
            out.setdefault(qid, [brand, cuisine, kind, sorted(n for n in names if n)])
    return out

if __name__ == "__main__":
    with urllib.request.urlopen(URL) as r: nsi = json.load(r)["nsi"]
    with urllib.request.urlopen(f"https://cdn.jsdelivr.net/npm/name-suggestion-index@{VERSION}/LICENSE.md") as r: licence = r.read().decode()
    json.dump({"_source": URL, "_licence": licence, "brands": table(nsi)}, open("nsi_brands.json", "w"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
