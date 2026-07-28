# Resume here (fresh session pickup)

State (2026-07-29): branch `v0.5-field-report`, tree CLEAN (2 local commits ahead of
origin: findings `44ddb99` + this handoff — push before handing off). Suite
`bash ci/run-specs.sh` = exit 0 (776-test baseline; P1 added one).

## Task 3 (live probes) — DONE. All P1–P8 recorded.

Records live in `PROBES-P1-P8.md`, each section's RESULT line.

| probe | outcome | commit |
|---|---|---|
| P0 | endpoint free | — |
| P1 autoedit_via_hook | PASSED, **flipped default-on** | `c50aa64` |
| P2 MCP fencing | UNPROVEN (worker wouldn't call the MCP tool) — recorded | `17b61b4` |
| P3 edit/replace field names | MATCH (no change) — `edit`+`file_path` attributed; `old_string`/`new_string` immaterial | `44ddb99` |
| P4 cached tokens | inert here (cached always 0) — recorded | `17b61b4` |
| P5 BurnLimit/usage | BurnLimit fired; `usage` fallback still unproven — recorded | `17b61b4` |
| P7 fixture provenance | MIXED — keep opt-in; worker complies on comment-friendly formats, thrashes on JSON (sidecar is binary-only) — recorded | `44ddb99` |
| P8 progress.json cadence | per-record, unthrottled — recorded | `17b61b4` |

**P3 + P7 are DONE (both record-only, no runtime code changed).** P3: the live executor's
`edit` tool carries `file_path` (read by the named write-branch in `scoped_hook.py`) → write
attributed; content fields `old_string`/`new_string` aren't in `EFFECT_KEYS` but that's
immaterial (a named tool never hits the `:143` shape-check) → no attribution hole, no change.
P7: worker complied on a `.py` fixture (attempt 2, success) but thrashed to `fixture_unproven`
on JSON — `_unproven_fixtures` honors the `.src` sidecar only for binaries (`engine.py:271`),
so comment-free text formats have no compliant route. Default-on would thrash the common
JSON-fixture case → keep opt-in. Pinpointed fix (honor the sidecar for text too) recorded as
PENDING, not a v0.5 blocker.

## Task 4 — release (the only remaining task; USER'S CALL)

- Version bump in `.claude-plugin/plugin.json` (+ marketplace metadata).
- CHANGELOG polish (HUMAN-only).
- Merge `v0.5-field-report` → master (= publish).

**Open items carried forward (NOT v0.5 blockers):**
- P5: `usage` fallback path (`invoke.py:477`) still spec-only — never exercised live (this
  endpoint's stream kept `usage` intact; the stop came via the `on_line` BurnLimit path).
- P7: the sidecar-for-text fix above (would make `fixture_provenance` default-on safe; a
  quick v0.5.1 if desired).
- P2: MCP-namespaced fencing remains spec-only — the worker declined to call an MCP tool in
  both test runs.

## Done — do not redo

| commit | what |
|---|---|
| `e030857` | Phase 6 playbooks |
| `c705f0b` | README + docs/USAGE rebuilt against v0.5 |
| `4c84978` | lld-principles on-demand in manager + trim (was ~52% of weekly usage) |
| `a9ee49f` | heartbeat recipe (later rewritten to push+poll in `c50aa64`) |
| `c50aa64` | P1: autoedit_via_hook default-on + push+poll heartbeat recipe |
| `0fd861b` + `17b61b4` | runbook: P1 result, then P2/P4/P5/P8 findings |
| `44ddb99` | runbook: P3 + P7 findings (both record-only) |

Skill-usage root cause + heartbeat mechanism: memory
(`lld-principles-cache-cost`, `qwen-heartbeat-mechanism`, `user-claude-sub-ttl`,
`feedback-test-off-claude`, `v05-probes-status`).

## Standing rules (unchanged)

- One commit per task, on `v0.5-field-report`, never to master.
- Suite exit 0 before any commit. `specs/*_spec.py` are permanent gates — never remove.
- Runtime code stdlib-only. Comments say WHY (the measured failure), never what.
- Read the receipt FILE; the worker's self-report is never evidence — the gate decides.
- **Tone rule:** no sharp Qwen-skepticism in agent-facing surfaces; mechanism statements only.
- **Heartbeat:** push (`run_in_background` `until [ -f receipt ]`) + poll watchdog (long
  runs only). Both read only what the submit's `RECEIPT:`/`HEARTBEAT:` lines advertise.
