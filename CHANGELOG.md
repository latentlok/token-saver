# Changelog

## 0.5.0 — 2026-07-29

The async-delegation release. The workflow is now submit-and-poll by default: a
delegation returns immediately with a run id, a receipt path, and a heartbeat file;
the caller does other work and reads the receipt on completion. A free local model
builds; a smart model orchestrates and verifies through an objective gate.

### Workflow
- **Async by default.** `qwen_delegate` submits and answers in seconds; `wait: true`
  blocks for the receipt. Result contracts (`result_schema`), `retry_of` (cold corrected
  re-run), and a self-updating capability map.
- **Playbooks (briefs).** Briefs live in the repo, versioned by git, sent by name
  (`brief_file` + `vars`); `chain: true` compiles `## Step <n>` sections into a chain.
- **Receipt diet.** Receipts carry only what a caller acts on; verbose internals moved
  to `runs.jsonl`.

### Safety & attribution
- **C10 write attribution.** A PreToolUse hook resolves and logs every worker write
  path; the engine reverts only what it can attribute, so a caller's concurrent edit is
  never rolled back.
- **`autoedit_via_hook` default ON** (probe P1). Attribution exists outside scoped mode
  by default; opt out per-project with `"autoedit_via_hook": false`. Behaviorally free
  vs plain auto-edit (~1s overhead, same outcome/gate).
- **Gate hygiene.** `preflight_expect` (red/green/any), `verify_timeout_sec`,
  `gate_slow` detection, `report_dont_fix` (diagnose, don't repair).
- **Scoped shell.** Path-confinement for writes, an exact-verify + read-only shell
  allowlist, deny-by-shape for unknown effect-bearing tools (`EFFECT_KEYS`).

### Features
- `fixture_provenance` (opt-in) — fixtures must carry a `captured-from:` source line;
  imagined fixtures were the field report's worst defect class.
- `findings` / `strays` / `result_schema` / `touch_scope` / `shell_allow`.
- `trust` dial: `self` (delegate's own suite is the gate) / `verified` / `auto`.

### Heartbeat (C11)
- `progress.json` sidecar updates **per streamed record** by default (the default
  10M-token burn budget wires it on every run). Push+poll recipe in the `delegation`
  skill: a background `until [ -f receipt ]` for completion, a `ScheduleWakeup`
  watchdog on `progress.json` for liveness. A stall shows as a frozen `updated` timestamp.

### Live-probe field report (P1–P8)
- P1 autoedit_via_hook: passed, default-on. P3 edit/replace field names: match, no change
  (executor's `edit` carries `file_path`; content fields immaterial — named tool, path
  attribution). P5 BurnLimit: fires correctly mid-stream. P8 progress.json: per-record,
  unthrottled.
- Recorded non-blocking open items: P5 `usage` fallback path still spec-only (this
  endpoint's stream kept `usage` intact); P7 `fixture_provenance` kept opt-in (the
  `.src` sidecar is honored for binaries only — comment-free text formats like JSON have
  no compliant route); P2 MCP-namespaced fencing spec-only (worker declined to call an
  MCP tool in test runs); streaming mode does not emit tool counts (`tools.calls` reads 0).

### Skills & docs
- `lld-principles` loaded on demand in the manager (was ~52% of weekly Claude usage;
  trimmed ~18%). README + `docs/USAGE` rebuilt against v0.5. `docs/HLD.md` contracts
  amended for the async flip.

---

## 0.4.1

- Receipt and runlog refinements; scoped-shell hardening.

## 0.4.0

- Scoped shell, BurnLimit, gate pre-flight, trust dial.
