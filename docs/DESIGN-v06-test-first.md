# Design — test tiers, the test-first pipeline, and the dispatch rule

Successor to [PLAN-v06-ledger.md](PLAN-v06-ledger.md) §2.1 (the self-gate default, A14).
That entry describes a symptom; this describes the mechanism that replaces it.

**Status: design, nothing built.** Five defects found while designing are recorded in §9;
three of them exist today independent of anything here.

This doc has been through one adversarial review; its findings are folded in place rather
than listed separately. Two are worth knowing about because they were *wrong design*, not
omissions, and both would be easy to re-invent: the red gate's original "fail by assertion,
not by error" rule would have rejected every correct greenfield artifact (§6.1), and the
cross-link contract check was specified with no channel to carry it (§3.3).

---

## 1. The problem, restated

A14's measurement: runs 1–9 under the default gate produced **zero** mergeable units;
runs 10–21 under a worker-written gate + `preflight_expect="red"` produced **11 of 11**
one-shot successes. Same model, same repo, same day.

The ledger read this as "the default gate is too weak." That is right but not the whole
shape. The default gate is `detect_test_cmd()` — the project's existing suite, which on a
healthy repo is already green, so the verdict cannot distinguish real work from a no-op.
The `min_override` ratchet (`_ensure_self_gate`) partially mitigates it by requiring N+1
tests, but a count is not a contract.

What the successful runs had was not a *stronger* gate. It was a gate **the run could not
have passed without doing the work**, authored before the work and frozen against it.

Everything below is machinery for producing that condition reliably.

---

## 2. Test tiers

### 2.1 Why

A gate has two jobs that pull in opposite directions: prove the work happened, and finish
fast enough to run 4–5 times per delegation (preflight, the `trust="self"` ratchet re-run,
then once per attempt at `_DEFAULT_MAX_ITER = 3`).

Today there is no vocabulary for this. `detect_test_cmd()` returns **one string** and every
consumer — the `QWEN.md` testing line, the `_SELF_GATE` suite, `never_executed()`'s
coverage check — uses that same string for a different purpose.

The only tier that exists anywhere is a hand-maintained `case` in `ci/run-specs.sh`
excluding `dispatch_spec.py` for wall-clock flake. The insight is already in the repo; it
just isn't a concept the code knows.

### 2.2 Config shape

```json
"tests": {
  "unit":        "pytest tests/unit -q",
  "integration": "pytest tests/integration -q",
  "e2e":         "pytest tests/e2e -q"
}
```

**Declared, never guessed.** Guessing a single command fails *visibly* — wrong command,
obviously broken gate, noticed immediately. Guessing a tier map fails *silently*: mislabel
the integration suite as `unit` and every delegation either eats the wall clock or refuses
on `GATE UNUSABLE`; mislabel a subset as "the unit tier" and you under-gate forever with no
symptom.

`detect_test_cmd()` is **not** removed. It remains the fallback for untiered projects and
for the `unit` tier itself. The change is narrow:

- project declares `tests` → use it
- project declares nothing **and the task crosses a seam** (§5) → refuse and ask

"Ask" over stdio means *refuse with the question*. Precedent exists twice:
`trust: "auto"` refuses a bare call and makes the caller pick (`engine.py:720`), and
`_shape_refusal` refuses `chain`+`batch` by name before anything spawns.

### 2.3 Why the delegated worker gates on `unit` by default

Three reasons, in increasing order of force:

1. **Retry amplification.** 4–5 gate runs per delegation. A 5-minute integration suite is
   20–25 minutes of gate per unit of work.
2. **The code already refuses it.** `_DEFAULT_VERIFY_TIMEOUT = 300`, and a preflight that
   blows it returns `GATE UNUSABLE` *before any work*. A slow gate doesn't degrade the run;
   it kills it.
3. **The worker structurally cannot run them.** With `"worktree": "auto"` the build happens
   in `~/.qwen-delegate/worktrees/<slug>/` — a fresh checkout from HEAD. **A git worktree
   contains only tracked files.** No `.env`, no `node_modules/`, no `venv/`, no seeded
   database, no docker volumes. An integration suite fails on a missing import long before
   reaching an assertion. And the worker runs under `scoped` approval with a denial hook;
   handing it live credentials to fix that would dismantle the sandbox.

(1) and (2) are tradeoffs. (3) is not — it is an impossibility.

### 2.4 The cost of unit-only, stated honestly

Unit tests are fast *because they mock the outside world*. The plugin already ships
`MOCKED SEAM:` precisely because a delegated worker's cheapest route to green is mocking
the thing that is broken. Making the unit tier the gate **by policy** makes that route the
sanctioned one.

This is not a reason to reject the policy. It is the reason the receipt must say
`gated on unit tier — integration deferred, not passed`, and why §7's demotion exists. A
unit-gated green and a full-gated green must not render identically.

### 2.5 Directory shape and provenance

Mirror the source tree, with per-module subfolders:

```
tests/unit/notifications/test_send_qwen.py
      ^tier                    ^provenance
```

**Tier and provenance are orthogonal axes and must stay that way.**

- Tier decides *when it runs*.
- The `_qwen` suffix decides *whose it is* — for auditing and for grading whether the
  worker's tests are good enough.

Splitting by **author into separate directories** is the `NEVER EXECUTED:` defect by
construction, and `ci/run-specs.sh` already argues against it in prose:

> Worker-written tests (`*_qwen.py`) run here too. They are never a delegation GATE — that
> stays the Claude-authored spec — but once their work is accepted they are regression
> coverage like any other, and a test file that ran once and is never run again protects
> nothing.

Author determines *authority*. Requirements determine *tier*. Two different questions.

Enforcement is cheap: the naming rule is one line in the generated `QWEN.md`
(`render_worker_rules`), and compliance is one check against `_created()`. **Report, never
auto-rename** — renaming breaks imports.

Mirroring the source tree also makes §5's derivation mechanical: changed
`notifications/service.py` → expected `tests/unit/notifications/` → missing or unchanged →
flag. No inference.

---

## 3. The contract

The orchestrator is a software architect with worker engineers. It does not read code and
should not start.

**An architect does not write tests. They write acceptance criteria.** That distinction
dissolves the "the orchestrator doesn't know the code" objection entirely, because
acceptance criteria require no code reading.

```
C1  send_notification(user_id: int, message: str) -> NotificationResult
    lives in notifications/service.py
C2  after a successful call, a row exists in `notifications`
    with status='sent' for that user
C3  on transport failure, the row exists with status='failed'
    and the call does not raise
```

Two things matter:

- The **behavior** is the acceptance criterion.
- The **entry point** — module path and signature — is the part people omit, and it is why
  test-first desynchronises: run A invents `send_notification`, run B builds `notify_user`,
  and the test fails for a reason unrelated to correctness.

When the architect does not know the terrain, that is what `qwen_query` is for: read-only,
plan mode, free tokens, no gate. Ask what the area talks to, get a map, write the contract.

### 3.1 The contract declares the seam — nothing predicts it

> "a row exists in `notifications` with `status='sent'`" → a DB seam, declared
> "returns the string formatted as X" → no seam

The architect does not need the worker to *predict* seams. Writing the acceptance criterion
**is** declaring one. Nothing is inferred, because the person who knows what they are asking
for said it.

This matters because the alternative — asking the worker to read the code and forecast
seams — fails three ways:

- A query answer is a claim, not evidence. The tool's own schema says so: *"Answers are
  LEADS to verify, not truth"* (`qd/schemas.py:195`).
- The errors are asymmetric. A false "seam" costs a pipeline you didn't need; a false "no
  seam" ships unguarded seam-crossing code under a green receipt.
- The query reads code that **exists**. The seam question is about the change that does
  **not yet**.

So the three mechanisms stack, each doing only what it is good at:

| Mechanism | Role | Trust |
|---|---|---|
| `qwen_query` | maps the terrain so the architect can write the contract | advisory |
| the contract | **declares** the seam | authoritative — the architect wrote it |
| post-hoc detection (§7) | catches contracts that were weak or skipped | the only one that is *evidence* |

### 3.2 Where the contract must live

Today, acceptance criteria have **no durable home**:

| Store | Holds | Durable? |
|---|---|---|
| runlog `.qwen-delegate/runs.jsonl` | `"task": digest(...)` — head + hash only | no, one-way by design |
| stored brief `.qwen-delegate/briefs/<session>.json` | full args incl. `task`, `verify` | no — self-ignoring dir, session-scoped, `store_briefs: false` kills it |
| playbook (`brief_file`) | the document itself | **yes** — git-versioned, `BRIEF: path @ digest` pinned in the receipt, worker edits auto-reverted |

`digest()` keeps only a head and a hash *deliberately*, so whole prompts — which embed real
source — do not accumulate in a permanent log. `save_brief`'s own docstring scopes it as "a
working file for ONE session."

So: **the contract is its own versioned file** — `contracts/<feature>.md`, git-tracked,
which the chain's links point at. Not `vars`: values passed through `vars` land only in the
gitignored, session-scoped brief and are gone by review time, which defeats both §6's clause
coverage (a clause ID must address something stable) and the §2.5 audit goal (*was the
worker's test any good* — asked weeks later). And not inside a pipeline playbook either: the
pipeline is generic and reused, the contract is per-feature (§4.2).

### 3.3 The contract is load-bearing, so it must be pinned

Once the contract is a separate file, it becomes an **input to the gate that can change
without anyone noticing** — the same class of defect as a worker editing a spec, one level
up. Three ways it bites:

- the worker edits the contract to match what it built
- the contract is edited between link 1 and link 2, so the gate was written against one
  document and the implementation graded against another
- the contract is edited between the run and review, so the receipt you audit no longer
  describes the criteria that ran

The brief document already solves exactly this and the machinery is reusable: `brief_sha0 =
file_sha(work_cwd, brief_rel)` is captured right after the T0 snapshot, edits are reverted,
and the receipt pins `BRIEF: path @ digest`. Give the contract the same three:

1. **Protection from the worker** — the contract path is covered by `spec_globs`. Config
   only, no new code; worker edits auto-revert like any spec edit.
2. **Pinned in the receipt** — `CONTRACT: contracts/<feature>.md @ <digest>`, so a reviewer
   weeks later can tell whether the file they are reading is the file that ran.
3. **Compared across links** — link 2 refuses if the contract digest differs from the one
   link 1 recorded. A gate written against a different document is not evidence about this
   one, and the check is a string comparison.

(3) is the chain-specific half and the one nothing else covers. Without it, the pipeline's
whole premise — *the gate was frozen before the implementation* — is true of the test file
and false of the thing the test file was derived from.

**Where link 2 reads link 1's digest.** Not from the receipt: receipts are text returned to
the caller, and there is no state channel between links. In-memory state would work for one
server call and break on retry or resume. Instead **link 1 writes the digest into the
artifact it commits** — a header line in the test file:

```python
# contract: contracts/notifications-send.md @ a3f9c1
```

It is then in the tree link 2 inherits, committed, protected by `spec_globs`, and it
survives a retry. No new state channel, and the pin travels with the thing it pins.

---

## 4. The test-first pipeline

### 4.1 Shape

1. **Link 1** — fresh session, writes *only* the test, from the contract. Never sees an
   implementation plan.
2. The test is committed, becoming tracked and therefore protected by `spec_globs`.
3. **Link 2** — fresh session, implements. `verify` = link 1's test. `preflight_expect="red"`.

Link 2 **cannot edit the gate** — `violated_specs` / `revert_specs` auto-revert. That is
the whole mechanism: it converts self-grading into **contract-grading against a frozen
artifact**.

This is consistent with A14's own data. The gate being worker-written was never the
problem. The gate being *editable by the run it gates* was.

The naive version — one delegation that writes the test and then the implementation — buys
nothing. Same session, same context, so the model co-adapts: writes a test it knows it can
satisfy, then satisfies it.

### 4.2 Composition — the chain owns the sequence, playbooks own the links

**A playbook is ONE delegation's brief, saved so it need not be retyped.** That is what it
was built for, and it is the right scope for it. A chain is control flow. So:

> a playbook can be a link in a chain; a chain should not live inside a playbook.

The code already says this — the refusal when `brief_file` rides beside `chain`:

> *"`brief_file` describes ONE delegation (or compiles to its own chain) — it cannot ride
> beside `chain`/`batch`. **Put brief_file on the items instead.**"* (`engine.py:589-593`)

So the shape is:

```
chain: [
  { brief_file: "playbooks/write-gate.md",
    vars: { contract: "contracts/notifications-send.md", test_path: "..." } },
  { brief_file: "playbooks/implement.md",
    vars: { contract: "contracts/notifications-send.md", test_path: "..." },
    preflight_expect: "red" },
]
```

Each playbook is generic and reusable across features. The per-feature contract is its own
versioned file (§3.2). The chain composes them.

`expand_playbook` (`engine.py:578`) does support `chain: true` inside a single document,
compiling `## Step <n>` sections into links. That is a convenience for the one-file case,
not the layering — **test-first should not use it**, because the pipeline is generic and the
contract is not, and one file cannot be both.

**Design invariant: LLM decisions live inside a link, never between links.** Which step runs
next, what gates it, whether it passed, whether to halt — all code or document, none of it
inference.

### 4.3 Two protections, two objects

A playbook protects what the worker was **told**. It does nothing for what the worker
**wrote**. Do not let one stand in for the other:

| Object | Protected by | Mechanism |
|---|---|---|
| the instructions | the playbook | worker edits to the document are reverted like a spec edit |
| the test (the gate) | chain plumbing | `spec_globs` + tracked file + commit between links (§8.2) |

Freezing the gate is entirely §8's work. It is not a playbook feature and cannot be made
one.

### 4.4 Do not collapse single delegation into `chain(1)`

They differ in **who authored the gate relative to the run being gated**:

- single delegation — the gate pre-exists the run (caller's `verify`, or `_SELF_GATE`)
- chain — link 2's gate was authored by link 1, a *different run*, frozen by a commit

Two different evidence models. The receipt must be able to say which one you got; collapsing
makes that invisible, which is the exact failure class this round is about.

Also: halt-on-red is the chain's defining semantic and is vacuous at length 1. And
`CHAIN_ARG` is reserved specifically so *"a hand-written call cannot claim to be link 3 of a
chain that never ran"* (`engine.py:70-73`) — if every call is a chain, that guard stops
meaning anything.

---

## 5. The dispatch rule

**Chain is not the default. The contract selects the shape.**

```
call carries acceptance clauses  →  test-first chain
call carries only a task         →  plain delegation + post-hoc seam demotion (§7)
read-only question               →  qwen_query
```

Chain-as-default fails worse than opt-in: forcing a pipeline forces a contract, and a
forced contract is a fake one. `task="fix the off-by-one in parse_range"` has no acceptance
criteria and should not manufacture any — if it does, the worker writes a test restating
the brief (A23), and a green gate now defends a restatement where before there was simply a
fix. That is a downgrade.

Under this rule the orchestrator never picks a *mode*. It picks how much specification it is
willing to write, and the shape follows mechanically — the same "no LLM decision" principle
applied one level up. The safe path is neither opt-in nor forced onto trivial work.

`qwen_query` is **not** a lower-priority sibling. It is read-only, ungated, and not a
delegation. Under this design it becomes *more* central: contracts must be written from
something.

---

## 6. Verifying the gate matches the contract

Layered, cheap to expensive. Layers 1–2 are the plugin's job and are free; layer 3 is the
architect's and the goal is to make it cost a glance.

**Layer 1 — is it a real test?** (mechanical) The red gate, four checks (§6.1). Proves the
file is a gate rather than a broken import wearing a red costume.

**Layer 2 — is it sensitive to the work?** (free) The chain gives red → green for nothing.
Proves sensitivity to *something*.

**Layer 3 — does it mean what you asked?** (~40 tokens) Neither layer above can answer this;
only the contract's author knows what the contract meant. So do not try to automate it —
surface it:

```
GATE CONTRACT: 3 clauses
  C1 entry point + signature       → test_signature_matches   assert isinstance(r, NotificationResult)
  C2 row exists, status='sent'     → test_sent_row_is_written assert row.status == "sent"
  C3 does not raise on failure     → UNCOVERED
+ 7 further assertions, no clause
```

**Bounded by the contract, not by the test.** Echoing the test's assertions makes receipt
size scale with the test — unbounded across parametrized suites, batches and chains.
Echoing the *clauses* makes it scale with what the architect wrote thirty seconds ago. The
remaining assertions are a count, never a list.

Three consequences that keep it cheap:

- **No contract, no line.** Same discipline as the TEST DODGE fix: a detector that fires
  with nothing to say is one nobody reads.
- **The mapping is a grep.** Clause IDs plus one `QWEN.md` rule ("each test names the clause
  it covers") makes coverage mechanical.
- **`UNCOVERED` is a status change, not a warning.** If C3 has no test, the gate does not
  gate what was asked. Act, don't narrate.

A 40-clause contract *should* produce 40 lines — that is the size of what was asked. A
receipt too long to read is then a symptom of an over-scoped delegation, which is useful
signal rather than a receipt problem.

### 6.1 The red gate, specified

An earlier draft of this doc required link 1's test to fail **by assertion, not by error**.
That is wrong, and it would have rejected every correct greenfield artifact: a test-first
test imports a symbol that does not exist yet, so it fails at module import — `ERROR` in
unittest, exit 2 in pytest. The rule intended to catch broken tests would have caught only
the correct ones.

The achievable version is four checks:

1. **The test file itself parses and collects.** A syntax error in the test is fatal; it is
   the one thing that is unambiguously the worker's fault.
2. **At least one test ran, and none were skipped.** Zero collected is not a gate, and a
   skip reads as a pass (D1, A19).
3. **Every clause has a test naming it.** See §6.2 — this replaces a `min_tests` floor,
   which link 1 does not otherwise have.
4. **It failed for a legible reason** — either an assertion failure, *or* an error whose
   traceback names the entry point C1 declares does not exist yet. Anything else — an error
   unrelated to C1's symbol — is a broken test, not a red gate.

Check 4 is why §3 insists the contract declare the entry point. It is not only
anti-desynchronisation: it is what makes "red for the right reason" decidable at all.

Note the asymmetry: **modification work is the easy case.** When the entry point already
exists the import succeeds, the test runs, and it fails by assertion naturally. Greenfield is
the hard case, and check 4 exists for it.

### 6.2 Clause coverage gates link 1, it does not merely annotate the receipt

An uncovered clause is knowable at the end of link 1 — before paying for link 2. So it is
**link 1's gate**, not an end-of-run demotion: the chain halts with `UNCOVERED: C3`, and the
failure lands where the fix belongs (usually a vague clause, not a lazy worker).

This also supplies link 1's missing floor. `_SELF_GATE`'s `min_tests` does not apply — link
1 is gated by the red gate — so without this, one weak test satisfying one clause is a green
link 1. "Every clause covered" is a better floor than a test count anyway, because it is tied
to what was asked rather than to volume.

**What is mechanical here, and what is not.** The *presence* of a `C2` tag is a grep. Whether
the test tagged `C2` actually asserts C2 is the worker's claim, and a test tagged `C2` that
asserts something adjacent reads as covered. There is no mechanical fix for that — it is
precisely why layer 3 echoes the assertion text beside each clause rather than a checkmark.
The receipt line is a prompt for a five-second human check, not a verdict.

**Escalation, per-task, not default — the adversarial pass.** For correctness-critical work,
spend one delegation on *"make this test pass while violating C2."* If a fresh worker
succeeds, the test is insufficient and you know which clause is unguarded. This is
adversarial in the useful direction: attacking the gate, not forecasting the work.

---

## 7. The backstop for plain delegation

The plugin cannot know before a run which files will change. It knows **after**:

> run completes → a **new symbol that itself references a seam** (calls a client, session,
> connection or subprocess) is reached by the tests only through a mock →
> `SEAM CROSSED, UNIT-GATED ONLY` → status demoted

**The predicate is the new symbol, not the file.** "Lives in a file that imports a driver"
would fire on a pure helper added beside one, and this project has spent a phase deleting
false accusations of exactly that shape (the `skipif` false positives, TEST DODGE crying
wolf). A detector that fires on the line you are told to read is worse than no detector. The
narrower predicate uses the same per-symbol analysis `new_public_symbols` already does.

Orchestrator picks the cheap path; the plugin disagrees when the cheap path was wrong. It
costs nothing until it is actually wrong, requires no pre-knowledge, and reuses data the
plugin already computes (`CHANGED`, `new_public_symbols`, `MOCKED SEAM:`, `blast_radius()`).

This is what keeps §5 from being A14 again. The orchestrator chooses freely *because* the
plugin is not relying on that choice being right.

---

## 8. Concurrency

Nothing above works under concurrent workers without three fixes. The first is a hard
blocker.

### 8.1 Blocker — a chain does not share a worktree

- `delegate()` acquires its **own** worktree per call: `wt = worktrees.acquire(cwd)`
  (`engine.py:928`). Each chain link is a separate `delegate()` call.
- On success it commits **to its own branch** and only *classifies* the merge for the
  caller: `ctx["merge"] = worktrees.classify_merge(...)` (`engine.py:1751-1762`). Nothing
  merges back.

So with `"worktree": "auto"`, link 2 cuts a clean tree from an unchanged HEAD and **link 1's
test is not in it.** `run_chain`'s "link 2 builds on link 1's tree" holds only in `"off"`
mode — where in-tree runs take the repo lock and concurrent pipelines serialize.

| Mode | Test-first works? | Concurrency? |
|---|---|---|
| `auto` | ✗ link 2 cannot see the test | ✓ isolated |
| `off` | ✓ shared tree | ✗ repo lock serializes |

Today it is isolation **XOR** test-first.

**Fix:** a chain acquires one worktree, holds it across all links, releases at the end.
`run_chain` already acquires and releases guards per link, so the seam exists. This is not
test-first-specific — it makes the docstring true for every chain.

### 8.2 Same fix, second half — protection needs a tracked file

`spec_files()` is `git ls-files` — **tracked only** (`gittree.py:245`). Link 1's brand-new
test is untracked, so it is not in `spec_files()`, not in `violated_specs()`, and **not
protected**. Link 2 could rewrite or weaken it freely, collapsing the design back to
self-grading.

So the chain must **commit link 1's output before link 2 starts**. Without this the shared
worktree makes things actively worse: link 2 gets the file *and* the ability to edit it.

### 8.3 The shared preflight cache assumes something chains break

`_preflight_once` keys on `(base_sha, worktrees_dir, cmd)` and states its invariant plainly:
*"every item is cut from the SAME base commit into its own clean worktree, so that answer is
identical for every item by construction."*

True for a **batch**. False for **chain link N>1** — its tree deliberately is not base.

Tiering makes collision *more* likely: once tiers are declared, every pipeline's gate
becomes the same string (`pytest tests/integration -q`), so concurrent pipelines from one
base share a cache key while holding different trees.

**Fix:** skip the cache when `CHAIN_ARG["pos"] > 1`, or fold the link's tree state into the
key.

### 8.4 Fan-out costs one submit per pipeline

`chain` and `batch` are mutually exclusive, refused by name in `_shape_refusal`. Five
concurrent test-first pipelines would be five async submits, not one batch.

**Corrected after cross-checking the ledger: those five submits are not merely linear, they
are forbidden.** A11 / PLAN §2.3 — a second `qwen_*` tool call while a run is in flight
closes the stdio transport, and the documented workaround is *"fan out through `batch` in
one call rather than through separate calls."* Which `chain` cannot do.

So today: **concurrent test-first pipelines are impossible from one session.** Fixing A11
(refuse the second call cleanly instead of dropping the connection) is a prerequisite for
this design's concurrency story, not a parallel nicety — or a batch-of-chains shape has to
exist. Tracked in [ROADMAP.md](ROADMAP.md) §2.1.

### 8.5 Chain worktrees need a reaper

§8.1 holds a worktree for a whole chain instead of per link. Note this runs *opposite* to
how `run_chain` treats endpoint slots, which it deliberately releases between links ("a chain
holds one endpoint slot at a time, not one for its whole length"). Different resource,
different answer — but the consequence is that N concurrent chains hold N worktrees for full
chain duration, and a chain that dies mid-way orphans one under
`~/.qwen-delegate/worktrees/`.

Release in `finally`, and give `doctor` a check for worktrees whose owning chain is gone.
Same class as this round's teardown work (A0a/A0c/A11), which is worth remembering: the
orphan there was invisible until it was counted.

### 8.6 Load

Test-first doubles delegations per unit of work, and gates run 4–5× per delegation: ~5 gate
runs per feature becomes ~10. With integration-weight gates that is the real scaling limit,
and it is the exact contention `_preflight_once` was built to fix. Measure before raising
`parallel_max`.

Two links also means two `timeout_sec` windows and two compaction opportunities per feature.
The fitted-timeout regression added this round measures a *single run*; a chain's budget is
the sum of its links, so do not set a chain timeout from a single-run fit.

---

## 9. Defects found while designing

Three exist today, independent of anything above.

**D1 — the vacuous-pass guard counts skipped tests as evidence.** *(exists today, verified)*
`_SELF_GATE` greps `Ran [0-9]+ tests?|[0-9]+ passed` and compares the sum to `min_tests`.
Measured against five `@unittest.skip` tests:

```
Ran 5 tests in 0.000s
OK (skipped=5)
exit=0          → gate reads ran=5, floor is 5, PASS
```

The pytest path fails differently and worse: `5 skipped` matches neither alternative, so
`ran` is empty, the script prints `SELF-GATE NOTE: … vacuous-pass guard inactive`, and exits
0 anyway. This is A19's shape — a guard that silently declines to be evidence — inside the
guard written to catch it.

**D2 — `preflight_expect="red"` cannot tell FAIL from ERROR.** *(exists today)*
`_run_verify_timed` returns `passed = (proc.returncode == 0)`, so "red" is satisfied equally
by an assertion failure, an `ImportError`, a syntax error, or zero tests collected. **A test
that errored at import never executed a single assertion** — `NEVER EXECUTED:` in a
different costume.

Occasional today. **Structural** under test-first, because link 1 writes tests against code
that does not exist yet, so import-error-red is the *normal* state of a correct artifact.
The one signal proving link 1 did real work is one every broken file also produces.

The raw material for a fix is at the same parse site as D1:

```
unittest:  FAIL: test_x   → assertion ran and failed
           ERROR: test_x  → exception before the assertion
pytest:    exit 1 → tests failed
           exit 2 → collection error
           exit 5 → no tests collected
```

**But "FAIL good, ERROR bad" is not the rule** — see §6.1. Under test-first, `ERROR` is the
*expected* state of a correct link-1 artifact, so the rule has to distinguish an error that
names C1's not-yet-existing entry point from one that does not.

**D3 — chain worktree isolation contradiction.** §8.1. Exists today; `run_chain`'s docstring
is false under `"worktree": "auto"`.

**D4 — preflight cache invariant broken by chains.** §8.3. Exists today; latent because gate
commands currently differ per pipeline.

**D5 — spec protection does not cover untracked files.** §8.2. Correct as designed for
today's uses; becomes load-bearing under test-first.

---

## 10. Message passing — link to link, and worker to caller

### 10.1 What already exists

| Channel | Direction | Shape | Where |
|---|---|---|---|
| `HANDOFF` / `FILES` / `NEXT` / `FINDINGS` | worker → caller | four typed lines, always requested | `verdict.py:70`, `parse_handoff` at 99, pinned by `wireformat_spec` |
| `result_schema` | worker → caller | validated JSON | `qd/jsonschema.py`, used in `queries.py` |
| session resume | run → run | the whole conversation, held by the executor | `render_argv`; a cold run drops the flag, a warm one carries the id |
| stored brief | caller → run | full args, for `retry_of` | `.qwen-delegate/briefs/<session>.json` |
| `refs/` | worker → later runs | files the worker fetched and saved | `qd/refs.py` |
| the git tree | link → link | the work product itself | after §8.1 |

This is more than it looks. The handoff block is already a **typed envelope with a pinned
wire format** — `wireformat_spec` asserts the constant and the parser round-trip together,
precisely so the asked-for shape and the read-back shape cannot drift apart.

### 10.2 The gap: the handoff is parsed and then only displayed

Nothing forwards it. `parse_handoff` turns link 1's reply into
`{"HANDOFF": ..., "FILES": ..., "NEXT": ...}`, the receipt renders it for the caller, and
there it stops.

So for link 2 to know anything link 1 learned, the **orchestrator** must read link 1's
receipt and hand-write the relevant part into link 2's task. That is wrong twice over:

- it routes worker knowledge through the caller's context — the exact burn the product
  exists to prevent
- it is an LLM decision *between* links, which §4.2's invariant forbids

The fix is small precisely because the envelope and its parser already exist: `run_chain`
injects link N's parsed handoff into link N+1 as a declared slot. No new format, no new
parser, no new spec — the wire format is already frozen.

### 10.3 Four grades of continuity, declared per link

| Grade | Carries | Isolation |
|---|---|---|
| `none` | nothing | full |
| `handoff` | the four typed lines (~50 tokens) | full — fresh session |
| `structured` | `result_schema` JSON, validated | full — fresh session |
| `session` | the entire conversation | **none** |

**Test-first requires link 2 at `none` or `handoff`, and never `session`.** A shared session
reintroduces exactly the co-adaptation §4.1 splits the runs to prevent: the implementer would
have watched the test being written and could satisfy the version it remembers rather than
the version on disk.

Note the trap in that table: `session` is the *cheapest* grade for us — the executor already
holds the conversation, so it costs us nothing to pass an id — and it is the most dangerous.
Cost and safety run in opposite directions, which is exactly the shape of a default that gets
chosen for the wrong reason. Make the grade an explicit per-link declaration; do not let it
default to whatever is cheap.

### 10.4 Where an object earns its place here

**Not a hierarchy, and not a message bus.** The value is the envelope plus validation, and
both already exist — `parse_handoff` and `qd/jsonschema.py`. Those stay pure functions.

But this is now the *second* thing with chain lifetime, and it has the same lifetime as the
worktree (§11.2). That is the argument for one object rather than three parallel mechanisms:

```
ChainScope — created at chain start, released in finally
  · the worktree held across links          (§8.1)
  · the previous link's handoff payload     (§10.2)
  · the contract digest link 1 recorded     (§3.3)
  · the link index
```

One lifecycle, one acquire/release, following the protocol `EndpointGuard` and `FileSlots`
already use. Three ad-hoc side channels collapse into one object whose scope is exactly the
thing it belongs to — which is what an object is *for*, and why this is a better answer here
than it would be anywhere else in the codebase.

---

## 11. Implementation shape

### 11.1 Keep the procedural grain

`qd/` is functions over data, and classes appear in exactly four places, every one with a
real lifecycle: `BurnLimit` and `Progress` (`limits.py`), `FileSlots` and `EndpointGuard`
(`server.py`), plus the two exception types. Everything else is a pure function taking `cwd`
and returning data — `never_executed(cwd, changed, verify_cmd) -> list`.

That is not an oversight to correct. It is why the spec suite is hermetic: a pure function
needs no fixture, no mock and no teardown, and 875 tests run in ~80s because of it. The
domain is *read git → parse output → return a verdict*, which is transformation, not
entities with state.

Everything this design adds fits that grain — the red gate is a sibling of
`_ensure_self_gate`, and tier lookup, clause parsing and contract digests are pure
functions. **Do not introduce class hierarchies for them.**

### 11.2 The one place an object earns itself: the chain-scoped worktree

§8.1's fix is *acquire once, hold across N links, release at the end*. That is lifecycle,
which is the one thing plain functions handle badly, and it is exactly the shape
`EndpointGuard` and `FileSlots` already have. **Follow that acquire/release protocol rather
than inventing a new one.**

Two constraints on it:

- Release in `finally`. `run_chain` already wraps its per-link guard acquisition that way.
- Note the asymmetry with endpoint slots, which `run_chain` deliberately releases *between*
  links ("a chain holds one endpoint slot at a time, not one for its whole length"). The
  worktree moves the other way for a different resource, so N concurrent chains now hold N
  worktrees for full chain duration, and a chain that dies mid-way orphans one. That needs a
  reaper — same class as the teardown work already done this round (A0a/A0c/A11), and
  `doctor` is the natural place to report orphans.

### 11.3 Extract the post-run block before adding to it

`delegate()` is the top god node in the graph — **58 edges**, ~40% more than the next — and
`_delegate()` runs from `engine.py:806` to ~1813. Roughly a thousand lines in one function.

This design adds four more things to its post-run section: contract digest comparison, tier
resolution, clause coverage, seam demotion. **Extract the post-run receipt assembly into its
own function first.** It is a plain extraction, not a refactor toward objects, and it is a
prerequisite rather than a cleanup — four more inline blocks in that function is how it
becomes unmaintainable.

---

## 12. Work items

Split by whether the item is **plumbing** (true for every chain, independent of any policy)
or **policy** (specific to the test-first pipeline). Build the plumbing generally; keep the
policy specific until there are three real pipelines to generalise from.

**Plumbing — ship independently.**

| # | Item | Kind |
|---|---|---|
| P1 | Chain holds one worktree across links, commits between them (D3, D5) | fix |
| P2 | Skip the preflight cache for `CHAIN_ARG["pos"] > 1` (D4) | fix |
| P3 | Fix skip-counting in `_SELF_GATE` (D1) | fix — a live hole today, unrelated to chains |

P1 and P2 carry a hard constraint: **single delegation and `qwen_query` must be unchanged.**
A single delegation is not a chain of length 1 (§4.4) — do not unify the paths. P1 also owes
a reaper for orphaned chain worktrees (§8.5).

**Policy — after the plumbing.**

| # | Item | Kind |
|---|---|---|
| 0 | **Extract `_delegate`'s post-run receipt assembly** (§11.3) — prerequisite, not cleanup | refactor |
| 1 | Red gate generator, four checks (§6.1) — sibling to `_SELF_GATE` | new |
| 2 | Contract pinning: `spec_globs` cover, `CONTRACT: path @ digest`, digest header in the test file, cross-link compare (§3.3) | new |
| 3 | `"tests"` tier map + refuse-don't-guess | new config |
| 4 | Clause coverage as **link 1's gate** (§6.2), + the receipt line | new |
| 5 | `SEAM CROSSED, UNIT-GATED ONLY` — predicate on the new *symbol*, not the file (§7) | new |
| 6 | `*_qwen` naming rule in `QWEN.md` + `_created()` compliance check | new |
| 7 | `playbooks/write-gate.md`, `playbooks/implement.md` | documents |
| 8 | Contract lifecycle: `doctor` check for contracts naming symbols that no longer exist | new |
| 9 | Forward link N's parsed handoff to link N+1 as a declared slot (§10.2) — reuses the existing envelope and parser | new |
| 10 | Per-link continuity grade: `none` / `handoff` / `structured` / `session`, explicit, never defaulted to the cheap one (§10.3) | new |

---

## 13. The residual risk

Both links read the same brief. If the contract is wrong, link 1 writes a test asserting the
wrong thing, link 2 satisfies it, and a green gate defends a defect. No amount of
run-splitting or session isolation touches this — the error is upstream of both.

That is A23 (`challenge_brief`, [PLAN-v06-ledger.md](PLAN-v06-ledger.md) §2.4), and it means
**this design and `challenge_brief` are one project, not two list items.** Splitting the
runs protects you from the worker. Challenging the brief is the only thing that protects you
from the orchestrator. Test-first without it makes the orchestrator's contract
unfalsifiable — a worse failure than the one being fixed, because it now has a green receipt
behind it.
