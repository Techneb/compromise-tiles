"""python3 test_crowd.py — the crowd export on made-up records, no CloudKit needed. The first half is
MeetCore's CrowdFactsTests and CrowdOverlayTests (testDayHoursValues), case for case: a drift between
the Swift and this port fails here."""
import csv, gzip, hmac, hashlib, io, json, os, subprocess, sys, tempfile
import crowd
from crowd import DAY_CAP, LIFETIME_MS, agreed, canonical, day_hours, deny_hash, export, flatten, ignored, to_delete, verdict
from crowd_restore import operations, read_archive

START = 1_790_000_000_000   # ms, 2026-09, as CrowdFactsTests' start
SPOT = "osm:n42"
n = 0


def record(kind, user, minutes, fields):
    """A record as CloudKit Web Services returns it, created and modified `minutes` after START."""
    global n
    n += 1
    t = START + int(minutes * 60_000)
    return {"recordName": f"r{n:04d}", "recordType": kind, "created": {"timestamp": t, "userRecordName": user},
            "modified": {"timestamp": t, "userRecordName": user}, "fields": {k: {"value": v} for k, v in fields.items()}}


def vote(who, value, at, fact="terrace", spot=SPOT, cell="2443,116"):
    return flatten(record("Correction", who, at, {"spot": spot, "cell": cell, "fact": fact, "value": value, "v": 1}))


def report(who, sunny, azimuth, at, elevation=30, sky="clear", part="front", model="unknown", spot=SPOT):
    return flatten(record("SunReport", who, at, {"spot": spot, "cell": "2443,116", "part": part, "seen": "sun" if sunny else "shade",
                                                  "sunAz": azimuth, "sunEl": elevation, "month": 9, "sky": sky, "model": model, "v": 1}))


def value(votes, fact="terrace", ignoring=frozenset(), now=START + 86400_000):
    a = agreed(votes, now, ignoring).get((SPOT, fact))
    return a and a[0]


# --- CrowdFactsTests ------------------------------------------------------------------------------

# testFirstPairAgreesLoneVoiceNever
assert value([vote("a", "yes", 0)]) is None
assert value([vote("a", "yes", 0), vote("b", "yes", 1)]) == "yes"
assert value([vote("a", "yes", 0), vote("b", "no", 1)]) is None

# testLaterContradictingPairOverrides
votes = [vote("a", "yes", 0), vote("b", "yes", 1), vote("c", "no", 2), vote("d", "no", 3)]
assert value(votes) == "no"
assert value(votes[:3]) == "yes", "one later voice does not override"
assert agreed(votes, START + 600_000)[(SPOT, "terrace")][1] == START + 180_000, "agreed when the second voice came"

# testVotesBeforeTheAgreementNeverPairAgainstIt
votes = [vote("a", "yes", 0), vote("c", "no", 1), vote("b", "yes", 2), vote("d", "no", 3)]
assert value(votes) == "yes"
assert value(votes + [vote("e", "no", 4)]) == "no", "two voices after it do"

# testThirdVoiceChangesNothing
assert value([vote("a", "yes", 0), vote("b", "yes", 1), vote("c", "yes", 2), vote("d", "no", 3)]) == "yes"

# testOneAccountNeverAgreesWithItself
assert value([vote("a", "yes", 0), vote("a", "yes", 1)]) is None

# testExpiredDeniedAndInvalidVotesDropOut
old = [vote("a", "yes", 0), vote("b", "yes", 1)]
assert value(old, now=START + LIFETIME_MS + 3600_000) is None, "12 months on"
assert value(old, ignoring={"b"}) is None
assert value([vote("a", "maybe", 0), vote("b", "maybe", 1)]) is None, "not a fixed choice"
assert value([vote("a", "yes", 0, fact="tag.unicorn"), vote("b", "yes", 1, fact="tag.unicorn")], fact="tag.unicorn") is None

# testFactsAndSpotsAreReplayedApart
assert agreed([vote("a", "yes", 0), vote("b", "yes", 1, fact="food"), vote("c", "yes", 2, spot="osm:n7")], START) == {}

# testHoursAgreeOnTheSameSpansWhateverTheSpelling
assert value([vote("a", "18:00-02:00", 0, fact="hours.fr"), vote("b", "18:00-02:00", 1, fact="hours.fr")], fact="hours.fr") == "18:00-02:00"
assert value([vote("a", "00:00-00:00", 0, fact="hours.mo"), vote("b", "24h", 1, fact="hours.mo")], fact="hours.mo") == "24h"

# testClosedUntilAgreesWhateverTheDatesOnTheEarlier
votes = [vote("a", "2026-10-02", 0, fact="closed_until"), vote("b", "2026-09-20", 1, fact="closed_until")]
assert value(votes, fact="closed_until") == "2026-09-20"
assert value(votes + [vote("c", "no", 2, fact="closed_until"), vote("d", "no", 3, fact="closed_until")], fact="closed_until") == "no"
assert canonical("2026-02-30", "closed_until") is None, "not a day"

# testDailyCapIgnoresAnAccountEntirely
busy = [vote("spam", "yes", i, spot=f"osm:n{i}") for i in range(DAY_CAP + 1)]
calm = [vote("calm", "yes", i, spot=f"osm:n{i}") for i in range(DAY_CAP)]
assert ignored(busy + calm, []) == {"spam"}, "141 in a UTC day is over, 140 is not"
assert ignored([vote("spread", "yes", i * 60, spot=f"osm:n{i}") for i in range(DAY_CAP + 1)], []) == set(), "spread over days"

# testDenyListIsHashed (and lowercased on reading, as testStoreConfigCrowdKeys)
votes = [vote("_abc", "yes", 0), vote("_def", "yes", 1)]
assert ignored(votes, [deny_hash("_abc")]) == {"_abc"} and ignored(votes, [deny_hash("_abc").upper()]) == {"_abc"}
assert len(deny_hash("_abc")) == 64

# testSunReportsAgreeInTheSunsWindow
def sun_verdict(reports, azimuth=200, elevation=30):
    return verdict(reports, SPOT, azimuth, elevation, START)[0]
shade = [report("a", False, 195, 0), report("b", False, 205, 1)]
assert sun_verdict(shade) is False
assert sun_verdict(shade, azimuth=230) is None, "another time of day"
assert sun_verdict(shade, elevation=40) is None, "another season"
assert sun_verdict([report("a", False, 186, 0), report("b", False, 214, 1)]) is None, "28° apart from each other"
assert sun_verdict([report("a", False, 200, 0, sky="partlyCloudy"), report("b", False, 200, 1)]) is None, "shade under cloud"
assert sun_verdict([report("a", True, 200, 0, sky="partlyCloudy"), report("b", True, 200, 1)]) is True, "sun through broken cloud"
assert sun_verdict(shade + [report("c", True, 200, 2), report("d", True, 201, 3)]) is True, "a later pair overrides"
assert sun_verdict(shade + [report("e", True, 200, 4, part="back"), report("f", True, 200, 5, part="back")]) is True, "one sunny part"
assert sun_verdict([report("a", False, 355, 0), report("b", False, 5, 1)]) is None
assert sun_verdict([report("a", False, 355, 0), report("b", False, 5, 1)], azimuth=0) is False, "north wraps"

# --- CrowdOverlayTests.testDayHoursValues -----------------------------------------------------------

assert day_hours("08:00-23:30") == "08:00-23:30"
assert day_hours("22:00-06:00") == "22:00-06:00", "06:00 next day, the latest"
assert day_hours("22:00-06:30") is None
assert day_hours("8:00-23:00") is None, "two digits"
assert day_hours("12:00-15:00,14:00-23:00") is None, "overlapping"
assert day_hours("10:00-11:00,12:00-13:00,14:00-15:00") is None, "two spans at most"
assert day_hours("open") is None
assert day_hours("12:00-15:00,18:00-23:00") == "12:00-15:00,18:00-23:00"
assert day_hours("24h") == "24h" and day_hours("closed") == "closed"
# The fixed choices beyond the Swift tests: CrowdTags.keys and the diets.
assert canonical("yes", "tag.italian") == "yes" and canonical("yes", "tag.brunch") == "yes" and canonical("yes", "tag.rooftop") == "yes"
assert canonical("options", "diet.vegan") == "options" and canonical("yes", "diet.vegan") is None and canonical("yes", "closed") == "yes"

# --- The export -----------------------------------------------------------------------------------

NOW = START + 86400_000
cities = [{"city": "Paris", "box": [48.815, 2.224, 48.902, 2.47]}]
rows = [vote("a", "yes", 0), vote("b", "yes", 1),                                          # osm:n42 terrace
        vote("a", "no", 0, fact="food"),                                                    # one voice: not agreed
        vote("a", "yes", 0, spot="apple:I123"), vote("b", "yes", 1, spot="apple:I123"),     # agreed, never public
        vote("a", "yes", 0, spot="geo:48.8606,2.3376:abcd1234"), vote("b", "yes", 1, spot="geo:48.8606,2.3376:abcd1234"),
        vote("x", "pub", 0, fact="type"), vote("y", "pub", 1, fact="type"),                 # y denied: not agreed
        vote("a", "bar", 0, fact="type", spot="osm:w7"), vote("b", "bar", 1, fact="type", spot="osm:w7"),
        report("a", False, 200, 2, model="sun"), report("b", False, 201, 3, model="sun")]
tile = {"osm_type": "n", "osm_id": 42, "outdoor_seating": False, "food": True, "amenity": "pub"}
d = tempfile.mkdtemp()
m = export(rows, d, [deny_hash("y")], NOW, salt="pepper", cities=cities, venue=lambda spot, cell: tile if spot == SPOT else None)

# The public file: osm: spots only, five columns, a day; no apple:, no geo:, no sun, no account.
public = gzip.decompress(open(os.path.join(d, "public", "latest.csv.gz"), "rb").read()).decode()
assert public.splitlines() == ["osm_type,osm_id,fact,value,agreed_on", "node,42,terrace,yes,2026-09-21", "way,7,type,bar,2026-09-21"], public
assert "apple" not in public and "geo" not in public and "I123" not in public and "shade" not in public
assert open(os.path.join(d, "public", "2026-09-22.csv.gz"), "rb").read() == open(os.path.join(d, "public", "latest.csv.gz"), "rb").read()
assert sorted(os.listdir(os.path.join(d, "public"))) == ["2026-09-22.csv.gz", "latest.csv.gz"]
assert crowd.odbl_rows({("apple:I1", "terrace"): ("yes", NOW), ("geo:1,2:ab", "food"): ("no", NOW), ("osm:x9", "food"): ("no", NOW)}) == []

# The archive: JSON Lines, the creator re-hashed with the salt, never in clear nor as its deny hash.
day = os.path.join(d, "private", "2026-09-22")
assert sorted(os.listdir(day)) == ["corrections.jsonl.gz", "facts.csv.gz", "manifest.json", "sun.jsonl.gz"]
raw = gzip.decompress(open(os.path.join(day, "corrections.jsonl.gz"), "rb").read()).decode()
first = json.loads(raw.splitlines()[0])
assert first["creator"] == hmac.new(b"pepper", b"a", hashlib.sha256).hexdigest() and first["fields"]["spot"] == SPOT, first
assert '"a"' not in raw and deny_hash("a") not in raw and len(raw.splitlines()) == 11
assert len(gzip.decompress(open(os.path.join(day, "sun.jsonl.gz"), "rb").read()).decode().splitlines()) == 2
facts = list(csv.DictReader(io.StringIO(gzip.decompress(open(os.path.join(day, "facts.csv.gz"), "rb").read()).decode())))
assert [(f["spot"], f["fact"], f["value"], f["source"], f["osm_value"]) for f in facts] == [
    ("apple:I123", "terrace", "yes", "crowd", ""), ("geo:48.8606,2.3376:abcd1234", "terrace", "yes", "crowd", ""),
    (SPOT, "food", "yes", "osm", ""), (SPOT, "terrace", "yes", "crowd", "no"), (SPOT, "type", "pub", "osm", ""),
    ("osm:w7", "type", "bar", "crowd", "")], facts
manifest = json.load(open(os.path.join(day, "manifest.json")))
assert manifest["v"] == 1 and manifest["run"] == "2026-09-22" and manifest["counts"]["Correction"] == 11
assert manifest["counts"]["SunReport"] == 2 and manifest["counts"]["odbl"] == 2 and manifest["counts"]["ignored_accounts"] == 1
assert manifest["model_check"] == {"Paris": {"agreed": 1, "compared": 1, "contradicted": 1}}, manifest["model_check"]

# Without the salt: the public file only.
d2 = tempfile.mkdtemp()
export(rows, d2, [], NOW, venue=lambda s, c: None)
assert os.listdir(d2) == ["public"]

# Retention: the last 12 weekly snapshots, then the first of each month for 24 months.
mondays = [f"{y}-{mo:02d}-{dd:02d}" for y in (2024, 2025, 2026) for mo in range(1, 13) for dd in (1, 8, 15, 22)]
gone = set(to_delete(mondays + ["junk"], "2026-12-22"))
kept = set(mondays) - gone
assert set(mondays[-12:]) <= kept and "2024-12-01" not in kept and "2025-01-01" in kept and "2025-01-08" not in kept, sorted(kept)
assert len(kept) == 12 + 24 - 3   # three of the last 12 are also firsts

# Restore: the archive back into records/modify operations, typed, the record's fields only.
ops = operations(read_archive(day))
assert len(ops) == 13 and {o["operationType"] for o in ops} == {"create"}
s = next(o["record"] for o in ops if o["record"]["recordType"] == "SunReport")
assert s["fields"]["sunAz"] == {"value": 200, "type": "DOUBLE"} and s["fields"]["month"] == {"value": 9, "type": "INT64"}, s
k = next(o["record"] for o in ops if o["record"]["recordType"] == "Correction")
assert set(k["fields"]) == {"spot", "cell", "fact", "value", "v"} and "creator" not in k["fields"], k
assert {o["operationType"] for o in operations(read_archive(day), replace=True)} == {"forceReplace"}

# Without the secrets the export writes nothing and exits 0; the restore is a dry run unless --apply.
env = {k: v for k, v in os.environ.items() if not k.startswith(("CLOUDKIT_", "CROWD_"))}
out = os.path.join(tempfile.mkdtemp(), "out")
p = subprocess.run([sys.executable, "crowd.py", "export", "--out", out], env=env, capture_output=True, text=True, check=True)
assert "not set" in p.stdout and not os.path.exists(out), p.stdout
p = subprocess.run([sys.executable, "crowd_restore.py", day], env=env, capture_output=True, text=True, check=True)
assert "13 records (11 Correction, 2 SunReport), create" in p.stdout and "Dry run" in p.stdout, p.stdout
p = subprocess.run([sys.executable, "crowd.py", "prune", "--today", "2026-12-22"], input="".join(f"PRE {m}/\n" for m in mondays),
                   capture_output=True, text=True, check=True)
assert set(p.stdout.split()) == gone - {"junk"}

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
