# Pending — the v4 queue

> **v0.6 friction-ledger work is tracked separately in
> [PLAN-v06-ledger.md](PLAN-v06-ledger.md)** — that is the live queue for everything
> the field report turned up. This file remains the longer-horizon v4 list.

v3 (the architect model at L5) is complete — executed, measured, shipped; its records
live in [archive/](archive/). This is the ONLY forward-looking doc: what v4 draws
from. Three buckets: work items, further context trims ("leaner files"), and worker
speed (wall-clock — the one axis v3 deliberately traded away).

## Work items

| item | what / why | unblocked by |
|---|---|---|
| Narrative architecture doc for v2 engine | The v1 walk-through was archived (archive/ARCHITECTURE.md); HLD.md is the accurate reference but terse. Write the reader-friendly walk-through of the qd/ engine when packaging demands it. | writing time / P4 |
| ~~Standing worker discipline → server, not task text~~ | **DONE (0.5.0-dev)** — `task_suffix` in `.qwen-delegate.json` is appended to every task server-side (`\n\n---\n<suffix>`), so it rides the compaction re-injection exactly as designed. Not limited to `trust="self"`: it applies to every run in the project, which is what "standing" turned out to mean. A stored brief keeps the task WITHOUT the suffix, so a `retry_of` never stacks copies of it. | — |
| Routing floor | When NOT to delegate: a round-trip costs ~500-token floor, so tiny inline edits shouldn't route. Needs the threshold put into CLAUDE-snippet + skill. **Half done (0.5.0-dev)**: the delegation skill now carries a routing decision table with a stated threshold (≤20 lines + known location + an existing fast gate ⇒ edit it inline). The CLAUDE-snippet half waits on the U2.7 capability-map rewrite. | more small-task data points (the +28% loss is the only one) |
| Skeleton-flesh conformance | Deterministic check of code vs DESIGN.md (interface match both ways, placement, boundary edges) as a receipt line — the drift test the benchmarks only partially covered. `grade/stage1.py` is the prototype; generalize its manifest to read DESIGN.md. | user go |
| Same-model cross-examiner | Does a FRESH Qwen session catch the builder's blindspots, or share its priors? One free query per green module. Cheap experiment, answers whether L5 gets a free second opinion. | user go |
| Semantic graph for orientation | The untested graphify layer: one-time LLM index (local backend, offline) of an unfamiliar repo, then measure whether the architect's decomposition step improves. Point-lookups are settled (worker-side, L5W); orientation is not. | a genuinely unfamiliar large repo to test on |
| Trust L1–L4 | The intermediate dial stops. Design only after real use shows where `self` isn't enough but `verified` is too expensive. | field experience |
| Capability slider outward | Weaker delegate (finer decomposition) and stronger delegate (API-class, coarser tasks + the Fable-architect/Opus-builder corner). C7 profiles are the hook; nothing tested. | a second executor profile |
| Bug-resolution primitive doc | The C flow (symptom → self-authored repro → fix) works; write it into the skill as a first-class recipe with the repro-must-fail-first enforcement question decided (measure first: does the ratchet suffice?). | more C runs at depth (deep-diagnosis bugs; n=2 shallow so far) |
| Overnight runner | Evening design session → task queue → drain overnight → morning digest. | explicitly parked by user; do not build until green-lit |
| P4 packaging | Executors presets, installer, quickstart for "anyone with a local model". USAGE.md is the seed. | loop proven in daily use |
| P3 remainder | The dedicated lean-architect *agent* (stripped built-in toolset, ~35–50k base saving) — out of scope for regular sessions by user decision; open for a dedicated-agent mode. | discussion with user |

## Leaner files (context still on the table)

| file / surface | today | trimmable | note |
|---|---|---|---|
| Worker baseline (QWEN.md template + Qwen's own 88 tool schemas + system) | ~19–22k per delegation | maybe −3–5k | free tokens, but it's pure prefill wall-time on every session (~2s at measured rates) and compaction headroom; template prose can compress |
| `HANDOFF_SUFFIX` + retry prompts (engine) | ~150–400/attempt | some | worker-side; matters only for wall |
| MCP schema payload (R1 done) | ~1.2k resident | −~400 more | param descriptions could go one-line-only once skills are the norm |
| Green receipt (R2 done, 0.5 diet) | ~50–150 + the fixed `RUN:` line | little | **Superseded by the receipt diet (0.5.0-dev)**: the trim was spent, not banked — grouped `SHELL APPROVAL NEEDED` (was ~40% of a red receipt), one fixed `RUN:` telemetry line, and a cap that is finally ENFORCED (C2 drops → result tail to 200 → verify tail to 400). Flag-only GRAPH/REFS is moot: every C2 block now has a pinned drop priority. |
| Delegation + architect skills | ~1.3k + ~1.2k when loaded | some | loaded per-use, not resident; low priority |
| CLAUDE-snippet | ~350 resident/project + 8 lines (U5.3) | little | it IS the routing policy; trim only with the routing floor. Phase 5 spent 8 lines on async submit / `wait` / `retry_of` / `result_schema` — deliberate, because async-by-default is not optional knowledge for a caller. **Owed**: the U2.7 rewrite into a compact capability map must ABSORB those lines, not add to them. The block is MANAGED now (`qd/setup.py` rewrites it once per plugin version), so the budget is enforced at the template, not per project. |

## Qwen speedups (wall-clock; tokens already won)

Measured anchors: decode **70 tok/s**, prefill **10,882 tok/s**, 98% of a delegation
is model inference, baseline prefill ~19–22k/session, Ollama serialized at 1.

| lever | expected effect | cost/risk |
|---|---|---|
| Serving engine: vLLM (or llama.cpp server) instead of Ollama | biggest single lever: better batching/KV handling, typically 1.5–3× decode | setup; redo the C7 endpoint config; re-measure the timing model |
| Speculative decoding (small draft model) | 1.5–2.5× decode on code (high acceptance rates) | needs engine support (vLLM/llama.cpp); VRAM for the draft |
| Quantization check (current quant unknown) | if running high-precision, a Q4/Q5 variant ≈ big decode win at small quality cost — re-run the L5 ladder to confirm quality holds | quality re-validation (the benchmarks are the harness for this) |
| Trim worker prefill (leaner QWEN.md, fewer Qwen-side MCP tools) | ~2s/session + faster session starts | small |
| `OLLAMA_NUM_PARALLEL` + endpoint `parallel_max` 2 | true parallel fan-out for `batch` | halves context window to ~96k — safe per measured peaks (24–45k typical), decided config-only in HLD probe 4 |
| Session reuse for tight follow-ups | skips re-prefill of the baseline | already supported (`session_id`); discipline, not code |
| Smaller model for low-level tasks (capability slider down) | faster decode on mechanical work | pairs with finer decomposition; untested |

Priority order if wall-clock starts to matter: engine swap → speculative decoding →
quant check, in that order — all three are re-validated for free by re-running the
existing benchmark arms (`grow`, `existing_large`, `bugs`).

## Done (was parked): compaction is refused, not survived

Shipped as `on_compaction="refuse"` (the default) — the run stops on a compaction,
its output is discarded ungraded, and the receipt tells the orchestrator to split
the task. `compact_hook.py` also exits 2 on PreCompact to ask the executor to block.

**What could not be done:** auto-compaction cannot be turned OFF. qwen's
`autoCompactThreshold` is a fraction of the window with a 0.01 floor, so it only
moves the trigger; and the auto-compaction call site reads only the hook's
additionalContext, so the documented "exit 2 blocks compaction" is unverified there.
The stop is the mechanism that holds; the block is best-effort. If upstream ever
honours it, the refusal gets cheaper (no summary is ever built) with no code change.

Still open: `compaction_threshold` on a profile is unset by default. Lowering it
buys an earlier stop with more headroom, but ONLY if the block is honoured —
otherwise it just compacts more often. Measure the block on a real run before
setting it.

## Open after 0.4.0

Carried from the release notes so they stay visible:

- **Streaming loses tool and line counts.** The streaming adapter's result record
  omits `stats` entirely. Tokens are recovered from the top-level `usage`, but
  `tools` and `lines_added`/`lines_removed` read 0 in stream mode with no way to
  tell that from a measured zero — the exact ambiguity this release spent the day
  removing elsewhere.
- **The `usage` fallback has never run for real.** Spec-covered only; it gets its
  first live exercise the moment a delegation attaches a limit and streams.
- **dispatch_spec is excluded from CI** — narrowed, no longer a coverage hole. The
  load-robust serialization claims (cross-process endpoint slot, cross-process repo
  lock, serial policy, guard shape) were split into `specs/serialize_spec.py`, which
  CI runs. What stays excluded is only the OVERLAP half ("these two genuinely ran
  concurrently"), which a loaded box can fail honestly.
- **`workers` (best-of-N) is advertised and not implemented.** It is in the tool
  schema and documented in the delegation skill; `qd/engine.py` never reads it.
  Either build it or remove the claim — an advertised parameter that silently does
  nothing is the same class of defect as a metric that reads 0 without measuring.
  *Still open after 0.5.0-dev, and deliberately untouched there: the round added
  params, and closing this one is a build (or a schema deletion), not a doc pass.*
- **Per-token records would replace the decode-rate knob.** Requesting partial
  messages gives sub-second stall granularity instead of hours, making
  `decode_tps` unnecessary. Cost: they must be filtered out of both the on_line
  callback and the accumulated buffer, or they become noise and memory.
- **`detect_test_cmd` still cannot place this repo.** 0.4.0 added `test_command` /
  `test_dir` so any project can say where its tests are, but the detectors
  themselves gained nothing for a `specs/*_spec.py` layout.

## Deferred live probes (0.5.0-dev) — endpoint required, none blocked the build

The field-report round was built entirely against the hermetic spec harness because the
Qwen endpoint was busy. Everything that depends on unverified live behavior shipped
dark. One line each, with what it gates:

- **P1 — auto-edit as yolo + observe-hook parity.** Gates turning `autoedit_via_hook` on
  by default, which is the only way attribution (C10) exists outside `scoped`. Specs
  prove flag-off is byte-identical argv/env; nothing proves flag-ON behaves like plain
  auto-edit on a real worker.
- **P2 — PreToolUse deny for MCP-namespaced tools.** Gates whether the scoped hook can
  fence the worker's own MCP tools at all; today an unknown tool is judged by the shape
  of its input (`file_path`/`path`/`command`/`content` deny, the rest allow + log
  `ungated:<tool>`).
- **P3 — real `edit` / `replace` tool_input field names.** Gates the completeness of the
  write log: a field name the hook does not read is a worker write nobody attributes,
  and C10's policy only reverts what it can attribute.
- **P4 — cached-token counters.** Gates the `BURN:` cache clause (HEAVY binds on
  `prompt − cached`); on an endpoint that never reports `cached`, that branch is dead
  code that has never rendered.
- **P5 — `usage` fallback + BurnLimit on a real stream.** Carried from 0.4.0. Gates
  trusting stream-mode token totals and every live limit built on them.
- **P6 — worker delete-command phrasing.** Gates `allow_delete` and stray auto-clean,
  which are DESIGNED and NOT BUILT: `strays` today is a receipt line, a `RUN:` count and
  a run-log integer, nothing more. Build only once real phrasings are known — a delete
  parser guessing at commands is the one bug class with no rollback.
- **P7 — fixture-provenance compliance loop.** Gates flipping `fixture_provenance` to
  default-on. The open question is behavioral: does a worker told "capture it or name
  its source" comply, or thrash against the check until the attempts run out?
- **P8 — `progress.json` write cadence.** Gates advertising the heartbeat as a liveness
  check with a stated interval. The sidecar's shape is spec'd (C11); how often a real
  stream actually writes it is not measured.

## API-expansion readiness (0.5.0-dev assessment)

Swapping in an API-class executor is **config only** — no code path is local-specific:
an `~/.qwen-delegate/executors.json` profile with a complete `settings_overlay`
(C1: only `QWEN_CODE_SYSTEM_SETTINGS_PATH` with a full `modelProviders` entry switches
endpoint/model), its `price_in_per_mtok` / `price_out_per_mtok` so `COST:` and the run
log stop reading $0, an `endpoints` entry with a real `parallel_max`, and
`"dispatch": "parallel"` to stop pinning it to one request. Worktrees, per-endpoint
semaphores, batch fan-out and the cross-process file slots were all built for capacity
that does not exist locally and have never been exercised against any.

Open gaps before that is a supported path, not an experiment:

- `workers` (best-of-N) is still unimplemented — the one fan-out shape an expensive
  endpoint would actually pay for (N candidates, first gate-pass wins).
- Streaming loses `tools` and `lines_added`/`lines_removed`; any run with a limit
  attached streams, so on a metered endpoint the telemetry gaps land exactly where cost
  attribution matters.
- The `usage` fallback (P5) has still never run for real, and on a priced endpoint it is
  what the cost figure is computed from.

## Delegation-pattern gaps deliberately deferred

- **Detached runner surviving session death.** A submitted run lives on a daemon thread
  of the MCP server process, so ending the Claude session kills it mid-flight
  (`runs_in_flight()` reports this honestly rather than preventing it). A true detached
  runner — its own process, receipts and heartbeat unchanged — is what "queue it and
  close the laptop" needs, and it overlaps the parked Overnight runner above. Not built:
  the async submit already bought the interactive half of the win.
- **Judge / best-of-N verification for ungateable work.** Everything here rests on a
  command that exits 0. Work with no such command (prose, design docs, judgement calls)
  is currently out of scope rather than degraded gracefully — an LLM judge or N-candidate
  vote is the obvious shape, and both are unmeasured here. Related: "Same-model
  cross-examiner" above, which is the cheap first experiment.
