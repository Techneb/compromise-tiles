# Shrinking buildings/

On 2026-10-06 the tile store held 9.3 GB of R2's free 10 GB. buildings/
alone was 9.1 GB in 572,448 files, about 16 KB each gzipped. This note
measures what each option saves on real published tiles, says which ones
`make_tiles.py` now implements, and makes a recommendation.

## How it was measured

Nine published tiles were read from `tiles.alephb.uk` (buildings/, terraces-v2/,
venues/), three per city type:

- **dense Paris:** Opéra `24435,1165`, Marais `24429,1179`, Oberkampf `24432,1187`, all from IGN;
- **mid-size French cities:** Rouen `24720,547`, Tours `23697,342`, Dijon `23661,2520`, all from IGN;
- **US cities:** New York Midtown `20377,-36992`, Chicago Loop `20941,-43815`, San Francisco Mission `18880,-61209`, all from the city feeds.

Each option was applied to the tile's JSON, which was then compressed as the
upload does it (`gzip -9n`, or brotli for the brotli rows). The sizes below
are KB at rest.

The chosen format was also checked on the 7,099 tiles committed under
`tiles/buildings/` (mostly Tel Aviv, Jerusalem and Spain's cadastre). There
it saves **68.2%**, 216 MB down to 69 MB, the same as on the nine tiles.

| Option | Paris Opéra | Paris Marais | Paris Oberkampf | Rouen | Tours | Dijon | New York Midtown | Chicago Loop | San Francisco Mission | All 9 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| now (gzip -9) | 26.5 | 52.5 | 41.2 | 72.5 | 39.6 | 65.3 | 15.7 | 5.7 | 78.6 | 398 KB |
| closing vertex dropped | 25.4 | 50.0 | 39.3 | 69.2 | 37.6 | 61.8 | 14.8 | 5.3 | 76.3 | 380 KB (-5%) |
| heights in 0.5 m steps | 26.0 | 51.4 | 40.4 | 70.8 | 38.7 | 63.8 | 15.6 | 5.7 | 77.5 | 390 KB (-2%) |
| heights in 1 m steps | 25.9 | 51.1 | 40.1 | 70.3 | 38.3 | 63.4 | 15.4 | 5.7 | 77.1 | 387 KB (-3%) |
| simplified, 0.5 m tolerance | 18.5 | 36.8 | 32.0 | 51.2 | 32.1 | 52.5 | 10.4 | 4.9 | 54.8 | 293 KB (-26%) |
| simplified, 1 m | 16.9 | 33.9 | 29.3 | 45.3 | 29.0 | 47.3 | 9.8 | 4.8 | 38.3 | 255 KB (-36%) |
| simplified, 2 m | 15.5 | 30.0 | 25.2 | 40.6 | 25.8 | 41.9 | 9.0 | 4.5 | 27.6 | 220 KB (-45%) |
| integer deltas, 1e-6° (lossless) | 15.2 | 30.0 | 23.5 | 41.0 | 22.5 | 34.1 | 8.4 | 3.2 | 32.2 | 210 KB (-47%) |
| integer deltas, 1e-5° | 9.3 | 18.2 | 14.8 | 25.2 | 14.4 | 21.7 | 5.5 | 2.2 | 17.7 | 129 KB (-68%) |
| **buildings-v2/**: 1e-5° deltas, 0.5 m heights | 9.1 | 17.7 | 14.4 | 24.4 | 13.9 | 21.1 | 5.4 | 2.2 | 17.3 | **125 KB (-68%)** |
| buildings-v2/, then simplified 0.5 m | 7.5 | 14.4 | 12.2 | 19.6 | 12.3 | 18.2 | 4.0 | 2.0 | 14.4 | 105 KB (-74%) |
| brotli 11, today's JSON | 19.0 | 38.1 | 29.4 | 52.1 | 28.5 | 43.2 | 10.8 | 4.2 | 50.6 | 276 KB (-31%) |
| buildings-v2/ + brotli 11 | 8.2 | 16.2 | 13.2 | 22.2 | 12.7 | 19.3 | 4.9 | 2.0 | 15.8 | 115 KB (-71%) |
| only footprints near a terrace in the cell | 14.6 | 29.1 | 29.6 | 47.0 | 23.1 | 49.4 | 7.3 | 0.0 | 52.8 | 253 KB (-36%) |
| each footprint in one tile only | 2.2 | 4.1 | 3.8 | 7.1 | 3.1 | 6.6 | 0.7 | 0.4 | 7.9 | 36 KB (-91%) |
| footprints in the tile | 390 | 836 | 661 | 1,373 | 852 | 1,162 | 232 | 129 | 756 | 6,391 |

## What each option does

- **A coarser simplification tolerance.** Footprints are not simplified today:
  every vertex the source gives is written, at 6 decimals. Douglas–Peucker at
  0.5 m saves 26%, at 2 m 45% (San Francisco's curved outlines lose the
  most). Each ring is simplified on its own, though. Two buildings that share
  a wall can each drop a different vertex of it, which opens a gap or an
  overlap of up to the tolerance between them. A shadow ray tested through
  such a gap would read "sun" in the middle of a terrace row. The error is
  small but not zero, so this is **not implemented**.
- **Quantised heights.** On its own this saves 2% in 0.5 m steps and 3% in
  1 m steps. Heights are already written to 0.1 m and repeat a lot, so gzip
  gains little. The 0.5 m step (an error of 0.25 m at most) is part of
  buildings-v2/.
- **Delta-encoded integer coordinates, like streets/.** This is the big
  lever: −47% losslessly at 1e-6°, −68% at 1e-5°. Small integers repeat far
  more than `{"latitude":48.869664,"longitude":2.333743}`, and gzip finds the
  repeats.
  - At 1e-5° a vertex moves by 0.56 m at most (half a step: 1.1 m north–south,
    0.7 m east–west in Paris). That is finer than BD TOPO's own planimetric
    accuracy, about 1 m.
  - Absolute coordinates are rounded before the deltas are taken, so walls
    two buildings share stay shared: no gaps open.
  - A footprint smaller than one step collapses. That is 0.3% of the
    committed footprints (Palma's cadastre slivers) and 0.002% of their area.
    It is dropped.
- **Dropping footprints far from any venue or terrace.** On the nine tiles,
  keeping only the footprints within 180 m (the BUILDING_REACH reasoning) of
  a terrace inside the cell saves 36%. Counting the cell's venues too saves
  28%. Across the whole store (coverage.json):
  - 68% of the building cells have no terrace within 400 m;
  - 45% have neither a terrace within 400 m nor a venue in their 3×3 block.
    They hold 27% of the footprints, and could go entirely.

  **The sunny filter would not stay right**, for three reasons:
  1. Where a cell has too few terraces, the filter judges the side of the
     street, so it needs the buildings without any terrace (coverage.py).
  2. Terraces and venues are rebuilt weekly, buildings yearly. A terrace that
     opens after the buildings run would have no shadows until January.
  3. coverage.json's `passing` (20 footprints in the cell) would drop the
     cells that lose their buildings, and the app would switch the filter off
     there.

  **Not implemented.**
- **gzip and brotli.** The upload already uses `gzip -9`; level 6 would be 6%
  bigger.
  - zopfli writes ordinary gzip, so the phone reads it unchanged. It saves
    16% on today's JSON, but takes 4–6 s per dense tile against 0.09 s for
    gzip. A city job of tens of thousands of tiles would run past its
    6 hours. On buildings-v2/ it saves only 7%.
  - Brotli 11 saves 31% on today's JSON and 9% more on buildings-v2/, at
    about 0.17 s per buildings-v2/ tile.
  - Levels 9 and below save nothing over gzip on today's JSON: brotli's
    gain is in level 11's bigger context modelling.
- **Each footprint in one tile only.** This is not one of the options asked
  about, but it is the largest. A tile holds every footprint within 200 m of
  its cell, so each footprint is stored about 14 times: only 7% of a tile's
  footprints have their centre in the cell. Storing each footprint in its
  own cell would cut 91%. The app would then read the 3×3 block around a
  cell, nine requests instead of one, and that needs a reader change and
  thought about request counts. It is left for later.

## What iOS can decompress natively

Nothing here was checked on a device: there is no Apple toolchain in this
environment.

- **URLSession** decodes `Content-Encoding` itself: `gzip` and `deflate`,
  plus `br` over HTTPS since iOS 11. It advertises them in `Accept-Encoding`,
  so a brotli tile would reach the app's code already decoded, as gzip
  tiles do today. zstd is not among the encodings it advertises.
- **The Compression framework**, for decoding bytes in the app:
  - `COMPRESSION_ZLIB` (raw deflate, no gzip header);
  - `COMPRESSION_LZFSE`, `COMPRESSION_LZ4`, `COMPRESSION_LZMA`;
  - `COMPRESSION_BROTLI`, iOS 15 and later.

  It is not needed while the store sets `Content-Encoding`.

## Implemented: buildings-v2/, the store's only buildings layer

`make_tiles.py` writes `buildings-v2/<key>.json` beside `buildings/<key>.json`,
from the same items. Since 2026-10-08 only buildings-v2/ is uploaded:
buildings/ stays in the run (the build's resume), and the old layer was
deleted from the store (`delete-superseded.yml`) once every key had its
buildings-v2/ tile and a sample of 300 matched. Coverage counts and the
weekly data check read buildings-v2/. Format:

```
[[height, y0, x0, dy1, dx1, dy2, dx2, …], …]
```

- `height`: metres in 0.5 m steps, an integer when whole (`22`, `22.5`). It
  is `null` for a footprint whose source gave no height, the one buildings/
  writes as `15` with `"guessed": true`. The reader applies its own 15 m.
- Coordinates are in 1e-5° steps, as in streets/. `y0, x0` is the first
  vertex from the key's corner (`ky * 200`, `kx * 200`), and each pair after
  it is the step from the vertex before. As in streets/, a negative key's
  cell lies below that corner, so its offsets are negative.
- The ring is open: its closing vertex is not repeated. Vertices that round
  to the same step are merged, and a ring left with fewer than 3 corners is
  dropped.
- Buildings keep buildings/'s order, so a reader can check one against the
  other. A reader decodes a building with the loop `expand()` in
  `test_make_tiles.py`.

`storage.py` (and `stats.json`) count the new layer at the fine scale. The
upload compares it with the store by ETag like every other layer.

## Recommendation

1. **Ship buildings-v2/: −68%, near-lossless.**
   - The app reads `buildings-v2/<key>.json` and falls back to `buildings/`
     on a 404.
   - Run the yearly buildings build (or dispatch it city by city) with
     `buildings_v2`.
   - Once the app versions that read only buildings/ are gone, stop
     uploading buildings/ and delete the prefix. buildings/ goes from 9.1 GB
     to about 2.9 GB, and the store to about 3.1 GB.
2. **Then, maybe, brotli 11 on buildings-v2/: about 9% more (≈0.26 GB).** It
   is not implemented, because it depends on two things this repository
   cannot check:
   - Cloudflare must pass an object stored with `Content-Encoding: br`
     through to clients that accept br. It must also decode it for clients
     that don't: `check_data.py`'s `fetch_store` reads buildings/ and only
     knows gzip, and Python's standard library has no brotli. Cloudflare
     decodes gzip that way today (a request without `Accept-Encoding` gets
     plain JSON), but for br this is untested. Upload one test object and
     request it with and without `Accept-Encoding: br` first.
   - The runner needs `brotli` (apt), and the upload step needs a second
     `--content-encoding`.
3. **Do not drop footprints far from venues or terraces, and do not simplify,
   at least for now** (see above). If space runs short again, store each
   footprint in one tile only: that is the next 90%.

## Risks

- **Two layers during the transition.** buildings/ and buildings-v2/
  together come to about 12 GB, roughly 2 GB over the free tier, about $0.03
  a month at $0.015/GB-month. Building v2 city by city spreads that out.
- **A second reader.** The app must decode deltas and treat `null` as the
  guess. A decoding bug would misplace every footprint. The test's
  `expand()` is the reference, and comparing a decoded v2 tile with its
  buildings/ twin in the app's tests would catch one.
- **0.56 m rounding.** For a shadow ray of 150 m this is negligible. A
  footprint narrower than about 1 m disappears, and with it a slender
  structure's shadow (Palma: 0.002% of the area).
- **coverage.py and check_data.py read buildings/ only** (counts.py reads
  them through the coverage feed). Once buildings/ is retired they must read
  buildings-v2/: the footprint count becomes `len(tile)`, the guessed count
  the `null` heights.
