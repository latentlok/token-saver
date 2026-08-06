# Handover — after the restructure round

**State: clean. Branch `v0.6`, 79 commits ahead of `origin/v0.6`, NOTHING PUSHED — deliberately.**
`bash ci/run-specs.sh` → exit 0, **1,227 tests** (was 1,013).
**All five patterns from DESIGN §7 are built.**
**Steps 1–7 done; 8's user-visible half done. The one real gap is `core/pipeline.py` — see below.**
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
    qd/core/status.py       classify(...)              -- the first VERB
    qd/core/violation.py    Violation(kind,trail,prompt,rider,notes)
    qd/core/attempt.py      Attempt(n, of, changed, writes)
    qd/core/runnable.py     of(args) -> Run | ChainOfRuns
    qd/core/prompt.py       compose/tail              -- the Decorator
    qd/core/pipeline.py     ratchet_minimum           -- started
    qd/features/detectors/  6 detectors, enumerable
    qd/features/gates/      2 gates (challenge, red)
    qd/features/guards/     4 guards (specs, brief, touch_scope, fixtures)

**Steps done:** 2 (findings), 3 (receipt-as-list, core), 4 (gates), 5 (container
+ T0 + attribution), 6 (resolver **and** the frozen `RunPlan`), 7 (Composite).
8's cheap half (query telemetry) done; the full fold left, deliberately.

**Parked items built:** G1 (`SUPPRESSED:`), G3 (`stuck_no_progress`), A1 (the red
gate), A6 (unmarked worker tests), step 8's cheap half (query call telemetry).

**The scaffolding is GONE, not smaller.** `DetectorInputs` was introduced in
step 2 as an explicitly temporary carrier, with its risk named out loud — a
general bag handed to every feature is `ctx` with a nicer name. 7 fields → 4
(step 5 took `work_cwd`/`pre_status`/`pre_sha`) → **deleted** (step 6's
`RunPlan` took `task`/`verify`/`touch_scope`; `created` went to `RunScope`).
Detectors are now `detect(facts, scope, plan)`. There is no bag to grow back.

---

## Honest measurements

**`_delegate`: 1,106 → 1,135 → 949 lines.** It GREW through steps 2–6 and only
began shrinking when the VERBS started leaving. The diagnosis is the important
part: **every step until then extracted a NOUN** — facts, findings, scope, plan,
blocks — **and none extracted the sequence that orders them.** `core/status.py`
and `features/guards/` are the first two verbs out; the loop's remaining phases
are what `core/pipeline.py` is still for.

**Three roles, now separate, and the separation is the point:**

    a gate     refuses the RUN       -- nothing is built        features/gates/
    a guard    fails the ATTEMPT     -- worker told, tries again features/guards/
    a detector reports on the RESULT -- nobody is stopped        features/detectors/

Guards are NOT pure and detectors are: most guards revert, because a spec edit
merely *reported* is a spec edit that stands. That asymmetry is why they are
three directories and not one with flags.

**Concurrency on snowy: measurement RETRACTED.** An earlier reading (throughput
flat, latency tripling from 1 → 3 concurrent) was taken while another process
held the GPU. Contention alone explains it, so the data cannot distinguish that
from vLLM declining to batch. `parallel_max: 4` stands; nothing measured argues
against it. FINDINGS lists what a real measurement would need.

**Endpoint reality:** ceiling is 4, another build uses ~2 intermittently, so keep
live tests at ≤3. Raw HTTP probes need `VLLM_TOKEN` in env; delegations do not
(the `qwen` CLI carries its own credentials).

---

## The patterns — 4 of 5 built, and the audit that found the gap

§7 adopts five patterns. Built: **Registry+Pipeline** (detectors, gates,
**guards**), **Strategy** (gates), **Builder** (`RunPlan`), **Composite**
(`runnable.py`). Not built: **Decorator** for the prompt — `task_suffix` + `HANDOFF` + `FINDINGS`
+ `CHALLENGE` + the chain preamble are still string concatenation across three
files. That is the last unbuilt pattern and a genuine remaining seam.

**I was wrong about Composite and the correction is on record.** DESIGN §8.1
argued against it, on the grounds that a batch and a chain are deliberately not
uniform. Half of that was wrong: Composite does not require nesting to be
ALLOWED, only that both kinds answer the same question — so the nesting refusal
moved to CONSTRUCTION (`runnable.NestingRefused`), which is what the design asked
for. The surviving half is why there is no shared `.execute()`: execution stays
with `run_chain`, which owns the worktree sharing and the between-link commits.

**Step 8's fold is still not worth it.** The user-visible payoff was query
telemetry, and that is done. The rest buys uniformity nobody can see, on the one
caller that has never misbehaved.

---

## What is left, best first

1. **`core/pipeline.py` — started, and read its docstring before continuing.**
   `status.py` and `features/guards/` took the two phases that were whole
   IDEAS. What remains in `_delegate` is largely **orchestration** — run the
   gate, share the verdict, time it, thread the result on — which is the loop's
   own job and does not improve by being moved somewhere and called. What
   belongs in `pipeline.py` is the *logic* those phases carry:
   `ratchet_minimum` is the first, and its "sum across files" rule had lived in
   a comment with no test. Look for more of those, not for more phases.
   *Also nominally missing from §5: `surface/schema.py` and `surface/runlog.py`
   — but those are MOVES of `qd/schemas.py` and `qd/runlog.py`. Cosmetic; left
   undone on purpose rather than churned for a tick in a table.*
2. ~~**A2 + A4**~~ **DONE.** `qd/core/contract.py`, `features/guards/clauses.py`
   (coverage gates link 1), `features/gates/contract.py` (the cross-link pin),
   and the non-droppable `CONTRACT:` receipt line. **One thing it did NOT
   close:** the red gate's check 3 is still an exception-type heuristic, because
   exactness needs the contract to declare its ENTRY POINT as a symbol rather
   than as prose. `red.py`'s docstring says so rather than claiming a precision
   it lacks — closing it means extending the contract format, not the gate.

3. **The free ones:** the two playbooks, a doctor check, the skill pass, server
   lifecycle, G5 (cold-vs-warm retry, answerable from existing telemetry).
4. **Release:** 0.6.0 is bumped and the changelog written. PR → CI →
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

**`/tmp` fills up, and full disk looks like test failures.** 55,933 leaked temp
dirs (9.1G) hit ENOSPC mid-session, and four unrelated tests "failed" purely
from it. `ci/run-specs.sh` traps and cleans its own `TMPDIR`; running a spec
file **directly** does not. Set `TMPDIR=$(mktemp -d)` for direct runs, and check
`df -h /tmp` before believing a surprising failure.

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
