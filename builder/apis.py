"""The open-data APIs the feeds speak: WFS, ArcGIS, Opendatasoft, Socrata."""
import datetime, json, urllib.error, urllib.parse, urllib.request
from builder.net import get, get_json

def wfs(url, params, rect, cap=5000, depth=0):
    """A WFS 2.0 GetFeature over a box (latitude first, EPSG:4326 URN); a box that hits the cap is split in four."""
    s, w, n, e = rect
    # params may override VERSION (Zurich's QGIS server answers only 1.1.0).
    q = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "COUNT": str(cap), **params,
         "BBOX": f"{s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326"}
    features = get_json(url + "?" + urllib.parse.urlencode(q)).get("features", [])
    if len(features) < cap or depth >= 3: return features
    ms, me = (s + n) / 2, (w + e) / 2
    return [f for part in ((s, w, ms, me), (s, me, ms, e), (ms, w, n, me), (ms, me, n, e))
            for f in wfs(url, params, part, cap, depth + 1)]

def arcgis(url, rect, fields, where="1=1", oid="OBJECTID"):
    """An ArcGIS REST envelope query in WGS84, paged on the layer's OID field (resultOffset repeats and skips rows on SITG)."""
    s, w, n, e = rect
    def parse(raw):
        root = json.loads(raw)
        if "error" in root: raise ValueError(root["error"])
        return root
    out, last = [], -1   # a shapefile's FID starts at 0 (San Sebastián's first terrace)
    while True:
        q = urllib.parse.urlencode({"where": f"({where}) AND {oid}>{last}", "geometry": f"{w},{s},{e},{n}",
                                    "geometryType": "esriGeometryEnvelope", "inSR": "4326", "outSR": "4326",
                                    "outFields": ",".join([oid] + fields), "f": "geojson", "orderByFields": oid})
        page = get(url + "/query?" + q, parse=parse)
        features = page.get("features") or []
        out += features
        # A FeatureServer flags truncation under "properties", a MapServer (Wrocław) at the top level.
        more = page.get("exceededTransferLimit") or (page.get("properties") or {}).get("exceededTransferLimit")
        if not features or not more: return out
        last = max(f["properties"][oid] for f in features)

def opendatasoft(host, dataset, where, select):
    q = urllib.parse.urlencode({"where": where, "select": select, "limit": "-1"})
    return get_json(f"https://{host}/api/explore/v2.1/catalog/datasets/{dataset}/exports/geojson?" + q).get("features") or []

def in_bbox(point, rect):
    s, w, n, e = rect
    return f"in_bbox({point}, {s}, {w}, {n}, {e})"

def intersects(geometry, rect):
    s, w, n, e = rect
    return f"intersects({geometry}, 'POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))')"

def socrata(host, dataset, where, select):
    """A Socrata dataset as GeoJSON, paged: SoQL numbers come back as strings."""
    out, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"$where": where, "$select": select, "$limit": "2000", "$offset": str(offset), "$order": ":id"})
        page = get_json(f"https://{host}/resource/{dataset}.geojson?" + q).get("features") or []
        out += page
        if len(page) < 2000: return out
        offset += 2000

def arcgis_filter(text):
    """ArcGIS dates: `DATE 'YYYY-MM-DD'` in the where clause; {today} and {fifteenMonthsAgo} (Seattle) resolved at run time."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    return text.replace("{fifteenMonthsAgo}", (today - datetime.timedelta(days=457)).isoformat()).replace("{today}", today.isoformat())

def socrata_filter(text):
    """Socrata dates: a floating timestamp, no zone."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    try: ago = now.replace(year=now.year - 2)
    except ValueError: ago = now.replace(year=now.year - 2, day=28)  # 29 February
    return text.replace("{twoYearsAgo}", ago.isoformat()).replace("{today}", now.isoformat())
