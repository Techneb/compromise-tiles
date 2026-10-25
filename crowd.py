#!/usr/bin/env python3
"""Weekly crowd export: the venue corrections and sun reports of the app's public CloudKit database.

  python3 crowd.py export --out crowd-out [--config config.json]            # CloudKit -> files
  python3 crowd.py export --records raw.jsonl --out crowd-out               # from saved records (no CloudKit)

Reads every Correction and SunReport record of the container's public database through CloudKit Web
Services, signed with a server-to-server key (CLOUDKIT_KEY_ID, CLOUDKIT_PRIVATE_KEY: the PEM itself;
read from the environment, never printed). Without them `export` says so and exits 0 having written
nothing. Writes, under --out:

  public/facts.csv         the agreed facts (ODbL): spot, cell, fact, value, agreed (UTC day); no account, no time of day
  public/model-check.json  per month, how often a sun report's seen matched the model's verdict
  public/LICENCE.txt       the export's licence notice
  private/facts.csv        every record fetched, raw, with the SHA-256 of its creator: the archive crowd_restore.py replays

Before the replay, records older than MAX_AGE_DAYS are dropped, then the accounts of the deny list
(`crowdDeny` of config.json, the store config: SHA-256 hex of the creator's user record name), then
every record of an account with more than DAY_CAP records on one UTC day. The replay is MeetCore's
CrowdFacts.agreed: per spot and fact, each account's current record, by modification date; a value
is agreed once two different accounts hold it, both dated after the current agreed value was agreed.
Standard library, and `cryptography` for the request signature (export from CloudKit only).
"""
import argparse, base64, csv, datetime, hashlib, io, json, os, time, urllib.error, urllib.request

CONTAINER = "iCloud.uk.alephb.compromise"
API = "https://api.apple-cloudkit.com"
MAX_AGE_DAYS = 365
DAY_CAP = 140   # records per account per UTC day; above, the account is ignored entirely
VERDICTS = {"sun", "shade"}
# The fixed choices of each fact: a value outside them is dropped before the replay.
# TODO: copy the app's choices per fact here; until then a fact not listed keeps every value as-is.
FACT_CHOICES = {}
TYPES = {"Correction": ["spot", "cell", "fact", "value", "v"],
         "SunReport": ["spot", "cell", "part", "seen", "sky", "model", "sunAz", "sunEl", "month", "v"]}
FIELD_TYPES = {"v": "INT64", "month": "INT64", "sunAz": "DOUBLE", "sunEl": "DOUBLE"}   # the rest STRING
ARCHIVE = ["type", "recordName", "creator", "created", "modified",
           "spot", "cell", "fact", "value", "part", "seen", "sky", "model", "sunAz", "sunEl", "month", "v"]
PUBLIC = ["spot", "cell", "fact", "value", "agreed"]
NOTICE = """Compromise crowd facts, (c) Compromise contributors. This database is made available under the
Open Database License (ODbL) 1.0: https://opendatacommons.org/licenses/odbl/1-0/. Any rights in
individual contents of the database are licensed under the Database Contents License:
https://opendatacommons.org/licenses/dbcl/1-0/. You may share, adapt and use it, provided you
attribute "Compromise contributors", keep any adapted database open under the ODbL, and do not
apply technical restrictions that prevent others from doing the same. facts.csv lists, per venue
key, the facts its users agreed on and the UTC day each was agreed; model-check.json counts how
often reported sun or shade matched the computed one, per month. It holds no account identifiers.
"""


def sha(s): return hashlib.sha256(s.encode()).hexdigest()


def day(ms): return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")


# --- Records ----------------------------------------------------------------------------------------

def flatten(r):
    """One CloudKit record (Web Services JSON) as a flat row: type, recordName, creator (SHA-256 hex of
    the creator's user record name), created and modified (ms since 1970), then its fields."""
    out = {"type": r["recordType"], "recordName": r["recordName"],
           "creator": sha(r.get("created", {}).get("userRecordName", "")),
           "created": int(r.get("created", {}).get("timestamp", 0)),
           "modified": int(r.get("modified", r.get("created", {})).get("timestamp", 0))}
    for k, f in r.get("fields", {}).items(): out[k] = f.get("value")
    return out


def screen(rows, now_ms, deny):
    """The rows the export counts: none older than MAX_AGE_DAYS, none of a denied account, none of an
    account over DAY_CAP records on one UTC day (the cap counted over the rows left by the first two)."""
    cutoff = now_ms - MAX_AGE_DAYS * 86400_000
    deny = {d.lower() for d in deny}
    rows = [r for r in rows if r["modified"] >= cutoff and r["creator"] not in deny]
    per_day = {}
    for r in rows:
        k = (r["creator"], day(r["modified"]))
        per_day[k] = per_day.get(k, 0) + 1
    capped = {c for (c, _), n in per_day.items() if n > DAY_CAP}
    return [r for r in rows if r["creator"] not in capped]


def agreed(records):
    """CrowdFacts.agreed over one spot and fact: (value, record that agreed it) or None.
    `records`: dicts with creator, modified, value. Each account's current record is its latest."""
    current = {}
    for r in records:
        c = current.get(r["creator"])
        if c is None or (r["modified"], r["recordName"]) > (c["modified"], c["recordName"]): current[r["creator"]] = r
    value, at, by = None, None, None
    seen = []   # records walked so far
    for r in sorted(current.values(), key=lambda r: (r["modified"], r["recordName"])):
        if r["value"] != value and (at is None or r["modified"] > at):
            if any(s["value"] == r["value"] and s["creator"] != r["creator"] and (at is None or s["modified"] > at) for s in seen):
                value, at, by = r["value"], r["modified"], r
        seen.append(r)
    return None if by is None else (value, by)


def facts(rows):
    """The agreed facts of the Correction rows: [{spot, cell, fact, value, agreed}], by spot and fact.
    A value outside its fact's FACT_CHOICES is dropped first."""
    groups = {}
    for r in rows:
        if r["type"] != "Correction" or r.get("value") is None: continue
        choices = FACT_CHOICES.get(r["fact"])
        if choices is not None and r["value"] not in choices: continue
        groups.setdefault((r["spot"], r["fact"]), []).append(r)
    out = []
    for (spot, fact), rs in sorted(groups.items()):
        a = agreed(rs)
        if a: out.append({"spot": spot, "cell": a[1].get("cell") or "", "fact": fact, "value": a[0], "agreed": day(a[1]["modified"])})
    return out


def model_check(rows):
    """{"YYYY-MM": {reports, compared, matched}} over the SunReport rows, by the UTC month of each
    report: `compared` those whose seen and model are both sun or shade, `matched` those equal."""
    out = {}
    for r in rows:
        if r["type"] != "SunReport": continue
        m = out.setdefault(day(r["modified"])[:7], {"reports": 0, "compared": 0, "matched": 0})
        m["reports"] += 1
        if r.get("seen") in VERDICTS and r.get("model") in VERDICTS:
            m["compared"] += 1
            m["matched"] += r["seen"] == r["model"]
    return dict(sorted(out.items()))


def to_csv(rows, columns):
    f = io.StringIO()
    w = csv.DictWriter(f, columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows({k: "" if r.get(k) is None else r[k] for k in columns} for r in rows)
    return f.getvalue()


def read_archive(path):
    """The rows of a private facts.csv, typed back (empty cells absent)."""
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            row = {k: v for k, v in r.items() if v != ""}
            for k in ("created", "modified", "v", "month"):
                if k in row: row[k] = int(row[k])
            for k in ("sunAz", "sunEl"):
                if k in row: row[k] = float(row[k])
            out.append(row)
    return out


# --- CloudKit Web Services --------------------------------------------------------------------------

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


# --- Export -----------------------------------------------------------------------------------------

def deny_list(config_path):
    try:
        with open(config_path) as f: return json.load(f).get("crowdDeny", [])
    except FileNotFoundError:
        return []


def export(rows, out, deny, now_ms):
    """Writes the public and private files under `out`; returns (archived, kept, agreed facts)."""
    kept = screen(rows, now_ms, deny)
    agreed_facts = facts(kept)
    os.makedirs(os.path.join(out, "public"), exist_ok=True)
    os.makedirs(os.path.join(out, "private"), exist_ok=True)
    with open(os.path.join(out, "public", "facts.csv"), "w") as f: f.write(to_csv(agreed_facts, PUBLIC))
    with open(os.path.join(out, "public", "model-check.json"), "w") as f:
        json.dump({"generated": day(now_ms), "months": model_check(kept)}, f, indent=1)
    with open(os.path.join(out, "public", "LICENCE.txt"), "w") as f: f.write(NOTICE)
    rows = sorted(rows, key=lambda r: (r["type"], r["recordName"]))
    with open(os.path.join(out, "private", "facts.csv"), "w") as f: f.write(to_csv(rows, ARCHIVE))
    return len(rows), len(kept), len(agreed_facts)


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = a.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", default="crowd-out")
    e.add_argument("--config", default="config.json", help="the store config; its crowdDeny is the deny list")
    e.add_argument("--records", help="CloudKit records saved one JSON a line, instead of asking CloudKit")
    args = a.parse_args()
    if args.records:
        with open(args.records) as f: raw = [json.loads(l) for l in f if l.strip()]
    else:
        ck = cloudkit_from_env()
        if ck is None:
            print("CLOUDKIT_KEY_ID or CLOUDKIT_PRIVATE_KEY is not set: nothing exported.")
            return
        raw = [rec for t in TYPES for rec in ck.query(t)]
    rows = [flatten(r) for r in raw if r.get("recordType") in TYPES]
    n, kept, agreed_n = export(rows, args.out, deny_list(args.config), int(time.time() * 1000))
    print(f"{n} records fetched, {kept} counted, {agreed_n} agreed facts -> {args.out}/public, {args.out}/private")


if __name__ == "__main__":
    main()
