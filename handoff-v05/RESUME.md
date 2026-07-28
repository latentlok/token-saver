# Resume here (fresh session pickup)

State (2026-07-29): branch `v0.5-field-report`, pushed to origin (0 ahead). Suite
`bash ci/run-specs.sh` = 775 tests, exit 0. **Currently running Task 3 (live probes).**

## Done — do not redo

| commit | what |
|---|---|
| `e030857` | Phase 6 playbooks (qd/playbook.py, brief_file/vars/amend_brief, brief protection, BRIEF line + per-brief ledger, size guard, C11 heartbeat fix) |
| `c705f0b` | README + docs/USAGE rebuilt against v0.5; OVERVIEW refreshed; CLAUDE-snippet compressed (~480 resident tokens) |
| `cd315ce` + `9070e59` + `8cdd7bc` | handoff update; snippet audit; tone revert (see below) |
| `4c84978` | skill: lld-principles on-demand in manager + ~18% body trim (was ~52% of weekly usage — subagent 5-min TTL re-billed it every turn across long Qwen runs) |
| `a9ee49f` | skill: heartbeat recipe (ScheduleWakeup 900s, reads progress.json, stop on receipt) — loop live-tested on a non-Claude model via synthetic fixture |
| `053b754` | handoff: P1–P8 live probe runbook (`PROBES-P1-P8.md` in this folder) |

Build log + deliberate deviations: end of `ARCHITECTURE-playbooks.md` (this folder).
Skill-usage root cause + heartbeat mechanism: see memory
(`lld-principles-cache-cost`, `qwen-heartbeat-mechanism`, `user-claude-sub-ttl`).

## Next — DO THIS

1. **Task 3 — live probes P1–P8.** Run them per `PROBES-P1-P8.md` (this folder) — one
   section per probe: what it gates, what to run, what to observe, the decision after.
   Prereq: Qwen endpoint FREE/idle (confirm with a throwaway `qwen_query("ping")`).
   ~1 h supervised. Also the first live exercise of async submit/receipts + playbooks.
   - P1, P7 may flip defaults → own commit each (config + spec pin), suite green first.
   - P3 may build (extend `EFFECT_KEYS`); P6 builds `allow_delete` ONLY if real delete
     phrasings are gathered first (never a blind parser).
   - P2, P4, P5, P8 are record-and-decide.
2. **Task 4 — release (user's call).** Version bump in `.claude-plugin/plugin.json`
   (+ marketplace metadata), CHANGELOG polish (HUMAN-only — no v0.5 entries exist
   yet, deliberate), then the user decides `v0.5-field-report` → master (= publish).

## Standing rules (unchanged)

- One commit per task, on `v0.5-field-report`, never to master.
- Suite exit 0 before any commit. `specs/*_spec.py` are permanent gates.
- Runtime code stdlib-only. Comments say WHY (the measured failure), never what.
- **Tone rule:** no sharp Qwen-skepticism in agent-facing surfaces
  (snippet/skill/schema). Mechanism statements ("the gate decides") yes; suspicion no.
