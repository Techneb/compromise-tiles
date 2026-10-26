#!/usr/bin/env python3
"""A city asked for, added to cities.json as one small area (on-demand.yml).

  python3 on_demand.py plan --city NAME --country CC --cells "lat,lon lat,lon …" [--cities cities.json] --write request.json
  python3 on_demand.py add --request request.json --counts counts.json [--cities cities.json]

plan: checks the inputs (they come from outside), keeps the cells (0.01°, the first the most asked for)
within REACH_M of the first, skips when one of them already lies in a cities.json area, names the city
(with its country code when the name is taken) and writes the box, the cells padded by PAD_M, as a
counts.py entry. add: appends the entry to cities.json when counts.py finds at least GATE going-out cells
in the box; with fewer the area is too thin and nothing is written. Both print key=value lines for
$GITHUB_OUTPUT. Standard library only.
"""
import argparse, json, os, re, sys
from make_tiles import contains, metres, padded

NAME = re.compile(r"[^\W\d_][\w .'’-]{0,59}")   # no comma: tiles.yml's `only` splits on it
COUNTRY = re.compile(r"[A-Z]{2}")
CELL = re.compile(r"(-?\d{1,2}\.\d{2}),(-?\d{1,3}\.\d{2})")
MAX_CELLS = 12
REACH_M = 5000   # cells farther from the first are another place under the same name: left out
PAD_M = 1000     # around the asked cells, so the venues near a meeting point are in
GATE = 3         # ponytail: going-out cells the box needs; tune once a few thin requests are seen


def fail(why): sys.exit(f"refused: {why}")


def cells_of(text):
    out = []
    for c in text.split():
        m = CELL.fullmatch(c)
        if not m: fail(f"cell {c!r} is not lat,lon at 0.01°")
        lat, lon = float(m[1]), float(m[2])
        if not (-85 <= lat <= 85 and -180 <= lon <= 180): fail(f"cell {c!r} is off the map")
        out.append((lat, lon))
    if not 1 <= len(out) <= MAX_CELLS: fail(f"give 1 to {MAX_CELLS} cells")
    return [c for c in out if metres(*out[0], *c) <= REACH_M]


def held(entries, root, lat, lon):
    """The cities.json entry whose area holds the point, if any."""
    for e in entries:
        s, w, n, east = e["box"]
        if not (s <= lat <= n and w <= lon <= east): continue
        if not e.get("boundary"): return e
        with open(os.path.join(root, e["boundary"])) as f: b = json.load(f)
        if contains(b.get("geometry", b), lat, lon): return e
    return None


def plan(city, country, cells_text, cities_path):
    """(request entry or None, message)."""
    if not NAME.fullmatch(city): fail(f"city {city!r}")
    if not COUNTRY.fullmatch(country): fail(f"country {country!r}")
    cells = cells_of(cells_text)
    entries = json.load(open(cities_path))
    root = os.path.dirname(os.path.abspath(cities_path))
    for lat, lon in cells:
        e = held(entries, root, lat, lon)
        if e: return None, f"already in cities.json: {lat},{lon} is in {e['city']} ({e['district']})"
    names = {e["city"] for e in entries}
    name = next((n for n in (city, f"{city} ({country})") if n not in names), None)
    if not name: return None, f"{city} and {city} ({country}) are both taken"
    box = padded((min(c[0] for c in cells) - 0.005, min(c[1] for c in cells) - 0.005,
                  max(c[0] for c in cells) + 0.005, max(c[1] for c in cells) + 0.005), PAD_M)
    return {"city": name, "country": country, "box": [round(x, 4) for x in box]}, f"{name}: {len(cells)} cells"


def add(request, counts, cities_path):
    """(added, message): the entry appended to cities.json past the gate."""
    rows = [r for r in counts.get("cities", []) if r["city"] == request["city"]]
    going = rows[0]["goingOutCells"] if rows else 0
    if going < GATE: return False, f"too thin: {going} going-out cells (gate {GATE})"
    entries = json.load(open(cities_path))
    if any(e["city"] == request["city"] for e in entries): return False, f"{request['city']} is in cities.json already"
    entries.append({"city": request["city"], "district": f"{request['city']} (centre)", "box": request["box"]})
    with open(cities_path, "w") as f: f.write(json.dumps(entries, indent=2) + "\n")   # the file's own format
    return True, f"{request['city']}: {going} going-out cells"


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("step", choices=["plan", "add"])
    a.add_argument("--city", default="")
    a.add_argument("--country", default="")
    a.add_argument("--cells", default="")
    a.add_argument("--cities", default="cities.json")
    a.add_argument("--request", default="request.json")
    a.add_argument("--counts", default="counts.json")
    a.add_argument("--write", default="request.json")
    args = a.parse_args()
    if args.step == "plan":
        entry, why = plan(args.city.strip(), args.country.strip(), args.cells, args.cities)
        if entry: json.dump([entry], open(args.write, "w"), ensure_ascii=False)
        print(f"go={'true' if entry else 'false'}\nname={entry['city'] if entry else ''}\nwhy={why}")
    else:
        done, why = add(json.load(open(args.request))[0], json.load(open(args.counts)), args.cities)
        print(f"added={'true' if done else 'false'}\nwhy={why}")


if __name__ == "__main__":
    main()
