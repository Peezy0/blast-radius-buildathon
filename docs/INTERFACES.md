# Verified interfaces and limits

## Competition

Official track: https://www.joinbuilders.org/buildathon/tracks/blast-radius
Leaderboard: https://www.joinbuilders.org/buildathon/leaderboard
Scoring fleet status: https://www.joinbuilders.org/buildathon/status

The documented runtime interface is local files and stdout, not a network API:

| Interface | Contract |
| --- | --- |
| /data/tests.json | tests with number, id, file, seconds; skipped tests unnumbered |
| /data/commits/<id>.patch | independent unified diff against the pinned baseline |
| stdout | one JSON line per patch, exactly commit and workers; at most 2 MB |
| stderr | diagnostic logs |
| workers | at most eight nonempty arrays; only catalog numbers; no duplicates |
| scorer | unmodified official/score.py; arithmetic in integer milliseconds |
| submission | published web form accepts public GitHub repository and commit |

No documented public REST submission API was found in the published brief or
starter. Do not invent an endpoint or claim API submission access. Runtime
network access is disabled; external LLM APIs cannot be called inside it.

The page currently permits one practice submission per hour. Recheck before
submitting; quotas and rules may change. The final uses new unseen changes.

## Coverage research: verified public APIs

- pytest hooks and pytest.main(args, plugins):
  https://docs.pytest.org/en/stable/reference/reference.html
- Independent pytest processes are needed when code changes because imports can
  remain cached across pytest.main calls:
  https://docs.pytest.org/en/stable/how-to/usage.html#calling-pytest-from-python-code
- CoverageData.measured_files, measured_contexts and contexts_by_lineno:
  https://coverage.readthedocs.io/en/latest/api_coveragedata.html
- pytest-cov --cov-context=test records node IDs and setup/run/teardown phases:
  https://pytest-cov.readthedocs.io/en/latest/contexts.html

Check the pinned installed versions before relying on a specific API. The above
documents establish available mechanisms, not a tested coverage collector.

## Public repositories researched

- https://github.com/tarpas/pytest-testmon: baseline execution-dependency-based
  selection; useful technique and possible benchmark, not yet installed here.
- https://www.testmon.org/blog/determining-affected-tests/: documented block and
  coverage design. This does not prove our heuristic catches every failure.
- https://github.com/anapaulagomes/pytest-picked/blob/dev/README.md: Git-based
  changed-test-file selection; not a source-to-test impact analyzer.

No verified repository from another entrant was found in indexed searches.
Unrelated projects named "blast radius" are not evidence about this contest.
The track forbids cross-team code sharing and precomputed patch-answer tables.

## Agent communication

Use the same authorized GitHub repository, separate branches, published handoff
notes and an explicitly identified coordination issue if created. No direct
Codex-to-Claude messaging API is configured. Sending a message through the
Claude UI is different from establishing a persistent two-way worker channel.
Verify receipt and a response before calling communication operational.
