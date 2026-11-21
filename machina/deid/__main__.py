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


def cmd_prune(a):
    """Re-apply the current discovery rules to an existing identity map (keeps the key)."""
    reg = Registry(a.private)
    before = len(reg.items)
    kept = {}
    for low, v in reg.items.items():
        kinds = v.get("kinds", [])
        if ("email" in kinds or "domain" in kinds) and not disc._email_ok(low if "@" in low else "x@" + low):
            if not ({"account", "person", "person-or-org", "os-user"} & set(kinds)):
                continue
        if disc._ok(low) or "account" in kinds:
            v.pop("context_only", None)
            if all(disc.context_only(low, k) for k in kinds):
                v["context_only"] = True
            kept[low] = v
    reg.items = kept
    reg.save()
    print(json.dumps({"before": before, "after": len(kept)}))


def _init(private):
    global _REG
    _REG = Registry(private)


PARTIAL = ".resume-partial"


def _partial_copy(src: str, data: bytes):
    """A truncated download (``x.7z.resume-partial``) cannot be opened, so its members cannot
    be cleaned. When it is a byte prefix of its complete sibling, clean the sibling and keep
    the same number of bytes: same size, still truncated, and no original content left."""
    if not src.endswith(PARTIAL) or not data:
        return None
    sib = Path(src[: -len(PARTIAL)])
    try:
        if not sib.is_file() or sib.stat().st_size <= len(data):
            return None
        with sib.open("rb") as f:
            if f.read(len(data)) != data:
                return None
        full, res = process_bytes(sib.read_bytes(), sib.name, _REG)
    except OSError:
        return None
    if len(full) < len(data):
        return None  # keep the size: fall back to the normal path
    res.notes.append("partial-from-cleaned-sibling")
    res.changed = True
    return full[: len(data)], res


def _apply_one(src: str, dst: str):
    data = Path(src).read_bytes()
    new, res = _partial_copy(src, data) or process_bytes(data, Path(src).name, _REG)
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


def _hits(data, kind, name, reg, depth=0):
    """Identity hits where content is actually readable: inside sections and archive
    members, image metadata only; never random matches in compressed bytes."""
    import gzip
    import io
    import zipfile
    try:
        if kind == "swx":
            n = 0
            for _nm, raw in swfile.iter_text(data):
                n += _hits(raw, "swx", "", reg, depth + 1) if depth < 3 and swfile.is_swx(raw) else reg.count_bytes(raw)
            return n
        if kind == "image":
            return _image_meta_hits(data, reg)
        if kind in ("zip", "7z", "gzip") and depth < 5:
            members = []
            if kind == "zip":
                z = zipfile.ZipFile(io.BytesIO(data))
                members = [(i.filename, z.read(i)) for i in z.infolist() if not i.is_dir()]
            elif kind == "7z":
                import py7zr
                with py7zr.SevenZipFile(io.BytesIO(data), "r") as z:
                    members = [(k, v.read()) for k, v in z.readall().items()]
            else:
                members = [(name[:-3], gzip.decompress(data))]
            n = 0
            for mname, blob in members:
                n += reg.count_bytes(mname.encode("utf-8", "replace"))
                n += _hits(blob, classify(blob[:8192], mname, len(blob)), mname, reg, depth + 1)
            return n
    except Exception:
        pass  # unreadable archive (source defect): fall through to a raw count
    return reg.count_bytes(data)


def _image_meta_hits(data, reg):
    from .process import _image
    return 0 if _image(data, reg) == data else 1


def _pdf_ok(data: bytes) -> bool:
    """Parse the PDF and read the first pages' text: catches broken object streams,
    xref offsets and content streams (a source PDF can fail too: compare with the input)."""
    import io
    import logging
    try:
        import pypdf
    except ImportError:
        return True
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    try:
        r = pypdf.PdfReader(io.BytesIO(data), strict=False)
        for page in r.pages[:3]:
            page.extract_text()
        return True
    except Exception:
        return False


def cmd_verify(a):
    """Count remaining original identities (plain, UTF-16, inside SolidWorks sections)
    and check SolidWorks/ZIP structure in the output."""
    import io
    import zipfile
    reg = Registry(a.private)
    root = Path(a.output)
    leaks, struct_bad, pdf_bad, n = Counter(), [], [], 0
    for p in iter_files(root):
        n += 1
        rel = p.relative_to(root).as_posix()
        if reg.sub_str(rel) != rel:
            leaks["path"] += 1
        data = p.read_bytes()
        kind = classify(data[:8192], p.name, len(data))
        c = _hits(data, kind, p.name, reg)
        if kind == "swx":
            if swfile.check(data)["valid_sections"] == 0:
                struct_bad.append((rel, "no valid sections"))
        elif kind == "pdf":
            if not _pdf_ok(data):
                pdf_bad.append(rel)
        elif kind == "zip":
            try:
                bad = zipfile.ZipFile(io.BytesIO(data)).testzip()
                if bad:
                    struct_bad.append((rel, "zip crc"))
            except Exception as e:
                struct_bad.append((rel, type(e).__name__))
        if c:
            leaks[kind] += c
    out = {"files": n, "remaining_identity_hits_by_kind": leaks, "structure_problems": len(struct_bad),
           "pdf_unreadable": len(pdf_bad)}
    Path(a.private, "verify-report.json").write_text(
        json.dumps({**out, "problems": struct_bad, "pdf_unreadable_files": pdf_bad}, indent=1))
    print(json.dumps(out, indent=1))


def main():
    ap = argparse.ArgumentParser(prog="python -m deid")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("discover", "apply", "verify", "prune"):
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
    {"discover": cmd_discover, "apply": cmd_apply, "verify": cmd_verify, "prune": cmd_prune}[a.cmd](a)


if __name__ == "__main__":
    main()
