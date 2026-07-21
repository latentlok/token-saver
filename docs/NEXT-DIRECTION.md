# Next direction: make Qwen a net token *saver*, not a subagent tax

> **Superseded by [DIRECTION-v2.md](DIRECTION-v2.md)** (2026-07-21). Kept for the
> economics measurements (break-even ~21k, subagent-tax analysis), which still hold and
> motivated the v2 redesign.

Handoff for a fresh session. **Start here — this is the question to ponder before any code.**

## The opening question

We proved the mechanism works but the accounting doesn't pay yet: single-task delegation is
**break-even at ~21k solo output and a loss below it**. The reason is structural — **launching
a manager subagent is the waste.** Each delegation pays a duplicated system prompt + a ~6k
agent-definition + ~1.2k skill + ~4k tool schemas, cached and re-read every turn, plus context
accumulation. That overhead cancels the free-Qwen output savings.

But the **server is already the free delegation engine** — it drives Qwen + gate + retries at
zero Claude tokens. The only Claude cost that *should* exist is: write the gate (short), fire
the tool, read the verdict (short, trimmable). A subagent that wraps that in a heavy persona is
the tax. So:

> **How do we restructure delegation so using Qwen actually SAVES tokens — instead of wasting
> them on a subagent launch?**

### Candidate restructurings to explore
1. **Delegation as a bare tool call, not a subagent.** The main agent (or a lightweight ~1k
   skill) calls `qwen_delegate` directly — killing the ~6k agent-def preamble and the
   duplicated system prompt. The discipline (gate-first, never-read-the-code, escalation
   ladder) moves into (a) the server's enforcement (it already refuses ungated/uncommitted
   runs) and (b) a concise skill / the tool contract, not a heavy persona. **Measure: does
   bare-call delegation beat solo on single tasks?** If yes, the subagent *was* the waste.
2. **Isolation only when it earns its keep.** A subagent's isolation is worth its preamble
   only when the delegation would pollute a long main session; otherwise use the bare call.
   (The parked #13 nuance, made a rule.)
3. **Flat context via graphify** (below) — Claude reads maps not code, so its context stays
   minimal on existing repos; compounds with the bare-call structure.
4. **Batch** — amortize whatever irreducible preamble remains across N similar tasks per call.

### The measurement that decides it
Same task, three ways — **(a) subagent delegation, (b) bare-tool-call delegation, (c) solo** —
compare Claude tokens. If (b) < (c), Qwen is a net saver and the subagent was the tax. This
supersedes the earlier solo-vs-subagent framing, which baked in the very overhead we should be
removing.

---

The graphify / existing-codebase work below is **one component** of the answer (flat context),
not the whole of it — the bare-call restructuring is the primary lever. Greenfield insight was
"Claude reads specs, not code"; the existing-codebase analog is **"Claude reads a map, not
code"** — graphify builds that map, Qwen builds and implements, gates arbitrate.

## Why graphify fits (verified from its README)

- **Structure layer is local + free:** tree-sitter AST extraction, deterministic, no LLM,
  ~40 languages. Nodes = functions/classes/packages/design-rationale; edges = `calls`,
  `imports`, `inherits`, `mixes_in`, `depends_on`, `references`, each tagged `EXTRACTED`
  (explicit) or `INFERRED` (graphify resolved it — treat as lower confidence).
- **Semantic layer runs on *your configured model*** — point it at **Qwen (local, free)**,
  so the *entire* index build is free. This is the "deploy Qwen to cheaply read/map the repo."
- **Consumable by Claude:** `graphify query "<question>"` returns a **scoped subgraph** (a
  few hundred tokens, not thousands of lines); `graphify path A B`; `graphify explain X`.
  Outputs `graph.json` (traversable), `GRAPH_REPORT.md`, `graph.html`. Ships an **MCP
  server** (stdio/HTTP) that Claude Code can call directly.
- Install: `uv tool install graphifyy && graphify install`; run `/graphify .`.

This is exactly the two-layer design we sketched (deterministic coordinates + Qwen
semantics), already built — so we integrate, not rebuild.

## End-to-end workflow (existing codebase)

1. **Index (once, free):** graphify builds the graph; configure its semantic backend to
   Qwen so nothing costs Claude tokens. Re-index incrementally on diff.
2. **Query (per task, flat context):** the manager calls graphify (`query`/`path`/`explain`
   or its MCP) to get a scoped subgraph — Claude's context stays flat on any repo size.
3. **Locate + spec:** from the subgraph, Claude finds the seams, verifies precise claims
   against source only where a decision hinges (INFERRED edges + Qwen summaries are leads,
   not truth — coordinates from tree-sitter are trustworthy), and pins the change as a
   Claude-authored spec/gate.
4. **Delegate:** `qwen_delegate` implements against the spec (Qwen, free); gate + the repo's
   existing suite verify (regression = don't-break-anything).
5. **Escalate:** the existing step-5 ladder (reflexion retries -> best-of-N -> failing
   snippet -> manager patches as last resort).

## Plugin integration work (for the new session)

- Add a "map via graphify, don't read the repo" step to `agents/qwen-manager.md` — the
  existing-repo analog of step 0 (`qwen_query`); graphify augments/replaces `qwen_query`
  for whole-repo orientation at scale.
- Decide the wiring: does qwen-delegate shell out to the `graphify` CLI, or does the manager
  call graphify's own MCP server running alongside? (Leaning: rely on graphify's MCP; keep
  our server focused on delegation.)
- Verify graphify's semantic backend can target the local Qwen endpoint (qwen CLI / ollama /
  OpenAI-compatible). If yes, indexing is fully free.
- Freshness: incremental re-index hook on file change / pre-delegation.

## The flat-context benchmark (what actually proves this)

Claim: solo Claude's context accumulates the whole codebase; delegation stays flat. With a
**manager attached to Qwen the context is *slower-growing*, not literally flat** — one
manager building N units accumulates subgraphs + specs + verdicts. Three arms separate it:

| Arm | orchestration | Claude context grows with |
|---|---|---|
| A solo | one Claude reads + builds | all code (+ existing code it must read) |
| B one-manager | one manager, N delegations | subgraphs + specs + verdicts (slower) |
| C isolated | fresh manager+Qwen per unit, discarded | module list + final reports (~flat) |

- **Metric:** Claude `cache_read` + total cost as a function of codebase / module count.
- **Current implementation measures Arm B (slower-growth).** To prove the *flat* claim, add
  **Arm C** (per-unit isolation — throwaway manager, or the parked stateless flat delegation).
- **Existing codebases widen the gap:** solo must *read the existing code* into context;
  delegation reads only graph queries. So this is where delegation should win decisively,
  not just tie.
- Control: pre-seed / share the design cost so we measure only the build phase.

## Current plugin state (shipped this session, all gated + mutation-tested)

- **#12** manager verifies by the gate, **never reads Qwen's code on green**; step-5 ladder
  ends in "manager patches the failing sliver as last resort" (`baf0d21` area).
- **#24 Reflexion** in the retry loop: diagnose-then-fix + repeated-failure "change approach"
  escalation. Free (`d0202d2`).
- **#26 Best-of-N** `workers` param (config + per-call, default 1, clamp [1,8]); gate-selected
  winner, short-circuits. Sequential now; parallel-via-worktrees parked (`3a849c5`).
- **#22 Trim** manager prompt + tool schemas, -14.5% (`baf0d21`).
- Research: `docs/RESEARCH-delegation-landscape.md` (`4e55918`). Archived backlog:
  `docs/BACKLOG-archived.md`.

## Key findings to carry forward

- Single gated task: delegation **break-even at ~21k solo output** (XL spreadsheet), lost
  **+34% at 16k** (calclang). Crossover roughly hit. See `token-saver-eval/results_xl/` and
  `results_large/`.
- Delegation is a **speed play at single-module** (~2× slower, recoverable via parallelism)
  and a **token play at multi-module / existing-codebase scale via flat context** — the thing
  to prove next.
- Design/HLD cost is **shared overhead** (paid either way); delegation's win is implementation
  volume + flat context, not the design.
- Aider borrow: keep Claude's **output** short (specs, not code); a **tight contract makes a
  weak executor reliable**; the gate replaces "architect reviews the diff."

## File pointers

- Plugin: `/home/dev-vishal/projects/qwen-delegate` — `server.py`, `agents/qwen-manager.md`,
  `*_spec.py` (gates), `docs/`.
- Eval harness: `/home/dev-vishal/projects/token-saver-eval` — `xl/` (spreadsheet task +
  mutation test), `results_xl/summary.json`, `results_large/`.
- graphify: https://github.com/Graphify-Labs/graphify

## Open risks

- graphify freshness (incremental re-index).
- Confirm graphify can point semantic enrichment at local Qwen (else indexing isn't free).
- Retrieval quality: does `graphify query` return the *right* subgraph for a change task?
- Never trust INFERRED edges / Qwen summaries for a decision — verify against source.
- Flat-context proof needs Arm C (isolation), not just the current manager.
