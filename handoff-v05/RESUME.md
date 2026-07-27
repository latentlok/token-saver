# Resume here (fresh session pickup)

State at pause (2026-07-27): repo `/home/dev-vishal/projects/qwen-delegate`,
branch `v0.5-field-report`, HEAD `0f36f73`, working tree CLEAN, suite
`bash ci/run-specs.sh` = 706 tests, exit 0. Nothing in flight.

Read order for a fresh session:
1. `TASKS.md` (this folder) — the queue and the standing rules.
2. `ARCHITECTURE-playbooks.md` (this folder) — the complete Phase 6 design;
   build exactly this, do not re-derive. Canonical copy also sits at the TOP of
   `/home/dev-vishal/.claude/plans/wobbly-fluttering-ladybug.md`, which holds
   every phase's build log underneath it.
3. `git log --oneline -4` and `bash ci/run-specs.sh` to confirm the state
   above before any edit.

Then: Task 1 (playbooks) via an opus agent → verify suite → one commit →
Task 2 (README/USAGE rebuild) → verify → one commit → probes when the GPU is
free → user decides the master merge.
