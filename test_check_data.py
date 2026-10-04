"""python3 test_check_data.py — the weekly data check's comparison logic on made-up records; no network."""
import json, os, random
import check_data as cd
from check_data import compare, feed_known, feed_mismatches, flags, licence_source, metadata_url, part_of, rows, summary

ok = lambda count, measure, value, **more: dict({"status": "ok", "count": count, "fetched": count, measure: value}, **more)
tile = lambda count, measure, value, source, **more: dict({"status": "ok", "count": count, measure: value, "source": source}, **more)

# The with-a-height share: a tile built since 2026-10-04 says which footprints are the 15 m guess ("guessed"),
# one built before only wrote 15 m for them (so a measured 15 m counts as none there, as before).
assert cd.with_height([{"height": 15.0, "guessed": True}, {"height": 15.0}, {"height": 20.0}]) == 0.667
assert cd.with_height([{"height": 15.0}, {"height": 15.0}, {"height": 20.0}]) == 0.333
assert cd.with_height([]) == 0.0

# A healthy row against a healthy last run and tile: no flag.
now = ok(40, "named", 0.9, tile=tile(42, "named", 0.88, "Paris"), licence="ODbL")
prev = ok(41, "named", 0.9, tile=tile(42, "named", 0.88, "Paris"), licence="ODbL")
assert flags("permits", now, prev) == []
# The first run: no last run, only the tile to compare with.
assert flags("permits", now, None) == []

# Dead: an error, a timeout, nothing parsed — whatever the tile says.
assert flags("permits", {"status": "dead", "error": "SourceError: Barcelona: no terrace parsed"}, prev) == \
    [("dead", "SourceError: Barcelona: no terrace parsed")]
assert flags("permits", {"status": "timeout", "error": "no answer in 25 min"}, prev)[0][0] == "dead"
# A row never probed (its shard failed) is missing, a row skipped on purpose (Tel Aviv) is nothing.
assert flags("buildings", {"status": "unprobed", "error": "not probed"}, prev) == [("missing", "not probed")]
assert flags("buildings", {"status": "skipped", "error": "answers only from Israel"}, prev) == []

# A count fallen by more than half against the last run, and against the tile when the last run is unusable.
fell = ok(19, "named", 0.9, tile=tile(42, "named", 0.88, "Paris"))
assert [k for k, _ in flags("permits", fell, prev)] == ["count"]
assert "the last run" in flags("permits", fell, prev)[0][1]
assert "the published tile" in flags("permits", fell, {"status": "dead"})[0][1]
assert flags("permits", ok(21, "named", 0.9, tile=tile(42, "named", 0.88, "Paris")), prev) == []   # exactly half is not a fall
assert flags("permits", ok(0, "named", 0.0, tile=tile(0, "named", 0.0, "Paris")), ok(0, "named", 0.0)) == []  # a static empty cell
# Zero where there were entries is a fall, not a share flag on top of it.
assert [k for k, _ in flags("permits", ok(0, "named", 0.0, tile=tile(42, "named", 0.88, "Paris")), prev)] == ["count"]

# The named share fell (a renamed name column): the count is fine, the share is not.
unnamed = ok(40, "named", 0.1, tile=tile(42, "named", 0.88, "Paris"))
assert [k for k, _ in flags("permits", unnamed, prev)] == ["share"]
assert flags("permits", unnamed, prev)[0][1].startswith("named 10%")
# Buildings: the with-a-height share, against the tile too.
assert [k for k, _ in flags("buildings", ok(300, "height", 0.2, tile=tile(310, "height", 0.95, "Berlin")), None)] == ["share"]
# A row that never names (Barcelona) has nothing to fall from.
assert flags("permits", ok(40, "named", 0.0, tile=tile(42, "named", 0.0, "Barcelona")), ok(40, "named", 0.0)) == []

# A combine row's probe holds only the city's heights: compared with the tile's with-height count, never its footprints.
combine = {"key": "1,1", "combine": True}
london = ok(120, "height", 1.0, tile=tile(400, "height", 0.35, "London+OSM", with_height=140))
assert flags("buildings", london, None, combine) == []
assert [k for k, _ in flags("buildings", ok(60, "height", 1.0, tile=london["tile"]), None, combine)] == ["count"]

# The licence string changed; an unknown one (the metadata API down) does not flag.
assert flags("permits", dict(now, licence="CC BY 4.0"), prev) == [("licence", "was “ODbL”, now “CC BY 4.0”")]
assert flags("permits", dict(now, licence=None), prev) == []
assert flags("permits", now, dict(prev, licence=None)) == []

# The published cell no longer credits the row: the build fell back.
fallen = dict(now, tile=tile(42, "named", 0.88, None))
assert flags("permits", fallen, prev) == []   # no source read: nothing to compare
fallen = ok(300, "height", 0.9, tile=tile(310, "height", 0.9, "OSM"))
assert flags("buildings", fallen, ok(300, "height", 0.9, tile=tile(310, "height", 0.9, "Berlin"))) == \
    [("source", "the published cell credits OSM, last run Berlin")]

# compare(): every row of make_tiles.py gets a verdict; one no shard reported is missing.
ids = [rid for rid, _, _ in rows()]
assert ids[0] == "permits/Paris" and "buildings/Berlin" in ids and len(ids) == len(set(ids))
records = {rid: ok(10, "named" if rid.startswith("permits") else "height", 0.5, tile={"status": "HTTP 404"}) for rid in ids[1:]}
verdicts = compare(records, {"rows": {}}, {})
assert verdicts["permits/Paris"] == [("missing", "not probed: its shard did not finish")]
assert all(v == [] for rid, v in verdicts.items() if rid != "permits/Paris")

# Shards: every row in exactly one part, the Catastro run of rows spread out.
parts = [part_of(ids, f"{k}/6") for k in range(1, 7)]
assert sorted(sum(parts, [])) == sorted(ids) and max(len(p) for p in parts) - min(len(p) for p in parts) <= 1
catastro = [rid for rid, _, b in rows() if b.get("fetch") is cd.mt.catastro]
assert len(catastro) == 14 and all(any(r in p for r in catastro) for p in parts)

# Every row has a probe cell in probes.json, and no probe outlives its row; a skipped row says why.
probes = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "probes.json")))
assert set(probes) == set(ids), set(probes) ^ set(ids)
assert all(p.get("key") and len(p["key"].split(",")) == 2 for p in probes.values())
assert probes["buildings/Tel Aviv"].get("skip")
for rid, layer, row in rows():   # a probe cell lies inside its row's box
    ky, kx = map(int, probes[rid]["key"].split(","))
    assert row["lat"][0] <= ky / 500 <= row["lat"][1] and row["lon"][0] <= kx / 500 <= row["lon"][1], rid
# A combine row is marked, so its count is compared with the tile's with-height count.
assert all(bool(probes[rid].get("combine")) == bool(b.get("combine")) for rid, layer, b in rows() if layer == "buildings")

# Licence endpoints: derived from a permit row's shape, overridden (or disabled) by the probe entry.
paris = next(c for c in cd.mt.PERMIT_CITIES if c["city"] == "Paris")
assert metadata_url("permits", paris) == ("opendatasoft", "https://parisdata.opendatasoft.com/api/explore/v2.1/catalog/datasets/terrasses-autorisations")
assert metadata_url("permits", next(c for c in cd.mt.PERMIT_CITIES if c["city"] == "Camden"))[0] == "socrata"
assert metadata_url("permits", next(c for c in cd.mt.PERMIT_CITIES if c["city"] == "Barcelona")) == \
    ("ckan", "https://opendata-ajuntament.barcelona.cat/data/api/3/action/package_show?id=terrasses-comercos-vigents")
assert metadata_url("permits", next(c for c in cd.mt.PERMIT_CITIES if c["city"] == "Madrid")) is None
assert metadata_url("buildings", {}) is None
assert licence_source("permits", paris, {"licence": None}) is None
assert licence_source("permits", paris, {"licence": {"kind": "wfs", "url": "https://x/?"}}) == ("wfs", "https://x/?")
assert licence_source("permits", paris, {}) == metadata_url("permits", paris)

# The feed check (check_feed.py's rule): a cell whose tile count differs from the feed's is a mismatch; a tile the
# store lacks agrees with 0 and is listed as missing.
feed = {"updated": "2026-10-04", "areas": [{"city": "A", "name": "A", "box": [0.0, 0.0, 0.01, 0.01], "terracesKnown": 1}],
        "cells": [[1, 1, 5, 30, 25, 0, 0, "", 12, 3], [2, 2, 0, 0, 0, -1, -1, "", 0, 0], [3, 3, 7, 40, 40, 0, 0, "", 0, 0]],
        "venueCells": [[0, 0, 100, 10, 0]]}
store = {"1,1": (30, 5), "2,2": (None, None), "3,3": (40, 6)}
checked, bad, missing = feed_mismatches(feed, lambda k: store[k], per_area=3, seed=1)
assert checked == 3 and missing == ["2,2"] and [m["key"] for m in bad] == ["3,3"] and bad[0]["feed"] == [40, 7] and bad[0]["tiles"] == [40, 6]
# The same seed samples the same cells; a different one may not.
random.seed(1); first = random.sample(range(100), 3); random.seed(1); assert random.sample(range(100), 3) == first
# terracesKnown: the venue tile spreads 100 venues over 100 cells (9 near each cell); 5 and 7 terraces pass (≥ 5, ≥ 30 % × 9), 0 does not.
assert feed_known(feed) == [("A / A", 1, 2)]

# The report: flags first, one line per row of make_tiles.py, the feed's verdict last.
run = {"run": "2026-10-05", "rows": {"permits/Paris": dict(now, key="24430,1174")}}
previous = {"run": "2026-09-28", "rows": {"permits/Paris": prev}}
text = summary(run, run["rows"], previous, probes, {"permits/Barcelona": [("dead", "SourceError: no terrace parsed")]},
               {"updated": "2026-10-04", "checked": 10, "mismatches": [{"key": "1,1", "area": "A / A", "feed": [1, 2], "tiles": [1, 3]}], "missing": [], "known": []})
assert "**2 flags**" in text and "- `permits/Barcelona` **dead**: SourceError: no terrace parsed" in text
assert "| `permits/Paris` | `24430,1174` | ok | 40 / 41 / 42 | 90% / 90% / 88% | ODbL |  |" in text, text
assert "| `permits/Barcelona` |" in text and text.count("\n| `") == len(ids)
assert "1 mismatches" in text and "`1,1` (A / A): feed buildings 1 / tile 1; feed terraces 2 / tile 3" in text
clean = summary(run, run["rows"], previous, probes, {}, None)
assert "**Clean**" in text.replace(text, clean) and "flag" not in clean.split("\n")[2]
print("ok")
