# Changelog

## 0.5.1 — 2026-08-01

The vLLM cutover release: everything that shipped dark in 0.5.0 exercised against a
live endpoint, plus the two fences and two accounting fixes the exercise demanded.
Runbook in `handoff-v05/VLLM-ROUND.md`.

### vLLM cutover round (A1–A4, B1–B4, C1)
- **Executor profiles proven live.** Endpoint/model/sampling pinned per profile via
  `settings_overlay` → provider `generationConfig.samplingParams`; machine-default
  resolution and overlay effectiveness verified against a real vLLM endpoint.
- **Parallel dispatch proven live.** `endpoints.<n>.parallel_max: 2` +
  `dispatch: "parallel"`: two gated builds fan out across worktrees and overlap on
  continuous batching with per-item receipts and sane MERGE lines; the machine-wide
  repo lock field-confirmed serializing two OS processes in-tree on one repo across
  different endpoints; worktree config-default + main-tree co-work confirmed in a
  real repo.
- **Scoped MCP fence (closes P2).** The worker does call MCP tools
  (`mcp__<server>__<tool>`), and input shape says nothing about what the server does —
  a `{url}` input fetched the network inside the fence. Scoped mode now denies
  `mcp__*` by default; allow per-tool with `mcp_allow` name regexes (call arg >
  project config, stored in briefs). Receipts render `MCP APPROVAL NEEDED` naming
  the right knob. Observed auto-edit keeps record-don't-gate, byte-identical.
- **Accounting fixes.** The run ledger records the RESOLVED executor profile (a
  machine default no longer mislabels every run `qwen-local`); the token-provenance
  ladder gains `usage` as coarsest source (closes P5 — streamed runs no longer read
  as unmeasured).
- Cached-token reporting on vLLM needs `--enable-prompt-tokens-details` server-side;
  absent it, `cached` reads 0 and the BURN cache clause stays inert (server note,
  not a client defect).

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
  P2 and P5 were closed in 0.5.1.

### Skills & docs
- `lld-principles` loaded on demand in the manager (was ~52% of weekly Claude usage;
  trimmed ~18%). README + `docs/USAGE` rebuilt against v0.5. `docs/HLD.md` contracts
  amended for the async flip.

---

## 0.4.1

- Receipt and runlog refinements; scoped-shell hardening.

## 0.4.0

- Scoped shell, BurnLimit, gate pre-flight, trust dial.
