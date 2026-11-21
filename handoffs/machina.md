# Machina De-ID handoff (Claude → Codex)

- Agent: Claude (project thread "Machina feasibility and first pipeline")
- UTC: 2026-11-21
- Branch: codex/machina-feasibility (owns only machina/ and this file; Blast Radius untouched)
- Checks: `python3 -m unittest discover -s machina/tests` (9 synthetic tests, pass);
  official `--limit 100` sample and a 300-file batch processed + verified (see machina/FEASIBILITY.md)
- Score scope: no attempt submitted. Baseline on the board is 292/1000.

## Findings

- Cloud container: 59–61 GB free, 4 vCPU, 15 GB RAM. Full corpus (139.7 GB) only fits
  streamed: 11 whole-folder batches ≤ 15 GB, two passes. Mirror ≈ 39 MB/s.
- SolidWorks 2015+ files are a ZIP-like container with CRC-checked deflate sections;
  account names sit in compressed UTF-16 paths. The pipeline rewrites them in place at
  identical size; 98.7 % of changed sections refit, the rest are left unchanged and reported.
- Sample results: 0 structure problems; 3 identity hits left (unfittable sections).

## Requested action (Codex)

1. Choose the run host (this ephemeral cloud session streamed, or a ≥ 300 GB machine)
   and confirm before the ≈ 5–7 h full run starts.
2. When upload starts, Prince provides a fresh 1-hour upload token and the team
   destination; `scripts/full_run.sh` supports `TOKEN_FILE` for renewals.
3. Codex submits; Claude will not submit or touch attempt folders.
4. Reply here or in the project thread with the chosen host and any rule reading
   that changes scope (e.g. part numbers / project names).

## Update 2026-11-21 ~02:45 UTC (Claude)

- Batch 000 first pass exposed noisy discovery (OLE/PDF binary junk, BOM-encoded PDF authors,
  random "emails" from raw binary, common-word OS account names). Fixed: value cleaning,
  no discovery on raw binary, OS accounts replaced only inside profile paths, `deid prune`.
- PDF/SolidWorks refit search bounded (zopfli ≤ 256 KB, skip hopeless fits): batch 000 had stalled on PDF streams.
- Regression gate after the fix: official sample 100 files → 0 leftover identity hits (was 3), 0 structure
  problems; 300-file batch → 0 / 0. 9 unit tests pass.
- Pass 2 is waiting on Prince (token file + upload address); cleaned output is kept until uploaded.

## Update 2026-11-21 ~05:00 UTC (Claude)

- Commits: e37938e (encrypted-zip member names renamed in place), 3543e19 (noise rules), this commit
  (JPEG/PNG metadata cleaned at equal length; verify counts hits only in readable content).
- Batches 000–001 cleaned + verified locally (29,014 files). Their only structure issues are 9 source
  defects (7 password-protected zips, 1 truncated `.7z.resume-partial`, 1 file without valid SolidWorks sections).
  These outputs used an incomplete name list and will be re-cleaned before upload.
- Name scan over all 11 batches complete; pruned list has 2,686 identities by kind (values private).
- Gate with the complete list: official sample 0 structure problems, 14 leftover hits (13 inside the two
  `.7z` files incl. the unreadable truncated copy, 1 in a nested SolidWorks part); 300-file batch 0 / 0.
- Waiting on Prince: token file + upload address. Then: re-clean 000–001, clean + upload 002–010.

## Update 2026-11-21 ~05:40 UTC (Claude)

- This commit: exact-size deflate fitter gains byte-aligned non-final prefixes (empty fixed blocks then
  an empty stored block: 5,6,7,9,10,11,12,14,15 bytes) and bit-level tails after a single-block stream.
  Every size from the shortest stream upward is now reachable for small sections.
- Gate with the complete list: official sample 14 → 6 leftover hits, 0 structure problems. All 6 left are in
  the truncated `.7z.resume-partial` source copy, which cannot be opened (source defect). 300-file batch 0 / 0.
  11 unit tests pass (new: small-slack fit test).
- Still waiting on Prince: token file + upload address. Then: re-clean 000–001, clean + upload 002–010.

## Update 2026-11-21 ~06:50 UTC (Claude)

- Found a pre-existing bug: a few ordinary words were in the identity list as plain matches (one was a
  person value equal to the PDF key `/First`). Replacing it broke object streams: 20 of 36 tested PDFs from
  batches 000–001 no longer opened in pypdf. Fix: `discover.COMMON_WORDS` makes such words context-only;
  the private map was re-pruned with the same key (backup kept). Result: 0 of 36 broken.
  Batches 000–001 local output predates this and will be re-cleaned before upload (already planned).
- Fitter: byte-aligned prefix pairs reach every residue mod 5; the "hopeless" shortcut leaves 20 % room
  for zopfli. On the 55 batch 000–001 files with unfit sections: SolidWorks unfit 0 (was ~30),
  PDF streams 69 → 7.
- Gate: official sample 6 leftovers (all in the unreadable truncated `.7z` copy), 0 structure problems;
  300-file batch 0 / 0; 12 unit tests pass.
- Still waiting on Prince: token file + upload address.

## Update 2026-11-21 ~07:15 UTC (Claude)

- This commit: `verify` also reports `pdf_unreadable` (pypdf parse + text of the first 3 pages), so the
  PDF-syntax class of bug fixed in 3f83b70 now shows up in every batch report. Cost ≈ 0.1–0.5 s per PDF.
- Proof it catches the bug: old output of the 55 problem files → 20 unreadable; current code → 0
  (originals → 0). Gate: sample 6 leftovers / 0 structure / 0 unreadable; 300-file batch 0 / 0 / 0. Tests pass.
- Still waiting on Prince: token file + upload address.

## Update 2026-11-21 ~08:20 UTC (Claude)

- This commit: a truncated download `x.7z.resume-partial` that is a byte prefix of its complete sibling is
  now written as the cleaned sibling cut to the same length (same size, still truncated, no original
  content). Before, it was copied as-is because it cannot be opened.
- Gate: official sample leftovers 6 → 2 (both 4-letter random matches inside compressed bytes), 0 structure
  problems, 0 unreadable PDFs; 300-file batch 0 / 0 / 0. Tests pass.
- Residual-pattern scan (emails and user-profile paths not in the list) over both cleaned gates: only false
  positives (file@part names, random binary) and one generic account. Discovery recall on those patterns looks complete.
- Still waiting on Prince: token file + upload address.

## Update 2026-11-21 ~09:25 UTC (Claude)

- Fixed a pre-existing PDF bug: the plain-text pass ran over the whole file, so a random 4–13 byte match
  inside a compressed image/font stream changed those bytes (3 damaged streams in 36 tested PDFs, not caught
  by the text check). Now text is cleaned outside streams and in unfiltered streams only; Flate streams go
  through inflate → clean → exact-size refit; other filtered streams are never touched. Damaged streams: 0.
- PDFs with a stream that cannot keep its size are rebuilt with pypdf (new xref) when the result parses and its
  pages read (8 of 8 such files in batch 000–001 problem set); otherwise the in-place result is kept and reported.
- `verify` counts PDF hits only in readable parts (same view as apply), not random bytes in compressed data.
- Gate (same verify for before/after): sample 2 leftovers (random bytes in the truncated 7z copy), 0 structure,
  0 unreadable PDFs; 300-file batch 0/0/0; 55 problem files 0 leftovers, 0 unreadable. Tests pass.
- Still waiting on Prince: token file + upload address.

## Update 2026-11-21 ~10:20 UTC (Claude)

- Fixed a pre-existing geometry bug: in binary data (binary STL, OLE, binary SolidWorks sections, Creo and
  other raw files) a random 4-letter ASCII match inside float/compressed bytes was replaced, changing vertex
  coordinates. Measured on the 300-file batch: 3 of 101 binary STLs had changed triangle data; now 0.
  Rule: single-byte hits in non-text data are replaced only when they sit in text (up to 3 neighbours each side
  printable or NUL). UTF-16 hits (SolidWorks length-prefixed strings) are always replaced; checked that no real
  SolidWorks string hit is skipped on the sample (only one random binary match is).
- `verify` uses the same view. Gate: official sample 0 leftovers, 0 structure, 0 unreadable PDFs;
  300-file batch 0/0/0. 12 unit tests pass.
- Still waiting on Prince: token file + upload address.

## Update 2026-11-21 ~11:15 UTC (Claude)

- Checks this hour (no code change to the cleaner): 150 Inventor files from batch 001 (every 10th):
  0 leftovers, 0 structure problems; the text-context rule skips only 1 random binary match;
  1,200 zlib-compressed Inventor streams hold only vendor names (no personal identity).
  Sample SolidWorks: 777 changed sections, 0 unfit.
- Docs updated (README/FEASIBILITY limits), `pypdf` added to requirements (used by the PDF fallback and verify).
- Still waiting on Prince: token file + upload address.

## Update 2026-11-21 ~12:35 UTC (Claude) — submission readiness

- Gap found: the challenge requires `code/` and `README.txt` (run instructions, limitations, exceptions by
  final path) in every attempt folder, and `full_run.sh` uploaded only `corpus/`. New `scripts/make_extras.py`
  builds both (paths rendered with the same pseudonyms; tested: 0 identity hits in the README), and
  `full_run.sh` uploads them with the official `upload.py --code/--readme` after the last batch.
- Pass-2 prep (private, reversible renames only): old batch 000–001 markers/reports renamed `old-list-*`, their
  old output moved aside (kept, never uploaded). Disk ≈ 33 GB free, so pass 2 runs with `MAXGB=8`
  (21 whole-folder batches, peak ≈ 17 GB). `upload.py` placed in WORK.
- Command when the token file and address exist (Codex submits the address afterwards):
  `WORK=/tmp/claude-0/mach/work PRIVATE=/tmp/claude-0/mach/private BACKUP=/mnt/project-files/machina-private
   MAXGB=8 SKIP_PASS1=1 DEST=<gs://…/attempt-1/> TOKEN_FILE=<file> bash scripts/full_run.sh`

## Update 2026-11-21 ~14:25 UTC (Claude)

- Found: the 7 RAR files (RAR5) were treated as plain binary, so cleaning names in their headers would break
  the header CRC32s (one archive had 349 identity hits in headers + 89 in its quick-open index). New in-place
  RAR5 handler: names in file/service headers and in quick-open records cleaned at equal length with every
  CRC32 recomputed; stored members cleaned with data CRC updated; compressed members reported.
- Evidence: all 7 archives (2,059 members) list with `rarfile` 4.2 before and after; 58 stored members read
  back with CRC check, 0 failures; 0 identity hits left in headers or index. Gates: sample 0/0/0, 300-file 0/0/0;
  14 tests pass. README notes updated.

## Update 2026-11-21 ~15:25 UTC (Claude)

- Measured on a new random 400-file sample across the whole corpus (seed 21, files < 3 MB): 0 known
  leftovers, 0 structure problems, but a residual-pattern scan found Windows profile-path account names
  that discovery had missed: numeric accounts (rejected by the 'not all digits' rule) and accounts in raw
  binary files (Creo etc., which discovery skipped).
- Fix: `discover.user_paths()` (anchored profile-path rule, ASCII + UTF-16) now also runs on binary files and
  accepts numeric accounts of 4+ digits; apply learns such names on the fly as context-only identities (keyed
  pseudonyms, so every worker derives the same one) — no re-scan of the corpus needed.
- After: residual scan shows only pseudonyms, generic accounts and one 3-letter account (below the 4-char
  minimum). The single unreadable PDF in that sample is unreadable in the source too. Gates: sample 0/0/0,
  300-file 0/0/0; 14 tests pass.

## Update 2026-11-21 ~16:25 UTC (Claude)

- Second random 400-file corpus sample (seed 22, < 15 MB, no `*.extracted/`): 1 structure problem — a Fusion
  `.f3d` whose members use Zstandard (zip method 93), which Python 3.13 zipfile cannot read, so it was only
  renamed in place. Now read with `zstandard` 0.23.0 (CRC-checked) and rebuilt with Deflate (noted in README).
  Also 3-letter profile-path accounts (e.g. initials) are now replaced, context-only (paths only).
- After: sample 0/0/0, 300-file 0/0/0, random-2 0 leftovers / 0 structure / 0 unreadable; 14 tests pass.
  `zstandard==0.23.0` added to requirements.
