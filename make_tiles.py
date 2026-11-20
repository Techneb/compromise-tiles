#!/usr/bin/env python3
"""Pre-cut city-data tiles for Compromise's sunny filter and venue list.

Writes one JSON array per ~200 m
cell, keyed "<int(lat*500)>,<int(lon*500)>", under buildings/,
terraces-v2/, streets/ and communes/ (French cells: the build and the coverage feed
read them, the upload leaves them out), and one per ~2 km cell, keyed
"<int(lat*50)>,<int(lon*50)>", under venues/. Beside the first two,
sources/ names what built each cell ([{"permits": city|null,
"buildings": city|"IGN"|"OSM"|city+"+OSM", "boundaries"?: source}]) — what the app credits.
streets/ is built only for the areas whose entry says "streets": true: each
item is one OpenStreetMap street cut at the cell's edges, a flat array
[kind, y0, x0, dy1, dx1, …] (kind 1 main road, 2 street, 3 pedestrian),
with the way's name after the kind where it has one, [kind, "name", y0, …] (2026-10-08),
coordinates in 1e-5° steps, the first from the key's corner (key / 500),
each next from the one before. numbers/ (2026-10-10), built with streets/ on
its cells: [["name", lowest, highest], …] sorted by name, one row per named
street of the cell's streets/ tile with at least two distinct house numbers
inside the cell — OpenStreetMap's addr:housenumber (its leading digits:
"12bis" is 12) on nodes, buildings and along addr:interpolation ways, whose
addr:street, else associatedStreet relation's name, matches the name without
case, accents or apostrophes; no file for a cell with none. streets-main/ is the same on ~4 km cells
(1/25°, keyed "<int(lat*25)>,<int(lon*25)>"): main roads only, 1e-4° steps
from key / 25. places/, on the same cells: [{"name", "kind" (suburb,
quarter, neighbourhood), "coordinate", "admin_level"?}]: "admin_level" (an
integer, OSM's) only when the name comes from or matches an administrative
boundary relation (Paris: 9 the arrondissements, 10 the quartiers
administratifs), absent otherwise, so a reader of the first format reads
it unchanged (2026-10-06); "name_en" (OSM's name:en) the same way, only
where it exists and differs from "name": Tokyo's, Seoul's or Kyiv's names
are in their own scripts (2026-10-09). Both only where streets/ is.
boundaries/ (2026-10-10), on places/'s cells and only where streets/ is: [{"name", "level", "source",
"polygons"}], each neighbourhood's outline whole in the cell of its label point (the places/ place of
the same name inside it, whose name is then written; else a point inside it), not cut at the cell's
edges. "level" is the source's word for the unit ("quartier", "Ortsteil") or OSM's admin_level,
"source" the key sources/ names; "polygons" [[ring, …], …], per polygon its outer ring
(counter-clockwise) then its holes (clockwise), each ring [y0, x0, dy1, dx1, …] open, in 1e-5° steps,
the first from the key's corner (key / 25), each next from the one before. Simplified at 5 m with
shapely's coverage_simplify over each source's set, so neighbours keep sharing an edge, then rounded
on the 1e-5° grid. One source per city (AREA_SOURCES: the city's portal, else a national agency,
else OpenStreetMap's boundaries or place polygons from the extract), fetched once per run; sources/
names it ("boundaries") in each ~200 m cell whose centre one of its polygons holds.
buildings-v2/ is what the store holds (buildings/ stays local, read by coverage.py; 2026-10-08):
the same footprints as
[height or null, y0, x0, dy1, dx1, …], 1e-5° steps from key / 500, heights in
0.5 m steps (BUILDINGS.md).
transit/ (2026-10-13), on places/'s cells and only where streets/ is: [{"mode" (subway, tram, train, light_rail, bus, trolleybus, aerialway),
"ref", "colour"? ("#RRGGBB"), "network"?, "operator"?, "stops": [name, …], "points": [y0, x0, dy1, dx1, …], "shape": [[y0, x0, …], …]}],
OpenStreetMap's route relations (the route_master's ref, colour, network and operator where the route has none), each line
whole in every cell one of its stops falls in; points one per stop, in 1e-5° steps, the first from the key's corner
(key / 25), each next from the one before; shape the track's runs coded the same way, each from the corner,
simplified at 10 m. Long-distance trains, coaches, night and tourist lines are left out; one direction kept where the other stops at the
same names.

    make_tiles.py --city Paris --lat 48.8719 --lon 2.3316 --half-km 0.5 --out ./tiles
    make_tiles.py --cities cities.json --out ./tiles --layers terraces-v2,venues
    make_tiles.py --cities cities.json --out ./tiles --layers sources --store https://tiles.alephb.uk/tiles/

With --store (the published tiles' base URL), a sources/ tile keeps the field a run does not build:
a run building terraces keeps the published tile's "buildings", one building buildings keeps its
"permits". The `sources` layer writes nothing else: it finds each cell's building source the way the
buildings layer does (its tiles not written) for the published sources tiles that lack it.

OpenStreetMap comes from a Geofabrik extract (the smallest region holding
each area, found in Geofabrik's index), cut and read with osmium-tool —
never from Overpass. City permit and height feeds, IGN and the commune
contours are downloaded in bulk at build time; the phone reads only tiles.
Every request is retried (10, 30, then 90 s), spaced per host (1 s) and
sent with a User-Agent naming the project. The run is resumable: a cell
whose file already parses is skipped while younger than its layer's
MAX_AGE_DAYS, by the file's mtime. A file is written only from a complete
answer — a source that failed leaves no file, so the next run asks again.
Failures are listed at the end; the exit status is non-zero only if more
than 20% of the cells failed.

Needs Python 3 and osmium-tool on the PATH; the standard library only, but for boundaries/:
shapely ≥ 2.1, pyproj and pyshp (requirements.txt).
Derived tiles that include OpenStreetMap data are ODbL: publish them under
ODbL with the credit "© OpenStreetMap contributors".
"""
import argparse, concurrent.futures, json, math, os, re, sys, time
from builder import extract, store
from builder.cells import area, blocks, bounds, cells_in, clip, COARSE, contains, in_france, index, M, MAIN, metres, padded, union
from builder.net import download, log, SourceError
from builder.shapes import compact_buildings, estimated, unique
from builder.streets import MAIN_UNIT, number_rows, street_tiles
from builder.transit import transit_tiles
from builder.osmium import buckets, geofabrik_pbf
from builder.places import place_key
from builder.overture import OVERTURE_CITIES, overture_facts, overture_places
from builder.extract import OSM, osm_addresses, osm_main_streets, osm_places, osm_streets, osm_terraces, osm_venues
from builder.buildings_table import building_source, CITY_BUILDINGS, COMBINE, FETCH_BUILDINGS, next_source
from builder.store import done, in_store, LAYERS, note_sources, path, published_items, published_sources, read, STORE_THREADS, STREET_LAYERS, write
from builder.permits import permit_city, permits
from builder.communes import commune
from builder.area_sources import AREA_SOURCES, BOUNDARY_TOLERANCE, BOUNDARY_UNIT

# The scripts and tests that import names from make_tiles still find them here while they move to builder/
# (rebound state excepted: builder.store.STORE, builder.store.LISTED, builder.extract._osm).
import builder.cells, builder.net, builder.projections, builder.shapes, builder.formats, builder.names, builder.streets, builder.transit, builder.osmium, builder.places, builder.venues, builder.overture, builder.extract, builder.apis, builder.buildings_citygml, builder.buildings_measured, builder.buildings_vector, builder.buildings_table, builder.store, builder.permit_table, builder.permits, builder.communes, builder.area_sources
for _m in [builder.cells, builder.net, builder.projections, builder.shapes, builder.formats, builder.names, builder.streets, builder.transit, builder.osmium, builder.places, builder.venues, builder.overture, builder.extract, builder.apis, builder.buildings_citygml, builder.buildings_measured, builder.buildings_vector, builder.buildings_table, builder.store, builder.permit_table, builder.permits, builder.communes, builder.area_sources]:
    globals().update((k, v) for k, v in vars(_m).items() if not k.startswith("__") and k not in builder.REBOUND)

TERRACE_RADIUS = 400          # metres around the cell's centre: the radius the density gate is calibrated on
# Every footprint whose box comes within this of the cell: the app's reach
# plus a margin (its docs; Opéra, 2026-09-24: 690 KB → 280 KB a cell against
# a 400 m reach). Raise it if that reach grows.
BUILDING_REACH = 200
BUILDING_PAD = 250            # fetched around a block: some feeds select by a footprint's centre, not its outline

TITLE_SMALL = {"de", "des", "du", "la", "le", "les", "del", "della", "delle", "degli", "dei", "di", "da", "do", "dos", "das", "sul", "y", "et",
               "e", "i", "and", "of", "the",
               "van", "der", "den", "het", "von", "am", "im"}

def titled(name):
    """A name written in capitals only ("LA CREU COBERTA", "ΚΟΛΩΝΑΚΙ") in title case, its articles and particles after
    the first word in lower case and a Roman numeral kept ("Avenue Léopold III"); any other name as it is."""
    if not any(ch.isalpha() for ch in name) or name != name.upper(): return name
    def word(m):
        w = m[0]
        if re.fullmatch(r"[IVX]+", w) and m.start() > 0: return w
        if w.lower() in TITLE_SMALL and m.start() > 0 and name[m.start() - 1] == " ": return w.lower()
        return w[0] + w[1:].lower()
    return re.sub(r"[^\W\d_]+", word, name)

def area_features(row, rect):
    """[{"name", "level", "source", "geometry"}] of one row: kept, named, dissolved, made valid, polygons only."""
    import shapely
    out, groups = [], {}
    for p, g in row["fetch"](rect):
        if row["keep"] and not row["keep"](p): continue
        name = row["name"](p) if callable(row["name"]) else p.get(row["name"])
        name = " ".join(str(name or "").split())
        if len(name) > 2 and name[0] == name[-1] == '"': name = name[1:-1].strip()   # Zagreb's units named after people
        if not any(ch.isalnum() for ch in name): continue   # e-Stat's "‐", an unnamed area
        level = row["level"](p) if callable(row["level"]) else row["level"] if row["level"] is not None else p.get("level")
        g = shapely.make_valid(g)
        g = shapely.union_all([part for part in shapely.get_parts(g) if part.geom_type in ("Polygon", "MultiPolygon")] or [shapely.Polygon()])
        if g.is_empty: continue
        group = None if not row["dissolve"] else name if row["dissolve"] is True else row["dissolve"](p)
        if group is not None and group in groups:
            groups[group]["geometry"] = shapely.union(groups[group]["geometry"], g); continue
        out.append({"name": name, "level": level, "source": row["source"], "geometry": g})
        if group is not None: groups[group] = out[-1]
    return out

def simplified_coverage(geoms, tolerance=BOUNDARY_TOLERANCE):
    """The geometries (lon/lat) simplified together by `tolerance` metres, in a local equirectangular frame:
    an edge two of them share is simplified once, so it stays shared. Then rounded on the 1e-5° grid,
    vertex by vertex, so the shared vertices stay equal."""
    import shapely, numpy
    if not geoms: return []
    lat0 = numpy.mean([g.centroid.y for g in geoms])
    k = math.cos(math.radians(lat0))
    there = shapely.transform(numpy.array(geoms, dtype=object), lambda xy: xy * [M * k, M])
    back = shapely.transform(shapely.coverage_simplify(there, tolerance), lambda xy: xy / [M * k, M])
    return list(shapely.set_precision(back, 1 / BOUNDARY_UNIT, mode="pointwise"))

def area_rings(geometry, cell_key):
    """boundaries/'s "polygons" for a geometry: per polygon its outer ring (counter-clockwise) then its holes
    (clockwise), each open, in BOUNDARY_UNIT steps, the first vertex from the key's corner (key / 25), each next
    from the one before. A ring of under 3 distinct vertices is left out, and a polygon whose outer ring is."""
    import shapely
    ky, kx = map(int, cell_key.split(","))
    per = BOUNDARY_UNIT // MAIN
    out = []
    for polygon in shapely.get_parts(shapely.orient_polygons(geometry)):
        rings = []
        for ring in [polygon.exterior, *polygon.interiors]:
            steps = []
            for lon, lat in ring.coords:
                q = (round(lat * BOUNDARY_UNIT), round(lon * BOUNDARY_UNIT))
                if not steps or q != steps[-1]: steps.append(q)
            if len(steps) > 1 and steps[0] == steps[-1]: steps.pop()
            if len(steps) < 3:
                if not rings: break
                continue
            flat, (y, x) = [steps[0][0] - ky * per, steps[0][1] - kx * per], steps[0]
            for qy, qx in steps[1:]:
                flat += [qy - y, qx - x]
                y, x = qy, qx
            rings.append(flat)
        if rings: out.append(rings)
    return out

def label_point(feature, places):
    """(name, lat, lon): the places/ place inside the polygon under the same place_key() (its name and point),
    else the polygon's name, in title case if written in capitals, at a point inside it."""
    import shapely
    key = place_key(feature["name"])
    for p in places:
        lat, lon = p["coordinate"]["latitude"], p["coordinate"]["longitude"]
        if place_key(p["name"]) == key and feature["geometry"].contains(shapely.Point(lon, lat)): return p["name"], lat, lon
    at = feature["geometry"].representative_point()
    return titled(feature["name"]), at.y, at.x

def area_tiles(features, places):
    """{MAIN key: [item, …] sorted by name}: each feature whole in its label point's cell."""
    tiles = {}
    for f in features:
        name, lat, lon = label_point(f, places)
        key = f"{index(lat, MAIN)},{index(lon, MAIN)}"
        polygons = area_rings(f["geometry"], key)
        if not polygons: continue
        tiles.setdefault(key, []).append({"name": name, "level": f["level"], "source": f["source"], "polygons": polygons})
    for items in tiles.values(): items.sort(key=lambda i: (i["name"], i["polygons"][0][0][:2]))
    return tiles

def area_region(entries, half_km=0.5):
    """The city's tiled area: each entry's box, cut to its boundary where it has one, together."""
    import shapely, shapely.geometry
    parts = []
    for e in entries:
        s, w, n, e_ = area(e, half_km)
        part = shapely.box(w, s, e_, n)
        if e.get("_boundary"):   # a ring of under 4 positions (Marseille's file holds one) is left out
            g = e["_boundary"]
            polygons = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            shape = shapely.MultiPolygon([shapely.Polygon(p[0], [r for r in p[1:] if len(r) >= 4]) for p in polygons if p and len(p[0]) >= 4])
            part = part.intersection(shapely.make_valid(shape))
        parts.append(part)
    return shapely.union_all(parts)

_areas = {}   # city → (features, tiles) or SourceError: built once a run, at the city's first entry

def city_areas(city, entries):
    """The city's boundaries/ features (simplified) and tiles, from every row of the city: a row's features meeting
    the tiled area, simplified as one set. A row whose key is not set gives nothing (said once); a row that
    fails fails the city (its published tiles stay)."""
    import shapely
    if city in _areas: return _areas[city]
    features = []
    try:
        region = area_region(entries)
        s, w, n, e = region.bounds[1], region.bounds[0], region.bounds[3], region.bounds[2]
        for row in (r for r in AREA_SOURCES if r["city"] == city):
            if row["key"] and not os.environ.get(row["key"], "").strip():
                log(f"  {city} boundaries: skipped, {row['key']} is not set"); continue
            found = [f for f in area_features(row, (s, w, n, e))
                     if f["geometry"].intersects(region) and shapely.area(shapely.intersection(f["geometry"], region)) > 0]
            for f, g in zip(found, simplified_coverage([f["geometry"] for f in found])): f["geometry"] = g
            features += [f for f in found if not f["geometry"].is_empty]
            log(f"  {city} boundaries: {len(found)} from {row['source']}")
        _areas[city] = features, area_tiles(features, extract._osm.places() if extract._osm else [])
    except SourceError as error:
        _areas[city] = error
    except Exception as error:  # noqa: BLE001 — a malformed answer (bad zip, missing field) fails the city, not the run
        _areas[city] = SourceError(f"{type(error).__name__}: {error}"[:300])
    return _areas[city]

def do_boundaries(city, entries, cells, out, failures, published=None):
    """The city's boundaries/ tiles (written at its first entry; an earlier run's tile in a cell of the city's area
    that holds none now is removed), and in sources/ each of this entry's ~200 m cells whose centre lies in a
    polygon names its source ("boundaries")."""
    first = city not in _areas
    answer = city_areas(city, entries)
    wide = {c.key for e in entries for c in cells_in(*area(e, 0.5), scale=MAIN)}
    if isinstance(answer, SourceError):
        if first: failures += [(k, "boundaries", str(answer)) for k in sorted(wide)]
        return
    features, tiles = answer
    if first:
        for key, items in tiles.items(): write(os.path.join(out, "boundaries", key + ".json"), items)
        for key in wide - set(tiles):
            if os.path.exists(os.path.join(out, "boundaries", key + ".json")): os.remove(os.path.join(out, "boundaries", key + ".json"))
    if not features or not cells: return
    import shapely
    tree = shapely.STRtree([f["geometry"] for f in features])
    at, hit = tree.query(shapely.points([(c.lon, c.lat) for c in cells]), predicate="intersects")
    source = {}
    for i, j in zip(at, hit): source.setdefault(cells[i].key, features[j]["source"])
    noted = [c for c in cells if c.key in source]
    published = published if published is not None else published_sources(out, noted)
    for c in noted: note_sources(out, c, published, boundaries=source[c.key])

UNSHADED = None  # --unshaded: the file the weekly run lists the cells no venue or terrace reaches and no small place holds in

def build_order(cells, block, layers):
    """The blocks, those with a tile missing from the store first, each part in key order."""
    return sorted(blocks(cells, block), key=lambda g: all(in_store(c, layers) for c in g))

_cell_by_cell = set()  # sources whose blocks failed where single cells answered: blocks too big for a proxy

def gather(cells, fetch, pad, source):
    """{key: items or SourceError}: the block asked once, and each cell alone if the block failed.
    A source whose block failed while its cells answered is asked cell by cell for the rest of the
    run (Overpass from a cloud proxy, 2026-09-24: 3×3 blocks reset every time, single cells went through).
    Not when the block got an HTTP status: the server answered, so only that block goes cell by cell
    (one IGN 400 in Paris, 2026-09-28, sent 4,700 cells one at a time and ran past the job's 6 hours)."""
    if len(cells) > 1 and source not in _cell_by_cell:
        try:
            items = unique(fetch(padded(union(cells), pad)))
            return {c.key: items for c in cells}
        except SourceError as e:
            log(f"    {source}: block failed ({e}); asking cell by cell")
            block_failed = "HTTP " not in str(e)
    else:
        block_failed = False
    answers = {}
    for c in cells:
        try: answers[c.key] = unique(fetch(padded(c.rect, pad)))
        except SourceError as e: answers[c.key] = e
    if block_failed and any(not isinstance(a, SourceError) for a in answers.values()):
        log(f"    {source}: cell by cell from now on")
        _cell_by_cell.add(source)
    return answers

def near_buildings(cell, buildings):
    s, w, n, e = padded(cell.rect, BUILDING_REACH)
    def meets(b):
        lats = [p["latitude"] for p in b["outline"]]
        lons = [p["longitude"] for p in b["outline"]]
        return min(lats) <= n and max(lats) >= s and min(lons) <= e and max(lons) >= w
    return [b for b in buildings if meets(b)]

def near_terraces(cell, terraces):
    return [t for t in terraces
            if metres(cell.lat, cell.lon, t["coordinate"]["latitude"], t["coordinate"]["longitude"]) <= TERRACE_RADIUS]

# A buildings tile is written only for a cell within SHADE_MARGIN of a venue (a bar, pub, beer garden, café or restaurant
# of the extract, named or not) or of a terrace (terraces-v2/) (owner, 2026-10-13; BUILDINGS.md). Each tile already holds
# every footprint within BUILDING_REACH of its cell, so a kept cell needs no neighbour.
SHADE_MARGIN = 100

def shade_keys(points, margin=SHADE_MARGIN):
    """The keys of the ~200 m cells whose rect comes within `margin` metres of a (lat, lon) of `points`."""
    keys = set()
    for lat, lon in points:
        k = M * math.cos(math.radians(lat))
        for c in cells_in(*padded((lat, lon, lat, lon), margin)):
            if math.hypot(max(c.s - lat, 0, lat - c.n) * M, max(c.w - lon, 0, lon - c.e) * k) <= margin: keys.add(c.key)
    return keys

def shade_points(cells, out):
    """(lat, lon) of the extract's venues around the cells and of the terraces of their terraces-v2/ tiles (this run's,
    else the published one: each holds the terraces within TERRACE_RADIUS, so together they hold every terrace near
    the cells). None when a published tile could not be read: the caller then keeps every cell."""
    points = {(v["coordinate"]["latitude"], v["coordinate"]["longitude"])
              for v in extract._osm.near(extract._osm.venues, padded(union(cells), SHADE_MARGIN))}
    todo = [c for c in cells if read(path(out, "terraces-v2", c)) is None]
    with concurrent.futures.ThreadPoolExecutor(STORE_THREADS) as pool:
        published = dict(zip((c.key for c in todo), pool.map(lambda c: published_items("terraces-v2", c.key), todo)))
    for c in cells:
        items = published[c.key] if c.key in published else read(path(out, "terraces-v2", c))
        if isinstance(items, SourceError):
            log(f"    {c.key}: terraces-v2/ not read ({items}); every cell keeps its buildings"); return None
        points.update((t["coordinate"]["latitude"], t["coordinate"]["longitude"]) for t in items)
    return points

# A small place keeps every buildings tile (owner, 2026-10-15): a commune (OSM admin_level 8) holding fewer than SMALL_PLACE_VENUES
# of the extract's venues (the same as SHADE_MARGIN's), so a venue OSM lacks there (Périgny, 2026-10-14) still has its shade.
SMALL_PLACE_LEVEL, SMALL_PLACE_VENUES = 8, 5

def small_place_keys(cells, rect):
    """The keys of the cells whose centre lies in a small commune meeting the rect, None when the communes could not be read."""
    by_key, held = {c.key: c for c in cells}, []
    try:
        for b in extract._osm.areas(rect)[0]:
            if b["admin_level"] != SMALL_PLACE_LEVEL or not (g := b["geometry"]): continue
            keys = {c.key for k in cells_in(*bounds(g)) if (c := by_key.get(k.key)) and contains(g, c.lat, c.lon)}
            if keys: held.append((g, keys))
        # A commune reaching past the area's box (Saint-Germain-en-Laye past the petite couronne's) has venues the box left
        # out: those communes' venues are read again from the whole extract, at once.
        s, w, n, e = extract._osm.box
        far = [g for g, _ in held if not (s <= (x := bounds(g))[0] and w <= x[1] and x[2] <= n and x[3] <= e)]
        far_venues = {}
        if far:
            boxes = [bounds(g) for g in far]
            for v in extract._osm.venues_in((min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes))):
                lat, lon = v["coordinate"]["latitude"], v["coordinate"]["longitude"]
                far_venues.setdefault(buckets(lat, lon, lat, lon)[0], []).append(v)
    except SourceError as e:
        log(f"    communes not read ({e}); every cell keeps its buildings"); return None
    small = set()
    for g, keys in held:
        table = far_venues if any(g is f for f in far) else extract._osm.venues
        inside = (v for v in extract._osm.near(table, bounds(g)) if contains(g, v["coordinate"]["latitude"], v["coordinate"]["longitude"]))
        if sum(1 for _ in zip(range(SMALL_PLACE_VENUES), inside)) < SMALL_PLACE_VENUES: small |= keys
    return small

def shaded_keys(cells, out, rect):
    """The cells a buildings tile is written for: near a venue or terrace (shade_keys), or in a small place. None: every cell."""
    points = shade_points(cells, out)
    small = small_place_keys(cells, rect) if points is not None else None
    return None if small is None else shade_keys(points) | small

def do_buildings(cells, out, failures, published=None, tiles=True):
    """The building tiles (with tiles=False, only each cell's source in sources/), by source."""
    published = published if published is not None else published_sources(out, cells)
    groups = {}
    for c in cells: groups.setdefault(building_source(c), []).append(c)
    while groups:
        source, group = groups.popitem()
        answers = gather(group, FETCH_BUILDINGS[source], BUILDING_PAD, source)
        for c in group:
            answer = answers[c.key]
            if isinstance(answer, SourceError):
                city = next((b for b in CITY_BUILDINGS if b["city"] == source), None)
                if city and city["osm_on_error"]:
                    log(f"    {source}: {answer}; {c.key} from OSM")
                    groups.setdefault("OSM", []).append(c); continue
                failures.append((c.key, "buildings", str(answer))); continue
            items = near_buildings(c, answer)
            if not items and next_source(source, c):
                groups.setdefault(next_source(source, c), []).append(c); continue
            name = source
            if source in COMBINE:  # "<city>+OSM" where the city gave a height, else OSM's alone
                name = f"{source}+OSM" if any(b.get("city") for b in items) else "OSM"
                items = [{k: b[k] for k in ("outline", "height", "guessed") if k in b} for b in items]
            items = estimated(items, answer)
            if tiles: write(path(out, "buildings", c), items)
            if tiles: write(path(out, "buildings-v2", c), compact_buildings(items, c.key))
            note_sources(out, c, published, buildings=name)

def do_terraces(cells, communes, out, failures):
    osm = gather(cells, osm_terraces, TERRACE_RADIUS, "OSM terraces")
    feeds, feed_of = {}, {}
    for c in cells:
        feed = feed_of[c.key] = permit_city(c, communes.get(c.key))
        if feed: feeds.setdefault(id(feed), (feed, []))[1].append(c)
    found, published = {}, published_sources(out, cells)
    for feed, group in feeds.values():
        found.update(gather(group, lambda r, feed=feed: permits(feed, r), TERRACE_RADIUS, feed["city"] + " permits"))
    for c in cells:
        answers = [osm[c.key]] + ([found[c.key]] if c.key in found else [])
        errors = [a for a in answers if isinstance(a, SourceError)]
        if errors:  # half an answer is not a tile
            failures.append((c.key, "terraces-v2", "; ".join(map(str, errors)))); continue
        write(path(out, "terraces-v2", c), near_terraces(c, [t for a in answers[1:] for t in a] + answers[0]))
        note_sources(out, c, published, permits=feed_of[c.key] and feed_of[c.key]["city"])

def do_streets(cells, out, failures):
    """The street tiles of a block: OpenStreetMap's ways and house numbers asked once, cut cell by cell."""
    try: streets, (points, interpolations) = osm_streets(union(cells)), osm_addresses(union(cells))
    except SourceError as e:
        failures += [(c.key, "streets", str(e)) for c in cells]; return
    tiles = street_tiles(cells, streets)
    numbers = number_rows(tiles, points, interpolations)
    for c in cells:
        write(path(out, "streets", c), tiles[c.key])
        if numbers[c.key]: write(path(out, "numbers", c), numbers[c.key])
        elif os.path.exists(path(out, "numbers", c)): os.remove(path(out, "numbers", c))   # an earlier run's, now stale

def do_main_streets(cells, out, failures):
    """The streets-main/ tiles, one ~4 km cell at a time."""
    for c in cells:
        try: lines = street_tiles([c], osm_main_streets(c.rect), MAIN, MAIN_UNIT, named=False)[c.key]
        except SourceError as e:
            failures.append((c.key, "streets-main", str(e))); continue
        write(path(out, "streets-main", c), lines)

def do_places(cells, out, failures):
    try:
        for c in cells: write(path(out, "places", c), osm_places(c))
    except SourceError as e: failures += [(c.key, "places", str(e)) for c in cells]

def do_transit(cells, out, failures):
    """The transit/ tiles of the area's ~4 km cells; a cell no line stops in has no file (an earlier run's is removed)."""
    try: tiles = transit_tiles(cells, extract._osm.transit())
    except SourceError as e:
        failures += [(c.key, "transit", str(e)) for c in cells]; return
    for c in cells:
        if c.key in tiles: write(path(out, "transit", c), tiles[c.key])
        elif os.path.exists(path(out, "transit", c)): os.remove(path(out, "transit", c))

def do_sources(cells, out, failures):
    """The `sources` layer: the published sources/ tiles that lack a building source get it, found as
    the buildings layer finds it (the city feed, IGN, then OSM), its tiles not written. A cell without
    a published tile was never built and is left alone; one whose tile already names its source too."""
    if not store.STORE:
        failures += [(c.key, "sources", "no --store to read the published tiles from") for c in cells]; return
    published = published_sources(out, cells)
    for c in cells:
        if isinstance(published.get(c.key), SourceError): failures.append((c.key, "sources", str(published[c.key])))
    need = [c for c in cells if isinstance(published.get(c.key), dict) and published[c.key] and "buildings" not in published[c.key]]
    if need: do_buildings(need, out, failures, published, tiles=False)

def run(label, rect, out, block, layers, extracts, departements=None, boundary=None, city=None, city_entries=()):
    started = time.monotonic()
    cells, coarse, wide = cells_in(*rect), cells_in(*rect, scale=COARSE), cells_in(*rect, scale=MAIN)
    if boundary:  # a box around a municipality keeps only the cells inside its boundary, and the venue cells over them
        cells = clip(cells, boundary)
        kept = {f"{index(c.lat, COARSE)},{index(c.lon, COARSE)}" for c in cells}
        coarse = [c for c in coarse if c.key in kept]
        kept = {f"{index(c.lat, MAIN)},{index(c.lon, MAIN)}" for c in cells}
        wide = [c for c in wide if c.key in kept]
    failures, skipped, outside = [], 0, set()
    log(f"{label}: {len(cells)} cells, {len(coarse)} venue cells, blocks of {block}×{block}")
    lat, lon = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    try:  # the box every OSM question can reach: venue cells whole, buildings and terraces past the edge
        pbf = geofabrik_pbf(lat, lon)
        reach = union(coarse), padded(rect, 600)
        extract._osm = OSM(download(pbf, extracts), (min(r[0] for r in reach), min(r[1] for r in reach),
                                             max(r[2] for r in reach), max(r[3] for r in reach)))
    except SourceError as e:
        log(f"{label}: OpenStreetMap unavailable ({e})")
        return len(cells), [(c.key, "all", str(e)) for c in cells]
    if city in OVERTURE_CITIES and "venues" in layers:
        try:
            counts = overture_facts(extract._osm, overture_places(extract._osm.box))
            log(f"{label}: Overture " + ", ".join(f"{k} {sum(c.values()) if k == 'dropped' else dict(c)}" for k, c in sorted(counts.items())))
        except Exception as e:   # the venues still go up, without the kinds, and the log says why
            log(f"{label}: Overture unavailable ({str(e)[:200]})")
    # A French extract means French communes; elsewhere the geo API would answer [] a cell at a time.
    french = "/europe/france" in pbf
    sources_only = set(layers) <= {"sources", "venues", "streets-main", "places", "boundaries", "transit"}
    shaded = None   # the cells a buildings tile is written for (shade_keys); None: every cell
    if "buildings" in layers and (shaded := shaded_keys(cells, out, rect)) is not None:
        log(f"{label}: buildings for {sum(c.key in shaded for c in cells)} of {len(cells)} cells, those near a venue or terrace or in a small place")
    if "venues" in layers:
        for c in coarse:
            if not done(out, "venues", c): write(path(out, "venues", c), osm_venues(c))
    if "streets-main" in layers: do_main_streets([c for c in wide if not done(out, "streets-main", c)], out, failures)
    if "places" in layers: do_places([c for c in wide if not done(out, "places", c)], out, failures)
    if "transit" in layers: do_transit(wide, out, failures)
    for group in build_order(cells, block, layers):
        communes, need_terraces, need_buildings, need_sources, need_streets = {}, [], [], [], []
        for c in group:
            if "sources" in layers: need_sources.append(c)
            if sources_only: continue  # no commune needed: a cell outside the départements has no published tile
            if french and in_france(c.lat, c.lon):
                communes[c.key] = read(path(out, "communes", c))
                if communes[c.key] is None:
                    try:
                        communes[c.key] = commune(c.lat, c.lon)
                        write(path(out, "communes", c), communes[c.key])
                    except SourceError as e:
                        failures.append((c.key, "communes", str(e)))
            # A box of départements (the petite couronne) keeps only their cells.
            if departements and not any(x["code"][:2] in departements for x in communes.get(c.key) or []):
                outside.add(c.key); continue
            if "buildings" in layers and (shaded is None or c.key in shaded) \
                    and not (done(out, "buildings", c) and done(out, "buildings-v2", c)):
                need_buildings.append(c)
            if "streets" in layers and not done(out, "streets", c): need_streets.append(c)
            if "terraces-v2" in layers and not done(out, "terraces-v2", c):
                if french and communes.get(c.key) is None:
                    failures.append((c.key, "terraces-v2", "commune unknown, so the permit feed is too")); continue
                need_terraces.append(c)
        skipped += sum(1 for c in group if c not in need_buildings and c not in need_terraces and c not in need_sources
                       and c not in need_streets and c.key not in outside)
        if need_buildings: do_buildings(need_buildings, out, failures)
        if need_streets: do_streets(need_streets, out, failures)
        if need_terraces: do_terraces(need_terraces, communes, out, failures)
        if need_sources: do_sources(need_sources, out, failures)
    # The weekly run: a venue or terrace new since the yearly buildings run gets its cells' buildings now. The store's
    # listing (with buildings-v2/, tiles.yml) says which cells lack them; without one nothing is added.
    # ponytail: a new area's first weekly run builds all its cells' buildings here, inside the same 290 minutes.
    if "terraces-v2" in layers and "buildings" not in layers and store.LISTED is not None:
        mine = [c for c in cells if c.key not in outside]
        keys = shaded_keys(mine, out, rect) if mine else None
        if keys is not None:
            top = [c for c in mine if c.key in keys and f"tiles/buildings-v2/{c.key}.json" not in store.LISTED
                   and not done(out, "buildings-v2", c)]
            log(f"{label}: buildings for {len(top)} cells a venue or terrace newly reaches or a small place holds")
            for group in blocks(top, block): do_buildings(group, out, failures)
            if UNSHADED:  # the cells no venue or terrace reaches: what tiles.yml's prune_buildings deletes from the store
                with open(UNSHADED, "a") as f: f.writelines(c.key + "\n" for c in mine if c.key not in keys)
    # After the blocks, so a cell's sources/ tile written by this run's buildings or terraces is the one noted.
    if "boundaries" in layers: do_boundaries(city or label, city_entries or [{"box": rect, "_boundary": boundary}], [c for c in cells if c.key not in outside], out, failures)
    failed = {k for k, _, _ in failures}
    log(f"{label}: {len(cells) - len(outside) - len(failed)} of {len(cells) - len(outside)} cells complete ({skipped} already there), "
        f"{len(failed)} failed, {time.monotonic() - started:.0f} s")
    return len(cells) - len(outside), failures

def entry_layers(entry, layers):
    """The layers built for one area: streets/, streets-main/, places/ and boundaries/ only where its entry says
    "streets": true (owner, 2026-10-05: a few cities a country, chosen by hand), every other layer as asked."""
    return [l for l in layers if l not in STREET_LAYERS or entry.get("streets") is True]

def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("--cities", help="a cities.json: [{city, district?, lat, lon, half_km}] or [{city, district?, box: [s, w, n, e], departements?, boundary?, streets?}]"
                   " (boundary: a GeoJSON Polygon or MultiPolygon file, relative to the cities.json, whose cells alone are built;"
                   " streets: true for an area whose streets/ tiles are built, none otherwise)")
    a.add_argument("--city", help="a label: feeds are picked per cell, by commune or box"); a.add_argument("--lat", type=float); a.add_argument("--lon", type=float)
    a.add_argument("--half-km", type=float, default=0.5)
    a.add_argument("--block", type=int, default=3, help="cells per side asked of a source at once (default 3)")
    a.add_argument("--out", default="tiles")
    a.add_argument("--layers", default=",".join(LAYERS), help="which to write (default all): " + ", ".join(LAYERS)
                   + "; or sources, the building source of the published sources/ tiles that lack it (needs --store)")
    a.add_argument("--store", help="the published tiles' base URL (https://tiles.alephb.uk/tiles/): sources/ tiles keep its fields")
    a.add_argument("--listing", help="the store's keys (changed.py's remote.tsv): blocks with a tile missing there are built first")
    a.add_argument("--unshaded", help="a terraces-v2 run with --listing appends here the keys of the cells no venue or terrace reaches")
    a.add_argument("--extracts", default="extracts", help="where Geofabrik extracts are kept between runs (default ./extracts)")
    args = a.parse_args()
    unknown = [l for l in args.layers.split(",") if l not in LAYERS + ("sources",)]   # numbers/ is written beside streets/
    if unknown: a.error(f"unknown layer {', '.join(map(repr, unknown))}: give {', '.join(LAYERS)} or sources")
    global UNSHADED
    store.STORE = args.store and args.store.rstrip("/") + "/"
    UNSHADED = args.unshaded
    if args.listing and os.path.exists(args.listing):
        with open(args.listing) as f: store.LISTED = {line.split("\t")[0] for line in f}
    if args.cities:
        with open(args.cities) as f: entries = json.load(f)
    elif args.city and args.lat is not None and args.lon is not None:
        entries = [{"city": args.city, "lat": args.lat, "lon": args.lon, "half_km": args.half_km}]
    else:
        a.error("give --cities, or --city with --lat and --lon")
    total, failures = 0, []
    for entry in entries:
        entry["_boundary"] = None
        if entry.get("boundary"):
            with open(os.path.join(os.path.dirname(os.path.abspath(args.cities)), entry["boundary"])) as f: boundary = json.load(f)
            entry["_boundary"] = boundary.get("geometry", boundary)
        entry.setdefault("half_km", args.half_km)
    for entry in entries:
        label = entry["city"] + (f" ({entry['district']})" if entry.get("district") else "")
        layers = entry_layers(entry, args.layers.split(","))
        if not layers:
            log(f"{label}: no streets/ for this area (not flagged), nothing else asked"); continue
        # boundaries/ is the city's, built from all its flagged entries' areas at the first.
        city_entries = [e for e in entries if e["city"] == entry["city"] and "boundaries" in entry_layers(e, ["boundaries"])]
        n, f = run(label, area(entry, args.half_km), args.out, args.block, layers, args.extracts,
                   entry.get("departements"), entry["_boundary"], entry["city"], city_entries)
        total += n
        failures += [(label,) + x for x in f]
    failed = {(x[0], x[1]) for x in failures}
    if failures:
        log(f"\nFailures ({len(failed)} cells of {total}):")
        for label, key, layer, reason in failures: log(f"  {label} {key} {layer}: {reason}")
    if short(len(failed), total):
        log(f"More than {SHORT:.0%} of cells failed ({len(failed)} of {total})."); sys.exit(1)

SHORT = 0.05   # the share of failed cells that fails the run: the tiles still go up, the job turns red (tiles.yml)

def short(failed, total):
    """Too many cells failed for the run to pass (20 % until 2026-10-07, when Gijón's 1,536 of 1,536 and Genoa's
    2,472 of 3,204 failed and stayed green: the workflow's `|| true` dropped the exit code; it now turns the job red)."""
    return bool(total) and failed / total > SHORT

if __name__ == "__main__":
    main()
