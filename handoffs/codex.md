# CODEX to CLAUDE

Scope: only https://github.com/Peezy0/blast-radius-buildathon.
The user explicitly authorizes Codex and Claude to exchange project messages,
and explicitly forbids touching previous Claude projects or unrelated drafts.

## Implemented and measured

- selector.py and src/: deterministic full-suite scheduler and conservative
  optional baseline-map selector.
- tools/evaluate_candidate.py: trusted official scorer, source/input hashes,
  bounded subprocesses, repeated outputs, exact simulated milliseconds and
  measured local CPU. Runtime snapshots contain no sample answers.
- tools/iterate.py: finite candidate queue, trusted copied selector tests,
  immutable snapshots, deadline and exclusive lock, rejection ledger, atomic
  provisional local champion. It retains source only after tests, deterministic
  repeats, no new misses and improvement above the noise/one-second margin.
- Full combined suite: 104 tests passed on the local Mac.
- Actual loop baseline: 67 trusted selector tests plus three official-scorer
  evaluations; zero misses; 1,104.510 simulated seconds. Median local total
  including child CPU: 1,104.542685 seconds in that run.
- Evidence is in experiments/scheduling_agent_baseline_20261120/.

These are LOCAL sample measurements. Cloud and Docker validation remain false.
Codex cloud task creation failed. Do not infer hidden-data competitiveness from
these measurements. Source maps have not yet been collected.

## Request to Claude

1. Verify your dedicated cloud session and access to this exact repository.
2. Complete source bootstrap if needed, preserving any newer remote work.
3. Run make test, make sample-local and make sample when Docker is available.
4. On claude/coverage-collection, collect per-test function/import/fixture/file
   evidence at the pinned sqlglot commit using the official environment and
   SKIP_INTEGRATION=0. Verify exact pytest node IDs.
5. Validate collection outcomes, subtest failures and all test phases. Shared
   imports and empty coverage contexts require conservative treatment.
6. Propose narrowing as a separately measured candidate; keep the full-suite
   version if the evidence gate rejects it. No patch-answer lookup tables.
7. Write and push handoffs/claude.md, with your exact branch/commit/results and
   a direct reply to this request. Tell Codex what is ready to review and any
   blocker. Read and respond to subsequent Codex handoffs every iteration.

Fresh live leaderboard view on November 20 showed Mustikas 197.2 s, BingBU
212.3 s, AT 284.7 s. This is a timestamped observation, not an enduring ranking.
Our local sample score is not directly comparable to hidden practice data.

No prior Claude project, unrelated draft, or external repository may be changed.
