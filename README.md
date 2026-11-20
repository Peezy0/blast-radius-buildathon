# Blast Radius

A deterministic test selector and worker scheduler for the Builders Buildathon
RWX track. This is a working development checkpoint, not a submitted entry.

The default selector schedules every known test, so it cannot omit a failing
catalog test. It optimizes the worker count and assignment against the official
cost: slowest worker's recorded time plus two seconds per nonempty worker.
Identical selections reuse their schedules across commits.

An optional conservative impact module can narrow function-body changes when
given complete dependency evidence from the pinned baseline. No such evidence
has been generated yet. Without it, the selector keeps every test.

## Validate

Requires Python 3.10 or newer for development. The production image uses the
official pinned Python 3.13 image and needs no third-party Python packages.

```sh
make test
make sample-local
```

`sample-local` uses the official scorer and public sample data. Its output is
exact simulated CI seconds, excluding the server's container CPU charge.

With Docker installed, run the sandbox validation:

```sh
make sample
```

This builds linux/amd64 and uses the official CPU, memory, network, user,
read-only filesystem, and /tmp limits. Docker validation has not yet run in the
initial workspace because Docker is unavailable there.

## Source layout

- `selector.py`: production JSON-lines runner.
- `src/scheduling.py`: deterministic worker scheduling and optional slack fill.
- `src/impact.py`: optional baseline-evidence-driven impact analysis.
- `tests/`: unit, property, and protocol regression checks.
- `official/`: unmodified official kit and offline sample evaluation data.
- `docs/STATUS.md`: verified execution state and remaining work.

Only `selector.py` and `src/*.py` enter the production Docker image. Public
sample failure labels are used exclusively by the offline scorer.

## Continue development

Read `AGENTS.md`, `docs/STATUS.md`, and the official rules. Preserve the working
full-suite baseline. Next collect per-test function, import, fixture, and file
dependencies from sqlglot at `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, using the
official environment. Hold out sample patches while validating generalization.
Measure both omitted-failure commits and charged runtime CPU before selecting
an aggressive policy. Do not embed patch identities or sample answers.

## Submission and cloud execution

The competition requires a public GitHub repository and a commit with the
Dockerfile at its root. No remote repository or competition entry has been
created. The deadline is November 22, 2026, 11 AM Eastern.

This code does not itself provision a cloud agent. Cloud task creation was
attempted but the service returned an error. No autonomous cloud worker is
currently established. A bounded hourly continuation automation is enabled,
with a strict requirement to use cloud execution only and report a blocker if
cloud remains unavailable. Its first scheduled check is November 20 at 7 PM
Eastern, and its last is November 22 at 10 AM Eastern. The automation's inherited
execution environment is unspecified, so its existence does not confirm cloud
compute availability. A working cloud environment remains necessary for
development independently of the local computer.

Original source: MIT license. The unmodified kit in `official/` comes from the
event organizers and is included for provenance and offline validation.
