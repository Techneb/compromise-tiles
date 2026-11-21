"""Every request: retries, per-host spacing, the User-Agent, the IPv4-first connection, the log."""
import gzip, io, json, os, random, shutil, socket, sys, time, urllib.error, urllib.parse, urllib.request

UA = "compromise-tiles/2.0 (+https://github.com/Techneb/compromise; sunny-terrace tile generator)"
WAITS = (10, 30, 90)          # seconds before the 2nd, 3rd and 4th try
GAP = 1.0                     # seconds between two requests to one host
TIMEOUT = 60
DEAD_AFTER = 3                # a host that exhausted its retries this many times in a row is skipped for the run

# 2026-11-18: ~145 of ~340 build jobs (two runs) failed every try at download.geofabrik.de in 0.4 s with
# "[Errno 101] Network is unreachable": GitHub's runners have no IPv6 route, and CPython reports only the
# first address's error. Each family is asked on its own (an answer that lost its A records then still gets
# IPv4), IPv4 first, every address tried, and every failure named in the error.
_create_connection = socket.create_connection

def connect(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **_):
    host, port = address
    errors = []
    for family in (socket.AF_INET, socket.AF_INET6):
        try: infos = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
        except OSError as e: errors.append(f"{family.name}: {e}"); continue
        for *_, sockaddr in infos:
            try: return _create_connection(sockaddr[:2], timeout, source_address)
            except OSError as e: errors.append(f"{sockaddr[0]}: {e}")
    raise OSError("; ".join(errors)[:400])

socket.create_connection = connect  # ponytail: process-wide, so http.client (urllib) uses it too

# The city permit feeds, one row each. A cell picks its feed by INSEE
# code when the commune answered (a French commune without a feed gets none, so Levallois inside
# Paris's box is OSM only), else the first box holding the cell's centre — inside the row's municipal
# boundary when it has one (a feed holds only its own city's permits, so a neighbour's tiled cells in
# its box are OSM only, credited to no feed: Vaughan in Toronto's, Islington in Camden's). A borough's row names
# the cities.json entry it is part of (part_of="London"), so the status feed reads that city's terraces as permits.
# The environment variables holding a feed's API key (row and area_row add theirs): what redact() blanks.
SECRET_ENVS = []



class SourceError(Exception):
    """A source that did not answer completely, after every retry."""

_last, _dead = {}, {}

def redact(text):
    """The text with every keyed row's secret blanked: a key sits in its feed's URL path."""
    for key in SECRET_ENVS:
        secret = os.environ.get(key)
        if secret: text = text.replace(secret, "***")
    return text

def log(message): print(redact(str(message)), file=sys.stderr, flush=True)

def keyed_host(c):
    """A row's host with its API key filled in from the environment ({key}); None when the key is not set."""
    if not c.get("key"): return c["host"]
    secret = os.environ.get(c["key"], "").strip()
    return c["host"].replace("{key}", urllib.parse.quote(secret, safe="")) if secret else None

def get(url, data=None, parse=None, waits=WAITS, context=None, headers=None):
    """One request, retried after each of `waits`: spaced per host, gzip asked
    for (smaller transfers are the ones the proxy lets through), the body
    parsed inside the retry so a truncated answer is asked again rather than
    trusted. A host that fails DEAD_AFTER requests in a row is skipped."""
    host = urllib.parse.urlsplit(url).hostname
    if _dead.get(host, 0) >= DEAD_AFTER:
        raise SourceError(f"{host}: skipped, failed {DEAD_AFTER} requests in a row")
    error = "?"
    for attempt in range(len(waits) + 1):
        wait = _last.get(host, 0) + GAP - time.monotonic()
        if wait > 0: time.sleep(wait)
        try:
            request = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept-Encoding": "gzip", **(headers or {})})
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip": body = gzip.decompress(body)
            result = parse(body) if parse else body
            _last[host], _dead[host] = time.monotonic(), 0
            return result
        except urllib.error.HTTPError as e:
            error = f"HTTP {e.code}"
            if parse:  # an answer under an error status (Catastro's "No records" report) is still an answer
                try:
                    result = parse(gzip.decompress(e.read()) if e.headers.get("Content-Encoding") == "gzip" else e.read())
                    _last[host], _dead[host] = time.monotonic(), 0
                    return result
                except Exception:  # noqa: BLE001 — not an answer, then
                    pass
            if e.code in (400, 401, 404, 405, 410, 414): break  # a wrong request stays wrong
        except Exception as e:  # noqa: BLE001 — IncompleteRead, resets, timeouts, bad JSON: all worth another try
            error = f"{type(e).__name__}: {e}"[:200]
        _last[host] = time.monotonic()
        if attempt < len(waits):
            log(f"    {host}: {error}; retry in {waits[attempt]} s")
            time.sleep(waits[attempt])
    _dead[host] = _dead.get(host, 0) + 1
    raise SourceError(f"{host}: {error}")

def get_json(url, data=None, waits=WAITS): return get(url, data, parse=json.loads, waits=waits)

def kept(file, max_days):
    """The file is on disk and younger than `max_days`."""
    return os.path.exists(file) and time.time() - os.path.getmtime(file) < max_days * 86_400

def download(url, folder, max_days=6, headers=None):
    """A whole file, kept in `folder` and fetched again once older than `max_days` (Geofabrik updates daily).
    The bytes are kept as served: with `headers` asking for gzip, a store that holds the file gzipped
    (PLATEAU's) sends it so, and the reader inflates it."""
    file = os.path.join(folder, url.rsplit("/", 1)[1])
    if kept(file, max_days): return file
    os.makedirs(folder, exist_ok=True)
    log(f"  downloading {url}")
    # Retried: parallel build jobs pull extracts at the same moment and Geofabrik answers some of
    # them 504 or hangs (2026-09-27, three of three jobs, two lost); a minute later it serves them.
    # About ten minutes in all, each wait jittered so jobs refused together do not ask again together (2026-11-21).
    for attempt, wait in enumerate((60, 120, 180, 240, None)):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(file + ".tmp", "wb") as f:
                shutil.copyfileobj(response, f, 1 << 20)
                # http.client ends a body cut short without an error: Gijón's extract, 2026-10-07, read
                # "PBF error: unexpected EOF" in osmium and failed all 1,536 cells. Short is retried.
                expected = response.headers.get("Content-Length")
                if expected and f.tell() != int(expected): raise IOError(f"{f.tell()} of {expected} bytes")
            break
        except Exception as e:  # noqa: BLE001 — the area's OSM layers fail, not the run
            if wait is None: raise SourceError(f"{url}: {type(e).__name__}: {e}") from e
            wait *= random.uniform(0.75, 1.25)
            log(f"  {url.rsplit('/', 1)[1]}: {type(e).__name__}: {e}; retry in {wait:.0f} s")
            time.sleep(wait)
    os.replace(file + ".tmp", file)
    return file

# Shapefiles inside a zip (Buenos Aires, Milan, Rome): one layer's .shp, .shx, .dbf and .prj are fetched by HTTP range,
# the zip's central directory first (Rome's volumes are 58 MB of a 1.2 GB zip, Milan's 28 MB of 597 MB), kept a year
# under extracts/shapefile/<folder>/, and read in place: the .shx gives each record's offset, the .shp its box, so a
# block reads only the records whose box meets it.
RANGE_CHUNK = 8 << 20   # bytes a ranged request asks for

class Ranged(io.RawIOBase):
    """A remote file read by HTTP range, for zipfile: a seek is free, each read one request (retried like download())."""
    def __init__(self, url, headers):
        self.url, self.headers, self.at = url, headers, 0
        self.size = int(self.request(0, 0)[1].split("/")[1])
    def request(self, start, end):
        for wait in (*WAITS, None):
            try:
                request = urllib.request.Request(self.url, headers={"User-Agent": UA, **self.headers, "Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    if response.status != 206: raise SourceError(f"{self.url}: no range support (HTTP {response.status})")
                    return response.read(), response.headers.get("Content-Range", "")
            except SourceError: raise
            except Exception as e:  # noqa: BLE001 — resets, timeouts: worth another try
                if wait is None: raise SourceError(f"{self.url}: {type(e).__name__}: {e}") from e
                log(f"    {self.url.rsplit('/', 1)[1]}: {type(e).__name__}: {e}; retry in {wait} s")
                time.sleep(wait)
    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.at
    def seek(self, offset, whence=0):
        self.at = (offset, self.at + offset, self.size + offset)[whence]
        return self.at
    def readinto(self, buffer):
        if self.at >= self.size or not len(buffer): return 0
        body = self.request(self.at, min(self.at + len(buffer), self.size) - 1)[0]
        buffer[:len(body)] = body
        self.at += len(body)
        return len(body)
