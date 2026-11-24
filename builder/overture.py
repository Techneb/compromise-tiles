"""Overture Places' kinds and chains on the venues of the cities whose entry says "overture": true."""
import collections, re, urllib.error, urllib.parse, urllib.request
from builder.cells import metres, padded
from builder.names import MATCH_REACH, names_match, normalised_name

# Overture Places on the venues of the areas whose cities.json entry says "overture": true (owner, 2026-11-18: "yes for Overture's data extraction. We start with
# Paris and London"; 2026-11-24: the French cities next, then the big cities elsewhere): its kind of bar, finer than OSM's amenity, as `style`; its brand's wikidata as `chain` where OSM
# names no brand; and a venue Overture holds permanently closed is left out. No Overture place becomes a venue. A place
# speaks for the one venue within MATCH_REACH whose name matches (names_match); with a second such venue it says nothing,
# and a venue two places disagree on (two kinds, closed and open) keeps OSM's alone. Overture: CDLA-Permissive 2.0; the
# sources behind these places are credited in the app under the stricter reading (README, Licences and credits).
OVERTURE_STYLES = {"cocktail_bar": "cocktail_bar", "wine_bar": "wine_bar", "speakeasy": "speakeasy", "hotel_bar": "hotel_bar",
                   "lounge": "lounge", "dive_bar": "dive_bar", "beer_bar": "beer_bar", "pub": "pub", "irish_pub": "pub",
                   "gastropub": "gastropub"}   # Overture's taxonomy.primary: the app's key
OVERTURE_BUCKET = "https://overturemaps-us-west-2.s3.amazonaws.com"
OVERTURE_CONFIDENCE = 0.6   # Overture's own confidence that an open place exists; below it the place says nothing
OVERTURE_CHAIN = 3          # places sharing a brand in the box for it to be a chain, as chains_only's least

def overture_release():
    """The newest Overture release on its public bucket ("2026-10-21.0"): releases are dropped after a few months."""
    listing = urllib.request.urlopen(OVERTURE_BUCKET + "/?list-type=2&prefix=release/&delimiter=/", timeout=60).read().decode()
    return max(re.findall(r"<Prefix>release/([0-9.-]+)/</Prefix>", listing))

def overture_places(rect):
    """Overture's eating and drinking places inside the rect, [{"name", "lat", "lon", "status", "style", "qid"}]: `status`
    Overture's operating_status, `style` OVERTURE_STYLES' key or None, `qid` the brand's wikidata or None."""
    import duckdb   # here, not at the top: the tests and every other city need no DuckDB
    s, w, n, e = rect
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    rows = con.execute(f"""SELECT names.primary, (bbox.ymin + bbox.ymax) / 2, (bbox.xmin + bbox.xmax) / 2,
            coalesce(operating_status, 'open'), taxonomy.primary, brand.wikidata, confidence
        FROM read_parquet('s3://overturemaps-us-west-2/release/{overture_release()}/theme=places/type=place/*', hive_partitioning=0)
        WHERE bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ? AND names.primary IS NOT NULL
          AND list_contains(taxonomy.hierarchy, 'food_and_drink')""", [w, e, s, n]).fetchall()
    return [overture_place(*row) for row in rows]

def overture_place(name, lat, lon, status, kind, qid, confidence):
    """One row of overture_places(): an open place below OVERTURE_CONFIDENCE gives no kind and no brand."""
    sure = status != "open" or (confidence or 0) >= OVERTURE_CONFIDENCE
    return {"name": name, "lat": lat, "lon": lon, "status": status, "style": OVERTURE_STYLES.get(kind) if sure else None,
            "qid": qid if sure else None}

def overture_facts(osm, places):
    """Writes Overture's say onto the venues of `osm` (its venues and matches tables): `style`, `chain` where OSM gives no
    brand and the brand has OVERTURE_CHAIN places in `places`, and drops the venues held permanently closed. Returns
    {fact: Counter(matched, ambiguous, unmatched)} over the places that say something, the facts "style:<key>", "chain",
    "closed" and "temporarily_closed" (counted only), chain's "added" the venues it made chains, and the dropped venues'
    names under "dropped"."""
    brands = collections.Counter(p["qid"] for p in places if p["qid"])
    claims, counts = {}, collections.defaultdict(collections.Counter)
    for p in places:   # every place, the open ones too: an open twin keeps a venue another place holds closed
        status = "closed" if p["status"] == "permanently_closed" else p["status"]
        says = [f for f in (("style:" + p["style"]) if p["style"] else None, "chain" if brands[p["qid"]] >= OVERTURE_CHAIN else None,
                            status if status != "open" else None) if f]
        wanted = normalised_name(p["name"])
        near = [v for table in (osm.venues, osm.matches) for v in osm.near(table, padded((p["lat"], p["lon"]) * 2, MATCH_REACH))
                if metres(p["lat"], p["lon"], v["coordinate"]["latitude"], v["coordinate"]["longitude"]) <= MATCH_REACH
                and names_match(normalised_name(v["name"]), wanted)]
        if len(near) != 1:
            for fact in says: counts[fact]["unmatched" if not near else "ambiguous"] += 1
            continue
        claims.setdefault(id(near[0]), (near[0], []))[1].append(says)
    dropped = set()
    for venue, said in claims.values():
        styles = {f for says in said for f in says if f.startswith("style:")}
        closed = sum("closed" in says for says in said)
        for says in said:
            for fact in says:
                torn = (fact.startswith("style:") and len(styles) > 1) or (fact == "closed" and closed < len(said))
                counts[fact]["ambiguous" if torn else "matched"] += 1
        if closed and closed == len(said):
            dropped.add(id(venue)); counts["dropped"][venue["name"]] += 1; continue
        style = next(iter(styles))[len("style:"):] if len(styles) == 1 else None
        if style and (style, venue["amenity"]) != ("pub", "pub"): venue["style"] = style   # a pub's "pub" says nothing new
        if any("chain" in says for says in said) and "brand" not in venue and not venue.get("chain"):
            venue["chain"] = True; counts["chain"]["added"] += 1
    for table in (osm.venues, osm.matches):
        for key in table: table[key] = [v for v in table[key] if id(v) not in dropped]
    return counts
