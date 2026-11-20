"""The tiles on disk and in the published store: paths, ages, the store's listing and sources/."""
import concurrent.futures, gzip, json, os, time, urllib.error, urllib.parse, urllib.request
from builder.net import log, SourceError, TIMEOUT, UA

LAYERS = ("communes", "buildings", "terraces-v2", "venues", "streets", "streets-main", "places", "boundaries", "transit")
STREET_LAYERS = ("streets", "streets-main", "places", "boundaries", "transit")   # built only where an entry says "streets": true
# Days before a finished tile is cut again. Resuming skips a cell only while
# its file is younger than this, so the weekly run does refresh permits
# (they lapse) while a same-day re-run after a
# failure still skips what is done. Buildings and streets change yearly, communes never.
MAX_AGE_DAYS = {"communes": None, "buildings": 360, "buildings-v2": 360, "terraces-v2": 6, "venues": 6, "streets": 360, "streets-main": 360, "places": 360}
# boundaries/ has no age: a city's set is one small fetch, asked again each run that builds the layer.

def path(out, layer, cell): return os.path.join(out, layer, cell.key + ".json")

def read(file):
    try:
        with open(file) as f: items = json.load(f)
        return items if isinstance(items, list) else None
    except (OSError, ValueError):
        return None

def done(out, layer, cell):
    """A tile to keep: it parses, and is younger than its layer's MAX_AGE_DAYS."""
    file, limit = path(out, layer, cell), MAX_AGE_DAYS[layer]
    if read(file) is None: return False
    return limit is None or time.time() - os.path.getmtime(file) < limit * 86_400
LISTED = None   # --listing: the store's keys, so the blocks it lacks are built first

def in_store(cell, layers):
    """Every tile this run writes for the cell is already in the store (False without --listing). A run cut
    by the job's time limit (London, 2026-10-07: 37,562 of 51,466 cells in 290 min) then starts its rerun
    with the cells it did not reach, instead of the same ones again."""
    tiled = [{"buildings": "buildings-v2"}.get(l, l) for l in layers if l in ("buildings", "streets", "terraces-v2")]
    return LISTED is not None and all(f"tiles/{l}/{cell.key}.json" in LISTED for l in tiled)

def write(file, items):
    os.makedirs(os.path.dirname(file), exist_ok=True)
    with open(file + ".tmp", "w") as f: json.dump(items, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(file + ".tmp", file)

STORE = None           # --store: the published tiles' base URL, read for the sources/ fields a run does not build
PUBLIC_STORE = "https://tiles.alephb.uk/tiles/"   # where a venue match reads from when no --store is given
STORE_THREADS = 16     # the store is a CDN: no spacing, a few requests in flight
_bust = str(int(time.time()))   # one query string per run: the CDN serves the tile as published, not as cached

def published_items(layer, key, bust=False):
    """A published tile's items (a list; [] when there is none, 404), or a SourceError. `bust` asks past the CDN's cache."""
    url = f"{STORE or PUBLIC_STORE}{layer}/{key}.json" + (f"?v={_bust}" if bust else "")
    error = "?"
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA})  # the store refuses urllib's own
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response: body = response.read()
            if body[:2] == b"\x1f\x8b": body = gzip.decompress(body)  # gzipped at rest, inflated by the CDN or not
            items = json.loads(body)
            return items if isinstance(items, list) else []
        except urllib.error.HTTPError as e:
            if e.code == 404: return []
            error = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001 — resets, timeouts, a truncated body: asked again
            error = f"{type(e).__name__}: {e}"[:200]
        time.sleep(2 * (attempt + 1))
    return SourceError(f"store: {error}")

def published_source(cell):
    """The published sources/ entry of a cell: a dict ({} when there is none, 404), or a SourceError."""
    items = published_items("sources", cell.key)
    if isinstance(items, SourceError): return items
    return items[0] if items and isinstance(items[0], dict) else {}

def published_sources(out, cells):
    """{key: published entry or SourceError} for the cells with no sources/ tile in `out` yet
    (a tile there already holds them); {} without a store."""
    todo = [c for c in cells if STORE and read(path(out, "sources", c)) is None]
    if not todo: return {}
    with concurrent.futures.ThreadPoolExecutor(STORE_THREADS) as pool:
        return dict(zip((c.key for c in todo), pool.map(published_source, todo)))

def note_sources(out, cell, published=None, **fields):
    """sources/<key>.json, one object in an array like every layer: which permit feed
    (a PERMIT_CITIES city, or null) and which building source (a CITY_BUILDINGS city,
    "IGN", "OSM", or "<city>+OSM" for a combine row's cell the city gave heights to) built the cell — what the app credits. Each field is written with
    its own layer, over the tile already in `out`, else over the published one (`published`,
    from published_sources), so a run building one layer keeps the other's field. A cell
    whose published tile could not be read gets no tile: the upload leaves the published one."""
    file = path(out, "sources", cell)
    local = read(file)
    base = (published or {}).get(cell.key, {}) if local is None else local[0]
    if isinstance(base, SourceError):
        log(f"    {cell.key}: sources/ not written, the published tile was not read ({base})"); return
    write(file, [dict(base, **fields)])
