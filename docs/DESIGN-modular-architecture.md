# Design — modular architecture: a run is an object, a feature is a unit

**Status: step 1 built, steps 2–8 designed.** The parked queue
([PARKED.md](PARKED.md)) lands *into* this as the steps land.

The goal in one sentence: **adding or removing a feature should be a local
change**, and today it is not.

---

## 1. The problem, measured

Adding `challenge_brief` took **five edits across four modules**:

| module | edit |
|---|---|
| `engine.py` | a constant, a resolver function, a block inserted mid-procedure, a `ctx` key, an accumulator call |
| `verdict.py` | the wire-format constant, a parser key, a receipt branch |
| `schemas.py` | the parameter |
| `runlog.py` | the telemetry |

Nothing groups those sites. Nothing can *enumerate* features. Removing one
means rediscovering all five and hoping none were missed.

The pressure lands on two functions:

| file | lines | longest function |
|---|---|---|
| `qd/engine.py` | 2,134 | **`_delegate` — 1,111 lines, 105 distinct locals** |
| `qd/verdict.py` | 1,042 | **`render` — 888 lines, 20 inline receipt branches** |
| `qd/server.py` | 937 | `submit_delegate` — 88 |
| `qd/invoke.py` | 836 | `_stream_process` — 130 |
| `qd/gittree.py` | 707 | `mocked_seams` — 58 |

And the interface between them is undeclared: engine writes **28** `ctx` keys,
`verdict.render` reads **57**. Nothing says which are required, which are
optional, or which are dead.

**It compounds.** In one working day: `_delegate` 1,041 → 1,111, `engine.py`
1,947 → 2,134, `delegate()` 58 → 61 graph edges. Every feature added makes the
next one harder to add *and* harder to remove.

---

## 2. What is NOT wrong

Stated up front, because the fix must not damage it.

**Module layering is sound.** The import graph is acyclic:

```
server → engine → {bootstrap, gittree, invoke, profiles, runlog, verdict}
verdict → {gittree, invoke, runlog}        invoke, runlog → profiles
bootstrap, graph → {gittree, runlog}
```

**Every other module is healthy.** Outside the two god functions, the longest
function in the package is 130 lines.

**The detectors are already the right shape.** They are pure functions with
honest signatures, and they are already spec-covered:

```python
never_executed(cwd, changed, verify_cmd)
mocked_seams(cwd, changed, limit)
uncalled_symbols(cwd, pubs, limit)
dodge_markers(cwd, pre_sha, pre_status)
new_public_symbols(cwd)
```

**This is the finding that shrinks the job.** The logic is not tangled — the
*wiring* is. Detectors are called inline in `_delegate` and rendered inline in
`render`. Moving to a registry is largely declarative work, not a rewrite.

So: **do not reorganise modules.** Reorganise how features attach to them.

---

## 3. The core idea

Today there is no object representing a run — there is a function with 105 loose
variables standing in for one. Three things follow:

1. **A run becomes an object** carrying its plan, its scope, its facts, its
   findings.
2. **A feature becomes a unit** — a function plus one registry entry — instead
   of a set of edits scattered across four files.
3. **The pipeline becomes fixed and small**, and knows nothing about any
   particular feature.

---

## 4. Facts vs Findings — the load-bearing distinction

This is the correction that came out of attacking the first draft, and
everything else depends on it.

**The trap:** if features can compute or write shared state, they depend on each
other. `never_executed` needs `changed` *and* the gate command; the seam
demotion needs `new_public_symbols` *and* `mocked_seams`. Let each feature
gather its own and you re-run git calls N times. Let them share a mutable bag
and you have rebuilt `ctx` with a nicer name — including its ordering
dependencies, now hidden in a list's order.

**The rule:**

> **FACTS are computed once, by the pipeline, in dependency order.
> FINDINGS are pure functions of facts. A feature may read facts. A feature may
> never write them.**

Consequence: **features have no ordering between them.** That is the property
that makes add/remove safe, and the first draft did not have it.

If two features in the same phase ever appear to need ordering, that is a bug —
it means they are competing over a fact that should have been computed once,
upstream.

```
Facts     changed files · new public symbols · mocked modules · commits during
          run · blast radius · dirty-at-T0 snapshot · gate output
             ↓ read-only, shared
Findings  UNCALLED: · MOCKED SEAM: · NEVER EXECUTED: · TEST DODGE: · STRAY: ·
          TOUCH SCOPE: · SEAM CROSSED: · CHALLENGE: ...
```

---

## 5. The module map

```
qd/
  core/
    plan.py       args + config  →  RunPlan  (frozen)
    scope.py      RunScope: container, session, call log  (lifecycle)
    facts.py      tree + run observations, computed once, read-only
    pipeline.py   the fixed phase sequence
    findings.py   Finding record + severity/priority

  adapters/       ← ALREADY EXISTS as gittree / invoke / worktrees / profiles
    git · executor · worktrees · locks · config

  features/
    gates/        may refuse a run          (few)
    detectors/    facts → finding, pure     (many)

  surface/
    schema.py     the frozen wire contract — hand-written, verified not generated
    receipt.py    findings → the caller's text, by declared priority
    runlog.py     findings + per-call telemetry → runs.jsonl
```

### `core/plan.py` — *what was asked for*

Resolves the call's arguments against project config, machine config and
built-in defaults into **one frozen record**, once. Nothing downstream
re-resolves anything.

*Why:* roughly a third of the 105 locals are resolved settings, and
`preflight_expect` is currently resolved in **two different places** — the exact
class of bug a single resolution point removes.

### `core/scope.py` — *what this run owns*

The container (worktree **or** repo lock), the executor session, the `CallLog`.
Acquired at the start, released in `finally`.

*Why:* the genuinely object-shaped thing here. Lifetime is what plain functions
handle badly — the same argument that makes `EndpointGuard` an object, and the
one that made the chain worktree a real bug until it had an owner.

### `core/facts.py` — *what is true*

Computes the observations once, in dependency order, and hands out a read-only
record. See §4.

*Why:* the single most important module. It is what makes features independent,
and it stops the same `git` call being paid for by three detectors.

### `core/pipeline.py` — *the order things happen in*

```
prepare → gate → build (attempt loop) → observe → report
```

Fixed, short, feature-agnostic.

*Why:* the sequence really is a sequence. What it must never do again is
*contain* the features — that is precisely what made `_delegate` 1,111 lines.

### `features/gates/` — *the few things that can stop a run*

`challenge_brief`, `preflight_expect`, `trust="self"` gate synthesis, and the
parked red gate. Each answers one question: **refuse, or proceed?**

### `features/detectors/` — *the many things that observe*

Seams, dodge markers, strays, touch-scope violations, unproven fixtures,
uncalled symbols. Each is `facts → Finding | None`. **These already exist** —
they change from being called inline to being registered.

*Why two roles rather than one interface:* refusing and reporting are different
powers. A single `Feature` interface would force ~10 detectors to implement
empty veto hooks — a fat interface, and one that invites a detector to start
refusing things.

### `surface/receipt.py` — *what the caller reads*

Renders findings in a declared priority order. **A list, not a cascade.**

*Why:* today every feature edits an 888-line function to add its line. This is
the change that most directly buys "easy to add / easy to remove."

### `surface/schema.py` — *the frozen contract*

Hand-written and unchanged. A spec asserts it covers every registered feature.

*Why:* C9 is explicit — *"existing names, enums and required lists never change;
a caller's working call must keep working."* Generating the schema from code
would make the wire contract a function of code structure, so a refactor could
silently move it. **Verify, do not generate.**

---

## 6. Task types

A task type is not a different *kind* of thing. It is the same run with a
different **capability vector**:

| type | container | gate | loop | tree facts |
|---|---|---|---|---|
| **query** | none | none | single call | none |
| **delegate** | own worktree, or the repo lock under `worktree: "off"` | strategy | retry to `max_iterations` | yes |
| **chain** | **one worktree shared by all links** | per link | per link | yes |
| **batch** | one per item, isolated | per item | per item | per item |

Features then attach by **capability**, not by type name — a detector that needs
tree facts simply is not selected for a run that has none. Nothing has to know
what a "query" is.

```python
DELEGATE = [trust_self, spec_guard, touch_scope, strays, fixtures, seams, dodge]
CHAIN    = DELEGATE + [shared_container, handoff, challenge_at_head]
QUERY    = [schema_check, denials, context_peak]
```

### Why `query` is in, having first been left out

The first draft excluded it: 193 lines, one function, sharing only the executor
adapter — pulling it in looked like an abstraction paying rent for nobody.

That argument measured SIZE and ignored DUPLICATION. `queries.py` re-implements
five things `delegate` already does: profile resolution, `result_schema`
handling and validation, telemetry assembly (`_log_query` rebuilds what
`leverage_record` assembles), the `CONTEXT: peak` line, and denial reporting.

The evidence that settles it came from this repo's own history. Per-call
telemetry (`CallLog`) was added to the delegate path; `grep -c CallLog` returns
**2 in `engine.py`, 0 in `queries.py`**. A capability every run should have was
added once and silently reached one caller, because there was no shared pipeline
to add it to. That is the exact failure mode this whole document exists to fix,
and leaving `query` outside would preserve it by design.

So: `query` is a run whose container is `none`, whose gate is `none`, whose loop
runs once, and whose facts are not tree-derived. It gains per-call telemetry,
one telemetry path and one receipt renderer for free, and stops being the place
improvements forget to go.

---

## 7. Patterns

### Adopted

| Pattern | Where | The problem it removes |
|---|---|---|
| **Builder** | `RunPlan` | four layers of precedence resolved inline, in two places |
| **Composite** | a *runnable* is a Run **or** a chain of Runs | `_batch_item`'s hand-written type check; makes "nesting is one level" a property rather than a refusal message |
| **Strategy** | the gate | `verify` / self-gate / red gate are interchangeable; the parked red gate currently has nowhere to plug in |
| **Registry + Pipeline** | features | features have no home and cannot be enumerated |
| **Decorator** | the prompt | `task_suffix` + `HANDOFF` + `FINDINGS` + `CHALLENGE` + the chain preamble are string concatenation across three files |

### Already present — named here so they are not deleted by accident

| Pattern | Where | Why it matters |
|---|---|---|
| **Adapter** | `invoke.py`, `gittree.py` | why Ollama → vLLM was a config change |
| **Bridge** | argv template + executor profile | why an API-class executor is config-only |
| **Proxy** | `scoped_hook.py` | a protection proxy over the worker's tool calls |
| **Memento** | `snapshot_contents` / `restore_paths` | saves T0 **bytes**; its docstring records why `git checkout <sha>` destroyed a caller's uncommitted edits |
| **Multiton** | `_repo_locks`, `_endpoint_sems` | one lock per repo, one semaphore per endpoint |
| **Prototype** | `BRIEF_KEYS` + `_inherit` | already carries the pattern's hard question — `amend_brief` is excluded because a stored copy would re-amend on every retry |
| **Facade** | `server.py` over `engine.py` | keeps the MCP surface thin |

### Rejected, with reasons

| Pattern | Why not |
|---|---|
| **Abstract Factory** | for *families* of products across variants; there is one product with different feature lists |
| **Observer** | reintroduces the ordering dependency §4 exists to remove |
| **Singleton** for the worktree | a worktree is per-run **by design**; sharing one is corruption, not reuse |
| **Template Method** | the inheritance form of Pipeline; composition gives the same result without dragging pure functions into a hierarchy |
| **Flyweight** | shares memory across thousands of identical objects; there are a handful of runs |
| **Mediator** | centralises communication between components that barely talk — which is the good news, not the problem |
| **Visitor** | for stable structures with many varying operations; here the operations are stable and the data varies |
| **Chain of Responsibility** | already present procedurally in `_preconditions`; formalising buys little |
| **State** | runs have statuses, not behavioural modes. A status is data |

**The rule:** a pattern earns its place by removing a problem that exists here.
Builder removes measured duplication. Abstract Factory would guard an empty room.

---

## 8. Migration — no rewrite, every step ships green

Ordered so each step is independently valuable and independently revertable.

**Extract along the seams being kept, not the ones that happen to be visible.**

An earlier draft opened with "extract `_delegate`'s post-run block" — one
contiguous ~190-line region, the reflexive Extract Method move. That was wrong.
The post-run block is **four destinations in one region**: tree facts (→
`facts.py`), the detectors reading them (→ `detectors/`), worktree
commit-or-release (→ `scope.py`), and status/cost/refs/stored-brief (→ report).
Extracting it whole would build a function with four unrelated responsibilities
that steps 1, 2 and 5 then tear apart again — a shape alive for one commit, and
harder-to-read diffs exactly where reviewability matters most.

Contiguity is a property of the file. It is not a seam.

| # | Step | Why here |
|---|---|---|
| 1 | ~~**`Facts` record**~~ **DONE** (`e97e70e`) — `qd/core/facts.py` + `specs/facts_spec.py` | it proved the seam *and* the design: the extraction made visible that the detectors write their results back INTO the facts record (`tf["uncalled"] = ...`), which is the §4 confusion, invisible while it was one inline block. `collect()` returned a plain dict for exactly as long as that was true; **the freeze landed with step 2** (`f5b78d3`) |
| 2 | ~~**`Finding` record + detector registry**~~ **DONE** (`177530b`, `eafd5ae`, `f497eb4`, `f5b78d3`) — `qd/core/findings.py` + `qd/features/detectors/` + `specs/detectors_spec.py` | the detectors already had the right signatures, and the move exposed one bug and one wrong assumption. See below |
| 3 | **Receipt as a list** — `render`'s 20 branches become registered blocks | kills the second god function; after this, adding a feature never touches the renderer |
| 4 | ~~**Gate strategy**~~ **DONE** — `qd/features/gates/` + `specs/gates_spec.py` | A1's red gate is now a file plus one line in `GATES`. Settled what is NOT a gate: `advisory_gates` can never refuse, and by §5's own argument (a reporter forced to carry a veto hook is a fat interface that invites it to start refusing) it stays where it is. Found a hole — see below |
| 5 | **`RunScope`** — container + T0 **DONE** (`b162e8d`, this commit); session + call log remain — `qd/core/scope.py` + `specs/scope_spec.py` | the container's three disposal sites became one, and `DetectorInputs` lost its scope half (7 fields → 4). Found a hole first: a chain link releasing its BORROWED container, which deletes earlier links' committed work — see below |
| 6 | **`RunPlan` builder** — resolver **DONE** (`c15d242`, this commit): `qd/core/plan.py` + `specs/plan_spec.py`; the frozen RunPlan record itself remains | opened with a live bug rather than a refactor — `or`-chained precedence silently WIDENED a caller's explicitly narrowed `shell_allow=[]` to whatever the project declared. Six resolutions now share one helper; `max_iterations` is deliberately left on `or` (0 must stay "unspecified") |
| 7 | **Composite runnable** — Run / ChainOfRuns | late, because it is the only step that changes an external shape |
| 8 | **Fold `query` in** — `container=none, gate=none, loop=single, facts=none` | last on purpose: the smallest and best-behaved caller, safest to migrate once the shape is proven, and the first to benefit (it has no per-call telemetry today) |

**What step 2 turned up.**

*A bug the suite could not see.* The detectors sat in two unequal `try` blocks:
the three seam greps shared one, and `dodge_markers`/`_strays` sat in the OUTER
handler that sets `tree_facts = None`. So **one failed grep discarded every
fact** — the receipt lost CHANGED and COMMITTED and fell back to the v1 re-read
path, silently. Nothing raises on a healthy tree, so a green suite could never
show it. Fixed in `177530b`, deliberately BEFORE the move: a registry loop
isolates each detector naturally, so doing the move first would have smuggled
the fix in disguised as a refactor and no bisect could separate them.

*An assumption that was wrong.* The plan called for pinning TEST DODGE at the
receipt before moving anything. It had been pinned since `engine_spec:1689`.
The unpinned findings were the three SEAM ones — asserted only in `seams_spec`,
which calls the gittree functions directly and therefore proves the greps work
while proving nothing renders what they return. Those pins (`eafd5ae`) are what
made the move verifiable: written against rendered text, they passed unchanged
throughout it.

*A signature the design did not have.* §5 says a detector is `facts → Finding`.
Five of five need more: `verify` is the gate command and `task`/`touch_scope`
are the brief — inputs, not observations. `DetectorInputs` carries the seven,
frozen and closed, each field annotated with the step that takes it (5 or 6).
It is scaffolding with an expiry, and it is named as such, because a
general-purpose bag handed to every feature is `ctx` with a nicer name.

*Honest measurement.* `_delegate` **grew** 1,106 → 1,111 lines; `engine.py`
shrank 2,132 → 2,116. Step 2's benefit is not size — it is that the detectors
are now enumerable (`DETECTORS`) and that adding or removing one is a file plus
a line. The call-site lines come back in steps 5–6, when `DetectorInputs`
dissolves into scope and plan.

Steps 1–3 deliver most of the benefit. After step 3 a feature can be added
without touching the renderer; after step 4 there is a template to copy. The
post-run block is not a step — it **drains**, as steps 1, 2 and 5 each take
their part.

### 8.1 Steps 7 and 8, reassessed after 2–6 shipped

Recorded as an argued recommendation, not a decision taken. Steps 1–6 changed
what is known, and both remaining steps look different from here.

**Step 7 (Composite) — recommend NOT building as specified.**

The stated benefit is that it "makes *nesting is one level* a property rather
than a refusal message". But `_batch_item`'s own docstring argues the opposite,
deliberately: nesting is refused because *"a batch inside a batch item says
nothing `batch` does not already say, and would make the receipt's structure
depend on how deeply the caller happened to nest."* Composite's whole value is
treating leaf and composite uniformly — and here they are **not** uniform on
purpose. A batch is unordered and parallel; a chain is ordered and shares one
worktree. Uniform treatment loses the distinction that makes each correct.

The code it would replace is a four-line type check inside a thirteen-line
function. §7's own rule is that *a pattern earns its place by removing a problem
that exists here*, and Builder was adopted because it removed **measured**
duplication. This would guard an empty room.

**What is actually needed, and by what.** G2 (whole-chain contradiction check)
needs a chain to be an *addressable thing* it can hand to a gate — today a chain
is `items` plus `run_chain`, not a value. That is one small record, built as
part of G2, not a hierarchy built in advance of it.

**Step 8 (fold `query` in) — recommend the cheap 80% first.**

Its stated payoff is that `query` gains per-call telemetry, which it lacks. That
payoff does not require the fold: giving `queries.py` a `CallLog` is a small,
low-risk change that delivers the entire user-visible benefit. The fold's
remaining value is structural uniformity, which is real but buys nothing a
caller can see, and it touches the one caller that has never misbehaved.

Do the telemetry; leave the fold until something needs it.

---

## 9. Risks

**This refactors the most spec-covered code in the repo.** 1,000+ tests are the
safety net, and a green suite after a move only proves nothing *tested* broke.
Every step should be **mutation-checked**, not merely green — the same standard
used for the teardown and chain work.

**Step 2 is the one that can go quietly wrong.** If a fact is computed in the
wrong order, or a detector silently receives a stale one, receipts stay green
and say the wrong thing. Facts need their own spec asserting order and
freshness, not just their values.

**Do not migrate features and change them in the same commit.** A moved feature
must be byte-identical in behaviour; anything else makes a bisect useless.

**The `ctx` 28-vs-57 gap must be resolved, not carried.** Some of those 57 reads
are defensive, some are legacy, and some may be dead. Enumerate them during step
3 rather than reproducing the ambiguity in a new shape.

---

## 10. What this does NOT fix

- **A21** — identities coupled by coincidence. No structure closes it.
  *Delegate modules; gate seams yourself.*
- **Schema deletion stays manual.** By design: the wire contract is frozen, so
  removing a parameter must be a deliberate act, not a side effect of deleting
  a module.
- **The attempt loop stays a loop.** It is genuinely stateful, it carries the
  guards, and it is where a subtle change costs correctness. It shrinks because
  facts and findings leave it — it does not become declarative.
