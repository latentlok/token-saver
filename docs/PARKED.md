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
| **A1** red gate | step 4 | `features/gates/` |
| **A2** contract pinning | steps 1–3 | facts + a detector + a receipt block |
| **A3** tier map | step 6 | `core/plan.py` (it is config resolution) |
| **A4** clause coverage as link 1's gate | step 4 | `features/gates/` |
| **A5** `SEAM CROSSED` | steps 1–3 | a detector reading existing facts |
| **A6** `*_qwen` naming check | steps 1–2 | a detector |
| **B** continuity grades | steps 5, 7 | `core/scope.py` + composite |
| **D** `PAID:` receipt line | step 3 | a receipt block |
| **D** telemetry beyond the executor | step 5 | `core/scope.py` (the call log's owner) |

### Not blocked by anything — can ship whenever

These need no restructure at all, and are only parked because attention is
elsewhere:

| Item | Why it is free |
|---|---|
| **A7** the two playbooks | markdown documents |
| **A8** contract lifecycle check | a doctor check; doctor is not being restructured |
| **C** the skill pass | prose. *Best done after* the restructure so it is rewritten once, not twice — a scheduling choice, not a dependency |
| **D** server lifecycle (A0d) | server startup; untouched by this work |
| **D** document `advisory_gates` | documentation of something already shipped |
| **E** the PENDING carryovers | adapter-level (streaming, `usage` fallback, live probes) |
| **F** decisions owed | version bump, `reset_worktree()`, the `challenge_brief` false-positive rate |

**The useful read:** most of the pipeline work waits on steps 1–4, and *nothing*
waits past step 6. Roughly half the list is not blocked at all.

---

## A. The test-first pipeline (A14 replacement)

Fully designed in [DESIGN-v06-test-first.md](DESIGN-v06-test-first.md). The
mechanism half is what remains; the prerequisites (`challenge_brief`, chain
plumbing, batch-of-chains, handoff forwarding) all shipped.

| # | Item | Design | Lands in |
|---|---|---|---|
| A1 | **Red gate generator** — four checks (collects, ran-and-none-skipped, every clause covered, failed for a legible reason) | §6.1 | a `gates/` feature |
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

**Server lifecycle (A0d, partial).** Doctor reports stale servers; nothing kills
one. Write pid+version at startup, have a new server terminate a stale
predecessor.

**Measurement (A5/A7).** A `PAID:` receipt line. And **document
`advisory_gates`** — A7 asked "did self-grading catch what a real gate would?"
and the instrument already shipped: attach an owner-held spec as advisory, run
`trust="self"`, and green STATUS + red advisory *is* a measured blindspot. Pairs
with the `*_qwen` marker (A6) as the other half of grading worker tests.

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

- **Version.** `.claude-plugin/plugin.json` still says `0.5.1`; root
  `CHANGELOG.md` has no `0.6.0` entry. `docs/RELEASING.md` owns the sequence.
- **`reset_worktree()`** in `qd/gittree.py` — called only by its own spec since
  best-of-N was removed. Keep or drop.
- **`challenge_brief` false positives.** Observed live: it blocked a buildable
  brief on the grounds that `total_for` implies aggregation over a dict holding
  one value per key. Defensible, evidence-backed, still a block. The evidence
  check filters citations nobody can verify; it does not filter pedantry. Decide
  whether that rate is acceptable at default-on.

---

## G. Not closable in code

**A21** — two identifiers interchangeable *by accident*, nothing asserting they
were the same, nothing noticing when the accident ended. 29 foreign-key
violations over ~60 minutes of GPU; the operator spotted it before any test did.

> **Delegate modules; gate seams yourself.** A green receipt is evidence about a
> module and is routinely read as evidence about a product.
