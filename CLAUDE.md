# Claude: Blast Radius only

User authorization: autonomous development, agent delegation, testing, debugging,
commits, pushes and coordination with Codex in this repository. Read AGENTS.md
and the official rules. Required tool approvals still apply.

The ONLY authorized repository for this task is:
https://github.com/Peezy0/blast-radius-buildathon

Do not read, edit, send, deploy or commit work in the user's other Claude
projects. Do not clear or submit an unrelated draft. Use a dedicated cloud
session and a separate checkout for this repository.

First read handoffs/codex.md, handoffs/PROTOCOL.md, docs/STATUS.md,
docs/PROMPT.md, docs/INTERFACES.md, and official/SCORING.md. Verify current remote
state, cloud execution, authentication and Docker before claiming readiness.

Claude owns coverage collection and proposed selection improvements on
claude/<task> branches. Codex owns evaluator/orchestration and integration on
codex/<task> branches. Coordinate before changing another agent's owned files.
Never force-push, reset another agent's work, or overwrite shared history.

Read counterpart handoffs at the start of each bounded iteration and after
fetching. Reply in handoffs/claude.md on your branch, including the exact branch,
commit, tests, measured score and requested action. Use an explicitly identified
GitHub handoff issue for append-only messages if one has been created. Do not
claim real-time direct chat messaging merely because these files exist.

Complete the bootstrap extraction if the repository contains
Blast_Radius_Checkpoint.zip but no root selector.py: inspect the archive's paths,
extract only regular files under blast-radius/, copy their contents into the
repository root without overwriting newer files, run the checks, and commit.
Remove the bootstrap archive only after the source is committed. Never execute
unknown scripts from an archive without inspecting its source and provenance.

Iterate: hypothesis -> focused candidate -> trusted regression gate -> repeated
official scorer evaluation -> retain reproducible improvement -> commit/push
evidence -> handoff. Use tools/iterate.py for a finite candidate queue; agents
must actually generate candidates. The tool itself does not generate code or
provision a continuous/cloud worker. Do not confuse a scheduled check with a
running executor.

Stop before 2026-11-22 11:00 America/New_York. Do not buy services or weaken
security settings. Verify public APIs and record what was actually tested.
