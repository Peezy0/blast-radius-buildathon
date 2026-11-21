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
