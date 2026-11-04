"""python3 test_smoke.py — the smoke read's verdict on made-up answers, no network."""
import gzip
from smoke import verdict

z = lambda s: gzip.compress(s.encode())
# A cell with no terrace is uploaded as [] (it replaces last week's tile): served as built, it passes (run 41869356307).
assert verdict(z("[]"), "gzip", z("[]")) is None
assert verdict(z('[{"a":1}]'), "gzip", z('[{"a":1}]')) is None
assert verdict(z('[{"a":1}]'), "gzip", gzip.compress(b'[{"a": 1}]')) is None   # same JSON, other bytes
assert verdict(z('[{"a":1}]'), "gzip", z("[]")) == "not the tile built (stale or cut short)"
assert verdict(z("[]"), None, b"[]") == "Content-Encoding None"
assert verdict(z("[]"), "gzip", b"<html>") .startswith("unreadable: ")
print("ok")
