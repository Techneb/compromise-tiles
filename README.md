# compromise-tiles

Open city data used by the Compromise iOS app, one JSON file per map cell.

## Where the tiles are

The tiles are served from Cloudflare R2 at
`https://tiles.alephb.uk`, under
`tiles/<layer>/<key>.json` (gzipped at rest), with `config.json` at the
root. Layers: `buildings/` and `terraces-v2/` per ~200 m
cell, keyed `int(lat*500),int(lon*500)`; `venues/` per 1/50° cell (name,
point, amenity, outdoor seating, source, optional OSM details); `sources/` per ~200 m cell
names the permit feed, building source and neighbourhood boundaries each cell was built from;
`streets/` per ~200 m cell holds street lines and `numbers/` on the same
cells their house-number ranges, `streets-main/` per
1/25° cell the main roads alone, `places/` on the same cells
neighbourhood names and points and `boundaries/` their outlines, all six
for the areas `cities.json` flags with `"streets": true` only. A
building whose source gives no height is written at 15 m with
`"guessed": true` (builds since 2026-10-04; a tile built before carries
no flag); it stays local. The store holds `buildings-v2/`, the same
footprints as integer deltas, a third of the bytes (since 2026-10-08), for the cells near a
venue or terrace (since 2026-10-13);
[BUILDINGS.md](BUILDINGS.md) gives its format and which cells have one.

This repository holds the generator (`make_tiles.py`), the areas it
builds (`cities.json`, with municipal boundaries in `boundaries/`), the
coverage feed (`coverage.json` on R2; the copy here stopped updating on 2026-10-13; the site reads its slim copies `areas.json`, `summary.json` (per-city totals) and `coverage/<1° square>.json`, written beside it), the store's numbers
(`stats.json` on R2: size and file count per layer, build time per city, written by
`stats.py` after each build) and the tiles built
off GitHub under `tiles/`. GitHub Actions (`.github/workflows/tiles.yml`)
builds the rest on a schedule and uploads it to R2; those tiles are not
kept here. OpenStreetMap is read from Geofabrik extracts with
osmium-tool; city and national feeds are downloaded in bulk.

### streets/

Each tile is an array of rows. A street row is `[kind, y0, x0, dy1, dx1, …]`
(kind 1 main road, 2 street, 3 pedestrian), or `[kind, "name", y0, x0, …]`
where the OpenStreetMap way has a name (since 2026-10-08): one way cut at the
cell's edges, coordinates in 1e-5° steps, the first from the key's corner
(key / 500), each next from the one before.

### numbers/

House-number ranges, on the same cells, keys and paths as `streets/` (since
2026-10-10). Each tile is an array of `["name", lowest, highest]`, sorted by
name (by Unicode code point):

- `name` is written exactly as on the named rows of the same cell's
  `streets/` tile.
- `lowest` and `highest` are integers, the smallest and largest house number
  found on that street inside the cell. A number is OpenStreetMap's
  `addr:housenumber` on a node, a building or along an `addr:interpolation`
  way, whose `addr:street` (else the name of the `associatedStreet` relation
  it is a house of) equals the name once case, accents and apostrophes are
  set aside; only its leading digits count ("12bis" and "12 ter" are 12,
  "12-14" is 12, "12;14" is both), and one not starting with a digit is left
  out. The address point (a building's mean vertex) must lie in the cell.
- A street is listed only where at least two distinct numbers are found. A
  range is that cell's alone: a street crossing several cells is listed in
  each. A cell with no range has no file.

### places/

Each tile is an array of `{"name", "kind", "coordinate", "admin_level"?}`:

- `kind` is OpenStreetMap's `place` tag: `suburb`, `quarter` or
  `neighbourhood`.
- `admin_level` (an integer, OpenStreetMap's) is written only when the name
  comes from or matches an administrative boundary relation; absent
  otherwise. In Paris, 9 is an arrondissement and 10 one of the 80 quartiers
  administratifs. A place matches a boundary when it is the relation itself,
  the relation's `label` node, its `admin_centre` node under a name one
  holds the other ("17e Arrondissement" in "Paris 17e Arrondissement"), or
  lies inside it under the same name once "Quartier" and the articles are
  set aside ("Batignolles", "Quartier des Batignolles"). The deepest level
  wins.
- A boundary at admin_level 9 or 10 that no place matches is written as a
  place of its own at its ring's mean vertex, with its own `place` tag, or
  else `suburb` at 9 and `quarter` at 10. That is how most of Paris's
  quartiers come in: OpenStreetMap maps them as boundaries, not as places.

Added on 2026-10-06 and backward compatible: a reader of the first format
ignores the new key, and every `kind` is still one of the three. A tile
built before carries no `admin_level`.

### boundaries/

Neighbourhood outlines, on the same 1/25° cells and keys as `places/` (since
2026-10-10). Each tile is an array of `{"name", "level", "source", "polygons"}`:

- Each outline is written once and whole, in the cell of its label point, not
  cut at the cell's edges. The label point is the `places/` place of the same
  name lying inside the outline (compared as `places/` compares names: case,
  accents, punctuation, a leading "Quartier" and articles set aside), and
  `name` is then written as in `places/`; else it is a point inside the
  outline, and `name` is the source's (a name in capitals only is put in title
  case).
- `level` is the source's own word for the unit (`"quartier"`, `"Ortsteil"`,
  `"wijk"`), or an integer, OpenStreetMap's `admin_level`, or its `place` tag
  where the outline is a place polygon.
- `source` is the key under which `sources/` names the source (below).
- `polygons` is an array of polygons, each an array of rings: the outer ring
  (counter-clockwise) first, then its holes (clockwise). Most outlines are one
  polygon of one ring. A ring is `[y0, x0, dy1, dx1, …]`, as in `buildings-v2/`:
  integers in 1e-5° steps, the first vertex from the key's corner (key / 25,
  so it may be negative, or beyond the cell, for an outline reaching out of
  it), each next from the one before; the ring is open (its first vertex is
  not repeated).
- Outlines are simplified at 5 m over each source's whole set, so an edge two
  neighbours share is simplified once and stays shared, then rounded on the
  absolute 1e-5° grid, so shared vertices stay equal.
- A cell holding no label point has no file. The outlines are of one source
  per area (where a city has two, as Paris has, each covers its own part).

A `sources/` tile names, under `"boundaries"`, the source of the outline
holding the cell's centre.

### transit/

Public-transport lines, on the same 1/25° cells and keys as `places/` (since
2026-10-13; metro, tram, rail, bus and cable car), built on dispatch only, for the
areas flagged `"streets": true`. Each tile is an array of
`{"mode", "ref", "colour"?, "network"?, "operator"?, "stops", "points", "shape"}`:

- `mode` is OpenStreetMap's `route`: `subway`, `tram`, `train`,
  `light_rail`, `bus`, `trolleybus` or `aerialway`; `ref` the route's (else its route master's), `colour` the
  same way, as `#RRGGBB`, absent when neither has a valid one.
  `network` and `operator` the same way, absent when neither has one.
- `stops` are the stop names in the route's order (`""` for an unnamed one);
  `points` one per stop, `[y0, x0, dy1, dx1, …]` as in `boundaries/`:
  1e-5° steps, the first from the key's corner (key / 25), each next from
  the one before.
- `shape` is the track: the route's other way members chained in member
  order into runs (a new run where two ways do not meet), each simplified
  at 10 m (Douglas–Peucker) and coded as `points`, each run from the key's
  corner. `[]` when the route has no track ways.
- A line is written whole in every cell one of its stops falls in. A route's
  stops are its members with a stop role, else its platforms; two within
  60 m are one. A route with no ref, a long-distance train (`service`
  high_speed, long_distance, national, international, night, tourism, or a
  network starting VIA Rail, Amtrak, TGV, Ouigo, Intercités, Eurostar or
  Noctilien,
  or a ref listing train numbers, or a train's ref holding a number of four
  digits or more but where the network numbers its lines so, as the Dutch
  Sprinters' "4000"), a
  train whose ref starts with a high-speed brand (Frecciarossa,
  Frecciargento, Frecciabianca, Italo, Railjet, Nightjet, EuroCity,
  Intercity), a
  coach (a network or operator starting Flixbus, FlixTrain, Flibco,
  RegioJet, Autobuses de España, Rede Nacional de Expressos; a bus whose
  stops reach more than 60 km from its first), a Co.Tra.L. bus (Rome's
  regional coaches, whose refs are route codes), a night
  bus or tram (a ref of "N" and a number; Prague's trams 91–99 and buses
  9xx; Lisbon's Carris buses 201–210), a tourist line (Prague's trams 41
  and 42, Amsterdam's museum tram, Barcelona Bus Turístic, a route tagged `tourism=yes`, a network or
  operator holding Big Bus, Open Tour, Sightseeing, Hop-on, Tootbus,
  Museum or Heritage, or a network of just "tourism") or
  under two stops is left out, and so is one whose stop names all belong to
  a longer route of the same mode and ref (the other direction, a short
  working). A ref's bracketed service pattern is dropped first ("C-2
  (CIVIS)" is "C-2"). A cell no line stops in has no file.

## Weekly data check

`.github/workflows/data-check.yml` runs `check_data.py` every Monday at
01:23 UTC (before the tiles build) and on dispatch. Each permit and
height feed of `make_tiles.py` is asked for one dense probe cell
(`probes.json`, chosen once with `check_data.py pick`); its status, count
and named / with-a-height share are compared with the last run's record
(`data-check.json`, committed by the workflow) and with the published
tile of that cell, and the licence string is read where the publisher has
a metadata API. The coverage feed is sampled against the store's tiles
(the check of compromise's `tools/coverage/check_feed.py`). A dead feed, a
count or share fallen by more than half, a changed licence or a feed
mismatch opens or updates the issue "Weekly data check"; a clean run
closes it. When a row is added to `make_tiles.py`, run
`python3 check_data.py pick` to give it a probe cell
(`test_check_data.py` fails until it has one).

## Crowd export

`https://tiles.alephb.uk/crowd/odbl/latest.csv.gz` lists facts about
OpenStreetMap places that the app's users agreed on: OSM element type and
id, fact, value, and the day it was agreed. A new file is built every week,
with a dated copy beside it (`crowd/odbl/YYYY-MM-DD.csv.gz`). © Compromise
contributors, available under the
[Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/);
its individual contents under the
[Database Contents License 1.0](https://opendatacommons.org/licenses/dbcl/1-0/).

## Licences and credits

Each tile holds data from the sources below, depending on where it lies.
Licences are given as each publisher states them; where a publisher states
none, this says so rather than guessing.

**Everywhere**
- OpenStreetMap (venues and their outdoor seating; streets; place names; building footprints where no
  city or national source is used): © OpenStreetMap contributors, ODbL.
  Because these tiles include OpenStreetMap data, the tile set is
  published under the ODbL (share-alike).
- name-suggestion-index (chain names and cuisines of venues/): © name-suggestion-index contributors,
  BSD 3-Clause. Its notice is kept in `nsi_brands.json`.

**Terrace permits and outdoor seats**
- Paris: Ville de Paris, ODbL.
- Toulouse: Mairie de Toulouse, Licence Ouverte 2.0.
- Strasbourg: Eurométropole de Strasbourg, Licence Ouverte 2.0.
- Anglet: Ville d'Anglet, Licence Ouverte 2.0.
- Rouen: Ville de Rouen, 2021 edition, Licence Ouverte 2.0.
- Lorient: Ville de Lorient, 2020 edition, Licence Ouverte 2.0.
- La Rochelle: Ville de La Rochelle (Licence ouverte / Open Licence).
- Issy-les-Moulineaux: Ville d'Issy-les-Moulineaux, 2020 list (licence not
  stated by the publisher).
- Boulogne-Billancourt: Ville de Boulogne-Billancourt, 2020 list (licence
  not stated by the publisher).
- Melbourne: City of Melbourne CLUE census, CC BY.
- Camden (London): Camden Council pavement licences, UK Open Government
  Licence v3. © Crown copyright and database rights 2026 OS AC0000849991.
- Amsterdam: Gemeente Amsterdam (licence not stated by the publisher).
- Vienna: City of Vienna open data (Schanigärten), CC BY 3.0 AT.
- Copenhagen: City of Copenhagen open data (Udeservering), CC BY 4.0.
- Madrid: Ayuntamiento de Madrid, censo de terrazas, CC BY 4.0.
- Barcelona: Ajuntament de Barcelona, Open Data BCN, CC BY 4.0.
- Seville: Gerencia de Urbanismo de Sevilla, veladores (licence not stated
  by the publisher).
- Basel: Kanton Basel-Stadt open data (Allmendbewilligungen), CC BY 4.0.
- Geneva: Ville de Genève, « Données SITG » (accès libre).
- Vilnius: © 2014 Vilniaus miesto savivaldybė; © 2014 SĮ 'Vilniaus Planas'
  (licence not stated by the publisher).
- Oslo: outdoor serving licences, Copyright Plan- og bygningsetaten i Oslo
  kommune (licence not stated by the publisher).
- Buenos Aires: Gobierno de la Ciudad de Buenos Aires, permisos de uso del
  espacio público, área gastronómica, CC BY 2.5 AR (2026-09-28 snapshot,
  geocoded with the city's USIG service).
- Adelaide: City of Adelaide, outdoor dining permits and property
  boundaries, CC BY (2026-09-28 snapshot; a few points from OpenStreetMap
  Nominatim, ODbL).
- Toronto: City of Toronto, CaféTO locations. Contains information
  licensed under the Open Government Licence – Toronto.
- Riga: Rīgas valstspilsētas pašvaldība, GEO RĪGA outdoor terraces
  (licence not stated by the publisher).
- Eindhoven: Gemeente Eindhoven, terras tekeningen (licence not stated by
  the publisher).
- San Sebastián: © Donostiako Udala - Ayuntamiento de Donostia / San
  Sebastián, Terrazas autorizadas (licence not stated by the publisher).
- New York: NYC Open Data (licence not stated by the publisher).
- Chicago: City of Chicago Data Portal. "The City of Chicago makes no
  claims as to the content, accuracy, timeliness, or completeness of any
  of the data provided at this site. The data provided at this site is
  subject to change at any time. It is understood that the data provided
  at this site is being used at one's own risk."
- San Francisco: DataSF, City and County of San Francisco, PDDL.
- Helsinki: Helsingin kaupunki, Kaupunkiympäristön toimiala, short-term land
  rentals (terrace areas), CC BY 4.0 through Helsinki Region Infoshare.
- Gothenburg: Göteborgs Stad, miljöförvaltningen, Restauranger med
  serveringstillstånd (outdoor serving), CC0 1.0.
- Stockholm: Stockholms stad, Trafikkontoret, Markupplåtelse (uteservering),
  CC0 1.0.
- Washington DC: District Department of Transportation (DDOT), Annual Public
  Space Rental Permits, CC BY 4.0.
- Boston: City of Boston, Outdoor Dining (Public, 2024) (licence not stated by
  the publisher).
- Seattle: City of Seattle, Seattle Department of Transportation, Street Use
  Permits (licence not stated by the publisher).
- Kensington and Chelsea (London): Royal Borough of Kensington and Chelsea,
  Tables and Chairs licences (licence not stated by the publisher).
- Edinburgh: © City of Edinburgh Council, tables and chairs permits
  (2026-10-05 snapshot, geocoded with OpenStreetMap Nominatim, ODbL, and
  ONS postcode centres, OGL).
- Los Angeles: City of Los Angeles, L.A. Al Fresco dining locations, 2021
  (licence not stated by the publisher).
- Thessaloníki: Δήμος Θεσσαλονίκης, shops of health interest (licence: the
  Ministry of the Interior's "Ανοιχτή Άδεια", as linked by the publisher),
  2017–2019.
- Hong Kong: Food and Environmental Hygiene Department, Restaurant
  Licences (licensed restaurants with outside seating accommodation),
  through the Common Spatial Data Infrastructure portal; DATA.GOV.HK Terms
  and Conditions ("You are allowed to browse, download, distribute, reproduce, hyperlink
  to, and print the Data for both commercial and non-commercial purposes
  on a free-of-charge basis", with the source identified).
- San Diego: City of San Diego, Development Permits (data.sandiego.gov),
  Open Data Commons Public Domain Dedication and License (PDDL).
- Tucson: City of Tucson, Patio Expansions (licence not stated by the
  publisher).
- Pittsburgh: City of Pittsburgh, Department of Mobility and Infrastructure,
  Outdoor Dining (licence not stated by the publisher).
- Long Beach: City of Long Beach, Parklets (licence not stated by the publisher;
  the City's Open Data terms of use).
- Detroit: City of Detroit, Right of Way Encroachments (licence not stated by the
  publisher).
- Québec: Ville de Québec, Permis délivrés à la Ville de Québec (Données
  Québec), Attribution (CC-BY 4.0).

**Municipal boundaries (`boundaries/`, a permit feed held to its own city)**
- Toronto: City of Toronto, Regional Municipal Boundary. Contains
  information licensed under the Open Government Licence – Toronto.
- Camden (London): Office for National Statistics, Local Authority
  Districts (December 2025) Boundaries UK BFC. Source: Office for National
  Statistics licensed under the Open Government Licence v.3.0. Contains OS
  data © Crown copyright and database right 2025.

**Building footprints and heights**
- France: IGN BD TOPO, Licence Ouverte 2.0.
- Amsterdam, Rotterdam, The Hague, Utrecht: © 3DBAG by tudelft3d and 3DGI
  (https://docs.3dbag.nl/en/copyright/), CC BY 4.0.
- Berlin: Geoportal Berlin, Gebäudehöhen 2022, Datenlizenz Deutschland –
  Zero 2.0.
- Geneva: « Données SITG », Bâtiments hors-sol (accès libre).
- Bologna: Comune di Bologna, Carta Tecnica Comunale, CC BY 4.0.
- Turin: Regione Piemonte, BDTRE, CC BY 4.0.
- Wrocław: Gmina Wrocław, 2022 strategic noise map buildings (licence not
  stated by the publisher).
- Melbourne: City of Melbourne, 2023 Building Footprints, CC BY 4.0.
- New York: NYC Open Data (licence not stated by the publisher).
- Chicago: City of Chicago Data Portal (disclaimer above).
- San Francisco: DataSF, PDDL.
- Tel Aviv: Tel Aviv-Yafo Municipality open data, "Structures" (free to
  share and adapt with credit).
- Jerusalem: Jerusalem Municipality, bldg2020 ("for reference and general
  use only", as stated by the publisher).
- Ramat Gan: Ramat Gan Municipality, buildings layer (licence not stated
  by the publisher).
- Holon: Holon Municipality, buildings layer (licence not stated by the
  publisher).
- Herzliya: Herzliya Municipality, buildings layer (licence not stated by
  the publisher).
- Netanya: Netanya Municipality, buildings layer (licence not stated by
  the publisher).
- Hamburg: Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und
  Vermessung (LGV), 3D-Gebäudemodell LoD2-DE, Datenlizenz Deutschland –
  Namensnennung – 2.0.
- Denver: City and County of Denver, Building Outlines 2022 (licence not
  stated by the publisher).
- Cape Town: City of Cape Town, 2D Building Footprints (licence not stated
  by the publisher).
- Johannesburg: City of Johannesburg, Building Footprints (licence not
  stated by the publisher).
- San Sebastián: Gipuzkoa Provincial Council, INSPIRE Buildings ("may be
  reproduced, removed and reused freely on a nonexclusive basis, in whole
  or in part, by any person in any format and for any subsequent
  legitimate use"; "Authorship must be indicated in any case.").
- São Paulo: Prefeitura de São Paulo, GeoSampa Edificações (licence not
  stated by the publisher).
- Calgary: City of Calgary, 3D Buildings. Contains information licensed
  under the Open Government Licence – City of Calgary.
- Vancouver: City of Vancouver, Building footprints 2009. Contains
  information licensed under the Open Government Licence – Vancouver.
- Zurich: Stadt Zürich, Blockmodell, CC0.
- Austin: City of Austin, Building Footprints 2013 (licence not stated by
  the publisher).
- Los Angeles: City of Los Angeles, LARIAC4 Building Footprints (licence not
  stated by the publisher).
- Philadelphia: City of Philadelphia, L&I Building Footprints (the City
  reserves all rights; provided as is).
- Bratislava: Magistrát hlavného mesta SR, Bratislava,
  Pocet_obyvatelov_budovy (licence not stated by the publisher); heights
  on OpenStreetMap's footprints.
- Oakland: City of Oakland, BuildingFootprints (licence not stated by the
  publisher); heights on OpenStreetMap's footprints.
- Vaughan, Toronto, Montréal, Markham, Hamilton, London (Ontario), Winnipeg, Québec, Halifax: Natural Resources Canada, Automatically
  Extracted Buildings. Contains information licensed under the Open
  Government Licence – Canada.
- Bogotá: Unidad Administrativa Especial de Catastro Distrital – UAECD
  (IDECA), Construcción, CC BY 4.0.
- Edmonton: City of Edmonton, Rooflines (as of 2019). Contains information
  licensed under the Open Government Licence – City of Edmonton.
- London, Birmingham, Bristol, Leeds, Liverpool, Sheffield: Environment
  Agency, LIDAR Composite DSM and DTM, 1 m. © Environment Agency copyright
  and/or database right 2022. All rights reserved. Open Government Licence;
  heights on OpenStreetMap's footprints.
- Tokyo, Osaka: 3D City Model (Project PLATEAU), Tokyo 23 wards and Osaka
  City, 2025 editions, Ministry of Land, Infrastructure, Transport and
  Tourism (MLIT), processed into footprints and heights. Public Data
  License 1.0 (compatible with CC BY 4.0). 「3D都市モデル（Project
  PLATEAU）東京都23区・大阪市（2025年度）」（国土交通省）を加工して作成。
- Buenos Aires: Gobierno de la Ciudad de Buenos Aires, Tejido Urbano
  (Subsecretaría de Planeamiento), CC BY 2.5 AR.
- Milan: Comune di Milano, Database Topografico (DBT) 2020, CC BY 4.0.
- Rome: Regione Lazio, Database Geotopografico (DBGT) 2020, Roma. Fonte:
  Regione Lazio.
- Düsseldorf, Essen, Dortmund, Wuppertal, Duisburg, Bochum, Bielefeld, Bonn, Cologne: Geobasis
  NRW, 3D-Gebäudemodell LoD2 (Datenlizenz Deutschland –
  Zero – Version 2.0).
- Nuremberg, Augsburg, Munich: Datenquelle: Bayerische Vermessungsverwaltung –
  www.geodaten.bayern.de, 3D-Gebäudemodelle LoD2 (CC BY 4.0).
- Leipzig, Dresden: GeoSN, 3D-Gebäudemodell LoD2 (Datenlizenz Deutschland –
  Namensnennung – Version 2.0).
- Mannheim: LGL Baden-Württemberg, 3D-Gebäudemodell LoD2 (Datenlizenz
  Deutschland – Namensnennung – Version 2.0).
- Bremen: Landesamt GeoInformation Bremen, 3D-Gebäudemodell LoD2 (CC BY 4.0).
- Detroit: Southeast Michigan Council of Governments (SEMCOG), Buildings 2024
  (SEMCOG Copyright License Agreement). © SEMCOG.
- Columbus: City of Columbus, Building Footprints (licence not stated by the publisher).
- Anaheim: City of Anaheim, Building Footprint (CC BY 4.0, as quoted by the publisher).
- Las Vegas: City of Las Vegas, Downtown Building Heights (licence not stated by the
  publisher).
- Honolulu: City and County of Honolulu, HoLIS Building Footprints (licence not stated
  by the publisher).
- Cincinnati: CAGIS, Hamilton County Building Footprints (licence not stated by the
  publisher).
- Nashville: Metropolitan Government of Nashville and Davidson County, Building
  Footprints (licence not stated by the publisher).
- Louisville: LOJIC (Louisville/Jefferson County Information Consortium), Jefferson
  County KY Buildings with Building Heights (licence not stated by the publisher).
- Phoenix: NGA and USGS, LiDAR-Derived 3D Building Footprints, metro-Phoenix (2014),
  through Arizona State University's GeoHub (licence not stated by the publisher).
- Omaha: DCGIS (Douglas County), 2022 Building Footprints (licence not stated by the
  publisher).
- Memphis: City of Memphis, Building Footprints (licence not stated by the publisher).
- Riverside: City of Riverside, Downtown Buildings (licence not stated by the publisher).
- Portland: City of Portland, Bureau of Planning and Sustainability, Building Footprints
  (licence not stated by the publisher).
- Hannover: LGLN, 3D-Gebäudemodell LoD2 (Datenlizenz Deutschland – Namensnennung
  – Version 2.0).
- Brno: Statutární město Brno (data.brno.cz), Výška budov (CC BY).
- Lublin, Bydgoszcz, Gdańsk, Szczecin, Warsaw, Kraków, Łódź, Poznań: Główny Urząd Geodezji i Kartografii (GUGiK), Modele 3D
  budynków LoD1 (CC BY 4.0).
- Espoo: City of Espoo, Espoon rakennukset (Helsinki Region Infoshare, CC BY 4.0).
- Tallinn: Maa- ja Ruumiamet, Eesti 3D hoonete LoD1 (CC BY 4.0).
- Christchurch: Christchurch City Council, Building (CC BY 4.0).
- Wellington: Wellington City Council, Building Footprints (CC BY 4.0).
- Catania: Regione Siciliana - SITR, Carta Tecnica 1:2000 DBTRS (CC BY 4.0, the
  geoportal's terms).
- Zagreb: Grad Zagreb, ZG3D 2022 3D model Grada Zagreba (Otvorena dozvola).
- Belgrade: Beoland, Beograd 3D (licence not stated by the publisher).
- San José: Municipalidad de San José, SIG_SER_3D_Edificaciones ("Definidos
  por la Municipalidad de San José").
- San Jose: County of Santa Clara; Sanborn Map Co., Buildings Footprints 2D
  (licence not stated by the publisher).
- Genoa: Regione Liguria, DB Geotopografico NC5 3D (CC BY 4.0).
- Sofia: Sofiaplan, Сгради 18.12.2009 (licence not stated by the publisher).
- Spain (Madrid, Seville, Barcelona, Valencia, Zaragoza, Málaga, Palma,
  Las Palmas, Murcia, Alicante, Córdoba, Valladolid, Vigo, Gijón):
  outlines and heights derived from the Dirección General del Catastro's
  INSPIRE Buildings service (INSPIRE access and use licence). A derived
  dataset, not cadastral information; the cadastre's own files are not
  redistributed.

**Area boundaries (`boundaries/`)**
- Greater London: Office for National Statistics, Regions (December 2024)
  Boundaries EN BFE, Open Government Licence v3.0. Contains OS data ©
  Crown copyright and database right 2024.
- Birmingham, Bristol, Leeds, Liverpool (`liverpool-ons.geojson`, the lidar
  row's; `liverpool.geojson`, the tiled area's, stays OpenStreetMap's, ODbL),
  Sheffield: Office for National Statistics, Local Authority Districts
  (December 2025) Boundaries UK BFC, Open Government Licence v3.0. Contains
  OS data © Crown copyright and database right 2025.

**Communes (France: their names and codes in `coverage.json`)**
- Etalab geo API, Licence Ouverte 2.0; IGN Admin Express through apicarto
  (Licence Ouverte 2.0) when the geo API does not answer.

**City boundaries (`boundaries/`)**
- Paris (the city, `paris.geojson`): Ville de Paris, Arrondissements,
  Open Database License (ODbL); its 20 arrondissements dissolved into one.
- Rouen: Métropole Rouen Normandie, Cadastre – Communes (the DGFiP cadastre;
  licence not stated by the publisher).
- Lorient: Ville de Lorient, Découpage géographique des quartiers, through
  opendata56.fr, Licence Ouverte 2.0; its quartiers dissolved into one.
- Anglet: Etalab geo API (IGN Admin Express), Licence Ouverte 2.0.
- Antibes, Arles, Carcassonne, Colmar, Lourdes, Saint-Malo: Etalab geo API
  (IGN Admin Express), Licence Ouverte 2.0.
- San José (Costa Rica, `san-jose-cr.geojson`): © OpenStreetMap contributors,
  ODbL (the cantón's administrative boundary).
- Thessaloníki: Δήμος Θεσσαλονίκης, δημοτικές κοινότητες (the municipality's
  open licence); dissolved into one.
- Vancouver: City of Vancouver, City boundary. Contains information licensed
  under the Open Government Licence – Vancouver.
- Adelaide (the City of Adelaide): Government of South Australia, Local
  Government Areas (Location SA, CC BY 3.0 AU).
- Murcia, Córdoba, Szczecin: © OpenStreetMap contributors, ODbL (each
  municipality's administrative boundary); Córdoba's San Rafael de Navallana
  reservoir and Szczecin's Lake Dąbie left out (© OpenStreetMap contributors, ODbL).
- The other whole-city files made since 2026-10-15 (Rome to Johannesburg, Zurich
  to Poznań): © OpenStreetMap contributors, ODbL (each municipality's
  administrative boundary), cut to land with the OSM land polygons
  (osmdata.openstreetmap.de, ODbL).
- Berlin: Geoportal Berlin, ALKIS Land Berlin, Datenlizenz Deutschland –
  Zero 2.0.
- New York: NYC Department of City Planning, Borough Boundaries, NYC Open
  Data (licence not stated by the publisher).
- Istanbul: General Command of Mapping (Türkiye), administrative
  boundaries through OCHA (COD-AB), CC BY-IGO.
- Buenos Aires: Gobierno de la Ciudad de Buenos Aires, Perímetro,
  CC BY 2.5 AR.
- Milan: Comune di Milano, Confini amministrativi del Comune di Milano,
  CC BY 4.0.
- Rome (Roma Capitale, the Vatican left out): ISTAT, Confini delle unità
  amministrative a fini statistici al 1° gennaio 2025, CC BY 4.0.
- Tel Aviv: Tel Aviv-Yafo Municipality open data, city boundary (free to
  share and adapt with credit).
- Jerusalem: © OpenStreetMap contributors, ODbL (the municipality's
  administrative boundary).
- Be'er Sheva, Haifa, Minneapolis, Mississauga, Netanya, Newcastle upon Tyne,
  Rishon LeZion, San Diego, Taipei, Markham, Hamilton, London (Ontario),
  Winnipeg, Québec, Essen, Dortmund, Wuppertal, Duisburg, Bochum, Bielefeld, Bonn,
  Hannover, Augsburg, Brno, Wellington, Catania, Tucson, Leipzig, Dresden, Mannheim, Bremen, Detroit, Pittsburgh,
  Columbus, Anaheim, Las Vegas, Long Beach, Cincinnati, Nashville, Louisville, Phoenix, Omaha, Memphis,
  Riverside, Lublin, Bydgoszcz, Espoo, Graz, Tallinn, Gdańsk, Portland:
  © OpenStreetMap contributors, ODbL (each municipality's
  administrative boundary).
- Abu Gosh, Albireh, Azor, Bat Yam, Beit Sahur, Beitar Ilit, Bethlehem,
  Bnei Brak, Giv'at Ze'ev, Givat Shmuel, Givatayim, Har Adar, Kiryat Ono,
  Maale Adumim, Mevaseret Zion, Or Yehuda, Petah Tikva, Ramallah, Ramat
  HaSharon: © OpenStreetMap contributors, ODbL (each municipality's
  administrative boundary).
- Montréal (the agglomeration, `montreal.geojson`): Ville de Montréal,
  Limites administratives de l'agglomération de Montréal (arrondissements
  et villes liées), CC BY 4.0; its 34 polygons dissolved into one.
- Hong Kong (the SAR's land, `hong-kong.geojson`): Census and Statistics
  Department, 2021 Population Census (Statistics and Boundaries of District
  Council Districts), through the Common Spatial Data Infrastructure portal;
  CSDI Portal Terms and Conditions ("You are allowed to browse, download,
  distribute, reproduce, hyperlink to and print the Data for both commercial
  and non-commercial purposes on a free-of-charge basis", with the source
  identified); its 18 districts dissolved into one.

**Neighbourhood boundaries (`boundaries/`, by the `source` key)**
- OSM (Barcelona, Belgrade, Budapest, Delhi, Istanbul, Jerusalem, Kraków,
  Kuala Lumpur, Kyiv, Manila, San José, Warsaw): © OpenStreetMap contributors, ODbL.
- Paris: Ville de Paris, Quartiers administratifs, ODbL.
- IGN (Paris, the petite couronne's communes): IGN, ADMIN EXPRESS, Licence
  Ouverte 2.0.
- Lyon: Ville de Lyon, Périmètres des conseils de quartier (data.grandlyon.com),
  Licence Ouverte 2.0.
- Marseille: Datactivist, quartiers de Marseille (Métropole Aix-Marseille-Provence
  open data), Licence Ouverte 2.0.
- Toulouse: Mairie de Toulouse, grands quartiers, Licence Ouverte 2.0.
- Aix-en-Provence: IGN, Contours... IRIS®, grouped into quartiers, Licence Ouverte /
  Open Licence version 2.0.
- Ajaccio: IGN, Contours... IRIS®, grouped into quartiers, Licence Ouverte / Open Licence
  version 2.0.
- Amiens: IGN, Contours... IRIS®, grouped into quartiers, Licence Ouverte / Open Licence
  version 2.0.
- Amsterdam: Gemeente Amsterdam, wijken, CC0 1.0.
- Angers: Angers Loire Métropole, quartiers de la Ville d'Angers (data.angers.fr), Open
  Database License (ODbL).
- Annecy: IGN, Contours... IRIS®, grouped into quartiers, Licence Ouverte / Open Licence
  version 2.0.
- Antwerp: Copyright Stad Antwerpen, wijken (the city's terms of use).
- Athens: City of Athens, neighbourhoods, CC BY 4.0.
- Auckland: Sourced from the LINZ Data Service and licensed for reuse under the
  CC BY 4.0 licence (NZ Suburbs and Localities).
- Avignon: Ville d'Avignon, limites des mairies de quartier (DataSud), Licence Ouverte /
  Open Licence version 2.0.
- Basel: Geodaten Kanton Basel-Stadt, Wohnviertel, CC BY 4.0.
- Bayonne: Ville de Bayonne, quartiers officiels, Licence Ouverte / Open Licence version
  2.0.
- Berlin: Geoportal Berlin, ALKIS Berlin Ortsteile, Datenlizenz Deutschland –
  Zero 2.0.
- Besançon: Grand Besançon Métropole, quartiers de la Ville de Besançon (licence not
  stated by the publisher).
- Biarritz: IGN, Contours... IRIS®, grouped into quartiers, Licence Ouverte / Open Licence
  version 2.0.
- Bilbao: Fuente de los datos: Ayuntamiento de Bilbao, barrios (GeoBilbao), Creative Commons
  Reconocimiento 3.0 España (CC BY 3.0 ES).
- Bogotá: Unidad Administrativa Especial de Catastro Distrital (IDECA), UPZ,
  CC BY 4.0.
- Bologna: Comune di Bologna - U.I. Ufficio Comunale di Statistica, aree statistiche,
  CC BY 4.0.
- Bordeaux: Ville de Bordeaux, secteurs de vie (Bordeaux Métropole open data),
  Licence Ouverte.
- Boston: Boston Maps (Analyze Boston), BPDA Neighborhood Boundaries, Open Data
  Commons Public Domain Dedication and License (PDDL).
- Bratislava: Magistrát hlavného mesta SR, Bratislava, mestské časti, CC BY 4.0.
- Brest: Brest métropole, quartiers administratifs de Brest, Licence Ouverte / Open
  Licence version 2.0.
- Brisbane: © Brisbane City Council 2025 © State of Queensland (Department of
  Resources) 2025, suburb boundaries, CC BY 4.0.
- Brussels: Ville de Bruxelles, ibsa.brussels, perspective.brussels, Monitoring
  des quartiers, CC0 1.0.
- Buenos Aires: Gobierno de la Ciudad de Buenos Aires, barrios, CC BY 2.5 AR.
- Caen: IGN, Contours... IRIS®, grouped into quartiers, Licence Ouverte / Open Licence
  version 2.0.
- Cannes: Mairie de Cannes, quartiers politiques (data.cannes.fr), Licence Ouverte.
- Cape Town: City of Cape Town, Corporate GIS, major suburbs (through the Western
  Cape Government's mirror; the City's open data terms of use).
- Chicago: City of Chicago Data Portal, Boundaries – Neighborhoods. "This site
  provides applications using data that has been modified for use from its
  original source, www.cityofchicago.org, the official website of the City of
  Chicago. The City of Chicago makes no claims as to the content, accuracy,
  timeliness, or completeness of any of the data provided at this site. The
  data provided at this site is subject to change at any time. It is
  understood that the data provided at this site is being used at one's own
  risk."
- Clermont-Ferrand: IGN, Contours... IRIS®, Licence Ouverte / Open Licence version 2.0.
- Copenhagen: Københavns Kommune, kvarterer, CC BY 4.0.
- Dijon: Dijon métropole, ateliers de quartiers de Dijon (Open Dijon), Licence Ouverte
  Version 2.0.
- Dhaka: Bangladesh Bureau of Statistics (BBS), OCHA ROAP, 2015 thanas and upazilas
  (through geoBoundaries), Creative Commons Attribution 3.0 Intergovernmental
  Organisations (CC BY 3.0 IGO).
- Dublin: Dublin City Council, electoral divisions (Smart Dublin), Creative
  Commons Attribution.
- Edinburgh: Copyright City of Edinburgh Council, contains Ordnance Survey data
  © Crown copyright and database right 2026, natural neighbourhoods, Open
  Government Licence v3.0.
- Florence: Comune di Firenze, aree elementari 2021, Creative Commons Attribuzione
  4.0 Internazionale (CC-BY 4.0).
- Frankfurt: quelle: Stadt Frankfurt am Main, www.frankfurt.de, Stadtteile (WFS
  Stadtgebietsgliederung), Datenlizenz Deutschland – Namensnennung – Version 2.0.
- Geneva: « Données SITG », secteurs statistiques, extracted at each build
  (accès libre).
- Ghent: Stad Gent, stadswijken, Modellicentie Gratis Hergebruik.
- Glasgow: Glasgow City Council, community council areas, Open Government
  Licence v3.0; Crown copyright and database right 2022, licensed under the One
  Scotland Mapping Agreement.
- Gothenburg, Stockholm: Statistiska centralbyrån (SCB), RegSO 2025, CC0.
- Grenoble: Ville de Grenoble, unions de quartier (data.metropolegrenoble.fr), Open
  Data Commons Open Database License 1.0.
- Hamburg: Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und
  Vermessung (LGV), Stadtteile, Datenlizenz Deutschland – Namensnennung – 2.0.
- Helsinki: Helsingin kaupunki, kaupunkimittauspalvelut, osa-alueet, CC BY 4.0.
- Johannesburg: Statistics South Africa, Census 2011 sub places (UCT Libraries'
  republication); Stats SA is the source of the basic data.
- La Rochelle: Ville de La Rochelle, comités de quartier (opendata.agglo-larochelle.fr),
  data of 2012-05-23, Licence ouverte / Open Licence.
- Las Palmas: Origen de los datos: Ayuntamiento de Las Palmas de Gran Canaria, Callejero
  Municipal, barrios, Reutilización de la información del sector público para el ámbito
  del sector público estatal.
- Le Havre: Ville du Havre, conseils de quartier (Le Havre Seine Métropole open data),
  Licence ODbL.
- Le Mans: IGN, Contours... IRIS®, grouped into quartiers, Licence Ouverte / Open
  Licence version 2.0.
- Lille: Ville de Lille, limite des quartiers de Lille et de ses communes associées
  (data.lillemetropole.fr), Licence Ouverte v2.0 (Etalab).
- Limoges: Limoges Métropole, quartiers de la Ville de Limoges (licence not stated by
  the publisher).
- Lisbon: Câmara Municipal de Lisboa, Limite_Cartografia (freguesias), CC0.
- Liverpool: Liverpool City Council, Liverpool Electoral Ward Boundaries, UK Open
  Government Licence (OGL).
- London: Source: Office for National Statistics licensed under the Open
  Government Licence v.3.0, Wards (December 2024). Contains OS data © Crown
  copyright and database right 2024.
- Lorient: Lorient Agglomération, quartiers de l'aire urbaine (licence not stated by the
  publisher).
- Los Angeles: Los Angeles Times, LA Times Neighborhoods, CC BY 4.0.
- Madrid: Ayuntamiento de Madrid, barrios, CC BY 4.0.
- Málaga: Ayuntamiento de Málaga, datos abiertos, Sistema de Información
  Cartográfica – Barrio, CC BY-SA 4.0.
- Metz: Eurométropole de Metz, quartiers (DataGrandEst), Licence Ouverte / Open Licence
  Version 2.0.
- Miami: © City of Miami, Department of Innovation and Technology, GIS Team,
  Miami Neighborhoods, CC BY 4.0.
- Milan: Comune di Milano, Nuclei d'Identità Locale (NIL), CC BY 4.0.
- Montpellier: Montpellier Méditerranée Métropole, sous-quartiers de Montpellier (producer
  OpenStreetMap), Open Data Commons Open Database License (ODbL).
- Montréal: Ville de Montréal, quartiers de référence en habitation, CC BY 4.0.
- Munich: Landeshauptstadt München – GeodatenService, Stadtbezirke, Datenlizenz
  Deutschland – Namensnennung – 2.0.
- Nancy: Métropole du Grand Nancy, quartiers des communes du Grand Nancy (Ville de
  Nancy's quartiers; licence not stated by the publisher).
- Nantes: Nantes Métropole, quartiers de la ville de Nantes, Licence Ouverte.
- Naples: Comune di Napoli, territorio cittadino suddiviso in quartieri, IODL 2.0.
- New York: NYC Department of City Planning (DCP), 2020 Neighborhood Tabulation
  Areas, NYC Open Data (licence not stated by the publisher).
- Nice: IGN, Contours... IRIS®, grouped into quartiers, Licence Ouverte / Open Licence
  version 2.0.
- Nîmes: Nîmes Métropole, Comités de Quartiers de Nîmes (licence not stated by the
  publisher).
- Orléans: IGN, Contours... IRIS®, grouped into quartiers, Licence Ouverte / Open
  Licence version 2.0.
- Osaka, Tokyo: 出典：政府統計の総合窓口(e-Stat)（https://www.e-stat.go.jp/）,
  2020 census small areas, merged into towns (e-Stat terms of use, compatible
  with CC BY 4.0).
- Oslo: Kartverket, grunnkretser (Geonorge), CC BY 4.0; grouped into delbydeler
  with Oslo kommune's key (licence not stated by the publisher).
- Ottawa: Ottawa Neighbourhood Study, Neighbourhood Boundaries Gen 3. Contains
  information licensed under the Open Government Licence – City of Ottawa.
- Palermo: Comune di Palermo, perimetrazione quartieri storici, CC BY 4.0 IT.
- Melbourne, Perth, Sydney: Based on Australian Bureau of Statistics data,
  Suburbs and Localities (ASGS 2021), CC BY 4.0.
- Poznań: Miasto Poznań, serwis poznan.pl, osiedla (free reuse naming the
  source).
- Prague: datový podklad © IPR Praha, městské části, CC BY 4.0.
- Reims: IGN, Contours... IRIS®, grouped into quartiers, Licence Ouverte / Open Licence
  version 2.0.
- Rennes: Rennes Métropole, périmètres des 45 sous-quartiers de Rennes, Licence ODbL 1.0.
- Riga: Rīgas valstspilsētas pašvaldība (data.gov.lv), Rīgas apkaimes, Creative
  Commons Attribution 4.0.
- Rome: Roma Capitale, suddivisioni toponomastiche (licence not stated by the
  publisher).
- Rotterdam: CBS en Kadaster, Wijken en buurten 2025 (PDOK), CC0.
- Rouen: IGN, Contours... IRIS®, grouped into quartiers, Licence Ouverte / Open Licence
  version 2.0.
- Saint-Étienne: Ville de Saint-Étienne, Saint-Etienne Métropole, quartiers (flux de
  données géographiques ouvertes), Licence Ouverte / Open Licence version 2.0.
- San Francisco: DataSF, City and County of San Francisco, SF Find Neighborhoods,
  Public Domain U.S. Government.
- San Sebastián: © Donostiako Udala - Ayuntamiento de Donostia / San Sebastián, Donostiako
  auzoak (licence not stated by the publisher).
- Seattle: City of Seattle, Office of Planning and Community Development, Neighborhood
  Map Atlas Neighborhoods, PDDL.
- Seoul: 서울특별시 (Seoul Open Data Plaza, OA-22160), administrative dong
  boundaries, 공공누리 1유형 (KOGL Type 1).
- Seville: Fuente: Gerencia de Urbanismo (IDE.Sevilla), barrios, Reconocimiento 4.0
  Internacional (CC BY 4.0).
- Singapore: Contains information from the Urban Redevelopment Authority's
  planning area boundaries (dataset d_4765db0e87b9c86336792efe8a1f7a66) accessed
  at each build from data.gov.sg, which is made available under the terms of the
  Singapore Open Data Licence version 1.0.
- Sofia: ОП „Софияплан“, квартали (licence not stated by the publisher).
- Strasbourg: Eurométropole de Strasbourg, découpage de la ville de Strasbourg en 28
  quartiers, Licence Ouverte (Etalab).
- São Paulo: Prefeitura de São Paulo, GeoSampa, distritos municipais, CC BY-SA
  4.0.
- Tel Aviv: Tel Aviv-Yafo Municipality open data, neighbourhoods (free to share
  and adapt with credit).
- The Hague: Gemeente Den Haag, wijken, CC0 1.0.
- Thessaloníki: Δήμος Θεσσαλονίκης, δημοτικές κοινότητες (the municipality's open
  licence).
- Toronto: City of Toronto, Neighbourhoods. Contains information licensed under
  the Open Government Licence – Toronto.
- Toulon: Métropole Toulon Provence Méditerranée, comités d'intérêt local of the Mairie
  de Toulon (licence not stated by the publisher).
- Tours: Ville de Tours, quartiers (data.tours-metropole.fr), Licence Ouverte v2.0
  (Etalab).
- Turin: Comune di Torino, zone statistiche, CC BY 4.0.
- Utrecht: Gemeente Utrecht, buurten (Gis Buurt), CC-0 (1.0).
- Valencia: Fuente de los datos: Ajuntament de València – Dades Obertes, barris,
  CC BY 4.0.
- Vancouver: City of Vancouver, local area boundary. Contains information
  licensed under the Open Government Licence – Vancouver.
- Versailles: Communauté d'Agglomération Versailles Grand Parc, quartiers municipaux
  de Versailles, Licence Ouverte/Open License Version 2.0.
- Vienna: Datenquelle: Stadt Wien – data.wien.gv.at, Bezirksgrenzen, CC BY 4.0.
- Villeurbanne: Métropole de Lyon, quartiers des communes de la Métropole de Lyon
  (data.grandlyon.com), Licence Ouverte / Open Licence version 2.0.
- Vilnius: Vilniaus miesto savivaldybė, seniūnijos (licence not stated by the
  publisher).
- Washington DC: DC Office of Planning (https://planning.dc.gov/), Neighborhood
  Clusters, CC BY 4.0.
- Wrocław: Gmina Wrocław, System Informacji Przestrzennej Wrocławia (geoportal.wroclaw.pl),
  granice osiedli ("Warunki wykorzystania i przetwarzania: brak ograniczeń").
- Zagreb: Grad Zagreb, mjesni odbori, Otvorena dozvola (the source and date of
  last change named).
- Zurich: Stadt Zürich, statistische Quartiere, CC0.

**City names and points (`survey-candidates.json`)**
- GeoNames (geonames.org), CC BY 4.0.
- French communes of 50,000 and more: Etalab geo API (communes, populations,
  town halls), Licence Ouverte 2.0.

Every key is the rounded coordinate of a public area, and every area is
named by its city, a district or a public street or square. Venue names are the trading names
the city registers publish; nothing about a person beyond that.
