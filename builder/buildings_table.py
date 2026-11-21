"""CITY_BUILDINGS: one row per city height feed, and which feed a cell takes."""
import re
from builder.cells import boundary, in_feed, in_france
from builder.shapes import combined, difference, footprints, from_floors, number
from builder.formats import geopackage, shapefile
from builder.extract import osm_buildings
from builder.apis import arcgis, in_bbox, intersects, opendatasoft, socrata, wfs
from builder.buildings_citygml import citygml_archive, citygml_tiles, espoo, estonia_lod1_buildings, gipuzkoa, hamburg, lod1_archive, plateau
from builder.buildings_measured import lidar, seattle, virginia_beach, yarra
from builder.buildings_vector import bag3d, beoland, BUENOS_AIRES_TEJIDO, catastro, DBT_FIELDS, dbt_height, ign_buildings, LAZIO_DBGT, lazio_token, liguria, MILAN_DBT, NRCAN_GTA, NRCAN_HALIFAX, NRCAN_HAMILTON, NRCAN_LAKE_ERIE, NRCAN_MONTREAL, NRCAN_QUEBEC, NRCAN_WINNIPEG, oakland, socrata_buildings, turin, urbis

def building_row(name, lat, lon, fetch, osm_on_error=False, boundary=None, combine=None):
    return dict(city=name, lat=lat, lon=lon, fetch=fetch, osm_on_error=osm_on_error, boundary=boundary, combine=combine)

# Santa Clara County's 2020 lidar footprints (county-wide, no licence stated): Building_H is feet above
# ground (houses 10–30, San Jose City Hall 285.6).
santa_clara = lambda r: footprints(arcgis(
    "https://maps.santaclaracounty.gov/server/rest/services/opendata/SCCGISHUB/MapServer/40", r, ["Building_H"]),
    lambda p: p["Building_H"] * 0.3048 if p.get("Building_H") else None)

USA_STRUCTURES = "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/USA_Structures_View/FeatureServer/0"

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
        ["NUM_FLOORS"]), lambda p: from_floors(number(p.get("NUM_FLOORS"))))),
    building_row("Holon", (31.988, 32.039), (34.755, 34.815), boundary=boundary("holon"), fetch=lambda r: footprints(arcgis(
        "https://services2.arcgis.com/cjDo9oPmimdHxumn/arcgis/rest/services/Buildings_shp/FeatureServer/0", r,
        ["NUM_FLOORS"], oid="FID"), lambda p: from_floors(number(p.get("NUM_FLOORS"))))),
    building_row("Herzliya", (32.144, 32.203), (34.787, 34.865), boundary=boundary("herzliya"), fetch=lambda r: footprints(arcgis(
        "https://services3.arcgis.com/9qGhZGtb39XMVQyR/arcgis/rest/services/herzliya_reka_2023/FeatureServer/3", r,
        ["Num_floors"]), lambda p: from_floors(number(p.get("Num_floors"))))),
    # Netanya (2026-10-21): the municipality's קונטור מבנים layer (ArcGIS Online, no licence stated), NUM_FLOORS × 3 m
    # (13,810 of 14,000 with floors; ELEV is an elevator flag, not a height). Held to the municipal boundary (OSM).
    building_row("Netanya", (32.2549, 32.3606), (34.8247, 34.8961), boundary=boundary("netanya"), fetch=lambda r: footprints(arcgis(
        "https://services5.arcgis.com/ySpO9UsTrqoVbhLe/arcgis/rest/services/%D7%A7%D7%95%D7%A0%D7%98%D7%95%D7%A8_%D7%9E%D7%91%D7%A0%D7%99%D7%9D/FeatureServer/709",
        r, ["NUM_FLOORS"], oid="OBJECTID_12"), lambda p: from_floors(number(p.get("NUM_FLOORS"))))),
    # Metres, else floors × 3 m. Answers only from Israel (HTTP 571 elsewhere): from the cloud every
    # Tel Aviv cell fails and no tile is written — never an OSM tile in its place.
    building_row("Tel Aviv", (32.02, 32.16), (34.73, 34.86), lambda r: footprints(arcgis(
        "https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2/MapServer/513", r, ["gova_simplex_2019", "ms_komot"],
        oid="oid_mivne"), lambda p: p.get("gova_simplex_2019") or from_floors(p.get("ms_komot")))),
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
        "data.cityofchicago.org", "syp8-uezg", "the_geom", "stories", from_floors)),
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
        ["FLOORS"], oid="OBJECTID"), lambda p: from_floors(number(p.get("FLOORS"))), guess=False)),
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
        else from_floors(number(p.get("NUM_STORY"))))),
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
        lambda p: from_floors(number(p.get("surfacenumberoffloors"))))),
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
        ["CantPisos"], where="PISO=1"), lambda p: from_floors(number(p.get("CantPisos")), 3.5))),
    # Cupertino (2026-11-21): the county's lidar below, held to the City's boundary and listed before San Jose's box,
    # which overlaps it. The City's own Building Footprints (2011 base, BLDG_HEIGHT feet, sheds and pools among them,
    # no licence stated) were surveyed; the county's 2020 lidar has a height on every footprint.
    building_row("Cupertino", (37.2785, 37.3405), (-122.0924, -121.9957), boundary=boundary("cupertino"), fetch=santa_clara),
    building_row("San Jose", (37.1231, 37.4691), (-122.0463, -121.5858), santa_clara),
    # Regione Liguria's region-wide NC5 3D footprints (CC BY 4.0; Torre Piacentini 99.9 m); where NC5
    # has nothing (Pegli, Voltri) the cell falls to OSM. The box is Genoa's cities.json area.
    building_row("Genoa", (44.371, 44.52), (8.6657, 9.0956), liguria),
    # Sofiaplan's "Сгради 18.12.2009" (the 2009 cadastre's buildings; licence not stated by the publisher, wired
    # 2026-10-02 on the owner's decision): sgr_text's leading number is floors (26МСБЖ, Park Hotel Moskva: 78 m),
    # none is one floor, a leading minus underground floors (-1МС: the Serdika underpasses), left out (2026-11-20).
    # Towers built since 2009 are missing. The box is the layer's.
    building_row("Sofia", (42.424, 42.857), (23.077, 23.639), lambda r: footprints([f for f in arcgis(
        "https://gis.sofiaplan.bg/server/rest/services/oup_2009/oup_2009/FeatureServer/98", r, ["sgr_text"], oid="objectid")
        if not re.match(r"-\d", (f.get("properties") or {}).get("sgr_text") or "")],
        lambda p: from_floors(int((re.match(r"\d+", p.get("sgr_text") or "") or ["1"])[0])))),
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
        lambda p: from_floors(number(p.get("CONNPISOS"))))),
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
