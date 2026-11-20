"""Venue-name matching, shared by the permits, the chains and Overture."""
import unicodedata

NAME_LANGUAGES = ("en", "fr", "es", "de")  # the app's languages

def local_names(tags):
    """OSM's name:<lang> for the app's languages, as name_<lang>, each only when it differs from name (as places/' name_en):
    the app shows the one of its language, or name_en for a name in a script its language does not write (2026-11-19)."""
    name, out = str(tags.get("name") or "").strip(), {}
    for lang in NAME_LANGUAGES:
        value = str(tags.get(f"name:{lang}") or "").strip()
        if value and value != name: out[f"name_{lang}"] = value
    return out

# A snapshot's permit survives only where a venue of today stands under its name (Los Angeles, a January 2021 list,
# 2026-10-05). The venues are the published venues/ tiles (OpenStreetMap's: Apple's cannot be read from a build), within
# MATCH_REACH metres under a matching name — the app's own rule (TerraceNames.swift: normalised, namesMatch), ported so a
# permit kept here is one the app can hand to the venue. A permit with no name, or no venue tile, is dropped.
MATCH_REACH = 60

PLACE_WORDS = {
    "BAR", "BARS", "CAFE", "CAFES", "COFFEE", "PUB", "RESTAURANT", "RESTAURANTE", "RISTORANTE", "BRASSERIE",
    "BISTRO", "CERVECERIA", "TABERNA", "TAVERNA", "KNEIPE", "TRATTORIA", "OSTERIA", "THE", "LE", "LA", "LES",
    "EL", "LOS", "LAS", "IL", "DE", "DU", "DES", "DEL", "DI", "DER", "DIE", "DAS", "Y", "ET", "E", "AND", "UND",
}

def normalised_name(name):
    """Uppercased, accents and punctuation stripped, spaces collapsed: "Café Oz" and "CAFE OZ - THE AUSTRALIAN BAR" meet."""
    if not name: return ""
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", name) if not unicodedata.combining(ch)).upper()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded).split())

def names_match(lhs, rhs):
    """One name contains the other, or they share their first two words, or every word of the shorter is in the longer once
    words that name a kind of place and bare numbers are set aside — one short shared word is not a match."""
    if not lhs or not rhs: return False
    if rhs in lhs or lhs in rhs: return True
    left, right = lhs.split(" ")[:2], rhs.split(" ")[:2]
    if len(left) == 2 and left == right: return True
    core = lambda name: {w for w in name.split(" ") if w not in PLACE_WORDS and not w.isdigit()}
    small, big = sorted([core(lhs), core(rhs)], key=len)
    if not (len(small) >= 2 or (len(small) == 1 and len(next(iter(small))) >= 5)): return False
    return small <= big

def folded(text):
    """Lower case without diacritics: "Ärendekategori" → "arendekategori"."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).lower()
