# Handover — the modularity restructure

**State: clean. Branch `v0.6`, ahead of `origin/v0.6`, NOTHING PUSHED — deliberately.**
`bash ci/run-specs.sh` → exit 0, **1,029 tests**. **Steps 1 and 2 are DONE** — start at step 3.
Ledger: 20 of 23 findings closed. Version is **0.6.0**; the
remaining release steps (PR → CI → squash-merge → tag) are the user's, not yours.

Your job is the restructure. Everything else is parked and mapped.

---

## Read these, in this order

| Doc | What it gives you |
|---|---|
| **[docs/DESIGN-modular-architecture.md](docs/DESIGN-modular-architecture.md)** | **the plan.** The problem measured, the target shape, the 8 steps, the patterns, the risks. Start here |
| [docs/PARKED.md](docs/PARKED.md) | everything NOT being built, and which step unblocks each item |
| [docs/ROADMAP.md](docs/ROADMAP.md) | the ledger scoreboard: 20 of 23 field findings closed, what remains |
| [docs/README-walkthroughs.md](docs/README-walkthroughs.md) | how delegate / chain / query behave, end to end, in plain language |
| [docs/PRINCIPLES.md](docs/PRINCIPLES.md), [docs/HLD.md](docs/HLD.md) | the standing doctrine and the architecture as it is today |

**Reference — open only when a specific question sends you there:**

| Doc | When |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | how a caller uses the plugin; every setting and its default |
| [docs/LLD.md](docs/LLD.md) | module-by-module detail behind HLD |
| [docs/OVERVIEW.md](docs/OVERVIEW.md) | the one-page pitch |
| [docs/FINDINGS.md](docs/FINDINGS.md) | measured results from earlier rounds |
| [docs/RELEASING.md](docs/RELEASING.md) | the release sequence (0.6.0 is bumped; PR → CI → tag remains) |
| [docs/DESIGN-v06-test-first.md](docs/DESIGN-v06-test-first.md) | the A14 replacement design — parked, lands after the restructure |
| [docs/PLAN-v06-ledger.md](docs/PLAN-v06-ledger.md) | historical: what the v0.6 code round closed and why |
| [docs/PENDING.md](docs/PENDING.md) | the longer-horizon v4 list |

That is every doc in the repo. Nothing else needs finding.

Do **not** read `docs/archive/` unless you need a finding's original cost. It is
evidence, it is long, and the roadmap quotes what matters.

---

## Do NOT read the codebase into context

`qd/` is ~9,400 lines. Reading it is both unnecessary and against the point of
this product — the whole thesis is that a smart model orchestrates and does not
ingest source.

**Read this much and no more (~600 lines total):**

| For | Read |
|---|---|
| the plan | the design doc's §4 (facts vs findings) and §8 (the steps) |
| steps 1–2, the finished shape | `qd/core/facts.py`, `qd/core/findings.py`, `qd/features/detectors/__init__.py` — about 150 lines, and the pattern step 3 copies |
| what step 3 must not break | `specs/detectors_spec.py` — the three `ReachesTheReceipt` tests are the only thing asserting a finding reaches the caller |
| the shape a spec takes | one existing spec, e.g. `specs/challenge_spec.py` |

**Locate code without reading it.** The repo has a structural graph
(`graphify-out/`, ~2.4 MB, no LLM involved):

```
graphify explain "delegate()"          # what a symbol connects to
graphify query "how are facts computed" --budget 2000
graphify affected "new_public_symbols" # who breaks if this changes
graphify god-nodes --top 10            # the hubs, i.e. where the risk is
graphify update . --no-cluster         # refresh after your own edits
```

That is how the numbers in the design doc were produced. Rebuild it once at the
start; it takes seconds and costs nothing.

**Use the plugin on itself.** `qwen_query` answers questions about this codebase
on free tokens without spending your context. Answers are leads to verify, not
truth — every one carries a `VERIFY:` list.

If you find yourself opening a fifth file, stop and ask the graph instead.

---

## The problem, in one paragraph

Adding one feature (`challenge_brief`) took five edits across four modules with
nothing to group or enumerate them. The pressure lands on two functions —
`_delegate` (1,111 lines, 105 locals) and `verdict.render` (888 lines, 20 inline
receipt branches) — whose interface is an untyped dict: engine writes 28 keys,
verdict reads 57. It compounds: in one day `_delegate` grew 1,041 → 1,111 and
`delegate()` went 58 → 61 graph edges. **The goal is that adding or removing a
feature becomes a local change.**

What is **not** wrong, and must not be "fixed": the import graph is acyclic,
every module outside those two functions has a longest function under 130 lines,
and the detectors are already pure functions with honest signatures. **The logic
is not tangled — the wiring is.**

---

## The eight steps

Full detail in the design doc §8. Summary:

| # | Step | One line |
|---|---|---|
| 1 | ~~**Facts**~~ | **done** — `qd/core/facts.py` + `specs/facts_spec.py` |
| 2 | ~~**Findings**~~ | **done** — `qd/core/findings.py` + `qd/features/detectors/` + `specs/detectors_spec.py` |
| 3 | **Receipt as a list** | the report builds from registered blocks, not 20 hardcoded branches |
| 4 | **Gates** | the things that can refuse a run get one shape |
| 5 | **Scope** | one owner for worktree + session + call log |
| 6 | **Plan** | one place that resolves settings, once |
| 7 | **Composite** | a run and a chain-of-runs become the same kind of thing |
| 8 | **Query folded in** | it becomes a run with most capabilities off |

Steps 1–3 deliver most of the benefit. After step 3, adding a feature never
touches the renderer.

**`_delegate`'s post-run block is not a step.** It is four concerns sharing one
region (facts, detectors, worktree disposition, cost/refs/brief). It **drains**
as steps 1, 2 and 5 each take their part. Do not lift it whole — an earlier
draft said to, and that would build a four-responsibility function that the next
steps immediately dismantle.

---

## Steps 1 and 2 are done — read them before starting step 3

`qd/core/facts.py`, `qd/core/findings.py`, `qd/features/detectors/`, and their
two specs. They are short, and together they are the pattern step 3 copies.

**What step 2 settled.** Facts are observations (`changed`, `pubs`); findings
are judgements reached by reading them (`UNCALLED`, `MOCKED SEAM`). Detectors
now RETURN findings and the facts record is frozen, so the old
`tf["uncalled"] = ...` write-back raises instead of quietly working. Detectors
have no ordering between them and `DETECTORS` can be listed, so adding one is a
file plus a line.

**Three things step 2 cost, worth knowing before step 3 repeats them.**

1. **A green suite hid a real bug.** The detectors sat in two unequal `try`
   blocks, and one failed grep discarded EVERY fact — the receipt lost CHANGED
   and COMMITTED and fell back to the v1 path, silently. Nothing raises on a
   healthy tree, so 1,013 passing tests never showed it. It was fixed BEFORE the
   move (`177530b`) on purpose: a registry loop isolates detectors naturally, so
   moving first would have hidden a behaviour change inside a refactor.

2. **I was wrong about which specs were the net.** The plan said pin TEST DODGE
   at the receipt first; it had been pinned since `engine_spec:1689`. The
   unpinned ones were the three SEAM findings — `seams_spec` calls the gittree
   functions directly, so it proves the greps work and proves nothing renders
   what they return. **Step 3 rewrites the renderer, so check this first:
   which findings have a test asserting on rendered text?** For anything that
   does not, the renderer can stop printing it and the whole suite stays green.

3. **`facts → Finding` was not the real signature.** Five of five detectors need
   `verify`, `task` or `touch_scope` — the brief, not the tree.
   `DetectorInputs` carries them, frozen and closed, each field annotated with
   the step that takes it away. Expect the same gap in step 3 and resist the
   same temptation: a general bag handed to every feature is `ctx` renamed.

**`_delegate` grew.** 1,106 → 1,111 lines, while `engine.py` shrank 2,132 →
2,116. The detector logic left; seven named inputs cost more lines than five
terse calls. Step 2 bought enumerability, not size, and the size comes back in
steps 5–6 when `DetectorInputs` dissolves. Do not report it as a size win.

**One lesson from step 1's mutation pass, still worth repeating.** Breaking the
changed-set derivation SURVIVED the first spec: a deleted file still shows up in
the post status, so the obvious test did not bind. The case that actually breaks
is a file dirty at T0 that the run puts back — it disappears from the post status
entirely. Mutate every branch, not the one you had in mind when you wrote it.

---

## House rules

These are not optional; the repo is built on them.

1. **Spec first, and the spec must fail before the fix.** Write the spec, watch
   it go red, then fix. A spec that never failed proves nothing.
2. **Mutation-check.** After a fix passes, break it deliberately and confirm the
   spec goes red. A green suite only proves nothing *tested* broke.
3. **Never delegate a `specs/*_spec.py` file.** They define what correct means.
   `spec_globs` in `.qwen-delegate.json` auto-reverts worker edits to them.
4. **Run the full suite before every commit:** `bash ci/run-specs.sh` → exit 0.
5. **Comments explain WHY, with evidence.** Match the surrounding density. The
   codebase records measured failures next to the code that prevents them; keep
   doing that.
6. **Do not migrate a feature and change it in the same commit.** A moved
   feature must be behaviourally byte-identical, or a bisect is useless.
7. **The endpoint (`snowy`) is live.** Live-test anything whose behaviour a
   hermetic spec cannot prove — and mutation-check the live test too (see below).

---

## Traps, all of them paid for today

**`git checkout <file>` on unstaged work destroys it.** Used to undo a live
mutation test, it reverted an entire feature's uncommitted changes, and the
commit that followed shipped half the work. Stage before mutation-testing, or
copy the file aside.

**A broad `except Exception` will hide your bug.** The first `challenge_brief`
swallowed a plain `NameError` (engine never imported `verdict`) and every
challenge silently returned "no objection" — the feature looked like it worked.
Keep `try` around the call that can legitimately fail, nothing more.

**A live test that passes may not be testing anything.** My first A11 live test
passed identically with and without the fix. Always run the live test against
the *mutated* code too.

**Measure before claiming.** `challenge_warm` was designed as "one prefill
instead of two" and measured at **+50% input tokens** — a resumed session
re-sends its history every turn. The A/B was only possible because per-call
telemetry logs the calls separately.

**Check what a shared helper actually does.** `accum_stats` increments
`cum["attempts"]` on every call; reusing it for a non-attempt call would have
over-reported every receipt by one.

**Pin behaviour, not incidentals.** A doctor spec asserted the string
`ollama ps` appeared in a finding, so retiring a vendor broke a spec that was
never about that vendor.

---

## What is already built and must keep working

Do not regress these; they were expensive.

- **Concurrency.** Separate calls and `batch` both run genuinely in parallel
  (measured: peak 4 workers on `parallel_max: 4`). Every spawned child gets
  `stdin=subprocess.DEVNULL` — a child inheriting the server's stdin races the
  JSON-RPC reader for protocol bytes.
- **Chains share one worktree**, committing between links, so link 2 sees link
  1's work — and link N's handoff is forwarded to link N+1.
- **Batch of chains.** A chain item takes **no** batch-level guard; `run_chain`
  guards per link, and double-guarding deadlocks a one-slot endpoint.
- **`challenge_brief`** is default ON, blocks only on evidence naming a path
  that exists, skips diagnosis runs, and runs once at a chain's head.
- **Per-call telemetry** (`ExecutorCall` / `CallLog`) lands in `runs.jsonl`,
  deliberately **not** in the receipt — the receipt is context the caller pays
  for on every run.

---

## Where the parked work goes

[docs/PARKED.md](docs/PARKED.md) maps every parked item to the step that
unblocks it. The short version: most of the test-first pipeline waits on steps
1–4, nothing waits past step 6, and **roughly half the list is not blocked at
all** (the playbooks, the doctor checks, server lifecycle, the PENDING
carryovers, the decisions owed).

If you want a quick win before starting, those are it.

---

## Decisions — all closed

- ~~**Version.**~~ **Decided: 0.6.0.** Bumped in `.claude-plugin/plugin.json`
  and `qd/server.py` `SERVER_INFO` (they must stay equal), changelog entry
  written. Remaining per `docs/RELEASING.md`: PR → CI → squash-merge → tag
  `v0.6.0` on master → GitHub release.
- ~~**`reset_worktree()`**~~ **Decided: keep.** Two lines, spec'd, costs
  nothing. Its spec pins `clean -fd` and NEVER `-fdx` -- the `-x` would
  delete gitignored files, i.e. someone's `venv/`. Deleting the function
  deletes that warning with it.
- ~~**`challenge_brief` false positives.**~~ **Decided: tighten the prompt.**
  It blocked a buildable brief because `total_for` implies aggregation over a
  dict holding one value per key -- defensible, evidence-backed, still a block
  on work that could have been built as asked. The prompt now leads with one
  test (*can you build something that satisfies this brief?*) and names the
  non-reasons explicitly: naming, duplication, a design you would prefer,
  anything you would raise in review rather than refuse to start. Verified
  live: the false claim still blocks, the name quibble now builds.

**Nothing is pushed, on purpose.** Commit freely on `v0.6`; do not push.
