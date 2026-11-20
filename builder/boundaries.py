"""boundaries/: each neighbourhood's outline whole in the cell of its label point."""
import math, os, re
from builder import extract
from builder.cells import area, cells_in, index, M, MAIN
from builder.net import log, SourceError
from builder.places import place_key
from builder.store import note_sources, published_sources, write
from builder.area_sources import AREA_SOURCES, BOUNDARY_TOLERANCE, BOUNDARY_UNIT

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
