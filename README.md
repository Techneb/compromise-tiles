# compromise-tiles

Open city data used by the Compromise iOS app, one JSON file per map cell.

## Where the tiles are

The tiles are served from Cloudflare R2 at
`https://tiles.alephb.uk`, under
`tiles/<layer>/<key>.json` (gzipped at rest), with `config.json` at the
root. Layers: `buildings/` and `terraces-v2/` per ~200 m
cell, keyed `int(lat*500),int(lon*500)`; `venues/` per 1/50° cell (name,
point, amenity, outdoor seating, source); `sources/` per ~200 m cell
names the permit feed and building source each cell was built from;
`streets/` per ~200 m cell holds street lines, `streets-main/` per
1/25° cell the main roads alone and `places/` on the same cells
neighbourhood names and points, all three for the areas `cities.json`
flags with `"streets": true` only. A
building whose source gives no height is written at 15 m with
`"guessed": true` (builds since 2026-10-04; a tile built before carries
no flag). With `--buildings-v2`, `buildings-v2/` holds the same footprints
as integer deltas, a third of the bytes; [BUILDINGS.md](BUILDINGS.md)
gives its format and the size study behind it.

This repository holds the generator (`make_tiles.py`), the areas it
builds (`cities.json`, with municipal boundaries in `boundaries/`), the
coverage feed (`coverage.json`, also published on R2), the store's numbers
(`stats.json` on R2: size and file count per layer, build time per city, written by
`stats.py` after each build) and the tiles built
off GitHub under `tiles/`. GitHub Actions (`.github/workflows/tiles.yml`)
builds the rest on a schedule and uploads it to R2; those tiles are not
kept here. OpenStreetMap is read from Geofabrik extracts with
osmium-tool; city and national feeds are downloaded in bulk.

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

## Licences and credits

Each tile holds data from the sources below, depending on where it lies.
Licences are given as each publisher states them; where a publisher states
none, this says so rather than guessing.

**Everywhere**
- OpenStreetMap (venues and their outdoor seating; streets; place names; building footprints where no
  city or national source is used): © OpenStreetMap contributors, ODbL.
  Because these tiles include OpenStreetMap data, the tile set is
  published under the ODbL (share-alike).

**Terrace permits and outdoor seats**
- Paris: Ville de Paris, ODbL.
- Toulouse: Mairie de Toulouse, Licence Ouverte 2.0.
- Strasbourg: Eurométropole de Strasbourg, Licence Ouverte 2.0.
- Anglet: Ville d'Anglet, Licence Ouverte 2.0.
- Rouen: Ville de Rouen, 2021 edition, Licence Ouverte 2.0.
- Lorient: Ville de Lorient, 2020 edition, Licence Ouverte 2.0.
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
- Vaughan, Toronto, Montréal: Natural Resources Canada, Automatically
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
- Montréal (the agglomeration, `montreal.geojson`): Ville de Montréal,
  Limites administratives de l'agglomération de Montréal (arrondissements
  et villes liées), CC BY 4.0; its 34 polygons dissolved into one.

**City names and points (`survey-candidates.json`)**
- GeoNames (geonames.org), CC BY 4.0.
- French communes of 50,000 and more: Etalab geo API (communes, populations,
  town halls), Licence Ouverte 2.0.

Every key is the rounded coordinate of a public area, and every area is
named by its city, a district or a public street or square. Venue names are the trading names
the city registers publish; nothing about a person beyond that.
