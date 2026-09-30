#!/usr/bin/env python3
"""The survey candidates: cities the counts job should count although cities.csv does not list them yet.

  python3 candidates.py build [--geonames <folder>] [--write survey-candidates.json]
  python3 candidates.py merge --counts counts.json [--counts counts-2.json …] [--write survey-candidates.json]

The coverage brainstorm (2026-09-26, items 10 and 29) gave the counts job two sources: every cities.csv
row (counts-cities.json) and every city over 300,000 in Europe, North America and Oceania. `build` makes
the second list from GeoNames (cities15000 and countryInfo, CC BY 4.0, downloaded when no folder is given):

  - population at least POPULATION, on a continent of CONTINENTS (GeoNames' country continent: Russia is
    Europe, Turkey Asia);
  - not a section of a place (feature code PPLX: Pest, Hamburg-Nord, a Bucharest sector), nor a borough in
    PART_OF (Queens, a Mexico City alcaldía);
  - not already listed: its name (or one of GeoNames' alternate names) is a counts-cities.json city of the
    same country, or its point lies inside the 24 km square the counts job counts a listed city over;
  - not inside the 24 km square of a more populous candidate (Coyoacán is Mexico City's square, Brooklyn
    New York's): its count would be that city's.

Sorted by population, most first, which is the tiebreak the survey queue keeps. `merge` copies a counts
run's goingOutCells, terraceShare and extent into the list (the run given `--cities survey-candidates.json`),
so the cities page can show the candidates that meet the survey rule before they are listed.
"""
import argparse, io, json, math, os, sys, unicodedata, urllib.request, zipfile

GEONAMES = "https://download.geonames.org/export/dump/"
POPULATION = 300_000
CONTINENTS = {"EU", "NA", "OC"}
HALF_KM = 12  # the counts job's square around a city's point (counts.HALF_KM)
COUNTRY = {"The Netherlands": "Netherlands"}  # GeoNames' name -> the one cities.csv uses
# Boroughs GeoNames files as places of their own, whose point lies just past a listed city's square (checked 2026-09-30).
PART_OF = {"Queens": "New York", "The Bronx": "New York", "Staten Island": "New York",
           "Gustavo Adolfo Madero": "Mexico City", "Manukau City": "Auckland"}  # GeoNames' name -> the one cities.csv uses
UA = {"User-Agent": "compromise-tiles (contact@alephb.uk)"}


def fold(s):
    """A name compared without case or accents ("Köln" == "koln")."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c)).strip()


def in_square(lat, lon, c):
    """Whether (lat, lon) lies inside the counts job's square around c's point."""
    dy = abs(lat - c["lat"]) * 111.32
    dx = abs(lon - c["lon"]) * 111.32 * math.cos(math.radians(c["lat"]))
    return dy <= HALF_KM and dx <= HALF_KM


def read_geonames(folder):
    """(cities15000 lines, countryInfo lines), from a folder or downloaded."""
    def text(name):
        if folder: return open(os.path.join(folder, name), encoding="utf-8").read()
        raw = urllib.request.urlopen(urllib.request.Request(GEONAMES + name, headers=UA), timeout=120).read()
        return raw.decode("utf-8")
    cities = folder and os.path.exists(os.path.join(folder, "cities15000.txt"))
    if cities: lines = open(os.path.join(folder, "cities15000.txt"), encoding="utf-8").read()
    else:
        raw = urllib.request.urlopen(urllib.request.Request(GEONAMES + "cities15000.zip", headers=UA), timeout=300).read()
        lines = zipfile.ZipFile(io.BytesIO(raw)).read("cities15000.txt").decode("utf-8")
    return lines.splitlines(), text("countryInfo.txt").splitlines()


def select(city_lines, country_lines, listed):
    """The candidates, most populous first, from GeoNames' lines and the listed cities (counts-cities.json)."""
    continent, country = {}, {}
    for line in country_lines:
        if line.startswith("#") or not line.strip(): continue
        f = line.split("\t")
        continent[f[0]], country[f[0]] = f[8], COUNTRY.get(f[4], f[4])
    names = {(fold(e["city"]), e["country"]) for e in listed}
    places = []
    for line in city_lines:
        f = line.split("\t")
        if len(f) < 19: continue
        population, code = int(f[14] or 0), f[8]
        if population < POPULATION or continent.get(code) not in CONTINENTS or f[7] == "PPLX": continue
        places.append({"city": f[1], "country": country[code], "lat": round(float(f[4]), 4), "lon": round(float(f[5]), 4),
                       "population": population, "geonames": int(f[0]),
                       "_names": {fold(f[1]), fold(f[2])} | {fold(a) for a in f[3].split(",") if a}})
    places.sort(key=lambda p: (-p["population"], p["geonames"]))
    kept = []
    for p in places:
        if p["city"] in PART_OF or any((n, p["country"]) in names for n in p["_names"]): continue
        if any(in_square(p["lat"], p["lon"], e) for e in listed if "lat" in e): continue
        if any(in_square(p["lat"], p["lon"], k) for k in kept): continue
        kept.append(p)
    for p in kept: del p["_names"]
    return kept


def merge(candidates, runs):
    """The candidates with each counts run's numbers copied in (a later run wins); `counted` is the run's date."""
    by_city = {}
    for run in runs:
        for r in run.get("cities", []):
            by_city[(r["city"], r["country"])] = {"goingOutCells": r["goingOutCells"], "terraceShare": r.get("terraceShare"),
                                                  "extent": r.get("extent"), "counted": run.get("updated")}
    return [{**c, **by_city.get((c["city"], c["country"]), {})} for c in candidates]


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("action", choices=("build", "merge"))
    a.add_argument("--geonames", default="", help="a folder holding cities15000.txt and countryInfo.txt (else downloaded)")
    a.add_argument("--listed", default="counts-cities.json")
    a.add_argument("--counts", action="append", default=[], help="a counts.json of a run over the candidates")
    a.add_argument("--write", default="survey-candidates.json")
    args = a.parse_args()
    listed = json.load(open(args.listed))
    if args.action == "build":
        previous = json.load(open(args.write)) if os.path.exists(args.write) else []
        out = select(*read_geonames(args.geonames), listed)
        # A rebuild keeps the counts already merged for the cities that stay.
        counted = {(c["city"], c["country"]): c for c in previous if "goingOutCells" in c}
        out = [{**c, **{k: v for k, v in counted.get((c["city"], c["country"]), {}).items() if k not in c}} for c in out]
    else:
        out = merge(json.load(open(args.write)), [json.load(open(p)) for p in args.counts])
    with open(args.write, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{len(out)} candidates, {sum('goingOutCells' in c for c in out)} counted -> {args.write}", file=sys.stderr)


if __name__ == "__main__":
    main()
