# HLD — qwen-delegate v2

High-level design for [DIRECTION-v2.md](DIRECTION-v2.md). Owns: system context,
component boundaries, the delegation lifecycle, and **every cross-module contract**
(pinned once here; module LLDs reference them, never redefine them). Per-module design:
[LLD.md](LLD.md).

## 1. What the system is

Claude is the architect: it writes designs and gates (specs). Qwen is the builder: it
writes code on free local compute. A small Python referee (`server.py` + `qd/`) runs the
builder against the gate and reports a short receipt. The builder's word is never
evidence; the gate decides. Trust level is a stubbed seam (`verified` only, for now).

**Backend: Python 3, stdlib only.** 98% of delegation wall-clock is model inference
(measured); the referee only launches processes, runs git, and shuffles JSON. It must be
trivially readable and editable by Claude and Qwen — plain Python is a feature.

## 2. Requirements

Functional:
- F1  Delegate a build task against a verify gate; iterate on real failures; receipt.
- F2  Read-only Q&A / codebase mapping / web research (firecrawl via Qwen's own MCP).
- F3  Concurrent calls: parallel queries; parallel delegations across repos; parallel
      delegations within a repo via worktrees; parallel best-of-N.
- F4  Executor profiles: swap the worker (local Qwen now, API-class later) via config.
- F5  Graph freshness: record indexed sha; refresh incrementally post-verdict; report
      staleness from git facts.
- F6  Refs pinning: fetched web references land on disk, reported in the receipt.
- F7  Self-test prefilter: the builder's own tests run before the gate, advisory only.
- F8  Run log v2: tokens, cost (0 for local, *recorded*), worktree/merge/graph facts.

Non-functional:
- N1  Receipt ≤ 3,000 chars; everything returned to Claude is capped and trimmable.
- N2  Zero dependencies; single `python3 server.py` entry; no packaging metadata.
- N3  Zero-trust invariants (DIRECTION-v2 §Invariants) hold at every stage.
- N4  The crane rule: the current server builds v2 and is deleted only at cutover.
- N5  Every module ships with a spec in `specs/` and survives a mutation pass.

## 3. Components

    Claude Code ──(MCP stdio)──▶ server.py         dispatch: threads, write lock,
                                    │               repo locks, per-endpoint semaphores
                                    ▼
                                 qd/ package        the engine (see LLD.md)
                                    │  profiles → worktrees → engine → invoke → qwen CLI
                                    │  gittree guards · verdict · graph · refs · runlog
                                    ▼
                                 git working tree / worktrees      (the database)

    Claude Code ──(MCP)──▶ graphify server          queries only; we never proxy them
    qwen CLI ──(its own MCP)──▶ firecrawl           web access; invisible to our server

Claude-side: `skills/delegate/` (the canonical loop, loaded by main session and by the
thin `agents/qwen-manager.md` shell alike), `commands/delegate.md`, tool descriptions
as the ambient capability map.

## 4. The delegation lifecycle (the "conversation")

Claude speaks at most three times; everything else is free-side.

    0. PRE-FLIGHT (optional, skill-driven, read-only — no new tool):
       qwen_query: "is this spec implementable / grounded / contradiction-free?"
       Reliable because write-less Qwen can't game; this is where Claude's design
       mistakes surface (measured: 0/3 honesty mid-build, clean answers read-only).
    1. BUILD (qwen_delegate):
       preconditions (rules file, git, clean specs)
       → worktree acquire (fan-out / best-of-N; else in-tree under repo lock)
       → gate pre-run (already-green ⇒ pass proves nothing; identical-output ⇒ gate_suspect)
       → loop ≤ max_iterations:
            invoke executor (profile argv)
            → self-test prefilter (F7, advisory — see C8)
            → gate
            → on red: feed real error text (+ prefilter output) back, resume session
       → guards: spec revert, blast radius, new-public-surface scan
    2. RECEIPT: v1 verdict format (frozen) + new lines per C2. NOTES carries what the
       builder flagged — a lead, never a mechanism.
       Post-verdict, async: graph refresh (F5), refs scan (F6), run log (F8).

## 5. Cross-module contracts (single ownership — pinned here)

**C1 — Profile** (produced by `qd.profiles`, consumed by invoke/engine/server/runlog/
bootstrap):

    {"name": str, "argv": [str],            # template; MUST contain "{task}"
     "env": {str: str},                     # merged over os.environ at launch
     "settings_overlay": dict | null,       # PROBED (6a-6d): -m flag and OPENAI_* env
                                            # are silently ignored under settings v4;
                                            # only QWEN_CODE_SYSTEM_SETTINGS_PATH with a
                                            # COMPLETE modelProviders entry switches the
                                            # endpoint/model. Rendered to a temp file at
                                            # invoke; its path exported via env.
     "price_in_per_mtok": num, "price_out_per_mtok": num,
     "endpoint": str,                       # names a C7 endpoint — the shared scarce
                                            # resource (one GPU/proxy/provider account)
     "rules_file": str,                     # default "QWEN.md"
     "altitude": "lld" | "hld",             # relayed to skill; server never interprets
     "defaults": {"workers": int, "max_iterations": int, "timeout": int}}

**C2 — Receipt grammar additions** (produced by `qd.verdict`, relayed verbatim by the
skill). *Amended by R2 (PLAN-v3-l5): the v1-frozen clause is retired — a clean green
receipt is compact (per-attempt trail, CONTEXT, TIME, TOOLS, CONTINUE, NEXT render only
on non-success or flags); red/flagged receipts keep full diagnostics.* The C2 additions
append, in this order, each only when applicable:

    NOTES: <text ≤200 chars>
    WORKTREE: <abs path>
    MERGE: git merge --no-edit <branch> && git worktree remove <path> && git branch -d <branch>
    MERGE: CONFLICT — contract overlap with <branch>; escalate, do not force
    GRAPH: fresh @ <sha7> | stale (<n> files since <sha7>) — refresh running
           | indexing | failed: <reason> | none — run graphify once to index
    REFS: <n> saved (<comma-joined names>)
    COST: $<usd 4dp> (<profile name>)       # emitted only when cost > 0

**C3 — Engine→verdict seam:** engine hands verdict a ctx dict with exactly these v2
keys added to the v1 ctx: `notes: str`, `worktree: {path,branch}|None`,
`merge: "clean"|"conflict"|None`, `graph_line: str|None`, `refs_added: [str]`,
`cost_usd: float`, `trust: "verified"`. Internals of ctx beyond the seam are engine's.

**C4 — graph sidecar** `.qwen-delegate/graph.json`:
`{"indexed_sha": str, "ts": iso8601, "status": "fresh"|"indexing"|"failed",
"reason": str?}`.

**C5 — run-log v2 record:** v1 record (see `specs/runlog_spec.py`) + `executor: str`,
`cost_usd: float`, `worktree: str?`, `branch: str?`, `merge: str?`,
`graph_refresh: {files: int, seconds: num, status: str}?`.

**C6 — naming:** run-id = `r` + 6 lowercase hex; branch `qwen/<run-id>`; worktree dir
`~/.qwen-delegate/worktrees/<project-slug>/<run-id>/`; builder self-tests match
`*_qwen.*`; refs `.qwen-delegate/refs/<slug>.md` (source URL on line 1).

**C7 — executors file** `~/.qwen-delegate/executors.json`:
`{"default": str, "endpoints": {name: {"parallel_max": int >= 1}}, "profiles":
{name: Profile}}`. An **endpoint is the concurrency domain**: several profiles (model
or context-size variants served by the same GPU/proxy) may share one, and its semaphore
serializes across all of them — which also prevents model-swap reloads. Resolution
precedence: call arg > project `.qwen-delegate.json "executor"` > this file's
`default` > builtin `qwen-local` (which renders today's invocation byte-for-byte — the
regression pin — on an implicit endpoint with `parallel_max: 1`).

**C8 — prefilter semantics** (F7): after each invoke, if a test command is known
(bootstrap detection) and changed files match `*_qwen.*`, run the test command on those
files. **Advisory only:** gate red → prefilter output is appended to the retry feedback;
gate green + prefilter red → one NOTES clause (`self-tests failing`); never affects
STATUS. Rationale: self-tests catch the builder's mistakes cheaply, but code and
self-tests encode the same misunderstanding — only the gate is evidence. A broken
self-test must not doom-loop the run (measured failure mode), hence advisory.

**C9 — tool input additions** (`qwen_delegate`): `worktree: "auto"|"off"` (default
"off"), `executor: str`, `trust: str` (accepted value: `"verified"`; anything else →
refusal naming DIRECTION-v2's parked dial), and `batch: [{task, verify, ...}]` — N
independent delegation items fanned across worktrees *inside* the server (worktree
implied "auto", per-item receipts, endpoint semaphore caps concurrency). Batch is the
**primary fan-out mechanism**. Probe 5 (3 regimes): MCP dispatch serializes per
*agent loop* — one agent's parallel calls serialize even across different servers,
but N subagents multiplex concurrently over one shared connection. So: batch for
overhead-free fan-out, N thin manager subagents as the proven client-side alternative;
never rely on single-loop parallel tool_use.

## 6. Concurrency model

One reader thread (parse + lifecycle requests); one worker thread per `tools/call`;
`respond()` behind a global write lock, whole-line writes. Per-repo mutex for *in-tree*
delegations ("one actor per tree", structurally); worktree runs skip it; queries never
lock. Concurrency caps are **per-endpoint semaphores** (C7): every profile names its
endpoint, and the endpoint's `parallel_max` is the cap — a local GPU at 1–2 never
throttles an API provider at 20, and two profiles sharing one GPU (model or
context-size variants) serialize against each other, which also prevents model-swap
reloads. The semaphore is held around the **entire executor subprocess** — a whole
delegation or query, never an individual HTTP request — so on a single-slot endpoint
two sessions' turns are never interleaved and the KV cache survives by construction;
no priority logic is needed in the proxy. It gates read-only queries too: an
interleaved query would evict a running build's cache and force full re-prefill each
turn (probe 4). Queueing behind the build is cache-correct scheduling, not a
limitation. For API endpoints the cache question is economic, not temporal — stable
prompt prefixes earn provider cache discounts; the run log's cost field captures it.
Locked shared state: run-log file append, registry append, worktree table. Everything
else per-call.

## 7. Build method — the plugin builds itself

Specs (from LLD.md) are committed before any module. The current server runs every
build delegation until cutover (N4). Leaf-first:

| # | milestone | units | builder |
|---|---|---|---|
| M0 | probes (§8) | — | manager + human |
| M1 | leaves | profiles, runlog, refs, gittree (port) | Qwen |
| M2 | engine ports | invoke, verdict, bootstrap, queries, engine (+prefilter) | Qwen; Claude reviews seams |
| M3 | dispatch | server.py | Claude (races under-prove on gates) |
| M4 | worktrees | worktrees + engine fan-out | git plumbing Qwen; wiring Claude |
| M5 | graph | graph | Qwen (post-probe) |
| M6 | cutover | old internals deleted; `.mcp.json` unchanged | Claude |
| M7 | Claude-side | skill, thin manager, tool descriptions; 3-arm benchmark | Claude |

Cutover gate: all `specs/*` green against the new entry **and** live end-to-end flows
(delegate, query, best-of-N, bootstrap-on-fresh-repo, scoped) through Claude Code.

## 8. Probes (M0 — no code until answered)

> **Answered 2026-07-22 — results in [PROBES-M0.md](PROBES-M0.md).** Headlines: ollama
> backend works (free index confirmed, `graphifyy[ollama]` extra required); structural
> re-index 1.9s repo-wide; `built_at_commit` recorded natively; client serializes MCP
> dispatch → `batch` param (C9) is the fan-out mechanism; endpoint/model override only
> via full settings overlay (C1).

1. graphify semantic backend accepts the local Qwen endpoint.
2. graphify incremental API shape (file list? auto-diff? records a sha?) → `graphify_cmd`.
3. `graphify query` retrieval quality on a real repo for change-shaped questions.
4. ~~Ollama concurrency~~ — **settled by decision, not measurement** (user, 2026-07-22):
   Ollama stays at 1 worker; the local endpoint's `parallel_max` is 1, a configured
   fact mirroring the backend. Do NOT test parallel Ollama workers. The design carries
   parallelism anyway (per-endpoint caps, worktrees, fan-out), so enabling it later is
   config only: raise `OLLAMA_NUM_PARALLEL` + the endpoint's `parallel_max`, after the
   run log's peak-context column confirms real tasks fit the halved (96k) window.
   Standing facts, no probe needed: a different queued conversation on one slot evicts
   the KV cache (full re-prefill) — the whole-subprocess semaphore already prevents it;
   different *models* on one Ollama force full reloads — heterogeneous executors
   serialize on the shared endpoint, always.
5. Does Claude Code run two >120s MCP calls to one stdio server concurrently? If
   serialized: fan-out falls back to N thin manager subagents (works today).
6. Qwen Code config precedence: do endpoint/model env vars override
   `~/.qwen/settings.json` per process? If not, inject a per-run temp settings file via
   `QWEN_CODE_SYSTEM_SETTINGS_PATH` — the mechanism the scoped hook already proved.
   Either way the user's own Qwen config is never edited.

## 9. Risks

stdout interleaving (write lock, spec'd under load — the one bug that corrupts every
result) · port drift (ported spec suite runs against old and new before cutover) ·
client serialization (probe 5; degrades, doesn't break) · graphify unknowns (isolated
behind `graphify_cmd`; worst case background full re-index) · worktrees vs uncommitted
work (refuse unborn HEAD, warn dirty tree) · Ollama contention (parallel_max is a
configured fact mirroring the backend's worker count — currently 1; raised only with
the hardware, never assumed).
