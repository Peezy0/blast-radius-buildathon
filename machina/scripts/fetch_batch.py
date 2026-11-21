#!/usr/bin/env python3
"""Download the files listed in one batch file from the public Builders mirror,
resuming and verifying sizes (same mirror and URL layout as the official get-input.sh)."""
import argparse
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("MACHINA_INPUT_BASE", "https://machina-input.joinbuilders.org")


def fetch(row, dest):
    path, size, _md5, url = row.rstrip("\n").split("\t")[:4]
    size = int(size)
    target = os.path.join(dest, path)
    if os.path.exists(target) and os.path.getsize(target) == size:
        return 0
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if size == 0:
        open(target, "wb").close()
        return 0
    quoted = url  # the manifest's url path is already percent-encoded
    for attempt in range(6):
        try:
            with urllib.request.urlopen(f"{BASE}/{quoted}", timeout=120) as r, open(target + ".part", "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            if os.path.getsize(target + ".part") == size:
                os.replace(target + ".part", target)
                return size
        except Exception as exc:  # retry network errors
            err = exc
        time.sleep(2 * (attempt + 1))
    print(f"failed: {path} ({err if 'err' in locals() else 'size mismatch'})", file=sys.stderr)
    return -1


ap = argparse.ArgumentParser()
ap.add_argument("--batch", required=True)
ap.add_argument("--dest", required=True)
ap.add_argument("--jobs", type=int, default=16)
a = ap.parse_args()
rows = open(a.batch, encoding="utf-8").readlines()
t = time.time()
with ThreadPoolExecutor(a.jobs) as ex:
    res = list(ex.map(lambda r: fetch(r, a.dest), rows))
bad = sum(1 for r in res if r < 0)
got = sum(r for r in res if r > 0)
print(f"batch {os.path.basename(a.batch)}: {len(rows)} files, {got / 1e9:.2f} GB fetched in {time.time() - t:.0f}s, {bad} failed")
sys.exit(1 if bad else 0)
