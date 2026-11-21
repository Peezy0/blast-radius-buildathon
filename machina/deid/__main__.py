"""CLI: discover -> apply -> verify. Private state lives in --private (never in output or git)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import discover as disc
from . import swfile
from .formats import classify
from .process import process_bytes
from .pseudo import Registry

_REG = None


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".get-input")
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if p.is_file() and not p.is_symlink():
                yield p


def _discover_one(path_str: str, rel: str):
    p = Path(path_str)
    try:
        data = p.read_bytes()
    except OSError:
        return rel, []
    kind = classify(data[:8192], p.name, len(data))
    found = disc.from_path(rel)
    try:
        found += disc.from_file(data, kind)
    except Exception:
        pass
    return rel, found


def cmd_discover(a):
    reg = Registry(a.private)
    added = Counter()
    if a.manifest:
        for line in open(a.manifest, encoding="utf-8"):
            if line.startswith("#"):
                continue
            for v, k in disc.from_path(line.split("\t", 1)[0]):
                added[k] += reg.add(v, k, "manifest", disc.context_only(v, k))
    if a.input:
        root = Path(a.input)
        jobs = [(str(p), p.relative_to(root).as_posix()) for p in iter_files(root)]
        with ProcessPoolExecutor(a.jobs) as ex:
            for rel, found in ex.map(_discover_one, *zip(*jobs), chunksize=4):
                for v, k in found:
                    added[k] += reg.add(v, k, rel, disc.context_only(v, k))
    reg.save()
    print(json.dumps({"identities_total": len(reg.items), "added_by_kind": added}, indent=1))


def _init(private):
    global _REG
    _REG = Registry(private)


def _apply_one(src: str, dst: str):
    data = Path(src).read_bytes()
    new, res = process_bytes(data, Path(src).name, _REG)
    d = Path(dst)
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_bytes(new)
    return {"kind": res.kind, "changed": res.changed, "notes": res.notes, "in": len(data), "out": len(new),
            "md5": hashlib.md5(data).hexdigest(), "dst": dst}


def cmd_apply(a):
    reg = Registry(a.private)
    root, out = Path(a.input), Path(a.output)
    pairs, seen = [], {}
    for p in iter_files(root):
        rel = p.relative_to(root).as_posix()
        newrel = "/".join(reg.sub_str(part) for part in rel.split("/"))
        if newrel in seen and seen[newrel] != rel:
            sys.exit(f"path collision after renaming: {newrel}")
        seen[newrel] = rel
        pairs.append((str(p), str(out / newrel)))
    t = time.time()
    stats, notes, kinds = Counter(), [], Counter()
    with ProcessPoolExecutor(a.jobs, initializer=_init, initargs=(a.private,)) as ex:
        for r in ex.map(_apply_one, *zip(*pairs), chunksize=2):
            kinds[r["kind"]] += 1
            stats["files"] += 1
            stats["changed"] += r["changed"]
            stats["bytes_in"] += r["in"]
            if r["notes"]:
                notes.append({"path": os.path.relpath(r["dst"], out), "notes": r["notes"]})
    secs = time.time() - t
    report = {"files": stats["files"], "changed": stats["changed"], "bytes_in": stats["bytes_in"],
              "seconds": round(secs, 1), "MB_per_s": round(stats["bytes_in"] / 1e6 / max(secs, .01), 1),
              "kinds": kinds, "exceptions": notes}
    Path(a.private, "apply-report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "exceptions"}, indent=1),
          f"\nexceptions: {len(notes)} (details in private apply-report.json)")


def cmd_verify(a):
    """Count remaining original identities (plain, UTF-16, inside SolidWorks sections)
    and check SolidWorks/ZIP structure in the output."""
    import io
    import zipfile
    reg = Registry(a.private)
    root = Path(a.output)
    leaks, struct_bad, n = Counter(), [], 0
    for p in iter_files(root):
        n += 1
        rel = p.relative_to(root).as_posix()
        if reg.sub_str(rel) != rel:
            leaks["path"] += 1
        data = p.read_bytes()
        kind = classify(data[:8192], p.name, len(data))
        c = reg.count_bytes(data)
        if kind == "swx":
            chk = swfile.check(data)
            if chk["valid_sections"] == 0:
                struct_bad.append((rel, "no valid sections"))
            for _nm, raw in swfile.iter_text(data):
                c += reg.count_bytes(raw)
        elif kind == "zip":
            try:
                bad = zipfile.ZipFile(io.BytesIO(data)).testzip()
                if bad:
                    struct_bad.append((rel, "zip crc"))
            except Exception as e:
                struct_bad.append((rel, type(e).__name__))
        if c:
            leaks[kind] += c
    out = {"files": n, "remaining_identity_hits_by_kind": leaks, "structure_problems": len(struct_bad)}
    Path(a.private, "verify-report.json").write_text(json.dumps({**out, "problems": struct_bad}, indent=1))
    print(json.dumps(out, indent=1))


def main():
    ap = argparse.ArgumentParser(prog="python -m deid")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("discover", "apply", "verify"):
        s = sub.add_parser(name)
        s.add_argument("--private", required=True, help="private dir for key, mapping and reports")
        s.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
        if name == "discover":
            s.add_argument("--input")
            s.add_argument("--manifest")
        if name == "apply":
            s.add_argument("--input", required=True)
            s.add_argument("--output", required=True)
        if name == "verify":
            s.add_argument("--output", required=True)
    a = ap.parse_args()
    {"discover": cmd_discover, "apply": cmd_apply, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    main()
