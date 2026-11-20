"""places/: neighbourhood names, their levels and their English and local names."""
from builder.cells import contains, metres
from builder.shapes import vertex
from builder.names import normalised_name
from builder.osmium import mean_point

def english(place, name_en):
    """The place with "name_en", OSM's name:en, when it has one that is not its "name"."""
    name_en = str(name_en or "").strip()
    if name_en and name_en != place["name"]: place["name_en"] = name_en
    return place

PLACE_TWIN = 1000  # metres: the same name this close is one place mapped twice (a node and its area)

def distinct_places(places):
    """Each place once: a name met again within PLACE_TWIN of a kept one is left out, so the first
    wins (osmium exports nodes before areas: the mapped point over a polygon's mean vertex)."""
    kept = []
    for p in places:
        at = (p["coordinate"]["latitude"], p["coordinate"]["longitude"])
        if not any(k["name"] == p["name"] and metres(*at, k["coordinate"]["latitude"], k["coordinate"]["longitude"]) <= PLACE_TWIN
                   for k in kept):
            kept.append(p)
    return kept

# Boundary relations under a municipality that no place stands for are written as places of their own,
# the kind their admin_level stands for when they carry no place tag: Paris's 20 arrondissements (9)
# and 80 quartiers administratifs (10), mapped as boundaries far more often than as places.
PLACE_ADMIN_KINDS = {9: "suburb", 10: "quarter"}
PLACE_NAME_LEADS = {"QUARTIER", "DE", "DU", "DES", "D", "LA", "LE", "LES", "L"}

def place_key(name):
    """A name as places compare it: normalised_name() without the leading "Quartier" and articles, so
    "Quartier de la Goutte-d'Or" and "La Goutte-d'Or" meet. A name of nothing but those words stays whole."""
    words = normalised_name(name).split(" ")
    i = 0
    while i < len(words) - 1 and words[i] in PLACE_NAME_LEADS: i += 1
    return " ".join(words[i:])

def place_levels(places, boundaries):
    """The places (each with "osm": its "n…", "w…" or "r…" id) with "admin_level" when the name comes from or
    matches an administrative boundary (OSM.boundaries()): the place is the relation itself, or is the
    relation's label node, or its admin_centre node under a name one holds the other ("17e Arrondissement",
    "Paris 17e Arrondissement") at a level of PLACE_ADMIN_KINDS (a municipality's admin_centre is its seat:
    London's suburb Hackney is not the borough), or lies inside it under the same place_key(). The deepest level wins. A
    relation that is a place itself gives way to its label or admin_centre node when that is a place too
    (the node is the name as mapped). Then each boundary of PLACE_ADMIN_KINDS no place matched, whole in the
    extract, is added at its mean vertex. "osm" is not written."""
    by_id, by_node, by_key = {b["id"]: b for b in boundaries}, {}, {}
    for b in boundaries:
        for n in b["labels"] | b["centres"]: by_node.setdefault(n, []).append(b)
        by_key.setdefault(place_key(b["name"]), []).append(b)
    def stands_for(p, b):
        if p["osm"] in b["labels"]: return True
        if p["osm"] not in b["centres"] or b["admin_level"] not in PLACE_ADMIN_KINDS: return False
        a, z = normalised_name(p["name"]), normalised_name(b["name"])
        return a in z or z in a
    places_by_id = {p["osm"]: p for p in places}
    out, used = [], set()
    for p in places:
        own = by_id.get(p["osm"])
        if own and any(stands_for(places_by_id[n], own) for n in own["labels"] | own["centres"] if n in places_by_id):
            used.add(own["id"]); continue
        lat, lon = p["coordinate"]["latitude"], p["coordinate"]["longitude"]
        found = {own["id"]: own} if own else {}
        found.update((b["id"], b) for b in by_node.get(p["osm"], ()) if stands_for(p, b))
        found.update((b["id"], b) for b in by_key.get(place_key(p["name"]), ()) if b["geometry"] and contains(b["geometry"], lat, lon))
        entry = {k: v for k, v in p.items() if k != "osm"}
        if found:
            entry["admin_level"] = max(b["admin_level"] for b in found.values())
            used.update(found)
        out.append(entry)
    for b in boundaries:
        if b["id"] in used or b["admin_level"] not in PLACE_ADMIN_KINDS or not b["geometry"]: continue
        at = mean_point(b["geometry"])
        if not at: continue
        kind = b["place"] if b["place"] in PLACE_KINDS else PLACE_ADMIN_KINDS[b["admin_level"]]
        out.append(english({"name": b["name"], "kind": kind, "coordinate": vertex(*at), "admin_level": b["admin_level"]}, b.get("name_en")))
    return out
# places/: the names a neighbourhood goes by, from the widest to the narrowest.
PLACE_KINDS = ("suburb", "quarter", "neighbourhood")
