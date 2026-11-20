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
import argparse, array, collections, concurrent.futures, csv, datetime, gzip, io, json, math, os, re, ssl, struct, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, xml.etree.ElementTree as ET, zipfile, zoneinfo
from builder.cells import _overlaps, area, blocks, boundary, bounds, cells_in, clip, COARSE, contains, in_box, in_feed, in_france, index, M, MAIN, metres, padded, union
from builder.net import download, get, get_json, keyed_host, log, redact, SECRET_ENVS, SourceError, TIMEOUT, UA
from builder.projections import bng, cs92_to_wgs84, florida_east_to_wgs84, GK25, lambert72_to_wgs84, lcc3301_to_wgs84, mercator_to_wgs84, rd_new_to_wgs84, tm_to_wgs84, utm_to_wgs84, wgs84_to_lambert72, wgs84_to_tm, wgs84_to_utm
from builder.shapes import building, combined, compact_buildings, difference, estimated, footprints, number, osm_height, osm_tagged, outer_rings, triangles_outline, unique, vertex
from builder.formats import _atom_entries, _zip_members, geopackage, geotiff, mesh_faces, shapefile, xlsx_rows, zip_layer
from builder.names import folded, local_names, MATCH_REACH, names_match, normalised_name
from builder.streets import associated_streets, house_numbers, interpolation, MAIN_STREETS, MAIN_TOLERANCE, MAIN_UNIT, NUMBER_PAD, number_rows, simplified, street_kind, STREET_KINDS, STREET_PAD, street_tiles, STREET_TOLERANCE
from builder.transit import transit_lines, TRANSIT_MODES, transit_tiles
from builder.osmium import buckets, features, geofabrik_pbf, mean_point, osmium
from builder.places import distinct_places, english, place_key, PLACE_KINDS, place_levels

# The scripts and tests that import names from make_tiles still find them here while they move to builder/
# (rebound state excepted: builder.store.STORE, builder.store.LISTED, builder.extract._osm).
import builder.cells, builder.net, builder.projections, builder.shapes, builder.formats, builder.names, builder.streets, builder.transit, builder.osmium, builder.places
for _m in [builder.cells, builder.net, builder.projections, builder.shapes, builder.formats, builder.names, builder.streets, builder.transit, builder.osmium, builder.places]:
    globals().update((k, v) for k, v in vars(_m).items() if not k.startswith("__") and k not in builder.REBOUND)

TERRACE_RADIUS = 400          # metres around the cell's centre: the radius the density gate is calibrated on
# Every footprint whose box comes within this of the cell: the app's reach
# plus a margin (its docs; Opéra, 2026-09-24: 690 KB → 280 KB a cell against
# a 400 m reach). Raise it if that reach grows.
BUILDING_REACH = 200
BUILDING_PAD = 250            # fetched around a block: some feeds select by a footprint's centre, not its outline

def row(name, insee, host, dataset, name_field, kinds, lat, lon, boundary=None, **extra):
    if extra.get("key"): SECRET_ENVS.append(extra["key"])
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
        clean="digit", boundary=boundary("camden"), part_of="London"),
    row("New York", "", "data.cityofnewyork.us", "fpeh-f7ci", "assumed_name_s", [], (40.49, 40.92), (-74.26, -73.68),
        shape="socrata", point="location", filter="license_expiration_date >= '{today}'"),
    row("Chicago", "", "data.cityofchicago.org", "nxj5-ix6z", "doing_business_as_name", [], (41.64, 42.02), (-87.94, -87.52),
        shape="socrata", point="location", filter="expiration_date >= '{today}'"),
    row("San Francisco", "", "data.sf.gov", "dpch-7nr4", "dbaname", [], (37.70, 37.83), (-122.52, -122.35),
        shape="socrata", point="point", filter="tablesandchairs = true"),
    # DSO REST, HAL JSON, points in RD New: a terrace is a licence whose terrasgeometrie is not null. The box is the
    # municipality's (cities.json, whole since 2026-10-11, Weesp included): the city's own register, so city-wide.
    row("Amsterdam", "", "api.data.amsterdam.nl", "horeca/exploitatievergunning", "zaaknaam", [], (52.2779, 52.4311), (4.7287, 5.1077),
        shape="amsterdam"),
    # WFS: NAME is the placement, not the café, so no name field — door rule only. Read whole once per run (2,993
    # points, ~100 KB gzipped, 1.3 s, 2026-10-11): asked per block, then cell by cell after its timeouts, the
    # whole municipality (12,650 cells) reached 2,034 in the 290-minute cap (run 38129769421).
    row("Vienna", "", "data.wien.gv.at/daten/geo", "ogdwien:SCHANIGARTENOGD", "", [], (48.10, 48.35), (16.15, 16.58),
        shape="wfs", whole=True),
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
    # Waltham Forest and Redbridge (London): the granted pavement licences each publishes as a web page (no licence
    # stated: credited to the borough), geocoded by permits/geocode_london.py as Edinburgh's are; Waltham Forest's page
    # has no dates, Redbridge's rows are kept while unexpired on the snapshot's day. Each held to its borough (ONS Local
    # Authority Districts December 2025, OGL), as Camden is: the two boxes overlap.
    row("Waltham Forest", "", "raw.githubusercontent.com", "Techneb/compromise-tiles/master/permits/waltham-forest.geojson",
        "name", [], (51.55, 51.65), (-0.07, 0.03), shape="geojson", boundary=boundary("waltham-forest"), part_of="London"),
    row("Redbridge", "", "raw.githubusercontent.com", "Techneb/compromise-tiles/master/permits/redbridge.geojson",
        "name", [], (51.53, 51.63), (0.00, 0.15), shape="geojson", boundary=boundary("redbridge"), part_of="London"),
    # Southwark (London): the council's "sf/control" public register (no licence stated: credited to the borough), read
    # live once per run; approved pavement licences unexpired on the build's day, at the register's own coordinates.
    row("Southwark", "", "licencesandservices.southwark.gov.uk", "", "", [], (51.42, 51.51), (-0.12, -0.03),
        shape="southwark", boundary=boundary("southwark"), part_of="London"),
    # Tower Hamlets, Merton, Richmond upon Thames and Westminster (London): granted pavement licences with addresses
    # only (no licence stated: each credited to its borough), geocoded once by permits/geocode_london.py, points only
    # (no name, no address kept). Each held to its borough (ONS Local Authority Districts December 2025, OGL).
    row("Tower Hamlets", "", "raw.githubusercontent.com", "Techneb/compromise-tiles/master/permits/tower-hamlets.geojson",
        "", [], (51.48, 51.55), (-0.08, 0.01), shape="geojson", boundary=boundary("tower-hamlets"), part_of="London"),
    row("Merton", "", "raw.githubusercontent.com", "Techneb/compromise-tiles/master/permits/merton.geojson",
        "", [], (51.38, 51.45), (-0.26, -0.12), shape="geojson", boundary=boundary("merton"), part_of="London"),
    row("Richmond upon Thames", "", "raw.githubusercontent.com", "Techneb/compromise-tiles/master/permits/richmond-upon-thames.geojson",
        "", [], (51.39, 51.49), (-0.40, -0.22), shape="geojson", boundary=boundary("richmond-upon-thames"), part_of="London"),
    row("Westminster", "", "raw.githubusercontent.com", "Techneb/compromise-tiles/master/permits/westminster.geojson",
        "", [], (51.48, 51.54), (-0.22, -0.11), shape="geojson", boundary=boundary("westminster"), part_of="London"),
    # Harrow (London): the council's NEC register, current pavement licences, read in Chrome (it turns a script away)
    # and geocoded the same way, points only: a dated snapshot that only a refresh by hand updates. Held to the borough.
    row("Harrow", "", "raw.githubusercontent.com", "Techneb/compromise-tiles/master/permits/harrow.geojson",
        "", [], (51.55, 51.65), (-0.41, -0.26), shape="geojson", boundary=boundary("harrow"), part_of="London"),
    # CKAN (Open Government Licence – Toronto): CaféTO's static GeoJSON, monthly, fetched whole once per run;
    # sidewalk, curb-lane and private patios are all open air. Points come as one-point MultiPoints. Held to the
    # City's own municipal boundary (Open Government Licence – Toronto): the box reaches Vaughan's tiled cells.
    row("Toronto", "", "ckan0.cf.opendata.inter.prod-toronto.ca",
        "dataset/3b605a2e-f3bf-4b2b-b972-c0829b2788f5/resource/aa839e97-df7f-4c65-8aba-d336bb3c8f06/download/"
        "cafe-to-locations-4326.geojson", "OPERATOR_NAME", [], (43.58, 43.86), (-79.64, -79.11),
        shape="geojson", clean="toronto", boundary=boundary("toronto")),
    # ArcGIS MapServer (GEO RĪGA, no licence stated): layer 16 is the permits in force; the name is the
    # holder's company, less its legal form, else the venue's at the permit's spot (match="trading"). The OID field is gid.
    row("Riga", "", "georiga.lv/server/rest/services", "Ara_kafejnicas_terases/MapServer/16", "nosaukums", [],
        (56.85, 57.09), (23.93, 24.33), shape="arcgis", oid="gid", clean="riga", match="trading"),
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
        filter="ExpiryDate >= CURRENT_TIMESTAMP", boundary=boundary("kensington-and-chelsea"), part_of="London"),
    # Los Angeles: DataLA's L.A. Al Fresco dining locations (ArcGIS Online, no licence stated), a January 2021 snapshot of
    # COVID-era authorisations — 2,273 named points, no status or expiry, both types (sidewalk and private property,
    # on-street) open air. Many of those venues are gone, so a permit is kept only where a venue of today stands under its
    # name (`match="venues"`: the published venues/ tiles, OpenStreetMap's, within MATCH_REACH; owner, 2026-10-05). The box
    # is the tiled area's (cities.json), widened on 2026-10-11 with it to the whole city, which the layer covers.
    row("Los Angeles", "", "services5.arcgis.com/7nsPwEMP38bSkCjy/arcgis/rest/services", "Al_Fresco_Dining_Locations/FeatureServer/0",
        "Business_Name", [], (33.7047, 34.3374), (-118.6682, -118.1553), shape="arcgis", match="venues"),
    # Stockholm: Trafikkontoret's markupplåtelser (public-ground leases, CC0 1.0 per the layer's metadata), a WFS
    # behind a free API key: `key` names the environment variable holding it (an Actions secret), which goes into
    # the URL path at fetch time and never into a log; without it the row is left out of the run, logged. Only
    # the uteservering kind of ärendekategori is kept, and only unexpired leases; the attribute names are matched
    # loosely (no sample without the key, 2026-10-05) and the first answer's field names are logged. No name
    # field until that sample shows one, so the door rule. The box is the tiled area's (cities.json), widened on
    # 2026-10-11 with it to the whole municipality, which the WFS covers; no other city's cells fall in it, so no boundary.
    row("Stockholm", "", "openstreetgs.stockholm.se/geoservice/api/{key}/wfs", "od_gis:Markupplatelse_Punkt", "", [],
        (59.2296, 59.4403), (17.7696, 18.2001), shape="stockholm", key="STOCKHOLM_API_KEY"),
    # Hong Kong: FEHD's restaurant licences (the register of data.gov.hk's LP_Restaurants XML, daily) as CSDI serves
    # them, a keyless FeatureServer with each licence's point, so no geocoding (DATA.GOV.HK / CSDI terms: free reuse
    # with the source named). Only licences flagged #A, "Licensed restaurant with outside seating accommodation" (501 of
    # 17,294, 2026-10-11), in NSEARCH04 (the XML's INFO). The name is the shop sign, English or Chinese as registered.
    # The box is the terraces'. The city's cities.json area (2026-10-11) is the whole SAR's land (the Census and Statistics
    # Department's 2021 District Council districts, dissolved); no other tiled city reaches the box, so no boundary.
    # San Diego (2026-10-21): the City's Development Services approvals of active projects (data.sandiego.gov
    # "Development Permits", one CSV of every approval, ODC-PDDL on the dataset's page; daily), fetched whole once per
    # run (~270 MB): the Sidewalk Cafe Agreement and Permit approvals that read Issued (167 of 263 on 2026-10-21, at
    # 212 points, 42 in the Gaslamp Quarter). No venue name (APPROVAL_PERMIT_HOLDER names people and is never read), so
    # the door rule; the expiry is the approval's, not the agreement's. The Spaces as Places layer (streetaries,
    # DSDPermits MapServer/3 on webmaps.sandiego.gov) is fresher but its host refuses the Mac. The box is the cafés',
    # held to the city (OpenStreetMap's boundary, ODbL), which keeps National City off it.
    row("San Diego", "", "seshat.datasd.org", "development_permits/approvals_active_datasd.csv", "", ["APPROVAL_TYPE"],
        (32.69, 32.97), (-117.28, -117.10), shape="csv", point=("GIS_LATITUDE", "GIS_LONGITUDE"),
        keep=["Sidewalk Cafe Agreement", "Sidewalk Cafe Permit"], where={"APPROVAL_STATUS": ["Issued"]}, as_kind="TERRASSE",
        boundary=boundary("san-diego")),
    # Québec (2026-10-21): the City's issued permits (Données Québec, "Permis délivrés à la Ville de Québec",
    # Attribution (CC-BY 4.0), refreshed weekly), one CSV read whole: the café-terrasse kinds (216 rows, 2020-2026),
    # LATITUDE/LONGITUDE, no venue name (the address is never read). The box is the terraces', held to the city.
    row("Québec", "", "www.donneesquebec.ca",
        "recherche/dataset/879abf6e-c6b2-430a-b44a-16335467c6f6/resource/9555031e-cfc5-4b78-bec9-4ab84b549f67/download/vdq-permis.csv",
        "", ["RAISON"], (46.73, 46.96), (-71.52, -71.15), shape="csv", point=("LATITUDE", "LONGITUDE"),
        keep=["Café-terrasse", "Café-terrasse accessoire à un restaurant ou un débit d'alcool",
              "Café-terrasse accessoire à un restaurant, un débit d'alcool ou une aire de dégustation",
              "Aménagement d'un café-terrasse en cour latérale ou arrière"], as_kind="TERRASSE", boundary=boundary("quebec")),
    # Tucson: the City's Patio Expansions (ArcGIS Online, no licence stated), the 2020-21 programme's implemented patios,
    # 35 named points, frozen since 2022: kept only where a venue of today stands under the name (match="venues", as
    # Los Angeles). The box is the city's.
    row("Tucson", "", "services3.arcgis.com/9coHY2fvuFjG9HQX/arcgis/rest/services", "Patio_Expansions_view/FeatureServer/0",
        "Name", [], (31.99, 32.33), (-111.06, -110.70), shape="arcgis", match="venues", filter="Implemented = 'Yes'"),
    # The overnight run's US permit rows (2026-10-21), no licence stated on any (credited to the publisher). None names
    # the venue in a field of its own, so the door rule, except Long Beach's parklets (Business). Pittsburgh: DOMI's
    # outdoor dining permits, issued (the service is republished under a new name every few months: the weekly data
    # check flags it). Long Beach: deployed parklets. Detroit: the right-of-way encroachments of a café, patio or
    # outdoor-seating kind, each polygon at its mean vertex (Petitioner is never read: it can name a person).
    row("Pittsburgh", "", "services1.arcgis.com/YZCmUqbcsUpOKfj7/arcgis/rest/services", "Outdoor_Dining_August_2026/FeatureServer/8",
        "", [], (40.36, 40.51), (-80.10, -79.86), shape="arcgis", filter="Statusdescription = 'Issued'", boundary=boundary("pittsburgh")),
    row("Long Beach", "", "services6.arcgis.com/yCArG7wGXGyWLqav/arcgis/rest/services", "Temporary_Parklets_Edit/FeatureServer/0",
        "Business", [], (33.69, 33.89), (-118.25, -118.06), shape="arcgis", filter="Status = 'Deployed'", boundary=boundary("long-beach")),
    row("Detroit", "", "services2.arcgis.com/qvkbeam7Wirps6zC/arcgis/rest/services", "Encroachments_view_only/FeatureServer/0",
        "", [], (42.25, 42.46), (-83.29, -82.91), shape="arcgis", oid="FID", boundary=boundary("detroit"),
        filter="LOWER(ORIG_TYPE) LIKE '%cafe%' OR LOWER(ORIG_TYPE) LIKE '%patio%' OR LOWER(ORIG_TYPE) LIKE '%outdoor%'"),
    # Step 4's US rows (2026-11-04), no licence stated on Baltimore's or Orlando's (credited to the publisher).
    # Baltimore: CitiStat's 2020 Minor Privilege outdoor seating and café accounts (ArcGIS Online, 237 points, never
    # refreshed), no venue name (the door rule; a permit no venue stands at hands nothing on). Orlando: the City's
    # right-of-way use permits (Socrata, daily), the sidewalk-café applications whose milestone reads Active or
    # Waiting for Renewal (18 of 100, 2026-11-04), named by project_name; gpsx/gpsy are Florida East State Plane feet.
    # Only those four columns are asked: the dataset also holds contacts' names, phones and e-mails. Houston: the
    # Health Department's approved dog patios (ODC-BY), a 2023 list geocoded once (permits/geocode_houston.py), kept
    # only where a venue of today stands under the name. Each held to its OSM boundary; the box is the city's.
    row("Baltimore", "", "services1.arcgis.com/UWYHeuuJISiGmgXx/arcgis/rest/services",
        "2020_Minor_Privilege_accounts_outdoor_seating/FeatureServer/0", "", [], (39.1972, 39.372), (-76.7113, -76.5297),
        shape="arcgis", oid="ObjectId", boundary=boundary("baltimore")),
    row("Orlando", "", "data.cityoforlando.net",
        "resource/6sbc-v4b9.csv?$select=project_name,application_milestone,gpsx,gpsy"
        "&$where=application_type%3D%27Sidewalk%20Cafe%20Application%27", "project_name", ["application_milestone"],
        (28.348, 28.6143), (-81.5076, -81.1241), shape="csv", point=("gpsy", "gpsx"),
        grid=lambda e, n: florida_east_to_wgs84(e, n), keep=["Active", "Waiting for Renewal"], as_kind="TERRASSE",
        clean="formerly", boundary=boundary("orlando")),
    row("Houston", "", "raw.githubusercontent.com", "Techneb/compromise-tiles/master/permits/houston.geojson",
        "name", [], (29.547, 30.1104), (-95.9098, -95.012), shape="geojson", match="venues", boundary=boundary("houston")),
    row("Hong Kong", "", "portal.csdi.gov.hk/server/rest/services", "common/fehd_rcd_1630036390312_58893/FeatureServer/0",
        "NSEARCH03_EN", [], (22.20, 22.52), (113.93, 114.28), shape="arcgis", filter="NSEARCH04_EN LIKE '%#A%'"),
]
AMENITIES = ("bar", "pub", "biergarten", "cafe", "restaurant")  # every venue type
# In venues/ tiles only, for the app to match an Apple venue to its OSM twin and read its tags; never listed as a spot (2026-10-24).
MATCH_ONLY = ("fast_food", "ice_cream", "nightclub")

LIFECYCLE = ("disused", "abandoned", "was", "demolished", "removed", "razed", "destroyed")  # OSM's lifecycle prefixes

def closed(tags):
    """A venue OSM says is shut (owner, 2026-11-19, the Athens audit; left out of venues/): opening_hours off or closed,
    disused or abandoned yes, or a lifecycle prefix on its own amenity or its own name (disused:amenity=cafe beside
    amenity=cafe, was:name its name). A prefix on another amenity is the place's past, not a closure (amenity=cafe beside
    disused:amenity=restaurant: a restaurant turned café). A prefixed amenity alone never reaches here (tags-filter)."""
    if str(tags.get("opening_hours") or "").strip().lower() in ("off", "closed"): return True
    if "yes" in (tags.get("disused"), tags.get("abandoned")): return True
    own = {"amenity": tags.get("amenity"), "name": str(tags.get("name") or "").strip()}
    return any(own[key] and str(tags.get(f"{prefix}:{key}") or "").strip() == own[key] for prefix in LIFECYCLE for key in own)

def read_venues(pbf, kept, amenities=AMENITIES):
    """The venues (`amenities`) of a pbf, an area at its outer ring's mean vertex as the terrace permits; `kept` is a scratch file."""
    osmium("tags-filter", pbf, "nwr/amenity=" + ",".join(amenities), "-o", kept, "--overwrite")
    out = []
    for feature in features(kept, ids=True):
        tags, geometry = feature["properties"], feature["geometry"]
        if tags.get("amenity") not in amenities or closed(tags): continue
        if geometry["type"] == "Point": lon, lat = geometry["coordinates"][:2]
        else:
            ring = next(outer_rings(geometry), None)
            if not ring: continue
            lat, lon = (sum(v[k] for v in ring) / len(ring) for k in ("latitude", "longitude"))
        venue = {"name": str(tags.get("name", "")).strip(), "coordinate": {"latitude": round(lat, 6), "longitude": round(lon, 6)},
                 "amenity": tags["amenity"], "outdoor_seating": seating(tags.get("outdoor_seating"), rooftop(tags)), "source": "osm"}
        venue.update(local_names(tags))
        if drinks_late(tags): venue["bar"] = True
        if opening_hours(tags): venue["opening_hours"] = opening_hours(tags)
        venue.update(details(tags))
        if str(tags.get("brand:wikidata") or "").strip(): venue["_qid"] = str(tags["brand:wikidata"]).split(";")[0].strip()
        # The element, for the app's "Suggest an edit" to open its edit page (backlog, Keep OSM ids in venue tiles).
        if tags.get("@type") in ("node", "way", "relation") and tags.get("@id"): venue.update(osm_type=tags["@type"][0], osm_id=int(tags["@id"]))
        out.append(venue)
    return out

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

def opening_hours(tags):
    """OpenStreetMap's opening_hours as the tile carries it, raw for the app to parse (owner,
    2026-10-13): trimmed, None when absent, blank or over 255 characters (OSM's own tag limit)."""
    value = str(tags.get("opening_hours") or "").strip()
    return value if 0 < len(value) <= 255 else None

def details(tags):
    """The OSM tags behind a spot's subtitle (owner, 2026-10-20), short and left out when absent:
    cuisine as up to three lowercased ;-separated values, sport's first value, and True for
    microbrewery, live_music and cocktails tagged yes, drink:wine/drink:beer tagged yes or served, and
    diet:vegan, diet:vegetarian and diet:gluten_free as their value when yes or only. They also feed
    the app's keyword match (cocktail, pizza, vegan…). fine_dining and hotel feed the app's style labels (2026-11-17)."""
    out = {}
    cuisine = [v.strip().lower() for v in str(tags.get("cuisine") or "").split(";") if v.strip()][:3]
    if cuisine: out["cuisine"] = ";".join(cuisine)
    sport = next((v.strip().lower() for v in str(tags.get("sport") or "").split(";") if v.strip()), None)
    if sport: out["sport"] = sport
    for key in ("microbrewery", "live_music", "cocktails"):
        if tags.get(key) == "yes": out[key] = True
    for key in ("drink:wine", "drink:beer"):
        if tags.get(key) in ("yes", "served"): out[key] = True
    for key in ("diet:vegan", "diet:vegetarian", "diet:gluten_free"):
        if tags.get(key) in ("yes", "only"): out[key] = tags[key]
    # A venue's character (owner, 2026-10-21): True for real_ale, beer_garden, karaoke, brunch and live_sport (2026-10-22) tagged yes, real_fire
    # unless no, quiz whatever its value but no; food as yes/no; the brewery when it is one display name, not a tap
    # list or an id ("1664;leffe", "guinness", "local"; 2026-10-24);
    # internet_access as True for Wi-Fi; specialty_coffee from cuisine, coffee or coffee:specialty; the brand (chains only: OSM).
    for key in ("real_ale", "beer_garden", "karaoke", "brunch", "live_sport"):
        if tags.get(key) == "yes": out[key] = True
    for key in ("real_fire", "quiz"):
        if str(tags.get(key) or "no").strip().lower() != "no": out[key] = True
    if tags.get("food") in ("yes", "no"): out["food"] = tags["food"] == "yes"
    brewery = str(tags.get("brewery") or "").strip()
    if ";" not in brewery and brewery != brewery.lower() and brewery.lower() not in ("various", "multiple"): out["brewery"] = brewery
    if tags.get("internet_access") in ("wlan", "yes", "wifi"): out["internet_access"] = True
    every = {v.strip().lower() for v in str(tags.get("cuisine") or "").split(";")}
    if every & {"specialty_coffee", "speciality_coffee"} or tags.get("coffee") == "specialty" or tags.get("coffee:specialty") == "yes":
        out["specialty_coffee"] = True
    if str(tags.get("brand") or "").strip(): out["brand"] = str(tags["brand"]).strip()
    # Venue style (owner, 2026-11-17): fine_dining True for a starred restaurant, hotel 4 or 5 for a venue that is a 4–5★ hotel too.
    if fine_dining(tags): out["fine_dining"] = True
    if hotel_class(tags): out["hotel"] = hotel_class(tags)
    return out

def fine_dining(tags):
    """A restaurant OSM's mappers give one to three stars (`stars`, else `award:michelin`): their copy of a guide's, which the
    app shows only as "Fine dining", never by the guide's name (owner, 2026-11-17). Not on an element that is a hotel too, whose
    stars are the hotel's; "0", "Bib gourmand" and the rest are no star."""
    if tags.get("amenity") != "restaurant" or tags.get("tourism") == "hotel": return False
    return any(str(tags.get(key) or "").strip() in ("1", "2", "3") for key in ("stars", "award:michelin"))

def hotel_class(tags):
    """A tourism=hotel element's stars, 4 or 5 ("4S" too), else None: the hotels whose bars and restaurants the app labels."""
    stars = str(tags.get("stars") or "").strip().rstrip("Ss")
    return int(stars) if tags.get("tourism") == "hotel" and stars in ("4", "5") else None

def hotels(pbf, kept):
    """The 4–5★ hotels of a pbf drawn as areas, [(stars, geometry, bounds)]; `kept` is a scratch file."""
    osmium("tags-filter", pbf, "wr/tourism=hotel", "-o", kept, "--overwrite")
    return [(hotel_class(f["properties"]), f["geometry"], bounds(f["geometry"])) for f in features(kept, "polygon")
            if hotel_class(f["properties"]) and f["geometry"]["type"] in ("Polygon", "MultiPolygon")]

def in_hotels(venues, areas):
    """Sets `hotel` (the stars) on a venue inside one of `hotels()`' areas: a hotel's bar or restaurant (owner, 2026-11-17).
    A venue that is a hotel itself keeps its own."""
    index = {}
    for area in areas:
        for key in buckets(*area[2]): index.setdefault(key, []).append(area)
    for v in venues:
        lat, lon = v["coordinate"]["latitude"], v["coordinate"]["longitude"]
        stars = next((a[0] for a in index.get(buckets(lat, lon, lat, lon)[0], []) if a[2][0] <= lat <= a[2][2] and a[2][1] <= lon <= a[2][3]
                      and contains(a[1], lat, lon)), None)
        if stars and "hotel" not in v: v["hotel"] = stars
    return venues

_NSI = None
def nsi_brands():
    """nsi_brands.json's brands (nsi_table.py): {brand:wikidata: [brand, cuisine, amenity, folded names]}."""
    global _NSI
    if _NSI is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nsi_brands.json")) as f: _NSI = json.load(f)["brands"]
    return _NSI

def chains_only(venues, least=3, brands=None):
    """Names a venue's chain from the name-suggestion-index (S5, owner 2026-10-24): by its brand:wikidata, else by its name when a
    venue of the same extract carries that brand's wikidata (so a local "Paul" is no chain far from one), filling a missing brand
    and cuisine. Then drops the brand of a venue whose brand fewer than `least` of `venues` share, or whose name already says it,
    either holding the other ("GAIL's" for "GAIL's Bakery"): a brand on a row marks a chain, so an independent stands out by
    having none (owner, 2026-10-21)."""
    brands = nsi_brands() if brands is None else brands
    here = {v["_qid"] for v in venues if v.get("_qid") in brands}
    by_name = {}
    for qid in sorted(here):
        for name in brands[qid][3]: by_name.setdefault(name, qid)
    for v in venues:
        qid = v.pop("_qid", None)
        qid = qid if qid in brands else by_name.get(normalised_name(v["name"]))
        if not qid: continue
        brand, cuisine = brands[qid][:2]
        v.setdefault("brand", brand)
        if cuisine and "cuisine" not in v: v["cuisine"] = ";".join(cuisine.split(";")[:3])
    shared = collections.Counter(v.get("brand") for v in venues)
    for v in venues:
        if "brand" not in v: continue
        # chain stays where the brand goes for saying the name (owner, 2026-11-17): what "Hide chains" reads.
        if shared[v["brand"]] >= least: v["chain"] = True
        brand, name = normalised_name(v["brand"]), normalised_name(v["name"])
        if shared[v["brand"]] < least or brand in name or name in brand: del v["brand"]
    return venues

# Overture Places on the venues of Paris and London (owner, 2026-11-18: "yes for Overture's data extraction. We start with
# Paris and London"): its kind of bar, finer than OSM's amenity, as `style`; its brand's wikidata as `chain` where OSM
# names no brand; and a venue Overture holds permanently closed is left out. No Overture place becomes a venue. A place
# speaks for the one venue within MATCH_REACH whose name matches (names_match); with a second such venue it says nothing,
# and a venue two places disagree on (two kinds, closed and open) keeps OSM's alone. Overture: CDLA-Permissive 2.0; the
# sources behind these places are credited in the app under the stricter reading (README, Licences and credits).
OVERTURE_CITIES = ("Paris", "London")
OVERTURE_STYLES = {"cocktail_bar": "cocktail_bar", "wine_bar": "wine_bar", "speakeasy": "speakeasy", "hotel_bar": "hotel_bar",
                   "lounge": "lounge", "dive_bar": "dive_bar", "beer_bar": "beer_bar", "pub": "pub", "irish_pub": "pub",
                   "gastropub": "gastropub"}   # Overture's taxonomy.primary: the app's key
OVERTURE_BUCKET = "https://overturemaps-us-west-2.s3.amazonaws.com"
OVERTURE_CONFIDENCE = 0.6   # Overture's own confidence that an open place exists; below it the place says nothing
OVERTURE_CHAIN = 3          # places sharing a brand in the box for it to be a chain, as chains_only's least

def overture_release():
    """The newest Overture release on its public bucket ("2026-10-21.0"): releases are dropped after a few months."""
    listing = urllib.request.urlopen(OVERTURE_BUCKET + "/?list-type=2&prefix=release/&delimiter=/", timeout=60).read().decode()
    return max(re.findall(r"<Prefix>release/([0-9.-]+)/</Prefix>", listing))

def overture_places(rect):
    """Overture's eating and drinking places inside the rect, [{"name", "lat", "lon", "status", "style", "qid"}]: `status`
    Overture's operating_status, `style` OVERTURE_STYLES' key or None, `qid` the brand's wikidata or None."""
    import duckdb   # here, not at the top: the tests and every other city need no DuckDB
    s, w, n, e = rect
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    rows = con.execute(f"""SELECT names.primary, (bbox.ymin + bbox.ymax) / 2, (bbox.xmin + bbox.xmax) / 2,
            coalesce(operating_status, 'open'), taxonomy.primary, brand.wikidata, confidence
        FROM read_parquet('s3://overturemaps-us-west-2/release/{overture_release()}/theme=places/type=place/*', hive_partitioning=0)
        WHERE bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ? AND names.primary IS NOT NULL
          AND list_contains(taxonomy.hierarchy, 'food_and_drink')""", [w, e, s, n]).fetchall()
    return [overture_place(*row) for row in rows]

def overture_place(name, lat, lon, status, kind, qid, confidence):
    """One row of overture_places(): an open place below OVERTURE_CONFIDENCE gives no kind and no brand."""
    sure = status != "open" or (confidence or 0) >= OVERTURE_CONFIDENCE
    return {"name": name, "lat": lat, "lon": lon, "status": status, "style": OVERTURE_STYLES.get(kind) if sure else None,
            "qid": qid if sure else None}

def overture_facts(osm, places):
    """Writes Overture's say onto the venues of `osm` (its venues and matches tables): `style`, `chain` where OSM gives no
    brand and the brand has OVERTURE_CHAIN places in `places`, and drops the venues held permanently closed. Returns
    {fact: Counter(matched, ambiguous, unmatched)} over the places that say something, the facts "style:<key>", "chain",
    "closed" and "temporarily_closed" (counted only), chain's "added" the venues it made chains, and the dropped venues'
    names under "dropped"."""
    brands = collections.Counter(p["qid"] for p in places if p["qid"])
    claims, counts = {}, collections.defaultdict(collections.Counter)
    for p in places:   # every place, the open ones too: an open twin keeps a venue another place holds closed
        status = "closed" if p["status"] == "permanently_closed" else p["status"]
        says = [f for f in (("style:" + p["style"]) if p["style"] else None, "chain" if brands[p["qid"]] >= OVERTURE_CHAIN else None,
                            status if status != "open" else None) if f]
        wanted = normalised_name(p["name"])
        near = [v for table in (osm.venues, osm.matches) for v in osm.near(table, padded((p["lat"], p["lon"]) * 2, MATCH_REACH))
                if metres(p["lat"], p["lon"], v["coordinate"]["latitude"], v["coordinate"]["longitude"]) <= MATCH_REACH
                and names_match(normalised_name(v["name"]), wanted)]
        if len(near) != 1:
            for fact in says: counts[fact]["unmatched" if not near else "ambiguous"] += 1
            continue
        claims.setdefault(id(near[0]), (near[0], []))[1].append(says)
    dropped = set()
    for venue, said in claims.values():
        styles = {f for says in said for f in says if f.startswith("style:")}
        closed = sum("closed" in says for says in said)
        for says in said:
            for fact in says:
                torn = (fact.startswith("style:") and len(styles) > 1) or (fact == "closed" and closed < len(said))
                counts[fact]["ambiguous" if torn else "matched"] += 1
        if closed and closed == len(said):
            dropped.add(id(venue)); counts["dropped"][venue["name"]] += 1; continue
        style = next(iter(styles))[len("style:"):] if len(styles) == 1 else None
        if style and (style, venue["amenity"]) != ("pub", "pub"): venue["style"] = style   # a pub's "pub" says nothing new
        if any("chain" in says for says in said) and "brand" not in venue and not venue.get("chain"):
            venue["chain"] = True; counts["chain"]["added"] += 1
    for table in (osm.venues, osm.matches):
        for key in table: table[key] = [v for v in table[key] if id(v) not in dropped]
    return counts

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
    """The named venues of one ~2 km cell, MATCH_ONLY ones included, the key's own truncation deciding the edge."""
    return [v for table in (_osm.venues, _osm.matches) for v in _osm.near(table, cell.rect) if v["name"]
            and f"{index(v['coordinate']['latitude'], COARSE)},{index(v['coordinate']['longitude'], COARSE)}" == cell.key]

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

def socrata_buildings(host, dataset, geometry, field, height):
    def fetch(rect):
        return footprints(socrata(host, dataset, intersects(geometry, rect), f"{field},{geometry}"),
                          lambda p: height(number(p.get(field))))
    return fetch

CATASTRO_FEED = "https://www.catastro.hacienda.gob.es/INSPIRE/buildings/ES.SDGC.bu.atom.xml"
_catastro = {"provinces": None, "feeds": {}, "loaded": set(), "grid": {}}

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

ESPOO_NS = {"gml": "http://www.opengis.net/gml", "wfs": "http://www.opengis.net/wfs", "GIS": "http://www.tekla.com/schemas/GIS"}

def espoo_buildings(raw):
    """Espoo's Rakennukset (Tekla WFS 1.1.0, GML in ETRS-GK25, easting first): KERROSLUKU floors × 3 m."""
    out = []
    for b in ET.fromstring(raw).iterfind("gml:featureMember/GIS:Rakennukset", ESPOO_NS):
        floors = number(b.findtext("GIS:KERROSLUKU", namespaces=ESPOO_NS))
        for ring in b.iterfind("GIS:Geometry//gml:exterior/gml:LinearRing", ESPOO_NS):
            v = [float(x) for p in ring.iterfind("gml:pos", ESPOO_NS) for x in p.text.split()]
            v += [float(x) for p in ring.iterfind("gml:posList", ESPOO_NS) for x in p.text.split()]
            if len(v) >= 6:
                out.append(building([vertex(*tm_to_wgs84(v[i], v[i + 1], *GK25)) for i in range(0, len(v) - 1, 2)],
                                    floors * 3 if floors and floors > 0 else None))
    return out

def espoo(rect, cap=2000, depth=0):
    """The City of Espoo's buildings WFS over a box (its GK25 bounding box); a box that hits the cap is split in four."""
    s, w, n, e = rect
    xs, ys = zip(*(wgs84_to_tm(la, lo, *GK25) for la in (s, n) for lo in (w, e)))
    q = urllib.parse.urlencode({"service": "WFS", "version": "1.1.0", "request": "GetFeature", "typeName": "GIS:Rakennukset",
                                "maxFeatures": str(cap), "bbox": f"{min(xs):.1f},{min(ys):.1f},{max(xs):.1f},{max(ys):.1f}"})
    raw = get("https://kartat.espoo.fi/teklaogcweb/wfs.ashx?" + q)
    if raw.count(b"<gml:featureMember") >= cap and depth < 3:
        ms, me = (s + n) / 2, (w + e) / 2
        return [b for part in ((s, w, ms, me), (s, me, ms, e), (ms, w, n, me), (ms, me, n, e)) for b in espoo(part, cap, depth + 1)]
    return espoo_buildings(raw)

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

def citygml_tiles(url, km, zone=32, odd_e=False):
    """A fetch over LoD2 CityGML tiles of `km` km in UTM `zone`N, named by their south-west corner in km: each
    tile a box touches is downloaded and parsed once a run, then served from memory. A tile the publisher
    does not have (404: no building there) is empty. A .zip tile (Saxony's, Baden-Württemberg's) is read member
    by member; `odd_e` for a grid whose eastings are odd kilometres (Baden-Württemberg's 2 km zips)."""
    def parse(raw):
        if not url.endswith(".zip"): return citygml_buildings(raw, zone)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return [b for m in z.namelist() if m.lower().endswith((".gml", ".xml")) for b in citygml_buildings(z.read(m), zone)]
    def fetch(rect):
        s, w, n, e = rect
        corners = [wgs84_to_utm(la, lo, zone) for la in (s, n) for lo in (w, e)]
        def span(i):
            lo, hi = int(min(c[i] for c in corners) // 1000), int(max(c[i] for c in corners) // 1000)
            start = (lo - 1) // km * km + 1 if (odd_e and i == 0) else lo // km * km
            return range(start, hi + 1, km)
        out = []
        for x in span(0):
            for y in span(1):
                u = url.format(e=x, n=y)
                if u not in _citygml:
                    try: _citygml[u] = parse(get(u))
                    except SourceError as err:
                        if "HTTP 404" not in str(err): raise
                        _citygml[u] = []
                out += [b for b in _citygml[u] if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"])]
        return out
    return fetch

def citygml_archive(url, name, km):
    """LoD2 CityGML tiles of `km` km in UTM 32N held in one archive (Bremen's: a zip holding a stored zip of
    the tiles), downloaded once a run (kept a month) and read tile by tile from inside it, the tile `name`
    ({e}, {n}: its south-west corner in km) matched at the end of a member's path."""
    def tiles():
        if url not in _citygml:
            outer = zipfile.ZipFile(download(url, os.path.join("extracts", "citygml"), max_days=30))
            inner = next(m for m in outer.namelist() if m.lower().endswith(".zip"))
            _citygml[url] = zipfile.ZipFile(outer.open(inner))
        return _citygml[url]
    def fetch(rect):
        s, w, n, e = rect
        z = tiles()
        corners = [wgs84_to_utm(la, lo, 32) for la in (s, n) for lo in (w, e)]
        span = lambda i: range(int(min(c[i] for c in corners) // 1000 // km * km), int(max(c[i] for c in corners) // 1000) + 1, km)
        out = []
        for x in span(0):
            for y in span(1):
                key = (url, x, y)
                if key not in _citygml:
                    member = next((m for m in z.namelist() if m.endswith(name.format(e=x, n=y))), None)
                    _citygml[key] = citygml_buildings(z.read(member)) if member else []
                out += [b for b in _citygml[key] if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"])]
        return out
    return fetch

def lod1_buildings(raw, to_wgs84=cs92_to_wgs84):
    """LoD1 CityGML (GUGiK's Modele 3D budynków): each Building's solid, its bottom face (every vertex at the
    solid's lowest z) as the outline, its measuredHeight as the height; positions easting first (as GUGiK writes them).
    `raw` is bytes or an open file (a sheet streamed out of its zip)."""
    out = []
    for _, el in ET.iterparse(raw if hasattr(raw, "read") else io.BytesIO(raw)):
        if el.tag.rsplit("}", 1)[-1] != "Building": continue
        h = number(el.findtext("{*}measuredHeight"))
        faces = [[float(x) for x in p.text.split()] for p in el.findall("{*}lod1Solid//{*}exterior//{*}posList")]
        faces = [v for v in faces if len(v) >= 9]
        if faces:
            low = min(min(v[2::3]) for v in faces)
            bottom = next((v for v in faces if max(v[2::3]) - low < 0.01), None)
            if bottom:
                out.append(building([vertex(*to_wgs84(bottom[i], bottom[i + 1])) for i in range(0, len(bottom) - 2, 3)],
                                    h if h and h > 0 else None))
        el.clear()
    return out

def estonia_lod1_buildings(raw):
    """Maa- ja Ruumiamet's LoD1 CityGML (a lod1MultiSurface of triangles, EPSG:3301 northing first): the triangles at
    the building's lowest z make its outline (triangles_outline), measuredHeight its height."""
    out = []
    for _, el in ET.iterparse(raw if hasattr(raw, "read") else io.BytesIO(raw)):
        if el.tag.rsplit("}", 1)[-1] != "Building": continue
        h = number(el.findtext("{*}measuredHeight"))
        faces = []
        for p in el.findall("{*}lod1MultiSurface//{*}exterior//{*}posList"):
            v = p.text.split()
            faces.append([(float(v[i]), float(v[i + 1]), float(v[i + 2])) for i in range(0, len(v) - 2, 3)])
        if faces:
            low = min(z for face in faces for *_, z in face)
            ring = triangles_outline([[(n, e) for n, e, z in face] for face in faces if all(z - low < 0.01 for *_, z in face)])
            if ring:
                out.append(building([vertex(*lcc3301_to_wgs84(e, n)) for n, e in ring], h if h and h > 0 else None))
        el.clear()
    return out

def lod1_archive(url, reader=lod1_buildings):
    """A fetch over one zip of LoD1 CityGML sheets (GUGiK's, one per powiat; Estonia's, one file per municipality),
    downloaded once a run (kept a month), streamed and parsed whole on first use."""
    def fetch(rect):
        s, w, n, e = rect
        if url not in _citygml:
            with zipfile.ZipFile(download(url, os.path.join("extracts", "citygml"), max_days=30)) as z:
                _citygml[url] = [b for m in z.namelist() if m.lower().endswith(".gml") for b in reader(z.open(m))]
        return [b for b in _citygml[url] if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"])]
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
NRCAN_HAMILTON = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_ON_Hamilton_Niagara_2021_2_gpkg.zip"
NRCAN_LAKE_ERIE = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_ON_2017-LakeErie_gpkg.zip"
NRCAN_HALIFAX = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_NS_Halifax_County_Inland_2018_gpkg.zip"
NRCAN_WINNIPEG = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_MB_Winnipeg_2019_gpkg.zip"
NRCAN_QUEBEC = "https://ftp.maps.canada.ca/pub/nrcan_rncan/extraction/auto_building/gpkg/Autobuilding_QC_Quebec_Levis_2017_gpkg.zip"

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

USA_STRUCTURES = "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/USA_Structures_View/FeatureServer/0"

# Seattle's Building Outlines 2015 (roof outlines, BP99_APEX the roof apex's elevation in feet above NAVD88) and the
# USGS 3DEP bare-earth elevation (public domain, NAVD88 metres), exported for a block as an uncompressed float32 GeoTIFF.
SEATTLE_OUTLINES = "https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/Building_Outline_2015/FeatureServer/0"
USGS_DEM = ("https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage?bbox={},{},{},{}"
            "&bboxSR=4326&imageSR=4326&size={},{}&format=tiff&pixelType=F32&interpolation=RSP_BilinearInterpolation&f=image")
DEM_STEP = 1e-4   # degrees a ground pixel: ~11 m north–south, ~7.5 m east–west at Seattle; the ground under a roof
DEM_PAD = 200     # metres the ground grid reaches past a block: an outline it meets may have its centre outside
FOOT = 0.3048

def sampler(tiff, rect):
    """A ground grid's elevation at a point (its pixel), None outside the grid or on a no-data pixel. `tiff` is
    geotiff()'s answer for the lat/lon `rect`, which sets the pixel size."""
    west, north, width, height, values = tiff
    s, w, n, e = rect
    def at(lat, lon):
        x, y = math.floor((lon - west) / (e - w) * width), math.floor((north - lat) / (n - s) * height)
        if not (0 <= x < width and 0 <= y < height): return None
        v = values[y * width + x]
        return v if v > -1e30 else None
    return at

def usgs_ground(rect):
    s, w, n, e = padded(rect, DEM_PAD)
    width, height = math.ceil((e - w) / DEM_STEP), math.ceil((n - s) / DEM_STEP)
    s, w, n, e = rect = s, w, s + height * DEM_STEP, w + width * DEM_STEP   # square pixels: the server keeps the box as asked
    tiff = get(USGS_DEM.format(w, s, e, n, width, height), parse=geotiff)
    if abs(tiff[0] - w) > 1e-6 or abs(tiff[1] - n) > 1e-6: raise SourceError(f"3DEP: grid at {tiff[:2]}, asked {w},{n}")
    return sampler(tiff, rect)

def apex_heights(features, ground, field="BP99_APEX", guess=False):
    """Roof elevations in feet above NAVD88 (Seattle's BP99_APEX, Virginia Beach's HEIGHT_HIGHEST) less the ground under
    the outline's centre (the mean of its vertices). One with no apex, no ground, under 2 m above it (an outline the apex
    survey missed) or over 400 m (Virginia Beach's few apexes typed as a year, 2020 ft) is left out: OSM's own height
    stays (a combine row). With guess=True one with no apex at all is kept at the flagged 15 m guess instead."""
    out = []
    for f in features:
        apex = number((f.get("properties") or {}).get(field))
        for ring in outer_rings(f.get("geometry")) if apex or guess else ():
            g = ground(sum(p["latitude"] for p in ring) / len(ring), sum(p["longitude"] for p in ring) / len(ring)) if apex else None
            if g is not None and 2 <= apex * FOOT - g <= 400: out.append(building(ring, apex * FOOT - g))
            elif not apex: out.append(building(ring, None))
    return out

def seattle(rect):
    return apex_heights(arcgis(SEATTLE_OUTLINES, rect, ["BP99_APEX"], where="BP99_TYPE IN ('BLD','GAR')"), usgs_ground(rect))

# Virginia Beach's Structures_Buildings (232,372 outlines from the City's aerials, 2017–2026): HEIGHT_HIGHEST is the
# roof's highest point in feet above NAVD88, not above the ground (HEIGHT_LOWEST, the lowest roof point, never lies
# below 3DEP's ground; the raw value rises with the ground, 2026-11-05). A shed, canopy or other structure with no
# height is left out rather than guessed at 15 m; a building with none keeps the guess.
VIRGINIA_BEACH_BUILDINGS = "https://geo.vbgov.com/mapservices/rest/services/Basemaps/VB_Basemap/MapServer/8"

def virginia_beach(rect):
    return apex_heights(arcgis(VIRGINIA_BEACH_BUILDINGS, rect, ["HEIGHT_HIGHEST"],
                               where="HEIGHT_HIGHEST > 0 OR FCODE LIKE 'Building%' OR FCODE = 'Parking Garage'"),
                        usgs_ground(rect), field="HEIGHT_HIGHEST", guess=True)

# Brussels: UrbIS-Buildings3D (paradigm.brussels, CC0), LoD2 solids in 1 km Shapefile tiles in Lambert 72, named by the
# south-west corner in km and dated (monthly); the INSPIRE ATOM feed lists every tile's every date.
URBIS_FEED = "https://urbisdownload.datastore.brussels/atomfeed/e9ec2aa4-cffd-11ee-bccc-00090ffe0001-en.xml"
_urbis = {}   # "feed" → {(km east, km north): the latest tile's URL}; a tile's URL → its buildings, parsed once a run

def urbis_solids(raw):
    """UrbIS-Buildings3D's BuildingSolids .shp (MultiPatch, one closed solid per record): the rings lying flat at the
    solid's lowest z make its outline (triangles_outline), its z range its height. Every part is a ring (FirstRing, on
    all 3,569 solids of tile 148170, 2026-10-03); a solid whose bottom is not flat (6 of them there) is left out."""
    out, at = [], 100
    while at + 8 <= len(raw):
        size = 2 * struct.unpack_from(">i", raw, at + 4)[0]
        body, at = raw[at + 8:at + 8 + size], at + 8 + size
        if len(body) < 44 or struct.unpack_from("<i", body)[0] != 31: continue
        parts, points = struct.unpack_from("<ii", body, 36)
        starts = struct.unpack_from(f"<{parts}i", body, 44) + (points,)
        o = 44 + 8 * parts
        xy = struct.unpack_from(f"<{2 * points}d", body, o)
        low, high = struct.unpack_from("<2d", body, o + 16 * points)
        z = struct.unpack_from(f"<{points}d", body, o + 16 * points + 16)
        ring = triangles_outline([[(xy[2 * i], xy[2 * i + 1]) for i in range(a, b)]
                                  for a, b in zip(starts, starts[1:]) if all(z[i] - low < 0.01 for i in range(a, b))])
        if ring: out.append(building([vertex(*lambert72_to_wgs84(x, y)) for x, y in ring], high - low if high > low else None))
    return out

def urbis(rect):
    """The UrbIS solids of every tile the rect meets (the feed read once a run; a tile's BuildingSolids layer fetched
    by range once a year, zip_layer), those with a vertex in the rect."""
    if "feed" not in _urbis:
        latest = {}
        for url, e, n, day in re.findall(r'href="([^"]*/SHP/UrbISBuildings3D_31370_SHP_(\d{3})(\d{3})_(\d{8})\.zip)"', get(URBIS_FEED).decode()):
            if day > latest.get((int(e), int(n)), ("", ""))[0]: latest[(int(e), int(n))] = (day, url)
        if not latest: raise SourceError("UrbIS: no SHP tile in the feed")
        _urbis["feed"] = {k: url for k, (_, url) in latest.items()}
    s, w, n, e = rect
    corners = [wgs84_to_lambert72(la, lo) for la in (s, n) for lo in (w, e)]
    out = []
    for x in range(int(min(c[0] for c in corners) // 1000), int(max(c[0] for c in corners) // 1000) + 1):
        for y in range(int(min(c[1] for c in corners) // 1000), int(max(c[1] for c in corners) // 1000) + 1):
            url = _urbis["feed"].get((x, y))
            if url is None: continue   # outside the Region
            if url not in _urbis:
                with open(zip_layer(url, f"UrbISBuildings3D_{x}{y}_BuildingSolids", "urbis") + ".shp", "rb") as f:
                    _urbis[url] = urbis_solids(f.read())
            out += [b for b in _urbis[url] if any(s <= p["latitude"] <= n and w <= p["longitude"] <= e for p in b["outline"])]
    return out

# The City of Yarra's 3D buildings by suburb (data.gov.au, CC BY 4.0): every building of the municipality as built in
# 2015, one 3DS mesh a suburb, vertices in MGA zone 55 metres and AHD (OSM's outlines in Fitzroy sit on them within a
# metre, 2026-11-10). The roofs (every face not vertical) are drawn on a 1 m grid, a pixel holding the highest one over
# its centre; a footprint's height is the 90th percentile of its pixels (lidar_height's rule) less the lowest mesh
# vertex within 2 m of its box: its walls' foot. 280 MB for the 12 suburbs, read once a run. The Atherton Gardens towers
# read 57.6–59.2 m (OSM 63 m); a two-storey Victorian terrace 8–9 m (OSM's levels × 3 m: 6 m).
YARRA_3D = "https://data.gov.au/data/dataset/50923ab8-864c-4fbe-a2e2-42034b16cf5e/resource/{}/download/{}.3ds"
YARRA_SUBURBS = {"abbotsford": "6c30ac7f-fdc4-4236-a706-54f0d223da3c", "alphington": "59a0c906-f71f-4bcc-89cf-f2b179e46da2",
                 "burnley": "9e5313cc-50ed-4fd2-a218-b6535025ac5d", "carlton_north": "9f052ce3-111d-4dbd-84b4-3480a86d1eb9",
                 "clifton_hill": "c9e6b511-dab2-452a-8313-2e9ecd2fd271", "collingwood": "91230a66-1b8c-45dd-bb94-a7e83ec2e971",
                 "cremorne": "113bf0f6-4f0c-466e-a3ac-21cb1c4470e6", "fairfield": "19f15e3f-638f-4d2e-bc5e-77fd93fa4f0c",
                 "fitzroy": "bffd8969-6bfd-43c0-9546-1882ed7e5f96", "fitzroy_north": "6d6960ed-dcc6-4aba-81c9-692f79b8ebcb",
                 "princes_hill": "741670ff-1198-4770-9cd3-3c7fd54094b6", "richmond": "4b55f8eb-5203-4d5e-9d11-16ad44cc4960"}
YARRA_CHUNK = 500    # metres a side: a chunk's roofs (1 m pixels) and walls' feet (2 m cells), decimetres AHD
_yarra = {}          # (east, north) // YARRA_CHUNK → (roof array('h'), foot array('h')); "read" once the meshes are in

def yarra_chunk(key):
    if key not in _yarra:
        _yarra[key] = (array.array("h", [VOID]) * (YARRA_CHUNK * YARRA_CHUNK), array.array("h", [32767]) * (YARRA_CHUNK * YARRA_CHUNK // 4))
    return _yarra[key]

def draw_faces(faces):
    """The faces' roofs and walls' feet into the chunks."""
    for tri in faces:
        for x, y, z in tri:
            roof, foot = yarra_chunk((int(x // YARRA_CHUNK), int(y // YARRA_CHUNK)))
            i = int(y % YARRA_CHUNK // 2) * (YARRA_CHUNK // 2) + int(x % YARRA_CHUNK // 2)
            foot[i] = min(foot[i], round(z * 10))
        (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri
        d = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if abs(d) < 1e-6: continue   # a wall
        for gy in range(math.floor(min(y1, y2, y3)), math.ceil(max(y1, y2, y3))):
            for gx in range(math.floor(min(x1, x2, x3)), math.ceil(max(x1, x2, x3))):
                a = ((y2 - y3) * (gx + 0.5 - x3) + (x3 - x2) * (gy + 0.5 - y3)) / d
                b = ((y3 - y1) * (gx + 0.5 - x3) + (x1 - x3) * (gy + 0.5 - y3)) / d
                if a < 0 or b < 0 or a + b > 1: continue
                roof, _ = yarra_chunk((gx // YARRA_CHUNK, gy // YARRA_CHUNK))
                i = gy % YARRA_CHUNK * YARRA_CHUNK + gx % YARRA_CHUNK
                roof[i] = max(roof[i], round((a * z1 + b * z2 + (1 - a - b) * z3) * 10))

def yarra_height(outline):
    """One footprint's height from the meshes, None under 4 roof pixels or under 2 m (built after 2015)."""
    if "read" not in _yarra:
        for name, resource in YARRA_SUBURBS.items():
            with open(download(YARRA_3D.format(resource, name), os.path.join("extracts", "yarra3d"), max_days=365), "rb") as f:
                draw_faces(mesh_faces(f.read()))
        _yarra["read"] = True
    ring = [wgs84_to_tm(p["latitude"], p["longitude"], 147, false_northing=10_000_000) for p in outline]
    edges = list(zip(ring, ring[1:] + ring[:1]))
    heights = []
    for north in range(math.floor(min(y for _, y in ring)), math.ceil(max(y for _, y in ring))):
        y = north + 0.5
        xs = sorted(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]) for a, b in edges if (a[1] > y) != (b[1] > y))
        for x0, x1 in zip(xs[::2], xs[1::2]):
            for east in range(math.ceil(x0 - 0.5), math.floor(x1 - 0.5) + 1):
                roof = _yarra.get((east // YARRA_CHUNK, north // YARRA_CHUNK))
                h = roof[0][north % YARRA_CHUNK * YARRA_CHUNK + east % YARRA_CHUNK] if roof else VOID
                if h != VOID: heights.append(h)
    feet = [c[1][int(y % YARRA_CHUNK // 2) * (YARRA_CHUNK // 2) + int(x % YARRA_CHUNK // 2)]
            for x in range(math.floor(min(x for x, _ in ring) - 2), math.ceil(max(x for x, _ in ring) + 2) + 1, 2)
            for y in range(math.floor(min(y for _, y in ring) - 2), math.ceil(max(y for _, y in ring) + 2) + 1, 2)
            if (c := _yarra.get((int(x // YARRA_CHUNK), int(y // YARRA_CHUNK))))]
    if len(heights) < 4 or not feet or min(feet) == 32767: return None
    heights.sort()
    h = (heights[int(LIDAR_PERCENTILE * (len(heights) - 1))] - min(feet)) / 10
    return h if h >= 2 else None

def yarra(rect):
    """OpenStreetMap's footprints, each with the meshes' height where they read one (a combine row's fetch)."""
    return [building(b["outline"], h) for b in osm_buildings(rect, True) if (h := yarra_height(b["outline"])) is not None]

CITY_BUILDINGS = [
    building_row("Melbourne", (-37.8507, -37.7755), (144.897, 144.9913), boundary=boundary("melbourne"), fetch=lambda r: footprints(opendatasoft(
        "data.melbourne.vic.gov.au", "2023-building-footprints", in_bbox("geo_point_2d", r) + ' and footprint_type != "Tunnel"',
        "structure_extrusion"), lambda p: p.get("structure_extrusion"))),
    # Wired 2026-11-10: the City of Yarra's 2015 meshes on OSM's footprints, held to its boundary (ABS ASGS 2021 LGA
    # 27350, CC BY 4.0): Fitzroy, Collingwood, Richmond, Abbotsford, Cremorne, Carlton North, Fitzroy North, Clifton Hill.
    building_row("Yarra", (-37.8346, -37.7753), (144.9614, 145.0453), boundary=boundary("yarra"), combine="metres", fetch=yarra),
    building_row("Amsterdam", (52.2779, 52.4311), (4.7287, 5.1077), bag3d),   # 3DBAG is national: the whole municipality's box
    building_row("Rotterdam", (51.8421, 52.002), (3.9614, 4.6019), bag3d),
    building_row("The Hague", (52.00, 52.13), (4.18, 4.42), bag3d),
    building_row("Utrecht", (52.03, 52.14), (4.97, 5.20), bag3d),
    building_row("Berlin", (52.33, 52.68), (13.08, 13.77), lambda r: footprints(wfs(
        "https://gdi.berlin.de/services/wfs/ua_gebaeudehoehen", {"TYPENAMES": "ua_gebaeudehoehen:gebaeudehoehen",
        "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326", "PROPERTYNAME": "geom,hoehe"}, r), lambda p: p.get("hoehe"))),
    building_row("Geneva", (46.17, 46.24), (6.10, 6.19), lambda r: footprints(arcgis(
        "https://vector.sitg.ge.ch/arcgis/rest/services/CAD_BATIMENT_HORSOL/FeatureServer/0", r, ["HAUTEUR"]),
        lambda p: p.get("HAUTEUR"))),
    # BLDG_HEIGH is feet.
    building_row("Denver", (39.6143, 39.9143), (-105.1099, -104.5997), lambda r: footprints(arcgis(
        "https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_PROP_BUILDINGOUTLINES_A/FeatureServer/111",
        r, ["BLDG_HEIGH"]), lambda p: p["BLDG_HEIGH"] * 0.3048 if p.get("BLDG_HEIGH") else None)),
    building_row("Cape Town", (-34.36, -33.47), (18.30, 19.01), lambda r: footprints(arcgis(
        "https://esapqa.capetown.gov.za/agsext/rest/services/Theme_Based/ODP_SPLIT_6/FeatureServer/2", r, ["BLD_HGT"]),
        lambda p: p.get("BLD_HGT"))),
    building_row("São Paulo", (-24.01, -23.35), (-46.83, -46.36), lambda r: footprints(wfs(
        "https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/ows", {"TYPENAMES": "geoportal:edificacao",
        "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326", "PROPERTYNAME": "ge_poligono,qt_altura_edificacao"}, r),
        lambda p: p.get("qt_altura_edificacao"))),
    building_row("Wrocław", (51.0426, 51.2101), (16.8073, 17.1763), lambda r: footprints(arcgis(
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
    # Netanya (2026-10-21): the municipality's קונטור מבנים layer (ArcGIS Online, no licence stated), NUM_FLOORS × 3 m
    # (13,810 of 14,000 with floors; ELEV is an elevator flag, not a height). Held to the municipal boundary (OSM).
    building_row("Netanya", (32.2549, 32.3606), (34.8247, 34.8961), boundary=boundary("netanya"), fetch=lambda r: footprints(arcgis(
        "https://services5.arcgis.com/ySpO9UsTrqoVbhLe/arcgis/rest/services/%D7%A7%D7%95%D7%A0%D7%98%D7%95%D7%A8_%D7%9E%D7%91%D7%A0%D7%99%D7%9D/FeatureServer/709",
        r, ["NUM_FLOORS"], oid="OBJECTID_12"), lambda p: number(p.get("NUM_FLOORS")) * 3 if number(p.get("NUM_FLOORS")) else None)),
    # Metres, else floors × 3 m. Answers only from Israel (HTTP 571 elsewhere): from the cloud every
    # Tel Aviv cell fails and no tile is written — never an OSM tile in its place.
    building_row("Tel Aviv", (32.02, 32.16), (34.73, 34.86), lambda r: footprints(arcgis(
        "https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2/MapServer/513", r, ["gova_simplex_2019", "ms_komot"],
        oid="oid_mivne"), lambda p: p.get("gova_simplex_2019") or (p["ms_komot"] * 3 if p.get("ms_komot") else None))),
    # Jerusalem Municipality, bldg2020 (metres; "for reference and general use only"). West to 35.085 E with the
    # municipality's boundary (cities.json, 2026-10-11).
    building_row("Jerusalem", (31.70, 31.89), (35.085, 35.27), lambda r: footprints(arcgis(
        "https://services3.arcgis.com/jeqc1A7OfE9m4EPO/arcgis/rest/services/bldg2020/FeatureServer/0", r,
        ["realHeight", "height"]), lambda p: p["realHeight"] if (p.get("realHeight") or 0) > 0 else p.get("height"))),
    # Spain's cadastre (owner's decision, 2026-09-25): tiles hold derived footprints and heights, never
    # Catastro's GML, which its licence forbids spreading untransformed. A cell Catastro fails gets no tile
    # (the next run retries), never an OSM tile.
    building_row("Madrid", (40.31, 40.56), (-3.84, -3.52), catastro),
    building_row("Seville", (37.32, 37.45), (-6.03, -5.87), catastro),
    building_row("Barcelona", (41.32, 41.47), (2.05, 2.23), catastro),
    building_row("Valencia", (39.40, 39.52), (-0.43, -0.30), catastro),
    building_row("Zaragoza", (41.4516, 41.9315), (-1.1738, -0.6803), catastro),
    building_row("Málaga", (36.6356, 36.8941), (-4.5879, -4.2605), catastro),   # the whole municipality's box (2026-10-11)
    building_row("Palma", (39.5038, 39.6583), (2.5638, 2.847), catastro),   # the whole municipality's box (2026-10-11)
    building_row("Las Palmas", (28.05, 28.18), (-15.47, -15.40), catastro),
    building_row("Murcia", (37.7159, 38.1179), (-1.3855, -0.8509), catastro),   # the whole municipality's box (2026-10-25)
    building_row("Alicante", (38.2742, 38.5371), (-0.6567, -0.375), catastro),   # the whole municipality's box (2026-10-11)
    building_row("Córdoba", (37.6658, 38.0315), (-4.9986, -4.3514), catastro),   # the whole municipality's box (2026-10-25)
    building_row("Valladolid", (41.5231, 41.8156), (-4.9282, -4.6308), catastro),
    building_row("Vigo", (42.19, 42.26), (-8.78, -8.67), catastro),
    building_row("Gijón", (43.4415, 43.5868), (-5.8214, -5.5628), catastro),
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
    building_row("Düsseldorf", (51.1243, 51.3525), (6.6888, 6.94), citygml_tiles(
        "https://www.opengeodata.nrw.de/produkte/geobasis/3dg/lod2_gml/lod2_gml/LoD2_32_{e}_{n}_1_NW.gml", 1)),
    building_row("Nuremberg", (49.3314, 49.5408), (10.9887, 11.2826), citygml_tiles(
        "https://download1.bayernwolke.de/a/lod2/citygml/{e}_{n}.gml", 2)),
    # Saxony's LoD2 (GeoSN; no licence on the download, credited Datenlizenz Deutschland – Namensnennung – 2.0, the
    # stricter reading): 2 km zipped tiles in UTM 33N, through the download app's public share. Baden-Württemberg's
    # (LGL, Datenlizenz Deutschland in each zip, read the same way): 2 km zips at odd eastings of 1 km GMLs.
    # Bremen: Landesamt GeoInformation Bremen's LoD2 (CC BY 4.0), one 403 MB zip of the city's 2 km tiles, read whole.
    # The overnight run's US rows (2026-10-21), each the whole city held to its OSM boundary (Honolulu: the urban core's
    # box); feet × 0.3048 unless said. Licences: none stated (a disclaimer at most), credited to the publisher, the
    # stricter reading; Anaheim's item quotes the CC BY deed, read as CC BY 4.0; Detroit's is SEMCOG's Copyright
    # License Agreement, its notice shown in the credit.
    building_row("Detroit", (42.2551, 42.4504), (-83.288, -82.9104), boundary=boundary("detroit"), fetch=lambda r: footprints(arcgis(
        "https://gis.semcog.org/server/rest/services/Hosted/Buildings_2024/FeatureServer/2", r,
        ["median_hgt"], oid="objectid"), lambda p: number(p.get("median_hgt")) * 0.3048 if number(p.get("median_hgt")) else None)),
    # Columbus: FLOORS × 3 m, empty downtown, so combined with OSM's footprints.
    building_row("Columbus", (39.8086, 40.1573), (-83.2102, -82.7713), boundary=boundary("columbus-oh"), combine="floors", fetch=lambda r: footprints(arcgis(
        "https://maps2.columbus.gov/arcgis/rest/services/Schemas/RegionalLayers/MapServer/4", r,
        ["FLOORS"], oid="OBJECTID"), lambda p: number(p.get("FLOORS")) * 3 if number(p.get("FLOORS")) else None, guess=False)),
    building_row("Anaheim", (33.7889, 33.8791), (-118.0174, -117.6739), boundary=boundary("anaheim"), fetch=lambda r: footprints(arcgis(
        "https://gis.anaheim.net/map/rest/services/OpenData2/FeatureServer/47", r,
        ["BUILDINGHEIGHT"], oid="OBJECTID"), lambda p: number(p.get("BUILDINGHEIGHT")) * 0.3048 if number(p.get("BUILDINGHEIGHT")) else None)),
    # Las Vegas: downtown only; a cell it leaves empty asks OSM next.
    building_row("Las Vegas", (36.1295, 36.4015), (-115.4066, -115.062), boundary=boundary("las-vegas"), fetch=lambda r: footprints(arcgis(
        "https://services1.arcgis.com/F1v0ufATbBQScMtY/arcgis/rest/services/BuildingFoot_Downtown_Building_Heights/FeatureServer/173", r,
        ["HEIGHT"], oid="OBJECTID"), lambda p: number(p.get("HEIGHT")) * 0.3048 if number(p.get("HEIGHT")) else None)),
    # Honolulu: HoLIS gis_height (never the owner fields).
    building_row("Honolulu", (21.26, 21.35), (-157.89, -157.76), fetch=lambda r: footprints(arcgis(
        "https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services/Structures_Facilities/FeatureServer/0", r,
        ["gis_height"], oid="objectid"), lambda p: number(p.get("gis_height")) * 0.3048 if number(p.get("gis_height")) else None)),
    building_row("Cincinnati", (39.052, 39.2211), (-84.7124, -84.3695), boundary=boundary("cincinnati"), fetch=lambda r: footprints(arcgis(
        "https://services.arcgis.com/JyZag7oO4NteHGiq/arcgis/rest/services/Open_Data_Feature_Collection/FeatureServer/58", r,
        ["HEIGHT_IN_FEET"], oid="OBJECTID"), lambda p: number(p.get("HEIGHT_IN_FEET")) * 0.3048 if number(p.get("HEIGHT_IN_FEET")) else None)),
    building_row("Nashville", (35.9677, 36.4056), (-87.0548, -86.5155), boundary=boundary("nashville"), fetch=lambda r: footprints(arcgis(
        "https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/Building_Footprints_view/FeatureServer/0", r,
        ["Height"], oid="OBJECTID"), lambda p: number(p.get("Height")) * 0.3048 if number(p.get("Height")) else None)),
    # Louisville: LOJIC's roofline heights (metres, the whole consolidated metro). Phoenix: the 2014 LiDAR footprints
    # (NGA/USGS, metres above ground) cover the metro's core only; a cell they leave empty asks OSM next. Omaha (DCGIS
    # 2022 LiDAR) and Memphis (LiDAR, MAXHT_F) in feet; Memphis's few readings over 600 ft (its tallest tower is
    # ~430 ft) are noise, left to the guess.
    building_row("Louisville", (37.9971, 38.3802), (-85.9471, -85.4051), boundary=boundary("louisville"), fetch=lambda r: footprints(arcgis(
        "https://gis.lojic.org/maps/rest/services/LojicSolutions/OpenDataBase/MapServer/8", r,
        ["PTD.building_heights.MAX_HEIGHT_METERS"], oid="PTD.bg.OBJECTID"), lambda p: number(p.get("PTD.building_heights.MAX_HEIGHT_METERS")))),
    building_row("Phoenix", (33.2904, 33.9184), (-112.3241, -111.9255), boundary=boundary("phoenix"), fetch=lambda r: footprints(arcgis(
        "https://services3.arcgis.com/0OPQIK59PJJqLK0A/arcgis/rest/services/Phoenix_AZ_3D_Building/FeatureServer/0", r,
        ["HGT_AGL"], oid="FID"), lambda p: number(p.get("HGT_AGL")))),
    building_row("Omaha", (41.1905, 41.3931), (-96.2682, -95.8711), boundary=boundary("omaha"), fetch=lambda r: footprints(arcgis(
        "https://dcgis.org/server/rest/services/Hosted/2022_Building_Footprints/FeatureServer/0", r,
        ["buildingheight"], oid="objectid"), lambda p: number(p.get("buildingheight")) * 0.3048 if number(p.get("buildingheight")) else None)),
    building_row("Memphis", (34.9941, 35.2639), (-90.3084, -89.637), boundary=boundary("memphis"), fetch=lambda r: footprints(arcgis(
        "https://maps.memphistn.gov/mapping/rest/services/Basemaps/Common_Services_PROD/MapServer/28", r,
        ["MAXHT_F"], oid="OBJECTID"), lambda p: number(p.get("MAXHT_F")) * 0.3048 if 0 < (number(p.get("MAXHT_F")) or 0) < 600 else None)),
    # Riverside: the City's downtown buildings (HeightFt, 2019); a cell it leaves empty asks OSM next.
    # Step 4 (2026-11-04). Houston and Orlando: FEMA/ORNL's USA Structures (CC BY 4.0), HEIGHT in metres, combined with
    # OSM's footprints: its heights of the tallest towers run low (Orlando's 134 m SunTrust Center reads 56 m), so a
    # mapper's height tag stays. San Salvador: MITUR's Altura de las edificaciones, the historic centre only (the box is
    # the layer's extent; a cell it leaves empty asks OSM next), Altura_metros.
    building_row("Houston", (29.547, 30.1104), (-95.9098, -95.012), boundary=boundary("houston"), combine="metres", fetch=lambda r: footprints(arcgis(
        USA_STRUCTURES, r, ["HEIGHT"]), lambda p: number(p.get("HEIGHT")), guess=False)),
    building_row("Orlando", (28.348, 28.6143), (-81.5076, -81.1241), boundary=boundary("orlando"), combine="metres", fetch=lambda r: footprints(arcgis(
        USA_STRUCTURES, r, ["HEIGHT"]), lambda p: number(p.get("HEIGHT")), guess=False)),
    building_row("San Salvador", (13.6945, 13.7012), (-89.2055, -89.1864), fetch=lambda r: footprints(arcgis(
        "https://services8.arcgis.com/AamuaBHBnpGFgvGV/arcgis/rest/services/Altura_de_las_edificaciones/FeatureServer/9", r,
        ["Altura_metros"]), lambda p: number(p.get("Altura_metros")))),
    # Vienna: the Flächen-Mehrzweckkarte's Baukörpermodell (WFS, CC BY 4.0), one polygon per building part, O_KOTE − T_KOTE
    # (roof minus ground, metres); only the Gebäude parts (portals, overhangs, kiosks left out). Stephansdom: 136 m.
    # Hong Kong: LandsD's Building layer on CSDI (CSDI Portal Terms and Conditions), TopHeight − BaseHeight (mPD).
    building_row("Vienna", (48.1179, 48.3227), (16.1818, 16.5776), boundary=boundary("vienna"), fetch=lambda r: footprints(
        [f for f in wfs("https://data.wien.gv.at/daten/geo", {"TYPENAMES": "ogdwien:FMZKBKMOGD", "OUTPUTFORMAT": "json",
            "SRSNAME": "urn:ogc:def:crs:EPSG::4326"}, r) if (f.get("properties") or {}).get("LAYER") == "Gebäude"],
        lambda p: difference(p, "O_KOTE", "T_KOTE"), guess=False)),
    building_row("Hong Kong", (22.1534, 22.562), (113.835, 114.4419), boundary=boundary("hong-kong"), fetch=lambda r: footprints(arcgis(
        "https://portal.csdi.gov.hk/server/rest/services/common/landsd_rcd_1637211194312_35158/FeatureServer/0", r,
        ["TopHeight", "BaseHeight"]), lambda p: difference(p, "TopHeight", "BaseHeight"))),
    # Brussels: UrbIS-Buildings3D's LoD2 solids (CC0), the city's own footprints; the Town Hall's tower reads 93 m.
    # Seattle: Building Outlines 2015's roof apex less the 3DEP ground (licence not stated: credited to the City),
    # combined with OSM's footprints: the outlines are 2015's, OSM's are newer.
    building_row("Brussels", (50.7963, 50.9139), (4.3137, 4.4371), boundary=boundary("brussels"), fetch=urbis),
    building_row("Seattle", (47.4956, 47.7342), (-122.436, -122.2359), boundary=boundary("seattle"), combine="metres", fetch=seattle),
    # Virginia Beach: the City's own outlines, HEIGHT_HIGHEST (feet above NAVD88) less the 3DEP ground (warranty
    # disclaimer only: credited to the City). The box is the layer's extent; the city is not tiled yet (no user asked),
    # and its row takes a boundary("virginia-beach") when it is, so Norfolk's and Chesapeake's edge cells stay off it.
    building_row("Virginia Beach", (36.5487, 36.9315), (-76.2281, -75.8681), fetch=virginia_beach),
    building_row("Riverside", (33.8726, 34.0195), (-117.5238, -117.2788), boundary=boundary("riverside"), fetch=lambda r: footprints(arcgis(
        "https://services.arcgis.com/Fu2oOWg1Aw7azh41/arcgis/rest/services/DowntownBuildings/FeatureServer/0", r,
        ["HeightFt"], oid="FID"), lambda p: number(p.get("HeightFt")) * 0.3048 if number(p.get("HeightFt")) else None)),
    # Portland: the Bureau of Planning and Sustainability's footprints on PortlandMaps (unreachable on 2026-09-25,
    # answering now): AVG_HEIGHT in feet, else NUM_STORY × 3 m.
    building_row("Portland", (45.4325, 45.6529), (-122.8368, -122.472), boundary=boundary("portland"), fetch=lambda r: footprints(arcgis(
        "https://www.portlandmaps.com/od/rest/services/COP_OpenData_Property/MapServer/48", r, ["AVG_HEIGHT", "NUM_STORY"]),
        lambda p: number(p.get("AVG_HEIGHT")) * 0.3048 if (number(p.get("AVG_HEIGHT")) or 0) > 0
        else (number(p.get("NUM_STORY")) * 3 if (number(p.get("NUM_STORY")) or 0) > 0 else None))),
    building_row("Bremen", (53.0111, 53.6102), (8.4815, 8.9909), boundary=boundary("bremen"),
                 fetch=citygml_archive("https://gdi2.geo.bremen.de/inspire/download/LoD/data/LOD2_CITYGML_HB.zip",
                                       "LoD2_32_{e}_{n}_2_HB.gml", 2)),
    building_row("Leipzig", (51.2381, 51.4482), (12.2366, 12.5425), boundary=boundary("leipzig"), fetch=citygml_tiles(
        "https://geocloud.landesvermessung.sachsen.de/public.php/dav/files/AyJqXpJAZJXomCb/lod2_33{e}_{n}_2_sn_citygml.zip", 2, zone=33)),
    building_row("Dresden", (50.9749, 51.1777), (13.5793, 13.9661), boundary=boundary("dresden"), fetch=citygml_tiles(
        "https://geocloud.landesvermessung.sachsen.de/public.php/dav/files/AyJqXpJAZJXomCb/lod2_33{e}_{n}_2_sn_citygml.zip", 2, zone=33)),
    building_row("Mannheim", (49.4103, 49.5905), (8.4141, 8.59), boundary=boundary("mannheim"), fetch=citygml_tiles(
        "https://opengeodata.lgl-bw.de/data/lod2/LoD2_32_{e}_{n}_2_bw.zip", 2, odd_e=True)),
    # The overnight run's LoD2 rows (2026-10-21), each the whole city, held to its OSM boundary: NRW's statewide
    # model (as Düsseldorf's) for seven cities, LGLN's 1 km tiles for Hannover, Bavaria's (as Nuremberg's) for Augsburg.
    building_row("Essen", (51.3475, 51.5343), (6.8943, 7.1377), boundary=boundary("essen"), fetch=citygml_tiles(
        "https://www.opengeodata.nrw.de/produkte/geobasis/3dg/lod2_gml/lod2_gml/LoD2_32_{e}_{n}_1_NW.gml", 1)),
    building_row("Dortmund", (51.4157, 51.6001), (7.3023, 7.6382), boundary=boundary("dortmund"), fetch=citygml_tiles(
        "https://www.opengeodata.nrw.de/produkte/geobasis/3dg/lod2_gml/lod2_gml/LoD2_32_{e}_{n}_1_NW.gml", 1)),
    building_row("Wuppertal", (51.1657, 51.3181), (7.014, 7.3135), boundary=boundary("wuppertal"), fetch=citygml_tiles(
        "https://www.opengeodata.nrw.de/produkte/geobasis/3dg/lod2_gml/lod2_gml/LoD2_32_{e}_{n}_1_NW.gml", 1)),
    building_row("Duisburg", (51.3333, 51.5601), (6.6256, 6.8303), boundary=boundary("duisburg"), fetch=citygml_tiles(
        "https://www.opengeodata.nrw.de/produkte/geobasis/3dg/lod2_gml/lod2_gml/LoD2_32_{e}_{n}_1_NW.gml", 1)),
    building_row("Bochum", (51.4105, 51.5314), (7.102, 7.3494), boundary=boundary("bochum"), fetch=citygml_tiles(
        "https://www.opengeodata.nrw.de/produkte/geobasis/3dg/lod2_gml/lod2_gml/LoD2_32_{e}_{n}_1_NW.gml", 1)),
    building_row("Bielefeld", (51.9148, 52.1148), (8.3778, 8.6634), boundary=boundary("bielefeld"), fetch=citygml_tiles(
        "https://www.opengeodata.nrw.de/produkte/geobasis/3dg/lod2_gml/lod2_gml/LoD2_32_{e}_{n}_1_NW.gml", 1)),
    building_row("Bonn", (50.6326, 50.7745), (7.0225, 7.2107), boundary=boundary("bonn"), fetch=citygml_tiles(
        "https://www.opengeodata.nrw.de/produkte/geobasis/3dg/lod2_gml/lod2_gml/LoD2_32_{e}_{n}_1_NW.gml", 1)),
    building_row("Hannover", (52.3048, 52.4544), (9.6044, 9.9186), boundary=boundary("hannover"), fetch=citygml_tiles(
        "https://lod2.opengeodata.lgln.niedersachsen.de/LoD2_32_{e}_{n}_1_ni.gml", 1)),
    building_row("Augsburg", (48.2581, 48.4587), (10.7633, 10.9594), boundary=boundary("augsburg"), fetch=citygml_tiles(
        "https://download1.bayernwolke.de/a/lod2/citygml/{e}_{n}.gml", 2)),
    # 2026-10-25: Cologne on NRW's model, Munich on Bavaria's, each the whole city held to its boundary (Cologne
    # Cathedral 161 m, the Frauenkirche's square 80 m at most).
    building_row("Cologne", (50.8304, 51.085), (6.7725, 7.1621), boundary=boundary("cologne"), fetch=citygml_tiles(
        "https://www.opengeodata.nrw.de/produkte/geobasis/3dg/lod2_gml/lod2_gml/LoD2_32_{e}_{n}_1_NW.gml", 1)),
    building_row("Munich", (48.0616, 48.2481), (11.3607, 11.723), boundary=boundary("munich"), fetch=citygml_tiles(
        "https://download1.bayernwolke.de/a/lod2/citygml/{e}_{n}.gml", 2)),
    # Brno's Výška budov (data.brno.cz, CC BY): vyska_komp, metres (228 of 236 at náměstí Svobody).
    building_row("Brno", (49.1098, 49.2944), (16.4279, 16.7279), boundary=boundary("brno"), fetch=lambda r: footprints(arcgis(
        "https://services6.arcgis.com/fUWVlHWZNxUvTUh8/arcgis/rest/services/V%C3%BD%C5%A1ka_budov/FeatureServer/0", r,
        ["vyska_komp"], oid="FID"), lambda p: number(p.get("vyska_komp")))),
    # GUGiK's Modele 3D budynków, LoD1 2024 (CC BY 4.0): one zip of CityGML sheets per powiat, each
    # the city itself: Lublin, Bydgoszcz, Gdańsk, and since 2026-10-25 Szczecin, Warsaw, Kraków, Łódź, Poznań (lod1_archive).
    building_row("Lublin", (51.1398, 51.2966), (22.4537, 22.6736), boundary=boundary("lublin"),
                 fetch=lod1_archive("https://opendata.geoportal.gov.pl/InneDane/Budynki3D/LOD1/2024/06/0663.zip")),
    building_row("Bydgoszcz", (53.0501, 53.2094), (17.8741, 18.2026), boundary=boundary("bydgoszcz"),
                 fetch=lod1_archive("https://opendata.geoportal.gov.pl/InneDane/Budynki3D/LOD1/2024/04/0461.zip")),
    building_row("Gdańsk", (54.2749, 54.5828), (18.4295, 19.0703), boundary=boundary("gdansk"),
                 fetch=lod1_archive("https://opendata.geoportal.gov.pl/InneDane/Budynki3D/LOD1/2024/22/2261.zip")),
    building_row("Szczecin", (53.3217, 53.5377), (14.4438, 14.8054), boundary=boundary("szczecin"),   # 2026-10-25
                 fetch=lod1_archive("https://opendata.geoportal.gov.pl/InneDane/Budynki3D/LOD1/2024/32/3262.zip")),
    building_row("Warsaw", (52.0978, 52.3682), (20.8516, 21.2712), boundary=boundary("warsaw"),
                 fetch=lod1_archive("https://opendata.geoportal.gov.pl/InneDane/Budynki3D/LOD1/2024/14/1465.zip")),
    building_row("Kraków", (49.9676, 50.1262), (19.7923, 20.2174), boundary=boundary("krakow"),
                 fetch=lod1_archive("https://opendata.geoportal.gov.pl/InneDane/Budynki3D/LOD1/2024/12/1261.zip")),
    building_row("Łódź", (51.6861, 51.8599), (19.3208, 19.64), boundary=boundary("lodz"),
                 fetch=lod1_archive("https://opendata.geoportal.gov.pl/InneDane/Budynki3D/LOD1/2024/10/1061.zip")),
    building_row("Poznań", (52.2919, 52.5094), (16.7315, 17.0718), boundary=boundary("poznan"),
                 fetch=lod1_archive("https://opendata.geoportal.gov.pl/InneDane/Budynki3D/LOD1/2024/30/3064.zip")),
    # Riga (2026-10-25): the State Land Service's cadastre buildings through GEO RĪGA (licence not stated by the
    # publisher), surfacenumberoffloors × 3 m (69 of 83 in the Old Town), held to the city's boundary.
    building_row("Riga", (56.8573, 57.0862), (23.9325, 24.3245), boundary=boundary("riga"), fetch=lambda r: footprints(arcgis(
        "https://georiga.lv/server/rest/services/VZD_kadastrs_opendata/MapServer/3", r, ["surfacenumberoffloors"], oid="gid"),
        lambda p: (number(p.get("surfacenumberoffloors")) or 0) * 3 or None)),
    # The City of Espoo's buildings (Espoon rakennukset, CC BY 4.0 through HRI): KERROSLUKU floors × 3 m, its own WFS.
    building_row("Espoo", (59.901, 60.364), (24.4992, 24.9424), boundary=boundary("espoo"), fetch=espoo),
    # Tallinn: Maa- ja Ruumiamet's Eesti 3D hoonete LoD1 (CC BY 4.0), the municipality's one CityGML file (733 MB,
    # streamed out of its 27 MB zip), each footprint drawn as triangles (estonia_lod1_buildings).
    building_row("Tallinn", (59.3518, 59.5916), (24.5501, 24.9263), boundary=boundary("tallinn"), fetch=lod1_archive(
        "https://geoportaal.maaamet.ee/index.php?lang_id=1&plugin_act=otsing&andmetyyp=hooned_lod1&dl=1"
        "&f=hooned_lod1-Tallinn-citygml.zip&page_id=833", reader=estonia_lod1_buildings)),
    # Christchurch City Council's Building layer (CC BY 4.0): drvLiDARHeight, metres, 2019 lidar (94 of 96 at Cathedral
    # Square); the tiled box is the urban area. Wellington City Council's footprints (CC BY 4.0): approx_hei, metres.
    building_row("Christchurch", (-43.6, -43.42), (172.5, 172.78), fetch=lambda r: footprints(arcgis(
        "https://gis.ccc.govt.nz/server/rest/services/OpenData/Property/FeatureServer/12", r,
        ["drvLiDARHeight"], oid="BuildingID"), lambda p: number(p.get("drvLiDARHeight")))),
    building_row("Wellington", (-41.3624, -41.1435), (174.6131, 174.8955), boundary=boundary("wellington"), fetch=lambda r: footprints(arcgis(
        "https://gis.wcc.govt.nz/arcgis/rest/services/PropertyAndBoundaries/BuildingFootprints/MapServer/0", r,
        ["approx_hei"], oid="OBJECTID_1"), lambda p: number(p.get("approx_hei")))),
    # Catania: Regione Siciliana's 1:2000 DBTRS volumetric units (SITR; no licence on the layer, the geoportal's CC BY 4.0
    # footer, credited "Regione Siciliana - SITR"): eaves minus ground, metres (399 of 399 at Piazza del Duomo).
    building_row("Catania", (37.3583, 37.5624), (14.9358, 15.1258), boundary=boundary("catania"), fetch=lambda r: footprints(arcgis(
        "https://map.sitr.regione.sicilia.it/gis/rest/services/ctn_2000/cart2000_dbtrs/MapServer/30", r,
        ["quotaterra", "quotagrond"]), lambda p: (number(p.get("quotagrond")) or 0) - (number(p.get("quotaterra")) or 0)
        if number(p.get("quotagrond")) and number(p.get("quotaterra")) is not None else None)),
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
    building_row("San Jose", (37.1231, 37.4691), (-122.0463, -121.5858), lambda r: footprints(arcgis(
        "https://maps.santaclaracounty.gov/server/rest/services/opendata/SCCGISHUB/MapServer/40", r, ["Building_H"]),
        lambda p: p["Building_H"] * 0.3048 if p.get("Building_H") else None)),
    # Regione Liguria's region-wide NC5 3D footprints (CC BY 4.0; Torre Piacentini 99.9 m); where NC5
    # has nothing (Pegli, Voltri) the cell falls to OSM. The box is Genoa's cities.json area.
    building_row("Genoa", (44.371, 44.52), (8.6657, 9.0956), liguria),
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
    # The overnight run's NRCan rows (2026-10-21; Automatically Extracted Buildings, Open Government Licence – Canada,
    # heightmax in metres): Markham from the GTA file (before Toronto's box, which it touches), Hamilton, London (Ontario),
    # Winnipeg (its 2019 file covers the north of the city: the rest stays OSM, the next source a cell asks), Québec, each
    # held to its OSM boundary; Halifax's Regional Centre, the tiled box (the Inland file covers the peninsula).
    building_row("Markham", (43.798, 43.9626), (-79.4288, -79.1702), boundary=boundary("markham"),
                 fetch=geopackage(NRCAN_GTA, "heightmax")),
    building_row("Hamilton", (43.0505, 43.4707), (-80.2486, -79.6221), boundary=boundary("hamilton-on"),
                 fetch=geopackage(NRCAN_HAMILTON, "heightmax")),
    building_row("London (Canada)", (42.8245, 43.0731), (-81.3907, -81.107), boundary=boundary("london-ca"),
                 fetch=geopackage(NRCAN_LAKE_ERIE, "heightmax")),
    building_row("Winnipeg", (49.7135, 49.9941), (-97.3492, -96.9565), boundary=boundary("winnipeg"),
                 fetch=geopackage(NRCAN_WINNIPEG, "heightmax")),
    building_row("Québec", (46.7277, 46.9807), (-71.5493, -71.1336), boundary=boundary("quebec"),
                 fetch=geopackage(NRCAN_QUEBEC, "heightmax")),
    building_row("Halifax", (44.62, 44.705), (-63.63, -63.53), fetch=geopackage(NRCAN_HALIFAX, "heightmax")),
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
    # roof edge (plateau_buildings). Each box is the city's tiled area (cities.json), no other tiled city near it; widened on
    # 2026-10-11 from the going-out core to the whole city and the old core's cells (cities.json's boundary, which holds the
    # build). Tokyo: the 23 wards' files (688 meshes, 35.517–35.825 N, 139.5625–139.925 E), which cover the wards whole.
    # Osaka: 大阪市's (270 meshes, 34.583–34.775 N, 135.375–135.6 E); its land west of 135.375 E (about 1 km² of
    # Konohana-ku) and the old core's cells past the city line (Higashiōsaka, Moriguchi) have no file and fall to OSM.
    building_row("Tokyo", (35.5209, 35.8175), (139.5629, 139.9189), plateau([f"131{k:02d}" for k in range(1, 24)])),
    building_row("Osaka", (34.5861, 34.7689), (135.3434, 135.602), plateau(["27100"])),
    # Wired 2026-10-05 (the heights survey of 2026-10-04): three zipped Shapefiles (shapefile()), each box the city's
    # tiled area (cities.json), held to the municipality its file covers. Buenos Aires: Tejido Urbano (Subsecretaría de
    # Planeamiento, data to 2021, CC BY 2.5 AR; WGS84): altura, metres, on all 1,386,616 volumes; two read 280 and
    # 830 m, over the city's tallest (Alvear Tower, 235 m): over 250 is the flagged guess. Held to CABA's perimetro
    # (CC BY 2.5 AR): Vicente López and the partidos past General Paz stay OSM. Both boxes widened on 2026-10-11 to the
    # whole city's (cities.json, buenos-aires-whole and milan-whole), which each file covers; still held to the municipality.
    building_row("Buenos Aires", (-34.7059, -34.5265), (-58.5315, -58.3351), boundary=boundary("buenos-aires"),
                 fetch=shapefile(BUENOS_AIRES_TEJIDO, "tejido", "buenos-aires", ("altura",),
                                 lambda p, f: p["altura"] if p["altura"] and p["altura"] <= 250 else None)),
    # Milan: the Comune's DBT 2020 (CC BY 4.0; RDN2008 / UTM 32N), 237,743 unità volumetriche (dbt_height). Held to the
    # Comune's confine (CC BY 4.0): Sesto San Giovanni, Bresso, Corsico and the rest of the box stay OSM.
    building_row("Milan", (45.3867, 45.546), (9.0408, 9.2782), boundary=boundary("milan"),
                 fetch=shapefile(MILAN_DBT, "UN_VOL", "milan", DBT_FIELDS, dbt_height)),
    # Rome: Regione Lazio's DBGT 2020 for Roma (ETRS89 / UTM 33N; 386,769 volumes, 78 % surveyed 2003, the rest 2014 and
    # 2023; the licence asks that the source be named), the same rule. Monuments drawn as other classes (the Colosseum)
    # are not volumes. Held to Roma Capitale (ISTAT's 2025 limits, CC BY 4.0): the Vatican, which the file leaves out,
    # stays OSM.
    building_row("Rome", (41.6556, 42.1411), (12.2344, 12.8558), boundary=boundary("rome"),
                 fetch=shapefile(LAZIO_DBGT, "UN_VOL", "rome", DBT_FIELDS, dbt_height, headers=lazio_token)),
]



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



# boundaries/ (2026-10-10): each neighbourhood's real outline, on places/'s 1/25° cells. A polygon is written once and
# whole in the cell of its label point: the places/ point of the same name inside it (that place's name is then
# written), else a point inside it. Simplified over the whole set of a source, so neighbours keep sharing their
# edge, then rounded on the absolute 1e-5° grid; rings as buildings-v2/'s, from the cell's corner (key / 25).
# Needs shapely ≥ 2.1 (coverage_simplify), pyproj and pyshp (requirements.txt); the other layers need none.
BOUNDARY_TOLERANCE = 5        # metres
BOUNDARY_UNIT = 100_000       # 1e-5° steps

def area_row(city, source, fetch, name, level, keep=None, dissolve=None, key=None):
    """One source of a city's neighbourhoods. `fetch(rect)` → [(properties, shapely geometry in lon/lat)] for the
    tiled area's box; `name` a field or a function of the properties (a falsy name leaves the feature out);
    `level` the source's own word for the unit, or a function of the properties; `keep` a filter; `dissolve` a
    function of the properties whose equal values are merged into one feature (True: by name); `key` the
    environment variable holding the source's API key (unset: the city gets no boundaries, said once)."""
    if key: SECRET_ENVS.append(key)
    return dict(city=city, source=source, fetch=fetch, name=name, level=level, keep=keep, dissolve=dissolve, key=key)

def _crs_transform(geoms, crs):
    """lon/lat geometries from `crs` (anything pyproj reads: "EPSG:3857", a .prj's WKT); WGS84 and its kin as they are."""
    if crs is None: return geoms
    import numpy, pyproj, shapely
    source = pyproj.CRS.from_user_input(crs)
    code = source.to_epsg(min_confidence=70)
    if code in (4326, 4258, 6668, 4612): return geoms   # WGS84, ETRS89, JGD2011, JGD2000: within a metre
    # An ESRI .prj names no datum shift (Turin's Monte Mario): its EPSG code brings PROJ's.
    t = pyproj.Transformer.from_crs(f"EPSG:{code}" if code else source, "EPSG:4326", always_xy=True)
    return [shapely.transform(g, lambda xy: numpy.column_stack(t.transform(xy[:, 0], xy[:, 1]))) for g in geoms]

def _feature_crs(collection):
    name = ((collection.get("crs") or {}).get("properties") or {}).get("name") or ""
    code = ((collection.get("crs") or {}).get("properties") or {}).get("code")
    if code: name = f"EPSG:{code}"
    return None if not name or re.search(r"CRS84|EPSG:+(4326|4258)$", name) else name

def _shaped(features, crs=None):
    """GeoJSON features → [(properties, shapely geometry)], polygons only, in lon/lat."""
    import shapely.geometry
    out = [(f.get("properties") or {}, shapely.geometry.shape(f["geometry"])) for f in features
           if (f.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon")]
    return list(zip([p for p, _ in out], _crs_transform([g for _, g in out], crs)))

def geojson_source(url, crs=None):
    """One GET of a FeatureCollection (an Opendatasoft export, a WFS, Socrata, a plain file); its own "crs" read."""
    def fetch(rect):
        root = get_json(url)
        return _shaped(root.get("features") or [], crs or _feature_crs(root))
    return fetch

def ogc_source(url):
    """OGC API Features items, its "next" links followed."""
    def fetch(rect):
        features, at = [], url
        while at:
            page = get_json(at)
            features += page.get("features") or []
            at = next((l["href"] for l in page.get("links") or [] if l.get("rel") == "next" and page.get("features")), None)
        return _shaped(features)
    return fetch

def arcgis_source(url, fields, where="1=1", oid="OBJECTID"):
    """An ArcGIS REST layer over the area's box, paged on its OID field (arcgis())."""
    return lambda rect: _shaped(arcgis(url, rect, fields, where, oid))

def wfs_source(url, type_name, key=None):
    """A WFS 2.0 layer over the area's box, as GeoJSON in EPSG:4326 (wfs()); `key` the environment variable whose
    value fills the URL's {key}."""
    def fetch(rect):
        at = url.replace("{key}", urllib.parse.quote(os.environ.get(key, "").strip(), safe="")) if key else url
        return _shaped(wfs(at, {"TYPENAMES": type_name, "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326"}, rect))
    return fetch

def _shapefile_features(members, encoding=None, crs=None):
    """Every polygon record of each Shapefile in a zip's members, holes and parts kept (pyshp), in lon/lat; `crs`
    for a Shapefile that comes without its .prj (Palermo's)."""
    import shapefile, shapely.geometry
    out = []
    for shp in (m for m in members if m.lower().endswith(".shp")):
        part = lambda x: next((io.BytesIO(members[m]) for m in members if m.lower() == shp[:-4].lower() + x), None)
        cpg, prj = part(".cpg"), part(".prj")
        code = encoding or (cpg.read().decode("ascii", "replace").strip() if cpg else "latin-1")
        reader = shapefile.Reader(shp=part(".shp"), shx=part(".shx"), dbf=part(".dbf"), encoding=code)
        records = [(r.record.as_dict(), shapely.geometry.shape(r.shape.__geo_interface__)) for r in reader.iterShapeRecords()
                   if r.shape.shapeType in (5, 15, 25) and r.shape.points]
        geoms = _crs_transform([g for _, g in records], prj.read().decode("latin-1") if prj else crs)
        out += list(zip([p for p, _ in records], geoms))
    return out

def shapefile_source(*urls, encoding=None, crs=None):
    """Zipped Shapefiles, each fetched whole (a few hundred KB)."""
    return lambda rect: [f for url in urls for f in _shapefile_features(_zip_members(get(url)), encoding, crs)]

def posted_shapefile_source(url, form, encoding=None):
    """A zipped Shapefile answered to a form POST (Seoul's portal)."""
    return lambda rect: _shapefile_features(_zip_members(get(url, urllib.parse.urlencode(form).encode())), encoding)

def ckan_wkt_source(url, field):
    """A CKAN DataStore table whose `field` holds WKT (Athens)."""
    def fetch(rect):
        import shapely.wkt
        records = get_json(url)["result"]["records"]
        return [(r, shapely.wkt.loads(r[field])) for r in records if r.get(field)]
    return fetch

def csv_geojson_source(url, field="geo_shape"):
    """A CSV file whose `field` holds each row's GeoJSON geometry (Grenoble's: its data4citizen GeoJSON export
    answers an empty collection one request in two)."""
    def fetch(rect):
        rows = csv.DictReader(io.StringIO(get(url).decode("utf-8-sig")))
        return _shaped([{"properties": r, "geometry": json.loads(r[field])} for r in rows if r.get(field)])
    return fetch

def datagovsg_source(dataset):
    """data.gov.sg: poll-download answers a signed URL, valid an hour, for the GeoJSON."""
    def fetch(rect):
        link = get_json(f"https://api-open.data.gov.sg/v1/public/api/datasets/{dataset}/poll-download")["data"]["url"]
        return _shaped(get_json(link).get("features") or [])
    return fetch

def oslo_source(rect):
    """Oslo's 98 delbydeler: Geonorge's grunnkretser, each named by Oslo kommune's key (an XLSX: grunnkrets → delbydel)."""
    members = _zip_members(get("https://nedlasting.geonorge.no/geonorge/Basisdata/Grunnkretser/GeoJSON/Basisdata_03_Oslo_4258_Grunnkretser_GeoJSON.zip"))
    root = json.loads(next(v for k, v in members.items() if k.endswith("Grunnkrets_GeoJSON.geojson")).decode("utf-8-sig"))
    rows = xlsx_rows(get("https://www.oslo.kommune.no/get-file/2563152/7433a6d80e58a5128319f5e16325d3b94bda0c8e26d023d3fb19108c5c21040c"))
    head = rows[0]
    delbydel = {str(r[head.index("Grunnkrets_2017_nr")]).zfill(4): r[head.index("Delbydel_2017_navn")] for r in rows[1:]
                if len(r) == len(head) and r[head.index("Grunnkrets_2017_nr")]}
    for f in root["features"]: f["properties"]["delbydel"] = delbydel.get(str(f["properties"].get("grunnkretsnummer", ""))[4:])
    return _shaped(root["features"])

def estat_source(prefecture, wards):
    """e-Stat's 2020 census small areas (chōme), one zipped Shapefile per ward (JGD2011, cp932)."""
    url = "https://www.e-stat.go.jp/gis/statmap-search/data?dlserveyId=A002005212020&code={}{}&coordSys=1&format=shape&downloadType=5&datum=2011"
    return shapefile_source(*(url.format(prefecture, w) for w in wards), encoding="cp932")

def iris_head(name):
    """An IRIS name's part before its first hyphen between two names: "Ariane-Monzie" Ariane, "Saint-Roch-Ricolfi"
    Saint-Roch (a "Saint-", "Sainte-", "Notre-" or compass "Nord-Est" kept whole), "Cimiez" Cimiez. A numbered or
    "partie" piece is its whole: "Encagnane 2" Encagnane, "Arbois Partie Est" Arbois (Aix's); else a compass tail
    is dropped: "Murigny Sud-Est", "Saint-Rémi Nord Est et Sud", "Cernay-Centre" (Reims's), not "Croix du Sud"'s."""
    whole = re.sub(r"\s+(?:\d+|[Pp]artie\b.*)$", "", name)
    tail = whole == name and re.match(r"^(.*?)((?:[\s-]+(?:Nord|Sud|Est|Ouest|Centre|et))+)$", name)
    if tail and tail[1]:
        whole = tail[1]
        if whole.split()[-1] in ("du", "de", "des"): whole += re.match(r"[\s-]+\w+", tail[2])[0]
    name = whole
    m = re.match(r"^(.+?(?<!\bSaint)(?<!\bSainte)(?<!\bNotre)(?<!\bNord)(?<!\bSud))-(?=[A-ZÉÈ])", name)
    return m[1].strip() if m else name

def iris_source(insee):
    """IGN's IRIS of a commune (Géoplateforme WFS), each with "quartier": the head of its name (iris_head) where two
    IRIS or more of the commune share it ("Ariane-Monzie" and "Ariane-Ripert" are Ariane, "Cimiez" and "Cimiez-Valrose"
    Cimiez), else its own name. Dissolved by it, for a city that publishes no quartiers of its own (Nice, Ajaccio,
    Annecy, Aix-en-Provence, Biarritz, Rouen, Reims, Clermont-Ferrand, Caen, Amiens, Le Mans, Orléans)."""
    def fetch(rect):
        found = geojson_source(IGN_WFS + "?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES=STATISTICALUNITS.IRIS:contours_iris"
                               "&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326&CQL_FILTER=" + urllib.parse.quote(f"code_insee='{insee}'"))(rect)
        heads = [iris_head(str(p.get("nom_iris") or "")) for p, _ in found]
        for (p, _), head in zip(found, heads): p["quartier"] = head if heads.count(head) > 1 else p.get("nom_iris")
        return found
    return fetch

def osm_area_source(levels):
    """OpenStreetMap from the area's extract: administrative boundaries at the admin_levels (integers) or place
    polygons of the kinds (strings) in `levels`, in order: a later level's polygon is kept only where its inner
    point lies in no polygon kept before (Manila's districts, then the barangays of the cities around)."""
    def fetch(rect):
        import shapely, shapely.geometry
        found, (boundaries, places) = {}, _osm.areas(rect)
        for b in boundaries:
            if b["admin_level"] in levels and b["geometry"]:
                found.setdefault(b["admin_level"], []).append(({"name": b["name"], "level": b["admin_level"]}, shapely.geometry.shape(b["geometry"])))
        for tags, geometry in places:
            name = str(tags.get("name", "")).strip()
            if tags.get("place") in levels and name and tags.get("boundary") != "administrative":
                found.setdefault(tags["place"], []).append(({"name": name, "level": tags["place"]}, shapely.geometry.shape(geometry)))
        out = []
        for level in levels:
            kept = shapely.union_all([g for _, g in out]) if out else None
            for p, g in found.get(level, ()):
                if kept is not None and kept.contains(g.representative_point()): continue
                # One area mapped twice (a way and a relation of one name, Jerusalem's Har Tsiyon): the first kept.
                if any(q["name"] == p["name"] and h.intersects(g.representative_point()) for q, h in out): continue
                out.append((p, g))
        return out
    return fetch

def roman_part(name):
    """Athens's parts: "ΠΑΓΚΡΑΤΙ ΙΙ", "ΚΑΤΩ ΠΑΤΗΣΙΑ II", "ΠΡΟΜΠΟΝΑ2" are Pangrati, Kato Patisia, Prompona."""
    return re.sub(r"\s*(?:\b[IΙ]{1,3}|\d+)$", "", name).strip()

def logical_hebrew(name):
    """Tel Aviv's names keep a closing mark where right-to-left display shows it, at the start: "'נאות אפקה א" is
    Neot Afeka Alef, "נאות אפקה א'", and "(יפו ד' (גבעת התמרים" is "יפו ד' (גבעת התמרים)"."""
    lead = re.match(r"^[\"'(]+", name)
    if not lead: return name
    return name[lead.end():].strip() + lead[0][::-1].replace("(", ")")

def machi(p):
    """A Japanese chōme's town: 丸の内一丁目 → 丸の内."""
    return re.sub(r"[一二三四五六七八九十〇]+丁目$", "", p["S_NAME"] or "").strip()

ABS_SAL = "https://geo.abs.gov.au/arcgis/rest/services/ASGS2021/SAL/MapServer/0"
IGN_WFS = "https://data.geopf.fr/wfs/ows"
LINZ_WFS = "https://data.linz.govt.nz/services;key={key}/wfs"

# One row per source, from compromise's docs/research/neighbourhood_boundaries.csv (2026-10-10): the city's portal
# at the level people name, else a national statistics or mapping agency, else OpenStreetMap from the extract.
# Owner, 2026-10-10: every surveyed source is used (Chicago, São Paulo's CC BY-SA, Geneva included); Perth from
# ABS, not Landgate; Auckland from LINZ only with LINZ_API_KEY. The source key is what sources/ and each item name.
AREA_SOURCES = [
    area_row("Paris", "Paris", geojson_source("https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/quartier_paris/exports/geojson"),
             "l_qu", "quartier"),
    # The petite couronne: people name the commune (Montreuil, Vincennes), not its quartiers.
    area_row("Paris", "IGN", geojson_source(IGN_WFS + "?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES=ADMINEXPRESS-COG-CARTO.LATEST:commune"
             "&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326&CQL_FILTER=" + urllib.parse.quote("code_insee_du_departement IN ('92','93','94')")),
             "nom_officiel", "commune"),
    area_row("Lyon", "Lyon", geojson_source("https://data.grandlyon.com/geoserver/ville-de-lyon/ows?SERVICE=WFS&VERSION=2.0.0&request=GetFeature"
             "&typename=ville-de-lyon:vdl_vie_citoyenne.perimetre_de_quartier&outputFormat=application/json&SRSNAME=EPSG:4326"), "nom", "quartier"),
    area_row("Marseille", "Marseille", geojson_source("https://data.ampmetropole.fr/api/explore/v2.1/catalog/datasets/a7104f3c-e487-4af3-82ad-6197cedfaeb1/exports/geojson"),
             "nom", "quartier"),
    area_row("Toulouse", "Toulouse", geojson_source("https://data.toulouse-metropole.fr/api/explore/v2.1/catalog/datasets/"
             "recensement-population-2020-grands-quartiers-familles/exports/geojson?select=grd_quart,lib_grd_quart,geo_shape"),
             "lib_grd_quart", "grand quartier"),
    # Neither the city nor the Métropole (data.ampmetropole.fr has Marseille's quartiers only) publishes Aix's: its 54
    # IRIS grouped into 39 (iris_source; Encagnane 1 to 4, Jas 1 to 6, Couronne Est, Nord, Sud are one each).
    area_row("Aix-en-Provence", "Aix-en-Provence", iris_source("13001"), "quartier", "IRIS", dissolve=True),
    # Neither the city nor the CAPA publishes quartiers, and OpenStreetMap maps none: its 24 IRIS (iris_source).
    area_row("Ajaccio", "Ajaccio", iris_source("2A004"), "quartier", "IRIS", dissolve=True),
    # Amiens Métropole's "quartiers de la ville d'Amiens" WFS (geodata.amiens-metropole.com) was not reached (2026-10-11): its 58 IRIS
    # grouped into 23 (iris_source; Étouvie's 4, Saint-Honoré's 5), "Amiens-Val d'Avre 1" to "3" named Val d'Avre.
    area_row("Amiens", "Amiens", iris_source("80021"), lambda p: "Val d'Avre" if p["quartier"] == "Amiens" else p["quartier"], "IRIS", dissolve=True),
    # Without the filter the API also answers every past version of each wijk.
    area_row("Amsterdam", "Amsterdam", geojson_source("https://api.data.amsterdam.nl/v1/gebieden/wijken/?_format=geojson&_pageSize=2000&eindGeldigheid[isnull]=true"),
             "naam", "wijk"),
    # The Ville d'Angers's 10 quartiers, those of its conseils de quartier (Belle-Beille, Lac de Maine, Doutre - Saint-Jacques - Nazareth).
    area_row("Angers", "Angers", geojson_source("https://data.angers.fr/api/explore/v2.1/catalog/datasets/ccq_007/exports/geojson?select=nom_cq,geo_shape"),
             "nom_cq", "quartier"),
    # The commune nouvelle (Annecy-le-Vieux, Cran-Gevrier, Meythet, Pringy, Seynod since 2017) publishes no quartiers:
    # its 51 IRIS grouped into 47 (iris_source; the three Novel are one, the three Meythet one).
    area_row("Annecy", "Annecy", iris_source("74010"), "quartier", "IRIS", dissolve=True),
    area_row("Antwerp", "Antwerp", geojson_source("https://geodata.antwerpen.be/arcgissql/rest/services/P_Portal/portal_publiek2/MapServer/97/query"
             "?where=1%3D1&outFields=*&outSR=4326&f=geojson"),
             lambda p: re.sub(r"^Antwerpen - ", "", (p.get("wijknaam") or "").strip()), "wijk", keep=lambda p: (p.get("wijknaam") or "").strip() != "Schelde"),
    # Parts of one neighbourhood ("ΠΑΓΚΡΑΤΙ Ι", "ΠΑΓΚΡΑΤΙ ΙΙ") merged; friendly_name is filled for 25 of 144, some wrongly.
    area_row("Athens", "Athens", ckan_wkt_source("https://opendata.cityofathens.gr/api/3/action/datastore_search?resource_id=0283af02-7783-4688-897b-7153da7ed2fd&limit=500",
             "polygon_geo"), lambda p: roman_part(p.get("name") or ""), "neighbourhood", keep=lambda p: str(p.get("status")) == "1", dissolve=True),
    area_row("Auckland", "Auckland", wfs_source(LINZ_WFS, "layer-113764", "LINZ_API_KEY"), "name", "suburb", key="LINZ_API_KEY"),
    # The Ville d'Avignon's 9 mairies de quartier, on DataSud; named as it names them ("Avignon Est", "Avignon Montfavet").
    area_row("Avignon", "Avignon", geojson_source("https://www.datasud.fr/geoserver/avignon/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=avignon:avignon_mairie_quartier&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"), "libmairie", "mairie de quartier"),
    area_row("Barcelona", "OSM", osm_area_source((10,)), "name", None),   # the portal is behind a bot check; OSM's barris are the city's own (2018)
    area_row("Basel", "Basel", geojson_source("https://data.bs.ch/api/explore/v2.1/catalog/datasets/100042/exports/geojson"), "wov_name", "Wohnviertel"),
    # Bayonne's 26 quartiers officiels (Petit Bayonne, Saint-Esprit, Marracq); its 4 conseils de quartier are too coarse.
    area_row("Bayonne", "Bayonne", geojson_source("https://www.bayonne.fr/fileadmin/open-data/geojson/za_quartier.geojson"), "nom", "quartier"),
    area_row("Belgrade", "OSM", osm_area_source((8,)), "name", None),
    area_row("Berlin", "Berlin", geojson_source("https://gdi.berlin.de/services/wfs/alkis_ortsteile?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=alkis_ortsteile:ortsteile&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"), "nam", "Ortsteil"),
    # The Ville de Besançon's 13 quartiers (Battant, Chaprais - Cras, Planoise), on Grand Besançon Métropole's map server; no licence
    # stated. Its open data portal lists the streets by quartier only.
    area_row("Besançon", "Besançon", geojson_source("https://sig-portail.grandbesancon.fr/server/rest/services/Visionneuse/Limites_administratives/MapServer/8"
             "/query?where=1%3D1&outFields=NOM&outSR=4326&f=geojson"), "NOM", "quartier"),
    # Neither the city nor the Pays Basque agglomeration publishes quartiers: its 12 IRIS (iris_source), none merged.
    area_row("Biarritz", "Biarritz", iris_source("64122"), "quartier", "IRIS", dissolve=True),
    # GeoBilbao's barrios (Deustu's, Abando's; the open data catalogue has them as a CSV without outlines); each distrito's
    # rural rest, "DISEMINADO", left out: 40. Without srsName it answers in UTM.
    area_row("Bilbao", "Bilbao", geojson_source("https://api.bilbao.eus/maps?gsservice=ls&gsrequest=wfs&service=WFS&version=1.0.0&request=GetFeature"
             "&idservice=poblacion_territorio&typeName=poblacion_territorio:Barrios&outputFormat=application/json&srsName=EPSG:4326&maxFeatures=100000"),
             "Nombre", "barrio", keep=lambda p: (p.get("Nombre") or "").strip() != "DISEMINADO"),
    area_row("Bogotá", "Bogotá", geojson_source("https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/ordenamientoterritorial/unidadplaneamientozonal"
             "/MapServer/0/query?where=1%3D1&outFields=CODIGO_UPZ,NOMBRE&outSR=4326&f=geojson"), "NOMBRE", "UPZ"),
    # The Comune's 90 aree statistiche (Bolognina's Arcoveggio, Cirenaica, Irnerio), "GALVANI-1" and "GALVANI-2" one.
    area_row("Bologna", "Bologna", geojson_source("https://opendata.comune.bologna.it/api/explore/v2.1/catalog/datasets/aree-statistiche/exports/geojson"),
             lambda p: re.sub(r"-\d$", "", (p.get("area_statistica") or "").strip()), "area statistica", dissolve=True),
    area_row("Bordeaux", "Bordeaux", geojson_source("https://opendata.bordeaux-metropole.fr/api/explore/v2.1/catalog/datasets/se_quartvie_s/exports/geojson"
             "?select=libelle,geo_shape"), "libelle", "secteur de vie"),
    # The Planning Department's 26 neighborhoods ("not official neighborhood boundaries", it says): the ones people name.
    # Its data.boston.gov file redirects to S3 with ":443" in the URL, which urllib signs into the Host: 403.
    area_row("Boston", "Boston", geojson_source("https://gis.bostonplans.org/hosting/rest/services/Hosted/Boston_Neighborhood_Boundaries"
             "/FeatureServer/5/query?where=1%3D1&outFields=name&outSR=4326&f=geojson"), "name", "neighborhood"),
    area_row("Bratislava", "Bratislava", geojson_source("https://data.bratislava.sk/api/download/v1/items/f1daafc79a9247e8b93241aa005fbea3/geojson?layers=0"),
             "NAZOV_UTJ", "mestská časť"),
    # Brest métropole's 7 quartiers administratifs of Brest (its mairies de quartier: Bellevue, Europe, Saint-Marc); its TRIRIS are
    # unnamed. outFields=* answers nothing.
    area_row("Brest", "Brest", geojson_source("https://geo.brest-metropole.fr/arcgis/rest/services/public/GPB_LIM/MapServer/1610008/query"
             "?where=1%3D1&outFields=QUARTIERS&outSR=4326&f=geojson"), "QUARTIERS", "quartier"),
    area_row("Brisbane", "Brisbane", geojson_source("https://data.brisbane.qld.gov.au/api/explore/v2.1/catalog/datasets/suburb-boundaries/exports/geojson"),
             "suburb_name", "suburb"),
    area_row("Brussels", "Brussels", geojson_source("https://opendata.bruxelles.be/api/explore/v2.1/catalog/datasets/"
             "quartiers-du-monitoring-des-quartiers-ibsa-perspective-rbc/exports/geojson"), "namefre", "quartier"),
    area_row("Budapest", "OSM", osm_area_source((10,)), "name", None),
    area_row("Buenos Aires", "Buenos Aires", geojson_source("https://cdn.buenosaires.gob.ar/datosabiertos/datasets/innovacion-transformacion-digital/barrios/barrios.geojson"),
             "nombre", "barrio"),
    # Neither the city nor Caen la mer publishes quartiers: its 51 IRIS grouped into 45 (iris_source; IGN's "La Grace de Dieu Ouest"
    # joins La Grâce de Dieu).
    area_row("Caen", "Caen", iris_source("14118"), lambda p: p["quartier"].replace("La Grace de Dieu Ouest", "La Grâce de Dieu"), "IRIS", dissolve=True),
    # The Mairie's 12 quartiers ("quartiers politiques", not the QPV); its 63 secteurs de proximité are often a street's name.
    area_row("Cannes", "Cannes", geojson_source("https://data.cannes.fr/api/explore/v2.1/catalog/datasets/quartiers-politiques/exports/geojson"
             "?select=nom_quartier,geo_shape"), "nom_quartier", "quartier"),
    area_row("Cape Town", "Cape Town", geojson_source("https://gis.westerncape.gov.za/server2/rest/services/SpatialDataWarehouse/CoCT_Management_Boundaries"
             "/MapServer/15/query?where=1%3D1&outFields=SBRB_NAME&outSR=4326&f=geojson"), "SBRB_NAME", "major suburb"),
    area_row("Chicago", "Chicago", geojson_source("https://data.cityofchicago.org/resource/y6yq-dbs2.geojson?$limit=1000"), "pri_neigh", "neighborhood"),
    # Neither the city nor Clermont Auvergne Métropole publishes quartiers (its "Quartiers 2021" are 5 territoires): its 42 IRIS
    # (iris_source), none merged.
    area_row("Clermont-Ferrand", "Clermont-Ferrand", iris_source("63113"), "quartier", "IRIS", dissolve=True),
    area_row("Copenhagen", "Copenhagen", geojson_source("https://wfs-kbhkort.kk.dk/k101/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=k101:kvarter"
             "&outputFormat=json&SRSNAME=EPSG:4326"), "kvarternavn", "kvarter"),
    area_row("Delhi", "OSM", osm_area_source((10,)), "name", None),
    # Dijon's 9 quartiers of its ateliers de quartier; the portal left Opendatasoft (2025), its old export is a 404.
    area_row("Dijon", "Dijon", geojson_source("https://data.metropole-dijon.fr/sites/default/files/dataset/2025/06/20/8e39eea1-603a-4ad5-a937-8cc592b840a7"
             "/query.geojson"), "NOM", "quartier"),
    # The 41 Dhaka Metropolitan thanas (Gulshan, Dhanmondi, Motijheel) of BBS and OCHA ROAP's 2015 admin 3, as
    # geoBoundaries keeps it (125 MB, pinned): OCHA's current COD-AB stops at the two City Corporations, OSM maps no
    # thanas and names in Bengali. The box's edges take in 7 upazilas around (Savar, Keraniganj).
    area_row("Dhaka", "Dhaka", geojson_source("https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/BGD/ADM3"
             "/geoBoundaries-BGD-ADM3.geojson"), "shapeName", "thana"),
    # Electoral divisions: "Beaumont A" to "Beaumont F" are Beaumont.
    area_row("Dublin", "Dublin", geojson_source("https://data.smartdublin.ie/dataset/2afc02f8-314b-40eb-91f0-2955f84bb912/resource/a19c6a3e-bbc8-4f20-b178-65de8e6950f1"
             "/download/electoral-divisions_cso2.shp.geojson"), lambda p: re.sub(r" [A-F]$", "", (p.get("Pllng_D") or "").strip()), "electoral division", dissolve=True),
    area_row("Edinburgh", "Edinburgh", geojson_source("https://edinburghcouncilmaps.info/arcgis/rest/services/Misc/INSPIRE/MapServer/27/query"
             "?where=1%3D1&outFields=NATURALCOM&outSR=4326&f=geojson"), "NATURALCOM", "natural neighbourhood"),
    # ISTAT's sub-municipal areas (Rifredi, Campo di Marte); the 1995 rioni are older and misspelt. In EPSG:3003.
    area_row("Florence", "Florence", geojson_source("https://datigis.comune.fi.it/json/aree_elementari_2021.json"), "denae2021", "area elementare"),
    # The 46 Stadtteile (Bahnhofsviertel, Sachsenhausen-Nord, Bornheim); the server refuses "application/json".
    area_row("Frankfurt", "Frankfurt", geojson_source("https://geowebdienste.frankfurt.de/WFS_Stadtgebietsgliederung?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=Stadtgebietsgliederung:Stadtteile&SRSNAME=EPSG:4326&OUTPUTFORMAT=GEOJSON"), "STT_NAME", "Stadtteil"),
    area_row("Geneva", "Geneva", geojson_source("https://vector.sitg.ge.ch/arcgis/rest/services/Hosted/OCS_SECTEURS_STATISTIQUES/MapServer/0/query"
             "?where=no_com_federal%3D6621&outFields=nom_secteur&outSR=4326&f=geojson"), "nom_secteur", "secteur statistique"),
    area_row("Ghent", "Ghent", geojson_source("https://data.stad.gent/api/explore/v2.1/catalog/datasets/stadswijken-gent/exports/geojson"), "wijk", "stadswijk"),
    # Two "Shared …" rows are areas two councils share, over others.
    area_row("Glasgow", "Glasgow", geojson_source("https://www.mapping.glasgow.gov.uk/arcgis_web/rest/services/OPEN_DATA/Community_Councils/MapServer/0/query"
             "?where=1%3D1&outFields=AREANAME&outSR=4326&f=geojson"), "AREANAME", "community council area",
             keep=lambda p: not (p.get("AREANAME") or "").strip().startswith("Shared")),
    area_row("Gothenburg", "Gothenburg", geojson_source("https://geodata.scb.se/geoserver/stat/wfs?service=WFS&request=GetFeature&version=2.0.0&typeNames=stat:RegSO_2025"
             "&outputFormat=application/json&srsName=EPSG:4326&CQL_FILTER=" + urllib.parse.quote("kommunkod='1480'")), "regsonamn", "RegSO"),
    # The Ville de Grenoble's 22 unions de quartier (its 6 secteurs are too coarse), from the dataset's CSV.
    area_row("Grenoble", "Grenoble", csv_geojson_source("https://data.metropolegrenoble.fr/sites/default/files/dataset/2023/02/22/0dcfa54a-6dad-4a31-af19-d2baaef4320e"
             "/csv_gen_0dcfa54a_6dad_4a31_af19_d2baaef4320e_63f63698555a9.csv"), "sdec_libel", "union de quartier"),
    area_row("Hamburg", "Hamburg", ogc_source("https://api.hamburg.de/datasets/v1/verwaltungsgrenzen/collections/stadtteile/items?f=json&limit=1000"),
             "stadtteil_name", "Stadtteil"),
    area_row("Helsinki", "Helsinki", geojson_source("https://kartta.hel.fi/ws/geoserver/avoindata/wfs?service=WFS&version=2.0.0&request=GetFeature"
             "&typeNames=avoindata:Piirijako_osaalue&outputFormat=application/json&srsName=EPSG:4326"), "nimi_fi", "osa-alue"),
    area_row("Istanbul", "OSM", osm_area_source((8,)), "name", None),   # İBB publishes the mahalle as points only
    area_row("Jerusalem", "OSM", osm_area_source(("neighbourhood", "quarter", "suburb")), "name", None),   # CBS's statistical areas carry no names
    area_row("Johannesburg", "Johannesburg", arcgis_source("https://services3.arcgis.com/GMycIhSIBQnnjV35/arcgis/rest/services/Census_2011_Sub_Places_of_South_Africa/FeatureServer/0",
             ["SP_NAME"], "MN_NAME='City of Johannesburg'"), "SP_NAME", "sub place"),
    area_row("Kraków", "OSM", osm_area_source((11,)), "name", None),
    area_row("Kuala Lumpur", "OSM", osm_area_source(("neighbourhood", "quarter", "suburb")), "name", None),
    area_row("Kyiv", "OSM", osm_area_source((7, 10)), "name", None),
    # The Ville de La Rochelle's 19 comités de quartier (its 2012 cut; Port-Neuf, Les Minimes), on the Agglomération's portal;
    # its 4 conseils de secteur are too coarse.
    area_row("La Rochelle", "La Rochelle", geojson_source("https://opendata.agglo-larochelle.fr/d4c/api/records/2.0/downloadfile/"
             "format=geojson&resource_id=b55cda71-088c-4ea4-8df8-69fc80c7b9c8"), "cq_nom", "comité de quartier"),
    # The Ayuntamiento's barrios (Vegueta, Triana, Guanarteme); each distrito's rural rest, "DISEMINADO", left out: 112.
    area_row("Las Palmas", "Las Palmas", geojson_source("https://sit.laspalmasgc.es/server/rest/services/opendata/barrios/MapServer/0/query"
             "?where=1%3D1&outFields=NUCL_DS_NOMBRE&outSR=4326&f=geojson"), "NUCL_DS_NOMBRE", "barrio",
             keep=lambda p: (p.get("NUCL_DS_NOMBRE") or "").strip() != "DISEMINADO"),
    # The Ville du Havre's 20 conseils de quartier (Sanvic, Caucriauville, Danton); the port, "Hors conseil de quartier", is left out.
    # Every format of the portal is a zip; the shapefile's is a zip in it.
    area_row("Le Havre", "Le Havre", lambda rect: _shapefile_features(_zip_members(_zip_members(get(
             "https://data.lehavreseinemetropole.fr/api/v1/file/data/90/CONSEIL_QUARTIER/shp"))["OD.CONSEIL_QUARTIER.shp.zip"])),
             "NOM", "conseil de quartier", keep=lambda p: p.get("NUM") != 0),
    # Le Mans Métropole publishes 8 conseils de quartier ("Nord Est", "Sud Ouest") and its IRIS in capitals with codes: IGN's 71 IRIS,
    # grouped into 70 (iris_source).
    area_row("Le Mans", "Le Mans", iris_source("72181"), "quartier", "IRIS", dissolve=True),
    area_row("Lille", "Lille", geojson_source("https://data.lillemetropole.fr/geoserver/ville_lille/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=ville_lille:limite_des_quartiers_de_lille_et_de_ses_communes_associees&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"),
             "quartier", "quartier"),   # Lille's 10 quartiers, and Lomme and Hellemmes whole
    # The Ville de Limoges's 12 quartiers (Carnot, Louyat - Vigenal, La Bastide - La Brégère; IRIS aggregated for its housing
    # policy), on Limoges Métropole's map server; no licence stated. Its 57 secteurs CIL take in other communes.
    area_row("Limoges", "Limoges", geojson_source("https://sig.limoges-metropole.fr/servernf1/rest/services/_TER/ter_secteurs_habitat_consult/MapServer/4"
             "/query?where=1%3D1&outFields=quart_vdl&outSR=4326&f=geojson"), "quart_vdl", "quartier"),
    area_row("Lisbon", "Lisbon", geojson_source("https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services/Limite_Cartografia/FeatureServer/0/query"
             "?outFields=NOME&where=1%3D1&outSR=4326&f=geojson"), "NOME", "freguesia"),
    # The council's 64 wards of 2023 (Anfield, Toxteth, Penny Lane), the same as ONS's December 2025 wards.
    area_row("Liverpool", "Liverpool", wfs_source("https://geoserver.liverpool.gov.uk/geoserver/OpenData/ows", "OpenData:Wards"), "wardname", "ward"),
    area_row("London", "London", arcgis_source("https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Wards_December_2024_Boundaries_UK_BGC/FeatureServer/0",
             ["WD24NM"], "LAD24CD LIKE 'E09%'", "FID"), "WD24NM", "ward"),
    # Lorient Agglomération's 12 quartiers of Lorient (Kerentrech, Moustoir, Keryado); no licence stated. The Ville's own 5 (2020)
    # group several.
    area_row("Lorient", "Lorient", geojson_source("https://carto.lorient-agglo.bzh/server/services/Service_WMS_ISOGEO/MapServer/WFSServer?SERVICE=WFS&VERSION=2.0.0"
             "&REQUEST=GetFeature&TYPENAMES=Service_WFS_ISOGEO:Quartiers&OUTPUTFORMAT=GEOJSON&SRSNAME=EPSG:4326"), "nom", "quartier",
             keep=lambda p: p.get("insee") == "56121"),
    area_row("Los Angeles", "Los Angeles", geojson_source("https://services5.arcgis.com/7nsPwEMP38bSkCjy/arcgis/rest/services/LA_Times_Neighborhoods/FeatureServer/0/query"
             "?where=1%3D1&outFields=name&outSR=4326&f=geojson"), "name", "neighborhood"),
    area_row("Madrid", "Madrid", shapefile_source("https://geoportal.madrid.es/fsdescargas/IDEAM_WBGEOPORTAL/LIMITES_ADMINISTRATIVOS/Barrios/Barrios.zip"),
             "NOMBRE", "barrio"),
    # The Ayuntamiento's 419 barrios (El Perchel, La Trinidad, El Palo); its 11 distritos are too coarse.
    area_row("Málaga", "Málaga", geojson_source("https://datosabiertos.malaga.eu/recursos/urbanismoEInfraestructura/planimetria/callejero"
             "/da_cartografiaBarrio-4326.geojson"), "NOMBARRIO", "barrio"),
    # The City of Manila's 16 districts, then the barangays of the cities around it (Manila's own are numbered).
    area_row("Manila", "OSM", osm_area_source((8, 10)), "name", None),
    area_row("Melbourne", "Melbourne", arcgis_source(ABS_SAL, ["sal_name_2021"], "state_code_2021='2'", "objectid"),
             lambda p: re.sub(r" \(Vic\.\)$", "", p.get("sal_name_2021") or ""), "suburb"),
    # The Eurométropole de Metz's 14 quartiers of Metz (Ancienne Ville, Sablon, Borny); the same layer's 5 secteurs of
    # Montigny-lès-Metz left out.
    area_row("Metz", "Metz", geojson_source("https://maps.eurometropolemetz.eu/wfs?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES=public:fdp_adm_quar"
             "&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"), "lib", "quartier", keep=lambda p: p.get("_commid") == "57463"),
    # Two neighborhoods mapped in two parts each (Baypoint, Fair Isle) merged; one feature has no label.
    area_row("Miami", "Miami", geojson_source("https://services1.arcgis.com/CvuPhqcTQpZPT9qY/arcgis/rest/services/Miami_Neighborhoods_Shapefile"
             "/FeatureServer/0/query?where=1%3D1&outFields=LABEL&outSR=4326&f=geojson"), "LABEL", "neighborhood", dissolve=True),
    area_row("Milan", "Milan", geojson_source("https://dati.comune.milano.it/dataset/e8e765fc-d882-40b8-95d8-16ff3d39eb7c/resource/9c4e0776-56fc-4f3d-8a90-f4992a3be426"
             "/download/ds964_nil_wm.geojson"), "NIL", "NIL"),
    area_row("Montpellier", "Montpellier", geojson_source("https://data.montpellier3m.fr/sites/default/files/ressources/VilleMTP_MTP_SousQuartiers.json"),
             "name", "sous-quartier"),
    area_row("Montréal", "Montréal", geojson_source("https://donnees.montreal.ca/dataset/f38c91a1-e33f-4475-a112-3b84b1c60c1e/resource/a80e611f-5336-4306-ba2a-fd657f0f00fa"
             "/download/quartierreferencehabitation.geojson"), "nom_qr", "quartier de référence"),
    area_row("Munich", "Munich", geojson_source("https://geoportal.muenchen.de/geoserver/gsm_wfs/ows?service=WFS&version=1.0.0&request=GetFeature"
             "&typeName=gsm_wfs:vablock_stadtbezirk&outputFormat=application/json&srsName=EPSG:4326"), "sb_name", "Stadtbezirk", dissolve=True),
    # Nancy's 11 quartiers (its ateliers de vie: Plateau de Haye, Rives de Meurthe), on the Métropole du Grand Nancy's map server;
    # no licence stated. Its 5 territoires d'élus are too coarse.
    area_row("Nancy", "Nancy", geojson_source("https://geo.grandnancy.eu/server/rest/services/ADMINISTRATIF_Limites/MapServer/4/query"
             "?where=CCOCOM%3D%27395%27&outFields=NOM&outSR=4326&f=geojson"), "NOM", "quartier"),
    area_row("Nantes", "Nantes", geojson_source("https://data.nantesmetropole.fr/api/explore/v2.1/catalog/datasets/244400404_quartiers-de-la-ville-de-nantes"
             "/exports/geojson?select=nom,geo_shape"), "nom", "quartier"),
    # The 30 quartieri (Chiaia, Vomero, Posillipo) on the 2001 census sections, the newest the Comune publishes as a file.
    # Its server answers Actions' runners 403 (run 38180000936), so the file is read from a copy in this repository
    # (`areas/naples.zip`, unchanged, IODL 2.0), taken on 2026-10-12 from trasparenza.comune.napoli.it's
    # ServeAttachment.php/L/IT/D/8%252Fa%252Fe%252FD.8fbafa14e575ac594a1c/P/BLOB%3AID%3D26532/E/zip.
    area_row("Naples", "Naples", shapefile_source("https://raw.githubusercontent.com/Techneb/compromise-tiles/master/areas/naples.zip"),
             "QUART", "quartiere"),
    # ntatype 0: the residential NTAs; 5 to 9 are parks, cemeteries, airports.
    area_row("New York", "New York", geojson_source("https://data.cityofnewyork.us/resource/9nt8-h7nd.geojson?$limit=1000"), "ntaname", "NTA",
             keep=lambda p: str(p.get("ntatype")) == "0"),
    # The city publishes 6 territoires only; its 146 IRIS grouped into 131 quartiers (iris_source).
    area_row("Nice", "Nice", iris_source("06088"), "quartier", "IRIS", dissolve=True),
    # Nîmes's 56 comités de quartier (Tour Magne, Écusson's Feuchères, Pissevin), on Nîmes Métropole's ArcGIS Online, unaccented;
    # its 6 "non attribué" areas left out, its 7 conseils de quartier too coarse. One name is in mixed case, hence upper().
    area_row("Nîmes", "Nîmes", geojson_source("https://services2.arcgis.com/ghqRCL5wdHSQIHfv/arcgis/rest/services/Comit%C3%A9s_de_Quartiers/FeatureServer/1/query"
             "?where=1%3D1&outFields=COMITE_QUARTIER&outSR=4326&f=geojson"), lambda p: (p.get("COMITE_QUARTIER") or "").upper(), "comité de quartier",
             keep=lambda p: not (p.get("COMITE_QUARTIER") or "").startswith("NON ATTRIBUE")),
    # Orléans Métropole publishes the city's 6 quartiers (its mairies de proximité) only: its 45 IRIS grouped into 36 (iris_source;
    # Bourgogne's 5, Bannier's 3).
    area_row("Orléans", "Orléans", iris_source("45234"), "quartier", "IRIS", dissolve=True),
    area_row("Osaka", "Osaka", estat_source("27", [w for w in range(102, 129) if w not in (105, 110, 112)]), machi, "machi",
             dissolve=lambda p: (p["CITY"], machi(p))),
    area_row("Oslo", "Oslo", oslo_source, "delbydel", "delbydel", dissolve=True),
    # The Ottawa Neighbourhood Study's Gen 3 (116, the city's open data), in capitals; Gatineau, across the river, has none.
    area_row("Ottawa", "Ottawa", geojson_source("https://services.arcgis.com/G6F8XLCl5KtAlZ2G/arcgis/rest/services/GEN3_OTT_1_3_3/FeatureServer/0/query"
             "?where=1%3D1&outFields=ONS_Name&outSR=4326&f=geojson"), "ONS_Name", "neighbourhood"),
    # The 25 quartieri (2013; Politeama, Zisa, Tribunali-Castellammare), in Gauss-Boaga fuso Est with no .prj; "LIBERTA'"
    # is Libertà, "SFRRACAVALLO" Sferracavallo.
    area_row("Palermo", "Palermo", shapefile_source("https://opendata.comune.palermo.it/js/server/uploads/dataset/shp/_10012018113135.zip", crs="EPSG:3004"),
             lambda p: re.sub(r"A'(?=\s|-|$)", "À", (p.get("DENOMINAZI") or "").strip()).replace("SFRRACAVALLO", "SFERRACAVALLO"), "quartiere"),
    area_row("Perth", "Perth", arcgis_source(ABS_SAL, ["sal_name_2021"], "state_code_2021='5'", "objectid"),
             lambda p: re.sub(r" \(WA\)$", "", p.get("sal_name_2021") or ""), "suburb"),
    area_row("Poznań", "Poznań", geojson_source("https://www.poznan.pl/mim/plan/map_service.html?mtype=local_gov&co=osiedla"), "name", "osiedle"),
    area_row("Prague", "Prague", geojson_source("https://lkod-iprpraha.hub.arcgis.com/api/download/v1/items/35ede955057f4815b522ea91b488792a/geojson?layers=0"),
             "nazev_1", "městská část"),
    # Neither the city nor the Grand Reims publishes quartiers: its 85 IRIS grouped into 50 (iris_source; Murigny's 5, Laon's 4).
    area_row("Reims", "Reims", iris_source("51454"), "quartier", "IRIS", dissolve=True),
    # Rennes's 45 sous-quartiers (Villejean, Bréquigny, Thabor - Paris), under its 12 quartiers' compound names.
    area_row("Rennes", "Rennes", geojson_source("https://data.rennesmetropole.fr/api/explore/v2.1/catalog/datasets/perimetres-des-45-sous-quartiers-de-la-ville-de-rennes"
             "/exports/geojson?select=nomsquart,geo_shape"), "nomsquart", "sous-quartier"),
    # The 58 apkaimes (Āgenskalns, Grīziņkalns), in LKS-92 with an ISO-8859-13 .cpg.
    area_row("Riga", "Riga", shapefile_source("https://data.gov.lv/dati/dataset/ddc24ef1-4db4-46ce-a035-258b6ed69d91/resource/5409870d-287f-49e0-b336-d3c4ce936f69"
             "/download/apkaimes.zip"), "apkaime", "apkaime"),
    area_row("Rome", "Rome", geojson_source("https://geoportale.comune.roma.it/geoserver/ows?service=WFS&version=2.0.0&request=GetFeature"
             "&typeNames=DIPSER:SuddivisioniToponomastica&outputFormat=application/json&srsName=EPSG:4326"),
             "DESCRIZIONE", lambda p: (p.get("TIPOLOGIA") or "").lower() or "suddivisione"),
    area_row("Rotterdam", "Rotterdam", ogc_source("https://api.pdok.nl/cbs/wijken-en-buurten-2025/ogc/v1/collections/buurten/items?f=json&limit=1000&gemeentecode=GM0599"),
             "buurtnaam", "buurt", keep=lambda p: p.get("buurtnaam") != "Buitenwater"),
    # Neither the city nor the Métropole Rouen Normandie publishes quartiers: its 42 IRIS grouped into 40 (iris_source).
    area_row("Rouen", "Rouen", iris_source("76540"), "quartier", "IRIS", dissolve=True),
    # The Ville de Saint-Étienne's 47 quartiers (Badouillère, Crêt de Roc, Fauriel), its SIG's WFS, unaccented; its 21 conseils
    # de quartier bundle them.
    area_row("Saint-Étienne", "Saint-Étienne", geojson_source("https://extranet.saint-etienne.fr/extrasig/geoserver/wfs?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=vse:GENSECT1&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"), "nom", "quartier"),
    # SF Find's 117 neighborhoods (Hayes Valley, Noe Valley), not the 41 Analysis Neighborhoods; data.sfgov.org moved here.
    area_row("San Francisco", "San Francisco", geojson_source("https://data.sf.gov/resource/gfpk-269f.geojson?$limit=1000"), "name", "neighborhood"),
    # San José, Costa Rica: its barrios (admin_level 10); the Municipalidad's own layer is "para uso exclusivo de la
    # Municipalidad de San José", and IGN's finest are the distritos.
    area_row("San José", "OSM", osm_area_source((10,)), "name", None),
    # The City Clerk's Neighborhood Map Atlas, its 94 neighborhoods (Ballard, Belltown, Capitol Hill's Broadway):
    # "not intended to represent any 'official' City of Seattle neighborhood map", the ones people name.
    # The 19 auzoak (Gros, Egia, Antigua); Erdialdea's second part is Santa Clara island.
    area_row("San Sebastián", "San Sebastián", geojson_source("https://www.donostia.eus/geozerbitzuak/rest/services/ext/LIMITES_ADMINISTRATIVOS/MapServer/2/query"
             "?where=1%3D1&outFields=IzenAuzo&outSR=4326&f=geojson"), "IzenAuzo", "auzoa", dissolve=True),
    area_row("Seattle", "Seattle", geojson_source("https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/nma_nhoods_sub/FeatureServer/0/query"
             "?where=1%3D1&outFields=S_HOOD&outSR=4326&f=geojson"), "S_HOOD", "neighborhood"),
    area_row("Seoul", "Seoul", posted_shapefile_source("https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false",
             {"infId": "OA-22160", "seq": "1", "infSeq": "3"}), "ADSTRD_NM", "행정동"),
    # IDE.Sevilla's barrios (Triana's, Nervión's); "VILLEGAS" is mapped in two parts.
    area_row("Seville", "Seville", geojson_source("https://services1.arcgis.com/hcmP7kr0Cx3AcTJk/arcgis/rest/services/Barrios_de_Sevilla/FeatureServer/0/query"
             "?where=1%3D1&outFields=Barrio&outSR=4326&f=geojson"), "Barrio", "barrio", dissolve=True),
    area_row("Singapore", "Singapore", datagovsg_source("d_4765db0e87b9c86336792efe8a1f7a66"), "PLN_AREA_N", "planning area"),
    # The ж.к. (housing estates) and кв. (quarters); villages, villa zones and industrial areas left out.
    area_row("Sofia", "Sofia", geojson_source("https://api.sofiaplan.bg/datasets/297"),
             lambda p: re.sub(r"^(ЖК|КВ)\.\s*", "", (p.get("kvname") or "").strip()), lambda p: (p.get("prefname") or "").lower(),
             keep=lambda p: p.get("prefname") in ("ЖК.", "КВ.")),
    area_row("Stockholm", "Stockholm", geojson_source("https://geodata.scb.se/geoserver/stat/wfs?service=WFS&request=GetFeature&version=2.0.0&typeNames=stat:RegSO_2025"
             "&outputFormat=application/json&srsName=EPSG:4326&CQL_FILTER=" + urllib.parse.quote("kommunkod='0180'")), "regsonamn", "RegSO"),
    # The finest of the city's cuts (10, 14, 23, 28 quartiers), and the one with whole names.
    area_row("Strasbourg", "Strasbourg", geojson_source("https://data.strasbourg.eu/api/explore/v2.1/catalog/datasets/strasbourg-en-28-quartiers/exports/geojson"
             "?select=nom,geo_shape"), "nom", "quartier"),
    area_row("Sydney", "Sydney", arcgis_source(ABS_SAL, ["sal_name_2021"], "state_code_2021='1'", "objectid"),
             lambda p: re.sub(r" \(NSW\)$", "", p.get("sal_name_2021") or ""), "suburb"),
    area_row("São Paulo", "São Paulo", geojson_source("https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/wfs?service=WFS&version=2.0.0&request=GetFeature"
             "&typeNames=geoportal:distrito_municipal&outputFormat=application/json&srsName=EPSG:4326"), "nm_distrito_municipal", "distrito"),
    area_row("Tel Aviv", "Tel Aviv", geojson_source("https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2/MapServer/511/query"
             "?where=1%3D1&outFields=shem_shchuna&outSR=4326&f=geojson"), lambda p: logical_hebrew((p.get("shem_shchuna") or "").strip()), "shchuna"),
    area_row("The Hague", "The Hague", geojson_source("https://den-haag-opendata.opendatasoft.com/api/explore/v2.1/catalog/datasets/wijken/exports/geojson"),
             "wijknaam", "wijk"),
    area_row("Thessaloníki", "Thessaloníki", geojson_source("https://maps.thessaloniki.gr/server/rest/services/DataMapImage/MapServer/111/query"
             "?where=1%3D1&outFields=name&outSR=4326&f=geojson"), "name", "δημοτική κοινότητα"),
    # Chōme merged into their machi, ward by ward.
    area_row("Tokyo", "Tokyo", estat_source("13", range(101, 124)), machi, "machi", dissolve=lambda p: (p["CITY"], machi(p))),
    area_row("Toronto", "Toronto", geojson_source("https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/fc443770-ef0a-4025-9c2c-2cb558bfab00"
             "/resource/0719053b-28b7-48ea-b863-068823a93aaa/download/neighbourhoods-4326.geojson"), "AREA_NAME", "neighbourhood"),
    # The Mairie's 38 comités d'intérêt local (Mourillon, Le Jonquet, Saint-Jean du Var), on the Métropole TPM's map server; no
    # licence stated. The Faron, the Arsenal and the port lie in none; the layer's CIL of Le Pradet left out.
    area_row("Toulon", "Toulon", geojson_source("https://portailsig.metropoletpm.fr/ags2/rest/services/Metiers/MET_ADMINISTRATION/MapServer/6/query"
             "?where=commune%3D%27TOULON%27&outFields=nom&outSR=4326&f=geojson"), "nom", "comité d'intérêt local"),
    # The Ville de Tours's 22 quartiers (Sanitas-Rotonde, Deux Lions Gloriette; its IRIS aggregated); its 8 conseils de quartier are coarser.
    area_row("Tours", "Tours", geojson_source("https://data.tours-metropole.fr/api/explore/v2.1/catalog/datasets/quartiers-ville-de-tours"
             "/exports/geojson?select=quartier,geo_shape"), "quartier", "quartier"),
    area_row("Turin", "Turin", shapefile_source("http://geoportale.comune.torino.it/geodati/zip/zone_statistiche_geo.zip"), "DENOM", "zona statistica"),  # http only
    # The gemeente's 111 buurten (Lombok-oost, Wittevrouwen, Oog in Al), as the city spells them; its 10 wijken are coarse.
    area_row("Utrecht", "Utrecht", geojson_source("https://geodata.utrecht.nl/geoserver/UtrechtOpen/wfs?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=UtrechtOpen:GIS_BUURT&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"), "NAAM_BUURT", "buurt"),
    area_row("Valencia", "Valencia", geojson_source("https://geoportal.valencia.es/server/rest/services/OPENDATA/UrbanismoEInfraestructuras/MapServer/224/query"
             "?where=1%3D1&outFields=nombre&outSR=4326&f=geojson"), "nombre", "barri"),
    area_row("Vancouver", "Vancouver", geojson_source("https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/local-area-boundary/exports/geojson"),
             "name", "local area"),
    # Versailles Grand Parc's 8 quartiers municipaux of Versailles (the urbanisme department's, 2004).
    area_row("Versailles", "Versailles", geojson_source("https://services2.arcgis.com/YECJCCLQCtaylXWh/arcgis/rest/services/VersaillesQuartiersMunicipaux"
             "/FeatureServer/0/query?where=1%3D1&outFields=NOM&outSR=4326&f=geojson"), "NOM", "quartier"),
    area_row("Vienna", "Vienna", geojson_source("https://data.wien.gv.at/daten/geo?service=WFS&request=GetFeature&version=1.1.0&typeName=ogdwien:BEZIRKSGRENZEOGD"
             "&srsName=EPSG:4326&outputFormat=json"), "NAMEK", "Bezirk"),
    # The Métropole de Lyon's quartiers of its communes (201, no commune field): Villeurbanne's 8, those of its conseils de
    # quartier; La Doua's campus lies in none. The Ville publishes none of its own.
    area_row("Villeurbanne", "Villeurbanne", geojson_source("https://data.grandlyon.com/geoserver/metropole-de-lyon/ows?SERVICE=WFS&VERSION=2.0.0&request=GetFeature"
             "&typename=metropole-de-lyon:adr_voie_lieu.adrquartier&outputFormat=application/json&SRSNAME=EPSG:4326"),
             lambda p: (p.get("nom") or "").removeprefix("Quartier "), "quartier",
             keep=lambda p: (p.get("nom") or "").removeprefix("Quartier ") in {"Buers Croix-Luizet", "Charpennes Tonkin", "Cusset Bonnevay",
             "Cyprian Les Brosses", "Ferrandière Maisons Neuves", "Gratte-Ciel Dedieu Charmettes", "Perralière Grandclément", "Saint-Jean"}),
    area_row("Vilnius", "Vilnius", geojson_source("https://zemelapiai.vplanas.lt/arcgis/rest/services/Open_Data/Vilniaus_miesto_ribos/MapServer/1/query"
             "?where=1%3D1&outFields=SENIUNIJA&outSR=4326&f=geojson"), "SENIUNIJA", "seniūnija"),
    area_row("Warsaw", "OSM", osm_area_source((10,)), "name", None),   # the city's WFS was not reached (2026-10-10)
    # Each cluster named by its neighborhoods ("Kalorama Heights, Adams Morgan, Lanier Heights"; NAME is "Cluster 1");
    # the 7 "Additional" clusters are parks, the Mall, bases and campuses. Finer names are points only.
    area_row("Washington DC", "Washington DC", geojson_source("https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Administrative_Other_Boundaries_WebMercator"
             "/MapServer/17/query?where=1%3D1&outFields=NAME,NBH_NAMES,TYPE&outSR=4326&f=geojson"), "NBH_NAMES", "neighborhood cluster",
             keep=lambda p: p.get("TYPE") == "Original"),
    # The 48 osiedla of the city's 2016 resolution (Stare Miasto, Nadodrze, Ołbin), in Poland CS2000 zone 6.
    area_row("Wrocław", "Wrocław", shapefile_source("https://geoportal.wroclaw.pl/www/pliki/osiedla/granice-osiedli.zip"), "NAZWAOSIED", "osiedle"),
    area_row("Zagreb", "Zagreb", shapefile_source("https://data.zagreb.hr/dataset/37fa6630-0a87-4084-b62d-ff5edab3610b/resource/482b7289-d397-4c38-9929-5d0410ad0e16"
             "/download/rpj_mo.zip", encoding="cp1250"), "JMS_IME", "mjesni odbor"),
    area_row("Zurich", "Zurich", geojson_source("https://www.ogd.stadt-zuerich.ch/wfs/geoportal/Statistische_Quartiere?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature"
             "&TYPENAME=adm_statistische_quartiere_map&OUTPUTFORMAT=GeoJSON"), "qname", "statistisches Quartier"),
]

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
        _areas[city] = features, area_tiles(features, _osm.places() if _osm else [])
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


LAYERS = ("communes", "buildings", "terraces-v2", "venues", "streets", "streets-main", "places", "boundaries", "transit")
STREET_LAYERS = ("streets", "streets-main", "places", "boundaries", "transit")   # built only where an entry says "streets": true
# Days before a finished tile is cut again. Resuming skips a cell only while
# its file is younger than this, so the weekly run does refresh permits
# (they lapse) while a same-day re-run after a
# failure still skips what is done. Buildings and streets change yearly, communes never.
MAX_AGE_DAYS = {"communes": None, "buildings": 360, "buildings-v2": 360, "terraces-v2": 6, "venues": 6, "streets": 360, "streets-main": 360, "places": 360}
# boundaries/ has no age: a city's set is one small fetch, asked again each run that builds the layer.

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

UNSHADED = None  # --unshaded: the file the weekly run lists the cells no venue or terrace reaches and no small place holds in
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
              for v in _osm.near(_osm.venues, padded(union(cells), SHADE_MARGIN))}
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
        for b in _osm.areas(rect)[0]:
            if b["admin_level"] != SMALL_PLACE_LEVEL or not (g := b["geometry"]): continue
            keys = {c.key for k in cells_in(*bounds(g)) if (c := by_key.get(k.key)) and contains(g, c.lat, c.lon)}
            if keys: held.append((g, keys))
        # A commune reaching past the area's box (Saint-Germain-en-Laye past the petite couronne's) has venues the box left
        # out: those communes' venues are read again from the whole extract, at once.
        s, w, n, e = _osm.box
        far = [g for g, _ in held if not (s <= (x := bounds(g))[0] and w <= x[1] and x[2] <= n and x[3] <= e)]
        far_venues = {}
        if far:
            boxes = [bounds(g) for g in far]
            for v in _osm.venues_in((min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes))):
                lat, lon = v["coordinate"]["latitude"], v["coordinate"]["longitude"]
                far_venues.setdefault(buckets(lat, lon, lat, lon)[0], []).append(v)
    except SourceError as e:
        log(f"    communes not read ({e}); every cell keeps its buildings"); return None
    small = set()
    for g, keys in held:
        table = far_venues if any(g is f for f in far) else _osm.venues
        inside = (v for v in _osm.near(table, bounds(g)) if contains(g, v["coordinate"]["latitude"], v["coordinate"]["longitude"]))
        if sum(1 for _ in zip(range(SMALL_PLACE_VENUES), inside)) < SMALL_PLACE_VENUES: small |= keys
    return small

def shaded_keys(cells, out, rect):
    """The cells a buildings tile is written for: near a venue or terrace (shade_keys), or in a small place. None: every cell."""
    points = shade_points(cells, out)
    small = small_place_keys(cells, rect) if points is not None else None
    return None if small is None else shade_keys(points) | small

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
            items = estimated(items, answer)
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

def do_transit(cells, out, failures):
    """The transit/ tiles of the area's ~4 km cells; a cell no line stops in has no file (an earlier run's is removed)."""
    try: tiles = transit_tiles(cells, _osm.transit())
    except SourceError as e:
        failures += [(c.key, "transit", str(e)) for c in cells]; return
    for c in cells:
        if c.key in tiles: write(path(out, "transit", c), tiles[c.key])
        elif os.path.exists(path(out, "transit", c)): os.remove(path(out, "transit", c))

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

def run(label, rect, out, block, layers, extracts, departements=None, boundary=None, city=None, city_entries=()):
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
    if city in OVERTURE_CITIES and "venues" in layers:
        try:
            counts = overture_facts(_osm, overture_places(_osm.box))
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
    if "terraces-v2" in layers and "buildings" not in layers and LISTED is not None:
        mine = [c for c in cells if c.key not in outside]
        keys = shaded_keys(mine, out, rect) if mine else None
        if keys is not None:
            top = [c for c in mine if c.key in keys and f"tiles/buildings-v2/{c.key}.json" not in LISTED
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
    global STORE, LISTED, UNSHADED
    STORE = args.store and args.store.rstrip("/") + "/"
    UNSHADED = args.unshaded
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
