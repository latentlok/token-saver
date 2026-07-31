# vLLM cutover round — live findings (snowy)

Endpoint: `http://snowy.searobin-dragon.ts.net:11434/v1` (Caddy bearer proxy),
serving `unsloth/Qwen3.6-27B-NVFP4` on vLLM 0.26.0, `max_model_len` 226,144.
Same discipline as P1–P8: one variable per run, receipts read from the file,
runtime changes only where a result demands one (each its own commit).

## A1 — vLLM executor profile

**RESULT (2026-07-31): GREEN — cutover profile works end-to-end.**
`~/.qwen-delegate/executors.json` now carries `vllm-snowy` (machine default):
argv is the same qwen-local template; `settings_overlay` pins the provider
entry (baseUrl → snowy, `envKey: VLLM_TOKEN`) and `generationConfig` with
`contextWindowSize: 226144` + `samplingParams` {temperature 0.6, top_p 0.95,
top_k 20, max_tokens 65536} — `samplingParams` is a first-class
generationConfig field in qwen-code 0.19.11 and, once set, is sent wholesale
per request. Trivial gated delegation through the default-resolved profile:
`STATUS: success`, 1 attempt, 25s wall / 9s GPU, peak 11% ctx.

Overlay effectiveness proven by negative control, not assumed: a copy-profile
pinning a nonexistent model id fails with vLLM's own
`404 The model 'nonexistent-model-xyz' does not exist` — so the overlay, not
the inherited `~/.qwen` user settings (which also point at snowy), governs
the worker.

**Bug found + fixed (`2808cbc`):** the run ledger recorded the call arg, not
the resolved profile — with a machine default every default-routed run was
labeled `qwen-local`. `delegate` now records `profile["name"]` (queries
already did); spec pins it.

## A2 — context window re-verify

**RESULT (2026-07-31): VERIFIED, no drift.** `/v1/models` reports
`max_model_len: 226144`; `python3 -m qd.doctor --verified 226144` re-stamped
it; matches the value already in `~/.qwen-delegate/config.json`. Earliest
compaction on this window: 193,144 tokens (85.4%).

## A3 — priority passthrough

**RESULT (2026-07-31): NO per-request channel — derived-priority design
SHELVED, as pre-agreed.** qwen-code 0.19.11's OpenAI path contains no
`priority` anywhere; nothing per-request can reach vLLM's
`--scheduling-policy priority`. The endpoint slots stay the only scheduler.

Surviving coarse option, recorded not built: the provider entry's
`generationConfig.extra_body` is spread verbatim into every request body
(`buildRequest`, chunk-WMNXXRGL.js), so a second executor profile with
`extra_body: {"priority": N}` gives static two-lane scheduling with zero
runtime changes. vLLM accepts the field today (HTTP 200); scheduling on it
needs `--scheduling-policy priority` server-side.

## A4 — token accounting shape

**RESULT (2026-07-31): usage arrives; P5 fallback exercised live and works;
P4 cache clause stays inert on this endpoint.**

- Non-stream: `usage.prompt_tokens/completion_tokens/total_tokens` present;
  `prompt_tokens_details: null` — **always**, even on a fully cache-warm
  identical repeat (2,417-token prefix, two calls). vLLM only surfaces
  `cached_tokens` with `--enable-prompt-tokens-details` server-side. Until
  snowy adds that flag, `cached` is 0 everywhere and the `BURN:` HEAVY
  cache clause (P4) stays inert — same as the Ollama round. Server-config
  note, not a client bug.
- Through qwen-code, every delegation streams (the progress sidecar needs
  the stream), the result record carries no `stats`, and tokens land via the
  **top-level `usage` fallback** (`invoke.py` parse_stats) — the carried P5
  path, never before exercised live: 50,025 in / 175 out across 2 calls,
  sane; peak_context (25,130) correctly read per-turn, not the sum.
- C4 confirmed live: `tools`/`lines_added`/`lines_removed` read 0 in stream
  mode (the A1 run wrote a file; its record says `tools.calls: 0`).
- **Bug found + fixed (`90645c2`):** accum_stats dropped the `"usage"` label
  to `"none"` — a measured run indistinguishable from an unmeasured one, on
  every streamed (= every live) run. Ladder now coarsest-wins:
  usage > blended > bySource > none; spec pins it.

Worker note for receipt-readers: this model routes output into a `reasoning`
field (thinking model); `finish_reason: length` on tiny max_tokens probes is
the reasoning burn, not an endpoint fault.

## B — parallel round

Config for the round: `endpoints.snowy.parallel_max: 2` in executors.json +
`dispatch: "parallel"` in `~/.qwen-delegate/config.json`; a second profile
`vllm-snowy-b` (own endpoint entry `snowy-b`) exists so cross-endpoint tests
can't be serialized by the endpoint semaphore.

## B1 — two concurrent qwen builds, live

**RESULT (2026-07-31): GREEN — parallel fan-out works on vLLM; the Ollama
failure mode did not reproduce.** `batch` of two independent gated items
(`worktree: "auto"` each) on the same repo: both receipts `STATUS: success`
attempt 1, batch wall 14.0s vs 7s+9s GPU serial-sum plus setup — the items
overlapped (monitor sampled both qwen worktrees existing simultaneously).
No mid-tool-call truncation, no api_errors; per-item receipts each carry
their own `WORKTREE:`/`MERGE:`/`BURN:` lines, distinct branches, both merge
commands sane. This was the unproven claim behind serial-as-default;
continuous batching holds at N=2.

## B2 — machine-wide repo lock, live

**RESULT (2026-07-31): GREEN — field-confirms serialize_spec claim 3.** Two
OS processes, one repo, both in-tree, DIFFERENT endpoints (snowy /
snowy-b), submitted the same second through the real `submit_delegate`
entry: alpha done +8.7s, beta done +22.2s — beta waited out alpha's flock
then ran its own ~13.5s. Serialized by the repo lock alone (the endpoint
slots were disjoint by construction).

Probe-harness caution recorded: calling `run_delegate_batch()` directly on
a single item takes NO guards (they live in `submit_delegate`) — the first
attempt of this probe overlapped for exactly that reason and was invalid.
Real callers always enter through the MCP tool, so this is a harness note,
not a hole.

## B3 — worktree config default, live

**RESULT (2026-07-31): GREEN — config-default isolation + co-work hold in
the real repo.** A delegation in qwen-delegate itself with NO worktree arg:
the receipt carried `WORKTREE:`/`MERGE:` unprompted (project
`.qwen-delegate.json` `"auto"`), `STATUS: success`. Co-work half: this
findings file was being edited in the main tree WHILE the worker ran —
edit intact after, `git status` shows only the caller's own change, the
worker's file exists only on its `qwen/rc21b03` branch, merge probe clean.
Probe branch + worktree discarded unmerged (junk content by design).
Bonus: this run's ledger row reads `executor: vllm-snowy` and
`token_source: usage` — both A-round fixes observed live.

## B4 — heartbeat under parallel load

**RESULT (2026-07-31): one sidecar per submit cwd, interleaved —
record-only.** A same-repo batch advertises ONE
`.qwen-delegate/progress.json`; both items' streams write it (atomic
per-write, always parses, session field flips mid-batch: records 4 → 7 with
the session changing). Last-writer-wins. "Is the batch alive?" still
answers (any pulse is a pulse); "is item A hung?" does not while item B
keeps pulsing — bounded by the batch, masked at most until the sibling
lands. The C11 submit-cwd fix keeps sidecars apart across DIFFERENT submit
cwds (two sessions/two repos); within one batch there is one file by
design. Not a build item for v0.5.

## Round outcome — the cutover config, now standing

A green + B green. What stays configured on this machine:

- `~/.qwen-delegate/executors.json`: `vllm-snowy` as machine default,
  `endpoints.snowy.parallel_max: 2`. The probe-only `vllm-snowy-b` /
  `snowy-b` pair was removed after B2 — endpoint entries should map 1:1 to
  real capacity, and one GPU with two advertised endpoints would let three
  requests in flight.
- `~/.qwen-delegate/config.json`: `verified_context_window: 226144`,
  `dispatch: "parallel"` — B1/B2/B3 are the evidence this is safe here:
  fan-out isolates via worktrees, in-tree co-location serializes via the
  machine-wide repo lock regardless of dispatch.

## C1 — MCP-namespaced tool fencing (carried P2): RESOLVED

**RESULT (2026-08-01): gap CONFIRMED live, fence BUILT (`8ca9c54`).** The
new worker (unlike the Ollama-era one that declined twice) called the MCP
tool on request — and scoped mode allowed it: `firecrawl_scrape`'s input is
`{url}`, no effect-shaped key, so the shape policy waved through a live
network fetch (evidence: fresh `scrapeId` from the local firecrawl service
in the probe file, `denials: 0`). Two facts pinned on the way: qwen-code
0.19.11 names MCP tools `mcp__<server>__<tool>` (verified via `-o json`
stats), and the worker discovers them dynamically through a `tool_search`
meta-tool.

The build, per P2's pre-agreed decision rule: scoped mode now denies
`mcp__*` by default, allowlist via `mcp_allow` name regexes (call arg >
project `.qwen-delegate.json`, stored in briefs, C9-additive schema param).
Observed auto-edit keeps record-don't-gate, byte-identical. Receipts give
MCP denials their own `MCP APPROVAL NEEDED` block naming `mcp_allow` (the
first live denial rendered under `SHELL APPROVAL NEEDED`, pointing at a
knob that does nothing for MCP). Field pair on vLLM: unlisted → denied by
name, surfaced, result marked suspect; `mcp_allow: ["^mcp__firecrawl__"]`
→ real scrape landed. Specs: scoped_hook_spec `MCPTools`, engine_spec
plumb-through, verdict_spec routing.

Carried items C2–C5 (LIVE-TESTS-NEXT §C) remain open — none is vLLM-gated;
C4 (streaming stats gap) got fresh live confirmation this round.
