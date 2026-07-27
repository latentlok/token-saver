# Resume here (fresh session pickup)

State (2026-07-28): branch `v0.5-field-report`, tree CLEAN, suite
`bash ci/run-specs.sh` = 775 tests, exit 0. Nothing in flight.

## Done — do not redo

| commit | what |
|---|---|
| `e030857` | Phase 6 playbooks (qd/playbook.py, brief_file/vars/amend_brief, brief protection, BRIEF line + per-brief ledger, size guard, C11 heartbeat fix) |
| `c705f0b` | README + docs/USAGE rebuilt against v0.5; OVERVIEW refreshed; CLAUDE-snippet compressed (~480 resident tokens) |
| `cd315ce` + `9070e59` + `8cdd7bc` | handoff update; snippet audit; tone revert (see below) |

Build log + deliberate deviations: end of `ARCHITECTURE-playbooks.md` (this folder).

## Next, in order

1. **Task 3 — live probes P1–P8.** Needs the Qwen endpoint FREE; ~1 h supervised.
   What each probe gates: `docs/PENDING.md` (v0.5 section). Also the first live
   exercise of async submit/receipts and playbooks.
2. **Task 4 — release (user's call).** Version bump in `.claude-plugin/plugin.json`
   (+ marketplace metadata), CHANGELOG polish (HUMAN-only — no v0.5 entries exist
   yet, deliberate), then the user decides `v0.5-field-report` → master (= publish).

Open user items: the branch has unpushed commits (`git push` = backup, not publish).

## Standing rules (unchanged)

- One commit per task, on `v0.5-field-report`, never to master.
- Suite exit 0 before any commit. `specs/*_spec.py` are permanent gates.
- Runtime code stdlib-only. Comments say WHY (the measured failure), never what.
- **Tone rule (new, 2026-07-28):** no sharp Qwen-skepticism in agent-facing
  surfaces (snippet/skill/schema). Mechanism statements ("the gate decides") yes;
  suspicion no — the user trusts the worker more than early findings imply.
