# Confirmed track brief

Status: CONFIRMED. Read from the official live track and downloaded kit on
November 20, 2026. Track: RWX — Blast Radius, Builders Buildathon.

Source: https://www.joinbuilders.org/buildathon/tracks/blast-radius
Exact protocol and scoring: `../official/SCORING.md`.
Official kit: https://www.joinbuilders.org/buildathon/tracks/blast-radius/files/blast-radius-sample.tar.gz

For each independent unified diff against sqlglot commit
`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, choose tests from `/data/tests.json`
and partition them into at most eight workers. Emit exactly one JSON object per
patch, with exactly the keys `commit` and `workers`. Test IDs cannot repeat.

Minimize total simulated CI time plus actual container CPU. The cost per commit
is the busiest worker's recorded duration plus two seconds per nonempty worker,
and another 300 seconds if ANY failing test is absent. Runtime has no network.
Three valid repeated runs are required; the middle total is scored.

Submit a public GitHub repository and commit with a root Dockerfile. The final
rebuilds the selected commit and evaluates previously unseen changes, including
kinds of change absent from practice. A sample-only heuristic is insufficient.
No patch-to-answer or edit-kind-to-answer lookup tables. Maps from the pinned
baseline (coverage, imports, fixtures) are permitted.

The official kit contains 30 sample patches, their offline answers, the test
catalog, the reference scorer, a run-all-tests selector, and a pinned base image.
Read `official/README.md` for runtime and environment details.

Cutoff: Sunday, November 22, 2026, 11:00 AM America/New_York (16:00 UTC).
