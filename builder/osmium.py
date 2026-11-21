"""Geofabrik's extracts and osmium-tool: which region holds an area, and its features."""
import datetime, email.utils, json, math, os, re, subprocess, time, urllib.error, urllib.parse, urllib.request
from builder.cells import bounds, contains
from builder.net import download, kept, log, SourceError, TIMEOUT, UA
from builder.shapes import outer_rings

GEOFABRIK = "https://download.geofabrik.de/index-v1.json"
GRID = 100  # the in-memory index's buckets: 1/100°, ~1 km
_regions = None
# The store's copies of Geofabrik's files, at geofabrik/<its path> (store_key): tiles.yml copies up each one a job fetched
# from Geofabrik. 2026-11-19/20: Geofabrik refused every connection from part of GitHub's runners (~170 city jobs lost,
# the plan job's index among them); no public mirror serves Geofabrik's own files at its paths.
STORE_COPY = "https://tiles.alephb.uk/"
STALE_COPY_DAYS = 30   # the oldest copy taken when Geofabrik refuses: a week-old extract beats a city with no tiles

def store_key(url): return "geofabrik/" + urllib.parse.urlsplit(url).path.lstrip("/")

def copy_age(url):
    """Days since the store's copy at `url` was written (HEAD, its Last-Modified); None when the store has none."""
    try:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return (time.time() - email.utils.parsedate_to_datetime(response.headers["Last-Modified"]).timestamp()) / 86_400
    except Exception:  # noqa: BLE001 — no copy, or none we can date: not one to take
        return None

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args): return None   # the redirect comes back as an HTTPError carrying its Location
NO_REDIRECT = urllib.request.build_opener(_NoRedirect)

def previous_extract(url):
    """The dated file the day before the one Geofabrik's -latest redirects to (illinois-261120 → illinois-261119); None
    for a file with no dated copies, or Geofabrik not answering. 2026-11-21: during Geofabrik's nightly update -latest
    pointed for hours at a dated file not served yet (404, or a hang) while the day before's was (16 cities lost)."""
    if not url.endswith("-latest.osm.pbf"): return None
    try: NO_REDIRECT.open(urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA}), timeout=TIMEOUT)
    except urllib.error.HTTPError as redirect: location = redirect.headers.get("Location") or ""
    except Exception: return None  # noqa: BLE001 — refused or down: the store's copy is next
    else: return None
    dated = re.search(r"-(\d{6})\.osm\.pbf$", location)
    if not dated: return None
    day = datetime.datetime.strptime(dated[1], "%y%m%d") - datetime.timedelta(days=1)
    return location[:dated.start(1)] + day.strftime("%y%m%d") + ".osm.pbf"

def geofabrik(url, folder, max_days=6):
    """A Geofabrik file (the index or an extract) in `folder`, and where it came from: "disk", "store" or "geofabrik".
    The store's copy from today first (Geofabrik asked once a day per file, not once per job), then Geofabrik (download()'s
    backoff), then, Geofabrik refusing this runner, a copy up to STALE_COPY_DAYS old."""
    file = os.path.join(folder, url.rsplit("/", 1)[1])
    if kept(file, max_days): return file, "disk"
    copy = STORE_COPY + store_key(url)
    age = copy_age(copy)
    if age is not None and age < 1: return download(copy, folder, max_days), "store"
    try: return download(url, folder, max_days), "geofabrik"
    except SourceError as refused:
        previous = previous_extract(url)
        if previous:
            try:
                os.replace(download(previous, folder, max_days), file)   # under the -latest name the build and the store expect
                log(f"  {url.rsplit('/', 1)[1]}: not served yet, the day before's {previous.rsplit('/', 1)[1]}")
                return file, "geofabrik"   # copied up: a day older than -latest, and a refused sibling's best
            except SourceError: pass
        age = copy_age(copy)   # asked again: a sibling job may have copied it up during the backoff (Leeds after Bradford, 2026-11-18)
        if age is None or age > STALE_COPY_DAYS: raise SourceError(f"{refused}; the store has no copy from the last {STALE_COPY_DAYS} days") from refused
        log(f"  {url.rsplit('/', 1)[1]}: Geofabrik refused, the store's copy from {age:.1f} days ago")
        return download(copy, folder, max_days), "store"

def geofabrik_pbf(lat, lon):
    global _regions
    if _regions is None:
        # tiles.yml's plan job fetches the index once for all its build jobs (2026-11-18: ~357 fetches a run, each
        # 3.8 MB, and Geofabrik began refusing part of GitHub's runners).
        path = os.environ.get("GEOFABRIK_INDEX")
        if path and os.path.exists(path):
            with open(path) as f: _regions = json.load(f)["features"]
        else:
            with open(geofabrik(GEOFABRIK, "extracts", max_days=1)[0]) as f: _regions = json.load(f)["features"]
    return smallest_region(_regions, lat, lon)["properties"]["urls"]["pbf"]

def smallest_region(regions, lat, lon):
    """The smallest region holding the point: the deepest in its chain of parents, and among
    equally deep ones (us and illinois, dach and switzerland) the one with the smallest box."""
    parents = {f["properties"]["id"]: f["properties"].get("parent") for f in regions}
    depth = lambda i: 0 if i is None else 1 + depth(parents.get(i))
    def size(f):
        s, w, n, e = bounds(f["geometry"])
        return (n - s) * (e - w)
    holding = [f for f in regions if "pbf" in f["properties"].get("urls", {}) and contains(f["geometry"], lat, lon)]
    if not holding: raise SourceError(f"no Geofabrik extract holds {lat},{lon}")
    return max(holding, key=lambda f: (depth(f["properties"]["id"]), -size(f)))

def buckets(s, w, n, e):
    return [(y, x) for y in range(math.floor(s * GRID), math.floor(n * GRID) + 1) for x in range(math.floor(w * GRID), math.floor(e * GRID) + 1)]

def osmium(*args):
    try: return subprocess.run(["osmium", *args], check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as error: raise SourceError(f"osmium: {error.stderr.strip()[:200]}") from error

def features(pbf, kinds="point,polygon", ids=False):
    """An osmium file as GeoJSON features, points and areas only by default: a closed way also comes out as a
    line. Points include the nodes a way needed that carry tags of their own, so callers check tags. With
    ids=True each feature's properties also hold "@type" (node, way, relation) and "@id", an area's its way's or relation's."""
    export = osmium("export", pbf, "-f", "geojsonseq", "--geometry-types=" + kinds, *(["-a", "type,id"] if ids else []), "-o", "-")
    for line in export.split("\n"):  # not splitlines(): it breaks on the \x1e that opens each record
        if line.strip("\x1e"): yield json.loads(line.lstrip("\x1e"))

def mean_point(geometry):
    """(lat, lon): a point's own, an area's outer ring's mean vertex; None for an area with no ring."""
    if geometry["type"] == "Point": return geometry["coordinates"][1], geometry["coordinates"][0]
    ring = next(outer_rings(geometry), None)
    if not ring: return None
    return tuple(sum(v[k] for v in ring) / len(ring) for k in ("latitude", "longitude"))
