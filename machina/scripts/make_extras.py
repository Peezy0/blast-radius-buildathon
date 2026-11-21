#!/usr/bin/env python3
"""Build the attempt's code/ folder and README.txt (run instructions, limitations,
exceptions by final path). Paths are rendered with the same pseudonyms as the corpus,
so no original identifier is written. Reads only the private apply/verify reports."""
import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from deid.pseudo import Registry  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--private", required=True)
ap.add_argument("--out", required=True, help="folder that receives code/ and README.txt")
ap.add_argument("--commit", default="", help="git commit of the pipeline used")
a = ap.parse_args()

out = Path(a.out)
code = out / "code"
if code.exists():
    shutil.rmtree(code)
ign = shutil.ignore_patterns("__pycache__", "*.pyc", "input", "output", "cleaned-corpus", "work", ".machina-private")
code.mkdir(parents=True)
for item in ("deid", "scripts", "tests"):
    shutil.copytree(HERE / item, code / item, ignore=ign)
for item in ("requirements.txt", "README.md", "FEASIBILITY.md"):
    shutil.copy2(HERE / item, code / item)

reg = Registry(a.private)
priv = Path(a.private)
files = changed = 0
kinds, notes, exc = Counter(), Counter(), []
for rep in sorted(priv.glob("apply-report-[0-9][0-9][0-9].json")):
    d = json.loads(rep.read_text())
    files += d.get("files", 0)
    changed += d.get("changed", 0)
    kinds.update(d.get("kinds", {}))
    for x in d.get("exceptions", []):
        for n in x.get("notes", []):
            notes[n.split(":")[0]] += 1
        exc.append((reg.sub_str(x["path"]), ", ".join(sorted(set(x.get("notes", []))))))
leaks, struct, pdfbad = Counter(), 0, 0
for rep in sorted(priv.glob("verify-report-[0-9][0-9][0-9].json")):
    d = json.loads(rep.read_text())
    leaks.update(d.get("remaining_identity_hits_by_kind", {}))
    struct += d.get("structure_problems", 0)
    pdfbad += d.get("pdf_unreadable", 0)

EXPLAIN = {
    "pdf-unfit-streams": "a compressed PDF stream could not be rewritten at its exact size and the PDF could not be rebuilt; that stream is unchanged",
    "pdf-rewritten": "PDF rebuilt with new offsets because a stream could not keep its size (content unchanged apart from pseudonyms)",
    "sw-unfit-sections": "a SolidWorks section could not be recompressed to its exact size; that section is byte-identical to the source",
    "nested-unfit": "same as above, inside a component embedded in an assembly",
    "zip-error": "source archive is encrypted or damaged; member names were pseudonymised in place, members could not be read",
    "7z-error": "source 7z archive is truncated, damaged or uses an unsupported method; kept as is",
    "gzip-error": "source gzip stream is damaged; kept as is",
    "rar-compressed-members": "RAR5 archive: member names and stored members cleaned in place (CRCs updated); compressed members could not be rewritten (no RAR writer) and are unchanged",
    "rar-bad-header": "RAR archive with a damaged header; cleaned up to that point",
    "partial-from-cleaned-sibling": "truncated download copy of a sibling archive; written as the cleaned sibling cut to the same length",
}
lines = [
    "MACHINA CAD DE-ID: ATTEMPT NOTES",
    "",
    f"Pipeline commit: {a.commit or 'see code/'}",
    "",
    "HOW TO RUN",
    "  python3 -m pip install -r code/requirements.txt        # Python 3.11+, tested on 3.13",
    "  cd code",
    "  WORK=/path/with/40GB PRIVATE=/secure/private-dir MAXGB=8 bash scripts/full_run.sh",
    "  (pass 1 discovers identities over all batches; pass 2 cleans, verifies and uploads batch by batch.)",
    "  Single folder: python3 -m deid discover --private P --input IN; python3 -m deid apply --private P",
    "  --input IN --output OUT; python3 -m deid verify --private P --output OUT.",
    "  PRIVATE holds a random HMAC key and the identity map; it is never part of the submission.",
    "",
    "WHAT IS CHANGED",
    "  Identities (repository owners, OS account names in profile paths, e-mail addresses and their",
    "  non-webmail domains, person/company fields in XML, OLE, STEP, PDF and FreeCAD metadata, GitHub/GitLab",
    "  URLs) are replaced by keyed pseudonyms of the same length and character classes, consistently",
    "  across all files, file names and folder names. Nothing changes length, so offsets, length-prefixed",
    "  strings and references between drawings, parts and assemblies stay valid. SolidWorks 2015+",
    "  sections and PDF Flate streams are recompressed in place at their exact size; archives (zip, 7z,",
    "  tar, gzip) are rebuilt with members processed recursively. Geometry bytes are never edited:",
    "  single-byte matches in binary data are replaced only where they sit in text.",
    "",
    "LIMITATIONS",
    "  No CAD application was available to open native files; checks are structural (SolidWorks section",
    "  CRC/central directory, zip CRC, PDF parse and page text, identical sizes for in-place edits).",
    "  Free-text names in drawing notes, part numbers and project names are not detected. Logos and",
    "  other identifying pixels in images are not changed (image metadata is). RAR5 archives keep their compressed member data (names, stored members and the quick-open index are cleaned); DWG",
    "  compressed sections and Creo/JT/Parasolid binary internals are only scanned as raw bytes.",
    "",
    "RUN SUMMARY",
    f"  files processed: {files}; files changed: {changed}",
    f"  by type: {dict(sorted(kinds.items()))}",
    f"  verify: remaining identity hits by type {dict(leaks)}; structure problems {struct}; unreadable PDFs {pdfbad}",
    "",
    "EXCEPTIONS AND SOURCE DEFECTS (final paths)",
]
for k, v in sorted(notes.items()):
    lines.append(f"  {k}: {v} file(s): {EXPLAIN.get(k, 'see notes')}")
lines.append("")
for p, n in sorted(exc):
    lines.append(f"  {p}: {n}")
(out / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({"code_files": sum(1 for _ in code.rglob('*') if _.is_file()), "exceptions": len(exc)}))
