"""The current area's OpenStreetMap extract, read once (class OSM), and its readers."""
import os, subprocess, xml.etree.ElementTree as ET
from builder.cells import COARSE, index, MAIN, padded
from builder.net import SourceError
from builder.shapes import building, osm_height, osm_tagged, outer_rings, vertex
from builder.streets import associated_streets, house_numbers, interpolation, MAIN_STREETS, MAIN_TOLERANCE, NUMBER_PAD, simplified, street_kind, STREET_KINDS, STREET_PAD, STREET_TOLERANCE
from builder.transit import transit_lines, TRANSIT_MODES
from builder.osmium import buckets, features, mean_point, osmium
from builder.places import distinct_places, english, PLACE_KINDS, place_levels
from builder.venues import AMENITIES, chains_only, hotels, in_hotels, MATCH_ONLY, read_venues

class OSM:
    """An extract around one area: its venues in memory, bucketed by ~1 km, and its buildings cut
    from disk a block at a time — a whole box of them does not fit in memory (Paris intra-muros
    peaked at 3.5 GB held; the petite couronne is ten times the area)."""
    def __init__(self, pbf, rect):
        s, w, n, e = rect
        self.box = rect
        self.venues, self.matches, self.walls, self.roads, self.last = {}, {}, pbf + ".buildings.pbf", pbf + ".streets.pbf", None
        self.named, self.admin, self._places = pbf + ".places.pbf", pbf + ".admin.pbf", None
        self.numbers, self.pbf = pbf + ".numbers.pbf", pbf
        box, kept = pbf + ".box.pbf", pbf + ".venues.pbf"
        try:
            osmium("extract", "-b", f"{w},{s},{e},{n}", pbf, "-o", box, "--overwrite")
            osmium("tags-filter", box, "w/building", "-o", self.walls, "--overwrite")
            osmium("tags-filter", box, "w/highway=" + ",".join(STREET_KINDS), "-o", self.roads, "--overwrite")
            osmium("tags-filter", box, "nwr/place=" + ",".join(PLACE_KINDS), "-o", self.named, "--overwrite")
            osmium("tags-filter", box, "r/boundary=administrative", "-o", self.admin, "--overwrite")
            osmium("tags-filter", box, "nwr/addr:housenumber", "w/addr:interpolation", "r/type=associatedStreet", "-o", self.numbers, "--overwrite")
            # MATCH_ONLY venues apart: only venues/ tiles carry them, not terraces, shade or small-place counts.
            for venue in in_hotels(chains_only(read_venues(box, kept, AMENITIES + MATCH_ONLY)), hotels(box, kept)):
                lat, lon = venue["coordinate"]["latitude"], venue["coordinate"]["longitude"]
                (self.venues if venue["amenity"] in AMENITIES else self.matches).setdefault(buckets(lat, lon, lat, lon)[0], []).append(venue)
        finally:
            for f in (box, kept):
                if os.path.exists(f): os.remove(f)

    def venues_in(self, rect):
        """The extract's venues in a rect, read again from the whole extract: past the area's box (small_place_keys)."""
        s, w, n, e = rect
        box, kept = self.pbf + ".far.pbf", self.pbf + ".far-venues.pbf"
        try:
            osmium("extract", "-b", f"{w},{s},{e},{n}", self.pbf, "-o", box, "--overwrite")
            return read_venues(box, kept)
        finally:
            for f in (box, kept):
                if os.path.exists(f): os.remove(f)

    def buildings(self, rect, tagged=False):
        """Building ways meeting the rect. osmium keeps a way with a node in the cut, whole; a big one
        (a station hall) can reach a cell with every node outside it, hence 500 m more — the
        in-memory index had them, and near_buildings trims the rest. With tagged=True, a building
        whose height comes from its tags says which ("tagged": osm_tagged()), for combined(). The last
        answer is kept: London's lidar is measured on OSM's own footprints, so its block asks twice."""
        if self.last and self.last[0] == (rect, tagged): return self.last[1]
        s, w, n, e = padded(rect, 500)
        cut = self.walls + ".cut.pbf"
        osmium("extract", "-b", f"{w},{s},{e},{n}", self.walls, "-o", cut, "--overwrite")
        out = [dict(building(ring, osm_height(f["properties"])), **({"tagged": osm_tagged(f["properties"])} if tagged and osm_tagged(f["properties"]) else {})) for f in features(cut)
               if "building" in f["properties"] and f["geometry"]["type"] != "Point" for ring in outer_rings(f["geometry"])]
        self.last = ((rect, tagged), out)
        return out

    def streets(self, rect, kinds=None, tolerance=None):
        """The street ways meeting the rect, simplified: [(kind, [(lat, lon), …], name or "")]; with `kinds`, the
        ways of those highway values only. osmium keeps a way with a node in the cut, whole; a long
        straight one can cross a block with no node in it, hence STREET_PAD more."""
        s, w, n, e = padded(rect, STREET_PAD)
        cut = self.roads + ".cut.pbf"
        osmium("extract", "-b", f"{w},{s},{e},{n}", self.roads, "-o", cut, "--overwrite")
        out = []
        for f in features(cut, "linestring"):
            kind = street_kind(f["properties"])
            if kinds is not None and f["properties"].get("highway") not in kinds: continue
            if kind and f["geometry"]["type"] == "LineString":
                out.append((kind, simplified([(lat, lon) for lon, lat in f["geometry"]["coordinates"]], tolerance or STREET_TOLERANCE),
                            str(f["properties"].get("name", "")).strip()))
        return out

    def addresses(self, rect):
        """The house numbers around the rect: ([(lat, lon, addr:street, number)], [interpolation(), …]). A node
        at its point, a building at its outer ring's mean vertex; an addr:interpolation way kept when both its
        end nodes carry a number. A house with no addr:street takes the name of the associatedStreet relation
        it is a "house" of (France's way: three in four of Paris's numbered nodes, 2026-10-10). osmium keeps a
        way whole, its nodes with it."""
        s, w, n, e = padded(rect, NUMBER_PAD)
        cut = self.numbers + ".cut.pbf"
        osmium("extract", "-b", f"{w},{s},{e},{n}", self.numbers, "-o", cut, "--overwrite")
        points, ends, ways, houses = [], {}, [], associated_streets(osmium("cat", cut, "-t", "relation", "-f", "osm"))
        for f in features(cut, "point,linestring,polygon", ids=True):
            tags, geometry = f["properties"], f["geometry"]
            if geometry["type"] == "LineString":  # a closed building way comes out as a line too: skipped here
                if tags.get("addr:interpolation"): ways.append((tags, geometry["coordinates"]))
                continue
            street = str(tags.get("addr:street") or houses.get(tags.get("@type", "")[:1] + str(tags.get("@id")), "")).strip()
            numbers = house_numbers(tags.get("addr:housenumber", ""))
            if geometry["type"] == "Point": ends[tuple(geometry["coordinates"][:2])] = (street, numbers)
            at = mean_point(geometry)
            if street and numbers and at: points += [(at[0], at[1], street, x) for x in numbers]
        return points, [i for tags, coordinates in ways if (i := interpolation(tags, coordinates, ends))]

    def places(self):
        """The area's named places (PLACE_KINDS), read once: a node at its point, an area at its
        outer ring's mean vertex, as the venues; each with the admin_level of the boundary it is
        or stands for (place_levels)."""
        if self._places is None:
            found = []
            for f in features(self.named, ids=True):
                tags, geometry = f["properties"], f["geometry"]
                name = str(tags.get("name", "")).strip()
                if tags.get("place") not in PLACE_KINDS or not name: continue
                at = mean_point(geometry)
                if not at: continue
                found.append(english({"name": name, "kind": tags["place"], "coordinate": vertex(*at), "osm": tags["@type"][0] + str(tags["@id"])},
                                     tags.get("name:en")))
            self._places = distinct_places(place_levels(found, self.boundaries()))
        return self._places

    def boundaries(self, pbf=None):
        """The administrative boundary relations of the area (or of `pbf`): [{"id": "r…", "name", "admin_level" (int),
        "place", "labels", "centres" (the ids, "n…", of its label and admin_centre member nodes), "geometry" (None when
        its ring is not whole in the extract)}]. One without a name or a numeric admin_level is left out."""
        pbf = pbf or self.admin
        shapes = {"r" + str(f["properties"]["@id"]): f["geometry"] for f in features(pbf, "polygon", ids=True)
                  if f["properties"].get("@type") == "relation"}
        out = []
        for r in ET.fromstring(osmium("cat", pbf, "-t", "relation", "-f", "osm")).iter("relation"):
            tags = {t.get("k"): t.get("v") for t in r.iter("tag")}
            name, level = (tags.get("name") or "").strip(), tags.get("admin_level", "")
            if tags.get("boundary") != "administrative" or not name or not level.isdigit(): continue
            role = lambda kind: {"n" + m.get("ref") for m in r.iter("member") if m.get("type") == "node" and m.get("role") == kind}
            out.append({"id": "r" + r.get("id"), "name": name, "name_en": tags.get("name:en"), "admin_level": int(level), "place": tags.get("place"),
                        "labels": role("label"), "centres": role("admin_centre"), "geometry": shapes.get("r" + r.get("id"))})
        return out

    def areas(self, rect):
        """The administrative boundaries (boundaries()) and the place polygons ([(tags, geometry)]) meeting the rect,
        whole: cut from the extract with osmium's smart strategy, which completes a boundary or multipolygon relation
        crossing the cut's edge (the area's own box drops it: Kyiv's raions, 2026-10-10)."""
        s, w, n, e = rect
        cut, kept = self.pbf + ".areas.pbf", self.pbf + ".areas-kept.pbf"
        try:
            osmium("extract", "-b", f"{w},{s},{e},{n}", "-s", "smart", "-S", "types=boundary,multipolygon", self.pbf, "-o", cut, "--overwrite")
            osmium("tags-filter", cut, "r/boundary=administrative", "wr/place=" + ",".join(PLACE_KINDS), "-o", kept, "--overwrite")
            places = [(f["properties"], f["geometry"]) for f in features(kept, "polygon")]
            return self.boundaries(kept), places
        finally:
            for f in (cut, kept):
                if os.path.exists(f): os.remove(f)

    def transit(self):
        """transit_lines() of the whole extract, not the area's box: a line runs past it, and its far stops are where
        a transfer is made. The relations alone first, then only their node and way members (ways with their nodes)."""
        routes, ids, kept = (self.pbf + x for x in (".routes.pbf", ".stops.txt", ".stops.pbf"))
        modes = ",".join(TRANSIT_MODES)
        try:
            osmium("tags-filter", self.pbf, "r/route=" + modes, "r/route_master=" + modes, "-R", "-o", routes, "--overwrite")
            relations = ET.fromstring(osmium("cat", routes, "-f", "osm"))
            wanted = sorted({m.get("type")[0] + m.get("ref") for m in relations.iter("member") if m.get("type") in ("node", "way")})
            if not wanted: return []
            with open(ids, "w") as f: f.write("\n".join(wanted) + "\n")
            # getid exits 1 when an id is not in the extract (a far stop of a line leaving the region): what is there is kept.
            done = subprocess.run(["osmium", "getid", self.pbf, "-i", ids, "-r", "-o", kept, "--overwrite"], capture_output=True, text=True)
            if done.returncode > 1: raise SourceError(f"osmium: {done.stderr.strip()[:200]}")
            return transit_lines(relations, ET.fromstring(osmium("cat", kept, "-f", "osm")))
        finally:
            for f in (routes, ids, kept):
                if os.path.exists(f): os.remove(f)

    def near(self, table, rect):
        """Everything in the buckets the rect touches, once each: callers keep what is near enough."""
        seen = {}
        for k in buckets(*rect):
            for item in table.get(k, ()): seen[id(item)] = item
        return list(seen.values())

_osm = None  # the current area's OSM, set by run()

def osm_buildings(rect, tagged=False): return _osm.buildings(rect, tagged)

def osm_streets(rect): return _osm.streets(rect)

def osm_main_streets(rect): return _osm.streets(rect, MAIN_STREETS, MAIN_TOLERANCE)

def osm_addresses(rect): return _osm.addresses(rect)

def osm_places(cell):
    """The named places of one ~4 km cell, the key's own truncation deciding the edge."""
    return [p for p in _osm.places()
            if f"{index(p['coordinate']['latitude'], MAIN)},{index(p['coordinate']['longitude'], MAIN)}" == cell.key]

def osm_terraces(rect):
    """Outdoor seating on a bar, pub, beer garden, café or restaurant, named or not."""
    out = []
    for v in _osm.near(_osm.venues, rect):
        if not v["outdoor_seating"]: continue
        item = {"kind": "TERRASSE (OSM)", "coordinate": v["coordinate"]}
        if v["name"]: item["name"] = v["name"]
        out.append(item)
    return out

def osm_venues(cell):
    """The named venues of one ~2 km cell, MATCH_ONLY ones included, the key's own truncation deciding the edge."""
    return [v for table in (_osm.venues, _osm.matches) for v in _osm.near(table, cell.rect) if v["name"]
            and f"{index(v['coordinate']['latitude'], COARSE)},{index(v['coordinate']['longitude'], COARSE)}" == cell.key]
