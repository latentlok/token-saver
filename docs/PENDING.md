# Pending

The live list of what's not done. Consolidates and supersedes the parked ledger in
[PLAN-v3-l5.md](PLAN-v3-l5.md). Three buckets: work items, further context trims
("leaner files"), and worker speed (wall-clock — the one axis we deliberately traded
away so far).

## Work items

| item | what / why | unblocked by |
|---|---|---|
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
