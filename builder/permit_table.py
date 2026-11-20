"""PERMIT_CITIES: one row per city permit feed."""
from builder.cells import boundary
from builder.net import SECRET_ENVS
from builder.projections import florida_east_to_wgs84

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
