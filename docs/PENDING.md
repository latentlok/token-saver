# Pending — the v4 queue

v3 (the architect model at L5) is complete — executed, measured, shipped; its records
live in [archive/](archive/). This is the ONLY forward-looking doc: what v4 draws
from. Three buckets: work items, further context trims ("leaner files"), and worker
speed (wall-clock — the one axis v3 deliberately traded away).

## Work items

| item | what / why | unblocked by |
|---|---|---|
| Narrative architecture doc for v2 engine | The v1 walk-through was archived (archive/ARCHITECTURE.md); HLD.md is the accurate reference but terse. Write the reader-friendly walk-through of the qd/ engine when packaging demands it. | writing time / P4 |
| Standing worker discipline → server, not task text | The constant instruction block every architect task now carries (graphify-before-grep, own tests under tests/, never break the suite, do NOT stop after planning) is worker workflow, not task content. Move it to a server-injected suffix on `trust="self"` runs — injection into the task keeps it compaction-safe (QWEN.md alone is not; compaction eats it), and the architect stops paying/authoring it per task. See the note in USAGE.md's worked example. | user go — next up |
| Routing floor | When NOT to delegate: a round-trip costs ~500-token floor, so tiny inline edits shouldn't route. Needs the threshold put into CLAUDE-snippet + skill. | more small-task data points (the +28% loss is the only one) |
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
| Green receipt (R2 done) | ~50–150 | little | C2 lines (GRAPH/REFS) could go flag-only |
| Delegation + architect skills | ~1.3k + ~1.2k when loaded | some | loaded per-use, not resident; low priority |
| CLAUDE-snippet | ~350 resident/project | little | it IS the routing policy; trim only with the routing floor |

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
- **dispatch_spec is excluded from CI.** Its assertions are wall-clock based and
  flake under load, so the cross-process endpoint locking added in 0.4.0 is not
  covered by the automated suite. Either make the assertions load-independent or
  split the timing tests out from the protocol ones.
- **`workers` (best-of-N) is advertised and not implemented.** It is in the tool
  schema and documented in the delegation skill; `qd/engine.py` never reads it.
  Either build it or remove the claim — an advertised parameter that silently does
  nothing is the same class of defect as a metric that reads 0 without measuring.
- **Per-token records would replace the decode-rate knob.** Requesting partial
  messages gives sub-second stall granularity instead of hours, making
  `decode_tps` unnecessary. Cost: they must be filtered out of both the on_line
  callback and the accumulated buffer, or they become noise and memory.
- **`detect_test_cmd` still cannot place this repo.** 0.4.0 added `test_command` /
  `test_dir` so any project can say where its tests are, but the detectors
  themselves gained nothing for a `specs/*_spec.py` layout.
