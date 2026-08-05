# Handover — the modularity restructure

**State: clean. Branch `v0.6`, ahead of `origin/v0.6`, NOTHING PUSHED — deliberately.**
`bash ci/run-specs.sh` → exit 0, **1,008 tests**. **Step 1 is DONE** — start at step 2. Version is **0.6.0**; the
remaining release steps (PR → CI → squash-merge → tag) are the user's, not yours.

Your job is the restructure. Everything else is parked and mapped.

---

## Read these, in this order

| Doc | What it gives you |
|---|---|
| **[docs/DESIGN-modular-architecture.md](docs/DESIGN-modular-architecture.md)** | **the plan.** The problem measured, the target shape, the 8 steps, the patterns, the risks. Start here |
| [docs/PARKED.md](docs/PARKED.md) | everything NOT being built, and which step unblocks each item |
| [docs/ROADMAP.md](docs/ROADMAP.md) | the ledger scoreboard: 19 of 23 field findings closed, what remains |
| [docs/README-walkthroughs.md](docs/README-walkthroughs.md) | how delegate / chain / query behave, end to end, in plain language |
| [docs/PRINCIPLES.md](docs/PRINCIPLES.md), [docs/HLD.md](docs/HLD.md) | the standing doctrine and the architecture as it is today |

Do **not** read `docs/archive/plugin-improvement.md` unless you need a finding's
original cost. It is evidence, it is long, and the roadmap quotes what matters.

---

## Do NOT read the codebase into context

`qd/` is ~9,400 lines. Reading it is both unnecessary and against the point of
this product — the whole thesis is that a smart model orchestrates and does not
ingest source.

**Read this much and no more (~600 lines total):**

| For | Read |
|---|---|
| the plan | the design doc's §4 (facts vs findings) and §8 (the steps) |
| step 1 | `qd/engine.py` from `# --- Tree facts (C3) ---` to `# --- Advisory gates ---` — about 50 lines |
| what the detectors expect | the public signatures in `qd/gittree.py` — `grep '^def ' qd/gittree.py` |
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
| 2 | **Findings** | detectors return findings instead of writing into `ctx` |
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

## Step 1 is done — read it before starting step 2

`qd/core/facts.py` + `specs/facts_spec.py`. Read both; they are short, and step 2
is the other half of the same idea.

**What step 1 proved.** The extraction made visible something invisible while it
was one inline block: **the detectors write their results back INTO the facts
record** (`tf["uncalled"] = ...` in `qd/engine.py`). That is exactly the
facts/findings confusion design §4 describes. It is why `collect()` returns a
plain dict rather than a frozen type — the freeze lands in step 2, with them.

**What step 2 is.** Move `uncalled_symbols`, `mocked_seams`, `never_executed`,
`dodge_markers` and the strays check out of `_delegate` so they *return* findings
instead of writing into facts. Then freeze the facts record.

**One lesson from step 1's mutation pass, worth repeating.** Breaking the
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
