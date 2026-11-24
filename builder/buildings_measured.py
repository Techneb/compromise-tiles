"""Building heights measured from elevation: lidar and other heights grids (London, Washington DC, Naples), USGS ground,
Seattle, Virginia Beach, Yarra's meshes."""
import array, json, math, os
from builder.cells import padded
from builder.net import download, get, get_range, SourceError, with_intermediate
from builder.projections import bng, lcc, wgs84_to_tm, wgs84_to_utm
from builder.shapes import building, number, outer_rings
from builder.formats import esri_ascii, geotiff, mesh_faces, tiff_block, tiff_tags
from builder.extract import osm_buildings
from builder.apis import arcgis

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

def kept(cache, key, read, most):
    """`read()`'s answer for `key`, held in `cache` (the most recent last), the oldest dropped past `most`."""
    if key in cache:
        cache[key] = cache.pop(key)
        return cache[key]
    cache[key] = value = read()
    while len(cache) > most: del cache[next(iter(cache))]
    return value

def decimetres(heights):
    """Heights above ground in metres (None where the source has none) as a chunk: array('h') of decimetres, VOID
    where None or within 0.5 m of the ground (ground, or a void the source filled from it)."""
    return array.array("h", (VOID if h is None or abs(h) < 0.5 else max(-32767, min(32767, round(h * 10))) for h in heights))

def on_grid(name, grids, e0, n0, size):
    """Every grid (geotiff()'s or esri_ascii()'s answer) on the chunk's own: no resampling, one pixel a metre."""
    if any(g[:4] != (e0, n0 + size, size, size) for g in grids):
        raise SourceError(f"{name}: chunk {e0},{n0} came back on another grid ({[g[:4] for g in grids]})")

def surface_less_ground(surface, ground, nodata=-1e30):
    return decimetres(None if a <= nodata or g <= nodata else a - g for a, g in zip(surface[4], ground[4]))

def lidar_chunk(e0, n0):
    """Heights above ground in one chunk, decimetres: DSM (first return) minus DTM. VOID where either has no data or
    the two are within 0.5 m: ground, or a void the composite filled from the DTM — dark glass returns nothing, and
    30 St Mary Axe reads under 0.5 m over most of its footprint (its 90th percentile 180.3 m with them left out)."""
    def read():
        surface, ground = (lidar_tiff(k, e0, n0, e0 + LIDAR_CHUNK, n0 + LIDAR_CHUNK) for k in ("dsm", "dtm"))
        on_grid("lidar", (surface, ground), e0, n0, LIDAR_CHUNK)
        return surface_less_ground(surface, ground)
    return kept(_lidar, (e0, n0), read, LIDAR_KEPT)

def pixel_centres(ring):
    """The 1 m pixels whose centre the ring (grid metres) holds, each as its south-west corner (east, north), row by row."""
    edges = list(zip(ring, ring[1:] + ring[:1]))
    for north in range(math.floor(min(y for _, y in ring)), math.ceil(max(y for _, y in ring))):
        y = north + 0.5
        xs = sorted(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]) for a, b in edges if (a[1] > y) != (b[1] > y))
        for x0, x1 in zip(xs[::2], xs[1::2]):
            for east in range(math.ceil(x0 - 0.5), math.floor(x1 - 0.5) + 1): yield east, north

def raster_height(outline, project, size, chunk, origin=(0, 0)):
    """A heights grid's height for one footprint: the 90th percentile of the heights of the 1 m pixels whose centre it
    holds (the maximum is a mast or a spire, the median half a pitched roof), ground and void pixels left out. None
    under 4 pixels, or under 2 m: a building the survey saw as ground was built after it. `project` takes (lat, lon)
    to the grid's metres, `chunk(e0, n0)` answers the size × size chunk whose south-west corner that is (row 0
    north, decimetres, VOID), the chunks laid from `origin`."""
    heights, at, pixels = [], None, None
    for east, north in pixel_centres([project(p["latitude"], p["longitude"]) for p in outline]):
        e0, n0 = origin[0] + (east - origin[0]) // size * size, origin[1] + (north - origin[1]) // size * size
        if at != (e0, n0): at, pixels = (e0, n0), chunk(e0, n0)
        h = pixels[(n0 + size - 1 - north) * size + east - e0]
        if h != VOID: heights.append(h)
    if len(heights) < 4: return None
    heights.sort()
    rank = LIDAR_PERCENTILE * (len(heights) - 1)
    lo = int(rank)
    h = (heights[lo] + (heights[min(lo + 1, len(heights) - 1)] - heights[lo]) * (rank - lo)) / 10
    return h if h >= 2 else None

def on_osm(height):
    """A combine row's fetch: OpenStreetMap's footprints, each with `height(outline)` where it reads one."""
    return lambda rect: [building(b["outline"], h) for b in osm_buildings(rect, True) if (h := height(b["outline"])) is not None]

def lidar_height(outline): return raster_height(outline, bng, LIDAR_CHUNK, lidar_chunk)

lidar = on_osm(lidar_height)

# DC GIS's 2024 LiDAR Normalized Digital Surface Model (CC0): height above ground, 1 m float32, on its own grid,
# Maryland State Plane metres (NAD83 (HARN), EPSG:2804), read whole-pixel through exportImage (nearest neighbour,
# the box on the grid's metres: no resampling). The server's limit is 15,000 × 4,100 pixels a request. OSM's
# footprints in DC came from DC GIS's own outlines, on the same datum: projected with no shift. Negative heights are
# its no-data (water reads −5).
DC_NDSM = ("https://imagery.dcgis.dc.gov/dcgis/rest/services/Lidar/nDSM_2024/ImageServer/exportImage?bbox={},{},{},{}"
           "&bboxSR=2804&imageSR=2804&size={},{}&format=tiff&pixelType=F32&interpolation=RSP_NearestNeighbor&compression=None&f=image")
MARYLAND = lcc(6_378_137.0, 1 / 298.257222101, 39.45, 38.3, 37 + 40 / 60, -77, 400_000, 0)
DC_CHUNK = 2000      # metres a side: one request (16 MB uncompressed, ~20 s; LZW's pure-Python decoding costs what it saves)
_dc = {}

def dc_tiff(e0, n0, e1, n1):
    return get(DC_NDSM.format(e0, n0, e1, n1, e1 - e0, n1 - n0), parse=geotiff)

def dc_chunk(e0, n0):
    def read():
        tiff = dc_tiff(e0, n0, e0 + DC_CHUNK, n0 + DC_CHUNK)
        on_grid("DC nDSM", (tiff,), e0, n0, DC_CHUNK)
        return decimetres(v if v > 0 else None for v in tiff[4])
    return kept(_dc, (e0, n0), read, LIDAR_KEPT)

def dc_height(outline): return raster_height(outline, MARYLAND[0], DC_CHUNK, dc_chunk)

washington_dc = on_osm(dc_height)

# The Scottish Remote Sensing Portal's LiDAR for Scotland Phase V (2020–21, Open Government Licence v3): a DSM and a
# DTM, 0.5 m, in 5 km GeoTIFFs on British National Grid (an OS 10 km square and its quarter, NT27SE), LZW in 256 px
# tiles, on a public S3 bucket with no WCS: each chunk is read by HTTP range, one request a row of tiles. The grid
# counts half metres, so a footprint's pixels are a quarter square metre each (the rule's 4 pixels are 1 m²).
SRSP = "https://srsp-open-data.s3.eu-west-2.amazonaws.com/lidar/phase-5/{}/27700/gridded/{}_50CM_{}_PHASE5.tif"
SRSP_FILE = 10_000   # half metres a file's side
SRSP_CHUNK = 2000    # half metres a chunk's side (1 km, 8 MB): divides a file
SRSP_HEAD = 65_535   # the bytes a file's tags lie in (6.6 KB for a 5 km file)
_srsp, _srsp_tags = {}, {}

def os_quarter(east, north):
    """The Ordnance Survey 5 km square holding a BNG point, as Phase V names its files: NT27SE."""
    e, n = int(east) // 100_000, int(north) // 100_000
    letters = "ABCDEFGHJKLMNOPQRSTUVWXYZ"
    first, second = letters[(19 - n) // 5 * 5 + (e + 10) // 5], letters[(19 - n) * 5 % 25 + e % 5]
    return (f"{first}{second}{int(east) % 100_000 // 10_000}{int(north) % 100_000 // 10_000}"
            f"{'N' if north % 10_000 >= 5000 else 'S'}{'E' if east % 10_000 >= 5000 else 'W'}")

def srsp_range(url, start, end): return get_range(url, start, end)[0]

def srsp_grid(kind, e0, n0):
    """One chunk of the DSM or the DTM (south-west corner e0, n0 in half metres), as geotiff() answers."""
    fe, fn, c = e0 // SRSP_FILE * SRSP_FILE, n0 // SRSP_FILE * SRSP_FILE, SRSP_CHUNK
    url = SRSP.format(kind, os_quarter(fe / 2, fn / 2), kind.upper())
    if url not in _srsp_tags: _srsp_tags[url] = tiff_tags(srsp_range(url, 0, SRSP_HEAD))
    order, tags = _srsp_tags[url]
    tw, offsets, sizes = tags[322][0], tags[324], tags[325]
    if (tags[256][0], tags[257][0], tags[33922][3] * 2, tags[33922][4] * 2, tags[33550][:2]) != (SRSP_FILE, SRSP_FILE, fe, fn + SRSP_FILE, (0.5, 0.5)):
        raise SourceError(f"{url}: not a 5 km file at 0.5 m on its square")
    across, x0, y0 = -(-SRSP_FILE // tw), e0 - fe, fn + SRSP_FILE - n0 - c   # the chunk's top-left pixel in the file
    out = array.array("f", bytes(4 * c * c))
    for ty in range(y0 // tw, (y0 + c - 1) // tw + 1):
        first, last = ty * across + x0 // tw, ty * across + (x0 + c - 1) // tw
        if any(offsets[k + 1] != offsets[k] + sizes[k] for k in range(first, last)): raise SourceError(f"{url}: tiles not in order")
        span = srsp_range(url, offsets[first], offsets[last] + sizes[last] - 1)
        for k in range(first, last + 1):
            tx, values = k - ty * across, tiff_block(span[offsets[k] - offsets[first]:][:sizes[k]], order, tags)
            a, b = max(x0, tx * tw), min(x0 + c, tx * tw + tw)
            for r in range(max(y0, ty * tw), min(y0 + c, ty * tw + tw)):
                out[(r - y0) * c + a - x0:(r - y0) * c + b - x0] = values[(r - ty * tw) * tw + a - tx * tw:(r - ty * tw) * tw + b - tx * tw]
    return e0, n0 + c, c, c, out

def srsp_chunk(e0, n0):
    def read():
        surface, ground = srsp_grid("dsm", e0, n0), srsp_grid("dtm", e0, n0)
        return surface_less_ground(surface, ground, nodata=-9999)
    return kept(_srsp, (e0, n0), read, LIDAR_KEPT)

def scotland_height(outline):
    return raster_height(outline, lambda lat, lon: tuple(2 * v for v in bng(lat, lon)), SRSP_CHUNK, srsp_chunk)

scotland = on_osm(scotland_height)

# The Città Metropolitana di Napoli's 2009 LiDAR (its SIT; no licence stated): a DSM and a DTM, 1 m Esri ASCII grids
# in WGS84 / UTM 33N, 500 m tiles laid from 327 E, 244 N over Naples (the province's other two grids lie off the
# city). Each layer's WFS tile index gives a tile's URL by its corner; the two layers number their tiles apart. The
# server sends its certificate without Sectigo's intermediate.
NAPLES_INDEX = ("https://sit.cittametropolitana.na.it/geoserver/ows?service=WFS&version=2.0.0&request=GetFeature"
                "&typeNames=sit:quadro_unione_lidar_{}&propertyName=geom,url&outputFormat=application/json")
NAPLES_TILE = 500
NAPLES_INTERMEDIATE = "http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt"
NAPLES_ORIGIN = (327, 244)
NAPLES_KEPT = 256    # chunks held (0.5 MB each)
_naples, _naples_index = {}, {}   # _naples_index also holds the TLS context

def tile_index(collection):
    """A tile index's URLs by each tile's south-west corner, rounded to the metre (the grids' own corners)."""
    out = {}
    for f in collection["features"]:
        ring = f["geometry"]["coordinates"][0][0]
        out[(round(min(x for x, _ in ring)), round(min(y for _, y in ring)))] = f["properties"]["url"].strip()
    return out

def naples_tile(kind, e0, n0):
    """One tile of the DSM or the DTM, None where the survey has none."""
    if "tls" not in _naples_index: _naples_index["tls"] = with_intermediate(NAPLES_INTERMEDIATE)
    if kind not in _naples_index:
        _naples_index[kind] = tile_index(get(NAPLES_INDEX.format(kind), parse=json.loads, context=_naples_index["tls"]))
    url = _naples_index[kind].get((e0, n0))
    return url and get(url, parse=esri_ascii, context=_naples_index["tls"])

def naples_chunk(e0, n0):
    def read():
        surface, ground = naples_tile("dsm", e0, n0), naples_tile("dtm", e0, n0)
        if not surface or not ground: return array.array("h", [VOID]) * (NAPLES_TILE * NAPLES_TILE)
        on_grid("Naples lidar", (surface, ground), e0, n0, NAPLES_TILE)
        return surface_less_ground(surface, ground)
    return kept(_naples, (e0, n0), read, NAPLES_KEPT)

def naples_height(outline):
    return raster_height(outline, lambda lat, lon: wgs84_to_utm(lat, lon, 33), NAPLES_TILE, naples_chunk, NAPLES_ORIGIN)

naples = on_osm(naples_height)

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
    heights = []
    for east, north in pixel_centres(ring):
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

yarra = on_osm(yarra_height)
