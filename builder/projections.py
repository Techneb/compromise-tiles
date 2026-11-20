"""Map projections to and from WGS84, gathered from the feeds that need them."""
import math, re
from builder.net import SourceError

GK25 = (25, 1.0, 25_500_000)  # ETRS-GK25 (EPSG:3879): Helsinki region's plane grid

def cs92_to_wgs84(easting, northing): return tm_to_wgs84(easting, northing, 19, 0.9993, 500_000, -5_300_000)

def lcc(a, f, lat1, lat2, lat0, lon0, east0, north0):
    """A Lambert conformal conic with two standard parallels, Snyder's ellipsoidal formulas: (forward, inverse),
    forward (lat, lon) → (easting, northing) on the projection's own ellipsoid, inverse back."""
    e = math.sqrt(f * (2 - f))
    lat1, lat2, lat0, lon0 = map(math.radians, (lat1, lat2, lat0, lon0))
    m = lambda p: math.cos(p) / math.sqrt(1 - (e * math.sin(p))**2)
    t = lambda p: math.tan(math.pi / 4 - p / 2) / ((1 - e * math.sin(p)) / (1 + e * math.sin(p)))**(e / 2)
    n = (math.log(m(lat1)) - math.log(m(lat2))) / (math.log(t(lat1)) - math.log(t(lat2)))
    big_f = m(lat1) / (n * t(lat1)**n)
    rho0 = a * big_f * t(lat0)**n
    def forward(lat, lon):
        rho, theta = a * big_f * t(math.radians(lat))**n, n * (math.radians(lon) - lon0)
        return east0 + rho * math.sin(theta), north0 + rho0 - rho * math.cos(theta)
    def inverse(easting, northing):
        x, y = easting - east0, rho0 - (northing - north0)
        rho, theta = math.copysign(math.hypot(x, y), n), math.atan2(x, y)
        tt = (rho / (a * big_f))**(1 / n)
        phi = math.pi / 2 - 2 * math.atan(tt)
        for _ in range(8):
            phi = math.pi / 2 - 2 * math.atan(tt * ((1 - e * math.sin(phi)) / (1 + e * math.sin(phi)))**(e / 2))
        return math.degrees(phi), math.degrees(theta / n + lon0)
    return forward, inverse

L_EST97 = lcc(6_378_137.0, 1 / 298.257222101, 59 + 1 / 3, 58, 57.51755393055556, 24, 500_000, 6_375_000)

def lcc3301_to_wgs84(easting, northing):
    """Estonia's L-EST97 (EPSG:3301, Lambert conformal conic on GRS80) → WGS84."""
    return L_EST97[1](easting, northing)

def helmert(lat, lon, source, target, shift):
    """Geodetic (lat, lon) on the ellipsoid `source` (a, f) → on `target`, through a 7-parameter shift (tx, ty, tz in
    metres; rx, ry, rz in arc-seconds, position vector convention; s in ppm), the height taken as zero."""
    (a, f), p, l = source, math.radians(lat), math.radians(lon)
    e2 = f * (2 - f)
    nu = a / math.sqrt(1 - e2 * math.sin(p) ** 2)
    x, y, z = nu * math.cos(p) * math.cos(l), nu * math.cos(p) * math.sin(l), (1 - e2) * nu * math.sin(p)
    tx, ty, tz, *r, s = shift
    (rx, ry, rz), s = (math.radians(v / 3600) for v in r), 1 + s * 1e-6
    x, y, z = tx + s * (x - rz * y + ry * z), ty + s * (rz * x + y - rx * z), tz + s * (-ry * x + rx * y + z)
    (a, f) = target
    e2, q = f * (2 - f), math.hypot(x, y)
    p = math.atan2(z, q * (1 - e2))
    for _ in range(6): p = math.atan2(z + e2 * a / math.sqrt(1 - e2 * math.sin(p) ** 2) * math.sin(p), q)
    return math.degrees(p), math.degrees(math.atan2(y, x))

# Belgian Lambert 72 (EPSG:31370) on BD72's International 1924 ellipsoid, and EPSG's BD72 → WGS84 shift (transformation
# 15929, ~1 m), reversed by its negation: within 1e-5° of PROJ over Brussels (test_make_tiles.py).
INTERNATIONAL_1924, WGS84_ELLIPSOID = (6_378_388.0, 1 / 297), (6_378_137.0, 1 / 298.257223563)
LAMBERT_72 = lcc(*INTERNATIONAL_1924, 51 + 10 / 60 + 0.00204 / 3600, 49 + 50 / 60 + 0.00204 / 3600, 90,
                 4 + 22 / 60 + 2.952 / 3600, 150_000.013, 5_400_088.438)
BD72_TO_WGS84 = (-106.8686, 52.2978, -103.7239, 0.3366, -0.457, 1.8422, -1.2747)

def lambert72_to_wgs84(easting, northing):
    return helmert(*LAMBERT_72[1](easting, northing), INTERNATIONAL_1924, WGS84_ELLIPSOID, BD72_TO_WGS84)

def wgs84_to_lambert72(lat, lon):
    return LAMBERT_72[0](*helmert(lat, lon, WGS84_ELLIPSOID, INTERNATIONAL_1924, tuple(-v for v in BD72_TO_WGS84)))

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
    return tm_to_wgs84(easting, northing, zone * 6 - 183)

def tm_to_wgs84(easting, northing, lon0, k0=0.9996, false_easting=500_000, false_northing=0):
    """Any transverse Mercator grid on GRS80/WGS84 → WGS84 (UTM; Poland's CS92, EPSG:2180: 19°, 0.9993, −5,300,000 m)."""
    a, f = 6_378_137.0, 1 / 298.257223563
    e2 = f * (2 - f); ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    x, m = easting - false_easting, (northing - false_northing) / k0
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
    lon = math.radians(lon0) + (d - (1 + 2 * t1 + c1) * d**3 / 6
                              + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120) / cos1
    return math.degrees(phi), math.degrees(lon)

def wgs84_to_utm(lat, lon, zone):
    """WGS84 → UTM north (easting, northing), Snyder's forward transverse Mercator: which CityGML tiles a box needs."""
    return wgs84_to_tm(lat, lon, zone * 6 - 183)

def wgs84_to_tm(lat, lon, lon0, k0=0.9996, false_easting=500_000, false_northing=0):
    """WGS84 → any transverse Mercator grid on GRS80/WGS84 (easting, northing): tm_to_wgs84's inverse."""
    a, f = 6_378_137.0, 1 / 298.257223563
    e2 = f * (2 - f); ep2 = e2 / (1 - e2)
    phi = math.radians(lat)
    n = a / math.sqrt(1 - e2 * math.sin(phi)**2)
    t, c, A = math.tan(phi)**2, ep2 * math.cos(phi)**2, math.radians(lon - lon0) * math.cos(phi)
    m = a * ((1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * phi - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * phi)
             + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * phi) - (35 * e2**3 / 3072) * math.sin(6 * phi))
    x = false_easting + k0 * n * (A + (1 - t + c) * A**3 / 6 + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * A**5 / 120)
    y = false_northing + k0 * (m + n * math.tan(phi) * (A**2 / 2 + (5 - t + 9 * c + 4 * c**2) * A**4 / 24
                                                        + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * A**6 / 720))
    return x, y

def florida_east_to_wgs84(easting, northing):
    """NAD83 / Florida East (EPSG:2236, US survey feet) → WGS84 (NAD83 taken as WGS84, ~1 m): Orlando's gpsx/gpsy."""
    feet, lat0, lon0, k0 = 1200 / 3937, 24 + 1 / 3, -81, 0.999941177
    return tm_to_wgs84(easting * feet, northing * feet, lon0, k0, 200_000, -wgs84_to_tm(lat0, lon0, lon0, k0, 0, 0)[1])

def mercator_to_wgs84(x, y):
    """Web Mercator (EPSG:3857) → (lat, lon)."""
    r = 6_378_137.0
    return math.degrees(2 * math.atan(math.exp(y / r)) - math.pi / 2), math.degrees(x / r)
