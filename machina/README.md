# Machina CAD De-ID pipeline (scoped v0)

Source for the Machina De-ID track. Feasibility evidence: [FEASIBILITY.md](FEASIBILITY.md).
Raw input, outputs, identity mappings and logs never belong in this repository
(`.gitignore` blocks the usual folders).

## Install

```
python3 -m pip install -r machina/requirements.txt   # Python 3.11+, tested on 3.13
```

## Run (enough disk for input + output)

```
cd machina
bash get-input.sh --limit 100                 # official script, files into ./input/
python3 -m deid discover --private ~/.machina-private --manifest input/.get-input/manifest.tsv --input input
python3 -m deid apply    --private ~/.machina-private --input input --output cleaned-corpus
python3 -m deid verify   --private ~/.machina-private --output cleaned-corpus
python3 -m unittest discover -s tests          # synthetic tests only
```

`--private` holds the random HMAC key, the identity map and the reports. Keep it
out of the submission and out of git. The same key gives the same pseudonyms on
every run, so discovery can run before apply, batch by batch.

## Run streamed (less disk than input + output)

```
WORK=/path/with/35GB PRIVATE=~/.machina-private bash scripts/full_run.sh            # both passes, no upload
DEST=gs://builders-machina-submissions-1120/teams/<team>/attempt-N/ \
  TOKEN_FILE=/secure/path/upload-token SKIP_PASS1=1 bash scripts/full_run.sh      # pass 2 with upload
```

`full_run.sh` splits the manifest into whole-folder batches (≤ 15 GB), runs
discovery on every batch first, then apply + verify + upload per batch, and marks
finished batches so it can resume. Copy the official `upload.py` to `$WORK/` first.

## What it does

- `deid/pseudo.py`: same-length, case-preserving HMAC pseudonyms; one Aho-Corasick
  scan over ASCII and UTF-16LE; plain-word identities only in unambiguous contexts.
- `deid/discover.py`: identity rules (repository owner in `sources/<host>/<owner>__…`
  and `archives/<owner>__…`, Windows/macOS/Linux profile paths, emails and their
  non-webmail domains, XML core/app properties, FreeCAD CreatedBy/LastModifiedBy/Company,
  OLE summary properties, STEP FILE_NAME author/organisation/authorisation, PDF /Author,
  GitHub/GitLab URLs). Vendor and placeholder values are excluded.
- `deid/swfile.py` + `deid/deflatefit.py`: SolidWorks 2015+ section rewrite in place.
- `deid/process.py`: per-format handling (SolidWorks, PDF, zip, 7z, tar, gzip, text, OLE, binary).
- `deid/__main__.py`: `discover`, `apply`, `verify` (leaks by kind, structure checks).

## Known limitations

See "Validation limits and open gaps" in FEASIBILITY.md: images/logos, RAR, DWG
compressed sections, free-text names and part numbers, about 1.3 % of SolidWorks
sections that cannot be refitted to their exact size, and no CAD application to
confirm native files open.
