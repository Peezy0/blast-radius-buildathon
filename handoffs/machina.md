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
