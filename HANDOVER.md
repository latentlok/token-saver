# Handover — after the restructure round

**State: clean. Branch `v0.6`, 57 commits ahead of `origin/v0.6`, NOTHING PUSHED — deliberately.**
`bash ci/run-specs.sh` → exit 0, **1,120 tests** (was 1,013).
**Steps 1–6 are done. 7 and 8 are reassessed — read §"Steps 7 and 8" before building them.**
Verified live against `snowy` several times, including two mutation-checked live runs.

The restructure has delivered its headline: **adding or removing a feature is a
local change.** What is left is smaller than what is done.

---

## Read these, in this order

| Doc | What it gives you |
|---|---|
| **this file** | state, what changed, and the one lesson worth carrying |
| [docs/FINDINGS.md](docs/FINDINGS.md) | **the last four entries.** This round's measured results, and the most transferable thing in the repo |
| [docs/DESIGN-modular-architecture.md](docs/DESIGN-modular-architecture.md) | the plan. §8 has the step table (1–6 ticked); **§8.1 reassesses 7 and 8** |
| [docs/PARKED.md](docs/PARKED.md) | what is not built, and what unblocks each |
| [docs/PRINCIPLES.md](docs/PRINCIPLES.md) | the doctrine. Every bug found this round was a corollary of it |

Reference, open only when sent there: [USAGE](docs/USAGE.md) ·
[HLD](docs/HLD.md) · [LLD](docs/LLD.md) · [ROADMAP](docs/ROADMAP.md) ·
[DESIGN-v06-test-first](docs/DESIGN-v06-test-first.md) ·
[RELEASING](docs/RELEASING.md) · [README-walkthroughs](docs/README-walkthroughs.md)

---

## Do NOT read the codebase into context

Still true, and now cheaper to obey — the new modules are small and each states
its own rule.

| For | Read |
|---|---|
| the shape | `qd/core/facts.py`, `findings.py`, `scope.py`, `plan.py` — ~250 lines, and they are the pattern |
| how a feature is added | `qd/features/detectors/unmarked_tests.py` — the whole of A6, built last, one file |
| what must not break | `specs/detectors_spec.py` `ReachesTheReceipt` — the only tests asserting a finding reaches a caller |

`graphify explain "delegate()"` · `graphify affected "<symbol>"` ·
`graphify update . --no-cluster` after your edits. `qwen_query` answers questions
about this repo on free tokens — leads to verify, never truth.

---

## The one lesson

**Nine bugs were found this round. Every one passed a fully green suite, and
every one shared a single property: the failure looks like success.**

| Where | What broke silently |
|---|---|
| detector isolation | one failed grep discarded EVERY fact the run collected |
| receipt drop order | warnings shed under the size cap, bookkeeping kept |
| dropped findings | vanished with no trace anywhere |
| gate refusal | a run the system decided to refuse got built anyway |
| spec revert base | a worker that COMMITS its spec sabotage got it restored intact |
| spec violation status | reported as a gate failure, not a worker touching the gate |
| permission precedence | `shell_allow=[]` silently widened to the project's list |
| gates hostage | `challenge_brief: false` disabled EVERY refusal in the system |
| detector with no emit | a finding computed, and rendered nowhere |

A suite built by asking *"does this work?"* cannot find these, because they all
answer **yes** — right until the moment the answer stops mattering. The question
that finds them is:

> **What would still be green if this stopped working?**

The only way to ask it is to break the thing and look. Two of the nine came from
a deliberate sweep; the other seven turned up while touching something else,
which is a rate that argues for sweeping more.

**The sharper half:** of the nine, **five were WIRING, not logic.** In each case
the mechanism had a good passing test and the thing connecting it to the engine
had none. §1 of the design says the same from the other side — *the logic is not
tangled, the wiring is.* **Pin things where they run, not where they are defined.**

---

## What changed

**23 commits.** New modules, each with a spec:

    qd/core/findings.py     Finding(kind, data)        -- a judgement, not an observation
    qd/core/scope.py        RunScope                   -- what a run owns and disposes of
    qd/core/plan.py         setting(...)               -- four-layer precedence, once
    qd/surface/receipt.py   Block(kind,text,drop,pri)  -- the receipt as a list
    qd/features/detectors/  6 detectors, enumerable
    qd/features/gates/      2 gates (challenge, red)

**Steps done:** 2 (findings), 3 (receipt-as-list, core), 4 (gates), 5 (container
+ T0), 6 (settings resolver).

**Parked items built:** G1 (`SUPPRESSED:`), G3 (`stuck_no_progress`), A1 (the red
gate), A6 (unmarked worker tests), step 8's cheap half (query call telemetry).

**The scaffolding shrank as promised.** `DetectorInputs` was introduced in step 2
as an explicitly temporary carrier, with the risk named out loud — a general bag
handed to every feature is `ctx` with a nicer name. It began at **seven fields
and is now four**, three of them marked for the work that would take them.

---

## Honest measurements

**`_delegate` GREW: 1,106 → 1,135 lines.** The logic left; the named inputs that
replaced it cost more lines than the terse calls did. Steps 2–6 bought
*enumerability*, not size. Do not report this as a size win.

**Concurrency on snowy: measurement RETRACTED.** An earlier reading (throughput
flat, latency tripling from 1 → 3 concurrent) was taken while another process
held the GPU. Contention alone explains it, so the data cannot distinguish that
from vLLM declining to batch. `parallel_max: 4` stands; nothing measured argues
against it. FINDINGS lists what a real measurement would need.

**Endpoint reality:** ceiling is 4, another build uses ~2 intermittently, so keep
live tests at ≤3. Raw HTTP probes need `VLLM_TOKEN` in env; delegations do not
(the `qwen` CLI carries its own credentials).

---

## Steps 7 and 8

Reassessed in **DESIGN §8.1** after 1–6 shipped. Argued there in full:

- **Step 7 (Composite): recommended NOT as specified.** Its stated benefit —
  making "nesting is one level" a property rather than a refusal — contradicts a
  deliberate decision documented in `_batch_item` itself. Composite treats leaf
  and composite uniformly; here they are non-uniform *on purpose* (a batch is
  unordered and parallel, a chain is ordered and shares a worktree). It would
  replace a four-line type check. **What actually needs something is G2**, which
  needs a chain to be an addressable value — one small record, built as part of
  G2, not a hierarchy built in advance of it.
- **Step 8: cheap half DONE.** Query now logs its call. The remaining fold buys
  structural uniformity a caller cannot see, on the best-behaved caller in the
  system. Leave it until something needs it.

---

## What is left, best first

1. **Finish step 3's tail — the SLOT mechanism.** *Adding a detector is one file
   plus one line* is **still false**: the renderer names each detector's
   placement, so a new detector needs a line there too, and one with the
   registry entry but not the render line computes a finding nobody sees. A6's
   spec pins the gap; closing it needs an explicit `SLOT` per detector so
   placement is derived. **Placement is the size cap's tie-break, so this is a
   behaviour change — golden-diff it.**
2. **A4 / A2 (clause coverage + contract pinning).** The red gate's fourth check
   is unbuilt because it needs the contract format. With it, "failed for a
   legible reason" becomes exact rather than an exception-type heuristic.
3. **G2 (whole-chain contradiction).** Needs the small chain record above.
4. **G4 (brief-vs-diff advisory).** Unblocked. Must stay advisory — a witness
   that can refuse breaks §I.
5. **The free ones:** the two playbooks, a doctor check, the skill pass, server
   lifecycle, G5 (cold-vs-warm retry, answerable from existing telemetry).
6. **Release:** 0.6.0 is bumped and the changelog written. PR → CI →
   squash-merge → tag is the user's, per [docs/RELEASING.md](docs/RELEASING.md).

---

## House rules — unchanged, and they earned their keep

1. **Spec first, and it must fail before the fix.** Where the code is already
   right and only the test was missing, the red phase is the **mutation**.
2. **Mutation-check everything, including live tests.**
3. **Never delegate a `specs/*_spec.py`.**
4. **Full suite before every commit:** `bash ci/run-specs.sh` → exit 0.
5. **Comments explain WHY, with evidence.**
6. **Do not migrate a feature and change it in the same commit.**
7. **Live-test what a hermetic spec cannot prove — and mutation-check that too.**

---

## Traps paid for this round

**Restore by file copy, never `git checkout`.** Used ~30 times this round with no
loss. The trap it avoids is on record from the previous round.

**A live test whose observable depends on the MODEL cannot discriminate.** An
A/B of the `shell_allow` fix returned identical results in both arms — the worker
never ran a shell command, so the allowlist was never consulted. It measured the
model's choice of tool. Test deterministic things at the seam; spend live runs on
what only a live run shows. The two live mutations that *did* work both sat on a
path every run takes.

**Count `ERROR:` as well as `FAIL:`.** A mutation check reported "nothing caught
this" when three tests had. Prefer the suite's exit code.

**A sentinel is truthy.** A mutation intended only to skip falsy answers also
broke missing-key handling, muddying what it proved.

**Check the merge before claiming a divergence.** I nearly reported two config
resolutions as disagreeing; they were equivalent, and my *test* had rebuilt one
side wrong.

---

## What is already built and must keep working

- **Concurrency.** Separate calls and `batch` run genuinely in parallel; every
  spawned child gets `stdin=subprocess.DEVNULL`.
- **Chains share one worktree**, committing between links. **A link never
  disposes of a borrowed container** — pinned in `scope_spec.py` after a
  mutation showed a failing link would delete earlier links' committed work.
- **Batch of chains.** A chain item takes no batch-level guard.
- **`challenge_brief`** default ON, blocks only on evidence naming a path that
  exists, once at a chain's head, per-link opt-in via `challenge_brief: true`.
- **Per-call telemetry** in `runs.jsonl`, deliberately not in the receipt.

**Nothing is pushed, on purpose. Commit freely on `v0.6`; do not push.**
