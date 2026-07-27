# Resume here (fresh session pickup)

State at pause (2026-07-28): repo `/home/dev-vishal/projects/qwen-delegate`,
branch `v0.5-field-report`, working tree CLEAN, suite
`bash ci/run-specs.sh` = **775 tests, exit 0**. Nothing in flight.

**Tasks 1 and 2 are DONE and COMMITTED** (user-directed, built inline):
- `e030857` — Phase 6 playbooks (qd/playbook.py, brief_file/vars/amend_brief,
  content-based brief protection, BRIEF receipt line + per-brief ledger,
  BRIEF TOO BIG guard, C11 heartbeat submit-cwd fix). Build log + deliberate
  deviations at the END of `ARCHITECTURE-playbooks.md` (this folder).
- `c705f0b` — README + docs/USAGE rebuilt against v0.5 (async workflow,
  routing table, playbooks section, co-working section); OVERVIEW refreshed;
  CLAUDE-snippet compressed to ~470 resident tokens.

Read order for a fresh session:
1. `TASKS.md` (this folder) — the remaining queue and the standing rules.
2. The `## Build log — playbooks` section of `ARCHITECTURE-playbooks.md` —
   what shipped and where it deviated, each deviation spec-pinned.
3. `git log --oneline -5` and `bash ci/run-specs.sh` to confirm the state
   above before any edit.

Then: Task 3 (live probes P1–P8, needs the Qwen endpoint FREE — see
docs/PENDING.md for what each gates) → Task 4 (version bump + CHANGELOG
polish, human-in-loop) → user decides the master merge (= publishing).
