"""French communes and départements, from the geo API, one cell at a time."""
import urllib.error, urllib.parse, urllib.request
from builder.cells import bounds, contains
from builder.net import get_json, log, SourceError

_communes = []  # [(bounds, commune, contour)] of every département loaded so far
_departements = set()

def commune(lat, lon):
    """INSEE communes from Etalab's contours, a département at a time: a point in a loaded contour
    costs nothing, else the geo API names its commune and the département's contours are loaded.
    IGN's apicarto (the same communes, Admin Express) when Etalab does not answer. [] is an answer:
    no commune."""
    for (s, w, n, e), found, contour in _communes:
        if s <= lat <= n and w <= lon <= e and contains(contour, lat, lon): return [found]
    q = urllib.parse.urlencode({"lat": lat, "lon": lon, "fields": "nom,code,codeDepartement", "format": "json"})
    try:  # once: apicarto is the retry
        answer = get_json("https://geo.api.gouv.fr/communes?" + q, waits=())
    except SourceError as first:
        try:
            q = urllib.parse.urlencode({"lat": lat, "lon": lon})
            root = get_json("https://apicarto.ign.fr/api/limites-administratives/commune?" + q)
        except SourceError as second:
            raise SourceError(f"{first}; {second}")
        return [{"nom": f["properties"]["nom_com"], "code": f["properties"]["insee_com"]} for f in root.get("features", [])]
    for c in answer:
        if c.get("codeDepartement") and c["codeDepartement"] not in _departements:
            _departements.add(c["codeDepartement"])
            q = urllib.parse.urlencode({"fields": "nom,code,contour", "format": "geojson", "geometry": "contour"})
            try: features = get_json(f"https://geo.api.gouv.fr/departements/{c['codeDepartement']}/communes?" + q)["features"]
            except SourceError as e: log(f"    communes of {c['codeDepartement']}: {e}; point by point"); continue
            _communes.extend((bounds(f["geometry"]), {"nom": f["properties"]["nom"], "code": f["properties"]["code"]}, f["geometry"])
                             for f in features if f.get("geometry"))
    return [{"nom": c["nom"], "code": c["code"]} for c in answer]
