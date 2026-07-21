# M0 probe results

Measured 2026-07-22, per HLD §8. Raw artifacts in scratch (`~/scratch/graphify-probe`,
`~/scratch/graphify-mini`); rerun recipes inline.

## Probe 1 — graphify semantic backend on local Qwen: **PASS**

`graphifyy` 0.9.23 (= github.com/Graphify-Labs/graphify) has a native `ollama` backend.
Verified end-to-end against the real endpoint: semantic extraction ran via the snowy
proxy, real token accounting (`815 in / 2,056 out, est. cost $0.0000`).

Recipe (the qwen-local graph profile):

    uv tool install "graphifyy[ollama]"        # the openai client extra is REQUIRED
    OLLAMA_BASE_URL=http://snowy.tailcdc241.ts.net:11434/v1 \
    OLLAMA_MODEL=qwen3.6:27b-agent \
    OLLAMA_API_KEY=<proxy key> \
    GRAPHIFY_MAX_WORKERS=1 \
    graphify <path> --backend ollama

- `GRAPHIFY_MAX_WORKERS=1` is mandatory on the 1-worker Ollama (it fans chunks with a
  thread pool otherwise).
- **Default pipeline warning:** without `--backend`, graphify's semantic pass runs via
  *Claude Code subagents* (its skill integration) — i.e., costs Claude tokens. Always
  pass `--backend ollama` in our wiring.
- Bonus: graphify deterministically drops LLM-hallucinated nodes attributed to files it
  didn't dispatch (#1895) — a built-in guard matching our "semantic = lead" stance.

## Probe 2 — incremental API shape: **ANSWERED, better than assumed**

- `graph.json` carries **`built_at_commit`** natively — graphify records the indexed
  sha itself; our `qd/graph.py` sidecar wraps it (staleness = git diff against it)
  rather than inventing sha tracking.
- `graphify update <path>` = structural re-extract, **no LLM**, measured **1.9s** for
  this whole repo (640 nodes / 869 edges / 34 communities). Cheap enough to run
  synchronously at verdict time, exactly as the HLD assumed.
- Incremental *semantic* layer: `graphify-out/manifest.json` tracks per-file
  `{mtime, ast_hash, semantic_hash}`; re-running extract skips unchanged hashes
  (semantic cache). So "re-index only the blast radius" = just re-run extract; the
  manifest does the pruning.
- `affected.py` provides BFS over typed relations from changed nodes (impact analysis).
- Also present: `graphify watch` (we stay event-driven; unused), git hooks
  (`hook install` — post-commit/checkout; may replace part of our post-verdict wiring,
  evaluate at M5).

## Probe 3 — retrieval quality: **PASS** (semantic-at-scale confirmed)

At-scale semantic extract of this repo via local Qwen: 18 doc files enriched,
58,699 in / 8,401 out tokens, $0. After enrichment, **concept queries work**:
`explain "spec guard"` → a `concept` node ("Spec Guard Mechanism") referenced by
Findings, Principles, and Worker Rules — the design-rationale layer the one-loop
workflow needs. Code nodes fully survived the curated rebuild (`server_blast_radius`
et al. present; 640→482 was dedup/curation, not loss). Residue to know: the model
mis-attributed 5 nodes (deterministically dropped by graphify's guard) and omitted 4
doc files (README + archives; a re-run retries them) — the semantic layer stays a
lead, structurally guarded. Pre-enrichment detail below still holds:

- `graphify explain "blast_radius"` on this repo: exact source line (L770 — verified
  correct), true callers/callees, linked rationale node — all `[EXTRACTED]` with real
  line numbers. Deterministic coordinates are trustworthy, the precise thing Qwen
  fabricates.
- Weakness: fuzzy/concept queries (`path "qwen_delegate" "render"`) → ambiguity
  warnings, no path. **Skill rule: query the graph by symbol names, not concepts** —
  same lesson as qwen_investigate.
- `graphify-mcp` exposes a rich query surface: `query_graph`, `get_node`,
  `get_neighbors`, `get_community`, `god_nodes`, `graph_stats`, `shortest_path`, PR
  tools. Claude-side querying confirmed as MCP.
- Full semantic extract of the repo clone via ollama is running; judge summary quality
  when it lands.

## Probe 4 — Ollama parallelism: **SETTLED BY DECISION** (no test)

User decision 2026-07-22: 1 worker stays; `parallel_max: 1` is config mirroring the
backend; design carries parallelism; enabling it later = `OLLAMA_NUM_PARALLEL` + the
endpoint's `parallel_max` after the run log's peak-context column clears 96k. KV-cache
facts (queue eviction, model-swap reloads) are handled structurally by the
whole-subprocess endpoint semaphore.

## Probe 5 — client-side MCP concurrency: **SERIALIZED** (measured)

Stub stdio MCP server logging request arrival; headless `claude -p` told to make two
parallel tool calls in one message.

    15s calls:  A arrived 13.4s, resp 28.4s; B arrived 28.40s  (13ms after A's resp)
    130s calls: A arrived 6.0s, resp 136.0s; B arrived 136.0s; total 266s = sum

The client does not dispatch call B until response A returns, even past the 120s
auto-background threshold (headless mode). **Design consequence: multi-call fan-out
cannot be relied on; the guaranteed mechanism is a `batch` parameter** — one MCP call
carrying N delegation items, fanned across worktrees *inside* our concurrent server
(HLD C9 updated). Whether the interactive session or subagent-shared connections
multiplex differently is untested; if they do, multi-call fan-out becomes a bonus, not
a dependency. Re-validate during M4 wiring.

## Probe 6 — per-process endpoint/model override: **ANSWERED** (settings injection only)

Four sub-probes against qwen-code 0.19.11 with settings v4 (`modelProviders`):

| mechanism | result |
|---|---|
| `-m <model>` flag | **silently ignored** (model not in providers registry → falls back) |
| `OPENAI_BASE_URL` env | **ignored** (authType 'openai' + v4 registry wins) |
| `QWEN_CODE_SYSTEM_SETTINGS_PATH` with bare `model.baseUrl` | read, then **validated away** ("no longer matches any provider... using the first id match") |
| same, with **complete `modelProviders` entry** + `model` + auth | **WORKS** — request went to the injected endpoint (connection error on dead port in 3.3s, `totalErrors: 1`) |

So `qd/profiles.py` renders a full settings overlay per profile (C1 `settings_overlay`),
exported via `QWEN_CODE_SYSTEM_SETTINGS_PATH`; the user's `~/.qwen` is never touched.
`qwen-local` has a null overlay (inherits user config untouched).

Incidental re-confirmations during probing: the `managed-auto-memory-extractor`
overhead fired on every one-word run (~9.7k of ~28k tokens, ~35%); and a run told to
reply exactly "ping" replied **"pong"** — Qwen unreliability in one word.
