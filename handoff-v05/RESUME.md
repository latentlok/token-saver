# Resume here (fresh session pickup)

State (2026-07-29): branch `v0.5-field-report`, pushed to origin (0 ahead), tree CLEAN.
Suite `bash ci/run-specs.sh` = 776 tests, exit 0.

## Task 3 (live probes) — PARTIAL. Only P3 + P7 remain.

Done this session (records in `PROBES-P1-P8.md`, each section's RESULT line):

| probe | outcome | commit |
|---|---|---|
| P0 | endpoint free | — |
| P1 autoedit_via_hook | PASSED, **flipped default-on** | `c50aa64` |
| P2 MCP fencing | UNPROVEN (worker wouldn't call the MCP tool) — recorded | `17b61b4` |
| P4 cached tokens | inert here (cached always 0) — recorded | `17b61b4` |
| P5 BurnLimit/usage | BurnLimit fired; `usage` fallback still unproven — recorded | `17b61b4` |
| P8 progress.json cadence | per-record, unthrottled — recorded | `17b61b4` |

**Remaining: P3 and P7 — the two build/flip probes.** Run them per the P3 and P7 sections
of `PROBES-P1-P8.md` (this folder). Prereq: Qwen endpoint free/idle (confirm with a
throwaway `qwen_query("ping", cwd=<repo>)`). Use the push+poll heartbeat recipe
(push `until [ -f receipt ]` for completion; poll watchdog only on long runs).

- **P3** — extend `EFFECT_KEYS` in `scoped_hook.py` if the live executor's edit/replace
  `tool_input` field names diverge from `("file_path","path","command","content")`.
- **P7** — flip `fixture_provenance` to default-on IF a live run shows the worker complies
  (adds `captured-from:` headers) rather than thrashing to `fixture_unproven`.

Then **Task 4 — release (user's call):** version bump in `.claude-plugin/plugin.json`
(+ marketplace metadata), CHANGELOG polish (HUMAN-only), merge `v0.5-field-report` → master
(= publish).

## Files needed to run P3 + P7 (and nothing else)

**The runbook (read first):**
- `handoff-v05/PROBES-P1-P8.md` — the P3 and P7 sections (what to run / observe / decide).
  Their RESULT lines are still empty — fill them in as you go.

**P3 — code + spec to touch if a divergence is found:**
- `scoped_hook.py` — `EFFECT_KEYS = ("file_path","path","command","content")` (~line 73).
  This is the only field-name list; add the real executor edit/replace field names here if
  they differ. The file is small (read it whole).
- `specs/engine_spec.py` — the `ObservedAutoEdit` / attribution tests (search `EFFECT_KEYS`,
  `write_log`, `attribution`). The harness pins `autoedit_via_hook: false` via
  `QWEN_DELEGATE_CONFIG` in `Fixture.setUp` (P1's flip) — keep that in mind if you touch
  attribution tests.

**P7 — code + spec to touch if the worker complies (flip default-on):**
- `qd/engine.py` — `fixture_provenance = bool(args.get("fixture_provenance"))` (~line 848);
  the check loop ~line 1375; `_PROVENANCE_HEADER = "captured-from:"` ~line 90. Flip the
  default the same way P1 did: `args.get("fixture_provenance", True)`, opt-out via config.
- `specs/engine_spec.py` — the `FixtureProvenance` class (~line 1709) pins the current
  opt-in behavior; mirror P1's pattern (harness opt-out via `QWEN_DELEGATE_CONFIG`, a new
  `test_default_on` asserting the production default).
- `qd/schemas.py` — `fixture_provenance` param description (~line 128) says opt-in; reword.
- `docs/HLD.md` — note the default flip (P1's pattern: "default ON since probe P7").

**Shared (already correct, do not need editing — just context):**
- `qd/limits.py` — `Progress` / `read_progress` (the heartbeat sidecar; P8 vindicated it).
- `qd/invoke.py` — `BurnLimit` / `usage` fallback (P5; the fallback is still unproven — a
  noted open item, NOT a v0.5 blocker).
- `skills/delegation/SKILL.md` — the push+poll heartbeat recipe (already committed).

**The gate (unchanged):** `bash ci/run-specs.sh` must be exit 0 before any commit. Run it
after every change; 776 tests is the green baseline (P1 added one).

## Done — do not redo

| commit | what |
|---|---|
| `e030857` | Phase 6 playbooks |
| `c705f0b` | README + docs/USAGE rebuilt against v0.5 |
| `4c84978` | lld-principles on-demand in manager + trim (was ~52% of weekly usage) |
| `a9ee49f` | heartbeat recipe (later rewritten to push+poll in `c50aa64`) |
| `c50aa64` | P1: autoedit_via_hook default-on + push+poll heartbeat recipe |
| `0fd861b` + `17b61b4` | runbook: P1 result, then P2/P4/P5/P8 findings |

Skill-usage root cause + heartbeat mechanism: memory
(`lld-principles-cache-cost`, `qwen-heartbeat-mechanism`, `user-claude-sub-ttl`,
`feedback-test-off-claude`).

## Standing rules (unchanged)

- One commit per task, on `v0.5-field-report`, never to master.
- Suite exit 0 before any commit. `specs/*_spec.py` are permanent gates.
- Runtime code stdlib-only. Comments say WHY (the measured failure), never what.
- **Tone rule:** no sharp Qwen-skepticism in agent-facing surfaces; mechanism statements only.
- **Heartbeat:** push (`run_in_background` `until [ -f receipt ]`) + poll watchdog (long
  runs only). Both read only what the submit's `RECEIPT:`/`HEARTBEAT:` lines advertise.
