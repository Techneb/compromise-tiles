"""AREA_SOURCES: one neighbourhood-boundary source per city, and its fetchers."""
import csv, io, json, os, re, urllib.error, urllib.parse, urllib.request
from builder import extract
from builder.net import get, get_json, SECRET_ENVS
from builder.formats import _zip_members, xlsx_rows
from builder.apis import arcgis, wfs

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
        found, (boundaries, places) = {}, extract._osm.areas(rect)
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
