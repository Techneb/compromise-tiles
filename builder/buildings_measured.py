"""Building heights measured from elevation: lidar, USGS ground, Seattle, Virginia Beach, Yarra's meshes."""
import array, math, os
from builder.cells import padded
from builder.net import download, get, SourceError
from builder.projections import bng, wgs84_to_tm
from builder.shapes import building, number, outer_rings
from builder.formats import geotiff, mesh_faces
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
