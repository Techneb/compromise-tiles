#!/usr/bin/env python3
"""Pre-cut city-data tiles for Compromise's sunny filter and venue list.

Writes one JSON array per ~200 m
cell, keyed "<int(lat*500)>,<int(lon*500)>", under buildings/,
terraces-v2/, streets/ and communes/ (French cells: the build and the coverage feed
read them, the upload leaves them out), and one per ~2 km cell, keyed
"<int(lat*50)>,<int(lon*50)>", under venues/. Beside the first two,
sources/ names what built each cell ([{"permits": city|null,
"buildings": city|"IGN"|"OSM"|city+"+OSM"}]) — what the app credits.
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
buildings-v2/ is what the store holds (buildings/ stays local, read by coverage.py; 2026-10-08):
the same footprints as
[height or null, y0, x0, dy1, dx1, …], 1e-5° steps from key / 500, heights in
0.5 m steps (BUILDINGS.md).

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

Needs Python 3 (standard library only) and osmium-tool on the PATH.
Derived tiles that include OpenStreetMap data are ODbL: publish them under
ODbL with the credit "© OpenStreetMap contributors".
"""
import argparse, array, concurrent.futures, csv, datetime, gzip, io, json, math, os, re, shutil, sqlite3, ssl, struct, subprocess, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request, xml.etree.ElementTree as ET, zlib, zoneinfo

UA = "compromise-tiles/2.0 (+https://github.com/Techneb/compromise; sunny-terrace tile generator)"
WAITS = (10, 30, 90)          # seconds before the 2nd, 3rd and 4th try
GAP = 1.0                     # seconds between two requests to one host
TIMEOUT = 60
DEAD_AFTER = 3                # a host that exhausted its retries this many times in a row is skipped for the run
TERRACE_RADIUS = 400          # metres around the cell's centre: the radius the density gate is calibrated on
# Every footprint whose box comes within this of the cell: a terrace is
# matched within 30 m of a venue and a shadow ray walks 150 m from it toward
# the sun — 180 m, plus a margin (Opéra, 2026-09-24: 690 KB → 280 KB a cell
# against a 400 m reach). Raise it if either reach grows.
BUILDING_REACH = 200
BUILDING_PAD = 250            # fetched around a block: some feeds select by a footprint's centre, not its outline

def boundary(slug):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "boundaries", slug + ".geojson")) as f: return json.load(f)

# The city permit feeds, one row each. A cell picks its feed by INSEE
# code when the commune answered (a French commune without a feed gets none, so Levallois inside
# Paris's box is OSM only), else the first box holding the cell's centre — inside the row's municipal
# boundary when it has one (a feed holds only its own city's permits, so a neighbour's tiled cells in
# its box are OSM only, credited to no feed: Vaughan in Toronto's, Westminster and Islington in Camden's).
def row(name, insee, host, dataset, name_field, kinds, lat, lon, boundary=None, **extra):
    return dict(city=name, insee=insee, host=host, dataset=dataset, name=name_field, kinds=kinds, lat=lat, lon=lon, boundary=boundary, **extra)

PERMIT_CITIES = [
    # Terrace kinds only (TERRASSE, TERRASSES, CONTRE-TERRASSE…): the feed also lists stalls (ETALAGE),
    # planchers and accessory shopfronts, ~12 % of its rows, which inflated every terrace count (2026-09-26).
    row("Paris", "75056", "parisdata.opendatasoft.com", "terrasses-autorisations", "nom_enseigne", ["typologie"],
        (48.79, 48.95), (2.17, 2.53), filter='search(typologie, "TERRASSE")'),
    row("Toulouse", "31555", "data.toulouse-metropole.fr", "terrasses-autorisees-ville-de-toulouse", "etablissement",
        ["terrasse_ouverte", "extension_terrasse", "terrasse_fermee"], (43.55, 43.65), (1.37, 1.50)),
    row("Strasbourg", "67482", "data.strasbourg.eu", "terrasses-autorisees-en-{year}", "nom_enseigne", [],
        (48.54, 48.61), (7.68, 7.81)),
    row("Anglet", "64024", "anglet-opendatapaysbasque.opendatasoft.com", "autorisations-de-terrasses-a-anglet", "enseigne",
        ["surf_terr_ouverte_littoral", "surf_terr_sol_hors_littoral", "surf_terr_ouvr_fixe_hors_littoral",
         "surf_terr_fermee_littoral", "surf_terr_vol_ferme_hors_littoral"], (43.47, 43.54), (-1.56, -1.49)),
    row("Rouen", "76540", "data.metropole-rouen-normandie.fr", "rouen-terrasse-2021-dos", "enseigne", [],
        (49.40, 49.48), (1.03, 1.17), point="geolocalisation"),
    row("Lorient", "56121", "www.opendata56.fr", "liste-des-terrasses-autorisees-ville-de-lorient", "", ["terrasse_type"],
        (47.71, 47.78), (-3.40, -3.32)),
    # Petite couronne (survey 2026-09-26): two 2020 lists, never refreshed, no licence stated by either publisher.
    # Issy's `terrasses` names the kind ("terrasse fermée" is kept as a kind, like Paris's).
    row("Issy-les-Moulineaux", "92040", "data.issy.com", "terrasses-issy-les-moulineaux", "nom", ["terrasses"],
        (48.81, 48.84), (2.23, 2.29), point="geolocalisation"),
    # ArcGIS Online, the city's terrace-fee roll (RODP): every row is a terrace; the OID field is FID.
    row("Boulogne-Billancourt", "92012", "services.arcgis.com/jVVADh16Qba3NtWq/arcgis/rest/services",
        "Commerces_avec_terasses_VBB/FeatureServer/0", "Nom_du_dos", [], (48.82, 48.86), (2.22, 2.27),
        shape="arcgis", oid="FID"),
    row("Melbourne", "", "data.melbourne.vic.gov.au", "cafes-and-restaurants-with-seating-capacity", "trading_name", [],
        (-37.90, -37.75), (144.90, 145.02), point="location",
        filter="seating_type=\"Seats - Outdoor\" and census_year>=date'2023-01-01'"),
    # Socrata: `{twoYearsAgo}` and `{today}` are resolved at run time.
    # Camden's name is a full address, cut before its first digit.
    # Held to the borough (ONS Local Authority Districts, OGL): the box reaches 40 km² of London's other tiled cells.
    row("Camden", "", "opendata.camden.gov.uk", "8ixc-jf73", "development_address", [], (51.52, 51.58), (-0.22, -0.10),
        shape="socrata", point="location", filter="decision_type = 'Granted' AND registered_date >= '{twoYearsAgo}'",
        clean="digit", boundary=boundary("camden")),
    row("New York", "", "data.cityofnewyork.us", "fpeh-f7ci", "assumed_name_s", [], (40.49, 40.92), (-74.26, -73.68),
        shape="socrata", point="location", filter="license_expiration_date >= '{today}'"),
    row("Chicago", "", "data.cityofchicago.org", "nxj5-ix6z", "doing_business_as_name", [], (41.64, 42.02), (-87.94, -87.52),
        shape="socrata", point="location", filter="expiration_date >= '{today}'"),
    row("San Francisco", "", "data.sf.gov", "dpch-7nr4", "dbaname", [], (37.70, 37.83), (-122.52, -122.35),
        shape="socrata", point="point", filter="tablesandchairs = true"),
    # DSO REST, HAL JSON, points in RD New: a terrace is a licence whose terrasgeometrie is not null.
    row("Amsterdam", "", "api.data.amsterdam.nl", "horeca/exploitatievergunning", "zaaknaam", [], (52.28, 52.43), (4.73, 5.02),
        shape="amsterdam"),
    # WFS: NAME is the placement, not the café, so no name field — door rule only.
    row("Vienna", "", "data.wien.gv.at/daten/geo", "ogdwien:SCHANIGARTENOGD", "", [], (48.10, 48.35), (16.15, 16.58),
        shape="wfs"),
    # This GeoServer refuses bbox alongside CQL_FILTER: the box folds into BBOX().
    row("Copenhagen", "", "wfs-kbhkort.kk.dk/k101/ows", "k101:raaden_over_vej_events_anonym_aktuelt", "restaurantnavn", [],
        (55.60, 55.75), (12.40, 12.70), shape="wfs", filter="sagstype='Udeservering'"),
    # One 5 MB CSV, UTM zone 30N, no query API: fetched whole once per run.
    row("Madrid", "", "datos.madrid.es",
        "dataset/200085-0-censo-locales/resource/200085-6-censo-locales/download/200085-6-censo-locales.csv",
        "rotulo", [], (40.30, 40.56), (-3.90, -3.50), shape="madrid"),
    # The name sits inside a free-text description (boulevardName).
    row("Basel", "", "data.bs.ch", "100018", "bezeichng", [], (47.51, 47.60), (7.55, 7.70),
        filter='search(bezeichng, "Boulevard") and datum_bis >= now() and belestatbe = "bewilligt"', clean="boulevard"),
    # ArcGIS REST (SITG, "Accès libre"); a year in PERIODE means granted.
    row("Geneva", "", "vector.sitg.ge.ch/arcgis/rest/services", "VDG_TERRASSE_RESTO/FeatureServer/0", "NOM_CAFE", ["OBJET"],
        (46.17, 46.24), (6.10, 6.19), shape="arcgis", filter="PERIODE LIKE '%20%'"),
    # One static GeoJSON of frontage lines, fetched whole once per run.
    row("Seville", "", "map4.urbanismosevilla.org", "IDE.Sevilla/Visor_Veladores/Data_set/I_VM_TEX_45_03_OVF_JSON.geojson",
        "Nombre Del Establecimiento", [], (37.32, 37.45), (-6.05, -5.88), shape="seville"),
    # ArcGIS MapServer (Vilniaus planas, copyright only): polygons at their mean vertex, the venue
    # after the holder's slash, unexpired only. Only Imone is asked — the layer holds e-mails too.
    row("Vilnius", "", "gis.vplanas.lt/arcgis/rest/services", "Interaktyvus_zemelapis/Zalias_Vilnius/MapServer/76", "Imone", [],
        (54.57, 54.83), (25.02, 25.48), shape="arcgis", filter="Leid_galioj IS NULL OR Leid_galioj >= CURRENT_TIMESTAMP",
        clean="vilnius"),
    # MapServer WFS, UTM 32N only: serving licences, a terrace where outdoor hours (UTE_TID) are set.
    row("Oslo", "", "od2.pbe.oslo.kommune.no/cgi-bin/wms", "skjenkebevilling_punkt", "OBJEKTNAVN", [],
        (59.81, 60.00), (10.62, 10.95), shape="oslo"),
    # Open Data BCN (CKAN): the newest CSV of the dataset's resources, read whole through the datastore API once per run; no names.
    row("Barcelona", "", "opendata-ajuntament.barcelona.cat", "terrasses-comercos-vigents", "", [],
        (41.32, 41.47), (2.05, 2.23), shape="barcelona"),
    # Hand-refreshed snapshots in this repository (`permits/`): the feeds carry street addresses only,
    # geocoded once on the Mac (2026-09-28); a build reads the static file and never calls a geocoder.
    # Buenos Aires: granted sidewalk-dining permits (CC BY 2.5 AR), USIG's normaliser; no name, the
    # feed names only the holder (a company or a person), never kept.
    row("Buenos Aires", "", "raw.githubusercontent.com", "Techneb/compromise-tiles/master/permits/buenos-aires.geojson",
        "", [], (-34.71, -34.52), (-58.54, -58.33), shape="geojson"),
    # Adelaide: City of Adelaide outdoor dining permits (CC BY), matched on the city's own property parcels.
    row("Adelaide", "", "raw.githubusercontent.com", "Techneb/compromise-tiles/master/permits/adelaide.geojson",
        "name", [], (-34.96, -34.89), (138.57, 138.63), shape="geojson"),
    # Edinburgh: the council's fortnightly tables-and-chairs permits (© City of Edinburgh Council, used despite the
    # site's terms at the owner's risk, 2026-10-05), issued and valid on the snapshot's day; geocoded by
    # permits/geocode_edinburgh.py (Nominatim at the house, else the postcode's centre). The box is the permits'.
    row("Edinburgh", "", "raw.githubusercontent.com", "Techneb/compromise-tiles/master/permits/edinburgh.geojson",
        "name", [], (55.90, 55.99), (-3.31, -3.11), shape="geojson"),
    # CKAN (Open Government Licence – Toronto): CaféTO's static GeoJSON, monthly, fetched whole once per run;
    # sidewalk, curb-lane and private patios are all open air. Points come as one-point MultiPoints. Held to the
    # City's own municipal boundary (Open Government Licence – Toronto): the box reaches Vaughan's tiled cells.
    row("Toronto", "", "ckan0.cf.opendata.inter.prod-toronto.ca",
        "dataset/3b605a2e-f3bf-4b2b-b972-c0829b2788f5/resource/aa839e97-df7f-4c65-8aba-d336bb3c8f06/download/"
        "cafe-to-locations-4326.geojson", "OPERATOR_NAME", [], (43.58, 43.86), (-79.64, -79.11),
        shape="geojson", clean="toronto", boundary=boundary("toronto")),
    # ArcGIS MapServer (GEO RĪGA, no licence stated): layer 16 is the permits in force; the name is the
    # holder's company, less its legal form. The OID field is gid.
    row("Riga", "", "georiga.lv/server/rest/services", "Ara_kafejnicas_terases/MapServer/16", "nosaukums", [],
        (56.85, 57.09), (23.93, 24.33), shape="arcgis", oid="gid", clean="riga"),
    # Opendatasoft (no licence stated): the latest permit's terrace drawing, at its centre point; the only
    # text is the drawing's file name (an address), so no name field — door rule only.
    # ArcGIS MapServer (© Donostiako Udala - Ayuntamiento de Donostia / San Sebastián, no licence
    # stated): every point a granted terrace, IzenTe the venue; the OID field is FID. The box is the layer's.
    row("San Sebastián", "", "www.donostia.eus/geozerbitzuak/rest/services", "ext/URBANISMO/MapServer/34", "IzenTe", [],
        (43.29, 43.33), (-2.02, -1.91), shape="arcgis", oid="FID"),
    row("Eindhoven", "", "data.eindhoven.nl", "terrastekeningen", "", [], (51.39, 51.50), (5.38, 5.56)),
    # Municipality of Thessaloníki's shops of health interest (GeoServer; the licence a link to the Ministry
    # of the Interior's "Ανοιχτή Άδεια"): only those with a terrace permit number, the 2017–2019 register.
    # Metres asked for: its degrees come rounded to 3 decimals (~100 m). The box is the points'.
    row("Thessaloníki", "", "sdi.thessaloniki.gr/geoserver/wfs", "KOSE:TRAP2017", "eponymia", [],
        (40.59, 40.66), (22.92, 22.99), shape="wfs", filter="adeiestrap IS NOT NULL", geom="geom", srs="EPSG:3857"),
    # Ville de La Rochelle's public-space permits ("Licence ouverte / Open Licence"): one CSV, updated daily, no
    # query API, fetched whole once per run. The point is the `coordinates` column ("lat,lon"); the feed also lists
    # furniture, decking and kiosks, so only the three terrace kinds are kept (580 of 806 rows, 2026-10-03).
    # A covered terrace is written TERRASSE FERMEE, like the closed kinds of the other feeds (2026-10-08).
    # The box is the terraces'.
    row("La Rochelle", "17300", "opendata.agglo-larochelle.fr",
        "sites/default/files/dataset/143/e9172-661e-4c90-bd87-99c8a79c551b/b_commerces_marches_vlr_aot_surfaces.csv",
        "enseigne_etablissement", ["type_surface"], (46.14, 46.18), (-1.22, -1.12), shape="csv", point="coordinates",
        keep=["Terrasse", "Terrasse - extension saisonnière", "Terrasse couverte"], covered=["Terrasse couverte"]),
    # The terrace re-survey's registers (2026-10-04). Helsinki: the city's short-term land rentals (GeoServer WFS,
    # CC BY 4.0 through HRI), summer and winter terraces only — the layer also holds parklets, art and dog fields — each
    # the terrace's polygon at its mean vertex; only current and coming rentals are served. The name is `nimi` less its
    # "Terassialue:" prefix. The box is the terraces'.
    row("Helsinki", "", "kartta.hel.fi/ws/geoserver/avoindata/wfs", "avoindata:Lyhyt_maanvuokraus_alue", "nimi", [],
        (60.14, 60.28), (24.85, 25.16), shape="wfs", filter="hakemuksen_laji IN ('Kesäterassi','Talviterassi')",
        geom="singlegeom", clean="helsinki"),
    # Gothenburg: the serving-licence register (one ';' CSV, CC0 1.0, daily), read whole once per run: a terrace where
    # Serveringstyper lists Uteservering and the premises serve the public. Alcohol-licensed premises only, so a
    # complement to OSM's terraces, not a replacement. The box is the rows'.
    row("Gothenburg", "", "catalog.goteborg.se", "store/6/resource/49543", "Namn", [], (57.57, 57.80), (11.76, 12.06),
        shape="gothenburg"),
    # Washington DC: DDOT's public-space rental permits (MapServer, CC BY 4.0), issued sidewalk cafés and streateries;
    # an enclosed café is TERRASSE FERMEE. The amendments (name change, furniture, hours) are other event types, so the
    # filter leaves them out; a renewal repeats its point and gather() drops the repeat. Only ApplicantCompany is asked
    # for: OwnerName and PermitteeName hold people's names and never reach a tile. It reads "N/A" on most rows (no name,
    # so the door rule) and a permit expediter's firm on many of the rest. The box is the permits'.
    row("Washington DC", "", "maps2.dcgis.dc.gov/dcgis/rest/services", "DDOT/TOPS/MapServer/3", "ApplicantCompany",
        ["EventTypeDescription"], (38.86, 38.98), (-77.11, -76.92), shape="arcgis", enclosed=" Enclosed", clean="n/a",
        filter="(EventTypeDescription LIKE 'New Sidewalk Cafe%' OR EventTypeDescription LIKE 'Streatery%') AND Status = 'ISSUED'"),
    # Boston: the Outdoor Dining Program's public view (ArcGIS Online, no licence stated): active outdoor dining permits
    # and dog-friendly patios, both open air, no dates. The OID field is ObjectId. The box is the points'.
    row("Boston", "", "services.arcgis.com/sFnw0xNflSi8J0uh/arcgis/rest/services",
        "Outdoor_Dining_2023_Public_View/FeatureServer/0", "doing_business_as", [], (42.24, 42.39), (-71.17, -71.00),
        shape="arcgis", oid="ObjectId"),
    # Seattle: SDOT's street-use permits (ArcGIS Online, no licence stated) have no café type, so the description is
    # searched: issued, in the last fifteen months (an annual review; the expiry is blank on most, and an enforcement
    # case has no issue date). The name is PROJECT_NAME's "Venue | kind | street" form, else none. The box is the cafés'.
    row("Seattle", "", "services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services",
        "SU_Permit_Data_Model_Relationships/FeatureServer/0", "PROJECT_NAME", [], (47.52, 47.70), (-122.42, -122.27),
        shape="arcgis", clean="seattle", filter="UPPER(PROJECT_DESCRIPTION) LIKE '%SIDEWALK CAF%' AND PERMIT_STATUS = 'Issued'"
        " AND LAST_ISSUED_DATE >= DATE '{fifteenMonthsAgo}'"),
    # Kensington and Chelsea (London): RBKC's tables-and-chairs licences (an ArcGIS MapServer, no licence stated), the
    # unexpired ones of a 2025-11 extract. Held to the borough (ONS Local Authority Districts, OGL), as Camden is: the box
    # reaches Westminster's and Hammersmith and Fulham's tiled cells.
    row("Kensington and Chelsea", "", "utility.arcgis.com/usrsvcs/servers/d70af383b66642a59ffae23125511dfc/rest/services",
        "RBKC/EnvironmentalHealth/MapServer/6", "TradingName", [], (51.47, 51.53), (-0.23, -0.15), shape="arcgis",
        filter="ExpiryDate >= CURRENT_TIMESTAMP", boundary=boundary("kensington-and-chelsea")),
    # Los Angeles: DataLA's L.A. Al Fresco dining locations (ArcGIS Online, no licence stated), a January 2021 snapshot of
    # COVID-era authorisations — 2,273 named points, no status or expiry, both types (sidewalk and private property,
    # on-street) open air. Many of those venues are gone, so a permit is kept only where a venue of today stands under its
    # name (`match="venues"`: the published venues/ tiles, OpenStreetMap's, within MATCH_REACH; owner, 2026-10-05). The box
    # is the tiled area's (cities.json): the layer covers the whole city, the venue tiles only this.
    row("Los Angeles", "", "services5.arcgis.com/7nsPwEMP38bSkCjy/arcgis/rest/services", "Al_Fresco_Dining_Locations/FeatureServer/0",
        "Business_Name", [], (34.015, 34.087), (-118.2907, -118.2073), shape="arcgis", match="venues"),
    # Stockholm: Trafikkontoret's markupplåtelser (public-ground leases, CC0 1.0 per the layer's metadata), a WFS
    # behind a free API key: `key` names the environment variable holding it (an Actions secret), which goes into
    # the URL path at fetch time and never into a log; without it the row is left out of the run, logged. Only
    # the uteservering kind of ärendekategori is kept, and only unexpired leases; the attribute names are matched
    # loosely (no sample without the key, 2026-10-05) and the first answer's field names are logged. No name
    # field until that sample shows one, so the door rule. The box is the tiled area's (cities.json); no other
    # city's cells fall in it, so no boundary.
    row("Stockholm", "", "openstreetgs.stockholm.se/geoservice/api/{key}/wfs", "od_gis:Markupplatelse_Punkt", "", [],
        (59.267, 59.371), (17.9638, 18.1642), shape="stockholm", key="STOCKHOLM_API_KEY"),
]

def in_box(c, lat, lon): return c["lat"][0] <= lat <= c["lat"][1] and c["lon"][0] <= lon <= c["lon"][1]


# ---------------------------------------------------------------- network

class SourceError(Exception):
    """A source that did not answer completely, after every retry."""

_last, _dead = {}, {}

def redact(text):
    """The text with every keyed row's secret blanked: a key sits in its feed's URL path."""
    for c in PERMIT_CITIES:
        secret = c.get("key") and os.environ.get(c["key"])
        if secret: text = text.replace(secret, "***")
    return text

def log(message): print(redact(str(message)), file=sys.stderr, flush=True)

def keyed_host(c):
    """A row's host with its API key filled in from the environment ({key}); None when the key is not set."""
    if not c.get("key"): return c["host"]
    secret = os.environ.get(c["key"], "").strip()
    return c["host"].replace("{key}", urllib.parse.quote(secret, safe="")) if secret else None

def get(url, data=None, parse=None, waits=WAITS, context=None):
    """One request, retried after each of `waits`: spaced per host, gzip asked
    for (smaller transfers are the ones the proxy lets through), the body
    parsed inside the retry so a truncated answer is asked again rather than
    trusted. A host that fails DEAD_AFTER requests in a row is skipped."""
    host = urllib.parse.urlsplit(url).hostname
    if _dead.get(host, 0) >= DEAD_AFTER:
        raise SourceError(f"{host}: skipped, failed {DEAD_AFTER} requests in a row")
    error = "?"
    for attempt in range(len(waits) + 1):
        wait = _last.get(host, 0) + GAP - time.monotonic()
        if wait > 0: time.sleep(wait)
        try:
            request = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip": body = gzip.decompress(body)
            result = parse(body) if parse else body
            _last[host], _dead[host] = time.monotonic(), 0
            return result
        except urllib.error.HTTPError as e:
            error = f"HTTP {e.code}"
            if parse:  # an answer under an error status (Catastro's "No records" report) is still an answer
                try:
                    result = parse(gzip.decompress(e.read()) if e.headers.get("Content-Encoding") == "gzip" else e.read())
                    _last[host], _dead[host] = time.monotonic(), 0
                    return result
                except Exception:  # noqa: BLE001 — not an answer, then
                    pass
            if e.code in (400, 401, 404, 405, 410, 414): break  # a wrong request stays wrong
        except Exception as e:  # noqa: BLE001 — IncompleteRead, resets, timeouts, bad JSON: all worth another try
            error = f"{type(e).__name__}: {e}"[:200]
        _last[host] = time.monotonic()
        if attempt < len(waits):
            log(f"    {host}: {error}; retry in {waits[attempt]} s")
            time.sleep(waits[attempt])
    _dead[host] = _dead.get(host, 0) + 1
    raise SourceError(f"{host}: {error}")

def get_json(url, data=None, waits=WAITS): return get(url, data, parse=json.loads, waits=waits)

# ---------------------------------------------------------------- cells

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


# ---------------------------------------------------------------- OpenStreetMap, from an extract

GEOFABRIK = "https://download.geofabrik.de/index-v1.json"
AMENITIES = ("bar", "pub", "biergarten", "cafe", "restaurant")  # every venue type
GRID = 100  # the in-memory index's buckets: 1/100°, ~1 km
_regions = None

def geofabrik_pbf(lat, lon):
    global _regions
    if _regions is None: _regions = get_json(GEOFABRIK)["features"]
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

def download(url, folder, max_days=6, headers=None):
    """A whole file, kept in `folder` and fetched again once older than `max_days` (Geofabrik updates daily).
    The bytes are kept as served: with `headers` asking for gzip, a store that holds the file gzipped
    (PLATEAU's) sends it so, and the reader inflates it."""
    file = os.path.join(folder, url.rsplit("/", 1)[1])
    if os.path.exists(file) and time.time() - os.path.getmtime(file) < max_days * 86_400: return file
    os.makedirs(folder, exist_ok=True)
    log(f"  downloading {url}")
    # Retried: parallel build jobs pull extracts at the same moment and Geofabrik answers some of
    # them 504 or hangs (2026-09-27, three of three jobs, two lost); a minute later it serves them.
    for attempt, wait in enumerate((60, 120, 240, None)):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(file + ".tmp", "wb") as f:
                shutil.copyfileobj(response, f, 1 << 20)
                # http.client ends a body cut short without an error: Gijón's extract, 2026-10-07, read
                # "PBF error: unexpected EOF" in osmium and failed all 1,536 cells. Short is retried.
                expected = response.headers.get("Content-Length")
                if expected and f.tell() != int(expected): raise IOError(f"{f.tell()} of {expected} bytes")
            break
        except Exception as e:  # noqa: BLE001 — the area's OSM layers fail, not the run
            if wait is None: raise SourceError(f"{url}: {type(e).__name__}: {e}") from e
            log(f"  {url.rsplit('/', 1)[1]}: {type(e).__name__}: {e}; retry in {wait} s")
            time.sleep(wait)
    os.replace(file + ".tmp", file)
    return file

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

class OSM:
    """An extract around one area: its venues in memory, bucketed by ~1 km, and its buildings cut
    from disk a block at a time — a whole box of them does not fit in memory (Paris intra-muros
    peaked at 3.5 GB held; the petite couronne is ten times the area)."""
    def __init__(self, pbf, rect):
        s, w, n, e = rect
        self.venues, self.walls, self.roads, self.last = {}, pbf + ".buildings.pbf", pbf + ".streets.pbf", None
        self.named, self.admin, self._places = pbf + ".places.pbf", pbf + ".admin.pbf", None
        self.numbers = pbf + ".numbers.pbf"
        box, kept = pbf + ".box.pbf", pbf + ".venues.pbf"
        try:
            osmium("extract", "-b", f"{w},{s},{e},{n}", pbf, "-o", box, "--overwrite")
            osmium("tags-filter", box, "w/building", "-o", self.walls, "--overwrite")
            osmium("tags-filter", box, "w/highway=" + ",".join(STREET_KINDS), "-o", self.roads, "--overwrite")
            osmium("tags-filter", box, "nwr/place=" + ",".join(PLACE_KINDS), "-o", self.named, "--overwrite")
            osmium("tags-filter", box, "r/boundary=administrative", "-o", self.admin, "--overwrite")
            osmium("tags-filter", box, "nwr/addr:housenumber", "w/addr:interpolation", "r/type=associatedStreet", "-o", self.numbers, "--overwrite")
            osmium("tags-filter", box, "nwr/amenity=" + ",".join(AMENITIES), "-o", kept, "--overwrite")
            for feature in features(kept):
                tags, geometry = feature["properties"], feature["geometry"]
                if tags.get("amenity") not in AMENITIES: continue
                if geometry["type"] == "Point": lon, lat = geometry["coordinates"][:2]
                else:  # an area at its mean vertex, as the terrace permits
                    ring = next(outer_rings(geometry), None)
                    if not ring: continue
                    lat, lon = (sum(v[k] for v in ring) / len(ring) for k in ("latitude", "longitude"))
                venue = {"name": str(tags.get("name", "")).strip(), "coordinate": {"latitude": round(lat, 6), "longitude": round(lon, 6)},
                         "amenity": tags["amenity"], "outdoor_seating": seating(tags.get("outdoor_seating"), rooftop(tags)), "source": "osm"}
                if drinks_late(tags): venue["bar"] = True
                self.venues.setdefault(buckets(lat, lon, lat, lon)[0], []).append(venue)
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

    def boundaries(self):
        """The administrative boundary relations of the area: [{"id": "r…", "name", "admin_level" (int), "place",
        "labels", "centres" (the ids, "n…", of its label and admin_centre member nodes), "geometry" (None when its
        ring is not whole in the extract)}]. One without a name or a numeric admin_level is left out."""
        shapes = {"r" + str(f["properties"]["@id"]): f["geometry"] for f in features(self.admin, "polygon", ids=True)
                  if f["properties"].get("@type") == "relation"}
        out = []
        for r in ET.fromstring(osmium("cat", self.admin, "-t", "relation", "-f", "osm")).iter("relation"):
            tags = {t.get("k"): t.get("v") for t in r.iter("tag")}
            name, level = (tags.get("name") or "").strip(), tags.get("admin_level", "")
            if tags.get("boundary") != "administrative" or not name or not level.isdigit(): continue
            role = lambda kind: {"n" + m.get("ref") for m in r.iter("member") if m.get("type") == "node" and m.get("role") == kind}
            out.append({"id": "r" + r.get("id"), "name": name, "name_en": tags.get("name:en"), "admin_level": int(level), "place": tags.get("place"),
                        "labels": role("label"), "centres": role("admin_centre"), "geometry": shapes.get("r" + r.get("id"))})
        return out

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

def english(place, name_en):
    """The place with "name_en", OSM's name:en, when it has one that is not its "name"."""
    name_en = str(name_en or "").strip()
    if name_en and name_en != place["name"]: place["name_en"] = name_en
    return place

PLACE_TWIN = 1000  # metres: the same name this close is one place mapped twice (a node and its area)

def distinct_places(places):
    """Each place once: a name met again within PLACE_TWIN of a kept one is left out, so the first
    wins (osmium exports nodes before areas: the mapped point over a polygon's mean vertex)."""
    kept = []
    for p in places:
        at = (p["coordinate"]["latitude"], p["coordinate"]["longitude"])
        if not any(k["name"] == p["name"] and metres(*at, k["coordinate"]["latitude"], k["coordinate"]["longitude"]) <= PLACE_TWIN
                   for k in kept):
            kept.append(p)
    return kept

def mean_point(geometry):
    """(lat, lon): a point's own, an area's outer ring's mean vertex; None for an area with no ring."""
    if geometry["type"] == "Point": return geometry["coordinates"][1], geometry["coordinates"][0]
    ring = next(outer_rings(geometry), None)
    if not ring: return None
    return tuple(sum(v[k] for v in ring) / len(ring) for k in ("latitude", "longitude"))

# Boundary relations under a municipality that no place stands for are written as places of their own,
# the kind their admin_level stands for when they carry no place tag: Paris's 20 arrondissements (9)
# and 80 quartiers administratifs (10), mapped as boundaries far more often than as places.
PLACE_ADMIN_KINDS = {9: "suburb", 10: "quarter"}
PLACE_NAME_LEADS = {"QUARTIER", "DE", "DU", "DES", "D", "LA", "LE", "LES", "L"}

def place_key(name):
    """A name as places compare it: normalised_name() without the leading "Quartier" and articles, so
    "Quartier de la Goutte-d'Or" and "La Goutte-d'Or" meet. A name of nothing but those words stays whole."""
    words = normalised_name(name).split(" ")
    i = 0
    while i < len(words) - 1 and words[i] in PLACE_NAME_LEADS: i += 1
    return " ".join(words[i:])

def place_levels(places, boundaries):
    """The places (each with "osm": its "n…", "w…" or "r…" id) with "admin_level" when the name comes from or
    matches an administrative boundary (OSM.boundaries()): the place is the relation itself, or is the
    relation's label node, or its admin_centre node under a name one holds the other ("17e Arrondissement",
    "Paris 17e Arrondissement") at a level of PLACE_ADMIN_KINDS (a municipality's admin_centre is its seat:
    London's suburb Hackney is not the borough), or lies inside it under the same place_key(). The deepest level wins. A
    relation that is a place itself gives way to its label or admin_centre node when that is a place too
    (the node is the name as mapped). Then each boundary of PLACE_ADMIN_KINDS no place matched, whole in the
    extract, is added at its mean vertex. "osm" is not written."""
    by_id, by_node, by_key = {b["id"]: b for b in boundaries}, {}, {}
    for b in boundaries:
        for n in b["labels"] | b["centres"]: by_node.setdefault(n, []).append(b)
        by_key.setdefault(place_key(b["name"]), []).append(b)
    def stands_for(p, b):
        if p["osm"] in b["labels"]: return True
        if p["osm"] not in b["centres"] or b["admin_level"] not in PLACE_ADMIN_KINDS: return False
        a, z = normalised_name(p["name"]), normalised_name(b["name"])
        return a in z or z in a
    places_by_id = {p["osm"]: p for p in places}
    out, used = [], set()
    for p in places:
        own = by_id.get(p["osm"])
        if own and any(stands_for(places_by_id[n], own) for n in own["labels"] | own["centres"] if n in places_by_id):
            used.add(own["id"]); continue
        lat, lon = p["coordinate"]["latitude"], p["coordinate"]["longitude"]
        found = {own["id"]: own} if own else {}
        found.update((b["id"], b) for b in by_node.get(p["osm"], ()) if stands_for(p, b))
        found.update((b["id"], b) for b in by_key.get(place_key(p["name"]), ()) if b["geometry"] and contains(b["geometry"], lat, lon))
        entry = {k: v for k, v in p.items() if k != "osm"}
        if found:
            entry["admin_level"] = max(b["admin_level"] for b in found.values())
            used.update(found)
        out.append(entry)
    for b in boundaries:
        if b["id"] in used or b["admin_level"] not in PLACE_ADMIN_KINDS or not b["geometry"]: continue
        at = mean_point(b["geometry"])
        if not at: continue
        kind = b["place"] if b["place"] in PLACE_KINDS else PLACE_ADMIN_KINDS[b["admin_level"]]
        out.append(english({"name": b["name"], "kind": kind, "coordinate": vertex(*at), "admin_level": b["admin_level"]}, b.get("name_en")))
    return out

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

def listed(value, *words):
    """Whether an OSM value, or one entry of a ;-list, is one of the words (so "roof" but not "roofed")."""
    return any(v.strip() in words for v in str(value or "").split(";"))

def rooftop(tags):
    """Tagged as on a roof by location=roof, roof_terrace=yes or terrace=roof; rooftop=yes tags helipads."""
    return listed(tags.get("location"), "roof") or tags.get("roof_terrace") == "yes" or listed(tags.get("terrace"), "roof")

def seating(tag, on_roof=False):
    """OpenStreetMap's outdoor_seating as the tile carries it: true/false for yes/no, and the value
    itself when it names where the seats are (roof, terrace, patio…), so a reader can tell a rooftop;
    "roof" when another tag puts the venue on a roof and the value does not already say so."""
    if on_roof: return tag if listed(tag, "roof", "rooftop") else "roof"
    if tag in (None, "", "no"): return False
    if tag == "yes": return True
    return str(tag)

def drinks_late(tags):
    """A café or restaurant that is a bar in all but name, counted under Bar by the app: tagged
    bar=yes, or open past midnight on some day (owner, 2026-10-10: La Perle and Le Progrès in the
    Marais are a restaurant and a café closing at 02:00)."""
    return tags.get("amenity") in ("cafe", "restaurant") and (tags.get("bar") == "yes" or open_past_midnight(tags.get("opening_hours")))

_DAY = r"(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:\[[-0-9,]+\])?"
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_SELECTOR = re.compile(rf"(?:{_DAY}(?:-{_DAY})?|PH|SH|{_MONTH}(?:\s?\d{{1,2}})?(?:-{_MONTH}?(?:\s?\d{{1,2}})?)?|\d{{4}}(?:-\d{{4}})?|week\s?\d{{1,2}}(?:-\d{{1,2}})?)")
_SPAN = r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})"
_TIMES = re.compile(rf"{_SPAN}(?:\s*,\s*{_SPAN})*")

def open_past_midnight(value):
    """Whether an opening_hours value keeps the place open after midnight on some day: "24/7", a
    range that wraps (18:00-02:00) or runs past 24:00 (18:00-26:00), or a whole day (00:00-24:00).
    A rule ending "off" or "closed" opens nothing, one for public or school holidays alone (PH,
    SH) is ignored, and anything this strict reading cannot parse — sunset, open ends, typos — is
    False: a guess would put a restaurant under Bar."""
    if not value: return False
    late = False
    # Rules part at ";", "||", and at a comma after a time ("Mo-Fr 06:00-02:00, Sa-Su 08:00-02:00").
    for rule in re.split(r";|\|\||(?<=\d),\s*(?=[A-Za-z])", value):
        rule = re.sub(r'\s*"[^"]*"\s*$', "", rule).strip()  # a trailing comment
        if rule == "24/7": late = True
        if not rule or rule == "24/7": continue
        words = rule.split()
        if words[-1] in ("off", "closed"): continue
        if words[-1] == "open": words = words[:-1]
        cut = next((i for i in range(len(words)) if _TIMES.fullmatch(" ".join(words[i:]))), len(words))
        selectors = [t for t in re.split(r"[\s,]+", " ".join(words[:cut])) if t]
        if not all(_SELECTOR.fullmatch(t) for t in selectors): return False
        if cut == len(words) or (selectors and all(t in ("PH", "SH") for t in selectors)): continue
        for h1, m1, h2, m2 in (map(int, span) for span in re.findall(_SPAN, " ".join(words[cut:]))):
            if h1 > 24 or h2 > 48 or m1 > 59 or m2 > 59: return False
            start, end = h1 * 60 + m1, h2 * 60 + m2
            if end > 24 * 60 or 0 < end < start or (start == 0 and end == 24 * 60): late = True
    return late

def osm_venues(cell):
    """The named venues of one ~2 km cell, the key's own truncation deciding the edge."""
    return [v for v in _osm.near(_osm.venues, cell.rect) if v["name"]
            and f"{index(v['coordinate']['latitude'], COARSE)},{index(v['coordinate']['longitude'], COARSE)}" == cell.key]


# ---------------------------------------------------------------- streets

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
# places/: the names a neighbourhood goes by, from the widest to the narrowest.
PLACE_KINDS = ("suburb", "quarter", "neighbourhood")

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

def number_rows(tiles, points, interpolations):
    """{key: [[name, lowest, highest], …]} (numbers/) for the streets/ tiles: each named way of a tile whose
    street_key() matches the addr:street of at least two distinct numbers inside that cell (the key's
    truncation deciding), once per name as the tile writes it, sorted by name."""
    seen = {key: {} for key in tiles}
    points = list(points) + [(lat, lon, street, x) for street, line, first, last, step in interpolations
                             for lat, lon, x in interpolated(line, first, last, step)]
    for lat, lon, street, number in points:
        numbers = seen.get(f"{index(lat)},{index(lon)}")
        if numbers is not None: numbers.setdefault(street_key(street), set()).add(number)
    out = {}
    for key, lines in tiles.items():
        names = dict.fromkeys(line[1] for line in lines if len(line) > 1 and isinstance(line[1], str))
        found = [(name, seen[key].get(street_key(name), ())) for name in names]
        out[key] = sorted([name, min(n), max(n)] for name, n in found if len(n) >= 2)
    return out


# ---------------------------------------------------------------- shapes

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

# buildings-v2/ (2026-10-06; the store's only buildings layer since 2026-10-08): the same footprints as buildings/, a third of the bytes
# gzipped (BUILDINGS.md). Each building is a flat array [height, y0, x0, dy1, dx1, …]: the height in
# HEIGHT_STEP metres (an integer when whole), null for the 15 m guess ("guessed": true in buildings/);
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
    height = None if b.get("guessed") else round(b["height"] / HEIGHT_STEP) * HEIGHT_STEP
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
        height = same.get(key(b), next((h for theirs, c, h in near if contains(theirs, lat, lon) and contains(mine, *c)), None))
        kept = b.get("tagged") == "height" or (b.get("tagged") == "levels" and not metres)
        out.append(b if height is None or kept else dict({k: v for k, v in b.items() if k != "guessed"}, height=height, city=True))
    return out

def unique(items):
    seen, out = set(), []
    for item in items:
        k = json.dumps(item, sort_keys=True)
        if k not in seen: seen.add(k); out.append(item)
    return out

def wfs(url, params, rect, cap=5000, depth=0):
    """A WFS 2.0 GetFeature over a box (latitude first, EPSG:4326 URN); a box that hits the cap is split in four."""
    s, w, n, e = rect
    # params may override VERSION (Zurich's QGIS server answers only 1.1.0).
    q = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "COUNT": str(cap), **params,
         "BBOX": f"{s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326"}
    features = get_json(url + "?" + urllib.parse.urlencode(q)).get("features", [])
    if len(features) < cap or depth >= 3: return features
    ms, me = (s + n) / 2, (w + e) / 2
    return [f for part in ((s, w, ms, me), (s, me, ms, e), (ms, w, n, me), (ms, me, n, e))
            for f in wfs(url, params, part, cap, depth + 1)]

def arcgis(url, rect, fields, where="1=1", oid="OBJECTID"):
    """An ArcGIS REST envelope query in WGS84, paged on the layer's OID field (resultOffset repeats and skips rows on SITG)."""
    s, w, n, e = rect
    def parse(raw):
        root = json.loads(raw)
        if "error" in root: raise ValueError(root["error"])
        return root
    out, last = [], -1   # a shapefile's FID starts at 0 (San Sebastián's first terrace)
    while True:
        q = urllib.parse.urlencode({"where": f"({where}) AND {oid}>{last}", "geometry": f"{w},{s},{e},{n}",
                                    "geometryType": "esriGeometryEnvelope", "inSR": "4326", "outSR": "4326",
                                    "outFields": ",".join([oid] + fields), "f": "geojson", "orderByFields": oid})
        page = get(url + "/query?" + q, parse=parse)
        features = page.get("features") or []
        out += features
        # A FeatureServer flags truncation under "properties", a MapServer (Wrocław) at the top level.
        more = page.get("exceededTransferLimit") or (page.get("properties") or {}).get("exceededTransferLimit")
        if not features or not more: return out
        last = max(f["properties"][oid] for f in features)

def opendatasoft(host, dataset, where, select):
    q = urllib.parse.urlencode({"where": where, "select": select, "limit": "-1"})
    return get_json(f"https://{host}/api/explore/v2.1/catalog/datasets/{dataset}/exports/geojson?" + q).get("features") or []


# ---------------------------------------------------------------- buildings

def ign_buildings(rect):
    """IGN BD TOPO: hauteur, else floors × 3 m, else the flagged 15 m guess."""
    # Only the three fields read: half the bytes of the full record, and far quicker to answer (measured 2026-09-24).
    features = wfs("https://data.geopf.fr/wfs/ows", {"TYPENAMES": "BDTOPO_V3:batiment", "OUTPUTFORMAT": "application/json",
                                                    "PROPERTYNAME": "geometrie,hauteur,nombre_d_etages"}, rect)
    out = []
    for f in features:
        p = f.get("properties") or {}
        h = p.get("hauteur")
        if not isinstance(h, (int, float)):
            floors = p.get("nombre_d_etages")
            h = floors * 3 if isinstance(floors, (int, float)) else None
        for ring in outer_rings(f.get("geometry")):
            out.append(building(ring, h))
    return out

def turin(rect):
    """BDTRE refuses GeoJSON: MapServer CSV, WKT in lon lat then the height, 1,000 per request."""
    s, w, n, e = rect
    q = urllib.parse.urlencode({"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "ms:un_vol",
                                "OUTPUTFORMAT": "text/csv", "SRSNAME": "urn:ogc:def:crs:EPSG::4326", "PROPERTYNAME": "un_vol_av",
                                "COUNT": "1000", "BBOX": f"{s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326"})
    rows = get("https://geoservices.csi.it/ms/wfs/taims/rp-01/taimswfs/bdtre_imm?" + q,
               parse=lambda raw: list(csv.reader(raw.decode("latin-1").splitlines()))[1:])
    if len(rows) >= 1000:
        ms, me = (s + n) / 2, (w + e) / 2
        return [b for part in ((s, w, ms, me), (s, me, ms, e), (ms, w, n, me), (ms, me, n, e)) for b in turin(part)]
    features = []
    for row in rows:
        # POLYGON ((x y,…), (hole)) or MULTIPOLYGON (((…)), ((…))): "((" opens each outer ring.
        rings = [[[float(c) for c in v.split()] for v in p.lstrip("(").split(")")[0].split(",")] for p in row[0].split("((")[1:]]
        try: h = float(row[1]) if len(row) > 1 and row[1] else None
        except ValueError: h = None
        features.append({"geometry": {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}, "properties": {"h": h}})
    return footprints(features, lambda p: p.get("h"))

def bag3d(rect):
    """3DBAG LoD1.2: NAP roof (70th percentile) minus NAP ground."""
    features = wfs("https://data.3dbag.nl/api/BAG3D/wfs", {"TYPENAMES": "BAG3D:lod12", "OUTPUTFORMAT": "application/json",
                   "SRSNAME": "EPSG:4326", "PROPERTYNAME": "geom,b3_h_70p,b3_h_maaiveld"}, rect)
    return footprints(features, lambda p: None if p.get("b3_h_70p") is None or p.get("b3_h_maaiveld") is None
                      else p["b3_h_70p"] - p["b3_h_maaiveld"])

def in_bbox(point, rect):
    s, w, n, e = rect
    return f"in_bbox({point}, {s}, {w}, {n}, {e})"

def intersects(geometry, rect):
    s, w, n, e = rect
    return f"intersects({geometry}, 'POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))')"

def socrata(host, dataset, where, select):
    """A Socrata dataset as GeoJSON, paged: SoQL numbers come back as strings."""
    out, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"$where": where, "$select": select, "$limit": "2000", "$offset": str(offset), "$order": ":id"})
        page = get_json(f"https://{host}/resource/{dataset}.geojson?" + q).get("features") or []
        out += page
        if len(page) < 2000: return out
        offset += 2000

def number(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def socrata_buildings(host, dataset, geometry, field, height):
    def fetch(rect):
        return footprints(socrata(host, dataset, intersects(geometry, rect), f"{field},{geometry}"),
                          lambda p: height(number(p.get(field))))
    return fetch

CATASTRO_FEED = "https://www.catastro.hacienda.gob.es/INSPIRE/buildings/ES.SDGC.bu.atom.xml"
_catastro = {"provinces": None, "feeds": {}, "loaded": set(), "grid": {}}

def _atom_entries(url):
    """(title, href, [s, w, n, e], crs) per entry of a cadastre ATOM feed (regex: the feeds are not always well-formed XML)."""
    raw = get(url)
    declared = re.match(rb'<\?xml[^>]*encoding="([^"]+)"', raw)   # the province feeds are ISO-8859-1 (Carreño, Logroño)
    text = raw.decode(declared.group(1).decode() if declared else "utf-8", "replace")
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", text, re.S):
        title = re.search(r"<title>([^<]*)", entry); href = re.search(r'href="([^"]+)"', entry)
        poly = re.search(r"<georss:polygon>([^<]*)", entry); crs = re.search(r'term="[^"]*EPSG/0/(\d+)"', entry)
        if not (title and href and poly): continue
        v = [float(x) for x in poly.group(1).split()]
        lats, lons = v[0::2], v[1::2]
        out.append((title.group(1).strip(), href.group(1).strip(), (min(lats), min(lons), max(lats), max(lons)), crs.group(1) if crs else "25830"))
    return out

def _overlaps(a, b): return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]

def catastro(rect):
    """Spain's cadastre, INSPIRE Buildings as one download per municipality (the ATOM feeds, since
    2026-09-27; the block-by-block WFS took 4 s a block and ran whole cities into the 5.5 h cap):
    floors above ground × 3 m, a 0-floor part (a basement) skipped, the flagged 15 m guess when the count is missing.
    A municipality is downloaded once (kept a year), its parts inside the city's box indexed by
    200 m cell; a rect is then served from memory. Coordinates come in the province's UTM zone."""
    focus = next((padded((b["lat"][0], b["lon"][0], b["lat"][1], b["lon"][1]), 300) for b in CITY_BUILDINGS
                  if b["fetch"] is catastro and in_box(b, (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)), rect)
    if _catastro["provinces"] is None: _catastro["provinces"] = _atom_entries(CATASTRO_FEED)
    for _, feed, box, _ in _catastro["provinces"]:
        if not _overlaps(box, focus): continue
        if feed not in _catastro["feeds"]: _catastro["feeds"][feed] = _atom_entries(feed)
        for title, href, mbox, crs in _catastro["feeds"][feed]:
            if not _overlaps(mbox, focus) or href in _catastro["loaded"]: continue
            _catastro["loaded"].add(href)
            _load_catastro(href, crs, focus, title)
    s, w, n, e = rect
    out, seen = [], set()
    for ky in range(index(s) - 1, index(n) + 2):
        for kx in range(index(w) - 1, index(e) + 2):
            for b in _catastro["grid"].get((ky, kx), ()):
                if id(b) in seen: continue
                seen.add(id(b))
                if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"]): out.append(b)
    return out

def _load_catastro(href, crs, focus, title):
    """One municipality's building parts into the grid: streamed from its zip, UTM → WGS84, focus only."""
    import zipfile
    zone = int(crs[-2:])
    folder = os.path.join(_catastro.get("extracts", "extracts"), "catastro")
    file = None
    for encoding in ("utf-8", "latin-1"):  # the server takes the name percent-encoded either way, one of the two
        candidate = download(urllib.parse.quote(href, safe=":/", encoding=encoding), folder, max_days=365)
        if zipfile.is_zipfile(candidate): file = candidate; break
        os.remove(candidate)   # an HTML error page, not the package
    if file is None: raise SourceError(f"{title}: no package at {href}")
    ns = {"gml": "http://www.opengis.net/gml/3.2", "bu": "http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0"}
    started, kept = time.monotonic(), 0
    with zipfile.ZipFile(file) as z:
        member = next(m for m in z.namelist() if m.lower().endswith("buildingpart.gml"))
        with z.open(member) as f:
            for _, elem in ET.iterparse(f, events=("end",)):
                if elem.tag != "{%s}BuildingPart" % ns["bu"]: continue
                floors = number((elem.findtext("bu:numberOfFloorsAboveGround", namespaces=ns) or "").strip())
                if floors != 0:
                    for ring in elem.iterfind(".//gml:exterior//gml:posList", ns):
                        v = [float(x) for x in ring.text.split()]
                        pts = [utm_to_wgs84(v[i], v[i + 1], zone) for i in range(0, len(v) - 1, 2)]
                        if len(pts) < 3 or not any(focus[0] <= la <= focus[2] and focus[1] <= lo <= focus[3] for la, lo in pts): continue
                        b = building([vertex(la, lo) for la, lo in pts], floors * 3 if floors and floors > 0 else None)
                        for k in {(index(la), index(lo)) for la, lo in pts}:  # every cell a part touches
                            _catastro["grid"].setdefault(k, []).append(b)
                        kept += 1
                elem.clear()
    log(f"    catastro: {title}: {kept} parts inside the box in {time.monotonic() - started:.0f} s")

def hamburg(rect):
    """LGV Hamburg's LoD2 model (OGC API, CityJSON only, and only in a 3D CRS: ETRS89 / UTM 32N + height,
    whole metres): each building's ground surface as its outline, measuredHeight as its height."""
    s, w, n, e = rect
    q = urllib.parse.urlencode({"f": "cityjson", "bbox": f"{w},{s},{e},{n}", "limit": "10000",
                                "crs": "http://www.opengis.net/def/crs/EPSG/0/5555"})
    root = get_json("https://api.hamburg.de/datasets/v1/lod2_hamburg/collections/building/items?" + q)
    objects = root.get("CityObjects") or {}
    if len(objects) >= 10000:
        ms, me = (s + n) / 2, (w + e) / 2
        return [b for part in ((s, w, ms, me), (s, me, ms, e), (ms, w, n, me), (ms, me, n, e)) for b in hamburg(part)]
    (sx, sy, _), (tx, ty, _) = root["transform"]["scale"], root["transform"]["translate"]
    vertices = root.get("vertices") or []
    out = []
    for o in objects.values():
        for g in o.get("geometry") or []:
            surfaces = (g.get("semantics") or {}).get("surfaces") or []
            for shell, values in zip(g.get("boundaries") or [], (g.get("semantics") or {}).get("values") or []):
                for face, v in zip(shell, values):
                    if v is None or surfaces[v].get("type") != "GroundSurface": continue
                    ring = [vertex(*utm_to_wgs84(vertices[i][0] * sx + tx, vertices[i][1] * sy + ty, 32)) for i in face[0]]
                    if len(ring) >= 3: out.append(building(ring, (o.get("attributes") or {}).get("measuredHeight") or None))
    return out

GIPUZKOA_NS = {"gml": "http://www.opengis.net/gml/3.2", "wfs": "http://www.opengis.net/wfs/2.0",
               "bu-base": "http://inspire.ec.europa.eu/schemas/bu-base/4.0",
               "bu-core2d": "http://inspire.ec.europa.eu/schemas/bu-core2d/4.0"}

def gipuzkoa_buildings(raw):
    """INSPIRE BU GML 3.2 (the only format served), lat lon: heightAboveGround (estimated), else floors × 3 m, else the guess."""
    out = []
    for b in ET.fromstring(raw).iterfind("wfs:member/*", GIPUZKOA_NS):
        h = number(b.findtext("bu-base:heightAboveGround/bu-base:HeightAboveGround/bu-base:value", namespaces=GIPUZKOA_NS))
        if not h or h <= 0:
            floors = number(b.findtext("bu-base:numberOfFloorsAboveGround", namespaces=GIPUZKOA_NS))
            h = floors * 3 if floors and floors > 0 else None
        for ring in b.iterfind("bu-core2d:geometry2D//gml:exterior//gml:posList", GIPUZKOA_NS):
            v = [float(x) for x in ring.text.split()]
            if len(v) >= 6: out.append(building([vertex(v[i], v[i + 1]) for i in range(0, len(v) - 1, 2)], h))
    return out

def gipuzkoa(rect, cap=2000):
    """Gipuzkoa Provincial Council's INSPIRE buildings WFS (~13 KB a building: addresses ride along); a full page is split in four."""
    s, w, n, e = rect
    q = urllib.parse.urlencode({"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "bu-ext2d:Building",
                                "COUNT": str(cap), "SRSNAME": "urn:ogc:def:crs:EPSG::4326",
                                "BBOX": f"{s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326"})
    raw = get("https://b5m.gipuzkoa.eus/inspire/wfs/gipuzkoa_wfs_bu?" + q)
    if raw.count(b"<wfs:member") >= cap:
        ms, me = (s + n) / 2, (w + e) / 2
        return [b for part in ((s, w, ms, me), (s, me, ms, e), (ms, w, n, me), (ms, me, n, e)) for b in gipuzkoa(part, cap)]
    return gipuzkoa_buildings(raw)

def citygml_buildings(raw, zone=32):
    """LoD2 CityGML in UTM (NRW's, Bavaria's): each Building's or BuildingPart's ground surface as an outline,
    its measuredHeight (metres, ground to roof) as the height; a parent made only of parts gives none itself."""
    out = []
    for _, el in ET.iterparse(io.BytesIO(raw)):
        if el.tag.rsplit("}", 1)[-1] != "Building": continue
        for obj in [el] + el.findall(".//{*}BuildingPart"):
            h = number(obj.findtext("{*}measuredHeight"))
            for pos in obj.findall("{*}boundedBy/{*}GroundSurface//{*}exterior//{*}posList"):
                v, d = [float(x) for x in pos.text.split()], int(pos.get("srsDimension") or 3)
                ring = [vertex(*utm_to_wgs84(v[i], v[i + 1], zone)) for i in range(0, len(v) - d + 1, d)]
                if len(ring) >= 3: out.append(building(ring, h if h and h > 0 else None))
        el.clear()
    return out

_citygml = {}  # tile URL → its buildings, each tile parsed once a run

def citygml_tiles(url, km):
    """A fetch over LoD2 CityGML tiles of `km` km in UTM 32N, named by their south-west corner in km: each
    tile a box touches is downloaded and parsed once a run, then served from memory. A tile the publisher
    does not have (404: no building there) is empty."""
    def fetch(rect):
        s, w, n, e = rect
        corners = [wgs84_to_utm(la, lo, 32) for la in (s, n) for lo in (w, e)]
        span = lambda i: range(int(min(c[i] for c in corners) // 1000 // km * km), int(max(c[i] for c in corners) // 1000) + 1, km)
        out = []
        for x in span(0):
            for y in span(1):
                u = url.format(e=x, n=y)
                if u not in _citygml:
                    try: _citygml[u] = citygml_buildings(get(u))
                    except SourceError as err:
                        if "HTTP 404" not in str(err): raise
                        _citygml[u] = []
                out += [b for b in _citygml[u] if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"])]
        return out
    return fetch

# MLIT's Project PLATEAU (3D都市モデル): CityGML 2.0 per city, one building file per 1 km mesh, listed by the
# PLATEAU data catalogue's keyless API per city code (Osaka: 27100; Tokyo: each of the 23 wards, 13101–13123,
# a mesh on a ward border having a file per ward). The store holds the files gzipped and serves them so when
# asked (a 124 MB mesh is 16 MB on the wire).
PLATEAU_INDEX = "https://api.plateau.reearth.io/datacatalog/citygml/{}"
PLATEAU_KEPT = 40    # mesh files held parsed (a dense one ~3,000 buildings, ~10 MB): a row of 600 m blocks across
                     # a 13 km box touches ~30, so a sweep parses each once
_plateau_index = {}  # city code → {mesh code: [file URLs]}, read once a run
_plateau = {}        # file URL → its buildings, the PLATEAU_KEPT most recently used

def mesh_code(lat, lon):
    """The Japanese standard 3rd-level mesh (JIS X 0410: 30″ × 45″, ~1 km) holding a point, as its 8-digit code:
    the 1st mesh (lat × 1.5, lon − 100), its 8 × 8 cell, then that cell's 10 × 10 cell."""
    p, u = lat * 1.5, lon - 100
    q, v = (p % 1) * 8, (u % 1) * 8
    return f"{int(p):02d}{int(u):02d}{int(q)}{int(v)}{int((q % 1) * 10)}{int((v % 1) * 10)}"

def mesh_codes(rect):
    """The 3rd-level meshes a box touches, west to east then south to north."""
    s, w, n, e = rect
    return [mesh_code((i + 0.5) / 120, (j + 0.5) / 80)
            for i in range(math.floor(s * 120), math.floor(n * 120) + 1) for j in range(math.floor(w * 80), math.floor(e * 80) + 1)]

def plateau_buildings(raw):
    """A PLATEAU building file (gzipped or not): CityGML 2.0 in EPSG:6697, posLists "lat lon height" (three values
    a point, no srsDimension on them). Each Building's or BuildingPart's outline is its LoD2 ground surface where it
    has one (LoD2 covers 20 % of Tokyo's buildings, 2.6 % of Osaka's), else its LoD0 footprint (Osaka), else its
    LoD0 roof edge (Tokyo); its height is measuredHeight, metres (-9999 where unmeasured: the flagged guess)."""
    if raw[:2] == b"\x1f\x8b": raw = gzip.decompress(raw)
    out = []
    for _, el in ET.iterparse(io.BytesIO(raw)):
        if el.tag.rsplit("}", 1)[-1] != "Building": continue
        for obj in [el] + el.findall(".//{*}BuildingPart"):
            h = number(obj.findtext("{*}measuredHeight"))
            lists = next((found for where in ("{*}boundedBy/{*}GroundSurface", "{*}lod0FootPrint", "{*}lod0RoofEdge")
                          if (found := obj.findall(where + "//{*}exterior//{*}posList"))), [])
            for pos in lists:
                v, d = [float(x) for x in pos.text.split()], int(pos.get("srsDimension") or 3)
                ring = [vertex(v[i], v[i + 1]) for i in range(0, len(v) - d + 1, d)]
                if len(ring) >= 3: out.append(building(ring, h if h and h > 0 else None))
        el.clear()
    return out

def plateau(codes):
    """A fetch over PLATEAU's building files for the cities (or wards) of `codes`: their catalogues are read once a
    run, each mesh file a box touches is downloaded once (kept a year under extracts/plateau/<code>/, gzipped as
    served) and parsed once a run (the PLATEAU_KEPT most recent held). A mesh no city lists has no building."""
    def fetch(rect):
        files = {}
        for code in codes:
            if code not in _plateau_index:
                cities = sorted(get_json(PLATEAU_INDEX.format(code)).get("cities") or [], key=lambda c: c.get("year") or 0)
                index = {}
                for f in ((cities[-1].get("files") or {}).get("bldg") or []) if cities else []:
                    index.setdefault(f["code"], []).append(f["url"])
                _plateau_index[code] = index
            for mesh in mesh_codes(rect):
                for url in _plateau_index[code].get(mesh, ()): files[url] = code
        s, w, n, e = rect
        out = []
        for url, code in files.items():
            if url in _plateau:
                _plateau[url] = _plateau.pop(url)  # the most recent last
            else:
                file = download(url, os.path.join("extracts", "plateau", code), max_days=365, headers={"Accept-Encoding": "gzip"})
                with open(file, "rb") as f: _plateau[url] = plateau_buildings(f.read())
                while len(_plateau) > PLATEAU_KEPT: del _plateau[next(iter(_plateau))]
            out += [b for b in _plateau[url] if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"])]
        return out
    return fetch

def beoland(rect):
    """Beoland's Belgrade LoD2 multipatch, which answers no GeoJSON and leaves measuredheight empty: the
    footprints' outer (clockwise) rings from one query, each height (the extent's top minus its bottom,
    metres) from a second, matched by objectid."""
    s, w, n, e = rect
    def features(option):
        def parse(raw):
            root = json.loads(raw)
            if "error" in root: raise ValueError(root["error"])
            return root
        out, last = [], -1
        while True:
            q = urllib.parse.urlencode({"where": f"objectid>{last}", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
                                        "inSR": "4326", "outSR": "4326", "outFields": "objectid", "returnZ": "true",
                                        "multipatchOption": option, "orderByFields": "objectid", "f": "json"})
            page = get("https://gis.beoland.com/server/rest/services/Hosted/Beograd_3D_WSL1/FeatureServer/2/query?" + q, parse=parse)
            out += page.get("features") or []
            if not page.get("features") or not page.get("exceededTransferLimit"): return out
            last = max(f["attributes"]["objectid"] for f in page["features"])
    heights = {}
    for f in features("extent"):
        z = [p[2] for r in (f.get("geometry") or {}).get("rings") or [] for p in r if len(p) > 2 and p[2] is not None]
        if z: heights[f["attributes"]["objectid"]] = max(z) - min(z)
    out = []
    for f in features("xyFootprint"):
        for r in (f.get("geometry") or {}).get("rings") or []:
            if len(r) >= 4 and sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(r, r[1:])) < 0:  # clockwise: outer
                h = heights.get(f["attributes"]["objectid"])
                out.append(building([vertex(p[1], p[0]) for p in r], h if h and h > 0 else None))
    return out

LIGURIA = "https://geoservizi.regione.liguria.it/geoserver/ows"

def liguria(rect):
    """Regione Liguria's NC5 3D footprints, every vertex at the eave's elevation: the height is that minus the
    nearest spot height measured at a building's foot ("al piede", 0301) within 40 m, else the flagged 15 m guess."""
    params = {"OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326"}
    feet = {}  # 0.001° buckets
    for f in wfs(LIGURIA, {**params, "TYPENAMES": "M2052:L6911", "PROPERTYNAME": "wkb_geometry,pt_quo_q,pt_quo_sed"}, padded(rect, 50)):
        p = f.get("properties") or {}
        if p.get("pt_quo_sed") == "0301" and f.get("geometry") and p.get("pt_quo_q") is not None:
            lon, lat = f["geometry"]["coordinates"][:2]
            feet.setdefault((round(lat, 3), round(lon, 3)), []).append((lat, lon, p["pt_quo_q"]))
    out = []
    for f in wfs(LIGURIA, {**params, "TYPENAMES": "M2052:L6871", "PROPERTYNAME": "wkb_geometry"}, rect):
        g = f.get("geometry") or {}
        for polygon in [g.get("coordinates")] if g.get("type") == "Polygon" else g.get("coordinates") or []:
            ring = [v for v in (polygon or [[]])[0] if len(v) >= 3]
            if len(ring) < 3: continue
            lat, lon = sum(v[1] for v in ring) / len(ring), sum(v[0] for v in ring) / len(ring)
            near = [q for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                    for q in feet.get((round(round(lat, 3) + dy / 1000, 3), round(round(lon, 3) + dx / 1000, 3)), ())]
            foot = min(near, key=lambda q: metres(lat, lon, q[0], q[1]), default=None)
            h = ring[0][2] - foot[2] if foot and metres(lat, lon, foot[0], foot[1]) <= 40 else None
            out.append(building([vertex(v[1], v[0]) for v in ring], h if h and h > 0 else None))
    return out

def oakland(rect):
    """Oakland's footprints: nostory × 3 m. A 20-floor footprint under 5,000 sq ft is left out: the six
    of them are houses, a placeholder, not a count."""
    return footprints([f for f in socrata("data.oaklandca.gov", "iqfp-6kz5", intersects("the_geom", rect), "nostory,shape_area,the_geom")
                       if not (number(f["properties"].get("nostory")) == 20 and (number(f["properties"].get("shape_area")) or 0) < 5000)],
                      lambda p: (number(p.get("nostory")) or 0) * 3.0, guess=False)

NRCAN_GTA = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_ON_GTA_2023_gpkg.zip"
NRCAN_MONTREAL = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_QC_VILLE_MONTREAL_gpkg.zip"
_geopackages = {}  # zip URL → (its GeoPackage opened read-only, table, geometry column), unpacked once a run

def wkb_polygons(raw, at=0):
    """The polygons of an ISO WKB Polygon or MultiPolygon as [[ring of (x, y)]] (a Z or M ignored), and where it ends."""
    order = "<" if raw[at] == 1 else ">"
    kind, = struct.unpack_from(order + "I", raw, at + 1)
    dims, kind, at = (2, 3, 3, 4)[kind // 1000], kind % 1000, at + 5
    count, = struct.unpack_from(order + "I", raw, at)
    at += 4
    if kind == 6:
        out = []
        for _ in range(count):
            polygons, at = wkb_polygons(raw, at)
            out += polygons
        return out, at
    if kind != 3: raise ValueError(f"WKB type {kind}: not a polygon")
    rings = []
    for _ in range(count):
        points, = struct.unpack_from(order + "I", raw, at)
        v = struct.unpack_from(f"{order}{points * dims}d", raw, at + 4)
        rings.append([(v[i], v[i + 1]) for i in range(0, len(v), dims)])
        at += 4 + 8 * points * dims
    return [rings], at

def gpkg_polygons(blob):
    """A GeoPackage geometry: the "GP" header (version, flags, SRS id, an envelope of 0, 4, 6 or 8 doubles), then WKB."""
    return wkb_polygons(blob, 8 + (0, 32, 48, 48, 64)[(blob[3] >> 1) & 7])[0]

def geopackage(url, height):
    """A fetch over one zipped GeoPackage of footprints in lon/lat (NRCan's are NAD83(CSRS), WGS84 to a metre),
    downloaded once (kept a year) and queried through its R-tree: each footprint whose box meets the rect,
    its `height` column (metres above ground) as the height, the flagged 15 m guess when it reads none."""
    def fetch(rect):
        if url not in _geopackages:
            import zipfile
            folder = os.path.join("extracts", "geopackage")
            archive = download(url, folder, max_days=365)
            if not zipfile.is_zipfile(archive):
                os.remove(archive)
                raise SourceError(f"{url}: not a zip")
            with zipfile.ZipFile(archive) as z:
                member = next(m for m in z.namelist() if m.endswith(".gpkg"))
                file = os.path.join(folder, os.path.basename(member))
                if not os.path.exists(file) or os.path.getmtime(file) < os.path.getmtime(archive):
                    with z.open(member) as src, open(file + ".tmp", "wb") as dst: shutil.copyfileobj(src, dst, 1 << 20)
                    os.replace(file + ".tmp", file)
            db = sqlite3.connect(f"file:{file}?mode=ro", uri=True)
            _geopackages[url] = (db, *db.execute("SELECT table_name, column_name FROM gpkg_geometry_columns").fetchone())
        db, table, column = _geopackages[url]
        s, w, n, e = rect
        rows = db.execute(f'SELECT t."{column}", t."{height}" FROM "{table}" t JOIN "rtree_{table}_{column}" r ON t.rowid = r.id'
                          " WHERE r.minx <= ? AND r.maxx >= ? AND r.miny <= ? AND r.maxy >= ?", (e, w, n, s))
        return [building([vertex(y, x) for x, y in polygon[0]], h if h and h > 0 else None)
                for blob, h in rows if blob for polygon in gpkg_polygons(blob) if polygon and len(polygon[0]) >= 3]
    return fetch

# Shapefiles inside a zip (Buenos Aires, Milan, Rome): one layer's .shp, .shx, .dbf and .prj are fetched by HTTP range,
# the zip's central directory first (Rome's volumes are 58 MB of a 1.2 GB zip, Milan's 28 MB of 597 MB), kept a year
# under extracts/shapefile/<folder>/, and read in place: the .shx gives each record's offset, the .shp its box, so a
# block reads only the records whose box meets it.
RANGE_CHUNK = 8 << 20   # bytes a ranged request asks for
SHAPE_BUCKET = 250      # metres (or 1/400°) a side of a Shapefile's in-memory index of record boxes
MIN_RING = 1            # m²: a Shapefile ring under this is a sliver, left out
_shapefiles = {}        # (url, layer) → its Shapefile, opened once a run

class Ranged(io.RawIOBase):
    """A remote file read by HTTP range, for zipfile: a seek is free, each read one request (retried like download())."""
    def __init__(self, url, headers):
        self.url, self.headers, self.at = url, headers, 0
        self.size = int(self.request(0, 0)[1].split("/")[1])
    def request(self, start, end):
        for wait in (*WAITS, None):
            try:
                request = urllib.request.Request(self.url, headers={"User-Agent": UA, **self.headers, "Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    if response.status != 206: raise SourceError(f"{self.url}: no range support (HTTP {response.status})")
                    return response.read(), response.headers.get("Content-Range", "")
            except SourceError: raise
            except Exception as e:  # noqa: BLE001 — resets, timeouts: worth another try
                if wait is None: raise SourceError(f"{self.url}: {type(e).__name__}: {e}") from e
                log(f"    {self.url.rsplit('/', 1)[1]}: {type(e).__name__}: {e}; retry in {wait} s")
                time.sleep(wait)
    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.at
    def seek(self, offset, whence=0):
        self.at = (offset, self.at + offset, self.size + offset)[whence]
        return self.at
    def readinto(self, buffer):
        if self.at >= self.size or not len(buffer): return 0
        body = self.request(self.at, min(self.at + len(buffer), self.size) - 1)[0]
        buffer[:len(body)] = body
        self.at += len(body)
        return len(body)

def zip_layer(url, layer, folder, headers=None):
    """The path (without extension) of one Shapefile layer of a remote zip, its members fetched by range once a year:
    the members named `layer` (any folder inside the zip) with extension .shp, .shx, .dbf, .prj or .cpg."""
    folder = os.path.join("extracts", "shapefile", folder)
    base = os.path.join(folder, layer)
    if all(os.path.exists(base + x) and time.time() - os.path.getmtime(base + x) < 365 * 86_400 for x in (".shp", ".shx", ".dbf", ".prj")):
        return base
    import zipfile
    os.makedirs(folder, exist_ok=True)
    log(f"  fetching {layer} from {url}")
    with zipfile.ZipFile(io.BufferedReader(Ranged(url, headers() if callable(headers) else headers or {}), RANGE_CHUNK)) as z:
        members = [m for m in z.namelist() if os.path.splitext(m.rsplit("/", 1)[-1]) in
                   ((layer, x) for x in (".shp", ".shx", ".dbf", ".prj", ".cpg"))]
        if len({os.path.splitext(m)[1] for m in members} - {".cpg"}) < 4: raise SourceError(f"{url}: no complete layer {layer}")
        for m in members:
            file = base + os.path.splitext(m)[1]
            with z.open(m) as src, open(file + ".tmp", "wb") as dst: shutil.copyfileobj(src, dst, 1 << 20)
            os.replace(file + ".tmp", file)
    return base

def wkt_projection(prj):
    """A .prj's coordinates: None for longitude/latitude, else its Transverse Mercator as tm_constants() gives it. The
    datum must be within a metre of WGS84 (ETRS89, RDN2008, POSGAR, SIRGAS: realisations of the same frame, which drift
    apart by under 1 m in 2026); any other (Monte Mario, ED50, a local datum) is refused rather than misplaced."""
    datum = re.search(r'DATUM\["([^"]+)"', prj)
    if not datum or not re.search(r"WGS.?(19)?84|ETRS.?(19)?89|ETRF|RDN.?2008|POSGAR|SIRGAS", datum[1], re.I):
        raise SourceError(f"datum {datum[1] if datum else '?'}: not one within a metre of WGS84")
    if not prj.lstrip().startswith("PROJCS"): return None
    if not re.search(r'PROJECTION\["Transverse_Mercator"\]', prj, re.I): raise SourceError("a projection other than Transverse Mercator")
    unit = re.findall(r'UNIT\["[^"]+",\s*([\d.]+)', prj)
    if not unit or float(unit[-1]) != 1: raise SourceError("a projection not in metres")
    a, inverse_f = map(float, re.search(r'SPHEROID\["[^"]*",\s*([\d.]+),\s*([\d.]+)', prj).groups())
    p = {k.lower(): float(v) for k, v in re.findall(r'PARAMETER\["([^"]+)",\s*(-?[\d.]+)', prj)}
    return tm_constants(a, 1 / inverse_f, p.get("central_meridian", 0), p.get("scale_factor", 1), p.get("latitude_of_origin", 0),
                        p.get("false_easting", 0), p.get("false_northing", 0))

def tm_constants(a, f, lon0, k0, lat0, east0, north0):
    """Krüger's series to the 4th order in n (Karney 2011), a millimetre from the exact Transverse Mercator within
    several degrees of the central meridian (Rome lies 2.5° off UTM 33N's)."""
    n = f / (2 - f)
    tm = dict(n=n, k=k0 * a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64), lon0=lon0, east0=east0, north0=north0, north_lat0=0.0,
              alpha=(n / 2 - 2 / 3 * n ** 2 + 5 / 16 * n ** 3 + 41 / 180 * n ** 4, 13 / 48 * n ** 2 - 3 / 5 * n ** 3 + 557 / 1440 * n ** 4,
                     61 / 240 * n ** 3 - 103 / 140 * n ** 4, 49561 / 161280 * n ** 4),
              beta=(n / 2 - 2 / 3 * n ** 2 + 37 / 96 * n ** 3 - 1 / 360 * n ** 4, 1 / 48 * n ** 2 + 1 / 15 * n ** 3 - 437 / 1440 * n ** 4,
                    17 / 480 * n ** 3 - 37 / 840 * n ** 4, 4397 / 161280 * n ** 4),
              delta=(2 * n - 2 / 3 * n ** 2 - 2 * n ** 3 + 116 / 45 * n ** 4, 7 / 3 * n ** 2 - 8 / 5 * n ** 3 - 227 / 45 * n ** 4,
                     56 / 15 * n ** 3 - 136 / 35 * n ** 4, 4279 / 630 * n ** 4))
    tm["north_lat0"] = to_tm(tm, lat0, lon0)[1] - north0
    return tm

def to_tm(tm, lat, lon):
    """WGS84 longitude/latitude to the projection's (east, north) metres."""
    p, l = math.radians(lat), math.radians(lon - tm["lon0"])
    c = 2 * math.sqrt(tm["n"]) / (1 + tm["n"])
    t = math.sinh(math.atanh(math.sin(p)) - c * math.atanh(c * math.sin(p)))
    xi, eta = math.atan2(t, math.cos(l)), math.atanh(math.sin(l) / math.sqrt(1 + t * t))
    east = eta + sum(a * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, a in enumerate(tm["alpha"], 1))
    north = xi + sum(a * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, a in enumerate(tm["alpha"], 1))
    return tm["east0"] + tm["k"] * east, tm["north0"] + tm["k"] * north - tm["north_lat0"]

def from_tm(tm, east, north):
    """The projection's (east, north) metres to WGS84 (latitude, longitude)."""
    xi, eta = (north - tm["north0"] + tm["north_lat0"]) / tm["k"], (east - tm["east0"]) / tm["k"]
    xi, eta = (xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(tm["beta"], 1)),
               eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(tm["beta"], 1)))
    chi = math.asin(math.sin(xi) / math.cosh(eta))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(tm["delta"], 1))
    return math.degrees(lat), tm["lon0"] + math.degrees(math.atan2(math.sinh(eta), math.cos(xi)))

class Shapefile:
    """One polygon layer (.shp, .shx, .dbf, .prj; Polygon, PolygonZ or PolygonM, a Z or M ignored), read in place.
    Opening it reads every record's box once (from the .shp at each .shx offset) into a grid of SHAPE_BUCKET cells."""
    def __init__(self, base):
        with open(base + ".prj", encoding="latin-1") as f: self.tm = wkt_projection(f.read())
        encoding = "latin-1"
        if os.path.exists(base + ".cpg"):
            with open(base + ".cpg", encoding="ascii") as f: encoding = {"utf-8": "utf-8", "utf8": "utf-8"}.get(f.read().strip().lower(), "latin-1")
        self.encoding, self.shp, self.dbf = encoding, open(base + ".shp", "rb"), open(base + ".dbf", "rb")
        head = self.dbf.read(32)
        self.count, self.header, self.length = struct.unpack_from("<IHH", head, 4)
        self.fields, at = {}, 1
        for k in range((self.header - 33) // 32):
            f = self.dbf.read(32)
            if f[0] == 0x0D: break
            self.fields[f[:11].split(b"\0")[0].decode("latin-1")] = (at, f[16], chr(f[11]))
            at += f[16]
        with open(base + ".shx", "rb") as f: shx = f.read()
        self.offsets = array.array("q", (2 * struct.unpack_from(">i", shx, 100 + 8 * k)[0] for k in range((len(shx) - 100) // 8)))
        self.size = 1 / 400 if self.tm is None else SHAPE_BUCKET
        self.boxes, self.grid = array.array("d"), {}
        for k, offset in enumerate(self.offsets):
            self.shp.seek(offset + 8)
            kind, *box = struct.unpack("<i4d", self.shp.read(36))
            self.boxes.extend(box if kind else (0, 0, -1, -1))
            if not kind: continue
            for gx in range(math.floor(box[0] / self.size), math.floor(box[2] / self.size) + 1):
                for gy in range(math.floor(box[1] / self.size), math.floor(box[3] / self.size) + 1):
                    self.grid.setdefault((gx, gy), []).append(k)

    def box(self, rect):
        """A lat/lon rect in the file's coordinates: (x0, y0, x1, y1) around its corners and edge midpoints."""
        s, w, n, e = rect
        if self.tm is None: return w, s, e, n
        points = [to_tm(self.tm, lat, lon) for lat in (s, (s + n) / 2, n) for lon in (w, (w + e) / 2, e)]
        return min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points), max(y for _, y in points)

    def attributes(self, k, names):
        """Record k's fields `names` from the .dbf: numbers as floats (blank: None), text stripped; None if deleted."""
        self.dbf.seek(self.header + k * self.length)
        raw = self.dbf.read(self.length)
        if raw[:1] == b"*": return None
        out = {}
        for name in names:
            at, size, kind = self.fields[name]
            text = raw[at:at + size].decode(self.encoding, "replace").strip()
            out[name] = (number(text) if text else None) if kind in "NF" else text
        return out

    def table(self, names):
        """Every record's fields `names`, in order (a deleted one as None)."""
        return [self.attributes(k, names) for k in range(self.count)]

    def rings(self, k):
        """Record k's outer rings (clockwise, the Shapefile's rule; holes left out) as [(latitude, longitude)]. A ring
        under MIN_RING m² is left out: a sliver (half the volumes in Buenos Aires' densest cell, Barrio Padre Mugica)."""
        self.shp.seek(self.offsets[k] + 8)
        kind, = struct.unpack("<i", self.shp.read(4))
        if kind not in (5, 15, 25): return []
        body = self.shp.read(32 + 8)
        parts, points = struct.unpack_from("<ii", body, 32)
        starts = struct.unpack(f"<{parts}i", self.shp.read(4 * parts)) + (points,)
        xy = struct.unpack(f"<{2 * points}d", self.shp.read(16 * points))
        out = []
        for a, b in zip(starts, starts[1:]):
            ring = [(xy[2 * i], xy[2 * i + 1]) for i in range(a, b)]
            twice = -sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(ring, ring[1:])) if len(ring) >= 4 else 0
            if self.tm is None: twice *= M * M * math.cos(math.radians(ring[0][1]))
            if twice < 2 * MIN_RING: continue
            out.append([(y, x) if self.tm is None else from_tm(self.tm, x, y) for x, y in ring[:-1]])
        return out

    def records(self, rect, names):
        """(attributes, rings) for every record whose box meets the lat/lon rect."""
        x0, y0, x1, y1 = self.box(rect)
        seen = sorted({k for gx in range(math.floor(x0 / self.size), math.floor(x1 / self.size) + 1)
                       for gy in range(math.floor(y0 / self.size), math.floor(y1 / self.size) + 1) for k in self.grid.get((gx, gy), ())})
        for k in seen:
            bx0, by0, bx1, by1 = self.boxes[4 * k:4 * k + 4]
            if bx0 > x1 or bx1 < x0 or by0 > y1 or by1 < y0: continue
            p = self.attributes(k, names)
            if p is not None: yield p, self.rings(k)

def shapefile(url, layer, folder, names, height, headers=None):
    """A fetch over one zipped Shapefile layer (zip_layer(), opened once a run): each record whose box meets the rect,
    `height(attributes, shapefile)` as its height (None: the flagged 15 m guess; False: left out), one building
    per outer ring."""
    def fetch(rect):
        if (url, layer) not in _shapefiles: _shapefiles[(url, layer)] = Shapefile(zip_layer(url, layer, folder, headers))
        f = _shapefiles[(url, layer)]
        out = []
        for p, rings in f.records(rect, names):
            h = height(p, f)
            if h is False: continue
            out += [building([vertex(lat, lon) for lat, lon in ring], h if h and h > 0 else None) for ring in rings]
        return out
    return fetch

# The Italian DBT's unità volumetriche (Milan's Comune, Regione Lazio): UN_VOL_AV is a volume's own height above its
# base. For one al suolo (UN_VOL_POR 01) that is its height above ground. A raised one (02 overhang, 03 portico
# ceiling, 04 underpass ceiling, 05 loggia, 09 covered passage: a tenth of Milan's) starts above the ground, so its top
# is UN_VOL_QE, the extrusion's elevation, less its building's ground: the lowest base (QE - AV) of the building's
# ground volumes (CEDIUV), 3.9 m more than AV at the median in Milan. Underground volumes (08) are left out.
DBT_FIELDS = ("UN_VOL_AV", "UN_VOL_POR", "UN_VOL_QE", "CEDIUV")

def dbt_height(p, f):
    if p["UN_VOL_POR"] == "08": return False
    if p["UN_VOL_POR"] in ("", "01") or p["UN_VOL_QE"] is None: return p["UN_VOL_AV"]
    if not hasattr(f, "ground"):  # {building: its ground elevation}, read once a run
        ground = f.ground = {}
        for q in f.table(DBT_FIELDS):
            if q and q["UN_VOL_POR"] == "01" and q["UN_VOL_QE"] is not None and q["UN_VOL_AV"] is not None:
                ground[q["CEDIUV"]] = min(ground.get(q["CEDIUV"], math.inf), q["UN_VOL_QE"] - q["UN_VOL_AV"])
    base = f.ground.get(p["CEDIUV"])
    return p["UN_VOL_QE"] - base if base is not None and p["UN_VOL_QE"] - base > 0 else p["UN_VOL_AV"]

MILAN_DBT = "https://gisportal.comune.milano.it/download/area_download/SIT/DBT2020/DBT_2020.zip"
LAZIO_DBGT = "https://geoportale.regione.lazio.it/cartografia/api/raw/2020_DBGT_5K_SHP/Roma.zip"
BUENOS_AIRES_TEJIDO = "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/secretaria-de-desarrollo-urbano/tejido-urbano/tejido.zip"

def lazio_token():
    """Regione Lazio's file browser serves its files to an anonymous session: its login endpoint hands any caller a JWT."""
    token = get("https://geoportale.regione.lazio.it/cartografia/api/login", data=b"{}").decode().strip()
    if token.count(".") != 2: raise SourceError("Lazio file browser: no token")
    return {"X-Auth": token}

# The Environment Agency's LIDAR Composite, 1 m (England; London from the National LIDAR Programme's 2018–2021
# surveys), read through its WCS: no key, a DEFLATE GeoTIFF in British National Grid metres for any window.
LIDAR_WCS = "https://environment.data.gov.uk/spatialdata/{}/wcs?service=WCS&version=2.0.1&request=GetCoverage&coverageId={}" \
            "&format=image/tiff&subset=E({},{})&subset=N({},{})&geotiff:compression=DEFLATE"
LIDAR = {"dsm": ("lidar-composite-digital-surface-model-first-return-dsm-1m", "df4e3ec3-315e-48aa-aaaf-b5ae74d7b2bb__Lidar_Composite_Elevation_FZ_DSM_1m"),
         "dtm": ("lidar-composite-digital-terrain-model-dtm-1m", "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m")}
LIDAR_CHUNK = 2000   # metres a side, on the grid: one DSM and one DTM request each (~8 MB apiece, 3–6 s)
LIDAR_KEPT = 64      # chunks held (8 MB each): two rows of blocks across Greater London, so each is asked once a run
LIDAR_PERCENTILE = 0.9
VOID = -32768        # a chunk's no-height pixel
_lidar = {}          # (east, north) of a chunk's south-west corner → array('h') of heights in decimetres, row 0 north

# The Helmert transformation's mean offset from OSTN15 (east, north, metres) over each lidar city, by its centre; the
# nearest applies. London at six points from Harefield to Rainham (2026-10-03); the five others on a grid of 6–9 points
# inside each district against PROJ's OSTN15 grid (2026-10-04, each within 0.15 m of its mean across the district).
BNG_OFFSETS = {(51.49, -0.09): (1.7, 0.2), (52.49, -1.88): (0.4, 1.6), (51.47, -2.63): (-0.2, -0.2),
               (53.82, -1.55): (-0.3, 1.8), (53.40, -2.91): (0.0, 1.8), (53.40, -1.56): (0.0, 1.9)}

def bng(lat, lon):
    """WGS84 to British National Grid (OSGB36 Transverse Mercator) metres, through Ordnance Survey's 7-parameter
    Helmert transformation, less its mean offset from OSTN15 over the nearest lidar city (BNG_OFFSETS: 1.7 m east,
    0.2 m north over Greater London, up to 1.9 m north over the northern cities): within 0.4 m of OSTN15 in each."""
    a, b = 6378137.0, 6356752.3141  # GRS80
    e2, p, l = 1 - b * b / (a * a), math.radians(lat), math.radians(lon)
    nu = a / math.sqrt(1 - e2 * math.sin(p) ** 2)
    x, y, z = nu * math.cos(p) * math.cos(l), nu * math.cos(p) * math.sin(l), (1 - e2) * nu * math.sin(p)
    s, (rx, ry, rz) = 20.4894e-6, (math.radians(r / 3600) for r in (-0.1502, -0.2470, -0.8421))
    x, y, z = -446.448 + (1 + s) * x - rz * y + ry * z, 125.157 + rz * x + (1 + s) * y - rx * z, -542.060 - ry * x + rx * y + (1 + s) * z
    a, b = 6377563.396, 6356256.909  # Airy 1830
    e2, q = 1 - b * b / (a * a), math.hypot(x, y)
    p = math.atan2(z, q * (1 - e2))
    for _ in range(6): p = math.atan2(z + e2 * a / math.sqrt(1 - e2 * math.sin(p) ** 2) * math.sin(p), q)
    l, f0, p0, n = math.atan2(y, x) - math.radians(-2), 0.9996012717, math.radians(49), (a - b) / (a + b)
    sp, cp, t = math.sin(p), math.cos(p), math.tan(p)
    nu = a * f0 / math.sqrt(1 - e2 * sp * sp)
    rho = a * f0 * (1 - e2) / (1 - e2 * sp * sp) ** 1.5
    eta2 = nu / rho - 1
    m = b * f0 * ((1 + n + 5 / 4 * n ** 2 + 5 / 4 * n ** 3) * (p - p0) - (3 * n + 3 * n ** 2 + 21 / 8 * n ** 3) * math.sin(p - p0) * math.cos(p + p0)
                  + (15 / 8 * n ** 2 + 15 / 8 * n ** 3) * math.sin(2 * (p - p0)) * math.cos(2 * (p + p0))
                  - 35 / 24 * n ** 3 * math.sin(3 * (p - p0)) * math.cos(3 * (p + p0)))
    north = (m - 100000 + nu / 2 * sp * cp * l ** 2 + nu / 24 * sp * cp ** 3 * (5 - t * t + 9 * eta2) * l ** 4
             + nu / 720 * sp * cp ** 5 * (61 - 58 * t * t + t ** 4) * l ** 6)
    east = (400000 + nu * cp * l + nu / 6 * cp ** 3 * (nu / rho - t * t) * l ** 3
            + nu / 120 * cp ** 5 * (5 - 18 * t * t + t ** 4 + 14 * eta2 - 58 * t * t * eta2) * l ** 5)
    de, dn = BNG_OFFSETS[min(BNG_OFFSETS, key=lambda c: (c[0] - lat) ** 2 + ((c[1] - lon) * 0.6) ** 2)]
    return east - de, north - dn

def geotiff(raw):
    """A one-band float32 GeoTIFF, tiled or in strips, uncompressed or DEFLATE, no predictor (what the WCS sends):
    (west, north, width, height, array('f') row by row from the north)."""
    order = {b"II": "<", b"MM": ">"}.get(raw[:2])
    if not order or struct.unpack_from(order + "H", raw, 2)[0] != 42: raise SourceError("not a classic TIFF")
    at, = struct.unpack_from(order + "I", raw, 4)
    count, = struct.unpack_from(order + "H", raw, at)
    tags = {}
    for k in range(count):
        tag, kind, n, value = struct.unpack_from(order + "HHI4s", raw, at + 2 + 12 * k)
        code, size = {1: ("B", 1), 3: ("H", 2), 4: ("I", 4), 12: ("d", 8)}.get(kind, (None, 0))
        if code is None: continue
        data = value if n * size <= 4 else raw[struct.unpack(order + "I", value)[0]:][:n * size]
        tags[tag] = struct.unpack(order + code * n, data[:n * size])
    width, height = tags[256][0], tags[257][0]
    if tags.get(258, (32,))[0] != 32 or tags.get(339, (1,))[0] != 3 or tags.get(317, (1,))[0] != 1 or tags.get(277, (1,))[0] != 1:
        raise SourceError("not a one-band float32 GeoTIFF without predictor")
    compression = tags.get(259, (1,))[0]
    if compression not in (1, 8, 32946): raise SourceError(f"TIFF compression {compression}")
    if 34264 in tags: west, north = tags[34264][3], tags[34264][7]          # ModelTransformation
    else: west, north = tags[33922][3], tags[33922][4]                     # ModelTiepoint, pixel (0, 0)
    if 322 in tags: tw, th, offsets, sizes = tags[322][0], tags[323][0], tags[324], tags[325]
    else: tw, th, offsets, sizes = width, tags.get(278, (height,))[0], tags[273], tags[279]
    across = -(-width // tw)
    out = array.array("f", bytes(4 * width * height))
    for k, (start, size) in enumerate(zip(offsets, sizes)):
        block = raw[start:start + size]
        if compression != 1: block = zlib.decompress(block)
        values = array.array("f", block)
        if order != ("<" if sys.byteorder == "little" else ">"): values.byteswap()
        x0, y0 = (k % across) * tw, (k // across) * th
        w = min(tw, width - x0)
        for r in range(min(th, height - y0, len(values) // tw)):
            out[(y0 + r) * width + x0:(y0 + r) * width + x0 + w] = values[r * tw:r * tw + w]
    return west, north, width, height, out

def lidar_tiff(kind, e0, n0, e1, n1):
    """One window of the DSM or the DTM, parsed inside the retry: a truncated or XML answer is asked again."""
    return get(LIDAR_WCS.format(*LIDAR[kind], e0, e1, n0, n1), parse=geotiff)

def lidar_chunk(e0, n0):
    """Heights above ground in one chunk, decimetres: DSM (first return) minus DTM. VOID where either has no data or
    the two are within 0.5 m: ground, or a void the composite filled from the DTM — dark glass returns nothing, and
    30 St Mary Axe reads under 0.5 m over most of its footprint (its 90th percentile 180.3 m with them left out)."""
    if (e0, n0) in _lidar:
        _lidar[(e0, n0)] = _lidar.pop((e0, n0))  # the most recent last
        return _lidar[(e0, n0)]
    surface, ground = (lidar_tiff(k, e0, n0, e0 + LIDAR_CHUNK, n0 + LIDAR_CHUNK) for k in ("dsm", "dtm"))
    if surface[:4] != ground[:4] or surface[:4] != (e0, n0 + LIDAR_CHUNK, LIDAR_CHUNK, LIDAR_CHUNK):
        raise SourceError(f"lidar: chunk {e0},{n0} came back on another grid ({surface[:4]}, {ground[:4]})")
    heights = array.array("h", (VOID if a < -1e30 or g < -1e30 or abs(a - g) < 0.5 else max(-32767, min(32767, round((a - g) * 10)))
                          for a, g in zip(surface[4], ground[4])))
    _lidar[(e0, n0)] = heights
    while len(_lidar) > LIDAR_KEPT: del _lidar[next(iter(_lidar))]
    return heights

def lidar_height(outline):
    """The lidar's height for one footprint: the 90th percentile of the heights of the 1 m pixels whose centre it
    holds (the maximum is a mast or a spire, the median half a pitched roof), ground and void pixels left out. None
    under 4 pixels, or under 2 m: a building the survey saw as ground was built after it."""
    ring = [bng(p["latitude"], p["longitude"]) for p in outline]
    edges = list(zip(ring, ring[1:] + ring[:1]))
    heights, at, chunk = [], None, None
    for north in range(math.floor(min(y for _, y in ring)), math.ceil(max(y for _, y in ring))):
        y = north + 0.5
        xs = sorted(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]) for a, b in edges if (a[1] > y) != (b[1] > y))
        for x0, x1 in zip(xs[::2], xs[1::2]):
            for east in range(math.ceil(x0 - 0.5), math.floor(x1 - 0.5) + 1):
                e0, n0 = east // LIDAR_CHUNK * LIDAR_CHUNK, north // LIDAR_CHUNK * LIDAR_CHUNK
                if at != (e0, n0): at, chunk = (e0, n0), lidar_chunk(e0, n0)
                h = chunk[(n0 + LIDAR_CHUNK - 1 - north) * LIDAR_CHUNK + east - e0]
                if h != VOID: heights.append(h)
    if len(heights) < 4: return None
    heights.sort()
    rank = LIDAR_PERCENTILE * (len(heights) - 1)
    lo = int(rank)
    h = (heights[lo] + (heights[min(lo + 1, len(heights) - 1)] - heights[lo]) * (rank - lo)) / 10
    return h if h >= 2 else None

def lidar(rect):
    """OpenStreetMap's footprints, each with the lidar's height where it reads one (a combine row's fetch)."""
    return [building(b["outline"], h) for b in osm_buildings(rect, True) if (h := lidar_height(b["outline"])) is not None]

def building_row(name, lat, lon, fetch, osm_on_error=False, boundary=None, combine=None):
    return dict(city=name, lat=lat, lon=lon, fetch=fetch, osm_on_error=osm_on_error, boundary=boundary, combine=combine)

def in_feed(b, lat, lon): return in_box(b, lat, lon) and (b["boundary"] is None or contains(b["boundary"], lat, lon))

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
CITY_BUILDINGS = [
    building_row("Melbourne", (-37.8507, -37.7755), (144.897, 144.9913), boundary=boundary("melbourne"), fetch=lambda r: footprints(opendatasoft(
        "data.melbourne.vic.gov.au", "2023-building-footprints", in_bbox("geo_point_2d", r) + ' and footprint_type != "Tunnel"',
        "structure_extrusion"), lambda p: p.get("structure_extrusion"))),
    building_row("Amsterdam", (52.28, 52.43), (4.73, 5.02), bag3d),
    building_row("Rotterdam", (51.86, 51.99), (4.37, 4.60), bag3d),
    building_row("The Hague", (52.00, 52.13), (4.18, 4.42), bag3d),
    building_row("Utrecht", (52.03, 52.14), (4.97, 5.20), bag3d),
    building_row("Berlin", (52.33, 52.68), (13.08, 13.77), lambda r: footprints(wfs(
        "https://gdi.berlin.de/services/wfs/ua_gebaeudehoehen", {"TYPENAMES": "ua_gebaeudehoehen:gebaeudehoehen",
        "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326", "PROPERTYNAME": "geom,hoehe"}, r), lambda p: p.get("hoehe"))),
    building_row("Geneva", (46.17, 46.24), (6.10, 6.19), lambda r: footprints(arcgis(
        "https://vector.sitg.ge.ch/arcgis/rest/services/CAD_BATIMENT_HORSOL/FeatureServer/0", r, ["HAUTEUR"]),
        lambda p: p.get("HAUTEUR"))),
    # BLDG_HEIGH is feet.
    building_row("Denver", (39.66, 39.80), (-105.05, -104.87), lambda r: footprints(arcgis(
        "https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_PROP_BUILDINGOUTLINES_A/FeatureServer/111",
        r, ["BLDG_HEIGH"]), lambda p: p["BLDG_HEIGH"] * 0.3048 if p.get("BLDG_HEIGH") else None)),
    building_row("Cape Town", (-34.20, -33.55), (18.30, 18.80), lambda r: footprints(arcgis(
        "https://esapqa.capetown.gov.za/agsext/rest/services/Theme_Based/ODP_SPLIT_6/FeatureServer/2", r, ["BLD_HGT"]),
        lambda p: p.get("BLD_HGT"))),
    building_row("São Paulo", (-23.65, -23.49), (-46.74, -46.59), lambda r: footprints(wfs(
        "https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/ows", {"TYPENAMES": "geoportal:edificacao",
        "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326", "PROPERTYNAME": "ge_poligono,qt_altura_edificacao"}, r),
        lambda p: p.get("qt_altura_edificacao"))),
    building_row("Wrocław", (51.08, 51.16), (16.95, 17.10), lambda r: footprints(arcgis(
        "https://gis.um.wroc.pl/portal_srv/rest/services/SMH_2022_Budynki/MapServer/0", r, ["HA"]), lambda p: p.get("HA"))),
    # Ramat Gan, Holon, Herzliya (ArcGIS Online, no licence stated): floors × 3 m. Before Tel Aviv's
    # box, which covers Ramat Gan whole; each held to its municipal boundary (OSM, admin level 8).
    building_row("Ramat Gan", (32.035, 32.106), (34.799, 34.855), boundary=boundary("ramat-gan"), fetch=lambda r: footprints(arcgis(
        "https://services8.arcgis.com/MdnyDq2GlaTWezhQ/arcgis/rest/services/building_23_02_2025/FeatureServer/0", r,
        ["NUM_FLOORS"]), lambda p: number(p.get("NUM_FLOORS")) * 3 if number(p.get("NUM_FLOORS")) else None)),
    building_row("Holon", (31.988, 32.039), (34.755, 34.815), boundary=boundary("holon"), fetch=lambda r: footprints(arcgis(
        "https://services2.arcgis.com/cjDo9oPmimdHxumn/arcgis/rest/services/Buildings_shp/FeatureServer/0", r,
        ["NUM_FLOORS"], oid="FID"), lambda p: number(p.get("NUM_FLOORS")) * 3 if number(p.get("NUM_FLOORS")) else None)),
    building_row("Herzliya", (32.144, 32.203), (34.787, 34.865), boundary=boundary("herzliya"), fetch=lambda r: footprints(arcgis(
        "https://services3.arcgis.com/9qGhZGtb39XMVQyR/arcgis/rest/services/herzliya_reka_2023/FeatureServer/3", r,
        ["Num_floors"]), lambda p: number(p.get("Num_floors")) * 3 if number(p.get("Num_floors")) else None)),
    # Metres, else floors × 3 m. Answers only from Israel (HTTP 571 elsewhere): from the cloud every
    # Tel Aviv cell fails and no tile is written — never an OSM tile in its place.
    building_row("Tel Aviv", (32.02, 32.16), (34.73, 34.86), lambda r: footprints(arcgis(
        "https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2/MapServer/513", r, ["gova_simplex_2019", "ms_komot"],
        oid="oid_mivne"), lambda p: p.get("gova_simplex_2019") or (p["ms_komot"] * 3 if p.get("ms_komot") else None))),
    # Jerusalem Municipality, bldg2020 (metres; "for reference and general use only").
    building_row("Jerusalem", (31.70, 31.89), (35.10, 35.27), lambda r: footprints(arcgis(
        "https://services3.arcgis.com/jeqc1A7OfE9m4EPO/arcgis/rest/services/bldg2020/FeatureServer/0", r,
        ["realHeight", "height"]), lambda p: p["realHeight"] if (p.get("realHeight") or 0) > 0 else p.get("height"))),
    # Spain's cadastre (owner's decision, 2026-09-25): tiles hold derived footprints and heights, never
    # Catastro's GML, which its licence forbids spreading untransformed. A cell Catastro fails gets no tile
    # (the next run retries), never an OSM tile.
    building_row("Madrid", (40.31, 40.56), (-3.84, -3.52), catastro),
    building_row("Seville", (37.32, 37.45), (-6.03, -5.87), catastro),
    building_row("Barcelona", (41.32, 41.47), (2.05, 2.23), catastro),
    building_row("Valencia", (39.40, 39.52), (-0.43, -0.30), catastro),
    building_row("Zaragoza", (41.58, 41.72), (-0.98, -0.80), catastro),
    building_row("Málaga", (36.66, 36.78), (-4.55, -4.35), catastro),
    building_row("Palma", (39.53, 39.62), (2.58, 2.75), catastro),
    building_row("Las Palmas", (28.05, 28.18), (-15.47, -15.40), catastro),
    building_row("Murcia", (37.95, 38.03), (-1.18, -1.08), catastro),
    building_row("Alicante", (38.32, 38.40), (-0.53, -0.43), catastro),
    building_row("Córdoba", (37.85, 37.92), (-4.82, -4.73), catastro),
    building_row("Valladolid", (41.60, 41.70), (-4.78, -4.68), catastro),
    building_row("Vigo", (42.19, 42.26), (-8.78, -8.67), catastro),
    building_row("Gijón", (43.50, 43.56), (-5.72, -5.62), catastro),
    # Not Catastro: the Basque cadastres are their own. Gipuzkoa's covers the whole province, so the
    # box is the municipality's (cities.json) and needs no boundary.
    building_row("San Sebastián", (43.2178, 43.3382), (-2.0868, -1.8879), gipuzkoa),
    # CoJ Building Footprints (no licence stated): ELEVATION is metres above ground (Carlton Centre
    # 204.1, Michelangelo Towers 145.7; the city sits at ~1,750 m). Captured for the CBDs, business
    # zones and station areas only; the box is the layer's extent.
    building_row("Johannesburg", (-26.5696, -25.9075), (27.7418, 28.2540), lambda r: footprints(arcgis(
        "https://ags.joburg.org.za/server/rest/services/Property/MapServer/1", r, ["ELEVATION"]),
        lambda p: p.get("ELEVATION"))),
    building_row("Bologna", (44.42, 44.56), (11.23, 11.44), lambda r: footprints(opendatasoft(
        "opendata.comune.bologna.it", "c_a944ctc_edifici_pl", in_bbox("geo_point_2d", r), "altezza_gr"),
        lambda p: p.get("altezza_gr"))),
    building_row("Turin", (45.00, 45.14), (7.57, 7.78), turin),
    # height_roof is feet; one row carries its own BIN as its height, hence the cap.
    building_row("New York", (40.49, 40.92), (-74.26, -73.68), socrata_buildings(
        "data.cityofnewyork.us", "5zhs-2jue", "the_geom", "height_roof", lambda h: h * 0.3048 if h and h < 2000 else None)),
    # No height at all: floors × 3 m; 0 floors (a third of the Loop) falls to 15 m.
    building_row("Chicago", (41.64, 42.02), (-87.94, -87.52), socrata_buildings(
        "data.cityofchicago.org", "syp8-uezg", "the_geom", "stories", lambda h: h * 3 if h and h > 0 else None)),
    building_row("San Francisco", (37.70, 37.83), (-122.52, -122.35), socrata_buildings(
        "data.sf.gov", "ynuv-fyni", "shape", "hgt_median_m", lambda h: h)),
    # Roof elevation minus the lowest ground point, metres (Open Government Licence – City of Calgary).
    building_row("Calgary", (50.84, 51.22), (-114.32, -113.86), lambda r: footprints(socrata(
        "data.calgary.ca", "cchr-krqg", intersects("polygon", r), "rooftop_elev_z,grd_elev_min_z,polygon"),
        lambda p: None if number(p.get("rooftop_elev_z")) is None or number(p.get("grd_elev_min_z")) is None
        else number(p["rooftop_elev_z"]) - number(p["grd_elev_min_z"]))),
    # The 2009 capture, height above ground in metres.
    building_row("Vancouver", (49.19, 49.32), (-123.23, -123.02), lambda r: footprints(opendatasoft(
        "opendata.vancouver.ca", "building-footprints-2009", in_bbox("geo_point_2d", r), "hgt_agl"),
        lambda p: p.get("hgt_agl"))),
    # Mean roof height above ground; the QGIS server answers WFS 1.1.0 only (2.0.0 returns an HTML error page).
    building_row("Zurich", (47.32, 47.44), (8.44, 8.63), lambda r: footprints(wfs(
        "https://www.ogd.stadt-zuerich.ch/wfs/geoportal/Bauten___Blockmodell", {"VERSION": "1.1.0",
        "TYPENAME": "bauten_blockmodell_2d", "MAXFEATURES": "5000", "OUTPUTFORMAT": "application/vnd.geo+json",
        "SRSNAME": "EPSG:4326", "PROPERTYNAME": "geometry,h_rel_mean_boden"}, r), lambda p: p.get("h_rel_mean_boden"))),
    # max_height is feet (2013 capture).
    building_row("Austin", (30.10, 30.52), (-97.94, -97.56), socrata_buildings(
        "data.austintexas.gov", "3qcc-8uhz", "the_geom", "max_height", lambda h: h * 0.3048 if h else None)),
    # LARIAC4, City of Los Angeles only (neighbouring cities answer nothing and fall to OSM); HEIGHT is feet.
    building_row("Los Angeles", (33.70, 34.34), (-118.67, -118.15), lambda r: footprints(arcgis(
        "https://services5.arcgis.com/7nsPwEMP38bSkCjy/arcgis/rest/services/Building_Footprints/FeatureServer/0",
        r, ["HEIGHT"]), lambda p: p["HEIGHT"] * 0.3048 if p.get("HEIGHT") else None)),
    # LoD2 measuredHeight, metres (Datenlizenz Deutschland – Namensnennung – 2.0); box: the model's extent.
    building_row("Hamburg", (53.39, 53.94), (8.48, 10.34), hamburg),
    # max_hgt is feet, else approx_hgt.
    building_row("Philadelphia", (39.86, 40.14), (-75.29, -74.95), lambda r: footprints(arcgis(
        "https://services.arcgis.com/fLeGjb7u4uXqeF9q/arcgis/rest/services/LI_BUILDING_FOOTPRINTS/FeatureServer/0",
        r, ["max_hgt", "approx_hgt"], oid="objectid"),
        lambda p: (p.get("max_hgt") or p.get("approx_hgt") or 0) * 0.3048 or None)),
    # Wired 2026-10-02. Statewide LoD2 models (Düsseldorf: Dreischeibenhaus 93.6 m; Nuremberg: Business
    # Tower 131.6 m), so the box is the city's cities.json area.
    building_row("Düsseldorf", (51.179, 51.269), (6.7203, 6.8497), citygml_tiles(
        "https://www.opengeodata.nrw.de/produkte/geobasis/3dg/lod2_gml/lod2_gml/LoD2_32_{e}_{n}_1_NW.gml", 1)),
    building_row("Nuremberg", (49.409, 49.491), (11.0034, 11.1366), citygml_tiles(
        "https://download1.bayernwolke.de/a/lod2/citygml/{e}_{n}.gml", 2)),
    # ZG3D 2022 (Otvorena dozvola): Z_Delta is metres from ground to top (the cathedral 103.5 m). The box is the layer's.
    building_row("Zagreb", (45.622, 45.969), (15.771, 16.229), lambda r: footprints(arcgis(
        "https://services8.arcgis.com/Usi0jGQwMmBUpFjr/arcgis/rest/services/ZG3D_2022_3d_model_GZ/FeatureServer/0", r,
        ["Z_Delta"]), lambda p: p.get("Z_Delta"))),
    # Beoland's central-Belgrade model (no licence stated; Beograđanka 99.8 m). The box is the layer's.
    building_row("Belgrade", (44.797, 44.824), (20.438, 20.485), beoland),
    # One polygon per floor slab: the ground floor's, CantPisos × 3.5 m (each slab's ALTURA). The box is the layer's.
    building_row("San José", (9.9005, 9.9658), (-84.1499, -84.0472), lambda r: footprints(arcgis(
        "https://services5.arcgis.com/0ZvuJDanWVJc4vYr/arcgis/rest/services/SIG_SER_3D_Edificaciones/FeatureServer/0", r,
        ["CantPisos"], where="PISO=1"), lambda p: number(p.get("CantPisos")) * 3.5 if number(p.get("CantPisos")) else None)),
    # Santa Clara County's 2020 lidar footprints (county-wide, no licence stated): Building_H is feet above
    # ground (houses 10–30, City Hall 285.6). The box is San Jose's cities.json area.
    building_row("San Jose", (37.309, 37.359), (-121.9236, -121.8544), lambda r: footprints(arcgis(
        "https://maps.santaclaracounty.gov/server/rest/services/opendata/SCCGISHUB/MapServer/40", r, ["Building_H"]),
        lambda p: p["Building_H"] * 0.3048 if p.get("Building_H") else None)),
    # Regione Liguria's region-wide NC5 3D footprints (CC BY 4.0; Torre Piacentini 99.9 m); where NC5
    # has nothing (Pegli, Voltri) the cell falls to OSM. The box is Genoa's cities.json area.
    building_row("Genoa", (44.371, 44.441), (8.8399, 9.0141), liguria),
    # Sofiaplan's "Сгради 18.12.2009" (the 2009 cadastre's buildings; licence not stated by the publisher, wired
    # 2026-10-02 on the owner's decision): sgr_text's leading number is floors (26МСБЖ, Park Hotel Moskva: 78 m),
    # none is one floor. Towers built since 2009 are missing. The box is the layer's.
    building_row("Sofia", (42.424, 42.857), (23.077, 23.639), lambda r: footprints(arcgis(
        "https://gis.sofiaplan.bg/server/rest/services/oup_2009/oup_2009/FeatureServer/98", r, ["sgr_text"], oid="objectid"),
        lambda p: int((re.match(r"\d+", p.get("sgr_text") or "") or ["1"])[0]) * 3.0)),
    # Combined with OSM (owner, 2026-10-02): Bratislava's Pocet_obyvatelov_budovy holds residential buildings
    # only (Vyska, metres; licence not stated by the publisher), so the office towers come from OSM.
    # The box is the layer's extent.
    building_row("Bratislava", (48.0059, 48.2894), (16.9578, 17.3772), combine="metres", fetch=lambda r: footprints(arcgis(
        "https://services8.arcgis.com/pRlN1m0su5BYaFAS/arcgis/rest/services/Pocet_obyvatelov_budovy/FeatureServer/0", r,
        ["Vyska"]), lambda p: number(p.get("Vyska")), guess=False)),
    # Oakland's 2015 BuildingFootprints (Socrata iqfp-6kz5; licence not stated by the publisher): nostory
    # floors × 3 m, combined with OSM (complete in few cells). The box is the layer's extent.
    building_row("Oakland", (37.7224, 37.8527), (-122.3301, -122.1622), combine="floors", fetch=oakland),
    # Wired 2026-10-03. NRCan's Automatically Extracted Buildings, the GTA's 2023 lidar (Open Government Licence –
    # Canada): heightmax, metres above ground, on every footprint. One GeoPackage for the whole GTA, cut to Vaughan's
    # box and held to its boundary (York Region's Municipal Boundary, York Region Open Data Licence): Toronto,
    # Brampton, Markham and King stay OSM.
    building_row("Vaughan", (43.7498, 43.9243), (-79.7108, -79.42), boundary=boundary("vaughan"),
                 fetch=geopackage(NRCAN_GTA, "heightmax")),
    # City of Edmonton Rooflines as of 2019 (Socrata jpxi-a9a5; Open Government Licence – City of Edmonton):
    # building_height, metres (roof minus ground). The box is the layer's; held to the corporate boundary (a62q-eaea),
    # which keeps St. Albert and Sherwood Park off it.
    building_row("Edmonton", (53.3382, 53.7158), (-113.7134, -113.2784), boundary=boundary("edmonton"), fetch=socrata_buildings(
        "data.edmonton.ca", "jpxi-a9a5", "the_geom", "building_height", lambda h: h)),
    # Wired 2026-10-03. The Environment Agency's LIDAR Composite DSM and DTM, 1 m (Open Government Licence; "© Environment
    # Agency copyright and/or database right 2022. All rights reserved."), on OSM's footprints: each takes the 90th
    # percentile of DSM minus DTM inside it (lidar() above). Against OSM's height tags, within 2 m on 16 of 21 ordinary
    # buildings (median error 1.0 m); against 22 towers' published heights, a median error of 4 m. Held to Greater
    # London: ONS Regions (December 2024) BFE, the tidal Thames included (Open Government Licence v3.0; contains OS
    # data © Crown copyright and database right 2024).
    building_row("London", (51.2868, 51.6919), (-0.5102, 0.334), boundary=boundary("london"), combine="metres", fetch=lidar),
    # Wired 2026-10-04: London's lidar fetch for five more English cities (the composite is England-wide: no void over
    # any of the five centres, and every footprint of a central cell read a height in Birmingham, Leeds, Liverpool and
    # Sheffield, 9 of 12 in Bristol's Castle Park, the three others built after the survey). Flat-topped towers read
    # within 2 m of their published heights (BT Tower 139.8 for 140, Altus House 113.4 for 114, St Paul's Tower 99.9
    # for 101, Arts Tower 78.5 for 78, Beetham Tower 93.1 for 90); a clock tower or a spire on a wide footprint reads
    # the main roof (the 90th percentile), as in London. Each row is held to its own Local Authority District: ONS
    # Local Authority Districts (December 2025) BFC (Open Government Licence v3.0; contains OS data © Crown copyright
    # and database right 2025), simplified to 2 m. The cities.json areas are unchanged; Liverpool's tiled area keeps
    # OpenStreetMap's boundary (liverpool.geojson, the estuary included), the lidar row the ONS one.
    building_row("Birmingham", (52.381, 52.6088), (-2.0337, -1.7288), boundary=boundary("birmingham"), combine="metres", fetch=lidar),
    building_row("Bristol", (51.3972, 51.5445), (-2.7559, -2.5104), boundary=boundary("bristol"), combine="metres", fetch=lidar),
    building_row("Leeds", (53.6989, 53.9459), (-1.8005, -1.2903), boundary=boundary("leeds"), combine="metres", fetch=lidar),
    building_row("Liverpool", (53.3268, 53.475), (-3.0088, -2.818), boundary=boundary("liverpool-ons"), combine="metres", fetch=lidar),
    building_row("Sheffield", (53.3045, 53.5032), (-1.8015, -1.3245), boundary=boundary("sheffield"), combine="metres", fetch=lidar),
    # Wired 2026-10-05 (the heights survey of 2026-10-04). Toronto: Vaughan's GTA GeoPackage again (heightmax on all
    # 928,813 footprints; one download serves both rows), the box the City's boundary's and held to it (toronto.geojson,
    # the permit row's): York University is in, Mississauga, Markham and Pickering stay OSM. After Vaughan, whose box
    # it overlaps. Both NRCan files draw attached buildings as one footprint (a Plateau block is one outline at its
    # tallest part), so a cell holds fewer footprints than OSM's, every one with a height.
    building_row("Toronto", (43.581, 43.8555), (-79.6393, -79.1152), boundary=boundary("toronto"),
                 fetch=geopackage(NRCAN_GTA, "heightmax")),
    # Montréal: NRCan's Autobuilding_QC_VILLE_MONTREAL (the 2015 municipal lidar, Open Government Licence – Canada):
    # heightmax on 227,310 of 227,327 footprints, -1 on the rest (the flagged guess). The box is the file's extent,
    # which reaches Pont-Viau across the Rivière des Prairies; held to the agglomeration (montreal.geojson: the island,
    # the villes liées included, which the file covers like the arrondissements), so Laval and Longueuil stay OSM.
    building_row("Montréal", (45.3933, 45.7044), (-73.9762, -73.4732), boundary=boundary("montreal"),
                 fetch=geopackage(NRCAN_MONTREAL, "heightmax")),
    # Bogotá: IDECA's Construcción (UAECD, CC BY 4.0; 2.4 M footprints, refreshed monthly, 2,000 a page): CONNPISOS
    # floors × 3 m, 0 floors (9 of 261 at Zona T) the flagged guess; CONALTURA is not a height. The box is the layer's
    # extent, the Distrito Capital.
    building_row("Bogotá", (3.8214, 4.8324), (-74.3934, -73.9939), lambda r: footprints(arcgis(
        "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/catastro/construccion/MapServer/0", r, ["CONNPISOS"]),
        lambda p: number(p.get("CONNPISOS")) * 3 if number(p.get("CONNPISOS")) else None)),
    # Wired 2026-10-05 (the heights survey of 2026-10-04). MLIT's Project PLATEAU 3D都市モデル, the 2025 editions (PLATEAU site
    # policy: PDL 1.0, CC BY-compatible, commercial use allowed; the credit names the model and says it was processed):
    # bldg:measuredHeight, metres (lidar 2021 in Tokyo, 2017 in Osaka), on the LoD2 ground surface, else the LoD0 footprint or
    # roof edge (plateau_buildings). Each box is the city's tiled area (cities.json), no other tiled city near it. Tokyo: the
    # 23 wards' files (307 meshes, 981,053 buildings in the box), which cover the box whole. Osaka: 大阪市's (129 meshes,
    # 387,528 buildings); the box's eastern sliver past the city line (Higashiōsaka, Moriguchi) has no file and falls to OSM.
    building_row("Tokyo", (35.6298, 35.7398), (139.6616, 139.7971), plateau([f"131{k:02d}" for k in range(1, 24)])),
    building_row("Osaka", (34.6394, 34.7494), (135.4664, 135.6002), plateau(["27100"])),
    # Wired 2026-10-05 (the heights survey of 2026-10-04): three zipped Shapefiles (shapefile()), each box the city's
    # tiled area (cities.json), held to the municipality its file covers. Buenos Aires: Tejido Urbano (Subsecretaría de
    # Planeamiento, data to 2021, CC BY 2.5 AR; WGS84): altura, metres, on all 1,386,616 volumes; two read 280 and
    # 830 m, over the city's tallest (Alvear Tower, 235 m): over 250 is the flagged guess. Held to CABA's perimetro
    # (CC BY 2.5 AR): Vicente López and the partidos past General Paz stay OSM.
    building_row("Buenos Aires", (-34.6433, -34.5332), (-58.4952, -58.3615), boundary=boundary("buenos-aires"),
                 fetch=shapefile(BUENOS_AIRES_TEJIDO, "tejido", "buenos-aires", ("altura",),
                                 lambda p, f: p["altura"] if p["altura"] and p["altura"] <= 250 else None)),
    # Milan: the Comune's DBT 2020 (CC BY 4.0; RDN2008 / UTM 32N), 237,743 unità volumetriche (dbt_height). Held to the
    # Comune's confine (CC BY 4.0): Sesto San Giovanni, Bresso, Corsico and the rest of the box stay OSM.
    building_row("Milan", (45.435, 45.5451), (9.0903, 9.2473), boundary=boundary("milan"),
                 fetch=shapefile(MILAN_DBT, "UN_VOL", "milan", DBT_FIELDS, dbt_height)),
    # Rome: Regione Lazio's DBGT 2020 for Roma (ETRS89 / UTM 33N; 386,769 volumes, 78 % surveyed 2003, the rest 2014 and
    # 2023; the licence asks that the source be named), the same rule. Monuments drawn as other classes (the Colosseum)
    # are not volumes. Held to Roma Capitale (ISTAT's 2025 limits, CC BY 4.0): the Vatican, which the file leaves out,
    # stays OSM.
    building_row("Rome", (41.8417, 41.9517), (12.4256, 12.5734), boundary=boundary("rome"),
                 fetch=shapefile(LAZIO_DBGT, "UN_VOL", "rome", DBT_FIELDS, dbt_height, headers=lazio_token)),
]


# ---------------------------------------------------------------- terraces

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

def not_applicable(text):
    """"N/A", a blank or a bare number names no company (Washington DC's ApplicantCompany on most rows)."""
    name = text.strip()
    return None if name.upper() in ("N/A", "NA", "NONE") or not re.search(r"[A-Za-z]", name) else name

CLEAN = {"digit": cut_before_first_digit, "boulevard": boulevard_name, "vilnius": vilnius_venue_name,
         "riga": riga_venue_name, "toronto": toronto_name, "helsinki": helsinki_venue_name, "seattle": seattle_venue_name,
         "n/a": not_applicable}

# A snapshot's permit survives only where a venue of today stands under its name (Los Angeles, a January 2021 list,
# 2026-10-05). The venues are the published venues/ tiles (OpenStreetMap's: Apple's cannot be read from a build), within
# MATCH_REACH metres under a matching name — the app's own rule (TerraceNames.swift: normalised, namesMatch), ported so a
# permit kept here is one the app can hand to the venue. A permit with no name, or no venue tile, is dropped.
MATCH_REACH = 60
_venue_tiles = {}   # {key: items} read once per run

PLACE_WORDS = {
    "BAR", "BARS", "CAFE", "CAFES", "COFFEE", "PUB", "RESTAURANT", "RESTAURANTE", "RISTORANTE", "BRASSERIE",
    "BISTRO", "CERVECERIA", "TABERNA", "TAVERNA", "KNEIPE", "TRATTORIA", "OSTERIA", "THE", "LE", "LA", "LES",
    "EL", "LOS", "LAS", "IL", "DE", "DU", "DES", "DEL", "DI", "DER", "DIE", "DAS", "Y", "ET", "E", "AND", "UND",
}

def normalised_name(name):
    """Uppercased, accents and punctuation stripped, spaces collapsed: "Café Oz" and "CAFE OZ - THE AUSTRALIAN BAR" meet."""
    if not name: return ""
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", name) if not unicodedata.combining(ch)).upper()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded).split())

def names_match(lhs, rhs):
    """One name contains the other, or they share their first two words, or every word of the shorter is in the longer once
    words that name a kind of place and bare numbers are set aside — one short shared word is not a match."""
    if not lhs or not rhs: return False
    if rhs in lhs or lhs in rhs: return True
    left, right = lhs.split(" ")[:2], rhs.split(" ")[:2]
    if len(left) == 2 and left == right: return True
    core = lambda name: {w for w in name.split(" ") if w not in PLACE_WORDS and not w.isdigit()}
    small, big = sorted([core(lhs), core(rhs)], key=len)
    if not (len(small) >= 2 or (len(small) == 1 and len(next(iter(small))) >= 5)): return False
    return small <= big

def venue_tiles_around(lat, lon):
    """The keys of the published venue tiles a venue within MATCH_REACH of the point can sit in (one to four)."""
    s, w, n, e = padded((lat, lon, lat, lon), MATCH_REACH)
    return sorted({f"{index(a, COARSE)},{index(b, COARSE)}" for a in (s, n) for b in (w, e)})

def surviving(items, city):
    """The permits a venue of today stands under: a named one with a venue within MATCH_REACH under a matching name.
    A venue tile that could not be read fails the feed (half an answer is not a tile), a missing one holds no venue."""
    out = []
    for t in items:
        wanted = normalised_name(t.get("name"))
        if not wanted: continue
        lat, lon = t["coordinate"]["latitude"], t["coordinate"]["longitude"]
        venues = []
        for key in venue_tiles_around(lat, lon):
            if key not in _venue_tiles: _venue_tiles[key] = published_items("venues", key, bust=True)
            if isinstance(_venue_tiles[key], SourceError): raise SourceError(f"{city}: venues/{key}: {_venue_tiles[key]}")
            venues += _venue_tiles[key]
        if any(metres(lat, lon, v["coordinate"]["latitude"], v["coordinate"]["longitude"]) <= MATCH_REACH
               and names_match(normalised_name(v.get("name")), wanted) for v in venues):
            out.append(t)
    return out

def arcgis_filter(text):
    """ArcGIS dates: `DATE 'YYYY-MM-DD'` in the where clause; {today} and {fifteenMonthsAgo} (Seattle) resolved at run time."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    return text.replace("{fifteenMonthsAgo}", (today - datetime.timedelta(days=457)).isoformat()).replace("{today}", today.isoformat())

def socrata_filter(text):
    """Socrata dates: a floating timestamp, no zone."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    try: ago = now.replace(year=now.year - 2)
    except ValueError: ago = now.replace(year=now.year - 2, day=28)  # 29 February
    return text.replace("{twoYearsAgo}", ago.isoformat()).replace("{today}", now.isoformat())

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

def rd_new_to_wgs84(x, y):
    """RD New (EPSG:28992) → WGS84, the Kadaster approximation rdNewToWGS84 uses (1–2 m)."""
    dx, dy = (x - 155_000) * 1e-5, (y - 463_000) * 1e-5
    lat = 52.15517440 + (3235.65389 * dy - 32.58297 * dx**2 - 0.24750 * dy**2 - 0.84978 * dx**2 * dy
                         - 0.06550 * dy**3 - 0.01709 * dx**2 * dy**2 - 0.00738 * dx - 0.00530 * dx**4
                         + 0.00039 * dx**2 * dy**3 - 0.00033 * dx**4 * dy + 0.00012 * dx * dy - 0.00034 * dx**2) / 3600
    lon = 5.38720621 + (5260.52916 * dx + 105.94684 * dx * dy + 2.45656 * dx * dy**2 - 0.81885 * dx**3
                        + 0.05594 * dx * dy**3 - 0.05607 * dx**3 * dy + 0.01199 * dy - 0.00256 * dx**3 * dy**2
                        + 0.00128 * dx * dy**4 + 0.00022 * dy**2 - 0.00022 * dx**2 + 0.00026 * dx**5) / 3600
    return lat, lon

def utm_to_wgs84(easting, northing, zone=30):
    """ETRS89 / UTM north (Madrid 30, Oslo 32) → WGS84, Snyder's inverse transverse Mercator, as utmNorthToWGS84."""
    a, f, k0 = 6_378_137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f); ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    x, m = easting - 500_000, northing / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu) + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
            + (151 * e1**3 / 96) * math.sin(6 * mu) + (1097 * e1**4 / 512) * math.sin(8 * mu))
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1, t1 = ep2 * cos1**2, tan1**2
    n1 = a / math.sqrt(1 - e2 * sin1**2)
    r1 = a * (1 - e2) / (1 - e2 * sin1**2) ** 1.5
    d = x / (n1 * k0)
    phi = phi1 - (n1 * tan1 / r1) * (d**2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
                                    + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720)
    lon = math.radians(zone * 6 - 183) + (d - (1 + 2 * t1 + c1) * d**3 / 6
                              + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120) / cos1
    return math.degrees(phi), math.degrees(lon)

def wgs84_to_utm(lat, lon, zone):
    """WGS84 → UTM north (easting, northing), Snyder's forward transverse Mercator: which CityGML tiles a box needs."""
    a, f, k0 = 6_378_137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f); ep2 = e2 / (1 - e2)
    phi = math.radians(lat)
    n = a / math.sqrt(1 - e2 * math.sin(phi)**2)
    t, c, A = math.tan(phi)**2, ep2 * math.cos(phi)**2, math.radians(lon - (zone * 6 - 183)) * math.cos(phi)
    m = a * ((1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * phi - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * phi)
             + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * phi) - (35 * e2**3 / 3072) * math.sin(6 * phi))
    x = 500_000 + k0 * n * (A + (1 - t + c) * A**3 / 6 + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * A**5 / 120)
    y = k0 * (m + n * math.tan(phi) * (A**2 / 2 + (5 - t + 9 * c + 4 * c**2) * A**4 / 24
                                       + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * A**6 / 720))
    return x, y

def mercator_to_wgs84(x, y):
    """Web Mercator (EPSG:3857) → (lat, lon)."""
    r = 6_378_137.0
    return math.degrees(2 * math.atan(math.exp(y / r)) - math.pi / 2), math.degrees(x / r)

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

def csv_all(c):
    """A whole CSV (La Rochelle): comma-separated, quoted, a BOM; the point a "lat,lon" column, the kind a
    column kept only for the values in `keep`, those in `covered` written TERRASSE FERMEE."""
    text = get(f"https://{c['host']}/{c['dataset']}").decode("utf-8-sig")
    kind, out = c["kinds"][0], []
    for r in csv.DictReader(io.StringIO(text)):
        if r[kind] not in c["keep"]: continue
        lat, _, lon = (r[c["point"]] or "").partition(",")
        lat, lon = number(lat.strip()), number(lon.strip())
        if lat is None or lon is None: continue
        item = {"kind": "TERRASSE FERMEE" if r[kind] in c.get("covered", []) else r[kind],
                "coordinate": {"latitude": lat, "longitude": lon}}
        if r[c["name"]]: item["name"] = r[c["name"]]
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

def folded(text):
    """Lower case without diacritics: "Ärendekategori" → "arendekategori"."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).lower()

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
    """A feed's terraces in the rect; a `match="venues"` row keeps only those a venue of today stands under."""
    items = fetched_permits(c, rect)
    return surviving(items, c["city"]) if c.get("match") == "venues" else items

def fetched_permits(c, rect):
    s, w, n, e = rect
    if c.get("shape") == "oslo": return oslo(c, rect)
    if c.get("shape") == "stockholm": return stockholm(c, rect)
    if c.get("shape") == "amsterdam": return amsterdam(c, rect)
    if c.get("shape") == "madrid": return whole_file(c, rect, madrid_all)
    if c.get("shape") == "seville": return whole_file(c, rect, seville_all)
    if c.get("shape") == "barcelona": return whole_file(c, rect, barcelona_all)
    if c.get("shape") == "csv": return whole_file(c, rect, csv_all)
    if c.get("shape") == "gothenburg": return whole_file(c, rect, gothenburg_all)
    if c.get("shape") == "geojson":  # a static GeoJSON of points, fetched whole once per run (Toronto)
        return whole_file(c, rect, lambda c: permit_items(get_json(f"https://{c['host']}/{c['dataset']}").get("features") or [], c))
    if c.get("shape") == "socrata":
        where = f"within_box({c['point']}, {n}, {w}, {s}, {e})" + (f" AND {socrata_filter(c['filter'])}" if c.get("filter") else "")
        return permit_items(socrata(c["host"], c["dataset"], where, ",".join(f for f in [c["name"], c["point"]] if f)), c)
    if c.get("shape") == "wfs":
        # WFS 1.1.0: longitude first, and the
        # bbox needs its own trailing CRS or the server silently answers zero.
        params = {"service": "WFS", "version": "1.1.0", "request": "GetFeature",
                  "typeName": c["dataset"], "outputFormat": "json", "srsName": c.get("srs", "EPSG:4326")}
        if c.get("filter"):
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


# ---------------------------------------------------------------- communes

_communes = []  # [(bounds, commune, contour)] of every département loaded so far
_departements = set()

def commune(lat, lon):
    """INSEE communes from Etalab's contours, a département at a time: a point in a loaded contour
    costs nothing, else the geo API names its commune and the département's contours are loaded.
    IGN's apicarto (the same communes, Admin Express) when Etalab does not answer. [] is an answer:
    no commune."""
    for (s, w, n, e), found, contour in _communes:
        if s <= lat <= n and w <= lon <= e and contains(contour, lat, lon): return [found]
    q = urllib.parse.urlencode({"lat": lat, "lon": lon, "fields": "nom,code,codeDepartement", "format": "json"})
    try:  # once: apicarto is the retry
        answer = get_json("https://geo.api.gouv.fr/communes?" + q, waits=())
    except SourceError as first:
        try:
            q = urllib.parse.urlencode({"lat": lat, "lon": lon})
            root = get_json("https://apicarto.ign.fr/api/limites-administratives/commune?" + q)
        except SourceError as second:
            raise SourceError(f"{first}; {second}")
        return [{"nom": f["properties"]["nom_com"], "code": f["properties"]["insee_com"]} for f in root.get("features", [])]
    for c in answer:
        if c.get("codeDepartement") and c["codeDepartement"] not in _departements:
            _departements.add(c["codeDepartement"])
            q = urllib.parse.urlencode({"fields": "nom,code,contour", "format": "geojson", "geometry": "contour"})
            try: features = get_json(f"https://geo.api.gouv.fr/departements/{c['codeDepartement']}/communes?" + q)["features"]
            except SourceError as e: log(f"    communes of {c['codeDepartement']}: {e}; point by point"); continue
            _communes.extend((bounds(f["geometry"]), {"nom": f["properties"]["nom"], "code": f["properties"]["code"]}, f["geometry"])
                             for f in features if f.get("geometry"))
    return [{"nom": c["nom"], "code": c["code"]} for c in answer]


# ---------------------------------------------------------------- the run

LAYERS = ("communes", "buildings", "terraces-v2", "venues", "streets", "streets-main", "places")
STREET_LAYERS = ("streets", "streets-main", "places")   # built only where an entry says "streets": true
# Days before a finished tile is cut again. Resuming skips a cell only while
# its file is younger than this, so the weekly run does refresh permits
# (they lapse) while a same-day re-run after a
# failure still skips what is done. Buildings and streets change yearly, communes never.
MAX_AGE_DAYS = {"communes": None, "buildings": 360, "buildings-v2": 360, "terraces-v2": 6, "venues": 6, "streets": 360, "streets-main": 360, "places": 360}

def path(out, layer, cell): return os.path.join(out, layer, cell.key + ".json")

def read(file):
    try:
        with open(file) as f: items = json.load(f)
        return items if isinstance(items, list) else None
    except (OSError, ValueError):
        return None

def done(out, layer, cell):
    """A tile to keep: it parses, and is younger than its layer's MAX_AGE_DAYS."""
    file, limit = path(out, layer, cell), MAX_AGE_DAYS[layer]
    if read(file) is None: return False
    return limit is None or time.time() - os.path.getmtime(file) < limit * 86_400

LISTED = None   # --listing: the store's keys, so the blocks it lacks are built first

def in_store(cell, layers):
    """Every tile this run writes for the cell is already in the store (False without --listing). A run cut
    by the job's time limit (London, 2026-10-07: 37,562 of 51,466 cells in 290 min) then starts its rerun
    with the cells it did not reach, instead of the same ones again."""
    tiled = [{"buildings": "buildings-v2"}.get(l, l) for l in layers if l in ("buildings", "streets", "terraces-v2")]
    return LISTED is not None and all(f"tiles/{l}/{cell.key}.json" in LISTED for l in tiled)

def build_order(cells, block, layers):
    """The blocks, those with a tile missing from the store first, each part in key order."""
    return sorted(blocks(cells, block), key=lambda g: all(in_store(c, layers) for c in g))

def write(file, items):
    os.makedirs(os.path.dirname(file), exist_ok=True)
    with open(file + ".tmp", "w") as f: json.dump(items, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(file + ".tmp", file)

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

def building_source(cell):
    city = next((b for b in CITY_BUILDINGS if in_feed(b, cell.lat, cell.lon)), None)
    return city["city"] if city else "IGN" if in_france(cell.lat, cell.lon) else "OSM"

FETCH_BUILDINGS = dict({b["city"]: (lambda r, b=b: combined(osm_buildings(r, True), b["fetch"](r), b["combine"] == "metres"))
                        if b["combine"] else b["fetch"]
                        for b in CITY_BUILDINGS}, IGN=ign_buildings, OSM=osm_buildings)
COMBINE = {b["city"] for b in CITY_BUILDINGS if b["combine"]}

def next_source(source, cell):
    """A city feed or IGN answering nothing for a cell hands it on; an error does not."""
    if source == "IGN": return "OSM"
    if source == "OSM": return None
    return "IGN" if in_france(cell.lat, cell.lon) else "OSM"

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
            if tiles: write(path(out, "buildings", c), items)
            if tiles: write(path(out, "buildings-v2", c), compact_buildings(items, c.key))
            note_sources(out, c, published, buildings=name)

STORE = None           # --store: the published tiles' base URL, read for the sources/ fields a run does not build
PUBLIC_STORE = "https://tiles.alephb.uk/tiles/"   # where a venue match reads from when no --store is given
STORE_THREADS = 16     # the store is a CDN: no spacing, a few requests in flight
_bust = str(int(time.time()))   # one query string per run: the CDN serves the tile as published, not as cached

def published_items(layer, key, bust=False):
    """A published tile's items (a list; [] when there is none, 404), or a SourceError. `bust` asks past the CDN's cache."""
    url = f"{STORE or PUBLIC_STORE}{layer}/{key}.json" + (f"?v={_bust}" if bust else "")
    error = "?"
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA})  # the store refuses urllib's own
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response: body = response.read()
            if body[:2] == b"\x1f\x8b": body = gzip.decompress(body)  # gzipped at rest, inflated by the CDN or not
            items = json.loads(body)
            return items if isinstance(items, list) else []
        except urllib.error.HTTPError as e:
            if e.code == 404: return []
            error = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001 — resets, timeouts, a truncated body: asked again
            error = f"{type(e).__name__}: {e}"[:200]
        time.sleep(2 * (attempt + 1))
    return SourceError(f"store: {error}")

def published_source(cell):
    """The published sources/ entry of a cell: a dict ({} when there is none, 404), or a SourceError."""
    items = published_items("sources", cell.key)
    if isinstance(items, SourceError): return items
    return items[0] if items and isinstance(items[0], dict) else {}

def published_sources(out, cells):
    """{key: published entry or SourceError} for the cells with no sources/ tile in `out` yet
    (a tile there already holds them); {} without a store."""
    todo = [c for c in cells if STORE and read(path(out, "sources", c)) is None]
    if not todo: return {}
    with concurrent.futures.ThreadPoolExecutor(STORE_THREADS) as pool:
        return dict(zip((c.key for c in todo), pool.map(published_source, todo)))

def note_sources(out, cell, published=None, **fields):
    """sources/<key>.json, one object in an array like every layer: which permit feed
    (a PERMIT_CITIES city, or null) and which building source (a CITY_BUILDINGS city,
    "IGN", "OSM", or "<city>+OSM" for a combine row's cell the city gave heights to) built the cell — what the app credits. Each field is written with
    its own layer, over the tile already in `out`, else over the published one (`published`,
    from published_sources), so a run building one layer keeps the other's field. A cell
    whose published tile could not be read gets no tile: the upload leaves the published one."""
    file = path(out, "sources", cell)
    local = read(file)
    base = (published or {}).get(cell.key, {}) if local is None else local[0]
    if isinstance(base, SourceError):
        log(f"    {cell.key}: sources/ not written, the published tile was not read ({base})"); return
    write(file, [dict(base, **fields)])

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

def do_sources(cells, out, failures):
    """The `sources` layer: the published sources/ tiles that lack a building source get it, found as
    the buildings layer finds it (the city feed, IGN, then OSM), its tiles not written. A cell without
    a published tile was never built and is left alone; one whose tile already names its source too."""
    if not STORE:
        failures += [(c.key, "sources", "no --store to read the published tiles from") for c in cells]; return
    published = published_sources(out, cells)
    for c in cells:
        if isinstance(published.get(c.key), SourceError): failures.append((c.key, "sources", str(published[c.key])))
    need = [c for c in cells if isinstance(published.get(c.key), dict) and published[c.key] and "buildings" not in published[c.key]]
    if need: do_buildings(need, out, failures, published, tiles=False)

def area(entry, half_km):
    """An entry's box: its own `box` [s, w, n, e], else `half_km` around its point."""
    if entry.get("box"): return tuple(entry["box"])
    lat, lon, half_km = entry["lat"], entry["lon"], entry.get("half_km", half_km)
    dlat, dlon = half_km / 111.32, half_km / (111.32 * math.cos(math.radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon

def clip(cells, boundary):
    """The cells whose centre falls inside a GeoJSON (Multi)Polygon."""
    return [c for c in cells if contains(boundary, c.lat, c.lon)]

def run(label, rect, out, block, layers, extracts, departements=None, boundary=None):
    global _osm
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
        _osm = OSM(download(pbf, extracts), (min(r[0] for r in reach), min(r[1] for r in reach),
                                             max(r[2] for r in reach), max(r[3] for r in reach)))
    except SourceError as e:
        log(f"{label}: OpenStreetMap unavailable ({e})")
        return len(cells), [(c.key, "all", str(e)) for c in cells]
    # A French extract means French communes; elsewhere the geo API would answer [] a cell at a time.
    french = "/europe/france" in pbf
    sources_only = set(layers) <= {"sources", "venues", "streets-main", "places"}
    if "venues" in layers:
        for c in coarse:
            if not done(out, "venues", c): write(path(out, "venues", c), osm_venues(c))
    if "streets-main" in layers: do_main_streets([c for c in wide if not done(out, "streets-main", c)], out, failures)
    if "places" in layers: do_places([c for c in wide if not done(out, "places", c)], out, failures)
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
            if "buildings" in layers and not (done(out, "buildings", c) and done(out, "buildings-v2", c)):
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
    failed = {k for k, _, _ in failures}
    log(f"{label}: {len(cells) - len(outside) - len(failed)} of {len(cells) - len(outside)} cells complete ({skipped} already there), "
        f"{len(failed)} failed, {time.monotonic() - started:.0f} s")
    return len(cells) - len(outside), failures

def entry_layers(entry, layers):
    """The layers built for one area: streets/, streets-main/ and places/ only where its entry says
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
    a.add_argument("--extracts", default="extracts", help="where Geofabrik extracts are kept between runs (default ./extracts)")
    args = a.parse_args()
    global STORE, LISTED
    STORE = args.store and args.store.rstrip("/") + "/"
    if args.listing and os.path.exists(args.listing):
        with open(args.listing) as f: LISTED = {line.split("\t")[0] for line in f}
    if args.cities:
        with open(args.cities) as f: entries = json.load(f)
    elif args.city and args.lat is not None and args.lon is not None:
        entries = [{"city": args.city, "lat": args.lat, "lon": args.lon, "half_km": args.half_km}]
    else:
        a.error("give --cities, or --city with --lat and --lon")
    total, failures = 0, []
    for entry in entries:
        label = entry["city"] + (f" ({entry['district']})" if entry.get("district") else "")
        boundary = None
        if entry.get("boundary"):
            with open(os.path.join(os.path.dirname(os.path.abspath(args.cities)), entry["boundary"])) as f: boundary = json.load(f)
            boundary = boundary.get("geometry", boundary)
        layers = entry_layers(entry, args.layers.split(","))
        if not layers:
            log(f"{label}: no streets/ for this area (not flagged), nothing else asked"); continue
        n, f = run(label, area(entry, args.half_km), args.out, args.block, layers, args.extracts,
                   entry.get("departements"), boundary)
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
