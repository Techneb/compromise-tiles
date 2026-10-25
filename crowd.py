#!/usr/bin/env python3
"""Weekly crowd export: the venue corrections and sun reports of the app's public CloudKit database.

  python3 crowd.py export --out crowd-out [--config config.json]     # CloudKit -> files
  python3 crowd.py export --records raw.jsonl --out crowd-out        # from saved records (no CloudKit)
  aws s3 ls .../archive/ | python3 crowd.py prune                    # the archive days to delete

Reads every Correction and SunReport record through CloudKit Web Services, signed with a
server-to-server key (CLOUDKIT_KEY_ID, CLOUDKIT_PRIVATE_KEY: the PEM; from the environment, never
printed). Without them `export` says so and exits 0 having written nothing. Writes, under --out:

  public/latest.csv.gz, public/<day>.csv.gz   the agreed facts on OpenStreetMap places (ODbL):
                                              osm_type, osm_id, fact, value, agreed_on
  private/<day>/                              the archive, only with CROWD_ARCHIVE_SALT set:
      corrections.jsonl.gz, sun.jsonl.gz      every record, raw, its creator re-hashed with the salt
      facts.csv.gz                            per spot and fact: what the app shows, its source, OSM's value
      manifest.json                           counts, run day, schema version, the sun model check

The rules are MeetCore's (CrowdFacts.swift, CrowdHours.swift, SunReports.swift), ported line for line;
test_crowd.py holds the Swift tests' cases. Standard library, and `cryptography` for the signature.
"""
import argparse, base64, csv, datetime, gzip, hashlib, hmac, io, json, os, re, sys, time, urllib.error, urllib.request

CONTAINER = "iCloud.uk.alephb.compromise"
API = "https://api.apple-cloudkit.com"
LIFETIME_MS = 365 * 86400_000   # CrowdFacts.lifetime
DAY_CAP = 20 * 7                # CrowdFacts.dailyRecordCap
SCHEMA = 1
TYPES = {"Correction": ["spot", "cell", "fact", "value", "v"],
         "SunReport": ["spot", "cell", "part", "seen", "sunAz", "sunEl", "month", "sky", "model", "v"]}
FIELD_TYPES = {"v": "INT64", "month": "INT64", "sunAz": "DOUBLE", "sunEl": "DOUBLE"}   # the rest STRING
ODBL = ["osm_type", "osm_id", "fact", "value", "agreed_on"]
FACTS = ["spot", "fact", "value", "source", "osm_value"]
OSM_SPOT = re.compile(r"osm:([nwr])([0-9]+)")
OSM_TYPE = {"n": "node", "w": "way", "r": "relation"}

# --- The fixed choices (CrowdFacts.canonical) --------------------------------------------------------

YES_NO = {"yes", "no"}
CUISINES = {   # VenueVocabulary.cuisines (VenueSubtitle.swift)
    "french", "italian", "pizza", "japanese", "sushi", "ramen", "chinese", "korean", "thai", "vietnamese",
    "indian", "lebanese", "turkish", "greek", "spanish", "tapas", "portuguese", "mexican", "peruvian",
    "brazilian", "american", "burger", "steak_house", "seafood", "fish", "crepe", "regional", "moroccan",
    "african", "ethiopian", "asian", "mediterranean", "middle_eastern", "israeli", "vegetarian", "vegan",
    "kebab", "sandwich", "bagel", "salad", "brunch", "breakfast", "bistro", "brasserie", "fine_dining",
    "georgian", "german", "british", "coffee_shop", "tea", "ice_cream", "bubble_tea", "juice", "cake",
    "pastry", "chocolate", "international", "barbecue", "pasta", "chicken", "noodle", "dessert",
}
FLAGS = ["cocktails", "wine", "live_music", "brewpub", "beer_garden", "real_ale", "real_fire", "karaoke", "quiz",
         "specialty_coffee", "brunch"]   # CrowdTags.flags
HOURS = ["hours.mo", "hours.tu", "hours.we", "hours.th", "hours.fr", "hours.sa", "hours.su"]   # CrowdHours.dayFacts
FACT_CHOICES = {"terrace": YES_NO, "food": YES_NO, "live_sport": YES_NO, "closed": YES_NO,
                "type": {"bar", "pub", "cafe", "restaurant", "other"},
                "diet.vegan": {"no", "options", "only"}, "diet.vegetarian": {"no", "options", "only"},
                **{"tag." + k: YES_NO for k in CUISINES | set(FLAGS) | {"rooftop"}}}   # CrowdTags.keys


def clock(text):
    """DayHours.clock: "HH:mm", two ASCII digits each, before 24:00; minutes from midnight or None."""
    parts = text.split(":")
    if len(parts) != 2 or not all(len(p) == 2 and p.isascii() and p.isdigit() for p in parts): return None
    hour, minute = int(parts[0]), int(parts[1])
    return hour * 60 + minute if hour < 24 and minute < 60 else None


def day_hours(value):
    """DayHours(value)?.value: `closed`, `24h` or one or two spans in order, a close at or before its
    open the next morning, 06:00 at the latest; a span over the whole day is `24h`. None if invalid."""
    if value in ("closed", "24h"): return value
    spans = []
    for text in value.split(","):
        ends = [clock(e) for e in text.split("-")]
        if len(ends) != 2 or ends[0] is None or ends[1] is None: return None
        start, end = ends
        if end <= start: end += 1440
        if end > 1440 + 360 or start < (spans[-1][1] if spans else 0): return None
        spans.append((start, end))
    if not 1 <= len(spans) <= 2: return None
    if any(s <= 0 and e >= 1440 for s, e in spans): return "24h"
    hm = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    return ",".join(f"{hm(s)}-{hm(e % 1440)}" for s, e in spans)


def is_day(text):
    """CrowdFacts.isDay: `YYYY-MM-DD`, a real day."""
    try: return len(text) == 10 and datetime.datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d") == text
    except ValueError: return False


def canonical(value, fact):
    """CrowdFacts.canonical: the value in its one written form, or None when not one of the fact's fixed choices."""
    if not isinstance(value, str) or not isinstance(fact, str): return None
    if fact in FACT_CHOICES: return value if value in FACT_CHOICES[fact] else None
    if fact == "closed_until": return value if value == "no" or is_day(value) else None
    if fact in HOURS: return day_hours(value)
    return None


# --- Records --------------------------------------------------------------------------------------

def flatten(r):
    """One CloudKit record (Web Services JSON) as a flat row: type, recordName, creator (the user record
    name, in memory only), created and modified (ms since 1970), then its fields."""
    created = r.get("created", {})
    out = {"type": r["recordType"], "recordName": r["recordName"], "creator": created.get("userRecordName", ""),
           "created": int(created.get("timestamp", 0)), "modified": int(r.get("modified", created).get("timestamp", 0))}
    for k, f in r.get("fields", {}).items(): out[k] = f.get("value")
    return out


def readable(r):
    """The records the app reads (CrowdStore.fact / .report): the rest count for nothing, the cap included."""
    if not r["creator"]: return False
    if r["type"] == "Correction": return all(isinstance(r.get(k), str) for k in ("spot", "fact", "value"))
    return (isinstance(r.get("spot"), str) and r.get("part") in ("front", "side", "back") and r.get("seen") in ("sun", "shade")
            and all(isinstance(r.get(k), (int, float)) for k in ("sunAz", "sunEl"))
            and r.get("sky") in ("clear", "partlyCloudy", "cloudy", "rain"))


def utc_day(ms): return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")


def deny_hash(creator):
    """CrowdFacts.denyHash: SHA-256 hex of the creator's user record name, as crowdDeny lists it."""
    return hashlib.sha256(creator.encode()).hexdigest()


def ignored(rows, deny):
    """CrowdFacts.ignored: accounts on the deny list (hashed, lowercased as StoreConfig reads it) and
    accounts with more than DAY_CAP records created on one UTC day, both record types together."""
    deny = {d.lower() for d in deny}
    per_day = {}
    for r in rows:
        key = (r["creator"], utc_day(r["created"]))
        per_day[key] = per_day.get(key, 0) + 1
    return {c for (c, _), n in per_day.items() if n > DAY_CAP} | {r["creator"] for r in rows if deny_hash(r["creator"]) in deny}


def replay(votes, answer, pairs=lambda a, b: True):
    """MeetCore's `replay`: votes by modification date; an answer is agreed when two accounts hold it,
    both after the current one was agreed. The pair that holds at the end, or None. Ties keep the
    input order, as Swift's (stable) sort does."""
    votes = sorted(votes, key=lambda v: v["modified"])
    agreed, since = None, None
    for i, vote in enumerate(votes):
        if since is not None and vote["modified"] <= since: continue
        if agreed and answer(agreed[1]) == answer(vote): continue
        partner = next((p for p in reversed(votes[:i]) if (since is None or p["modified"] > since)
                        and p["creator"] != vote["creator"] and answer(p) == answer(vote) and pairs(p, vote)), None)
        if partner is None: continue
        agreed, since = (partner, vote), vote["modified"]
    return agreed


def answer(r):
    """What two votes must share: the canonical value, any date for closed_until (§4d)."""
    value = canonical(r["value"], r["fact"])
    return "date" if r["fact"] == "closed_until" and value != "no" else value


def agreed(rows, now_ms, ignoring=frozenset()):
    """CrowdFacts.agreed over the Correction rows: {(spot, fact): (value, since ms)}."""
    groups = {}
    for r in rows:
        if r["type"] == "Correction" and r["creator"] not in ignoring and now_ms - r["modified"] < LIFETIME_MS \
                and canonical(r["value"], r["fact"]) is not None:
            groups.setdefault((r["spot"], r["fact"]), []).append(r)
    out = {}
    for key, votes in groups.items():
        pair = replay(votes, answer)
        if not pair: continue
        values = [canonical(v["value"], v["fact"]) for v in pair]
        out[key] = (min(values) if key[1] == "closed_until" else values[-1], pair[1]["modified"])
    return out


def odbl_rows(facts):
    """The public file's rows: agreed facts on `osm:` spots only. apple: and geo: never (§7)."""
    out = []
    for (spot, fact), (value, since) in sorted(facts.items()):
        m = OSM_SPOT.fullmatch(spot)
        if m: out.append({"osm_type": OSM_TYPE[m[1]], "osm_id": int(m[2]), "fact": fact, "value": value, "agreed_on": utc_day(since)})
    return out


# --- Sun reports (SunReports.swift), for the model check --------------------------------------------

def counts(r):
    """SunReport.counts: shade under a clear sky only, sun under clear or partly cloudy."""
    return r["sky"] in ("clear", "partlyCloudy") if r["seen"] == "sun" else r["sky"] == "clear"


def near(a, az, el):
    """SunReport.isNear: within 15° of azimuth (north wraps) and 5° of elevation."""
    turn = abs(a["sunAz"] - az) % 360
    return min(turn, 360 - turn) <= 15 and abs(a["sunEl"] - el) <= 5


def verdict(reports, spot, az, el, now_ms, ignoring=frozenset()):
    """SunReports.verdict: True sun, False shade, None. Also the agreed pairs, for the model check."""
    live = [r for r in reports if r["type"] == "SunReport" and r["spot"] == spot and counts(r) and r["creator"] not in ignoring
            and now_ms - r["modified"] < LIFETIME_MS and near(r, az, el)]
    pairs = []
    for part in sorted({r["part"] for r in live}):
        pair = replay([r for r in live if r["part"] == part], lambda r: r["seen"],
                      lambda a, b: near(a, b["sunAz"], b["sunEl"]))
        if pair: pairs.append(pair)
    return (any(p[1]["seen"] == "sun" for p in pairs) if pairs else None), pairs


def city_of(cell, cities):
    """The cities.json city whose box holds a "row,col" cell's middle, else the cell."""
    try: row, col = (int(x) for x in cell.split(","))
    except (AttributeError, ValueError): return "?"
    lat, lon = (row + (0.5 if row >= 0 else -0.5)) / 50, (col + (0.5 if col >= 0 else -0.5)) / 50
    return next((c["city"] for c in cities if c.get("box") and c["box"][0] <= lat <= c["box"][2] and c["box"][1] <= lon <= c["box"][3]), cell)


def model_check(rows, now_ms, ignoring, cities):
    """§4c: per city, the agreed observations (each report's sun position as a window, pairs counted
    once) and how many contradict what the model said at the second report. For the owner only."""
    reports = [r for r in rows if r["type"] == "SunReport"]
    seen, out = set(), {}
    for r in reports:
        for a, b in verdict(reports, r["spot"], r["sunAz"], r["sunEl"], now_ms, ignoring)[1]:
            if (a["recordName"], b["recordName"]) in seen: continue
            seen.add((a["recordName"], b["recordName"]))
            c = out.setdefault(city_of(b.get("cell"), cities), {"agreed": 0, "compared": 0, "contradicted": 0})
            c["agreed"] += 1
            if b.get("model") in ("sun", "shade"):
                c["compared"] += 1
                c["contradicted"] += b["model"] != b["seen"]
    return dict(sorted(out.items()))


# --- The archive's facts.csv: the app's view against OSM's ------------------------------------------

TILE_FLAGS = {"live_sport": "live_sport", "tag.cocktails": "cocktails", "tag.wine": "drink:wine", "tag.live_music": "live_music",
              "tag.brewpub": "microbrewery", "tag.beer_garden": "beer_garden", "tag.real_ale": "real_ale", "tag.real_fire": "real_fire",
              "tag.karaoke": "karaoke", "tag.quiz": "quiz", "tag.specialty_coffee": "specialty_coffee", "tag.brunch": "brunch"}


def tile_value(venue, fact):
    """A venue tile item's value for a crowd fact, in the crowd's words, or None when the tile says nothing."""
    if venue is None: return None
    seat = venue.get("outdoor_seating")
    if fact == "terrace": return None if seat is None else ("yes" if seat else "no")
    if fact == "food": return None if "food" not in venue else ("yes" if venue["food"] else "no")
    if fact == "type": return venue.get("amenity") if venue.get("amenity") in ("bar", "pub", "cafe", "restaurant") else "other"
    if fact in TILE_FLAGS: return "yes" if venue.get(TILE_FLAGS[fact]) else None
    if fact == "tag.rooftop": return "yes" if isinstance(seat, str) and "roof" in seat else None
    if fact.startswith("tag."): return "yes" if fact[4:] in (venue.get("cuisine") or "").split(";") else None
    if fact.startswith("diet."): return {"yes": "options", "only": "only"}.get(venue.get("diet:" + fact[5:]))
    # ponytail: the whole OSM week, not that day's; parse opening_hours here if the owner needs the day.
    if fact in HOURS: return venue.get("opening_hours")
    return None


def published_venue(spot, cell, _cache={}):
    """The venue tile item of an `osm:` spot in its cell, from the public store, or None."""
    m = OSM_SPOT.fullmatch(spot)
    if not m or not cell: return None
    if cell not in _cache:
        from make_tiles import published_items
        items = published_items("venues", cell)
        _cache[cell] = items if isinstance(items, list) else []
    return next((v for v in _cache[cell] if v.get("osm_type") == m[1] and v.get("osm_id") == int(m[2])), None)


def app_facts(rows, facts, venue=published_venue):
    """facts.csv's rows (§7b): per spot and fact with a live record, the agreed value (source crowd, with
    osm_value where OSM's differs), else on an osm: spot the tiles' (source osm)."""
    cells = {}
    for r in rows:
        if r["type"] == "Correction": cells.setdefault((r["spot"], r["fact"]), r.get("cell"))
    out = []
    for (spot, fact), cell in sorted(cells.items()):
        tile = tile_value(venue(spot, cell), fact)
        if (spot, fact) in facts:
            value = facts[(spot, fact)][0]
            out.append({"spot": spot, "fact": fact, "value": value, "source": "crowd", "osm_value": tile if tile not in (None, value) else None})
        elif OSM_SPOT.fullmatch(spot):
            out.append({"spot": spot, "fact": fact, "value": tile, "source": "osm"})
    return out


# --- Files ----------------------------------------------------------------------------------------

def csv_gz(rows, columns):
    f = io.StringIO()
    w = csv.DictWriter(f, columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows({k: "" if r.get(k) is None else r[k] for k in columns} for r in rows)
    return gzip.compress(f.getvalue().encode(), mtime=0)


def archived(r, salt):
    """A row as the archive keeps it: the creator re-hashed with the secret salt, never in clear."""
    return {"recordType": r["type"], "recordName": r["recordName"],
            "creator": hmac.new(salt.encode(), r["creator"].encode(), hashlib.sha256).hexdigest(),
            "created": r["created"], "modified": r["modified"],
            "fields": {k: r[k] for k in TYPES[r["type"]] if r.get(k) is not None}}


def jsonl_gz(rows):
    return gzip.compress("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows).encode(), mtime=0)


def export(rows, out, deny, now_ms, salt=None, cities=(), venue=published_venue):
    """Writes public/ always and private/<day>/ when `salt` is set; returns the manifest."""
    today = utc_day(now_ms)
    readable_rows = [r for r in rows if readable(r)]
    skip = ignored(readable_rows, deny)
    facts = agreed(readable_rows, now_ms, skip)
    public = odbl_rows(facts)
    os.makedirs(os.path.join(out, "public"), exist_ok=True)
    data = csv_gz(public, ODBL)
    for name in ("latest", today):
        with open(os.path.join(out, "public", name + ".csv.gz"), "wb") as f: f.write(data)
    manifest = {"run": today, "v": SCHEMA, "counts": {
        "Correction": sum(r["type"] == "Correction" for r in rows), "SunReport": sum(r["type"] == "SunReport" for r in rows),
        "unreadable": len(rows) - len(readable_rows), "ignored_accounts": len(skip), "agreed": len(facts), "odbl": len(public)},
        "model_check": model_check(readable_rows, now_ms, skip, cities)}
    if salt:
        d = os.path.join(out, "private", today)
        os.makedirs(d, exist_ok=True)
        rows = sorted(rows, key=lambda r: r["recordName"])
        for name, kind in (("corrections", "Correction"), ("sun", "SunReport")):
            with open(os.path.join(d, name + ".jsonl.gz"), "wb") as f: f.write(jsonl_gz([archived(r, salt) for r in rows if r["type"] == kind]))
        with open(os.path.join(d, "facts.csv.gz"), "wb") as f: f.write(csv_gz(app_facts(readable_rows, facts, venue), FACTS))
        with open(os.path.join(d, "manifest.json"), "w") as f: json.dump(manifest, f, indent=1, ensure_ascii=False)
    return manifest


def to_delete(days, today):
    """§7b's retention over the archive's days: the last 12 weekly snapshots, then the first of each
    month for 24 months; the days to delete."""
    days = sorted({d for d in days if is_day(d)})
    keep = set(days[-12:])
    firsts = {}
    for d in days: firsts.setdefault(d[:7], d)
    month = lambda d: int(d[:4]) * 12 + int(d[5:7])
    keep |= {d for d in firsts.values() if month(today) - month(d) < 24}
    return [d for d in days if d not in keep]


# --- CloudKit Web Services ------------------------------------------------------------------------

class CloudKit:
    """Server-to-server requests to the container's public database. The key never leaves this object."""

    def __init__(self, key_id, pem, environment="production"):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        self._key_id = key_id
        self._key = serialization.load_pem_private_key(pem.encode(), password=None)
        self._sign = lambda data: self._key.sign(data, ec.ECDSA(hashes.SHA256()))
        self.path = f"/database/1/{CONTAINER}/{environment}/public"

    def post(self, op, body):
        subpath = f"{self.path}/{op}"
        data = json.dumps(body).encode()
        date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = f"{date}:{base64.b64encode(hashlib.sha256(data).digest()).decode()}:{subpath}"
        headers = {"Content-Type": "application/json",
                   "X-Apple-CloudKit-Request-KeyID": self._key_id,
                   "X-Apple-CloudKit-Request-ISO8601Date": date,
                   "X-Apple-CloudKit-Request-SignatureV1": base64.b64encode(self._sign(message.encode())).decode()}
        for attempt in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(API + subpath, data, headers), timeout=60) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 503) or attempt == 3:
                    raise SystemExit(f"CloudKit {op}: HTTP {e.code} {e.read()[:300].decode(errors='replace')}")
            except urllib.error.URLError as e:
                if attempt == 3: raise SystemExit(f"CloudKit {op}: {e.reason}")
            time.sleep(2 ** (attempt + 1))

    def query(self, record_type):
        """Every record of one type (the type needs a queryable recordName index)."""
        out, marker = [], None
        while True:
            body = {"query": {"recordType": record_type}, "resultsLimit": 200}
            if marker: body["continuationMarker"] = marker
            r = self.post("records/query", body)
            for rec in r.get("records", []):
                if "serverErrorCode" in rec: raise SystemExit(f"CloudKit query {record_type}: {rec['serverErrorCode']}")
                out.append(rec)
            marker = r.get("continuationMarker")
            if not marker: return out


def cloudkit_from_env():
    """A CloudKit client from CLOUDKIT_KEY_ID and CLOUDKIT_PRIVATE_KEY, or None when either is unset."""
    key_id, pem = os.environ.get("CLOUDKIT_KEY_ID", "").strip(), os.environ.get("CLOUDKIT_PRIVATE_KEY", "").strip()
    if not key_id or not pem: return None
    return CloudKit(key_id, pem.replace("\\n", "\n"), os.environ.get("CLOUDKIT_ENVIRONMENT", "production"))


def load(path, key, default):
    try:
        with open(path) as f: data = json.load(f)
    except FileNotFoundError:
        return default
    return data.get(key, default) if key else data


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = a.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", default="crowd-out")
    e.add_argument("--config", default="config.json", help="the store config; its crowdDeny is the deny list")
    e.add_argument("--cities", default="cities.json", help="for the model check's cities")
    e.add_argument("--records", help="CloudKit records saved one JSON a line, instead of asking CloudKit")
    p = sub.add_parser("prune", help="reads archive day folders on stdin, prints those to delete")
    p.add_argument("--today", default=utc_day(time.time() * 1000))
    args = a.parse_args()
    if args.cmd == "prune":
        for d in to_delete([line.strip().strip("/").split()[-1] for line in sys.stdin if line.strip()], args.today): print(d)
        return
    if args.records:
        with open(args.records) as f: raw = [json.loads(l) for l in f if l.strip()]
    else:
        ck = cloudkit_from_env()
        if ck is None:
            print("CLOUDKIT_KEY_ID or CLOUDKIT_PRIVATE_KEY is not set: nothing exported.")
            return
        raw = [rec for t in TYPES for rec in ck.query(t)]
    salt = os.environ.get("CROWD_ARCHIVE_SALT", "")
    if not salt: print("CROWD_ARCHIVE_SALT is not set: the private archive is skipped.")
    rows = [flatten(r) for r in raw if r.get("recordType") in TYPES]
    export(rows, args.out, load(args.config, "crowdDeny", []), int(time.time() * 1000), salt, load(args.cities, None, []))
    print(f"Exported -> {args.out}")   # the counts stay in the private manifest: the run's log is public


if __name__ == "__main__":
    main()
