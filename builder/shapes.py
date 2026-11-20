"""Footprints and their heights: the shape every building feed returns, compacted for the tiles."""
import collections, json, math, statistics
from builder.cells import contains, FINE

def tag_number(tags, key):
    try: return float(str(tags[key]).replace(" m", "").strip())
    except (KeyError, ValueError): return None

def osm_tagged(tags):
    """Which tag gives a building its height: "height", "levels" (`building:levels`), or None (the 15 m guess)."""
    return "height" if tag_number(tags, "height") is not None else "levels" if tag_number(tags, "building:levels") is not None else None

def osm_height(tags):
    """`height`, else `building:levels` × 3 m, else None (building() then writes the 15 m guess, flagged)."""
    for key, scale in (("height", 1), ("building:levels", 3)):
        if tag_number(tags, key) is not None: return tag_number(tags, key) * scale
    return None



def vertex(lat, lon):
    """Six decimals is 0.1 m, finer than any footprint source is surveyed to; feeds send up to 15."""
    return {"latitude": round(lat, 6), "longitude": round(lon, 6)}

DEFAULT_HEIGHT = 15.0  # a footprint whose source gives no height, flagged "guessed" (2026-10-04)

def building(ring, height=None):
    """A tile's building: the outline and its height in metres. With no height (None), the 15 m guess,
    marked "guessed": true — the one key a reader needs to tell a measured height from the default.
    The flag is written only on guessed buildings, so a tile built before it reads as it did: a
    building without the key is measured, or published before 2026-10-04."""
    if height is None: return {"outline": ring, "height": DEFAULT_HEIGHT, "guessed": True}
    return {"outline": ring, "height": round(float(height), 1)}

# The local estimate (2026-11-19, the Athens audit): a guessed footprint takes the median height of the measured ones in
# its tile (its cell and BUILDING_REACH around, ~600 m), else in the block it was fetched with (CI's 5 × 5 cells and
# BUILDING_PAD, ~1.6 km), else keeps the 15 m guess. ESTIMATE_SAMPLES measured footprints make a median: 5, 10 and 20 gave
# the same error on held-out heights in seven cities (docs/research/OVERTURE_HEIGHTS.md in the app repo). It stays flagged.
ESTIMATE_SAMPLES = 10

def measured_median(items):
    heights = [b["height"] for b in items if not b.get("guessed")]
    return statistics.median(heights) if len(heights) >= ESTIMATE_SAMPLES else None

def estimated(items, block):
    """A tile's items with each guessed height replaced by the local estimate, when there is one."""
    if not any(b.get("guessed") for b in items): return items
    guess = measured_median(items) or measured_median(block)
    if guess is None: return items
    return [dict(b, height=round(float(guess), 1)) if b.get("guessed") else b for b in items]

def guessed_height(h):
    """A buildings-v2/ height that is a guess: null (15 m) or a quarter step (a local estimate, compact_building)."""
    return h is None or (h * 4) % 2 == 1

# buildings-v2/ (2026-10-06; the store's only buildings layer since 2026-10-08): the same footprints as buildings/, a third of the bytes
# gzipped (BUILDINGS.md). Each building is a flat array [height, y0, x0, dy1, dx1, …]: the height in
# HEIGHT_STEP metres (an integer when whole), null for the 15 m guess and a quarter step for a local estimate (both "guessed": true in buildings/);
# the ring open (its closing vertex not repeated), in BUILDING_UNIT steps, the first vertex from the
# key's corner (key / 500), each next from the one before — streets/'s encoding, the height in the kind's place.
BUILDING_UNIT = 100_000   # 1e-5° steps: ~1.1 m north–south, ~0.7 m east–west at Paris's latitude
HEIGHT_STEP = 0.5         # metres

def compact_building(b, cell_key):
    """One buildings-v2/ entry from a buildings/ one, or None when its ring rounds to under 3 corners.
    Absolute coordinates are rounded first, so two buildings sharing a wall keep sharing it."""
    ky, kx = map(int, cell_key.split(","))
    ring = b["outline"][:-1] if len(b["outline"]) > 3 and b["outline"][0] == b["outline"][-1] else b["outline"]
    steps = []
    for p in ring:
        q = (round(p["latitude"] * BUILDING_UNIT), round(p["longitude"] * BUILDING_UNIT))
        if not steps or q != steps[-1]: steps.append(q)
    if len(steps) > 1 and steps[0] == steps[-1]: steps.pop()
    if len(steps) < 3: return None
    if not b.get("guessed"): height = round(b["height"] / HEIGHT_STEP) * HEIGHT_STEP
    else:  # an estimate in quarter steps, which no measured height takes (guessed_height); the 15 m guess null, as before
        height = None if b["height"] == DEFAULT_HEIGHT else math.floor(b["height"] / HEIGHT_STEP) * HEIGHT_STEP + HEIGHT_STEP / 2
    if height is not None and float(height).is_integer(): height = int(height)
    per = BUILDING_UNIT // FINE
    out, (y, x) = [height, steps[0][0] - ky * per, steps[0][1] - kx * per], steps[0]
    for qy, qx in steps[1:]:
        out += [qy - y, qx - x]
        y, x = qy, qx
    return out

def compact_buildings(items, cell_key):
    """A buildings-v2/ tile from a buildings/ tile's items, in their order."""
    return [c for c in (compact_building(b, cell_key) for b in items) if c is not None]

def outer_rings(geometry):
    coordinates = (geometry or {}).get("coordinates") or []
    polygons = [coordinates] if (geometry or {}).get("type") == "Polygon" else coordinates
    for polygon in polygons:
        ring = [vertex(v[1], v[0]) for v in (polygon[0] if polygon else []) if len(v) >= 2]
        if len(ring) >= 3: yield ring

def footprints(features, height, guess=True):
    """GeoJSON → a building: outer rings, the city's height rule, the flagged 15 m guess when it reads
    nothing sensible; with guess=False such a footprint is left out (a combine row: OSM's own height stays)."""
    out = []
    for f in features:
        h = height(f.get("properties") or {})
        if not (h and h > 0) and not guess: continue
        for ring in outer_rings(f.get("geometry")):
            out.append(building(ring, h if h and h > 0 else None))
    return out

COMBINE_GRID = 2000  # the city footprints' index in combined(): 1/2000°, ~50 m

def combined(osm, city, metres=False):
    """OpenStreetMap's footprints (osm_buildings(rect, tagged=True)), each taking the height of the city
    footprint it matches, marked "city": True: each holds the other's centre (the mean of its vertices),
    so a small OSM building inside a big city outline (an annex under an office block's footprint) matches
    none; an OSM outline drawn round several city buildings takes the one under its centre. A `height` tag is kept: a mapper's
    measure, which a city's floor count is coarser than (Oakland's Ordway Building: 28 floors × 3 m = 84 m,
    tagged 123 m). `building:levels` × 3 m is kept too, unless the city's height is measured in metres
    (metres=True): two floor counts, the mapper's is the newer (Packard Lofts: 1 floor in Oakland's 2015
    layer, 4 levels in OSM). OSM's 15 m guess always gives way, and loses its "guessed" flag with it.
    City footprints OSM lacks are not added: the two outlines of one building rarely agree, and one added
    beside the other would double it. A city footprint identical to OSM's (London's lidar, measured on
    OSM's own outlines) matches it whatever its shape: a courtyard block's centre lies outside it."""
    def centre(b): return (sum(p["latitude"] for p in b["outline"]) / len(b["outline"]),
                           sum(p["longitude"] for p in b["outline"]) / len(b["outline"]))
    def ring(b): return {"type": "Polygon", "coordinates": [[[p["longitude"], p["latitude"]] for p in b["outline"]]]}
    def key(b): return tuple((p["latitude"], p["longitude"]) for p in b["outline"])
    same = {key(b): b["height"] for b in city}
    grid = {}
    for b in city:
        lats, lons = [p["latitude"] for p in b["outline"]], [p["longitude"] for p in b["outline"]]
        for y in range(math.floor(min(lats) * COMBINE_GRID), math.floor(max(lats) * COMBINE_GRID) + 1):
            for x in range(math.floor(min(lons) * COMBINE_GRID), math.floor(max(lons) * COMBINE_GRID) + 1):
                grid.setdefault((y, x), []).append((ring(b), centre(b), b["height"]))
    out = []
    for b in osm:
        lat, lon = centre(b)
        near = grid.get((math.floor(lat * COMBINE_GRID), math.floor(lon * COMBINE_GRID)), ())
        mine = ring(b)
        height = same[key(b)] if key(b) in same else next((h for theirs, c, h in near if contains(theirs, lat, lon) and contains(mine, *c)), None)
        kept = b.get("tagged") == "height" or (b.get("tagged") == "levels" and not metres)
        out.append(b if height is None or kept else dict({k: v for k, v in b.items() if k != "guessed"}, height=height, city=True))
    return out

def unique(items):
    seen, out = set(), []
    for item in items:
        k = json.dumps(item, sort_keys=True)
        if k not in seen: seen.add(k); out.append(item)
    return out

def number(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def triangles_outline(faces):
    """The outer ring of a footprint drawn as triangles (Estonia's LoD1 bottom faces): the edges used once, chained,
    the ring of largest area kept."""
    edges = collections.Counter()
    for face in faces:
        for p, q in zip(face, face[1:]):
            if p != q: edges[frozenset((p, q))] += 1
    links = collections.defaultdict(list)
    for edge, count in edges.items():
        if count == 1:
            p, q = tuple(edge); links[p].append(q); links[q].append(p)
    rings, used = [], set()
    for start in list(links):
        for nxt in links[start]:
            if frozenset((start, nxt)) in used: continue
            ring, prev, cur = [start], start, nxt
            used.add(frozenset((start, nxt)))
            while cur != start:
                ring.append(cur)
                step = next((q for q in links[cur] if frozenset((cur, q)) not in used), None)
                if step is None: break
                used.add(frozenset((cur, step))); prev, cur = cur, step
            if cur == start and len(ring) >= 3: rings.append(ring + [start])
    area = lambda r: abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(r, r[1:])))
    return max(rings, key=area) if rings else None

# The city height feeds, one row each. The first row holding the cell's centre (its box, and its
# municipal boundary when the feed stops there) is asked first, then IGN (France), then
# OpenStreetMap, each only when the one before answered nothing. A boundary keeps a neighbour's
# cells off a feed that answers only a few buildings there (Fitzroy got 73 from Melbourne's).
# A combine row ("metres" or "floors", what its heights are) keeps OpenStreetMap's footprints and
# takes the feed's height where it has the building (combined(), which says when OSM's own stays):
# for a feed that lacks many buildings, whose cells it would otherwise empty of them. Its fetch
# returns only the footprints with a height (footprints(..., guess=False)).
# A footprint no source gave a height is written at 15 m with "guessed": true (building()), so a
# reader can tell it from a measured one; the app's rooftop test reads the flag (2026-10-04).
def difference(p, top, ground):
    """A roof level minus a ground level (Vienna's O_KOTE − T_KOTE, Hong Kong's mPD): None where either is missing."""
    a, b = number(p.get(top)), number(p.get(ground))
    return a - b if a is not None and b is not None else None
