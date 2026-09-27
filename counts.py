#!/usr/bin/env python3
"""Counts per city from OpenStreetMap extracts; writes no tiles.

  python3 counts.py --cities counts-cities.json --extracts ./extracts --write counts.json [--only Paris,Budapest] [--drop]
      [--boxes cities.json] [--previous <url or path>]

For each city ({"city", "country", "lat", "lon"} or {"city", "country", "box": [s, w, n, e]}):
  goingOutCells  200 m cells, keyed int(lat*500), int(lon*500), whose 400 m disk (around the
                 cell's centre) holds at least GOING_OUT named bars, pubs, beer gardens, cafés
                 and restaurants
  terraceShare   the share of those cells whose 400 m disk holds outdoor_seating=yes venues,
                 named or not, at least TERRACE_RATIO of its named ones and TERRACE_MINIMUM (the
                 same rule the tiles' reader applies)
  venues, terraces  the named venues and the outdoor-seating ones inside the city's extent
  landmarkCells  cells holding a landmark (an OpenStreetMap attraction, museum, gallery, viewpoint,
                 castle, palace, monument, memorial, fort, city gate, ruin, archaeological site or
                 place of worship carrying a wikidata or wikipedia tag) whose 400 m disk holds at
                 least LANDMARK_VENUES named venues
  coreBox, coreKm2  the box [s, w, n, e] of the going-out and landmark cells' centres padded by CORE_PAD,
                 and its area
  goingOutCellsWide, landmarkCellsWide  the going-out and landmark cells over the wide square below
  landmarksAdded the landmarks (at most 15, with their cell key) whose cell lies outside the box the
                 going-out cells alone would give
  denseBox       only when coreKm2 exceeds DENSE_KM2: the square of DENSE_KM2 holding the city's point
                 that holds the most going-out and landmark cells (a 500 m step search)

The going-out numbers are counted over the city's extent below; the core, landmark and dense fields
always over the 2 × HALF_KM square around the city's point (or its given box), so a core can grow past
the box a city is tiled with today.
  extent         "municipality" where --boxes (cities.json) holds a box for the city, with its
                 boundary polygon when it has one; "commune" for a French city counted inside its own
                 contour (geo.api.gouv.fr); else the 2 × HALF_KM square around the point

A city given as a point is taken as a square of 2 × HALF_KM a side around it unless cities.json tiles
it as a municipality. A city without a point is listed under
"noPoint" and skipped (nothing is geocoded). OpenStreetMap comes from the smallest Geofabrik
extract holding the point, filtered with osmium-tool, never from Overpass.

Scope: every city of counts-cities.json. With --previous (the published counts.json), a run
given --only keeps the published rows of the cities it did not count.

Also writes the same rows as CSV beside --write, sorted by going-out cells, most first.
Needs Python 3 (standard library only) and osmium-tool on the PATH.
"""
import argparse, csv, datetime, json, math, os, sys, time, urllib.parse
from make_tiles import (AMENITIES, M, Cell, SourceError, bounds, contains, download, features, geofabrik_pbf, get_json, index,
                        metres, osmium, outer_rings, padded)

GOING_OUT = 10   # named venues in the disk for a going-out cell
TERRACE_RATIO, TERRACE_MINIMUM = 0.30, 5   # a terrace cell: outdoor-seating venues vs named ones in the disk
RADIUS = 400     # metres
HALF_KM = 12     # half-side of the box around a city's point
CORE_PAD = 2000  # metres around the going-out and landmark cells
LANDMARK_VENUES = 5  # named venues in the disk for a landmark's cell to join the core
LANDMARK_TAGS = {"tourism": {"attraction", "museum", "gallery", "viewpoint"},
                 "historic": {"castle", "palace", "monument", "memorial", "fort", "city_gate", "ruins", "archaeological_site"},
                 "building": {"cathedral", "basilica", "mosque", "synagogue", "temple", "church"}}
LANDMARKS_LISTED = 15
DENSE_KM2 = 150  # a core larger than this also gets its densest square of this area


def venues(pbf):
    """(lat, lon, named, outdoor) for every bar, pub, beer garden, café and restaurant of the extract."""
    kept = pbf + ".counts.pbf"
    try:
        osmium("tags-filter", pbf, "nwr/amenity=" + ",".join(AMENITIES), "-o", kept, "--overwrite")
        out = []
        for f in features(kept):
            tags, g = f["properties"], f["geometry"]
            if tags.get("amenity") not in AMENITIES: continue
            if g["type"] == "Point": lon, lat = g["coordinates"][:2]
            else:  # an area at its mean vertex
                ring = next(outer_rings(g), None)
                if not ring: continue
                lat, lon = (sum(v[k] for v in ring) / len(ring) for k in ("latitude", "longitude"))
            out.append((lat, lon, bool(str(tags.get("name", "")).strip()), tags.get("outdoor_seating") == "yes"))
        return out
    finally:
        if os.path.exists(kept): os.remove(kept)


def landmarks(pbf):
    """(lat, lon, name) for every landmark of the extract: one of LANDMARK_TAGS and a wikidata or wikipedia tag."""
    kept = pbf + ".landmarks.pbf"
    try:
        osmium("tags-filter", pbf, *(f"nwr/{k}={','.join(sorted(v))}" for k, v in LANDMARK_TAGS.items()), "-o", kept, "--overwrite")
        out = []
        for f in features(kept):
            tags, g = f["properties"], f["geometry"]
            if not (tags.get("wikidata") or tags.get("wikipedia")): continue
            if not any(tags.get(k) in v for k, v in LANDMARK_TAGS.items()): continue
            if g["type"] == "Point": lon, lat = g["coordinates"][:2]
            else:
                ring = next(outer_rings(g), None)
                if not ring: continue
                lat, lon = (sum(v[k] for v in ring) / len(ring) for k in ("latitude", "longitude"))
            out.append((lat, lon, str(tags.get("name") or tags.get("wikidata") or tags.get("wikipedia"))))
        return out
    finally:
        if os.path.exists(kept): os.remove(kept)


def city_box(e):
    if e.get("box"): return tuple(e["box"])
    return padded((e["lat"], e["lon"], e["lat"], e["lon"]), HALF_KM * 1000)


def municipal(cities_json):
    """city name -> (box, boundary or None) from a cities.json: its first plain box entry per city (not a
    departements one), with its boundary polygon when it has one — the municipality where it is tiled that way."""
    if not cities_json or not os.path.exists(cities_json): return {}
    out = {}
    for e in json.load(open(cities_json)):
        if not e.get("box") or e.get("departements") or e["city"] in out: continue
        boundary = None
        if e.get("boundary"):
            with open(os.path.join(os.path.dirname(os.path.abspath(cities_json)), e["boundary"])) as f: boundary = json.load(f)
            boundary = boundary.get("geometry", boundary)
        out[e["city"]] = (tuple(e["box"]), boundary)
    return out


def french_commune(name, lat, lon):
    """(box, contour) of the French commune called `name` that holds the row's point — the geo API's
    contours, so Levallois is counted inside Levallois and not over 24 km of Paris. None when unknown."""
    q = urllib.parse.urlencode({"nom": name, "fields": "nom,code,contour", "format": "geojson", "geometry": "contour", "limit": "10"})
    try: found = get_json("https://geo.api.gouv.fr/communes?" + q).get("features") or []
    except SourceError: return None
    for f in found:
        g = f.get("geometry")
        if g and contains(g, lat, lon): return bounds(g), g
    return None


within = lambda r, lat, lon: r[0] <= lat <= r[2] and r[1] <= lon <= r[3]


def disks(points, box, boundary=None):
    """cell key -> [named, outdoor] venues within RADIUS of its centre, for the cells in the box (and boundary)."""
    inside = lambda lat, lon: within(box, lat, lon) and (boundary is None or contains(boundary, lat, lon))
    reach = padded(box, RADIUS)
    near = {}  # cell key -> [named, outdoor] within RADIUS of its centre
    dlat = RADIUS / 111_320
    for lat, lon, named, outdoor in points:
        if not (named or outdoor) or not within(reach, lat, lon): continue
        dlon = dlat / math.cos(math.radians(lat))
        for ky in range(index(lat - dlat) - 1, index(lat + dlat) + 2):
            for kx in range(index(lon - dlon) - 1, index(lon + dlon) + 2):
                c = Cell(ky, kx)
                if not inside(c.lat, c.lon) or metres(c.lat, c.lon, lat, lon) > RADIUS: continue
                v = near.setdefault((ky, kx), [0, 0])
                v[0] += named
                v[1] += outdoor
    return near


def count(points, box, boundary=None):
    """Going-out cells, terrace cells among them, named venues and terraces in the box (inside the boundary when given)."""
    inside = lambda lat, lon: within(box, lat, lon) and (boundary is None or contains(boundary, lat, lon))
    near = disks(points, box, boundary)
    going = [v for v in near.values() if v[0] >= GOING_OUT]
    centres = [(Cell(*k).lat, Cell(*k).lon) for k, v in near.items() if v[0] >= GOING_OUT]
    in_box = [p for p in points if inside(p[0], p[1])]
    terrace = lambda v: v[1] >= max(TERRACE_MINIMUM, TERRACE_RATIO * v[0])
    return len(going), sum(terrace(v) for v in going), sum(p[2] for p in in_box), sum(p[3] for p in in_box), centres


def core_cells(points, marks, box):
    """(going-out cell centres, {landmark cell key: [names]}) over the box: a landmark's cell qualifies with
    at least LANDMARK_VENUES named venues in its disk."""
    near = disks(points, box)
    going = [(Cell(*k).lat, Cell(*k).lon) for k, v in near.items() if v[0] >= GOING_OUT]
    cells = {}
    for lat, lon, name in marks:
        k = (index(lat), index(lon))
        if within(box, lat, lon) and near.get(k, [0])[0] >= LANDMARK_VENUES: cells.setdefault(k, []).append(name)
    return going, cells


def km2(box):
    s, w, n, e = box
    return (n - s) * M * (e - w) * M * math.cos(math.radians((s + n) / 2)) / 1e6


def core(centres):
    """(box, km²) of the cells' centres padded by CORE_PAD, or (None, 0) without any."""
    if not centres: return None, 0
    lats, lons = [c[0] for c in centres], [c[1] for c in centres]
    box = padded((min(lats), min(lons), max(lats), max(lons)), CORE_PAD)
    return box, km2(box)


def dense(centres, lat, lon, area=DENSE_KM2, step=500):
    """The square of `area` km² that holds (lat, lon) and the most going-out cells, its centre moved by `step` m."""
    half = math.sqrt(area) * 500  # metres
    dlat, dlon = 1 / M, 1 / (M * math.cos(math.radians(lat)))
    best = None
    for dy in range(-int(half), int(half) + 1, step):
        for dx in range(-int(half), int(half) + 1, step):
            box = padded((lat + dy * dlat, lon + dx * dlon) * 2, half)
            held = sum(box[0] <= a <= box[2] and box[1] <= o <= box[3] for a, o in centres)
            if best is None or held > best[0]: best = held, box
    return best[1]


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--cities", default="counts-cities.json")
    a.add_argument("--extracts", required=True, help="folder the Geofabrik extracts are kept in")
    a.add_argument("--write", default="counts.json")
    a.add_argument("--only", default="", help="comma-separated city names")
    a.add_argument("--boxes", default="cities.json", help="a cities.json whose municipal boxes and boundaries replace the 24 km square")
    a.add_argument("--drop", action="store_true", help="delete each extract once its cities are counted (small disks)")
    a.add_argument("--previous", default="", help="the published counts.json (URL or path): its rows for cities not counted this run are kept")
    args = a.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    cities = [e for e in json.load(open(args.cities)) if not only or e["city"] in only]
    no_point = [f"{e['city']}, {e['country']}" for e in cities if "lat" not in e and not e.get("box")]

    # A city tiled as a municipality is counted over that box and boundary; the others over the 12 km square.
    munis = municipal(args.boxes)
    for e in cities:  # French communes not tiled as a box: their own contour, one geo API call each
        if e["city"] not in munis and e.get("country") == "France" and "lat" in e:
            shape = french_commune(e["city"], e["lat"], e["lon"])
            if shape: munis[e["city"]] = shape; e["_commune"] = True
    extent = lambda e: munis.get(e["city"], (city_box(e), None))
    groups, failed = {}, []  # extract URL -> cities, so each extract is read once
    for e in cities:
        if "lat" not in e and not e.get("box"): continue
        s, w, n, east = extent(e)[0]
        try: groups.setdefault(geofabrik_pbf((s + n) / 2, (w + east) / 2), []).append(e)
        except SourceError as x: failed.append(f"{e['city']}: {x}")

    rows = []
    for url, members in groups.items():
        t = time.monotonic()
        try:
            pbf = download(url, args.extracts)
            points, marks = venues(pbf), landmarks(pbf)
        except SourceError as x:
            failed += [f"{e['city']}: {x}" for e in members]
            continue
        print(f"{url.rsplit('/', 1)[1]}: {len(points)} venues, {len(marks)} landmarks read in {time.monotonic() - t:.0f} s", file=sys.stderr)
        for e in members:
            t = time.monotonic()
            box, boundary = extent(e)
            going, terrace, named, outdoor, _ = count(points, box, boundary)
            wide_going, marked = core_cells(points, marks, city_box(e))  # the core over the wide square, whatever the extent
            centres = wide_going + [(Cell(*k).lat, Cell(*k).lon) for k in marked]
            core_box, core_km2 = core(centres)
            only_going = core(wide_going)[0]
            added = [{"name": n, "cell": f"{k[0]},{k[1]}"} for k, names in marked.items() for n in names
                     if not only_going or not within(only_going, Cell(*k).lat, Cell(*k).lon)][:LANDMARKS_LISTED]
            rows.append({"city": e["city"], "country": e["country"], "goingOutCells": going,
                         "terraceShare": round(terrace / going, 3) if going else None, "venues": named, "terraces": outdoor,
                         "extent": "commune" if e.get("_commune") else "municipality" if e["city"] in munis else f"{2 * HALF_KM} km square",
                         "coreBox": [round(x, 4) for x in core_box] if core_box else None, "coreKm2": round(core_km2, 1),
                         "landmarkCells": len(marked), "landmarksAdded": added,
                         "goingOutCellsWide": len(wide_going), "landmarkCellsWide": len(marked)})
            if core_km2 > DENSE_KM2 and "lat" in e:
                rows[-1]["denseBox"] = [round(x, 4) for x in dense(centres, e["lat"], e["lon"])]
            print(f"  {e['city']}: {going} going-out cells, {terrace} with terraces, {named} venues, {outdoor} terraces, {len(marked)} landmark cells, core {core_km2:.0f} km² ({time.monotonic() - t:.1f} s)", file=sys.stderr)
        if args.drop: os.remove(pbf)

    # A partial run (--only) must not shrink the published file: the other cities keep their last rows.
    if args.previous:
        try:
            import urllib.request
            raw = urllib.request.urlopen(urllib.request.Request(args.previous, headers={"User-Agent": "compromise-tiles"})).read() \
                if "://" in args.previous else open(args.previous, "rb").read()
            counted = {r["city"] for r in rows}
            rows += [r for r in json.loads(raw).get("cities", []) if r["city"] not in counted]
        except Exception as x:  # noqa: BLE001 — a missing or unreadable previous file is not a reason to fail the run
            print(f"previous counts not merged: {x}", file=sys.stderr)
    rows.sort(key=lambda r: -r["goingOutCells"])
    with open(args.write, "w") as f:
        json.dump({"updated": datetime.date.today().isoformat(), "cities": rows, "noPoint": no_point, "failed": failed},
                  f, ensure_ascii=False, indent=1)
    with open(os.path.splitext(args.write)[0] + ".csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["city", "country", "goingOutCells", "terraceShare", "venues", "terraces", "extent", "coreBox", "coreKm2", "landmarkCells", "denseBox", "goingOutCellsWide", "landmarkCellsWide"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} cities, {len(no_point)} without a point, {len(failed)} failed -> {args.write}", file=sys.stderr)
    for x in failed: print(f"  failed {x}", file=sys.stderr)


if __name__ == "__main__":
    main()
