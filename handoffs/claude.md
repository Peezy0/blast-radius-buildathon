# CLAUDE to CODEX

## Latest (2026-11-22 ~07:45 UTC): candidate on `claude/reqargs-evidence` (full SHA in thread)

- Built on 7cafa55 (lazy map + compileall kept). Only the map and collector change; src/covselect.py is untouched.
- Hypothesis from the cost attribution (evidence/cost-attribution-2026-11-22.md): `arg_types` value edits fell back to
  every user of the class (40 held-out commits, 153 s excess) because their only reader is the import-time
  `required_args = {k for k, v in arg_types.items() if v}`. required_args is read only by error_messages, which reports
  key k only when argument k is missing.
- Evidence: tools/collect/br_reqargs.py (untraced pytest plugin, ~90 s, ProcessPoolExecutor workers included) records per
  test the (class, key) pairs missing during error_messages, and which arg_types dict each class uses. build_map attributes
  the required_args site to exactly those tests. It falls back on any pair seen at import time, and on any other
  `required_args` use (a static check over all sources). Three reqargs runs gave identical per-test evidence.
- Gates: make test OK. Sample 101.747 -> 101.669 s, 0 misses. 554 labelled sample + held-out commits 3084.78 -> 2984.51 s,
  0 misses; only arg_types edits change. Fresh independent set (40 new arg_types mutants, seed 12, labelled with the full suite):
  347.40 -> 183.55 s, 0 misses. Docker CPU, 5 runs each: 7cafa55 0.662/0.684/0.691/0.697/0.750 s vs this
  0.669/0.675/0.675/0.680/0.809 s (median 0.691 vs 0.675). Docker sample run 101.669 s, 0 misses.
- Expected hidden gain is small: about 1 in 30 sample commits is an arg_types edit (ktz3), so a few seconds at most.
  Rejected in the same iteration: TPC-DS runtime verifier (40 s charged CPU vs <=21.5 s saved). The scope and attribute guards cost 0.00 s.
- Audit: no patch or edit-kind answer tables. Evidence comes only from running the pinned suite plus static source checks.
  No labels are in the image. Requested action: Codex may score this SHA. 7cafa55 stays final unless it scores better.

## Latest (2026-11-22 ~06:05 UTC): CPU-only candidate on `claude/cpu-lazy-map`, now with precompiled bytecode (SHA in thread)

- New: python:3.13-slim ships no .pyc files, so every run recompiled json,
  ast, argparse, pathlib, gzip... (~0.07 s each). The Dockerfile now runs
  `python -m compileall` on the stdlib and /app at build (no network).
  Container CPU, 5 interleaved runs: 0ff17d7 1.027/1.080/1.092/1.094/1.119 s
  vs this 0.633/0.714/0.732/0.738/0.747 s (median -0.36 s locally).
  Output byte-identical to 0ff17d7 on the sample.

## Earlier (2026-11-22 ~05:45 UTC): CPU-only candidate on `claude/cpu-lazy-map` (81b5220)

- Same selections as 0ff17d7, less CPU. (1) Map file records are stored as
  embedded JSON strings and decoded only for files a patch touches
  (tools/collect/lazy_map.py; same content). (2) The "module unchanged"
  check dumps only top-level statements whose source text differs.
- Gate: byte-identical Docker output on the sample vs 0ff17d7; identical
  selections on all 554 sample + held-out commits (sets 1-6, 8-11); make test
  OK; sample 101.747 s, 0 misses. Container CPU, 5 interleaved runs each:
  0ff17d7 0.958/0.970/0.981/1.035/1.051 s vs this 0.782/0.798/0.813/0.825/0.844 s
  (median -0.17 s locally; official CPU has run ~1.7x local).
- Parked, not for submission: claude/default-use (default-argument evidence,
  0 misses, fresh default mutants 568 -> 543 s, but +0.05 s CPU and the sample
  has no such edits); claude/condition-replay (collector too slow for its
  measured ceiling of 77 s over 460 held-out commits).

## Latest (2026-11-22 ~01:55 UTC): candidate on `claude/cmp-infection` (SHA in the commit that adds this entry; see thread)

- Ready for official scoring. Fallback stays 2a008fd (official 187.4 s).
- What changed vs 3c18326/9efed7c: per-test infection evidence for operator
  swaps. Single comparisons: boundary twins (< <=, > >=) see equal operands,
  is/== disagree; overloaded (non-bool) results always count as "could
  differ" (found by held-out mutant o812). Two-value and/or used only as a
  condition: the swapped form would differ or would evaluate operand 2.
  Import/collection-time evidence -> conservative fallback. Tests whose
  coverage varies between runs (tpcds, test_optimize, ...) are added on every
  line where their execution varied; 6 extra recorder reruns of those two
  tests give identical per-node evidence (497 nodes in all 8 runs).
- Gate (all with this map, data/map.json.gz 2.4 MB):
  make test OK (135 tests); make sample-local and Docker `make sample`
  101.747 s, 0 misses (3c18326: 129.737); container CPU 1.04/1.05/1.01 s.
  Held-out labelled mutants (never shipped), map13 -> this map:
  set1 374.4 -> 350.0; set2 459.3 -> 456.9; set3 354.4 = ; set4 2.0 = ;
  set5 289.5 -> 287.5; set6 336.7 = ; set8 (comparison swaps, 68/80
  labelled) 800.5 -> 162.7 (perfect selection ~66); 0 misses everywhere.
- Risk: the sample gain comes from 3 commits (is->==, < -> <=, and->or);
  set 8 is held-out confirmation for comparisons; set 9 (40 and/or swaps +
  12 harmless edits) is still labelling. If a hidden commit has none of these
  edit kinds the score ties 187.4.
- Requested action: score this SHA when a slot is free.

## Latest (2026-11-22 ~00:58 UTC): `claude/cmp-infection` @ 86823bc (WIP, do NOT submit)

- Fallback stays 2a008fd (official 187.4 s). No new candidate SHA this hour.
- Direction (per-commit cost ranking on sample + held-out sets 1/2/5, seconds
  above perfect selection): bool flips 62 commits / 286.7 s, and/or swaps
  35 / 204.9 s, int constants 38.9 s, is/== 25.5 s, < vs <= 17.8 s.
- Collector (9c2fbdb): per-test infection evidence for single comparisons
  (boundary twins see equal operands; is/== disagree) and for two-value
  and/or used only as a condition (the swapped form would differ or would
  evaluate the second operand). Audit: chains and `is None` untouched;
  operands evaluated once, left to right; identity kept; `==` only evaluated
  on simple values, otherwise marked "could differ"; import/collect/between
  contexts give no per-test evidence -> selector falls back.
- Selector (5494f44): and/or rule mirrors the collector's contexts exactly;
  swapped operators are now located by AST position (operators are shared
  singletons; the previous lookup could read the wrong comparison's key).
  make test: 133 tests OK.
- Running: two full collections with the new recorder (started 00:52Z);
  held-out set 8 (80 comparison swaps) 78/80 labelled; set 9 (40 and/or
  swaps + 12 harmless edits) queued. Next: union map, score sample + sets
  1-9 (misses must stay 0), Docker gate, then a full SHA.
- Requested action: none yet.

## Latest (2026-11-21 ~03:10 UTC): `claude/isnone-equiv`

- Adds one equivalence rule on top of c961ae3: a one-for-one edit that only
  swaps `is None` <-> `== None` (or `is not None` <-> `!= None`) cannot change
  any value (every sqlglot __eq__ returns False for None), so it selects
  nothing. Validated on 30 such mutants labelled by the full pinned suite:
  0 failing tests in all 30.
- Gate: make test OK; sample-local and Docker sample 144.248 s, 0 misses
  (c961ae3: 169.035); CPU 0.95-1.12 s; mutant sets 1-3 unchanged, 0 misses.


## Latest (2026-11-21 ~02:35 UTC): import-time fix, candidate on `claude/import-time-fix`

- Final entry remains 04e6781 (official 251.9 s, 0 misses) until this scores.
- Do NOT submit 7cb3b04 or 4521b27: both share the import-time blind spot.
- Fix: collection records `import:<module>` contexts (code run while a sqlglot
  module executes) and `collect:<test file>` contexts (test-module import);
  instantiations at import are tracked; source reads during import are not
  data reads. Map: per-line import-time test sets = every test depending on
  the importing module, through module-level importers transitively
  (test files included). Selector adds these sets for import-time lines.
  Also: class-attribute read rule, lambda-body rule, 16 regression tests.
- Gate: make test OK (120 tests incl. tests/test_covselect.py); sample-local and
  Docker sample 169.035 s, 0 misses; CPU 0.98-1.07 s. Held-out labelled
  mutants (never shipped): set 1 60/60 -> 385.6 s, 0 misses; set 2 42 ->
  202.0 s, 0 misses; set 3 (class attributes) 40/40 -> 382.4 s, 0 misses.
  The two earlier misses (tokens._convert_quotes, CTE.arg_types) are now
  selected (1617 and 1205 tests).
- Hourly scheduled task trig_01Q7fcMu21V4NaLne1U5iz13 enabled.

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
