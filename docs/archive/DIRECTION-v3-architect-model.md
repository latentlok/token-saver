# Direction v3 — the architect model

> **Status: EXECUTED (2026-07-23).** The premise below was validated and built —
> execution record in [PLAN-v3-l5.md](PLAN-v3-l5.md), measured results in
> [FINDINGS.md](FINDINGS.md) (−18% greenfield / −43% bugs / −69% existing-repo, equal
> quality), day-to-day workflow in [USAGE.md](USAGE.md). This file remains as the
> reasoning the build rests on; its §8 "resume order" is done.

**Start the next session from this premise:** Qwen (or any delegate) is given
**higher-level tasks at L5 trust** — it writes the code *and* grades itself; Claude never
touches code. Validate that corner first, then work outward to the lower trust levels and
the capability slider. Everything below is the reasoning and design that premise rests on.

Supersedes the benchmark *conclusions* in [DIRECTION-v2.md](DIRECTION-v2.md) (which are
premature — see §2). The v2 *build* (M0–M7: the `qd/` engine, worktrees, graph, dispatch)
is done and stands; v3 is the direction that runs on top of it.

---

## 1. The thesis (correct on tokens; this is the whole idea)

**Claude is a system architect that never touches code.** It knows the system "from the
top" — module boundaries, public contracts, design rationale — and gets code updates only
through delegate verdicts and graphify queries. It may do rare one-off logic/algo inline.
All of it is driven in chat with the user, the way a product is actually built.

- **Greenfield:** Claude takes the project outline, defines top-down (spends *design*
  thinking), never writes code, receives updates through the delegate.
- **Existing code:** Claude gets structure through **graphify**, changes the boundaries
  of files/modules, never reads the code.

**Why it saves tokens, structurally, and why the saving grows with scale:**
architecture is a *compressed* representation of code, and the compression ratio
**improves** with size.
- A contract (signatures + behavior + edge cases) is 3–10× smaller than its
  implementation (measured: a 199-token LLD described ~30–50 lines).
- Code grows with every feature/helper/edge case; architecture grows only with modules
  and public contracts — far slower (sub-linear in lines). A 10× bigger codebase does not
  have 10× the architectural surface.
- With graphify, Claude holds only the *scoped subgraph* for the current task — bounded by
  task locality, not product size.

So on **both axes**: output (bounded design vs growing code) and context (architecture,
queried, vs code, accumulated), the architect stays flat while a solo builder grows with
the codebase. **You cannot make code stop growing with the product; you can make the
architect's context stop growing with the code.** That difference compounds every feature.

## 2. What the benchmarks actually showed — and why the conclusions were premature

Full data in `~/projects/token-saver-eval/results_*`. Solo vs inline delegation,
claude-fable-5, cold sessions, n=1:

| task | inline vs solo | note |
|---|---|---|
| large single-shot build (Sheet engine) | **−20%** (win) | implementation ≫ spec |
| small single change (join) | +28% (loss) | overhead > tiny change |
| incremental product, full-spec delegation | +166% (loss) | Claude wrote a full test file per feature |
| incremental product, **LLD / some-trust** | **+5% (parity)** | Claude wrote only a short LLD; Qwen wrote code + own tests |

The parity result was measured at the **worst possible scale** for the thesis (5 features
/ ~200 lines — where architecture and code are still comparable in size, so compression
hasn't bitten) **and with a bloated architect** (see §3). Read *per feature*, the LLD arm
already **crosses over**: it loses features 1–2 (warmup) then wins 3, 4, 5 with a widening
margin (solo output climbs to 2,950 on the complex feature; the LLD stays ~286). And
context: over 5 features solo's grew **+66k**, delegation's only **+27k** — most of that
27k being strippable bloat. The mechanism was already visible; the totals hid it.

**Meta-lesson for the next session:** do not read small-scale, bloated data as a verdict
on the mechanism. The mechanism is sound. Prior "it doesn't pay" conclusions were wrong.

## 3. The bloat teardown (make the architect lean — this is prerequisite work)

Measured per-feature spend of the LLD architect, and whether each item dies:

| bloat | measured | kill? | how |
|---|---|---|---|
| full toolset resident in context | ~35–50k of base | **YES (biggest)** | architect only needs `qwen_delegate` + `graphify` (+ `qwen_query`). Strip Read/Write/Edit/Bash/Grep/Glob/Task schemas it never uses |
| MCP tool schema re-read every turn | 2.7k/turn | **YES** | deferred tools + a one-line lookup per fn; full schema loads on demand (ToolSearch). Verbose param essays (approval_mode 616 tok, timeout formula) → a skill loaded once, not resident. ~2.7k → ~150 |
| Bash re-verification of the gate | 5 calls + output | **YES, fully** | forbid it — the gate already ran inside the delegation; re-running is distrust the design removes |
| verdict receipt | ~660/feat, accumulates | **YES ~85%** | compact to `STATUS + changed + 1 line`; HANDOFF/CONTINUE/CONTEXT/TIME/TOOLS is human-debug boilerplate |
| prose narration to user | 200–560/feat | **YES ~70%** | a status line, not an essay |
| thinking | 800–1,100/feat | **PARTIAL** | keep *design* thinking (the value); kill *orchestration* thinking (parsing bloated verdicts, deciding to re-verify) — dies with compact verdicts + trusted gate; lower reasoning effort when design is simple |
| accumulated LLDs | 1.3k/5 feats | **PARTIAL** | current architecture stays; stale LLDs live in graphify, not the chat |
| the LLD itself | ~200–320/feat | **NO** | the irreducible work |

**Target lean architect:** base (~52k, minimal tools) + per feature ≈ design-thinking +
~300-token LLD + ~100-token verdict → **per-feature context increment ~400 tokens, ~flat
forever.** That is the ideal; every gap to the measured 113k is on this table, mostly
killable. **Specing the lean architect (exact toolset, compact verdict grammar) is the
first concrete build step of v3.**

## 4. The trust slider (0–5)

Varies **who authors verification and how thoroughly** — trading architect output (cost)
against reliance on the delegate (risk):

- **L0 — trust nothing:** architect writes an *exhaustive* behavioral gate (every edge
  pinned); the delegate only makes it pass (and may run/judge, passing the verdict
  through). Max architect cost, max safety.
- **L1–L4 (design later):** the gradient — progressively hand test-authoring to the
  delegate. L1 thorough gate + model edge tests → L2 interface + critical assertions →
  L3 signature/smoke gate, model writes behavioral tests → L4 LLD only, architect
  spot-checks the *test list* (names, not code).
- **L5 — full trust (START HERE):** LLD only; the delegate writes code *and* grades
  itself; architect trusts the implementation. Min architect cost, min safety.

Pick per task by **stakes** (payment flow → L0; a settings toggle → L5).

## 5. The model-capability slider (task cascade)

Capability sets **decomposition granularity** and the **trust ceiling**:
- **Weak delegate** (< Qwen): cascade to *low-level* tasks ("write this one function,
  this signature, this behavior"). Fine-grained → more architect decomposition work, but
  cheap/free compute. Low trust ceiling → pairs with L0–L1.
- **Strong delegate** (Opus-class): cascade *coarse* tasks ("build the auth module to this
  contract"). Minimal architect work. High trust ceiling → L4–L5.

**Economics (the point):** architect cost is *inversely* tied to delegate capability.
`total = architect_tokens(capability) + delegate_cost(capability)`. Dumber → more
architect tokens, cheaper compute; smarter → fewer architect tokens, pricier compute.
The **Fable-architect + Opus-delegate** corner wins whenever
`Fable(architecture) + Opus(implementation) < Opus(solo)` — which holds structurally
because architecture ≪ code (compression) and Fable < Opus (price). Cheap model
architects; capable-but-metered model builds; nobody re-reads code.

The v2 `executor` profiles + the parked `trust` field are the hooks for both sliders —
capability = which profile; trust = the (to-be-built) trust level. See HLD C1/C7/C9.

## 6. Bug resolution — delegable, maybe a *better* fit than features

A bug is "observed ≠ expected." The architect delegates the fix without reading the code:
1. **Reproduction gate:** from the symptom (English report), write a *failing* test —
   "input X should give Y, gives Z." Writable from expected behavior alone, no code.
2. **Delegate the fix:** "make this pass without breaking the suite." The delegate greps/
   reads to *locate the cause* (free for it), fixes; repro gate + regression suite verify.
3. Architect reads a one-line verdict.

Better fit than features, because bug-fixing is exactly where a solo builder pays most
(must read existing code to find the cause) — the architect offloads that read entirely.
Two branches: **implementation bugs** (above) and **design bugs** (architecture is wrong)
→ architect changes contracts via graphify and re-delegates affected modules. Rides the
capability slider: weak delegate needs the architect to localize first (graphify → "bug
is in module M"); strong delegate gets "diagnose and fix."

## 7. The system, one line

Claude is a system architect that never touches code: it holds the architecture
(compressed, queried via graphify), emits compact contracts, receives compact verdicts.
Two sliders — **capability** (how coarsely it can task the delegate, how far it can trust)
and **trust** (how much verification it authors vs delegates) — place any job on the
cost/safety/compute frontier. **Features and bugs are the same primitive:** a gate the
architect specifies and the delegate satisfies. Because architecture compresses and code
does not, the architect's tokens stay flat while a solo builder's grow with the codebase —
so the saving is structural and widens with scale.

## 8. Next-session plan (resume order)

1. **Anchor on L5 + high-level tasks** — the premise. Prove Qwen handles coarse,
   high-level tasks with self-grading; find where it breaks (that boundary defines the
   trust ceiling for Qwen specifically).
2. **Build the lean architect** (§3): the restricted toolset, deferred/lookup MCP schema,
   compact verdict grammar, forbid-re-verify, terse output, low reasoning for simple
   design. This is prerequisite — without it, no measurement is clean.
3. **Then** work down the trust slider (L4→L0) and out the capability slider (weaker and
   stronger delegates), mapping each to gate-thoroughness and decomposition granularity.
4. Design **bug resolution** as a first-class delegation primitive (repro gate).
5. Only after the lean architect exists, re-measure at real scale (dozens of features) —
   watch the *per-feature slope*, not the cumulative. Ignore wall-time (separate
   optimization).

Open-source goal: reduce dev cost for anyone using local/cheap models as the delegate.
The whole value is the token/cost saving; keep every design decision pointed at that.

## Pointers
- Built system: this repo, `qd/` package, `docs/HLD.md` (contracts C1–C9), `docs/LLD.md`.
- Benchmark data + harness: `~/projects/token-saver-eval/` — `results_grow/`,
  `results_gf_large/`, `results_existing/`, `results_3arm_pilot/`,
  `results_combined_summary.md`, and `grow/` (the accumulating harness).
- Bloat evidence: transcripts under
  `~/.claude/projects/-home-you-scratch-bench-grow-repo-{SOLO,LLD}/`.
