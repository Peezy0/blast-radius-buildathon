"""Store each file record of a coverage map as an embedded JSON string, so the
runtime only decodes the files a patch touches (same content, less CPU).
Usage: python lazy_map.py IN.json.gz OUT.json.gz"""
import gzip, json, sys
m = json.load(gzip.open(sys.argv[1], "rt", encoding="utf-8"))
m["files"] = {k: v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))
              for k, v in m["files"].items()}
with gzip.open(sys.argv[2], "wt", encoding="utf-8", compresslevel=9) as f:
    json.dump(m, f, separators=(",", ":"))
