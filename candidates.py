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
  A candidate named as a listed city of another country is named with its country ("London (Canada)"): the
  counts job looks cities.json boxes up by name, and counted London, Canada over London's box on 2026-09-30.

Then a France pass (owner, 2026-10-02): every French commune of at least FRANCE_POPULATION (the Etalab geo
API's communes and populations, Licence Ouverte 2.0) that counts-cities.json and the list above do not hold,
with its town hall as its point (the commune's centre when it has none). No square rule here: the counts job
counts a French city inside its own commune's contour, so Montreuil is counted apart from Paris. A commune
is already listed when a listed French city of its name has its point within the 24 km square (Saint-Denis,
Seine-Saint-Denis); a namesake elsewhere is named with its département ("Saint-Denis (La Réunion)").
`python3 candidates.py france` redoes this pass alone over the current list, leaving the rest untouched.

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
GEO_API = "https://geo.api.gouv.fr/communes?fields=nom,code,population,centre,mairie,codeDepartement&format=json"
FRANCE_POPULATION = 50_000
# Overseas départements and collectivities: their own time zone and a name to tell a namesake apart; metropolitan France is Europe/Paris.
OVERSEAS = {"971": ("America/Guadeloupe", "Guadeloupe"), "972": ("America/Martinique", "Martinique"),
            "973": ("America/Cayenne", "Guyane"), "974": ("Indian/Reunion", "La Réunion"), "976": ("Indian/Mayotte", "Mayotte"),
            "987": ("Pacific/Tahiti", "Polynésie française"), "988": ("Pacific/Noumea", "Nouvelle-Calédonie")}


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
                       "population": population, "timezone": f[17], "geonames": int(f[0]),
                       "_names": {fold(f[1]), fold(f[2])} | {fold(a) for a in f[3].split(",") if a}})
    places.sort(key=lambda p: (-p["population"], p["geonames"]))
    kept = []
    for p in places:
        if p["city"] in PART_OF or any((n, p["country"]) in names for n in p["_names"]): continue
        if any(in_square(p["lat"], p["lon"], e) for e in listed if "lat" in e): continue
        if any(in_square(p["lat"], p["lon"], k) for k in kept): continue
        kept.append(p)
    listed_names = {e["city"] for e in listed}
    for p in kept:
        del p["_names"]
        # A listed city's name in another country (London, Canada): the counts job, cities.json and the status
        # feed key cities by name, so the candidate carries its country from the start.
        if p["city"] in listed_names: p["city"] = f"{p['city']} ({p['country']})"
    return kept


def read_communes():
    """Every French commune from the geo API: [{nom, code, population, centre, mairie, codeDepartement}]."""
    return json.load(urllib.request.urlopen(urllib.request.Request(GEO_API, headers=UA), timeout=300))


def france(communes, listed, kept):
    """The French communes of at least FRANCE_POPULATION that neither listed (counts-cities.json) nor kept (the
    candidates so far) holds, most populous first; each carries its INSEE code instead of a GeoNames id."""
    taken = [e for e in list(listed) + list(kept) if e.get("country") == "France" and "lat" in e]
    names = {e["city"] for e in list(listed) + list(kept)}
    out = []
    for c in sorted(communes, key=lambda c: (-(c.get("population") or 0), c["code"])):
        if (c.get("population") or 0) < FRANCE_POPULATION: continue
        point = (c.get("mairie") or c.get("centre") or {}).get("coordinates")
        if not point: continue
        lon, lat = round(point[0], 4), round(point[1], 4)
        if any(fold(e["city"]) == fold(c["nom"]) and in_square(lat, lon, e) for e in taken): continue
        zone, region = OVERSEAS.get(c["codeDepartement"], ("Europe/Paris", None))
        name = c["nom"] if c["nom"] not in names else f"{c['nom']} ({region or c['codeDepartement']})"
        names.add(name)
        out.append({"city": name, "country": "France", "lat": lat, "lon": lon, "population": c["population"],
                    "timezone": zone, "insee": c["code"]})
    return out


def keep_counts(out, previous):
    """A rebuild keeps the counts already merged for the cities that stay (same name, same country)."""
    counted = {(c["city"], c["country"]): c for c in previous if "goingOutCells" in c}
    out = sorted(out, key=lambda c: -c["population"])  # one list, most populous first, the French pass included
    return [{**c, **{k: v for k, v in counted.get((c["city"], c["country"]), {}).items() if k not in c}} for c in out]


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
    a.add_argument("action", choices=("build", "france", "merge"))
    a.add_argument("--geonames", default="", help="a folder holding cities15000.txt and countryInfo.txt (else downloaded)")
    a.add_argument("--listed", default="counts-cities.json")
    a.add_argument("--counts", action="append", default=[], help="a counts.json of a run over the candidates")
    a.add_argument("--write", default="survey-candidates.json")
    args = a.parse_args()
    listed = json.load(open(args.listed))
    previous = json.load(open(args.write)) if os.path.exists(args.write) else []
    if args.action == "build":
        out = select(*read_geonames(args.geonames), listed)
        out = keep_counts(out + france(read_communes(), listed, out), previous)
    elif args.action == "france":  # the France pass alone: the other candidates stay as they are
        out = [c for c in previous if "insee" not in c]
        out = keep_counts(out + france(read_communes(), listed, out), previous)
    else:
        out = merge(json.load(open(args.write)), [json.load(open(p)) for p in args.counts])
    with open(args.write, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{len(out)} candidates, {sum('goingOutCells' in c for c in out)} counted -> {args.write}", file=sys.stderr)


if __name__ == "__main__":
    main()
