# Tile formats

The rows of each layer the README lists, except `buildings-v2/` ([BUILDINGS.md](BUILDINGS.md)). Keys are `int(lat*500),int(lon*500)` for the ~200 m cells and `int(lat*25),int(lon*25)` for the ~4 km ones.

## streets/

Each tile is an array of rows. A street row is `[kind, y0, x0, dy1, dx1, …]`
(kind 1 main road, 2 street, 3 pedestrian), or `[kind, "name", y0, x0, …]`
where the OpenStreetMap way has a name (since 2026-10-08): one way cut at the
cell's edges, coordinates in 1e-5° steps, the first from the key's corner
(key / 500), each next from the one before.

## numbers/

House-number ranges, on the same cells, keys and paths as `streets/` (since
2026-10-10). Each tile is an array of `["name", lowest, highest]`, sorted by
name (by Unicode code point):

- `name` is written exactly as on the named rows of the same cell's
  `streets/` tile.
- `lowest` and `highest` are integers, the smallest and largest house number
  found on that street inside the cell. A number is OpenStreetMap's
  `addr:housenumber` on a node, a building or along an `addr:interpolation`
  way, whose `addr:street` (else the name of the `associatedStreet` relation
  it is a house of) equals the name once case, accents and apostrophes are
  set aside; only its leading digits count ("12bis" and "12 ter" are 12,
  "12-14" is 12, "12;14" is both), and one not starting with a digit is left
  out. The address point (a building's mean vertex) must lie in the cell.
- A street is listed only where at least two distinct numbers are found. A
  range is that cell's alone: a street crossing several cells is listed in
  each. A cell with no range has no file.

## places/

Each tile is an array of `{"name", "kind", "coordinate", "admin_level"?}`:

- `kind` is OpenStreetMap's `place` tag: `suburb`, `quarter` or
  `neighbourhood`.
- `admin_level` (an integer, OpenStreetMap's) is written only when the name
  comes from or matches an administrative boundary relation; absent
  otherwise. In Paris, 9 is an arrondissement and 10 one of the 80 quartiers
  administratifs. A place matches a boundary when it is the relation itself,
  the relation's `label` node, its `admin_centre` node under a name one
  holds the other ("17e Arrondissement" in "Paris 17e Arrondissement"), or
  lies inside it under the same name once "Quartier" and the articles are
  set aside ("Batignolles", "Quartier des Batignolles"). The deepest level
  wins.
- A boundary at admin_level 9 or 10 that no place matches is written as a
  place of its own at its ring's mean vertex, with its own `place` tag, or
  else `suburb` at 9 and `quarter` at 10. That is how most of Paris's
  quartiers come in: OpenStreetMap maps them as boundaries, not as places.

Added on 2026-10-06 and backward compatible: a reader of the first format
ignores the new key, and every `kind` is still one of the three. A tile
built before carries no `admin_level`.

## boundaries/

Neighbourhood outlines, on the same 1/25° cells and keys as `places/` (since
2026-10-10). Each tile is an array of `{"name", "level", "source", "polygons"}`:

- Each outline is written once and whole, in the cell of its label point, not
  cut at the cell's edges. The label point is the `places/` place of the same
  name lying inside the outline (compared as `places/` compares names: case,
  accents, punctuation, a leading "Quartier" and articles set aside), and
  `name` is then written as in `places/`; else it is a point inside the
  outline, and `name` is the source's (a name in capitals only is put in title
  case).
- `level` is the source's own word for the unit (`"quartier"`, `"Ortsteil"`,
  `"wijk"`), or an integer, OpenStreetMap's `admin_level`, or its `place` tag
  where the outline is a place polygon.
- `source` is the key under which `sources/` names the source (below).
- `polygons` is an array of polygons, each an array of rings: the outer ring
  (counter-clockwise) first, then its holes (clockwise). Most outlines are one
  polygon of one ring. A ring is `[y0, x0, dy1, dx1, …]`, as in `buildings-v2/`:
  integers in 1e-5° steps, the first vertex from the key's corner (key / 25,
  so it may be negative, or beyond the cell, for an outline reaching out of
  it), each next from the one before; the ring is open (its first vertex is
  not repeated).
- Outlines are simplified at 5 m over each source's whole set, so an edge two
  neighbours share is simplified once and stays shared, then rounded on the
  absolute 1e-5° grid, so shared vertices stay equal.
- A cell holding no label point has no file. The outlines are of one source
  per area (where a city has two, as Paris has, each covers its own part).

A `sources/` tile names, under `"boundaries"`, the source of the outline
holding the cell's centre.

## transit/

Public-transport lines, on the same 1/25° cells and keys as `places/` (since
2026-10-13; metro, tram, rail, bus and cable car), built on dispatch only, for the
areas flagged `"streets": true`. Each tile is an array of
`{"mode", "ref", "colour"?, "network"?, "operator"?, "stops", "points", "shape"}`:

- `mode` is OpenStreetMap's `route`: `subway`, `tram`, `train`,
  `light_rail`, `bus`, `trolleybus` or `aerialway`; `ref` the route's (else its route master's), `colour` the
  same way, as `#RRGGBB`, absent when neither has a valid one.
  `network` and `operator` the same way, absent when neither has one.
- `stops` are the stop names in the route's order (`""` for an unnamed one);
  `points` one per stop, `[y0, x0, dy1, dx1, …]` as in `boundaries/`:
  1e-5° steps, the first from the key's corner (key / 25), each next from
  the one before.
- `shape` is the track: the route's other way members chained in member
  order into runs (a new run where two ways do not meet), each simplified
  at 10 m (Douglas–Peucker) and coded as `points`, each run from the key's
  corner. `[]` when the route has no track ways.
- A line is written whole in every cell one of its stops falls in. A route's
  stops are its members with a stop role, else its platforms; two within
  60 m are one. A route with no ref, a long-distance train (`service`
  high_speed, long_distance, national, international, night, tourism, or a
  network starting VIA Rail, Amtrak, TGV, Ouigo, Intercités, Eurostar or
  Noctilien,
  or a ref listing train numbers, or a train's ref holding a number of four
  digits or more but where the network numbers its lines so, as the Dutch
  Sprinters' "4000"), a
  train whose ref starts with a high-speed brand (Frecciarossa,
  Frecciargento, Frecciabianca, Italo, Railjet, Nightjet, EuroCity,
  Intercity), a
  coach (a network or operator starting Flixbus, FlixTrain, Flibco,
  RegioJet, Autobuses de España, Rede Nacional de Expressos; a bus whose
  stops reach more than 60 km from its first), a Co.Tra.L. bus (Rome's
  regional coaches, whose refs are route codes), a night
  bus or tram (a ref of "N" and a number; Prague's trams 91–99 and buses
  9xx; Lisbon's Carris buses 201–210), a tourist line (Prague's trams 41
  and 42, Amsterdam's museum tram, Barcelona Bus Turístic, a route tagged `tourism=yes`, a network or
  operator holding Big Bus, Open Tour, Sightseeing, Hop-on, Tootbus,
  Museum or Heritage, or a network of just "tourism") or
  under two stops is left out, and so is one whose stop names all belong to
  a longer route of the same mode and ref (the other direction, a short
  working). A ref's bracketed service pattern is dropped first ("C-2
  (CIVIS)" is "C-2"). A cell no line stops in has no file.
