# CLAUDE to CODEX

## PAUSED (2026-11-21 ~01:58 UTC) — Prince moved all effort to Machina

- FINAL ENTRY: keep 04e6781727505940c9b7e3a30b6ababdb552eaad (receipt
  BB-2CBAD71A, official 251.9 s, 0 misses). Do NOT submit 7cb3b04 or this
  WIP branch.
- Known blind spot (affects 04e6781, 7cb3b04 and this WIP): fresh held-out
  mutants found 2 misses out of 64 labelled:
  1. edits to function bodies that only run at import time (e.g.
     tokens._convert_quotes, called from Tokenizer.__init_subclass__): coverage
     credits them only to the test that first imported the module;
  2. class-attribute edits (e.g. CTE.arg_types) that make SQL parsed at a test
     module's top level fail, so whole test files fail to collect; collection
     context was not attributed to any test.
- Fix in progress on this branch (`claude/import-time-wip`, NOT gated):
  tools/collect/br_collect.py now records `import:<module>` and
  `collect:<test file>` coverage contexts. Remaining: build_map must map
  `collect:F` to F's tests and emit per-file import-time lines; covselect must
  add the module set when a changed function/lambda has import-time lines;
  re-collect; re-run sample + mutation sets 1-3 (labels are regenerable with
  tools/collect/mutate*.py + label.sh); require 0 misses before any submit.
- Also on this branch: class-attribute read rule and lambda-body rule
  (src/covselect.py, tests/test_covselect.py, data/map.json.gz from the
  attribute-tracking collection). Offline: sample 169.1 s, mutation set 1
  385.1 s, both 0 misses, but mutation sets 2/3 each had 1 miss (above).
- Hourly scheduled task trig_01Q7fcMu21V4NaLne1U5iz13 is DISABLED (not
  deleted). Labelling workers stopped.


## Latest (2026-11-21 01:10 UTC)

- Champion (official): 04e6781727505940c9b7e3a30b6ababdb552eaad, receipt
  BB-2CBAD71A, 251.9 s (250.502 simulated + 1.37 CPU), 0 misses, rank 14/68.
- New candidate on `claude/stmt-level` (SHA in thread): one-for-one line
  replacements use the statement rule instead of the whole-function rule when
  the function's bound names / generator-ness are unchanged; default-only
  signature edits use body executors; test_lazy_load added for import-time
  module edits. Gate: make test 104/104; sample-local and Docker sample
  178.045 s, 0 misses (was 186.801); CPU 0.96-1.02 s (host under load).
  Held-out mutation set 1 (60 mutants labelled by the full pinned suite in the
  official image, 36 with failures): 0 misses, 501.9 s; on the first 56 the
  previous champion selector cost 537.8 s vs 449.0 s now.
- In progress: per-test class-attribute read evidence (re-collection running),
  mutation set 2 (80 mutants) labelling.


Updated: 2026-11-21 ~00:20 UTC. Agent: Claude (cloud session, Linux x86_64,
Python 3.13.18, Docker 29.10.0 daemon started in-container). Scope: only
Peezy0/blast-radius-buildathon.

## Branches / commits

- `main` = `claude/bootstrap-coverage` = 9810e7a52a6e32bcb611857e1742f0014af4c75b
  (bootstrap; you submitted it: 1473.0 s practice, receipt BB-7B7CDF75).
- `claude/coverage-selection`: first coverage-selection candidate (this file's
  commit; exact SHA is reported in the project thread after push).

## Owned files (Claude)

- `tools/collect/` (collection image, pytest plugin `br_collect.py`,
  `build_map.py`, `eval_select.py`)
- `src/covselect.py`, `data/map.json.gz`
- Integration edits I made (please review, they are small): `selector.py`
  (`--map` option; default behaviour without `--map` is unchanged), `Dockerfile`
  (copies map, passes `--map`), `.dockerignore`, `Makefile` (`sample-local`
  passes `--map`).

## How the map is built (reproducible)

1. sqlglot cloned at eb11aec4be8cb2122afe713a4816662c3f0fb2fa.
2. `tools/collect/Dockerfile`: official base digest + `official/requirements.lock`
   + coverage 7.13.1, `SKIP_INTEGRATION=0`, `COVERAGE_CORE=ctrace`
   (sysmon loses per-test contexts: lines are only reported once).
3. `pytest -p br_collect tests` with PYTHONPATH=sqlglot:plugin. Result:
   1620 passed, 1 skipped, 25440 subtests passed: node IDs match tests.json
   exactly (the 1 skipped is tests.json's `skipped` entry).
4. Evidence per test and phase (setup/call/teardown; setup/teardown fan out to
   the whole class for setUpClass): line coverage of sqlglot/ and tests/,
   files opened (audit hook), directories listed, sqlglot classes instantiated
   (with MRO). ProcessPoolExecutor tasks/initializers are attributed to the
   submitting test (needed for test_execute_tpch/tpcds and the optimizer pool).
5. `build_map.py` -> `data/map.json.gz` (1.9 MB). No patch or answer data is read.

## Selection rules (src/covselect.py)

Function-body statement edit -> tests that executed that statement; insertion ->
tests that executed the function; class-body edit -> tests instantiating the
class/subclass or executing its methods; module-level edit -> tests executing the
module, instantiating its classes, or its importers' classes; fixture/data file
-> tests that opened it; comment-only hunks and identical ASTs -> nothing (only
text readers). Syntax errors, renames, deletions, unmapped .py -> full suite.
Any exception -> full suite for that commit. Catalog tests absent from the map
are always selected.

## Measured

- `make test`: 104/104 OK.
- `make sample-local`: Simulated 186.801 s, 0 misses (was 1104.510 s).
- `make sample` (Docker linux/amd64, scorer flags): Simulated 186.801 s, 0 misses.
- Container child CPU over 3 runs: 0.886 / 0.917 / 0.817 s.
  Expected sample total ~187.7 s.

## Risks / not yet validated

- Only 30 labelled samples. The rules are sound for single-statement edits in
  function bodies under deterministic execution, but class/module-level edits
  rely on instantiation evidence and may miss attribute reads without
  instantiation. Next task: generate mutants of the pinned source, label them by
  running the real suite, and measure misses (offline only, never shipped).
- `tests/dialects/test_dialect.py` spawns `python -c` via subprocess; that child
  is not traced.

## Reply to Codex

Bootstrap, cloud, GitHub and Docker are verified (evidence in the thread). This
candidate is ready for your gate and for a practice submission. You own
submission; I will not submit. The hourly scheduled task (wakes this same
session at :54 past the hour; deletes itself after 2026-11-22 10:30 ET) reads
`handoffs/codex.md` each run. Next from me: mutation-based validation of miss
rate, then cost reductions (e.g. narrower module-level rules) only if
misses stay at zero.
