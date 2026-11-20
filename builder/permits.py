"""Terrace permit feeds: fetching, cleaning names, matching them to OpenStreetMap's venues."""
import csv, datetime, io, json, math, re, ssl, time, urllib.error, urllib.parse, urllib.request, zoneinfo
from builder.cells import COARSE, in_feed, index, metres, padded
from builder.net import get, get_json, keyed_host, log, redact, SourceError
from builder.projections import mercator_to_wgs84, rd_new_to_wgs84, utm_to_wgs84
from builder.shapes import number
from builder.names import folded, MATCH_REACH, names_match, normalised_name
from builder.apis import arcgis, arcgis_filter, in_bbox, opendatasoft, socrata, socrata_filter
from builder.store import published_items
from builder.permit_table import PERMIT_CITIES

def permit_items(features, c, point="geo_point_2d"):
    """GeoJSON permits → terraces."""
    out = []
    for f in features:
        p = f.get("properties") or {}
        g = f.get("geometry") or {}
        if g.get("type") == "Point": lon, lat = g["coordinates"][:2]
        elif g.get("type") == "MultiPoint" and g.get("coordinates"): lon, lat = g["coordinates"][0][:2]  # Toronto
        elif isinstance(p.get(point), dict): lat, lon = p[point]["lat"], p[point]["lon"]
        elif isinstance(p.get("geo_point_2d"), dict): lat, lon = p["geo_point_2d"]["lat"], p["geo_point_2d"]["lon"]
        elif g.get("type") in ("Polygon", "MultiPolygon"):  # Vilnius: the mean vertex
            rings = g["coordinates"] if g["type"] == "Polygon" else [r for poly in g["coordinates"] for r in poly]
            points = [v for r in rings for v in r if len(v) >= 2]
            if not points: continue
            lat, lon = sum(v[1] for v in points) / len(points), sum(v[0] for v in points) / len(points)
        else: continue
        kind = ""
        for field in c["kinds"]:
            v = p.get(field)
            if isinstance(v, str): kind = v; break
            if isinstance(v, (int, float)) and v > 0: kind = "TERRASSE FERMEE" if "ferm" in field else "TERRASSE OUVERTE"; break
        if not c["kinds"]: kind = "TERRASSE"
        # Washington DC: the kind text says enclosed ("New Sidewalk Cafe Enclosed", not "Un-Enclosed") or not.
        elif c.get("enclosed"): kind = "TERRASSE FERMEE" if c["enclosed"] in kind else "TERRASSE OUVERTE"
        item = {"kind": kind, "coordinate": {"latitude": lat, "longitude": lon}}
        name = p.get(c["name"]) if c["name"] else None
        if isinstance(name, str) and c.get("clean"): name = CLEAN[c["clean"]](name)
        if name: item["name"] = name
        out.append(item)
    return out

def cut_before_first_digit(text):
    """Camden's name is the full address: "Goodfare Italian Restaurant  26 - 28 Parkway…" ."""
    m = re.search(r"\d", text)
    if not m: return text
    return text[:m.start()].strip() or None

def boulevard_name(text):
    """Basel's permit description: "Boulevardrestaurant SCHNABEL  Fläche 57 m2…" (boulevardName)."""
    m = re.match(r"^Boulevard\S*\s*(?:-\s*)?(?:Restaurant\s+)?(.*?)(?:\s{2}|,|$)", text)
    return (m.group(1).strip(" \t\"'“”„«»?!.:;-") or None) if m else None

def vilnius_venue_name(text):
    """Vilnius's Imone: "UAB „holder“ / „venue“" — the venue, else the holder's quoted name, else the
    text less its legal form (vilniusVenueName)."""
    trim = lambda t: re.sub(r"^[\s\x00-\x1f\x7f-\x9f]+|[\s\x00-\x1f\x7f-\x9f]+$", "", t)
    quoted = re.search(r"„([^“]+)“", text)
    venue = text.split("/", 1)[1] if "/" in text else quoted.group(1) if quoted else text
    name = trim(re.sub(r"[„“”\"]", "", venue))
    return trim(re.sub(r"^(UAB|MB|AB|IĮ|VšĮ|ŽŪB|TŪB|KŪB)\s+", "", name)) or None

def riga_venue_name(text):
    """Riga's nosaukums is the holder's company: "Muca Bistro Bar SIA", "SIA Piga Avotu" — less its legal form."""
    name = re.sub(r"^(SIA|AS|IK)\s+|\s+(SIA|AS|IK)$", "", text.strip()).strip(" \"'“”„")
    return name or None

def toronto_name(text):
    """CaféTO's OPERATOR_NAME: "None" and "PUBLIC PARKLET" name no venue."""
    return None if text.strip().upper() in ("NONE", "PUBLIC PARKLET") else text.strip()

def helsinki_venue_name(text):
    """Helsinki's nimi: "Terassialue: On The Rocks", "Terassialue  25m2 Chaos Bar", "Terassialueet: Oluthuone Haavi",
    "Kesäterassi Kallionhovi" — the venue after the area word and any size; a bare "Talviterassi" names none."""
    name = re.sub(r"^\s*(Terassi\s?alue(et)?|Terassalue|Kesäterassi|Talviterassi)\s*:?\s*(\d+\s*m2\s*)?", "", text, flags=re.I)
    return name.strip(" \t\"'“”„:") or None

def seattle_venue_name(text):
    """Seattle's PROJECT_NAME: "Mecca | Sidewalk Cafe on Queen Anne Ave N" names the venue before the pipe. The free-text
    forms ("SIDEWALK CAFE", "HOPVINE PUB SIGNAGE AND SIDEWALK CAFE") and a first part that is a kind ("Outdoor Dining |…")
    name none — a wrong name would block the door rule."""
    if "|" not in text: return None
    venue = text.split("|", 1)[0].strip(" \t\"'")
    if not re.search(r"[A-Za-z]", venue) or re.search(r"cafe|dining|sign|streatery|transfer|ownership|street use", venue, re.I): return None
    return venue

def formerly_cut(text):
    """Orlando's project_name: "The Cask (formerly Slyfox Game Room)" is The Cask."""
    return re.sub(r"\s*\((?:formerly|fka)\b[^)]*\)", "", text, flags=re.I).strip() or None

def not_applicable(text):
    """"N/A", a blank or a bare number names no company (Washington DC's ApplicantCompany on most rows)."""
    name = text.strip()
    return None if name.upper() in ("N/A", "NA", "NONE") or not re.search(r"[A-Za-z]", name) else name

CLEAN = {"digit": cut_before_first_digit, "boulevard": boulevard_name, "vilnius": vilnius_venue_name,
         "riga": riga_venue_name, "toronto": toronto_name, "helsinki": helsinki_venue_name, "seattle": seattle_venue_name,
         "n/a": not_applicable, "formerly": formerly_cut}
_venue_tiles = {}   # {key: items} read once per run

def venue_tiles_around(lat, lon):
    """The keys of the published venue tiles a venue within MATCH_REACH of the point can sit in (one to four)."""
    s, w, n, e = padded((lat, lon, lat, lon), MATCH_REACH)
    return sorted({f"{index(a, COARSE)},{index(b, COARSE)}" for a in (s, n) for b in (w, e)})

def venues_near(lat, lon, city):
    """The published venue tiles' items a venue within MATCH_REACH of the point can be among. A venue tile that could not
    be read fails the feed (half an answer is not a tile), a missing one holds no venue."""
    venues = []
    for key in venue_tiles_around(lat, lon):
        if key not in _venue_tiles: _venue_tiles[key] = published_items("venues", key, bust=True)
        if isinstance(_venue_tiles[key], SourceError): raise SourceError(f"{city}: venues/{key}: {_venue_tiles[key]}")
        venues += _venue_tiles[key]
    return venues

def surviving(items, city):
    """The permits a venue of today stands under: a named one with a venue within MATCH_REACH under a matching name."""
    out = []
    for t in items:
        wanted = normalised_name(t.get("name"))
        if not wanted: continue
        lat, lon = t["coordinate"]["latitude"], t["coordinate"]["longitude"]
        if any(metres(lat, lon, v["coordinate"]["latitude"], v["coordinate"]["longitude"]) <= MATCH_REACH
               and names_match(normalised_name(v.get("name")), wanted) for v in venues_near(lat, lon, city)):
            out.append(t)
    return out

# Riga's permits name the holder's company ("Funny Fox SIA" for the bar Jautrā lapsa), and the feed has no trading-name
# field (GEO RĪGA's two terrace services carry the same nosaukums, 2026-11-11). A `match="trading"` row writes, as the
# permit's name, the name of the venue at its spot: the nearest named venue of the published venues/ tiles (OpenStreetMap's)
# within TRADING_REACH, when the next one stands at least TRADING_MARGIN farther. A holder's name that already matches a
# venue within NAME_REACH (the reach a venue's terrace is looked for in) is kept, and so is an unclear spot's.
NAME_REACH, TRADING_REACH, TRADING_MARGIN = 30, 15, 5

def trading_name(t, venues):
    """The permit `t` under the trading name of the venue at its spot, or as it came (see above)."""
    lat, lon = t["coordinate"]["latitude"], t["coordinate"]["longitude"]
    near = sorted(((metres(lat, lon, v["coordinate"]["latitude"], v["coordinate"]["longitude"]), v["name"])
                   for v in venues if v.get("name")), key=lambda x: x[0])
    wanted = normalised_name(t.get("name"))
    if wanted and any(d <= NAME_REACH and names_match(normalised_name(n), wanted) for d, n in near): return t
    if not near or near[0][0] > TRADING_REACH: return t
    if len(near) > 1 and near[1][0] - near[0][0] < TRADING_MARGIN: return t
    return dict(t, name=near[0][1])

def trading_names(items, city):
    return [trading_name(t, venues_near(t["coordinate"]["latitude"], t["coordinate"]["longitude"], city)) for t in items]

def amsterdam(c, rect):
    """DSO REST: a circle in WGS84 (lon,lat,metres) around the box, HAL pages of 1,000, points in RD New."""
    s, w, n, e = rect
    lat, lon = (s + n) / 2, (w + e) / 2
    radius = math.ceil(metres(lat, lon, n, e)) + 1
    q = urllib.parse.urlencode({"locatie[within]": f"{lon},{lat},{radius}", "_pageSize": "1000"})
    url, out = f"https://{c['host']}/v1/{c['dataset']}/?" + q, []
    resource = c["dataset"].split("/")[-1]
    while url:
        root = get_json(url)
        for item in (root.get("_embedded") or {}).get(resource, []):
            point = (item.get("locatie") or {}).get("coordinates") or []
            if item.get("terrasgeometrie") is None or len(point) < 2: continue
            lat, lon = rd_new_to_wgs84(point[0], point[1])
            entry = {"kind": "TERRASSE", "coordinate": {"latitude": lat, "longitude": lon}}
            if item.get(c["name"]): entry["name"] = item[c["name"]]
            out.append(entry)
        url = ((root.get("_links") or {}).get("next") or {}).get("href")
    return out

_whole = {}  # whole-file feeds, fetched once per run

def madrid_all(c):
    """Madrid CSV: ';', quoted, a BOM; "Abierta" only; any of four light-structure flags means enclosed."""
    rows = [[cell.strip('"\ufeff') for cell in line.split(";")]
            for line in get(f"https://{c['host']}/{c['dataset']}").decode("utf-8").splitlines() if line]
    header, out = rows[0], []
    col = {name: k for k, name in enumerate(header)}
    enclosure = [col[n] for n in ("construccion_ligera_fachada_es", "construccion_ligera_bordillo_es",
                                  "construccion_ligera_fachada_ra", "construccion_ligera_bordillo_ra") if n in col]
    for r in rows[1:]:
        if len(r) != len(header) or r[col["desc_situacion_terraza"]] != "Abierta": continue
        x, y = number(r[col["coordenada_x_local"]]), number(r[col["coordenada_y_local"]])
        if x is None or y is None: continue
        lat, lon = utm_to_wgs84(x, y)
        item = {"kind": "TERRASSE FERMEE" if any(r[k] == "True" for k in enclosure) else "TERRASSE OUVERTE",
                "coordinate": {"latitude": lat, "longitude": lon}}
        if r[col["rotulo"]]: item["name"] = r[col["rotulo"]]
        out.append(item)
    if not out: raise SourceError("Madrid: no terrace parsed")  # a changed header must not read as "no terraces"
    return out

def fnmt_context():
    """Seville's server sends its certificate without the intermediate: add FNMT's own
    (fetched from the URL the certificate names) so verification can complete. Nothing is
    trusted that a browser following the AIA link would not trust."""
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=get("https://www.cert.fnmt.es/certs/ACCOMP.crt"))
    return context

def seville_all(c):
    """Seville GeoJSON: a frontage line at its mean vertex; a permit past its end date dropped."""
    root = get(f"https://{c['host']}/{c['dataset']}", parse=json.loads, context=fnmt_context())
    today = datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Madrid")).date()
    out = []
    for f in root.get("features") or []:
        p = f.get("properties") or {}
        end = p.get("Final Periodo Autorizado")
        try:
            if end and datetime.datetime.strptime(end, "%d/%m/%Y").date() < today: continue
        except ValueError: pass
        g = f.get("geometry") or {}
        coords = g.get("coordinates") or []
        points = [v for line in coords for v in line] if g.get("type") == "MultiLineString" else coords
        points = [v for v in points if isinstance(v, list) and len(v) >= 2]
        if not points: continue
        item = {"kind": "TERRASSE", "coordinate": {"latitude": sum(v[1] for v in points) / len(points),
                                                   "longitude": sum(v[0] for v in points) / len(points)}}
        if p.get(c["name"]): item["name"] = p[c["name"]]
        out.append(item)
    if not out: raise SourceError("Seville: no terrace parsed")  # as for Madrid
    return out

def barcelona_all(c):
    """Barcelona: CKAN's newest CSV resource, WGS84 LATITUD/LONGITUD, no name. Read through the datastore API:
    the CSV's download URL answers a bot-detection page since 2026-10 (302 to /challenge)."""
    api = f"https://{c['host']}/data/api/3/action/"
    package = get_json(api + f"package_show?id={c['dataset']}")
    resource = next(r["id"] for r in package["result"]["resources"] if (r.get("format") or "").upper() == "CSV")
    rows = []
    while True:
        page = get_json(api + "datastore_search?" + urllib.parse.urlencode(
            {"resource_id": resource, "limit": "10000", "offset": str(len(rows))}))["result"]
        rows += page["records"]
        if not page["records"] or len(rows) >= page["total"]: break
    out = []
    for r in rows:
        lat, lon = number(r.get("LATITUD")), number(r.get("LONGITUD"))
        if lat is None or lon is None: continue
        out.append({"kind": "TERRASSE", "coordinate": {"latitude": lat, "longitude": lon}})
    if not out: raise SourceError("Barcelona: no terrace parsed")  # as for Madrid
    return out

def sf_register(host, licence_type):
    """Every issued licence of one type on a council's "sf/control" public register (Southwark, Tower Hamlets): the
    page hands out a session (in its links, ;jsessionid=…) and a CSRF token, which the register's search needs."""
    page = get(f"https://{host}/sf/control/publicregister").decode("utf-8", "replace")
    session = re.search(r";jsessionid=[\w.]+", page)
    token = re.search(r'name="csrf-token" content="([^"]+)"', page)
    if not (session and token): raise SourceError(f"{host}: no session or CSRF token on the register's page")
    data = urllib.parse.urlencode({"licenceType": licence_type, "licenceStatus": "licence", "keyword": " ", "type": "licence"})
    answer = get(f"https://{host}/sf/control/searchPublicRegister{session.group(0)}", data=data.encode(), parse=json.loads,
                 headers={"X-CSRF-Token": token.group(1)})
    return json.loads(answer["results"])

def southwark_all(c, today=None):
    """Southwark's pavement licences: approved, unexpired on the build's day (approvedEndDate dd/mm/yyyy), at the
    register's own "lat,lon", one terrace a point (a renewal and a temporary licence, or Borough Market's traders, share
    one); no name (the register names the holder, never kept)."""
    today = today or datetime.date.today()
    out, seen = [], set()
    for r in sf_register(c["host"], "Pavement"):
        if r.get("status") != "APP_APPROVED": continue
        try: end = datetime.datetime.strptime(r.get("approvedEndDate") or "", "%d/%m/%Y").date()
        except ValueError: continue
        lat, _, lon = (r.get("latLng") or "").partition(",")
        lat, lon = number(lat.strip()), number(lon.strip())
        if end < today or lat is None or lon is None or (lat, lon) in seen: continue
        seen.add((lat, lon))
        out.append({"kind": "TERRASSE", "coordinate": {"latitude": lat, "longitude": lon}})
    return out

def csv_all(c):
    """A whole CSV (La Rochelle, San Diego): comma-separated, quoted, a BOM; the point a "lat,lon" column, or a
    (lat, lon) pair of columns, the kind a column kept only for the values in `keep`, those in `covered` written
    TERRASSE FERMEE, and `as_kind` naming the kind written for every kept row; `where` keeps a row only when
    each of its columns holds one of the listed values; no `name` column, no name (the door rule)."""
    text = get(f"https://{c['host']}/{c['dataset']}").decode("utf-8-sig")
    kind, out = c["kinds"][0], []
    for r in csv.DictReader(io.StringIO(text)):
        if r[kind] not in c["keep"]: continue
        if any(r.get(k) not in v for k, v in c.get("where", {}).items()): continue
        if isinstance(c["point"], tuple): lat, lon = (r[k] or "" for k in c["point"])
        else: lat, _, lon = (r[c["point"]] or "").partition(",")
        lat, lon = number(lat.strip()), number(lon.strip())
        if lat is None or lon is None: continue
        if c.get("grid"): lat, lon = c["grid"](lon, lat)  # a projected pair, given (northing, easting): Orlando
        item = {"kind": "TERRASSE FERMEE" if r[kind] in c.get("covered", []) else c.get("as_kind", r[kind]),
                "coordinate": {"latitude": lat, "longitude": lon}}
        name = r[c["name"]].strip() if c["name"] and r[c["name"]] else None
        if name and c.get("clean"): name = CLEAN[c["clean"]](name)
        if name: item["name"] = name
        out.append(item)
    if not out: raise SourceError(f"{c['city']}: no terrace parsed")  # as for Madrid
    return out

def gothenburg_all(c):
    """Gothenburg's serving licences: one ';'-separated CSV with a BOM, read whole. A terrace where Serveringstyper lists
    Uteservering and the premises serve the public (ServeringTill names Allmänheten, alone or with closed companies);
    the point the WGS84 lat/long columns (a few rows have none), the name Namn (the premises, never the holder)."""
    text = get(f"https://{c['host']}/{c['dataset']}").decode("utf-8-sig")
    out = []
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        if "Uteservering" not in [v.strip() for v in (r.get("Serveringstyper") or "").split(",")]: continue
        if "Allmänheten" not in (r.get("ServeringTill") or ""): continue
        lat, lon = number(r.get("lat")), number(r.get("long"))
        if lat is None or lon is None: continue
        item = {"kind": "TERRASSE", "coordinate": {"latitude": lat, "longitude": lon}}
        if (r.get(c["name"]) or "").strip(): item["name"] = r[c["name"]].strip()
        out.append(item)
    if not out: raise SourceError("Gothenburg: no terrace parsed")  # as for Madrid
    return out

def whole_file(c, rect, load):
    if c["city"] not in _whole:
        try:
            _whole[c["city"]] = load(c)
        except SourceError:
            raise
        except Exception as e:  # noqa: BLE001 — a renamed column or a changed package fails this feed, not the run
            raise SourceError(f"{c['city']}: {type(e).__name__}: {e}") from e
    s, w, n, e = rect
    return [t for t in _whole[c["city"]] if s <= t["coordinate"]["latitude"] <= n and w <= t["coordinate"]["longitude"] <= e]

_fields_logged = set()

def stockholm(c, rect, today=None):
    """Stockholm's markupplåtelser (WFS 1.1.0 behind a key in the URL path): the uteservering kind of ärendekategori,
    unexpired. The key's absence never reaches here (permit_city leaves the row out); it is checked again for check_data."""
    host = keyed_host(c)
    if host is None: raise SourceError(f"{c['city']}: {c['key']} is not set")
    s, w, n, e = rect
    params = {"service": "WFS", "version": "1.1.0", "request": "GetFeature", "typeName": c["dataset"],
              "outputFormat": "application/json", "srsName": "EPSG:4326", "bbox": f"{w},{s},{e},{n},EPSG:4326"}
    try:
        features = get_json(f"https://{host}?" + urllib.parse.urlencode(params)).get("features") or []
    except SourceError as error:
        raise SourceError(redact(str(error))) from None
    if features and c["city"] not in _fields_logged:  # field names only, never values: the schema the row guessed at
        _fields_logged.add(c["city"])
        log(f"    {c['city']} permits: fields {sorted((features[0].get('properties') or {}).keys())}")
    today = today or datetime.date.today().isoformat()
    kept = []
    for f in features:
        p = {folded(k): v for k, v in (f.get("properties") or {}).items()}
        if "uteservering" not in folded(str(p.get("arendekategori") or "")): continue
        # Expired: an end date (slutdatum, tilldatum, giltig_till, datum_tom…) before today.
        ends = [str(v)[:10] for k, v in p.items() if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v)
                and re.search(r"slut|till|tom$|upphor", k)]
        if any(d < today for d in ends): continue
        g = f.get("geometry") or {}
        if g.get("type") == "Point" and len(g.get("coordinates") or []) >= 2 and g["coordinates"][0] > 45:
            g = dict(g, coordinates=g["coordinates"][1::-1])  # latitude first (59, 18): an EPSG:4326 axis order
        kept.append(dict(f, geometry=g))
    return [t for t in permit_items(kept, c)
            if s <= t["coordinate"]["latitude"] <= n and w <= t["coordinate"]["longitude"] <= e]

def oslo(c, rect):
    """Oslo: latitude-first bbox, UTM 32N answers, outdoor hours only; the holder never asked."""
    s, w, n, e = rect
    q = urllib.parse.urlencode({"map": "AAPNING", "service": "WFS", "version": "1.1.0", "request": "GetFeature",
                                "typename": c["dataset"], "propertyName": "OBJEKTNAVN,UTE_TID",
                                "outputFormat": "application/json; subtype=geojson; charset=UTF-8",
                                "bbox": f"{s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326"})
    out = []
    for f in get_json(f"https://{c['host']}?" + q).get("features") or []:
        p, point = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or []
        if not str(p.get("UTE_TID") or "").strip() or len(point) < 2: continue
        lat, lon = utm_to_wgs84(point[0], point[1], zone=32)
        item = {"kind": "TERRASSE", "coordinate": {"latitude": lat, "longitude": lon}}
        if p.get("OBJEKTNAVN"): item["name"] = p["OBJEKTNAVN"]
        out.append(item)
    return out

def permits(c, rect):
    """A feed's terraces in the rect; a `match="venues"` row keeps only those a venue of today stands under, a
    `match="trading"` row names each after the venue at its spot."""
    items = fetched_permits(c, rect)
    if c.get("match") == "venues": return surviving(items, c["city"])
    return trading_names(items, c["city"]) if c.get("match") == "trading" else items

def fetched_permits(c, rect):
    s, w, n, e = rect or (None,) * 4
    if c.get("shape") == "oslo": return oslo(c, rect)
    if c.get("shape") == "stockholm": return stockholm(c, rect)
    if c.get("shape") == "amsterdam": return amsterdam(c, rect)
    if c.get("shape") == "madrid": return whole_file(c, rect, madrid_all)
    if c.get("shape") == "seville": return whole_file(c, rect, seville_all)
    if c.get("shape") == "barcelona": return whole_file(c, rect, barcelona_all)
    if c.get("shape") == "csv": return whole_file(c, rect, csv_all)
    if c.get("shape") == "southwark": return whole_file(c, rect, southwark_all)
    if c.get("shape") == "gothenburg": return whole_file(c, rect, gothenburg_all)
    if c.get("shape") == "geojson":  # a static GeoJSON of points, fetched whole once per run (Toronto)
        return whole_file(c, rect, lambda c: permit_items(get_json(f"https://{c['host']}/{c['dataset']}").get("features") or [], c))
    if c.get("shape") == "socrata":
        where = f"within_box({c['point']}, {n}, {w}, {s}, {e})" + (f" AND {socrata_filter(c['filter'])}" if c.get("filter") else "")
        return permit_items(socrata(c["host"], c["dataset"], where, ",".join(f for f in [c["name"], c["point"]] if f)), c)
    if c.get("shape") == "wfs" and c.get("whole") and rect:  # a small layer, read whole once per run (Vienna)
        return whole_file(c, rect, lambda c: fetched_permits(c, None))
    if c.get("shape") == "wfs":
        # WFS 1.1.0: longitude first, and the
        # bbox needs its own trailing CRS or the server silently answers zero.
        params = {"service": "WFS", "version": "1.1.0", "request": "GetFeature",
                  "typeName": c["dataset"], "outputFormat": "json", "srsName": c.get("srs", "EPSG:4326")}
        if rect is None:  # the whole layer
            if c.get("filter"): params["CQL_FILTER"] = c["filter"]
        elif c.get("filter"):
            # Copenhagen's GeoServer refuses bbox alongside CQL_FILTER.
            params["CQL_FILTER"] = f"{c['filter']} AND BBOX({c.get('geom', 'wkb_geometry')},{w},{s},{e},{n},'EPSG:4326')"
        else:
            params["bbox"] = f"{w},{s},{e},{n},EPSG:4326"
        features = get_json(f"https://{c['host']}?" + urllib.parse.urlencode(params)).get("features") or []
        if c.get("srs") == "EPSG:3857":  # Thessaloníki: metres to the millimetre, where its degrees are rounded to ~100 m
            for f in features:
                g = f.get("geometry") or {}
                if g.get("type") in ("Point", "MultiPoint"):
                    pts = g["coordinates"] if g["type"] == "MultiPoint" else [g["coordinates"]]
                    g.update(type="MultiPoint", coordinates=[mercator_to_wgs84(*p[:2])[::-1] for p in pts])
        return permit_items(features, c)   # polygons land on their mean vertex
    if c.get("shape") == "arcgis":
        fields = [f for f in [c["name"]] + c["kinds"] if f]
        return permit_items(arcgis(f"https://{c['host']}/{c['dataset']}", rect, fields, arcgis_filter(c.get("filter") or "1=1"),
                                   c.get("oid", "OBJECTID")), c)
    point = c.get("point", "geo_point_2d")
    where = in_bbox(point, rect) + (f" and {c['filter']}" if c.get("filter") else "")
    # Empty name (Lorient) must not leave a leading comma in `select`: an ODSQLSyntaxError.
    select = ",".join(f for f in [c["name"], point] + c["kinds"] if f)
    year = time.gmtime().tm_year
    for y in (year, year - 1):
        try: features = opendatasoft(c["host"], c["dataset"].replace("{year}", str(y)), where, select)
        except SourceError:
            if "{year}" in c["dataset"] and y == year: continue  # this year's dataset may not exist yet
            raise
        if features or "{year}" not in c["dataset"]: break
    return permit_items(features, c, point)

def permit_city(cell, communes):
    """The permit feed for a cell: by INSEE code when a commune answered, else by box (and boundary, when the row has one)."""
    if communes: return next((c for c in PERMIT_CITIES if c["insee"] == communes[0]["code"]), None)
    feed = next((c for c in PERMIT_CITIES if in_feed(c, cell.lat, cell.lon)), None)
    if feed and keyed_host(feed) is None:  # a keyed row without its key (a local run, a fork): OSM only, said once
        if feed["city"] not in _keyless:
            _keyless.add(feed["city"])
            log(f"  {feed['city']} permits: skipped, {feed['key']} is not set (OSM terraces only)")
        return None
    return feed

_keyless = set()
