"""python3 test_candidates.py — the survey candidates' selection on made-up GeoNames lines, no network."""
from candidates import fold, in_square, merge, select

def line(gid, name, lat, lon, code, feature, population, alt=""):
    f = [""] * 19
    f[0], f[1], f[2], f[3], f[4], f[5], f[7], f[8], f[14] = str(gid), name, name, alt, str(lat), str(lon), feature, code, str(population)
    return "\t".join(f)

countries = ["#ISO\tISO3\tnum\tfips\tCountry\tCapital\tArea\tPop\tContinent",
             "\t".join(["DE", "", "", "", "Germany", "", "", "", "EU"]),
             "\t".join(["NL", "", "", "", "The Netherlands", "", "", "", "EU"]),
             "\t".join(["BR", "", "", "", "Brazil", "", "", "", "SA"])]
listed = [{"city": "Cologne", "country": "Germany", "lat": 50.94, "lon": 6.96},
          {"city": "Berlin", "country": "Germany", "lat": 52.52, "lon": 13.40}]
cities = [line(1, "Köln", 50.93, 6.95, "DE", "PPLA2", 1_000_000, "Cologne,Koeln"),   # listed, by name and square
          line(2, "Leverkusen", 51.03, 6.98, "DE", "PPLA3", 310_000),               # inside Cologne's square
          line(3, "Hamburg", 53.55, 10.0, "DE", "PPLA", 1_800_000),
          line(4, "Hamburg-Nord", 53.59, 10.0, "DE", "PPLX", 315_000),             # a section of a place
          line(5, "Norderstedt", 53.62, 10.0, "DE", "PPL", 400_000),               # inside Hamburg's square
          line(6, "Kiel", 54.32, 10.13, "DE", "PPLA", 299_999),                    # under the floor
          line(7, "Recife", -8.05, -34.9, "BR", "PPLA", 1_600_000),                # South America
          line(8, "Eindhoven", 51.44, 5.47, "NL", "PPL", 300_000),
          line(9, "Queens", 53.0, 8.8, "DE", "PPLA2", 2_000_000)]                  # a borough in PART_OF
got = select(cities, countries, listed)
assert [c["city"] for c in got] == ["Hamburg", "Eindhoven"], got                  # most populous first
assert got[1]["country"] == "Netherlands" and got[0]["population"] == 1_800_000 and got[0]["geonames"] == 3
assert fold("Köln") == "koln" and fold(" Łódź") == "łodz"
assert in_square(52.62, 13.40, listed[1]) and not in_square(52.63, 13.40, listed[1])   # 11.1 km in, 12.2 km out
# merge: a counts run's numbers join by city and country; the uncounted stay without them.
runs = [{"updated": "2026-09-30", "cities": [{"city": "Hamburg", "country": "Germany", "goingOutCells": 400, "terraceShare": 0.3, "extent": "24 km square"}]}]
merged = merge(got, runs)
assert merged[0]["goingOutCells"] == 400 and merged[0]["counted"] == "2026-09-30" and "goingOutCells" not in merged[1]
print("ok")
