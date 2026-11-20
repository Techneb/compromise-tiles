"""transit/: OpenStreetMap's route relations, one line whole in every cell a stop falls in."""
import re
from builder.cells import index, MAIN, metres
from builder.streets import simplified

# transit/ (2026-10-13): OpenStreetMap's public-transport route relations. Metro, tram, rail and bus.
TRANSIT_MODES = ("subway", "tram", "train", "light_rail", "bus", "trolleybus", "aerialway")
TRANSIT_SERVICE_OUT = ("high_speed", "long_distance", "national", "international", "night", "tourism")
# Intercity and night networks whose routes carry no service tag (Montréal, Lyon, Paris, 2026-10-13): a network starting
# with one is left out.
TRANSIT_NETWORK_OUT = ("VIA Rail", "Amtrak", "TGV", "Ouigo", "Intercités", "Eurostar", "Noctilien",
                       # Coaches (Amsterdam, Brussels, Prague, 2026-10-13): Flibco's routes have an operator, no network.
                       "Flixbus", "FlixBus", "FlixTrain", "Flibco", "RegioJet",
                       # Coaches and a sightseeing bus (Madrid, Barcelona, Lisbon, 2026-10-13): Spain's state
                       # long-distance concessions ("VAC-023"), Portugal's national express network.
                       "Autobuses de España", "Rede Nacional de Expressos", "Barcelona Bus Turístic")
# Sightseeing buses and heritage trams whose routes carry no service=tourism or tourism=yes (2026-10-19, every city's
# published tiles: Big Bus and Open Tour in Paris and Lyon, City Sightseeing in Glasgow, Turin and Palma, Kuala Lumpur's
# hop-on hop-off, Cologne's network "tourism", Denver's heritage trolley, Rotterdam's museum tram): matched anywhere in
# the network or operator, any case. Not a bare "touris": coach firms named so run ordinary lines (Clermont's P32,
# Munich's 459, Zagreb's 160).
TRANSIT_TOURIST = re.compile(r"big bus|open ?tour|sightseeing|hop.?on|tootbus|^tourism$|museum|heritage", re.I)
# High-speed trains whose ref is the brand, under the infrastructure's network ("Frecciarossa", RFI; Rome, Milan,
# 2026-10-13): a ref starting with one is left out.
TRANSIT_BRAND_OUT = ("Frecciarossa", "Frecciargento", "Frecciabianca", "Italo", "Railjet", "Nightjet", "EuroCity", "Intercity")
# Rome's regional coaches (Co.Tra.L.), by network or operator: their refs are route codes ("P0001" on some thirty
# routes), not lines. COTRAL's rail lines (Metromare, Roma–Viterbo) are not buses and stay.
TRANSIT_BUS_OUT = ("Co.Tra.L", "CoTraL", "COTRAL")
# A bus, trolleybus or tram whose ref is "N" and a number is a night line (Amsterdam's N47, Brussels' Noctis N05,
# London's N29, Berlin's N7); networks that number them otherwise are in TRANSIT_REF_OUT.
TRANSIT_NIGHT_REF = re.compile(r"N ?\d+[A-Za-z]?")
# {network: {mode: refs}} left out by a network's own numbering (2026-10-13): Prague's night trams 91–99 and buses
# 901–960, its tourist trams 41 and 42; Amsterdam's museum tram (EMA); Lisbon's Carris night buses 201–210.
TRANSIT_REF_OUT = {"Pražská integrovaná doprava": {"tram": re.compile(r"9\d|4[12]"), "bus": re.compile(r"9\d\d")},
                   "EMA": {"tram": re.compile(r".*")}, "Carris": {"bus": re.compile(r"2\d\d")}}
# Networks whose four-digit train refs are a line's series, not a train's number: the Dutch "4000" is a Sprinter line
# (its intercity series are tagged long_distance, so only the Sprinters stay).
TRANSIT_SERIES = ("Hoofdrailnet Nederland",)
# A bus whose stops reach farther than this from its first is a coach between towns (Barcelona Nord to the Val
# d'Aran, Lisbon to Porto, Sagalés to Vic), not a city's bus: kilometres, straight.
TRANSIT_BUS_REACH = 60
# A service pattern in brackets after the line's ref ("C-2 (CIVIS)", Madrid's semi-fast C-2) is the line's.
TRANSIT_REF_SUFFIX = re.compile(r"\s*\([^()]*\)$")
TRANSIT_TWIN = 60          # metres: a stop and the next this close are one (a stop_position and its platform)
TRANSIT_UNIT = 100_000     # 1e-5° steps
TRANSIT_TOLERANCE = 10     # metres a line's simplified track may stray from its ways (Douglas–Peucker)
STOP_ROLES = ("stop", "stop_entry_only", "stop_exit_only")

def transit_lines(relations, objects):
    """[{"mode", "ref", "colour"?, "network"?, "operator"?, "stops": [name, …], "points": [(lat, lon), …], "shape": [[(lat, lon), …], …]}] from
    two osmium XML roots: the route and route_master relations, and their node and way members (ways with their nodes).
    A route's stops are its members with a stop role in order, else its platforms (a way at its mean vertex); twins
    within TRANSIT_TWIN are one. Its shape is its other ways, chained in member order (chained()), simplified.
    A route with no ref, a long-distance train (TRANSIT_SERVICE_OUT, TRANSIT_NETWORK_OUT, a ref holding a train's
    number but in TRANSIT_SERIES, a TRANSIT_BRAND_OUT ref), a coach (by network, or stops past TRANSIT_BUS_REACH), a
    night or tourist line (TRANSIT_NIGHT_REF, TRANSIT_REF_OUT, tourism=yes, TRANSIT_TOURIST), a TRANSIT_BUS_OUT bus or under two stops is left out;
    so is one whose stop names are all on a longer kept route of the same mode and ref: the other direction, a short
    working. A ref's bracketed service pattern is dropped first (TRANSIT_REF_SUFFIX)."""
    tags_of = lambda e: {x.get("k"): x.get("v") for x in e.iter("tag")}
    nodes = {n.get("id"): (float(n.get("lat")), float(n.get("lon")), tags_of(n).get("name", "")) for n in objects.iter("node") if n.get("lat")}
    ways, tracks = {}, {}
    for w in objects.iter("way"):
        tracks[w.get("id")] = [r.get("ref") for r in w.iter("nd") if r.get("ref") in nodes]
        ring = [nodes[i] for i in tracks[w.get("id")]]
        if ring: ways[w.get("id")] = (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring), tags_of(w).get("name", ""))
    master = {}
    for r in relations.iter("relation"):
        if tags_of(r).get("type") == "route_master":
            for m in r.iter("member"): master[m.get("ref")] = tags_of(r)
    found = []
    for r in relations.iter("relation"):
        tags = tags_of(r)
        mode, up = tags.get("route"), master.get(r.get("id"), {})
        ref = (tags.get("ref") or up.get("ref") or "").strip()
        ref = TRANSIT_REF_SUFFIX.sub("", ref) or ref
        if tags.get("type") != "route" or mode not in TRANSIT_MODES or not ref or tags.get("service") in TRANSIT_SERVICE_OUT: continue
        if (tags.get("tourism") or up.get("tourism")) == "yes": continue   # a tourist route (Versailles' road train)
        if TRANSIT_TOURIST.search(tags.get("network") or up.get("network") or "") or TRANSIT_TOURIST.search(tags.get("operator") or up.get("operator") or ""): continue
        network, operator = (tags.get("network") or up.get("network") or ""), (tags.get("operator") or up.get("operator") or "")
        # A ref listing train numbers ("20;22;24"), or a train's number ("870603", Madrid's "RE 18009"), is a timetable's,
        # not a line's.
        if ";" in ref or (mode == "train" and re.search(r"\d{4}", ref) and network not in TRANSIT_SERIES): continue
        if network.startswith(TRANSIT_NETWORK_OUT) or operator.startswith(TRANSIT_NETWORK_OUT) or ref.startswith(TRANSIT_BRAND_OUT): continue
        if mode == "bus" and (network.startswith(TRANSIT_BUS_OUT) or operator.startswith(TRANSIT_BUS_OUT)): continue
        if mode in ("bus", "trolleybus", "tram") and TRANSIT_NIGHT_REF.fullmatch(ref): continue
        if (out := TRANSIT_REF_OUT.get(network, {}).get(mode)) and out.fullmatch(ref): continue
        members = [(m.get("type"), m.get("ref"), m.get("role") or "") for m in r.iter("member")]
        chosen = [m for m in members if m[2] in STOP_ROLES and m[0] == "node"] or [m for m in members if m[2].startswith("platform")]
        stops = []
        for kind, ref_id, _ in chosen:
            at = nodes.get(ref_id) if kind == "node" else ways.get(ref_id) if kind == "way" else None
            if not at: continue
            if stops and metres(stops[-1][0], stops[-1][1], at[0], at[1]) <= TRANSIT_TWIN:
                if not stops[-1][2] and at[2]: stops[-1] = (stops[-1][0], stops[-1][1], at[2])
                continue
            stops.append(at)
        if len(stops) < 2: continue
        if mode in ("bus", "trolleybus") and max(metres(stops[0][0], stops[0][1], s[0], s[1]) for s in stops) > TRANSIT_BUS_REACH * 1000: continue
        runs = chained([tracks.get(i, []) for kind, i, role in members if kind == "way" and not role.startswith(("stop", "platform"))])
        shape = [simplified([nodes[i][:2] for i in run], TRANSIT_TOLERANCE) for run in runs]
        line = {"mode": mode, "ref": ref, "stops": [s[2] for s in stops], "points": [(s[0], s[1]) for s in stops], "shape": shape}
        colour = (tags.get("colour") or up.get("colour") or "").strip()
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", colour): line["colour"] = colour.upper()
        for key in ("network", "operator"):
            value = (tags.get(key) or up.get(key) or "").strip()
            if value: line[key] = value
        found.append(line)
    kept = []
    for line in sorted(found, key=lambda l: -len(l["stops"])):
        names = set(line["stops"])
        if not any(k["mode"] == line["mode"] and k["ref"] == line["ref"] and names <= set(k["stops"]) for k in kept): kept.append(line)
    return kept

def chained(ways):
    """A route's track ways ([node id, …] in member order) joined into runs where an end meets the run's end (the
    first way turned round when the second meets its start); a way meeting neither starts a run. Runs under two nodes
    are dropped."""
    runs, fresh = [], False
    for w in ways:
        if len(w) < 2: continue
        run = runs[-1] if runs else []
        if fresh and run[-1] not in (w[0], w[-1]) and run[0] in (w[0], w[-1]): run.reverse()
        if run and run[-1] == w[0]: run += w[1:]; fresh = False
        elif run and run[-1] == w[-1]: run += w[-2::-1]; fresh = False
        else: runs.append(list(w)); fresh = True
    return runs

def transit_row(line, cell_key):
    """A transit/ row: the line with its "points" as [y0, x0, dy1, dx1, …], 1e-5° steps, the first from the key's corner
    (key / 25), each next from the one before; its "shape" a list of runs coded the same way, each from the corner."""
    ky, kx = map(int, cell_key.split(","))
    per = TRANSIT_UNIT // MAIN
    def flat(points):
        out, y, x = [], None, None
        for lat, lon in points:
            qy, qx = round(lat * TRANSIT_UNIT), round(lon * TRANSIT_UNIT)
            out += [qy - ky * per, qx - kx * per] if y is None else [qy - y, qx - x]
            y, x = qy, qx
        return out
    return dict(line, points=flat(line["points"]), shape=[flat(run) for run in line.get("shape", [])])

def transit_tiles(cells, lines):
    """{key: [row, …]} for the ~4 km cells: each line whole in every cell one of its stops falls in, so the lines near a
    start carry every stop a transfer can be made at. Cells with no stop get no key."""
    keys, out = {c.key for c in cells}, {}
    for line in lines:
        for key in sorted({f"{index(lat, MAIN)},{index(lon, MAIN)}" for lat, lon in line["points"]} & keys):
            out.setdefault(key, []).append(transit_row(line, key))
    return out
