# Codex and Claude coordination

Scope is only Peezy0/blast-radius-buildathon. Previous Claude projects and drafts
are excluded. Each agent uses a separate checkout and its own branch.

At the beginning and end of each bounded iteration:

1. Fetch origin and inspect branches/PRs belonging to this project.
2. Read the latest counterpart handoff from its published branch, and the
   project's named coordination issue if present.
3. Claim disjoint files and one measurable task before editing.
4. Validate source and measurements. Push the code and handoff together.
5. Include agent name, UTC timestamp, branch, commit, owned files, exact checks,
   score scope, findings, blockers, next task, and a clear message to the other
   agent. Do not infer acknowledgments; record the counterpart's actual reply.

Claude starts with baseline coverage collection and impact-selection proposals.
Codex starts with evaluation, candidate gating and integration. Neither edits
the other's files concurrently without an explicit handoff.

An optional GitHub issue titled "Codex and Claude coordination" is an append-only
message channel once created. Prefix comments [CODEX] or [CLAUDE]. Read and reply
through authorized GitHub access. Only this repository may be used; do not
message other users or projects. A file or issue does not wake an idle agent by
itself: an active worker or verified schedule must read it.

Never force-push or rewrite another agent's branch. Reconcile main before merge,
run relevant checks, and preserve a known-good source commit. New files and
handoffs must be committed to be visible to the other executor.
