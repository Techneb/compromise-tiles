#!/usr/bin/env python3
"""Houston's approved dog patios → permits/houston.geojson, a hand-refreshed snapshot.

The Houston Health Department publishes the food establishments with an approved dog patio (data.houstontx.gov,
"Food Establishments with HDHHS Approved Dog Patios", Open Data Commons Attribution License) as name, address
and ZIP, no point. Like Edinburgh's, the addresses are geocoded ONCE, here, into a static GeoJSON the tile
build reads whole through the `geojson` shape (with match="venues": the list dates from 2023). Placed by the
US Census Bureau's geocoder (public domain); a row it cannot place is left out. Only the name is written.

    python3 permits/geocode_houston.py [--out permits/houston.geojson]
"""

import argparse
import datetime
import json
import time
import urllib.parse
import urllib.request

FEED = "https://data.houstontx.gov/api/3/action/datastore_search?resource_id=70892746-ab7c-4b05-a4f5-2324bcb0f93c&limit=500"
CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
AGENT = "compromise-tiles/1.0 (https://github.com/Techneb/compromise-tiles; contact@alephb.uk)"


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=60) as r:
        return json.loads(r.read())


def place(address, zip_code):
    """The Census geocoder's first match as (lat, lon), or None."""
    q = urllib.parse.urlencode({"address": f"{address}, Houston, TX {zip_code}", "benchmark": "Public_AR_Current", "format": "json"})
    matches = get(f"{CENSUS}?{q}")["result"]["addressMatches"]
    return (matches[0]["coordinates"]["y"], matches[0]["coordinates"]["x"]) if matches else None


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--out", default="permits/houston.geojson")
    out = a.parse_args().out
    features, seen = [], set()
    for r in get(FEED)["result"]["records"]:
        name = " ".join((r.get("Establishment Name") or "").split())
        point = place((r.get("Address") or "").strip(), (r.get("Zip") or "").strip())
        time.sleep(1)
        if not point or (name, point) in seen: continue
        seen.add((name, point))
        features.append({"type": "Feature", "properties": {"name": name},
                         "geometry": {"type": "Point", "coordinates": [round(point[1], 6), round(point[0], 6)]}})
    with open(out, "w") as f:
        json.dump({"type": "FeatureCollection", "_source": {
            "feed": FEED, "publisher": "City of Houston, Houston Health Department",
            "licence": "Open Data Commons Attribution License", "geocoder": "US Census Bureau Geocoder (public domain)",
            "downloaded": datetime.date.today().isoformat(), "kept": "every approved dog patio the geocoder placed"},
            "features": features}, f, ensure_ascii=False)
    print(f"{len(features)} patios written to {out}")


if __name__ == "__main__":
    main()
