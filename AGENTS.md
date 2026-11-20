# Blast Radius

The user authorizes autonomous development and multiple agents for this project:
implement, test, debug, profile, improve, and save checkpoints without asking for
routine approval. This does not waive required tool approvals or authorize new
paid services, credential disclosure, or changes to security controls.

Read `official/SCORING.md` and `official/README.md` before making changes.
Official track: https://www.joinbuilders.org/buildathon/tracks/blast-radius
Submission cutoff: 2026-11-22 11:00 America/New_York (16:00 UTC).

## Goals and rules

- Start from a working, measurable baseline. Optimize the actual score.
- Preserve stdout's exact JSON-lines protocol and 2 MB size limit.
- A commit misses if ANY failing test is omitted. One caught failure is not enough.
- Compute recorded times in whole milliseconds, matching official/score.py.
- Up to eight nonempty workers; two seconds overhead per worker.
- CPU time is charged; runtime 2 cores/4 GB/180 seconds, 270 seconds final.
- Runtime has no network, a read-only root, 64 MB /tmp, and user nobody.
- Baseline-derived coverage/import/fixture maps are allowed. Patch-answer and
  edit-kind-answer lookup tables are forbidden. Never put sample labels in the
  runtime image. Do not hard-code public patch identities or failure answers.
- Pin sqlglot to eb11aec4be8cb2122afe713a4816662c3f0fb2fa if collecting evidence.
- Docker build downloads must follow the allowed-source list in the track rules.
- Keep the source repository at most 20 MB and MIT licensed.
- Run `make test` and `make sample-local`. Run `make sample` when Docker is
  available. Never describe local subprocess validation as Docker validation.

## Agent coordination

Give agents disjoint file ownership. Integrate and inspect their changes, then
rerun relevant checks. Preserve a known-good full-suite submission while testing
coverage-based narrowing. Incomplete coverage must fall back to the full suite.
Keep evaluation labels confined to offline evaluation.

## Unattended execution

Read `docs/STATUS.md` before continuing. Verify the actual execution location;
being a subagent does not prove cloud execution. Do not claim a background job,
cloud task, automation, repository push, or submission exists unless confirmed
by its owning service. Do not silently substitute a local loop for a requested
cloud agent. Never bypass an approval mechanism. Stop iteration at the cutoff.
Save a checkpoint and report a concrete blocker if infrastructure is unavailable.
