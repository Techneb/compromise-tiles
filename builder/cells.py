"""The ~200 m, ~2 km and ~4 km cell grids, boxes, point tests and the boundaries/ files."""
import json, math, os
from builder import ROOT

def boundary(slug):
    with open(os.path.join(ROOT, "boundaries", slug + ".geojson")) as f: return json.load(f)

def in_box(c, lat, lon): return c["lat"][0] <= lat <= c["lat"][1] and c["lon"][0] <= lon <= c["lon"][1]


M = 111_320  # metres per degree of latitude

FINE, COARSE = 500, 50  # cells per degree: ~200 m (buildings, terraces, communes) and ~2 km (venues)
MAIN = 25               # cells per degree, ~4 km: streets-main/ and places/, a whole city in a few hundred

def index(x, scale=FINE): return int(x * scale)  # truncation toward zero, the same key rule as the reader's

def span(k, scale=FINE):
    """The coordinates a key covers: truncation makes 0 twice as wide and a negative key reach down."""
    if k > 0: return k / scale, (k + 1) / scale
    if k < 0: return (k - 1) / scale, k / scale
    return -1 / scale, 1 / scale

class Cell:
    def __init__(self, ky, kx, scale=FINE):
        self.key = f"{ky},{kx}"
        (self.s, self.n), (self.w, self.e) = span(ky, scale), span(kx, scale)
        self.lat, self.lon = (self.s + self.n) / 2, (self.w + self.e) / 2
    @property
    def rect(self): return self.s, self.w, self.n, self.e

def cells_in(s, w, n, e, scale=FINE):
    return [Cell(ky, kx, scale) for ky in range(index(s, scale), index(n, scale) + 1)
            for kx in range(index(w, scale), index(e, scale) + 1)]

def blocks(cells, size):
    """Cells grouped size × size by key, so a source is asked once per block."""
    groups = {}
    for c in cells:
        ky, kx = map(int, c.key.split(","))
        groups.setdefault((ky // size, kx // size), []).append(c)
    return [groups[k] for k in sorted(groups)]

def union(cells):
    return min(c.s for c in cells), min(c.w for c in cells), max(c.n for c in cells), max(c.e for c in cells)

def padded(rect, metres):
    s, w, n, e = rect
    dlat = metres / M
    dlon = metres / (M * math.cos(math.radians((s + n) / 2)))
    return s - dlat, w - dlon, n + dlat, e + dlon

def metres(lat1, lon1, lat2, lon2):
    return math.hypot((lat2 - lat1) * M, (lon2 - lon1) * M * math.cos(math.radians(lat1)))

def in_france(lat, lon): return 41.3 <= lat <= 51.1 and -5.2 <= lon <= 9.6

def contains(geometry, lat, lon):
    """Point in a GeoJSON Polygon or MultiPolygon, even-odd over every ring, so holes count."""
    if not geometry: return False
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    inside = False
    for ring in (r for p in polygons for r in p):
        for a, b in zip(ring, ring[1:] + ring[:1]):
            if (a[1] > lat) != (b[1] > lat) and lon < a[0] + (lat - a[1]) * (b[0] - a[0]) / (b[1] - a[1]): inside = not inside
    return inside

def bounds(geometry):
    points = [v for p in ([geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]) for r in p for v in r]
    return min(v[1] for v in points), min(v[0] for v in points), max(v[1] for v in points), max(v[0] for v in points)

def _overlaps(a, b): return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]

def in_feed(b, lat, lon): return in_box(b, lat, lon) and (b["boundary"] is None or contains(b["boundary"], lat, lon))

def area(entry, half_km):
    """An entry's box: its own `box` [s, w, n, e], else `half_km` around its point."""
    if entry.get("box"): return tuple(entry["box"])
    lat, lon, half_km = entry["lat"], entry["lon"], entry.get("half_km", half_km)
    dlat, dlon = half_km / 111.32, half_km / (111.32 * math.cos(math.radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon

def clip(cells, boundary):
    """The cells whose centre falls inside a GeoJSON (Multi)Polygon."""
    return [c for c in cells if contains(boundary, c.lat, c.lon)]
