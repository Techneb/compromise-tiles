# buildings-v2/

The store's buildings layer: `tiles/buildings-v2/<key>.json` per ~200 m cell,
keyed `int(lat*500),int(lon*500)`, gzipped at rest. The build also writes
`buildings/<key>.json`, the same footprints as objects; it stays local
(the build's resume, read by `coverage.py`) and is not uploaded since
2026-10-08.

## Format

```
[[height, y0, x0, dy1, dx1, dy2, dx2, …], …]
```

- `height`: metres in 0.5 m steps, an integer when whole (`22`, `22.5`),
  where a source measured or tagged it. Where none did, the height is a
  guess, flagged `"guessed": true` in buildings/. Since 2026-11-20 so is
  a single floor on an outline of 300 m² or more in a tile whose other
  heights have a median of 9 m or more:
  - a quarter step (`9.25`, `17.75`) since 2026-11-19: the local estimate,
    the median of the measured heights in the tile, or in the block the
    build fetched it with, once either holds 10 of them;
  - `null`, the 15 m default, where neither does.

  A reader tells a guess by that alone: `null`, or a height times 4 that is
  odd (`guessed_height()`). A reader written before 2026-11-19 takes an
  estimate as a measured height, and still reads `null` as 15 m.
- Coordinates are in 1e-5° steps, as in streets/. `y0, x0` is the first
  vertex from the key's corner (`ky * 200`, `kx * 200`), and each pair after
  it is the step from the vertex before. A negative key's cell lies below
  that corner, so its offsets are negative.
- The ring is open: its closing vertex is not repeated. Vertices that round
  to the same step are merged, and a ring left with fewer than 3 corners is
  dropped.
- Buildings keep buildings/'s order. `expand()` in `test_make_tiles.py`
  decodes one.

A tile holds every footprint within `BUILDING_REACH` (200 m) of its cell.

## Which cells have a tile

Since 2026-10-13, only the cells within `SHADE_MARGIN` (100 m) of a venue of
the extract (bar, pub, beer garden, café or restaurant) or of a terrace of
terraces-v2/, and, since 2026-10-15, every cell of a commune (OSM admin_level 8)
with fewer than 5 such venues. The weekly terraces-v2 run builds the buildings of the cells a
new venue or terrace reaches. A dispatch of `tiles.yml` with
`prune_buildings` (dry run first) deletes the buildings-v2/ tiles of a
city's own cells that none reaches, after the upload. `coverage.json` keeps
a pruned cell's last count, and the data check takes a missing buildings
tile as agreeing with any count.

The tiles committed under `tiles/buildings/` (built off GitHub) are
converted by `buildings_v2.py`, with the generator's own writer.
