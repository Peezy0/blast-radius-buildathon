#!/usr/bin/env python3
"""Split manifest.tsv into batches of whole top-level folders, each at most --max-gb.

A folder (sources/<host>/<repo> or archives/<file>) is never split, so the
.7z/.extracted twins and assembly/part neighbours stay in the same batch.
Writes batches/NNN.tsv (manifest rows) and prints a summary.
"""
import argparse
import collections
import os

ap = argparse.ArgumentParser()
ap.add_argument("--manifest", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--max-gb", type=float, default=15.0)
a = ap.parse_args()

groups = collections.OrderedDict()
for line in open(a.manifest, encoding="utf-8"):
    if line.startswith("#"):
        continue
    path = line.split("\t", 1)[0]
    parts = path.split("/")
    key = "/".join(parts[:3]) if parts[0] == "sources" else "/".join(parts[:2])
    groups.setdefault(key, []).append(line)

os.makedirs(a.out, exist_ok=True)
limit = a.max_gb * 1e9
batches, cur, size = [], [], 0
for key, rows in groups.items():
    gsize = sum(int(r.split("\t")[1]) for r in rows)
    if cur and size + gsize > limit:
        batches.append((cur, size))
        cur, size = [], 0
    cur += rows
    size += gsize
if cur:
    batches.append((cur, size))
for i, (rows, size) in enumerate(batches):
    with open(os.path.join(a.out, f"{i:03d}.tsv"), "w", encoding="utf-8") as f:
        f.writelines(rows)
    print(f"{i:03d}.tsv files={len(rows)} GB={size / 1e9:.2f}")
