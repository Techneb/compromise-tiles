# compromise-tiles

Open city data used by the Compromise iOS app, one JSON file per map cell.

## Where the tiles are

The tiles are served from Cloudflare R2 at
`https://tiles.alephb.uk`, under
`tiles/<layer>/<key>.json` (gzipped at rest), with `config.json` at the
root. Layers: `buildings/`, `terraces-v2/` and `communes/` per ~200 m
cell, keyed `int(lat*500),int(lon*500)`; `venues/` per 1/50° cell (name,
point, amenity, outdoor seating, source).

This repository holds the generator (`make_tiles.py`), the areas it
builds (`cities.json`, with municipal boundaries in `boundaries/`), the
coverage feed (`coverage.json`, also published on R2) and the tiles built
off GitHub under `tiles/`. GitHub Actions (`.github/workflows/tiles.yml`)
builds the rest on a schedule and uploads it to R2; those tiles are not
kept here. OpenStreetMap is read from Geofabrik extracts with
osmium-tool; city and national feeds are downloaded in bulk.

## Licences and credits

Each tile holds data from the sources below, depending on where it lies.
Licences are given as each publisher states them; where a publisher states
none, this says so rather than guessing.

**Everywhere**
- OpenStreetMap (venues and their outdoor seating; building footprints where no
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
- New York: NYC Open Data (licence not stated by the publisher).
- Chicago: City of Chicago Data Portal. "The City of Chicago makes no
  claims as to the content, accuracy, timeliness, or completeness of any
  of the data provided at this site. The data provided at this site is
  subject to change at any time. It is understood that the data provided
  at this site is being used at one's own risk."
- San Francisco: DataSF, City and County of San Francisco, PDDL.

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
- Denver: City and County of Denver, Building Outlines 2022 (licence not
  stated by the publisher).
- Cape Town: City of Cape Town, 2D Building Footprints (licence not stated
  by the publisher).
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
- Spain (Madrid, Seville, Barcelona, Valencia, Zaragoza, Málaga, Palma,
  Las Palmas, Murcia, Alicante, Córdoba, Valladolid, Vigo, Gijón):
  outlines and heights derived from the Dirección General del Catastro's
  INSPIRE Buildings service (INSPIRE access and use licence). A derived
  dataset, not cadastral information; the cadastre's own files are not
  redistributed.

**Communes (France)**
- Etalab geo API, Licence Ouverte 2.0; IGN Admin Express through apicarto
  (Licence Ouverte 2.0) when the geo API does not answer.

Every key is the rounded coordinate of a public area, and every area is
named by its city, a district or a public street or square. Venue names are the trading names
the city registers publish; nothing about a person beyond that.
