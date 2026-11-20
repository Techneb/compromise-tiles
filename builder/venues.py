"""venues/: OpenStreetMap's bars, cafés and restaurants, their details, hotels and chains."""
import collections, json, os, re
from builder import ROOT
from builder.cells import bounds, contains
from builder.shapes import outer_rings
from builder.names import local_names, normalised_name
from builder.osmium import buckets, features, osmium

AMENITIES = ("bar", "pub", "biergarten", "cafe", "restaurant")  # every venue type
# In venues/ tiles only, for the app to match an Apple venue to its OSM twin and read its tags; never listed as a spot (2026-10-24).
MATCH_ONLY = ("fast_food", "ice_cream", "nightclub")

LIFECYCLE = ("disused", "abandoned", "was", "demolished", "removed", "razed", "destroyed")  # OSM's lifecycle prefixes

def closed(tags):
    """A venue OSM says is shut (owner, 2026-11-19, the Athens audit; left out of venues/): opening_hours off or closed,
    disused or abandoned yes, or a lifecycle prefix on its own amenity or its own name (disused:amenity=cafe beside
    amenity=cafe, was:name its name). A prefix on another amenity is the place's past, not a closure (amenity=cafe beside
    disused:amenity=restaurant: a restaurant turned café). A prefixed amenity alone never reaches here (tags-filter)."""
    if str(tags.get("opening_hours") or "").strip().lower() in ("off", "closed"): return True
    if "yes" in (tags.get("disused"), tags.get("abandoned")): return True
    own = {"amenity": tags.get("amenity"), "name": str(tags.get("name") or "").strip()}
    return any(own[key] and str(tags.get(f"{prefix}:{key}") or "").strip() == own[key] for prefix in LIFECYCLE for key in own)

def read_venues(pbf, kept, amenities=AMENITIES):
    """The venues (`amenities`) of a pbf, an area at its outer ring's mean vertex as the terrace permits; `kept` is a scratch file."""
    osmium("tags-filter", pbf, "nwr/amenity=" + ",".join(amenities), "-o", kept, "--overwrite")
    out = []
    for feature in features(kept, ids=True):
        tags, geometry = feature["properties"], feature["geometry"]
        if tags.get("amenity") not in amenities or closed(tags): continue
        if geometry["type"] == "Point": lon, lat = geometry["coordinates"][:2]
        else:
            ring = next(outer_rings(geometry), None)
            if not ring: continue
            lat, lon = (sum(v[k] for v in ring) / len(ring) for k in ("latitude", "longitude"))
        venue = {"name": str(tags.get("name", "")).strip(), "coordinate": {"latitude": round(lat, 6), "longitude": round(lon, 6)},
                 "amenity": tags["amenity"], "outdoor_seating": seating(tags.get("outdoor_seating"), rooftop(tags)), "source": "osm"}
        venue.update(local_names(tags))
        if drinks_late(tags): venue["bar"] = True
        if opening_hours(tags): venue["opening_hours"] = opening_hours(tags)
        venue.update(details(tags))
        if str(tags.get("brand:wikidata") or "").strip(): venue["_qid"] = str(tags["brand:wikidata"]).split(";")[0].strip()
        # The element, for the app's "Suggest an edit" to open its edit page (backlog, Keep OSM ids in venue tiles).
        if tags.get("@type") in ("node", "way", "relation") and tags.get("@id"): venue.update(osm_type=tags["@type"][0], osm_id=int(tags["@id"]))
        out.append(venue)
    return out

def listed(value, *words):
    """Whether an OSM value, or one entry of a ;-list, is one of the words (so "roof" but not "roofed")."""
    return any(v.strip() in words for v in str(value or "").split(";"))

def rooftop(tags):
    """Tagged as on a roof by location=roof, roof_terrace=yes or terrace=roof; rooftop=yes tags helipads."""
    return listed(tags.get("location"), "roof") or tags.get("roof_terrace") == "yes" or listed(tags.get("terrace"), "roof")

def seating(tag, on_roof=False):
    """OpenStreetMap's outdoor_seating as the tile carries it: true/false for yes/no, and the value
    itself when it names where the seats are (roof, terrace, patio…), so a reader can tell a rooftop;
    "roof" when another tag puts the venue on a roof and the value does not already say so."""
    if on_roof: return tag if listed(tag, "roof", "rooftop") else "roof"
    if tag in (None, "", "no"): return False
    if tag == "yes": return True
    return str(tag)

def drinks_late(tags):
    """A café or restaurant that is a bar in all but name, counted under Bar by the app: tagged
    bar=yes, or open past midnight on some day (owner, 2026-10-10: La Perle and Le Progrès in the
    Marais are a restaurant and a café closing at 02:00)."""
    return tags.get("amenity") in ("cafe", "restaurant") and (tags.get("bar") == "yes" or open_past_midnight(tags.get("opening_hours")))

def opening_hours(tags):
    """OpenStreetMap's opening_hours as the tile carries it, raw for the app to parse (owner,
    2026-10-13): trimmed, None when absent, blank or over 255 characters (OSM's own tag limit)."""
    value = str(tags.get("opening_hours") or "").strip()
    return value if 0 < len(value) <= 255 else None

def details(tags):
    """The OSM tags behind a spot's subtitle (owner, 2026-10-20), short and left out when absent:
    cuisine as up to three lowercased ;-separated values, sport's first value, and True for
    microbrewery, live_music and cocktails tagged yes, drink:wine/drink:beer tagged yes or served, and
    diet:vegan, diet:vegetarian and diet:gluten_free as their value when yes or only. They also feed
    the app's keyword match (cocktail, pizza, vegan…). fine_dining and hotel feed the app's style labels (2026-11-17)."""
    out = {}
    cuisine = [v.strip().lower() for v in str(tags.get("cuisine") or "").split(";") if v.strip()][:3]
    if cuisine: out["cuisine"] = ";".join(cuisine)
    sport = next((v.strip().lower() for v in str(tags.get("sport") or "").split(";") if v.strip()), None)
    if sport: out["sport"] = sport
    for key in ("microbrewery", "live_music", "cocktails"):
        if tags.get(key) == "yes": out[key] = True
    for key in ("drink:wine", "drink:beer"):
        if tags.get(key) in ("yes", "served"): out[key] = True
    for key in ("diet:vegan", "diet:vegetarian", "diet:gluten_free"):
        if tags.get(key) in ("yes", "only"): out[key] = tags[key]
    # A venue's character (owner, 2026-10-21): True for real_ale, beer_garden, karaoke, brunch and live_sport (2026-10-22) tagged yes, real_fire
    # unless no, quiz whatever its value but no; food as yes/no; the brewery when it is one display name, not a tap
    # list or an id ("1664;leffe", "guinness", "local"; 2026-10-24);
    # internet_access as True for Wi-Fi; specialty_coffee from cuisine, coffee or coffee:specialty; the brand (chains only: OSM).
    for key in ("real_ale", "beer_garden", "karaoke", "brunch", "live_sport"):
        if tags.get(key) == "yes": out[key] = True
    for key in ("real_fire", "quiz"):
        if str(tags.get(key) or "no").strip().lower() != "no": out[key] = True
    if tags.get("food") in ("yes", "no"): out["food"] = tags["food"] == "yes"
    brewery = str(tags.get("brewery") or "").strip()
    if ";" not in brewery and brewery != brewery.lower() and brewery.lower() not in ("various", "multiple"): out["brewery"] = brewery
    if tags.get("internet_access") in ("wlan", "yes", "wifi"): out["internet_access"] = True
    every = {v.strip().lower() for v in str(tags.get("cuisine") or "").split(";")}
    if every & {"specialty_coffee", "speciality_coffee"} or tags.get("coffee") == "specialty" or tags.get("coffee:specialty") == "yes":
        out["specialty_coffee"] = True
    if str(tags.get("brand") or "").strip(): out["brand"] = str(tags["brand"]).strip()
    # Venue style (owner, 2026-11-17): fine_dining True for a starred restaurant, hotel 4 or 5 for a venue that is a 4–5★ hotel too.
    if fine_dining(tags): out["fine_dining"] = True
    if hotel_class(tags): out["hotel"] = hotel_class(tags)
    return out

def fine_dining(tags):
    """A restaurant OSM's mappers give one to three stars (`stars`, else `award:michelin`): their copy of a guide's, which the
    app shows only as "Fine dining", never by the guide's name (owner, 2026-11-17). Not on an element that is a hotel too, whose
    stars are the hotel's; "0", "Bib gourmand" and the rest are no star."""
    if tags.get("amenity") != "restaurant" or tags.get("tourism") == "hotel": return False
    return any(str(tags.get(key) or "").strip() in ("1", "2", "3") for key in ("stars", "award:michelin"))

def hotel_class(tags):
    """A tourism=hotel element's stars, 4 or 5 ("4S" too), else None: the hotels whose bars and restaurants the app labels."""
    stars = str(tags.get("stars") or "").strip().rstrip("Ss")
    return int(stars) if tags.get("tourism") == "hotel" and stars in ("4", "5") else None

def hotels(pbf, kept):
    """The 4–5★ hotels of a pbf drawn as areas, [(stars, geometry, bounds)]; `kept` is a scratch file."""
    osmium("tags-filter", pbf, "wr/tourism=hotel", "-o", kept, "--overwrite")
    return [(hotel_class(f["properties"]), f["geometry"], bounds(f["geometry"])) for f in features(kept, "polygon")
            if hotel_class(f["properties"]) and f["geometry"]["type"] in ("Polygon", "MultiPolygon")]

def in_hotels(venues, areas):
    """Sets `hotel` (the stars) on a venue inside one of `hotels()`' areas: a hotel's bar or restaurant (owner, 2026-11-17).
    A venue that is a hotel itself keeps its own."""
    index = {}
    for area in areas:
        for key in buckets(*area[2]): index.setdefault(key, []).append(area)
    for v in venues:
        lat, lon = v["coordinate"]["latitude"], v["coordinate"]["longitude"]
        stars = next((a[0] for a in index.get(buckets(lat, lon, lat, lon)[0], []) if a[2][0] <= lat <= a[2][2] and a[2][1] <= lon <= a[2][3]
                      and contains(a[1], lat, lon)), None)
        if stars and "hotel" not in v: v["hotel"] = stars
    return venues

_NSI = None
def nsi_brands():
    """nsi_brands.json's brands (nsi_table.py): {brand:wikidata: [brand, cuisine, amenity, folded names]}."""
    global _NSI
    if _NSI is None:
        with open(os.path.join(ROOT, "nsi_brands.json")) as f: _NSI = json.load(f)["brands"]
    return _NSI

def chains_only(venues, least=3, brands=None):
    """Names a venue's chain from the name-suggestion-index (S5, owner 2026-10-24): by its brand:wikidata, else by its name when a
    venue of the same extract carries that brand's wikidata (so a local "Paul" is no chain far from one), filling a missing brand
    and cuisine. Then drops the brand of a venue whose brand fewer than `least` of `venues` share, or whose name already says it,
    either holding the other ("GAIL's" for "GAIL's Bakery"): a brand on a row marks a chain, so an independent stands out by
    having none (owner, 2026-10-21)."""
    brands = nsi_brands() if brands is None else brands
    here = {v["_qid"] for v in venues if v.get("_qid") in brands}
    by_name = {}
    for qid in sorted(here):
        for name in brands[qid][3]: by_name.setdefault(name, qid)
    for v in venues:
        qid = v.pop("_qid", None)
        qid = qid if qid in brands else by_name.get(normalised_name(v["name"]))
        if not qid: continue
        brand, cuisine = brands[qid][:2]
        v.setdefault("brand", brand)
        if cuisine and "cuisine" not in v: v["cuisine"] = ";".join(cuisine.split(";")[:3])
    shared = collections.Counter(v.get("brand") for v in venues)
    for v in venues:
        if "brand" not in v: continue
        # chain stays where the brand goes for saying the name (owner, 2026-11-17): what "Hide chains" reads.
        if shared[v["brand"]] >= least: v["chain"] = True
        brand, name = normalised_name(v["brand"]), normalised_name(v["name"])
        if shared[v["brand"]] < least or brand in name or name in brand: del v["brand"]
    return venues

_DAY = r"(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:\[[-0-9,]+\])?"
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_SELECTOR = re.compile(rf"(?:{_DAY}(?:-{_DAY})?|PH|SH|{_MONTH}(?:\s?\d{{1,2}})?(?:-{_MONTH}?(?:\s?\d{{1,2}})?)?|\d{{4}}(?:-\d{{4}})?|week\s?\d{{1,2}}(?:-\d{{1,2}})?)")
_SPAN = r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})"
_TIMES = re.compile(rf"{_SPAN}(?:\s*,\s*{_SPAN})*")

def open_past_midnight(value):
    """Whether an opening_hours value keeps the place open after midnight on some day: "24/7", a
    range that wraps (18:00-02:00) or runs past 24:00 (18:00-26:00), or a whole day (00:00-24:00).
    A rule ending "off" or "closed" opens nothing, one for public or school holidays alone (PH,
    SH) is ignored, and anything this strict reading cannot parse — sunset, open ends, typos — is
    False: a guess would put a restaurant under Bar."""
    if not value: return False
    late = False
    # Rules part at ";", "||", and at a comma after a time ("Mo-Fr 06:00-02:00, Sa-Su 08:00-02:00").
    for rule in re.split(r";|\|\||(?<=\d),\s*(?=[A-Za-z])", value):
        rule = re.sub(r'\s*"[^"]*"\s*$', "", rule).strip()  # a trailing comment
        if rule == "24/7": late = True
        if not rule or rule == "24/7": continue
        words = rule.split()
        if words[-1] in ("off", "closed"): continue
        if words[-1] == "open": words = words[:-1]
        cut = next((i for i in range(len(words)) if _TIMES.fullmatch(" ".join(words[i:]))), len(words))
        selectors = [t for t in re.split(r"[\s,]+", " ".join(words[:cut])) if t]
        if not all(_SELECTOR.fullmatch(t) for t in selectors): return False
        if cut == len(words) or (selectors and all(t in ("PH", "SH") for t in selectors)): continue
        for h1, m1, h2, m2 in (map(int, span) for span in re.findall(_SPAN, " ".join(words[cut:]))):
            if h1 > 24 or h2 > 48 or m1 > 59 or m2 > 59: return False
            start, end = h1 * 60 + m1, h2 * 60 + m2
            if end > 24 * 60 or 0 < end < start or (start == 0 and end == 24 * 60): late = True
    return late
