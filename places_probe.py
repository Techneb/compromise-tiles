"""One-off probe (2026-11-17): which style-like categories and price/rating-like fields Overture Places
carries for Paris and London. Reads the release's parquet over anonymous S3, prints Markdown to stdout.
Writes nothing else. Foursquare OS Places is not read: its S3 bucket is empty and it is gated on Hugging Face.
"""
import re
import sys

import duckdb

RELEASE = sys.argv[1] if len(sys.argv) > 1 else "2026-10-21.0"
SRC = f"s3://overturemaps-us-west-2/release/{RELEASE}/theme=places/type=place/*"
CITIES = {"Paris": (48.815, 2.25, 48.902, 2.42), "London": (51.28, -0.51, 51.70, 0.33)}
FOOD = r"(^|_)(restaurants?|bars?|cafes?|coffee|pubs?|bistros?|brasserie|tavern|lounge|bakery|food|eat_and_drink|drinks?|tapas|pizzeria|pizza|brewery|winery|wine|beer|cocktail|speakeasy|gastropub|diner|eatery|tea_room|nightclub|night_club|karaoke|jazz|biergarten|beer_garden|steakhouse|sushi|bbq|grill|canteen|buffet|bistro)(_|$)"
STYLES = ["hotel_bar", "hotel_lounge", "speakeasy", "wine_bar", "cocktail", "lounge", "dive_bar", "gastropub",
          "fine_dining", "brasserie", "bistro", "tapas", "beer_garden", "biergarten", "rooftop", "jazz", "karaoke",
          "pub", "sports_bar", "piano", "tiki", "whisky", "whiskey", "champagne", "beer_bar", "brewpub", "taproom",
          "irish", "night_club", "nightclub", "hookah", "shisha", "cabaret", "live_music", "terrace", "bar_tabac",
          "cafe_bar", "bistro_bar", "tea_room", "food_hall", "michelin", "upscale", "casual", "brunch"]
PRICE = re.compile(r"price|rating|cost|tier|popular|review|stars|confidence|hours", re.I)

con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
where = " OR ".join(f"(bbox.xmin BETWEEN {w} AND {e} AND bbox.ymin BETWEEN {s} AND {n})"
                    for s, w, n, e in CITIES.values())
city = "CASE " + " ".join(f"WHEN bbox.ymin BETWEEN {s} AND {n} AND bbox.xmin BETWEEN {w} AND {e} THEN '{c}'"
                          for c, (s, w, n, e) in CITIES.items()) + " END"
con.execute(f"CREATE TABLE t AS SELECT {city} AS city, * EXCLUDE (geometry) FROM read_parquet('{SRC}', hive_partitioning=0) WHERE {where}")
cols = {r[0]: r[1] for r in con.execute("DESCRIBE t").fetchall()}

out = [f"# Overture Places {RELEASE}: Paris and London\n", "## Schema\n"]
out += [f"- `{c}`: `{ty[:300]}`" for c, ty in cols.items()]


def has(expr):
    try:
        con.execute(f"SELECT {expr} FROM t LIMIT 1")
        return True
    except duckdb.Error:
        return False


# Every category string a place carries: the old categories struct, the taxonomy (2025+) and basic_category.
parts = [e for e in ["[categories.primary]", "categories.alternate", "[taxonomy.primary]", "taxonomy.hierarchy",
                     "taxonomy.alternates", "[basic_category]"] if has(e)]
cats = "list_distinct(list_filter(flatten([" + ", ".join(f"coalesce({p}, [])" for p in parts) + "]), lambda x: x IS NOT NULL))"
primary = next(e for e in ["taxonomy.primary", "categories.primary", "basic_category"] if has(e))
out.append(f"\nCategory fields read: {', '.join(parts)}; primary: `{primary}`.\n")
con.execute(f"CREATE TABLE f AS SELECT *, {cats} AS cats FROM t")
con.execute(f"CREATE TABLE fd AS SELECT * FROM f WHERE len(list_filter(cats, lambda x: regexp_matches(x, '{FOOD}'))) > 0")

for c in CITIES:
    total, fd = con.execute(f"SELECT (SELECT count(*) FROM t WHERE city='{c}'), (SELECT count(*) FROM fd WHERE city='{c}')").fetchone()
    out.append(f"\n## {c}\n\nAll places in the box: {total}. Food/drink places: {fd}.\n")
    out.append("### Style categories (places carrying any category containing the word; categories matched)\n")
    out.append("| word | places | categories (places) |\n|---|---|---|")
    for s in STYLES:
        n = con.execute(f"SELECT count(*) FROM f WHERE city='{c}' AND len(list_filter(cats, lambda x: regexp_matches(x, '(^|_){s}(_|$)'))) > 0").fetchone()[0]
        if n:
            names = con.execute(f"SELECT x, count(*) n FROM (SELECT unnest(cats) x FROM f WHERE city='{c}') WHERE regexp_matches(x, '(^|_){s}(_|$)') GROUP BY x ORDER BY n DESC LIMIT 8").fetchall()
            out.append(f"| {s} | {n} | " + ", ".join(f"{k} ({v})" for k, v in names) + " |")
    out.append("\n### Top 40 primary categories among food/drink places\n\n| primary | places |\n|---|---|")
    for k, v in con.execute(f"SELECT {primary} k, count(*) n FROM fd WHERE city='{c}' GROUP BY k ORDER BY n DESC LIMIT 40").fetchall():
        out.append(f"| {k} | {v} |")
    out.append("\n### Price/rating-like fields: food/drink places with a value\n\n| field | places |\n|---|---|")
    for col, ty in cols.items():
        fields = [col] if PRICE.search(col) else []
        fields += [f"{col}.{m}" for m in re.findall(r'"?(\w+)"? \w', ty) if PRICE.search(m)]
        for fld in fields:
            if has(fld):
                n = con.execute(f"SELECT count({fld}) FROM fd WHERE city='{c}'").fetchone()[0]
                out.append(f"| `{fld}` | {n} |")
            else:
                out.append(f"| `{fld}` (nested in a list) | n/a |")
    if has("confidence"):
        q = con.execute(f"SELECT round(quantile_cont(confidence, 0.25),2), round(median(confidence),2), round(quantile_cont(confidence, 0.75),2) FROM fd WHERE city='{c}'").fetchone()
        out.append(f"\nconfidence quartiles (food/drink): {q}")

print("\n".join(out))
