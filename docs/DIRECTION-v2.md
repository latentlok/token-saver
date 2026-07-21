# Direction v2: one loop, three layers, an executor you can swap

**Supersedes [NEXT-DIRECTION.md](NEXT-DIRECTION.md)** (kept for its economics measurements —
break-even at ~21k solo output, the subagent-tax analysis — which motivated this redesign
and still hold). Written 2026-07-21 after a design round; nothing below is implemented yet
unless marked as shipped.

## Decisions made this round

1. **Modes:** build a *code mode* first. A *document/research mode* (deep-research-shaped:
   fan-out fetches, adversarial verify, cited synthesis) comes later as a second skill over
   the same engine. Don't bake code-only assumptions into the server; "gate = a command
   that exits 0" already generalizes.
2. **Zero-trust first.** The trust dial (below, parked) is designed but not built until
   code mode ships.
3. **Greenfield and existing-codebase are one loop**, not two workflows. Greenfield is
   iteration zero.
4. **The manager-agent architecture inverts.** Knowledge moves from the agent definition
   into the tool + skill layers, ambient in every session. The subagent becomes a thin
   opt-in container, not the front door.
5. **Fan-out is designed in now** (worktrees + concurrent dispatch), with an *executor
   profile* abstraction so an API-class model (e.g. a GLM5.2-tier executor) is a config
   entry later, not a rearchitecture.

## The one loop

    map → design/spec (Claude) → delegate (Qwen) → gate verdict → incremental re-map

- **Existing repo:** the map comes from graphify. Claude queries it, pins the spec,
  delegates.
- **Greenfield:** the map is trivially empty; Claude's HLD / requirement docs *are* the
  map for iteration one. The moment the first gate goes green there is existing code, and
  every subsequent change is the existing-code case. Greenfield just enters the loop with
  a head start.
- **The convergence is literal:** graphify's node types include *design-rationale* nodes.
  Requirement docs and architecture decisions get indexed into the same graph, with edges
  from code to the requirement it implements. A change task months later queries the map
  and gets both "what calls this" and "why this exists." Design docs are graph nodes from
  day one, not a separate greenfield artifact.

## Three layers

The current problem is where the knowledge lives: the discipline sits in
`agents/qwen-manager.md` (302 lines), loaded only when the subagent spawns. The main
session knows almost nothing about Qwen. Invert:

1. **Engine (MCP server) — always present.** Once the plugin loads, the tool schemas are
   in every session. The tool descriptions carry the capability map — build against a
   gate, answer codebase questions, pull docs via firecrawl, run graphify enrichment —
   plus the hard rules the server enforces regardless. Costs what tool schemas already
   cost.
2. **Knowledge (the `delegate` skill) — one source of truth, loaded on demand.** A slim
   (~1k) skill holds the loop: map → spec → delegate → verify, when to reach for Qwen,
   gate discipline, escalation ladder. Its one-line description is the ambient trigger;
   the body loads only when reached for. `templates/CLAUDE-snippet.md` stays as the
   per-project proactivity knob.
3. **Containers — two, same brain.**
   - **Inline (default):** the main session runs the loop itself. Zero persona tax.
   - **Manager subagent (opt-in):** the same loop in an isolated context.
     `agents/qwen-manager.md` shrinks to a thin shell (~30 lines): "load the delegate
     skill, own the loop end-to-end, return a compact report."

   Both containers load the same skill, so they behave identically and there is one place
   to maintain the discipline. Decision rule (skill-encoded, benchmark-calibrated):
   inline for interactive work and small delegation counts; manager when isolation earns
   its keep — multi-unit builds whose spec/verdict rounds would silt up the main session,
   parallel fan-out of independent modules, or a main session busy being the conversation.

   The 3-arm benchmark from NEXT-DIRECTION (bare call vs subagent vs solo, same task)
   still runs — its job is now calibrating the threshold in that rule, not deciding
   whether the manager lives.

## Graphify: integration and the freshness policy

Two layers with different trust and different cost:

- **Structure layer** (tree-sitter): deterministic, free, seconds. Coordinates are
  trustworthy.
- **Semantic layer** (LLM summaries, backend pointed at local Qwen so it's free): slow
  (~70 tok/s), and its claims — like INFERRED edges — are *leads, not truth*. Verify
  load-bearing claims against source before pinning a spec on them.

**Cadence: event-driven, keyed to git. Never a timer, never a watcher.**

1. **Never index during a delegation.** Mid-run the tree is in flux (iterating worker,
   spec auto-reverts, possible rollback). The graph only ever reflects gate-green —
   ideally committed — states.
2. **The trigger is delegation completion, and the server already computes the
   worklist.** The blast-radius diff taken after every run *is* the incremental re-index
   file list. A post-verdict hook hands it to graphify.
3. **The two layers refresh at different speeds — that's where the concurrency lives.**
   Structure re-runs synchronously at verdict time (seconds). Semantics refresh only the
   changed nodes, in the background, while Claude writes the next spec. Qwen-side
   indexing overlaps Claude-side design: verdict N → re-index N ∥ spec N+1.
4. **Staleness resolves at query time, deterministically.** Record the commit hash the
   graph was indexed at; `git diff --name-only <indexed-commit> HEAD` names the stale
   nodes. Stale set intersects the queried subgraph → refresh those nodes first
   (structure sync, semantics on demand); otherwise serve as-is — it's accurate for that
   region. This also catches hand edits made outside any delegation: git is the change
   journal, so no watcher is needed.

Net: eventually-consistent with HEAD globally, strongly consistent at query time for the
queried region — build-system dirty-tracking. Zero-trust compatible: freshness is a git
fact, not anyone's claim.

**Open verification (phase 1):** NEXT-DIRECTION claims graphify re-indexes incrementally
on diff but lists freshness as a risk. Probe what its incremental API actually accepts
and whether it records the indexed commit; if not, our server records the hash under
`.qwen-delegate/` and wraps the call. Probe the tool, don't read its docs (FINDINGS).

## Firecrawl in code mode

Qwen already has firecrawl via its own MCP config. Two uses, both zero-trust-covered:

- **Pre-flight research:** before spec-writing, `qwen_query` has Qwen pull a library's
  docs and return a digest. Read-only, cheap, a lead — Claude pins whatever is
  load-bearing into the spec itself.
- **In-flight reference:** during a build, Qwen fetches docs to implement against. Web
  content is untrusted input, but wrong or injected content still has to get past a
  Claude-authored gate to become accepted code. The gate is the firewall between the
  internet and the repo.

**Discipline:** fetched references are saved to `.qwen-delegate/refs/` (self-ignored,
like the run log) rather than living only in Qwen's context. Reproducible builds, and
Claude can spot-check the source Qwen claims to follow. Also compaction-safe: a bulk doc
in context is exactly what triggers compaction and its measured honesty collapse.

## Fan-out

Current facts: the server is a single-threaded stdio loop with blocking
`subprocess.run` — a second tool call queues no matter what. Best-of-N (#26) exists but
runs candidates sequentially, resetting the *same* tree between them. The tool docs
already demand separate worktrees for parallel calls; that's aspiration until the engine
supports it. The client half already works: Claude Code auto-backgrounds MCP calls >120s
and notifies on completion.

**Two engine upgrades:**

1. **Concurrent dispatch** — each request on its own thread, stdio writes behind a lock.
   Still zero-dependency stdlib.
2. **Server-managed worktrees.** Not an optimization — what keeps zero-trust sound under
   parallelism. Every guarantee (spec guard, blast-radius diff, rollback, "the diff =
   Qwen's work") assumes **one actor per tree between snapshots**. Each parallel run gets
   its own worktree branched from HEAD; snapshot, gate, and diff happen there, so every
   guarantee holds per-worker unchanged. `worktree: auto`, plus a merge step on green.

**Two shapes:**

- **Best-of-N racing** — same task, N workers, gate picks the winner. Un-parks #26's
  parallel half.
- **Fan-out of units** — different modules concurrently. Contracts pinned before code is
  what makes this safe (proven: the bottom-up tokenizer → evaluator → calc build composed
  first try). A merge conflict is a *signal*, not a nuisance: two units touched the same
  surface, the contracts overlapped, and that escalates to Claude as a design question.
  Graphify re-indexes once, after the merge, on the union of the blast radii.

**Single-session UX:** pin contracts → fire N `qwen_delegate` calls in one message → they
auto-background → Claude specs the next unit or talks to the user while verdicts arrive →
merge green branches → one incremental re-index.

**Local-hardware caveat:** parallelism on Ollama is a wall-clock play bounded by the box,
not a token play — N workers share ~70 tok/s unless there's headroom (`num_parallel`,
VRAM). Sweet spot is probably 2–3; measure in phase 1 rather than assume. The bound
disappears against an API executor (below).

## Executor profiles: the API-class future

Design around Qwen now; make a smarter executor (GLM5.2-tier, ~Opus-class) a config
entry later. The server already talks to Qwen through a CLI hitting an OpenAI-compatible
endpoint — an API executor is plausibly the same CLI with a different endpoint.

**Invariant to executor intelligence:** the gate (verification is about evidence, not
trust — fabrication is orthogonal to competence, FINDINGS; for code a gate is nearly free
insurance either way); contracts-first for fan-out (true of senior humans too); worktree
isolation and merge policy; the map (flat context saves Claude tokens either way — and
executor tokens now cost money).

**What changes with executor class:**

- **Spec altitude rises.** Qwen-class needs LLD-grade specs — pinned signatures, tight
  scope, contradiction pre-flights. A frontier-class executor takes an HLD plus
  acceptance gates and does its own LLD; Claude moves up one altitude, from writing LLD
  to reviewing the executor's LLD. Fewer, higher-level gates; spec cost amortizes over
  bigger units; break-even drops.
- **Economics invert.** Local = free-but-slow → offload everything. API = cheap-but-
  metered → every delegation cost-justified. Leverage becomes dollar-denominated.
- **Parallel width unbounds.** From VRAM-limited (2–3) to rate-limit/budget-limited. The
  bottleneck then moves up the stack: throughput is limited by Claude's ability to
  decompose into cleanly-contracted parallel units. The system is an amplifier for
  architect quality.
- **Paranoia knobs get new defaults, not removal:** reflexion rarely fires, best-of-N
  drops toward 1, plan-mode pre-flight becomes optional. All already knobs.

**Profile shape:**

    executor profile = {
      name, endpoint / CLI invocation,
      cost per input/output token,     # 0 for local
      endpoint,                        # names a shared endpoint; concurrency caps live
                                       # on the endpoint, not the profile (HLD C7)
      trust_default,                   # the parked dial, one notch per class
      altitude,                        # "lld" | "hld"
      defaults: workers, max_iterations
    }

**Bake in during the current build (cheap now, expensive later):**

1. Never hardcode "free": the run log gains a price field (0 for Ollama) so leverage is
   cost-denominated from day one.
2. Parallel width comes from config — the shared *endpoint* entry (HLD C7), not the
   `[1,8]` clamp constant and not the profile.
3. "The executor's rules file" is a mechanism, not the literal name `QWEN.md` — other
   CLIs use AGENTS.md etc. Keep it pluggable.
4. The skill states discipline per *altitude*, not per model: LLD-altitude executors get
   pinned contracts and pre-flights; HLD-altitude executors get architecture +
   acceptance gates, and their LLD is a reviewable lead.

Framing rule: smarter → delegate **bigger and higher**, trust per **stakes** as before.
The dial's defaults shift one notch with executor class; for anything gated, the gate
stays. Where a smarter executor earns genuinely new territory is the *ungated* work —
LLD, code review, design critique — tasks Qwen was never eligible for.

## Parked (in order, behind code mode)

**1. The trust dial.** Per-call `trust` param + per-project default in
`.qwen-delegate.json`:

| level | gate | spec guard | free deterministic checks | executor self-report |
|---|---|---|---|---|
| `verified` (default) | required | on | on | ignored — gate decides |
| `checked` | optional | on | on | returned, labeled **UNVERIFIED** |
| `open` | none | off | snapshot + rollback only | passed through |

Two rules that survive any relaxation: **relax the evidence requirement, never the free
seatbelts** (snapshot, diff attribution, rollback, run log cost zero model tokens — no
level drops them); and **trust is stakes, not intelligence** (Qwen wrote correct code and
fabricated the test report in the same run). `QWEN.md` ships in all modes — the spec rule
is what *produces* honesty (FINDINGS), and it's free. Read-only work needs no dial:
`qwen_query` can't write, and "is the answer right" is already handled by
lead-not-truth.

**2. Document/research mode.** A second skill over the same engine — fan-out fetches,
adversarial verification, cited synthesis. Its gate problem (no objective check for
prose) is exactly why it sequences after the trust dial. It inherits both containers and
the fan-out engine for free.

## Phasing (code mode, zero-trust)

1. **Graphify probe** — install; point the semantic backend at local Qwen (the "free
   index" claim rests on this); test incremental re-index behavior and query quality on
   a real repo; measure the concurrent-worker sweet spot on this hardware.
2. **Freshness wiring** — indexed-commit recording, post-verdict incremental hook off the
   blast-radius list, query-time staleness check. Spec'd (`*_spec.py`) and
   mutation-tested, as everything here is.
3. **Bare-call restructure** — slim `delegate` skill as the canonical loop, tool
   descriptions rewritten as the capability map, manager thinned to a shell; run the
   3-arm benchmark to calibrate the inline-vs-manager threshold.
4. **Fan-out engine** — concurrent dispatch, server-managed worktrees, merge policy,
   parallel best-of-N. The executor-profile abstraction and the run-log cost field land
   here.
5. **Firecrawl-in-code-mode** — pre-flight research pattern in the skill; `refs/`
   pinning in the server.
6. **Greenfield entry path** — HLD/requirement docs indexed as design-rationale nodes at
   milestone commits, so iteration zero feeds the same graph.

Then the parked items, in order.

## Invariants — do not relax while restructuring

- A command decides success; prose never does.
- Free seatbelts (snapshot, diff, rollback, run log) run at every trust level, in every
  mode, for every executor.
- Specs are Claude-authored, protected, auto-reverted; the gate runs the spec.
- The executor's rules file is always present — it is what produces honesty.
- Map claims (semantic layer, INFERRED edges) and executor answers are leads, not truth.
- Fetched web content is untrusted input; the gate is the firewall.
- One actor per tree between snapshots — parallelism means worktrees, never a shared
  tree.

## Measurements that rule

- The 3-arm benchmark (bare vs subagent vs solo) calibrates the container decision rule.
- The parallel-width sweet spot on local hardware (phase 1).
- Cost-denominated leverage from the run log, from day one of phase 4.

## File pointers

- Design: [HLD.md](HLD.md) (components, lifecycle, cross-module contracts C1–C9, build
  order) → [LLD.md](LLD.md) (per-module design to spec-readiness).
- Plugin: this repo — `server.py`, `agents/qwen-manager.md`, `skills/`, `*_spec.py`,
  `docs/FINDINGS.md` (the evidence), `docs/PRINCIPLES.md`.
- Eval harness: `~/projects/token-saver-eval` — `results_xl/`, `results_large/`.
- graphify: https://github.com/Graphify-Labs/graphify
