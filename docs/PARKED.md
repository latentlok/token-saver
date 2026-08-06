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
| **B** continuity grades | steps 5, 7 | `core/scope.py` + composite |
| **D** `PAID:` receipt line | step 3 | a receipt block |
| **D** telemetry beyond the executor | step 5 | `core/scope.py` (the call log's owner) |

### Not blocked by anything — can ship whenever

These need no restructure at all, and are only parked because attention is
elsewhere:

| Item | Why it is free |
|---|---|
| ~~**A7** the two playbooks~~ | **DONE** — `playbooks/write-gate.md`, `playbooks/implement.md`, pinned by `specs/playbooks_spec.py` |
| ~~**A8** contract lifecycle check~~ | **DONE** — `doctor._stale_contract_pins`. Finds gates pinned to a contract that has since moved or vanished: the third way a contract bites, and the only one with no run left to refuse it |
| ~~**C** the skill pass~~ | **DONE** (the three content bugs). `specs/skill_spec.py` now pins the claims that cost tokens or capability when they drift |
| ~~**D** server lifecycle (A0d)~~ | **DONE** — `qd/core/lifecycle.py`. Scope corrected during the build: it records and REPORTS, it does not kill. See below |
| **E** the PENDING carryovers | adapter-level (streaming, `usage` fallback, live probes) |
| **F** decisions owed | version bump, `reset_worktree()`, the `challenge_brief` false-positive rate |
| ~~**G5** cold-vs-resumed retry~~ | **MEASURED.** Cold is 40% cheaper: a resumed call carries a FULL COPY of the previous prompt (verbatim, not a delta), so the cost is the ~50k PREFIX paid twice and it compounds O(N²). Not changed on n=3 — see FINDINGS |

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
| A2 | ~~**Contract pinning**~~ **DONE** — `qd/core/contract.py`, the `CONTRACT:` receipt line (non-droppable), and `features/gates/contract.py` for the cross-link compare | §3.3 | done |
| A3 | ~~**Tier map**~~ **DONE** — `qd/core/tiers.py`. Declared never guessed; refuses with the question (and the JSON to paste) when a seam is crossed and nothing is declared. `detect_test_cmd` stays the fallback | §2.2 | done |
| A4 | ~~**Clause coverage**~~ **DONE** — `features/guards/clauses.py`. A GUARD, so it fails the attempt and tells the worker which clause is missing, rather than demoting at the end | §6.2 | done |
| A5 | ~~**`SEAM CROSSED, UNIT-GATED ONLY`**~~ **DONE** — `features/detectors/seam_crossed.py`. Predicate on the new SYMBOL, and `mocked_seams` became a FACT because a second reader appeared (§4's own prescription) | §7 | done |
| A6 | **`*_qwen` naming rule** in generated `QWEN.md` + `_created()` compliance check | §2.5 | `provenance/` feature |
| A7 | **`playbooks/write-gate.md`, `playbooks/implement.md`** | §4.2 | documents |
| A8 | ~~**Contract lifecycle**~~ **DONE** — scoped to stale PINS rather than symbol references: a pin is mechanical and exact, where 'symbols named in prose' is a guess, and a doctor check that guesses gets switched off | §11 item 8 | done |

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

~~Three content bugs~~ **all three FIXED**, and pinned by `specs/skill_spec.py`
so the worst of them — a document contradicting its own measurement — is now a
catchable class rather than something only a careful reader notices:

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

~~**Server lifecycle (A0d).**~~ **DONE**, with its scope corrected by a spec.

The server now writes pid+version at startup and clears a STALE record. An
earlier draft also SIGTERMed a *live* predecessor — and `specs/serialize_spec.py`
caught it: two servers on one machine is a **supported configuration**, and that
spec exists to prove the repo lock and endpoint slot hold ACROSS processes.
Killing one would have broken a real guarantee to tidy up an accident.

So it records and reports. `doctor` already prints the exact `kill` for a human
who wants it, and the recorded VERSION is what lets anyone see that an old build
is the one serving — which was most of the harm.

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

~~**G2 — nothing reads a chain's links against each other.**~~ **DONE.** Link 1's
challenge now reads the WHOLE chain: `run_chain` composes every step's brief,
numbered and ordered, and hands it to the head link only. Built as the same
challenge pass with a wider subject rather than a second mechanism, so every
rule it already had still holds — once per chain, refuse only on evidence naming
a path that exists, diagnosis runs exempt.

Numbered and ORDERED because the contradictions that matter are ordinal: a later
step undoing an earlier one is a contradiction, while the same two briefs in the
other order might be a good refactor.

~~**G4 — nothing asks "is this what was asked for?" after the build.**~~ **DONE**
as `review_brief` (OFF by default). Compares the brief against a diff SUMMARY —
paths and line counts, never content, because the question is about shape and
the full diff would cost a second delegation's context to answer it worse.

**It is an advisory and must stay one.** It rides the `advisory_gates` shape, so
it never touches STATUS and never reaches the worker. A witness that can refuse
has been promoted to judge, and §I says the verdict is a command's exit code.
Step 4 made "can refuse" a property of the type so this cannot acquire the power
by being filed in `features/gates/`.

Defaults toward MATCHES: an unparseable answer is not evidence of a defect, and
a red line that is usually wrong is one nobody reads.

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
