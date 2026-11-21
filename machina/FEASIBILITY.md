# Machina CAD De-ID: feasibility report

Measured 2026-11-21 (UTC) in the Claude cloud container used for this branch.
Every number below was measured here unless marked *estimate*. No raw input,
identity value, mapping or log is committed; identities are described only by
kind.

## Verdict

| Question | Answer |
|---|---|
| Can this container hold input + expanded archives + output at once? | **No.** 59–61 GB free vs 139.7 GB input alone. |
| Can the full corpus be processed here anyway? | **Yes, streamed.** 11 batches of whole folders, each ≤ 15 GB (peak ≈ 31 GB on disk), two passes (discover, then apply + verify + upload). *Estimate*: ≈ 2 h download per pass at the measured rate, ≈ 1–2 h processing, upload rate not yet measurable. |
| Is a valid first submission feasible? | **Yes, with two outside steps:** Prince's 1-hour upload token from the signed-in track page (Codex submits), and a host that stays up for the ≈ 5–7 h run. This cloud session is ephemeral, so the run must be resumable (it is, per batch) and the private dir must survive between passes. |
| Are native files guaranteed to open in SolidWorks / Inventor? | **Not verifiable here** (no licenses, no CAD kernels). Edits are designed to be layout-neutral and are checked structurally; see "Validation limits". |

## Resources measured

- Disk: 252 GB volume, 59–61 GB available to this session (per-session allowance).
- CPU/RAM: 4 vCPU, 15 GB RAM, no swap.
- Network: `machina-input.joinbuilders.org` and `www.joinbuilders.org` were blocked
  by the environment policy until both hosts were added to the personal cloud
  environment allowlist (done 2026-11-20 23:59 UTC, approved by the person).
  Official `get-input.sh --limit 100`: 100 files, 426,654,136 bytes in 11 s (≈ 39 MB/s),
  all sizes verified. A 300-file batch via `scripts/fetch_batch.py`: 0.14 GB in 4 s.
- Upload host `storage.googleapis.com` is reachable (anonymous list returns HTTP 403
  from GCS, i.e. auth required, not a proxy block). Upload throughput is unmeasured
  because it needs the track-page token.
- Tools present: python 3.13, pypdf, Pillow, lxml, unzip, ImageMagick, java, node,
  docker. Installed from PyPI for this run: pyahocorasick 2.3.1, py7zr 0.22.0,
  olefile 0.47, zopfli 0.4.3. Absent: 7z/unar/unrar, qpdf/mutool, exiftool,
  FreeCAD, OpenCASCADE, ODA, any SolidWorks/Inventor runtime.

## Corpus (from the public manifest, 188,722 rows, 139.71 GB)

| Extension | Files | GB | Handling in v0 |
|---|---:|---:|---|
| .step/.stp | 62,226 | 53.7 | text: header FILE_NAME author/organisation discovered; all text cleaned |
| .stl | 12,768 | 14.9 | binary/text scan (80-byte header, ASCII names) |
| .sldprt/.sldasm/.slddrw | 22,931 | 15.6 | **native container rewrite** (below) |
| .1/.2/.3… (versioned Creo) | ~15,000 | 11.1 | raw length-preserving scan only |
| .pdf | 10,342 | 6.1 | raw scan + Flate streams rewritten in place |
| .zip | 1,645 | 5.9 | rebuilt, members processed recursively, names renamed |
| .sat | 26,379 | 4.5 | text scan |
| .iges/.igs | 937 | 4.4 | text scan |
| .obj | 461 | 4.1 | text scan |
| .fcstd | 4,717 | 3.1 | zip rebuild; Document.xml CreatedBy/LastModifiedBy/Company |
| .3mf / .f3d / .f3z | 1,503 | 4.8 | zip rebuild, members scanned |
| .ipt/.iam/.idw | 6,920 | 2.8 | OLE: summary properties discovered; raw UTF-16/ASCII scan |
| .svg/.dxf/.wrl | 13,039 | 3.2 | text scan |
| .tar/.gz/.tgz/.z | 1,603 | 1.8 | gzip/tar rebuilt; `.z` treated as binary (unsupported) |
| .7z | 8 | 0.19 | py7zr rebuild (slow: 116 s for one 135 MB archive) |
| .rar | 7 | 0.14 | **unsupported** (no RAR writer); listed as an exception |
| .dwg | 758 | 0.27 | raw scan only; R2004+ sections are compressed (**gap**) |
| images (.png/.jpg) | ~1,300 | 0.4 | copied; logo/EXIF de-ID **not in v0** |

Source defects seen in the manifest: 24 empty files; 40,397 extra byte-identical
copies (14.0 GB); 10,712 files (12.0 GB) under `*.extracted/` folders that duplicate
a sibling archive; 2 `*.7z.resume-partial` files that are truncated copies of a sibling
`.7z` (the sample's one fails to open: Bad7zFile); 2,279 rows without MD5
(multipart uploads); no case-only path collisions; longest path 395 bytes.

## What the sample showed

Official first 100 files: 90 SolidWorks files, 4 zips, 2 7z, 4 STEP.

- **SolidWorks 2015+ files are a ZIP-like container**, not OLE. Each section has an
  8-byte opaque prefix, a 4-byte signature that differs per file, then
  `14 00 06 00 08 00` (version 20, flags 6, deflate), DOS time, CRC-32 of the
  uncompressed data, sizes, a name whose bytes are nibble-swapped, and raw deflate
  data. A central directory repeats CRC, sizes, name and the section offset.
  Encrypted stub records (flags 1) are interleaved and left untouched. In 88 of 90
  sample files every CRC-valid section matched a central entry; the other 2 have
  1–2 extra local sections without a central entry (kept, still rewritten).
- Identities live **inside compressed sections**, invisible to a plain search:
  Windows profile/temp paths with account names (UTF-16) in `Biography`, `Header2`,
  `ReplaceRef`, `DisplayLists`, `CMgr`, model headers; `docProps/core.xml`
  lastModifiedBy; `docProps/app.xml` Company; vendor catalogue org in core props.
  Assemblies embed whole parts (`ImportedComp/*.SLDPRT`) as nested containers.
- Discovery on the sample + manifest found 402 identities: 359 repository owners
  (path rule), 17 OS accounts, 12 emails, 9 domains, 4 person/org fields,
  1 STEP author.

## Approach (why it keeps files usable)

1. **Length-preserving pseudonyms.** HMAC-keyed, same length as the original, letters
   to letters, digits to digits, punctuation kept, case pattern kept, TLD kept.
   Distinct identities get distinct pseudonyms. Because nothing changes length,
   offsets, length-prefixed strings and cross-file references stay valid, and a
   renamed file and every reference to it change together.
2. **SolidWorks in place.** Each changed section is re-deflated to *exactly* its old
   compressed size (zlib/zopfli variants plus standard empty deflate blocks as
   padding), then only the CRC fields (local + central) change. No offset moves.
   Nested containers are processed recursively.
3. **PDF Flate streams** use the same exact-size trick (zlib header and Adler-32
   updated), so the xref table stays valid.
4. **Archives** (zip, 7z, tar, gzip) are rebuilt with members processed recursively
   and member names renamed, so no unprocessed copy remains inside.
5. **Plain-word identities** (e.g. an account named like an English word) are only
   replaced in unambiguous contexts (`owner__repo` folders, `github.com/owner`,
   `@owner`), never as ordinary words in engineering text.

## Results on measured data

| Run | Files | MB/s (4 workers) | Changed | Identity hits left | Structure problems |
|---|---:|---:|---:|---:|---:|
| Official sample (100 files, 0.43 GB) | 100 | 3.6 | 93 | 3 (in 3 SolidWorks sections that could not be refitted) | 0 |
| Batch 009 head (300 files, 0.43 GB: FCStd/STL/STEP) | 300 | 60.2 | 112 | 0 | 0 |

The official sample is dominated by one 135 MB `.7z` (116 s single-threaded repack).
In the SolidWorks-only measurement, 874 sections needed changes and 863 (98.7 %)
were refitted in place; the rest are tiny XML sections (≈ 300 bytes compressed) where
no encoder found an exact fit. They are left byte-identical and reported.

## Validation limits and open gaps (ranked by likely score impact)

1. Native files cannot be opened in their CAD apps here. Checks are structural:
   CRC/central-directory consistency for SolidWorks, `testzip` + XML parse for
   zip-based formats, identical size for every in-place edit.
2. Identity discovery is rule-based (paths, OS profile paths, emails/domains, XML and
   OLE summary properties, STEP header, PDF /Author, FreeCAD properties). Names in
   free text, custom-property blocks of SolidWorks (`CusProps`, binary), BOM tables,
   drawing title blocks and part numbers are not yet discovered. "Identifying project
   or part names and numbers" is not attempted in v0.
3. Not handled: images (logos, EXIF), RAR, DWG compressed sections, Creo/JT/Parasolid
   binary internals, text drawn with font-encoded PDF content.
4. A small share of SolidWorks/PDF sections cannot be refitted to their exact size
   (the 1.3 % above); a container rewriter that moves offsets would close this.
5. The private key/mapping must persist between passes of one attempt. Losing it
   only changes pseudonyms between attempts, which is allowed.

## Next steps

1. Codex/Prince: decide the run host (this cloud session streamed, or a machine with
   ≥ 300 GB) and get a fresh upload token when the upload step starts.
2. Run `scripts/full_run.sh` pass 1 on the full manifest, review identity counts by
   kind (private), then pass 2 with `DEST` set.
3. First scored attempt gives category-level feedback; prioritise gaps 2 and 3 by it.
