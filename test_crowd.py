"""python3 test_crowd.py — the crowd export's replay, filters and files on made-up records, no CloudKit needed."""
import csv, io, json, os, subprocess, sys, tempfile
import crowd
from crowd import DAY_CAP, agreed, export, facts, flatten, model_check, read_archive, screen, sha
from crowd_restore import operations

DAY = 86400_000
NOW = 1_790_000_000_000   # 2026-09-21 UTC
n = 0


def c(user, value, t, spot="s1", fact="seats", cell="2442,116"):
    """A Correction as CloudKit Web Services returns it, by `user`, modified `t` ms before NOW."""
    global n
    n += 1
    return {"recordName": f"r{n}", "recordType": "Correction",
            "created": {"timestamp": NOW - t, "userRecordName": user}, "modified": {"timestamp": NOW - t, "userRecordName": user},
            "fields": {"spot": {"value": spot}, "cell": {"value": cell}, "fact": {"value": fact}, "value": {"value": value}, "v": {"value": 1}}}


def sun(user, seen, model, t):
    global n
    n += 1
    return {"recordName": f"r{n}", "recordType": "SunReport", "created": {"timestamp": NOW - t, "userRecordName": user},
            "modified": {"timestamp": NOW - t, "userRecordName": user},
            "fields": {"spot": {"value": "s1"}, "cell": {"value": "2442,116"}, "part": {"value": "front"}, "seen": {"value": seen},
                       "sky": {"value": "clear"}, "model": {"value": model}, "sunAz": {"value": 181.5}, "sunEl": {"value": 32.25},
                       "month": {"value": 9}, "v": {"value": 1}}}


def value(*records):
    a = agreed([flatten(r) for r in records])
    return a and a[0]


# The creator is kept only as the SHA-256 hex of its user record name.
r = flatten(c("_alice", "yes", 0))
assert r["creator"] == sha("_alice") and len(r["creator"]) == 64 and r["value"] == "yes" and r["modified"] == NOW, r

# Replay: one lone voice never agrees; the first pair of different accounts does.
assert value(c("_a", "yes", 9)) is None
assert value(c("_a", "yes", 9), c("_b", "no", 8)) is None
assert value(c("_a", "yes", 9), c("_b", "yes", 8)) == "yes"
# Two records of one account are one voice: its current (latest) record.
assert value(c("_a", "yes", 9), c("_a", "yes", 8)) is None
assert value(c("_a", "yes", 9), c("_a", "no", 8), c("_b", "yes", 7)) is None   # _a now says no
# A third voice for the agreed value changes nothing; one later voice for another value neither.
assert value(c("_a", "yes", 9), c("_b", "yes", 8), c("_c", "yes", 7), c("_d", "no", 6)) == "yes"
# Two later voices for another value override.
assert value(c("_a", "yes", 9), c("_b", "yes", 8), c("_c", "no", 7), c("_d", "no", 6)) == "no"
# ... but only voices dated after the agreement: a "no" before it and one after are not a pair.
assert value(c("_c", "no", 10), c("_a", "yes", 9), c("_b", "yes", 8), c("_d", "no", 6)) == "yes"
# and back again, with two voices after the override.
assert value(c("_a", "yes", 9), c("_b", "yes", 8), c("_c", "no", 7), c("_d", "no", 6), c("_e", "yes", 5), c("_f", "yes", 4)) == "yes"
# Order is by modification date, not by the order records come in.
assert value(c("_d", "no", 6), c("_c", "no", 7), c("_b", "yes", 8), c("_a", "yes", 9)) == "no"

# Per spot and fact: the pairs never mix across spots or facts.
rows = [flatten(x) for x in (c("_a", "yes", 9), c("_b", "yes", 8, spot="s2"), c("_c", "4", 9, fact="tables"), c("_d", "4", 8, fact="tables"))]
assert [(f["spot"], f["fact"], f["value"]) for f in facts(rows)] == [("s1", "tables", "4")], facts(rows)
# A value outside its fact's choices is dropped before the replay (a fact without listed choices keeps every value).
crowd.FACT_CHOICES["seats"] = {"yes", "no"}
rows = [flatten(x) for x in (c("_a", "yes", 9), c("_b", "yes", 8), c("_c", "maybe", 7), c("_d", "maybe", 6))]
assert [f["value"] for f in facts(rows)] == ["yes"] and facts(rows[2:]) == []
crowd.FACT_CHOICES.clear()
assert [f["value"] for f in facts(rows)] == ["maybe"]

# Expiry: a record over 365 days old is dropped, so it no longer makes a pair.
rows = [flatten(x) for x in (c("_a", "yes", 366 * DAY), c("_b", "yes", 1 * DAY))]
assert len(screen(rows, NOW, [])) == 1 and facts(screen(rows, NOW, [])) == []
assert len(screen([flatten(c("_a", "yes", 364 * DAY))], NOW, [])) == 1

# Deny list: the accounts whose hash is listed (any case) are dropped.
rows = [flatten(x) for x in (c("_a", "yes", 9), c("_b", "yes", 8))]
assert [r["creator"] for r in screen(rows, NOW, [sha("_b").upper()])] == [sha("_a")]
assert facts(screen(rows, NOW, [sha("_b")])) == [] and len(facts(screen(rows, NOW, []))) == 1

# Cap: over DAY_CAP records on one UTC day ignores the account entirely (its other days too); DAY_CAP is allowed.
spam = [flatten(c("_s", "yes", 3 * DAY + i, spot=f"x{i}")) for i in range(DAY_CAP + 1)] + [flatten(c("_s", "yes", 30 * DAY))]
ok = [flatten(c("_o", "yes", 3 * DAY + i, spot=f"x{i}")) for i in range(DAY_CAP)]
kept = screen(spam + ok, NOW, [])
assert {r["creator"] for r in kept} == {sha("_o")} and len(kept) == DAY_CAP, len(kept)
# Sun reports count toward the cap.
mixed = [flatten(c("_m", "yes", 5 * DAY + i, spot=f"x{i}")) for i in range(DAY_CAP)] + [flatten(sun("_m", "sun", "sun", 5 * DAY + 999))]
assert screen(mixed, NOW, []) == []

# Model check: per UTC month, the reports whose seen and model are both sun or shade, and those equal.
rows = [flatten(x) for x in (sun("_a", "sun", "sun", 1 * DAY), sun("_b", "shade", "sun", 1 * DAY), sun("_c", "mixed", "sun", 1 * DAY),
                             sun("_d", "shade", "shade", 40 * DAY), c("_e", "yes", 1 * DAY))]
assert model_check(rows) == {"2026-08": {"reports": 1, "compared": 1, "matched": 1},
                             "2026-09": {"reports": 3, "compared": 2, "matched": 1}}, model_check(rows)

# The files: the public CSV has its five columns, no account and no time finer than the day; the private one keeps everything.
d = tempfile.mkdtemp()
raw = [c("_a", "yes", 9), c("_b", "yes", 8), c("_x", "no", 7), sun("_a", "sun", "shade", 5)]
assert export([flatten(x) for x in raw], d, [sha("_x")], NOW) == (4, 3, 1)
public = open(os.path.join(d, "public", "facts.csv")).read()
assert public.splitlines() == ["spot,cell,fact,value,agreed", 's1,"2442,116",seats,yes,2026-09-21'], public
assert list(csv.reader(io.StringIO(public)))[1] == ["s1", "2442,116", "seats", "yes", "2026-09-21"] and sha("_a") not in public
assert "ODbL" in open(os.path.join(d, "public", "LICENCE.txt")).read()
check = json.load(open(os.path.join(d, "public", "model-check.json")))
assert check["months"] == {"2026-09": {"reports": 1, "compared": 1, "matched": 0}}, check
private = list(csv.DictReader(open(os.path.join(d, "private", "facts.csv"))))
assert len(private) == 4 and {r["creator"] for r in private} == {sha("_a"), sha("_b"), sha("_x")}, private   # the denied one too
assert list(private[0]) == crowd.ARCHIVE
assert sorted(os.listdir(os.path.join(d, "public"))) == ["LICENCE.txt", "facts.csv", "model-check.json"]

# Restore: the archive back into records/modify operations, typed, fields of the record's type only.
rows = read_archive(os.path.join(d, "private", "facts.csv"))
ops = operations(rows)
assert len(ops) == 4 and {o["operationType"] for o in ops} == {"create"}
s = next(o["record"] for o in ops if o["record"]["recordType"] == "SunReport")
assert s["fields"]["sunAz"] == {"value": 181.5, "type": "DOUBLE"} and s["fields"]["month"] == {"value": 9, "type": "INT64"}, s
assert s["fields"]["seen"] == {"value": "sun", "type": "STRING"} and "fact" not in s["fields"] and "creator" not in s["fields"]
k = next(o["record"] for o in ops if o["record"]["recordType"] == "Correction")
assert set(k["fields"]) == {"spot", "cell", "fact", "value", "v"} and k["fields"]["v"] == {"value": 1, "type": "INT64"}, k
assert {o["operationType"] for o in operations(rows, replace=True)} == {"forceReplace"}

# Without the secrets the export writes nothing and exits 0; the restore is a dry run unless --apply.
env = {k: v for k, v in os.environ.items() if not k.startswith("CLOUDKIT_")}
out = os.path.join(tempfile.mkdtemp(), "out")
p = subprocess.run([sys.executable, "crowd.py", "export", "--out", out], env=env, capture_output=True, text=True, check=True)
assert "not set" in p.stdout and not os.path.exists(out), p.stdout
p = subprocess.run([sys.executable, "crowd_restore.py", os.path.join(d, "private", "facts.csv")], env=env, capture_output=True, text=True, check=True)
assert "4 records (3 Correction, 1 SunReport), create" in p.stdout and "Dry run" in p.stdout, p.stdout

# The request signature: ECDSA P-256 over date:base64(sha256(body)):subpath, verifiable with the key's public half.
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
except ImportError:
    print("(cryptography not installed: signature not tested)")
else:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    ck = crowd.CloudKit("kid", pem)
    assert ck.path == "/database/1/iCloud.uk.alephb.compromise/production/public"
    key.public_key().verify(ck._sign(b"2026-10-25T00:00:00Z:abc=:/x"), b"2026-10-25T00:00:00Z:abc=:/x", ec.ECDSA(hashes.SHA256()))
print("ok")
