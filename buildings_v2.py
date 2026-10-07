"""buildings-v2/ for the buildings tiles built off GitHub and committed under tiles/buildings/ (Tel Aviv,
Jerusalem, Spain's cadastre), which repo_upload publishes: the generator's own writer, compact_buildings, so the
two paths cannot drift (BUILDINGS.md).

    python3 buildings_v2.py tiles/buildings out/buildings-v2
"""
import os, sys
from make_tiles import compact_buildings, read, write

def convert(src, dst):
    """Each <key>.json under src written to dst in buildings-v2/'s format; a tile that does not read is
    skipped, never written empty. Returns how many were written."""
    written = 0
    for name in sorted(os.listdir(src)):
        items = read(os.path.join(src, name)) if name.endswith(".json") else None
        if items is None: continue
        write(os.path.join(dst, name), compact_buildings(items, name[:-5]))
        written += 1
    return written

if __name__ == "__main__":
    print(convert(sys.argv[1], sys.argv[2]), "buildings-v2/ tiles written")
