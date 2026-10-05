#!/usr/bin/env python3
"""Weekly data check: is every city feed still answering what the published tiles hold?

Barcelona's permit CSV started answering a bot page on 2026-10-03 and nobody noticed. This asks each
PERMIT_CITIES and CITY_BUILDINGS row of make_tiles.py for one fixed, dense cell (probes.json, chosen
once with `pick`), the way a build does, and records the status, the count near the cell and the
share of entries named (permits) or with a height (buildings), plus the licence string where the
publisher has a metadata API (Opendatasoft, Socrata, CKAN, ArcGIS REST, WFS GetCapabilities). The
record is compared with the last run's (data-check.json, committed by the workflow) and with the
published tile of the same cell:

  dead     the feed did not answer (an error, a timeout, or nothing parsed)
  count    the count fell by more than half against the last run or the published tile
  share    the named / with-a-height share fell by more than half (a renamed name or height column)
  licence  the licence string changed
  source   the published sources/ tile no longer credits this row (the build fell back to OSM or IGN)
  missing  the row was not probed (its shard failed)
  feed     a sampled coverage.json cell disagrees with the tiles on the store (the check of
           Techneb/compromise's tools/coverage/check_feed.py, ported: that repository is private)

  python3 check_data.py pick [--coverage coverage.json]              # choose a probe cell for each row without one
  python3 check_data.py probe --part 1/6 --write part-1.json          # one shard of the rows (the workflow's matrix)
  python3 check_data.py probe --only Paris,Barcelona --write p.json   # a few rows, by hand
  python3 check_data.py feed --write feed.json [--per-area 2]         # the coverage feed against the store
  python3 check_data.py report --parts part-*.json --feed feed.json --previous data-check.json \\
                               --write data-check.json --summary report.md

`report` exits 0 either way and writes `flags=<n>` to $GITHUB_OUTPUT when set; the workflow opens or
updates the "Weekly data check" issue with report.md when n > 0 and closes it when a run is clean.
Standard library only; osmium-tool on the PATH for the rows that measure on OpenStreetMap's
footprints (London's lidar).
"""
import argparse, datetime, gzip, json, os, random, re, signal, sys, time, urllib.error, urllib.parse, urllib.request

import make_tiles as mt

STORE = "https://tiles.alephb.uk/tiles/"
FEED = "https://tiles.alephb.uk/coverage.json"
PROBES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probes.json")
HALF = 0.5                 # a count or share under this fraction of its reference is a fall
ROW_TIMEOUT = 25 * 60      # seconds one row may take: Catastro parses a whole municipality for its first cell
OSM_KIND = "TERRASSE (OSM)"  # the kind osm_terraces() writes: the other entries of a terraces tile are the feed's
NO_HEIGHT = 15.0           # the height written when a source gives none; flagged "guessed" since 2026-10-04 (coverage.py's FALLBACK_HEIGHT)
UA = {"User-Agent": "compromise-tiles-check (+https://github.com/Techneb/compromise-tiles)"}
FLOOR, RATIO, MINIMUM = 20, 0.30, 5  # the feed's terracesKnown rule, as check_feed.py restates it


# ---------------------------------------------------------------- rows and probes

def rows():
    """[(id, layer, row)] for every feed row of make_tiles.py, in its order: "permits/Paris", "buildings/Berlin"."""
    return [(f"permits/{c['city']}", "permits", c) for c in mt.PERMIT_CITIES] + \
           [(f"buildings/{b['city']}", "buildings", b) for b in mt.CITY_BUILDINGS]


def load_probes(file=PROBES):
    try:
        with open(file) as f: return json.load(f)
    except FileNotFoundError:
        return {}


def part_of(ids, part):
    """The ids of shard k of n ("k/n"), every kth row: the heavy rows (Catastro's fourteen in a row) spread out."""
    k, n = (int(x) for x in part.split("/"))
    if not 1 <= k <= n: raise ValueError(f"part {part}")
    return [i for j, i in enumerate(ids) if j % n == k - 1]


def metadata_url(layer, row):
    """Where the publisher states the licence, derived from a permit row's shape; None when it has no such API."""
    if layer != "permits": return None
    shape, host, dataset = row.get("shape"), row["host"], row["dataset"]
    if shape is None: return "opendatasoft", f"https://{host}/api/explore/v2.1/catalog/datasets/{dataset}"
    if shape == "socrata": return "socrata", f"https://{host}/api/views/{dataset}.json"
    if shape == "arcgis": return "arcgis", f"https://{host}/{dataset}?f=json"
    if shape == "wfs": return "wfs", f"https://{host}?service=WFS&request=GetCapabilities"
    if shape == "stockholm":  # the key in the URL path: no key, no metadata
        keyed = mt.keyed_host(row)
        return ("wfs", f"https://{keyed}?service=WFS&request=GetCapabilities") if keyed else None
    if shape == "barcelona": return "ckan", f"https://{host}/data/api/3/action/package_show?id={dataset}"
    return None


def licence_source(layer, row, probe):
    """("kind", url) from the probe entry when it says (null: none), else derived from the row."""
    if "licence" in probe:
        return (probe["licence"]["kind"], probe["licence"]["url"]) if probe["licence"] else None
    return metadata_url(layer, row)


def licence(kind, url):
    """The publisher's licence (or copyright) string, None when it states none or the API did not answer."""
    get = lambda u: mt.get(u, parse=json.loads, waits=(10,))
    try:
        if kind == "opendatasoft":
            year = time.gmtime().tm_year
            for u in ([url.replace("{year}", str(y)) for y in (year, year - 1)] if "{year}" in url else [url]):
                try: meta = (get(u).get("metas") or {}).get("default") or {}
                except mt.SourceError as e:
                    if "HTTP 404" in str(e) and "{year}" in url: continue  # this year's dataset may not exist yet
                    raise
                return meta.get("license") or None
            return None
        if kind == "socrata":
            d = get(url)
            return (d.get("license") or {}).get("name") or d.get("licenseId") or None
        if kind == "ckan": return (get(url).get("result") or {}).get("license_title") or None
        if kind == "arcgis": return get(url).get("copyrightText") or None
        if kind == "wfs":
            text = mt.get(url, waits=(10,)).decode("utf-8", "replace")
            found = [m.strip() for tag in ("Fees", "AccessConstraints")
                     for m in re.findall(rf"<(?:\w+:)?{tag}>([^<]*)<", text) if m.strip()]
            return " / ".join(dict.fromkeys(found)) or None
    except Exception as e:  # noqa: BLE001 — a metadata API down is not a data regression; the string is simply unknown
        mt.log(f"    licence: {kind} {url}: {type(e).__name__}: {e}"[:300])
    return None


# ---------------------------------------------------------------- the store

def fetch_store(url):
    """(body, status) from the store; the body None on an HTTP error (404: no tile)."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                raw = r.read()
                return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw, r.status
        except urllib.error.HTTPError as e:
            return None, e.code
        except Exception as e:  # noqa: BLE001 — resets, timeouts: asked again
            error = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
    return None, error


def share(items, has):
    return round(sum(1 for i in items if has(i)) / len(items), 3) if items else 0.0


def named(items): return share(items, lambda t: bool(t.get("name")))
def guessed(b, flagged):
    """Whether a building's height is the generator's guess: its flag where the tile carries any; else, in a
    tile built before the flag, the 15 m it wrote (a measured 15 m counts as a guess there, as before)."""
    return bool(b.get("guessed")) if flagged else b.get("height") == NO_HEIGHT

def with_height(items):
    flagged = any("guessed" in b for b in items)
    return share(items, lambda b: not guessed(b, flagged))


def published(layer, key):
    """What the store holds for the probe cell: the row's own entries of the tile (a terraces tile also holds
    OpenStreetMap's, by kind), their share, and the source the sources/ tile credits."""
    out = {}
    body, code = fetch_store(f"{STORE}{'terraces-v2' if layer == 'permits' else 'buildings'}/{key}.json")
    if body is None: out["status"] = f"HTTP {code}" if isinstance(code, int) else str(code)
    else:
        items = json.loads(body)
        if layer == "permits":
            own = [t for t in items if t.get("kind") != OSM_KIND]
            out.update(status="ok", count=len(own), named=named(own))
        else:
            flagged = any("guessed" in b for b in items)
            out.update(status="ok", count=len(items), height=with_height(items), with_height=sum(1 for b in items if not guessed(b, flagged)))
    body, code = fetch_store(f"{STORE}sources/{key}.json")
    if body is not None:
        entry = json.loads(body)
        entry = entry[0] if isinstance(entry, list) and entry and isinstance(entry[0], dict) else {}
        out["source"] = entry.get(layer)
    return out


def credits(layer, row, source):
    """Whether a sources/ entry credits this row: its city, or "<city>+OSM" for a combine row."""
    return source == row["city"] or (layer == "buildings" and row.get("combine") and source == row["city"] + "+OSM")


# ---------------------------------------------------------------- probing

class Timeout(Exception): pass


def _alarm(signum, frame): raise Timeout(f"no answer in {ROW_TIMEOUT // 60} min")


_osm_areas = {}

def osm_buildings(rect, tagged=False):
    """OpenStreetMap's footprints around a probe cell (a combine row measuring on them, London's lidar): the
    extract downloaded and cut for that cell alone, since no build sets make_tiles._osm here."""
    lat, lon = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    key = (round(lat, 2), round(lon, 2))
    if key not in _osm_areas:
        pbf = mt.geofabrik_pbf(lat, lon)
        _osm_areas[key] = mt.OSM(mt.download(pbf, "extracts"), mt.padded(rect, 600))
    return _osm_areas[key].buildings(rect, tagged)


def probe(layer, row, cell):
    """One row asked for one cell as a build asks it (the same padding, the same trimming to the cell):
    status, the count near the cell, how many the feed sent for the padded rect, and the share named
    or with a height. A whole-file feed (Madrid, Barcelona…) also gives its total."""
    out = {"status": "ok"}
    started = time.monotonic()
    signal.signal(signal.SIGALRM, _alarm); signal.alarm(ROW_TIMEOUT)
    try:
        if layer == "permits":
            items = mt.unique(mt.permits(row, mt.padded(cell.rect, mt.TERRACE_RADIUS)))
            near = mt.near_terraces(cell, items)
            out.update(count=len(near), fetched=len(items), named=named(near))
            if row["city"] in mt._whole: out["total"] = len(mt._whole[row["city"]])
        else:
            items = mt.unique(row["fetch"](mt.padded(cell.rect, mt.BUILDING_PAD)))
            near = mt.near_buildings(cell, items)
            out.update(count=len(near), fetched=len(items), height=with_height(near))
    except Timeout as e:
        out.update(status="timeout", error=str(e))
    except Exception as e:  # noqa: BLE001 — a SourceError, a changed column, a bot page: the feed did not answer
        out.update(status="dead", error=mt.redact(f"{type(e).__name__}: {e}")[:300])
    finally:
        signal.alarm(0)
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


def probe_rows(probes, ids, only=None):
    """{id: record} for the rows named: the probe, the published tile and the licence."""
    mt.osm_buildings = osm_buildings
    records = {}
    for rid, layer, row in rows():
        if rid not in ids or (only and row["city"] not in only): continue
        p = probes.get(rid)
        if not p:
            records[rid] = {"status": "unprobed", "error": "no probe cell in probes.json (run `pick`)"}
            mt.log(f"{rid}: no probe cell"); continue
        record = {"key": p["key"]}
        if p.get("skip"):
            record.update(status="skipped", error=p["skip"])
        elif row.get("key") and mt.keyed_host(row) is None:  # a keyed row without its secret (a fork, a local run)
            record.update(status="skipped", error=f"{row['key']} is not set")
        else:
            ky, kx = map(int, p["key"].split(","))
            record.update(probe(layer, row, mt.Cell(ky, kx)))
        record["tile"] = published(layer, p["key"])
        source = licence_source(layer, row, p)
        if source: record["licence"] = licence(*source)
        records[rid] = record
        mt.log(f"{rid}: {json.dumps(record, ensure_ascii=False)}")
    return records


# ---------------------------------------------------------------- comparison (pure: tested)

def flags(layer, now, previous=None, probe=None):
    """[(kind, detail)] for one row: `now` this run's record, `previous` the last run's, `probe` its probes.json entry."""
    out, previous = [], previous or {}
    if now.get("status") == "skipped": return out
    if now.get("status") == "unprobed": return [("missing", now.get("error", "not probed"))]
    if now.get("status") != "ok": return [("dead", now.get("error", now.get("status", "?")))]
    tile = now.get("tile") or {}
    measure = "named" if layer == "permits" else "height"
    # A combine row's probe returns only the footprints the city gave a height: the tile's with-height count is its match.
    tile_count = tile.get("with_height") if layer == "buildings" and probe and probe.get("combine") else tile.get("count")
    for label, ref_count, ref_share in (("the last run", previous.get("count") if previous.get("status") == "ok" else None, previous.get(measure)),
                                        ("the published tile", tile_count if tile.get("status") == "ok" else None, tile.get(measure))):
        if ref_count and now["count"] < HALF * ref_count:
            out.append(("count", f"{now['count']} near the cell, {ref_count} in {label}")); break
    for label, ref in (("the last run", previous.get(measure) if previous.get("status") == "ok" else None),
                       ("the published tile", tile.get(measure) if tile.get("status") == "ok" and not (layer == "buildings" and probe and probe.get("combine")) else None)):
        if ref and now.get(measure, 0) < HALF * ref and now.get("count"):
            out.append(("share", f"{measure} {now[measure]:.0%}, {ref:.0%} in {label}")); break
    if previous.get("licence") and now.get("licence") and previous["licence"] != now["licence"]:
        out.append(("licence", f"was “{previous['licence']}”, now “{now['licence']}”"))
    if previous.get("tile", {}).get("source") and tile.get("source") and previous["tile"]["source"] != tile["source"]:
        out.append(("source", f"the published cell credits {tile['source']}, last run {previous['tile']['source']}"))
    return out


def compare(records, previous, probes):
    """{id: [(kind, detail)]} over every row of make_tiles.py; a row absent from `records` is missing."""
    out = {}
    for rid, layer, _ in rows():
        now = records.get(rid) or {"status": "unprobed", "error": "not probed: its shard did not finish"}
        out[rid] = flags(layer, now, (previous.get("rows") or {}).get(rid), probes.get(rid))
    return out


def feed_mismatches(feed, tile_counts, per_area=2, seed=1):
    """check_feed.py's cell check: `per_area` cells sampled per area of the feed, each recounted from its tiles.
    `tile_counts(key)` gives (buildings, terraces) from the store, None for a tile the store lacks. A missing tile
    agrees with a count of 0 (a cell whose layer was never built). Returns (checked, [mismatch], [missing key])."""
    random.seed(seed)
    cells = {(c[0], c[1]): c for c in feed["cells"]}
    checked, bad, missing = 0, [], []
    for area in feed["areas"]:
        s, w, n, e = area["box"]
        inside = [c for (ky, kx), c in cells.items() if int(s * 500) <= ky <= int(n * 500) and int(w * 500) <= kx <= int(e * 500)]
        for c in random.sample(inside, min(per_area, len(inside))):
            key = f"{c[0]},{c[1]}"
            buildings, terraces = tile_counts(key)
            checked += 1
            if buildings is None or terraces is None: missing.append(key)
            ok_b = buildings == c[3] or (buildings is None and c[3] == 0)
            ok_t = terraces == c[2] or (terraces is None and c[2] == 0)
            if not (ok_b and ok_t):
                bad.append({"key": key, "area": f"{area['city']} / {area['name']}", "feed": [c[3], c[2]], "tiles": [buildings, terraces]})
    return checked, bad, missing


def feed_known(feed):
    """[(area, published terracesKnown, recomputed)] with the reader's ratio rule, as check_feed.py prints it."""
    venues = {(v[0], v[1]): v[2] for v in feed["venueCells"]}
    def near(ky, kx):
        keys = [(int((ky + i) / 10), int((kx + j) / 10)) for i in (-1, 0, 1) for j in (-1, 0, 1)]
        return sum(venues.get(k, 0) / 100 for k in keys) if any(k in venues for k in keys) else None
    def known(c):
        v = near(c[0], c[1])
        return c[2] >= FLOOR if v is None else (c[2] >= MINIMUM and c[2] >= RATIO * v)
    out = []
    for area in feed["areas"]:
        s, w, n, e = area["box"]
        inside = [c for c in feed["cells"] if int(s * 500) <= c[0] <= int(n * 500) and int(w * 500) <= c[1] <= int(e * 500)]
        out.append((f"{area['city']} / {area['name']}", area["terracesKnown"], sum(known(c) for c in inside)))
    return out


# ---------------------------------------------------------------- the report

def fmt_share(v): return "–" if v is None else f"{v:.0%}"


def summary(run, records, previous, probes, flagged, feed=None):
    """The Markdown report: the flags first, then every row's line, then the feed check."""
    date = run["run"]
    lines = [f"Run of {date} (probes: one dense cell per row, `probes.json`; last run: {previous.get('run') or 'none'}).", ""]
    n_flags = sum(len(v) for v in flagged.values()) + (1 if feed and feed.get("mismatches") else 0)
    if n_flags:
        lines += [f"**{n_flags} flag{'s' if n_flags != 1 else ''}**", ""]
        for rid, fl in flagged.items():
            for kind, detail in fl: lines.append(f"- `{rid}` **{kind}**: {detail}")
        if feed and feed.get("mismatches"):
            lines.append(f"- `coverage.json` **feed**: {len(feed['mismatches'])} of {feed['checked']} sampled cells disagree with the tiles")
        lines.append("")
    else:
        lines += ["**Clean**: every feed answered, no count or share fell by half, no licence changed.", ""]
    lines += ["| Row | Cell | Status | Count now / last / tile | Named or with height now / last / tile | Licence | Flags |",
              "|---|---|---|---|---|---|---|"]
    prev_rows = previous.get("rows") or {}
    for rid, layer, _ in rows():
        r, p = records.get(rid) or {"status": "unprobed"}, prev_rows.get(rid) or {}
        tile, measure = r.get("tile") or {}, "named" if layer == "permits" else "height"
        status = r.get("status", "?") + (f" ({r['error']})" if r.get("error") and r.get("status") != "ok" else "")
        count = " / ".join(str(x.get("count", "–")) if x.get("status") == "ok" else "–" for x in (r, p, tile))
        shares = " / ".join(fmt_share(x.get(measure)) if x.get("status") == "ok" else "–" for x in (r, p, tile))
        lic = r.get("licence") or "–"
        fl = ", ".join(k for k, _ in flagged.get(rid, ())) or ""
        lines.append(f"| `{rid}` | `{r.get('key', probes.get(rid, {}).get('key', '–'))}` | {status} | {count} | {shares} | {lic} | {fl} |")
    if feed:
        lines += ["", f"**Coverage feed** (updated {feed.get('updated', '?')}): {feed['checked']} cells sampled, "
                      f"{len(feed['mismatches'])} mismatches, {len(feed['missing'])} with a tile missing on the store."]
        for m in feed["mismatches"][:40]:
            lines.append(f"- `{m['key']}` ({m['area']}): feed buildings {m['feed'][0]} / tile {m['tiles'][0]}; feed terraces {m['feed'][1]} / tile {m['tiles'][1]}")
        off = [(a, pub, rec) for a, pub, rec in feed.get("known", []) if pub != rec]
        if off: lines.append(f"- terracesKnown differs from the ratio rule in {len(off)} areas (e.g. {off[0][0]}: published {off[0][1]}, recomputed {off[0][2]}); informational, as in check_feed.py")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- pick

def pick(coverage, probes, layer_rows=None, refresh=False):
    """A probe cell per row without one: the densest cell of the feed inside the row's box (and boundary, and
    commune for a French permit row) whose published sources/ tile credits the row, among the twelve densest."""
    cells = coverage["cells"]
    for rid, layer, row in layer_rows or rows():
        if rid in probes and not refresh: continue
        s, n = row["lat"]; w, e = row["lon"]
        inside = [c for c in cells if s <= c[0] / 500 <= n and w <= c[1] / 500 <= e
                  and (not row.get("boundary") or mt.contains(row["boundary"], mt.Cell(c[0], c[1]).lat, mt.Cell(c[0], c[1]).lon))
                  and (layer != "permits" or not row["insee"] or row["insee"] in c[7].split())]
        inside.sort(key=lambda c: -(c[2] if layer == "permits" else c[4]))  # terraces, or buildings with a height
        best = None
        for c in inside[:12]:
            key = f"{c[0]},{c[1]}"
            tile = published(layer, key)
            if tile.get("status") != "ok" or not credits(layer, row, tile.get("source")): continue
            if best is None or tile["count"] > best[1]["count"]: best = (key, tile)
        if best is None:
            mt.log(f"{rid}: no published cell credits this row among the {min(12, len(inside))} densest of {len(inside)}"); continue
        entry = dict(probes.get(rid) or {}, key=best[0], picked=datetime.date.today().isoformat())
        if layer == "buildings" and row.get("combine"): entry["combine"] = True
        probes[rid] = entry
        mt.log(f"{rid}: {best[0]} ({best[1]['count']} entries, source {best[1].get('source')})")
    return probes


# ---------------------------------------------------------------- main

def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("command", choices=["pick", "probe", "feed", "report"])
    a.add_argument("--probes", default=PROBES)
    a.add_argument("--coverage", default="coverage.json", help="pick: the feed to choose dense cells from")
    a.add_argument("--refresh", action="store_true", help="pick: choose again for every row")
    a.add_argument("--part", default="1/1", help="probe: shard k/n of the rows")
    a.add_argument("--only", default="", help="probe: city names, comma-separated")
    a.add_argument("--per-area", type=int, default=2, help="feed: cells sampled per area")
    a.add_argument("--seed", type=int, default=datetime.date.today().isocalendar()[1], help="feed: the sample's seed (default: the ISO week)")
    a.add_argument("--parts", nargs="*", default=[], help="report: the probe shards' files")
    a.add_argument("--feed", default="", help="report: the feed check's file")
    a.add_argument("--previous", default="data-check.json", help="report: the last run's record")
    a.add_argument("--write", help="the file to write")
    a.add_argument("--summary", help="report: the Markdown report")
    args = a.parse_args()
    probes = load_probes(args.probes)

    if args.command == "pick":
        with open(args.coverage) as f: coverage = json.load(f)
        probes = pick(coverage, probes, refresh=args.refresh)
        with open(args.probes, "w") as f: json.dump(probes, f, ensure_ascii=False, indent=1); f.write("\n")
        print(f"{len(probes)} probes -> {args.probes}")

    elif args.command == "probe":
        ids = part_of([rid for rid, _, _ in rows()], args.part)
        only = {x.strip() for x in args.only.split(",") if x.strip()}
        records = probe_rows(probes, set(ids), only)
        out = {"run": datetime.date.today().isoformat(), "part": args.part, "rows": records}
        if args.write:
            with open(args.write, "w") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"{len(records)} rows probed, {sum(1 for r in records.values() if r.get('status') == 'ok')} answered")

    elif args.command == "feed":
        body, code = fetch_store(FEED)
        if body is None: sys.exit(f"coverage feed: {code}")
        feed = json.loads(body)
        def tile_counts(key):
            b, _ = fetch_store(f"{STORE}buildings/{key}.json"); t, _ = fetch_store(f"{STORE}terraces-v2/{key}.json")
            time.sleep(0.2)
            return (b.count(b'"outline"') if b is not None else None), (len(json.loads(t)) if t is not None else None)
        checked, bad, missing = feed_mismatches(feed, tile_counts, args.per_area, args.seed)
        out = {"updated": feed.get("updated"), "seed": args.seed, "checked": checked, "mismatches": bad, "missing": missing,
               "known": feed_known(feed)}
        if args.write:
            with open(args.write, "w") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"{checked} cells checked, {len(bad)} mismatches, {len(missing)} with a tile missing on the store")

    else:
        records = {}
        for file in args.parts:
            if not os.path.exists(file): mt.log(f"{file}: no such shard (its job failed before the upload)"); continue
            with open(file) as f: records.update(json.load(f).get("rows") or {})
        feed = None
        if args.feed and os.path.exists(args.feed):
            with open(args.feed) as f: feed = json.load(f)
        previous = {}
        if os.path.exists(args.previous):
            with open(args.previous) as f: previous = json.load(f)
        run = {"run": datetime.date.today().isoformat(), "rows": records,
               "feed": {k: (len(v) if isinstance(v, list) else v) for k, v in (feed or {}).items() if k != "known"}}
        flagged = {rid: fl for rid, fl in compare(records, previous, probes).items() if fl}
        n = sum(len(v) for v in flagged.values()) + (1 if feed and feed.get("mismatches") else 0)
        text = summary(run, records, previous, probes, flagged, feed)
        if args.summary:
            with open(args.summary, "w") as f: f.write(text)
        if args.write:
            with open(args.write, "w") as f: json.dump(run, f, ensure_ascii=False, indent=1); f.write("\n")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f: f.write(f"flags={n}\n")
        print(text if not args.summary else f"{n} flags")


if __name__ == "__main__":
    main()
