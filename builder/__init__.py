"""make_tiles.py's pieces, one module per concern (2026-11-20): make_tiles.py is the run and the command."""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the repo: boundaries/, nsi_brands.json

# Names a function rebinds (`global`): read and written as module.name outside their own module, and not
# copied into make_tiles (a copy would go stale).
REBOUND = {"_osm", "STORE", "LISTED", "UNSHADED", "_regions", "_NSI"}

def patch(**values):
    """Sets each name in make_tiles and in every builder module that holds it, so one fake reaches every caller,
    as one assignment did when make_tiles was one module (the tests' fakes, check_data's OSM reader)."""
    modules = [m for n, m in list(sys.modules.items()) if n == "make_tiles" or n.startswith("builder.")]
    for name, value in values.items():
        holders = [m for m in modules if name in vars(m)]
        if not holders: raise AttributeError(f"no make_tiles module holds {name}")
        for m in holders: setattr(m, name, value)
