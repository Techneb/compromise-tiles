"""python3 test_sources.py — sources/ tiles keep the published field a run does not build; no network."""
import json, os, tempfile
import make_tiles as mt
from make_tiles import Cell, SourceError, cells_in, path, read

out = tempfile.mkdtemp()
a, b, c, d = cells_in(10.0, 10.0, 10.0, 10.006)[:4]   # outside France and every city feed: OSM's cells
store = {a.key: {"buildings": "OSM", "permits": None},   # whole
         b.key: {"permits": None},                       # lost its building source to a terraces run
         c.key: {},                                      # never built (404)
         d.key: SourceError("store: HTTP 503")}          # the store did not answer
asked = []
def fake(cell): asked.append(cell.key); return store[cell.key]
mt.published_source = fake

# Without a store nothing is read, and a tile holds only what this run built (a local run, as before).
assert mt.published_sources(out, [a]) == {} and asked == []
mt.STORE = "https://store.example/tiles/"

# A terraces run keeps the published building source, writes a new cell, and leaves an unread one alone.
published = mt.published_sources(out, [a, b, c, d])
for x in (a, b, c, d): mt.note_sources(out, x, published, permits="Paris")
assert read(path(out, "sources", a)) == [{"buildings": "OSM", "permits": "Paris"}]
assert read(path(out, "sources", b)) == [{"permits": "Paris"}]
assert read(path(out, "sources", c)) == [{"permits": "Paris"}]
assert not os.path.exists(path(out, "sources", d))   # not written, so the upload keeps the published tile

# A tile already in `out` (the buildings layer ran first in this run) is not asked of the store again.
asked.clear()
assert mt.published_sources(out, [a, b]) == {} and asked == []
mt.note_sources(out, a, {}, buildings="IGN")
assert read(path(out, "sources", a)) == [{"buildings": "IGN", "permits": "Paris"}]

# The sources layer: only the published tile without a building source gets one, and no building tile is written.
out = tempfile.mkdtemp()
mt.near_buildings = lambda cell, items: items
mt.FETCH_BUILDINGS = dict(mt.FETCH_BUILDINGS, OSM=lambda rect: [{"outline": [], "height": 9}])
failures = []
mt.do_sources([a, b, c, d], out, failures)
assert read(path(out, "sources", b)) == [{"permits": None, "buildings": "OSM"}], read(path(out, "sources", b))
assert all(not os.path.exists(path(out, "sources", x)) for x in (a, c, d))   # whole, never built, unread
assert not os.path.isdir(os.path.join(out, "buildings"))
assert [f[:2] for f in failures] == [(d.key, "sources")], failures

# No store: the sources layer fails its cells rather than writing half a tile.
mt.STORE, failures = None, []
mt.do_sources([b], tempfile.mkdtemp(), failures)
assert failures and failures[0][1] == "sources"
print("ok")
