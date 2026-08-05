# Parked

Work that is **specified and not being built right now**, because the
restructure ([ROADMAP.md](ROADMAP.md) → modularity) comes first. Nothing here is
abandoned; each item says what it needs and where its design lives.

**Why parked:** every item below adds code to `_delegate` (1,111 lines) or
`verdict.render` (888 lines). The structure that replaces them is designed in
[DESIGN-modular-architecture.md](DESIGN-modular-architecture.md). Adding features to those two is what made them
that size — today alone `_delegate` grew 1,041 → 1,111 and `delegate()` went
from 58 to 61 graph edges. Build the structure, then land these into it.

---

## When each of these can start

The eight refactoring steps are in
[DESIGN-modular-architecture.md](DESIGN-modular-architecture.md) §8:

> 1 Facts · 2 Findings · 3 Receipt-as-list · 4 Gates · 5 Scope · 6 Plan ·
> 7 Composite · 8 Query

Each parked item below needs some of them finished before it has anywhere to
land. This is the mapping — **not a merge**; the restructure list stays one
thing and this list stays another.

| Parked item | Blocked until | Then it lives in |
|---|---|---|
| **A2** contract pinning | steps 1–3 | facts + a detector + a receipt block |
| **A3** tier map | step 6 | `core/plan.py` (it is config resolution) |
| **A4** clause coverage as link 1's gate | ~~step 4~~ **unblocked** | `features/gates/` |
| **A5** `SEAM CROSSED` | steps 1–3 | a detector reading existing facts |
| **B** continuity grades | steps 5, 7 | `core/scope.py` + composite |
| **D** `PAID:` receipt line | step 3 | a receipt block |
| **D** telemetry beyond the executor | step 5 | `core/scope.py` (the call log's owner) |
| **G2** whole-chain brief contradiction | steps 4, 7 | `features/gates/`, on the composite |
| **G4** brief-vs-diff advisory | ~~step 4~~ **unblocked**, but NOT as a gate | `advisory_gates` — see §G4; a witness that can refuse breaks PRINCIPLES §I, and step 4 made "can refuse" a property of the type |

### Not blocked by anything — can ship whenever

These need no restructure at all, and are only parked because attention is
elsewhere:

| Item | Why it is free |
|---|---|
| **A7** the two playbooks | markdown documents |
| **A8** contract lifecycle check | a doctor check; doctor is not being restructured |
| **A6** `*_qwen` naming check | **unblocked by step 2** — it is a detector, and `qd/features/detectors/` now exists. Copy any of the five: a file with `KIND` and `detect(facts, inputs)`, plus one line in `DETECTORS` |
| **C** the skill pass | prose. *Best done after* the restructure so it is rewritten once, not twice — a scheduling choice, not a dependency |
| **D** server lifecycle (A0d), remaining half | server startup; untouched by this work. The reporting half shipped in `29b62e6` |
| **E** the PENDING carryovers | adapter-level (streaming, `usage` fallback, live probes) |
| **F** decisions owed | version bump, `reset_worktree()`, the `challenge_brief` false-positive rate |
| **G5** cold-vs-resumed retry | a measurement on telemetry that already exists |

**The useful read:** most of the pipeline work waits on steps 1–4, and *nothing*
waits past step 6. Roughly half the list is not blocked at all.

---

## A. The test-first pipeline (A14 replacement)

Fully designed in [DESIGN-v06-test-first.md](DESIGN-v06-test-first.md). The
mechanism half is what remains; the prerequisites (`challenge_brief`, chain
plumbing, batch-of-chains, handoff forwarding) all shipped.

| # | Item | Design | Lands in |
|---|---|---|---|
| A1 | ~~**Red gate generator**~~ **DONE** — three of four checks (parses, ran-and-none-skipped, failed legibly) in `qd/features/gates/red.py`. The fourth, clause coverage, needs the contract format and stays as A4 | §6.1 | `features/gates/` |
| A2 | **Contract pinning** — `spec_globs` cover, `CONTRACT: path @ digest`, digest header in the test file, cross-link compare | §3.3 | `contract/` feature |
| A3 | **Tier map** — `"tests": {unit, integration, e2e}`, declared never guessed; refuse-and-ask when a seam is crossed and nothing is declared | §2.2 | config + `tiers/` |
| A4 | **Clause coverage as link 1's gate** — `UNCOVERED: C3` halts the chain, not an end-of-run note | §6.2 | `contract/` feature |
| A5 | **`SEAM CROSSED, UNIT-GATED ONLY`** — predicate on the new *symbol*, not the file it lives in | §7 | `seams/` feature |
| A6 | **`*_qwen` naming rule** in generated `QWEN.md` + `_created()` compliance check | §2.5 | `provenance/` feature |
| A7 | **`playbooks/write-gate.md`, `playbooks/implement.md`** | §4.2 | documents |
| A8 | **Contract lifecycle** — doctor check for contracts naming symbols that no longer exist | §11 item 8 | doctor |

**Residual risk, unchanged:** both links read the same brief. `challenge_brief`
now covers the worst of it (§13), but a contract that is wrong *and*
uncontradicted by the code still produces a green gate defending it.

---

## B. Continuity grades (half-built)

`none` and `handoff` exist via `carry` / the chain preamble.
`structured` (forward a validated `result_schema` payload) and `session` (share
the executor conversation) do not. Design: DESIGN §10.3.

The table's warning stands and should be built in, not just written down:
`session` is the **cheapest** grade for us and the **most dangerous** — cost and
safety point in opposite directions, which is the shape of a default chosen for
the wrong reason.

---

## C. The `delegation` skill pass (A1, A2, A3, A10)

326 lines → ~100 hot. Deliberately last: the restructure and the items above
both delete prose this pass would otherwise rewrite twice.

Three content bugs to fix while in there:

- **A2** — the skill tells Claude to query graphify's MCP; `USAGE.md` says the
  opposite and is right. The same skill carries the measurement (+64%) that
  refutes its own advice two sections earlier.
- **A3** — nothing says worker-side graphify needs `approval_mode="scoped"`,
  because `auto-edit` has no shell. Following the skill's own default silently
  gets grep instead of the graph.
- **A10** — `^cmd\b` in `shell_allow` permits *every* subcommand. The natural
  `^graphify\b` also allows `graphify update`, which can bill a cloud account.

---

## D. Smaller open items

**Server lifecycle (A0d, partial).** Doctor now names the pids and the exact
`kill` command. Still open: a server that writes pid+version at startup and
terminates a stale predecessor — a behaviour change to process management that
wants live testing rather than a spec.

**Measurement (A5).** A `PAID:` receipt line. *(A7 is closed —
`advisory_gates` is documented in USAGE.md. It pairs with the `*_qwen` marker
(A6) as the other half of grading worker tests.)*

**Per-call telemetry, next step.** `ExecutorCall` exists. Not yet: gate runs and
subprocess work are not calls in the log, so wall-clock attribution stops at the
executor boundary.

---

## E. Carried from PENDING.md

Streaming loses `tools` / `lines_added` · the `usage` fallback has never run
live · live probes P1–P8 · `detect_test_cmd` still cannot place this repo (the
tier map addresses the symptom, not the detectors).

---

## F. Decisions owed

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

---

## G. Orchestration parity

Found by reading Anthropic's `superpowers:subagent-driven-development` skill
against [PRINCIPLES.md](PRINCIPLES.md). It attacks this repo's problem from the
opposite end — expensive agents reviewed by more agents, where we use one cheap
worker and a command — so the places the two designs *converge* are
corroboration, and the places they *disagree* are worth an item.

**Converged already, nothing owed:** the worker never grades itself; the thing
that defines correct comes from a different hand (`spec_globs`); artifacts go to
files because context is what the caller pays for (`runs.jsonl`, deliberately
not the receipt); the ledger has to survive the orchestrator forgetting
(`ledger_summary`, `runs_in_flight` with pid-liveness — theirs is a markdown
file an agent appends to, ours checks whether the process is alive).

~~**G1 — a dropped finding leaves no trace.**~~ **DONE.** `SUPPRESSED:` names
every check that did not report and why — `uncalled (size)` when the cap shed it,
`mocked_seams (failed)` when the detector raised — and the per-kind lists go to
`runs.jsonl` as `detections_suppressed` / `detections_failed`. Findings only: the
line stays quiet when the cap sheds RESUME or LEDGER, because a warning that
fires on every long receipt is one nobody reads. It is not in the droppable list
at all, so the cap cannot eat the line that reports capping.

*What building it turned up.* Mutation-checking the cap first showed the drop
ORDER itself was unpinned — inverting it, so the receipt shed its most important
blocks and kept the accounting, passed all 1,032 tests. Pinned by
`verdict_spec.SizeCap` before G1 was built on top of it.

**G2 — nothing reads a chain's links against each other.** Per-link challenge
already exists: `server.py:436` defaults `challenge_brief` off for links 2..N
because an eight-link chain would otherwise pay eight read-the-codebase passes,
and *"an item that asks for it explicitly still gets it"* — so
`challenge_brief: true` on a link re-enables it. What no pass performs is the
**whole-chain** read: every challenge sees one brief and the code, never the
other links. A link 3 that contradicts link 1 is found after link 2 has already
committed into the shared worktree. **Why steps 4 and 7:** it is a gate (step 4
gives gates one shape) whose subject is the *chain*, which only becomes an
addressable thing when a chain-of-runs is itself a runnable (step 7).

~~**G3 — three identical failures render as one failure.**~~ **DONE.** The loop
already switched the WORKER to Reflexion on the first repeat; the signal is now
retained past the loop and the caller gets `stuck_no_progress` plus a
`NO PROGRESS:` line saying the remedy is the brief or the gate, not another
attempt. A subtype of `verify_failed` and last in the status cascade, because
every branch above it is a more specific diagnosis. Controls in the spec pin the
half that matters: three DIFFERENT failures stay `verify_failed`, and one
attempt can never be stuck.

**G4 — nothing asks "is this what was asked for?" after the build.** The gate
proves the tests pass. The detectors prove nothing was left behind, nothing is
unwired, no seam was faked. None of them compare the delivered diff against the
brief — the one thing an LLM reviewer buys that an exit code structurally
cannot. Our reason for not having it is sound (§I: a witness is not a verdict),
and the socket already exists: `advisory_gates` are indicators that never touch
STATUS and never reach the worker, so this can be added without weakening the
rule. **Why step 4:** it is a gate, and step 4 is when gates get one shape.
**It stays advisory** — the moment it can refuse a run, §I is broken.

**G5 — cold restart vs resumed session on retry.** `challenge_warm` measured a
resumed session at **+50% input tokens**, because it re-sends its history every
turn; the retry loop resumes. Whether a cold attempt 3 carrying the stored brief
beats a resumed one is an open A/B, answerable from per-call telemetry exactly
the way `challenge_warm` was. Not blocked by the restructure — it is a
measurement, and the result decides whether there is any work here at all.

---

## H. Not closable in code

**A21** — two identifiers interchangeable *by accident*, nothing asserting they
were the same, nothing noticing when the accident ended. 29 foreign-key
violations over ~60 minutes of GPU; the operator spotted it before any test did.

> **Delegate modules; gate seams yourself.** A green receipt is evidence about a
> module and is routinely read as evidence about a product.
