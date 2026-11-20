"""Geofabrik's extracts and osmium-tool: which region holds an area, and its features."""
import json, math, os, subprocess
from builder.cells import bounds, contains
from builder.net import get_json, SourceError
from builder.shapes import outer_rings

GEOFABRIK = "https://download.geofabrik.de/index-v1.json"
GRID = 100  # the in-memory index's buckets: 1/100°, ~1 km
_regions = None

def geofabrik_pbf(lat, lon):
    global _regions
    if _regions is None:
        # tiles.yml's plan job fetches the index once for all its build jobs (2026-11-18: ~357 fetches a run, each
        # 3.8 MB, and Geofabrik began refusing part of GitHub's runners).
        path = os.environ.get("GEOFABRIK_INDEX")
        if path and os.path.exists(path):
            with open(path) as f: _regions = json.load(f)["features"]
        else: _regions = get_json(GEOFABRIK)["features"]
    return smallest_region(_regions, lat, lon)["properties"]["urls"]["pbf"]

def smallest_region(regions, lat, lon):
    """The smallest region holding the point: the deepest in its chain of parents, and among
    equally deep ones (us and illinois, dach and switzerland) the one with the smallest box."""
    parents = {f["properties"]["id"]: f["properties"].get("parent") for f in regions}
    depth = lambda i: 0 if i is None else 1 + depth(parents.get(i))
    def size(f):
        s, w, n, e = bounds(f["geometry"])
        return (n - s) * (e - w)
    holding = [f for f in regions if "pbf" in f["properties"].get("urls", {}) and contains(f["geometry"], lat, lon)]
    if not holding: raise SourceError(f"no Geofabrik extract holds {lat},{lon}")
    return max(holding, key=lambda f: (depth(f["properties"]["id"]), -size(f)))

def buckets(s, w, n, e):
    return [(y, x) for y in range(math.floor(s * GRID), math.floor(n * GRID) + 1) for x in range(math.floor(w * GRID), math.floor(e * GRID) + 1)]

def osmium(*args):
    try: return subprocess.run(["osmium", *args], check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as error: raise SourceError(f"osmium: {error.stderr.strip()[:200]}") from error

def features(pbf, kinds="point,polygon", ids=False):
    """An osmium file as GeoJSON features, points and areas only by default: a closed way also comes out as a
    line. Points include the nodes a way needed that carry tags of their own, so callers check tags. With
    ids=True each feature's properties also hold "@type" (node, way, relation) and "@id", an area's its way's or relation's."""
    export = osmium("export", pbf, "-f", "geojsonseq", "--geometry-types=" + kinds, *(["-a", "type,id"] if ids else []), "-o", "-")
    for line in export.split("\n"):  # not splitlines(): it breaks on the \x1e that opens each record
        if line.strip("\x1e"): yield json.loads(line.lstrip("\x1e"))

def mean_point(geometry):
    """(lat, lon): a point's own, an area's outer ring's mean vertex; None for an area with no ring."""
    if geometry["type"] == "Point": return geometry["coordinates"][1], geometry["coordinates"][0]
    ring = next(outer_rings(geometry), None)
    if not ring: return None
    return tuple(sum(v[k] for v in ring) / len(ring) for k in ("latitude", "longitude"))
