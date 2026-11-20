"""streets/, streets-main/ and numbers/ from OpenStreetMap's ways and addresses."""
import math, re, unicodedata, xml.etree.ElementTree as ET
from builder.cells import FINE, index, M

# The streets one can walk or drive in a city, by the width a map would draw them: 1 a main road,
# 2 a street, 3 a pedestrian one. No motorway, no ramp onto one, no track, path or pavement.
STREET_KINDS = {"trunk": 1, "primary": 1, "secondary": 1, "primary_link": 1, "secondary_link": 1,
                "tertiary": 2, "tertiary_link": 2, "unclassified": 2, "residential": 2, "service": 2,
                "living_street": 3, "pedestrian": 3}
STREET_SERVICE_OUT = ("driveway", "parking_aisle", "drive-through", "emergency_access")
STREET_TOLERANCE = 3     # metres a simplified line may stray from the way (Douglas–Peucker)
STREET_PAD = 500         # metres asked around a block: a way crossing it with no node inside still comes
STREET_UNIT = 100_000    # coordinates written in 1e-5° steps (~1 m), 200 to a cell side
# streets-main/, for a whole city at once: the main roads only, on ~4 km cells, coarser.
MAIN_STREETS = ("trunk", "primary", "secondary", "tertiary")
MAIN_TOLERANCE = 15      # metres
MAIN_UNIT = 10_000       # 1e-4° steps (~10 m), 400 to a cell side

def street_kind(tags):
    """A way's kind (STREET_KINDS), or None when it is not kept: a tunnel, an area, an expressway,
    a private way or a service way to a single door or a parking."""
    kind = STREET_KINDS.get(tags.get("highway"))
    if not kind or tags.get("area") == "yes" or tags.get("tunnel", "no") != "no" or tags.get("motorroad") == "yes": return None
    if tags.get("access") in ("private", "no") and tags.get("foot") not in ("yes", "designated", "permissive"): return None
    if tags.get("highway") == "service" and tags.get("service") in STREET_SERVICE_OUT: return None
    return kind

def simplified(points, tolerance=STREET_TOLERANCE):
    """Douglas–Peucker on [(lat, lon)], in metres around the first point; the ends always stay."""
    if len(points) < 3: return list(points)
    lat0 = points[0][0]
    k = math.cos(math.radians(lat0))
    xy = [((lon - points[0][1]) * M * k, (lat - lat0) * M) for lat, lon in points]
    keep, stack = {0, len(points) - 1}, [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = xy[a], xy[b]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        far, worst = None, tolerance
        for i in range(a + 1, b):
            px, py = xy[i]
            d = abs(dx * (ay - py) - dy * (ax - px)) / length if length else math.hypot(px - ax, py - ay)
            if d > worst: far, worst = i, d
        if far is not None:
            keep.add(far); stack += [(a, far), (far, b)]
    return [points[i] for i in sorted(keep)]

def clipped(points, rect):
    """The pieces of a line inside a rect (s, w, n, e), each a list of (lat, lon), cut at the edges
    (Liang–Barsky per segment). A piece runs on while segments join inside."""
    s, w, n, e = rect
    pieces, piece = [], []
    for (y0, x0), (y1, x1) in zip(points, points[1:]):
        dx, dy, t0, t1 = x1 - x0, y1 - y0, 0.0, 1.0
        for p, q in ((-dx, x0 - w), (dx, e - x0), (-dy, y0 - s), (dy, n - y0)):
            if p == 0:
                if q < 0: t0, t1 = 1, 0
            elif p < 0: t0 = max(t0, q / p)
            else: t1 = min(t1, q / p)
        if t0 > t1:
            if piece: pieces.append(piece); piece = []
            continue
        a, b = (y0 + t0 * dy, x0 + t0 * dx), (y0 + t1 * dy, x0 + t1 * dx)
        if piece and piece[-1] == a: piece.append(b)
        else:
            if piece: pieces.append(piece)
            piece = [a, b]
        if t1 < 1: pieces.append(piece); piece = []
    if piece: pieces.append(piece)
    return pieces

def street_line(kind, points, cell_key, scale=FINE, unit=STREET_UNIT, name=""):
    """One street of a streets/ tile: [kind, y0, x0, dy1, dx1, …], or [kind, name, y0, …] when named,
    every coordinate in 1e-5° steps,
    the first from the key's own corner (key / 500) and each next from the one before (streets-main/:
    1e-4° steps from key / 25). Rounded absolute coordinates first, so two cells cut a street at the
    same point. None when nothing of it is left once rounded."""
    ky, kx = map(int, cell_key.split(","))
    steps = []
    for lat, lon in points:
        q = (round(lat * unit), round(lon * unit))
        if not steps or q != steps[-1]: steps.append(q)
    if len(steps) < 2: return None
    per = unit // scale
    out, (y, x) = [kind] + ([name] if name else []) + [steps[0][0] - ky * per, steps[0][1] - kx * per], steps[0]
    for qy, qx in steps[1:]:
        out += [qy - y, qx - x]
        y, x = qy, qx
    return out

def street_tiles(cells, streets, scale=FINE, unit=STREET_UNIT, named=True):
    """{key: [street line, …]} for the cells (of `scale` cells per degree), each street cut at their edges;
    `named` writes the ways' names (streets/, not streets-main/)."""
    by_key = {c.key: c for c in cells}
    out = {c.key: [] for c in cells}
    for kind, points, name in streets:
        lats, lons = [p[0] for p in points], [p[1] for p in points]
        for ky in range(index(min(lats), scale), index(max(lats), scale) + 1):
            for kx in range(index(min(lons), scale), index(max(lons), scale) + 1):
                c = by_key.get(f"{ky},{kx}")
                if c is None: continue
                for piece in clipped(points, c.rect):
                    line = street_line(kind, piece, c.key, scale, unit, name if named else "")
                    if line: out[c.key].append(line)
    return out

# numbers/ (2026-10-10), beside streets/: per named street of a cell, [name, lowest, highest] from OpenStreetMap's
# addr:housenumber + addr:street or associatedStreet (nodes, buildings) and addr:interpolation ways, the addresses
# inside the cell only. A layer of its own: the app's streets/ decoder fails a tile on a row it does not know.
NUMBER_PAD = 200          # metres asked around a block: an interpolation way across it with both ends outside
NUMBER_MAX = 100_000      # a larger "number" is a typo or a reference, not a house
INTERPOLATION_MAX = 1000  # numbers along one interpolation way at most
INTERPOLATION_STEP = {"even": 2, "odd": 2, "all": 1}  # and a number: that step; "alphabetic" is left out

def street_key(name):
    """A street name as addr:street and a way's name are compared: no case, no accents, no apostrophes
    ("l'Échaudé", "L’Echaudé"), runs of spaces as one."""
    s = "".join(ch for ch in unicodedata.normalize("NFKD", str(name)) if not unicodedata.combining(ch)).casefold()
    return " ".join(re.sub(r"['’‘ʼ`´]", "", s).split())

def house_numbers(value):
    """The numbers an addr:housenumber gives: each ";"- or ","-part's leading digits ("12bis" 12, "12 ter" 12,
    "12-14" 12, "12;14" both); none for a part that does not start with one ("A3", "Flat 2")."""
    out = []
    for part in str(value).replace(",", ";").split(";"):
        m = re.match(r"\s*(\d+)", part)
        if m and 0 < int(m.group(1)) < NUMBER_MAX: out.append(int(m.group(1)))
    return out

def associated_streets(xml):
    """{"n…"/"w…"/"r…": street} for the "house" members of the type=associatedStreet relations in an
    osmium OSM-XML dump, the street the relation's name, else its addr:street."""
    out = {}
    for r in ET.fromstring(xml).iter("relation"):
        tags = {t.get("k"): t.get("v") for t in r.iter("tag")}
        name = (tags.get("name") or tags.get("addr:street") or "").strip()
        if tags.get("type") != "associatedStreet" or not name: continue
        for m in r.iter("member"):
            if m.get("role") == "house": out.setdefault(m.get("type", "")[:1] + str(m.get("ref")), name)
    return out

def interpolation(tags, coordinates, ends):
    """An addr:interpolation way as (street, [(lat, lon), …], first, last, step), or None: its kind not
    even/odd/all or a step, or an end with no number. `ends` maps a node's (lon, lat) to its (street, numbers);
    the street is the way's addr:street, else its ends'."""
    kind = str(tags.get("addr:interpolation", "")).strip()
    step = INTERPOLATION_STEP.get(kind) or (int(kind) if kind.isdigit() and int(kind) > 0 else None)
    if not step or len(coordinates) < 2: return None
    a, b = ends.get(tuple(coordinates[0][:2])), ends.get(tuple(coordinates[-1][:2]))
    if not (a and a[1] and b and b[1]): return None
    street = str(tags.get("addr:street") or a[0] or b[0]).strip()
    return (street, [(lat, lon) for lon, lat in (p[:2] for p in coordinates)], a[1][0], b[1][0], step) if street else None

def interpolated(line, first, last, step):
    """[(lat, lon, number)] along an interpolation way, the numbers from first to last spaced evenly by length."""
    count = abs(last - first) // step
    if not count or count > INTERPOLATION_MAX: return []
    k = math.cos(math.radians(line[0][0]))
    lengths = [math.hypot(b[0] - a[0], (b[1] - a[1]) * k) for a, b in zip(line, line[1:])]
    total, out = sum(lengths), []
    for i in range(count + 1):
        at, (lat, lon) = total * i / count, line[-1]
        for (a, b), d in zip(zip(line, line[1:]), lengths):
            if at <= d and d:
                lat, lon = a[0] + (b[0] - a[0]) * at / d, a[1] + (b[1] - a[1]) * at / d
                break
            at -= d
        out.append((lat, lon, first + (step if last > first else -step) * i))
    return out

def trimmed_high(numbers):
    """The distinct numbers, sorted, without a stray top: while the highest is over 3× the next and further
    above it than the rest span plus 100 (of two alone, more than 1,000 above: no ~200 m cell spans that), it
    goes. A flat or unit number tagged as the house number (Camden High Street's 48–2213, 2026-10-10) is the
    case; a real ~200 m cell spans tens of doors, and near a grid city's numbering origin the 0 and 100 blocks
    ({40, 150} and {20, 40, 150} stay)."""
    out = sorted(set(numbers))
    while len(out) >= 2 and out[-1] > 3 * out[-2] and out[-1] - out[-2] > (out[-2] - out[0] + 100 if len(out) > 2 else 1000): out.pop()
    return out


def number_rows(tiles, points, interpolations):
    """{key: [[name, lowest, highest], …]} (numbers/) for the streets/ tiles: each named way of a tile whose
    street_key() matches the addr:street of at least two distinct numbers inside that cell (the key's
    truncation deciding), once per name as the tile writes it, sorted by name; a stray top cut first (trimmed_high)."""
    seen = {key: {} for key in tiles}
    points = list(points) + [(lat, lon, street, x) for street, line, first, last, step in interpolations
                             for lat, lon, x in interpolated(line, first, last, step)]
    for lat, lon, street, number in points:
        numbers = seen.get(f"{index(lat)},{index(lon)}")
        if numbers is not None: numbers.setdefault(street_key(street), set()).add(number)
    out = {}
    for key, lines in tiles.items():
        names = dict.fromkeys(line[1] for line in lines if len(line) > 1 and isinstance(line[1], str))
        found = [(name, trimmed_high(seen[key].get(street_key(name), ()))) for name in names]
        out[key] = sorted([name, min(n), max(n)] for name, n in found if len(n) >= 2)
    return out
