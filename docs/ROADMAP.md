# Roadmap — the single queue

**This supersedes `HANDOVER.md` and the open sections of
[PLAN-v06-ledger.md](PLAN-v06-ledger.md).** Those two plus the v0.6 design doc had
overlapping, drifting task lists; this is the one place work is tracked.

| Doc | Role now |
|---|---|
| **this file** | the queue — what is open, in what order, and why |
| [DESIGN-modular-architecture.md](DESIGN-modular-architecture.md) | the **active** design — restructure so features are units |
| [DESIGN-v06-test-first.md](DESIGN-v06-test-first.md) | the design for the A14 replacement. Its work items are parked until the restructure lands |
| [PARKED.md](PARKED.md) | everything specified but not being built, and why |
| [PLAN-v06-ledger.md](PLAN-v06-ledger.md) | historical: what the v0.6 code round closed, and the reasoning. Its "Done" table is the record |
| [archive/plugin-improvement.md](archive/plugin-improvement.md) | evidence: the 23 field findings. Read only when a finding's cost is in question |
| [PENDING.md](PENDING.md) | the longer-horizon v4 list — unchanged, not merged here |
| [README-walkthroughs.md](README-walkthroughs.md) | how the shapes work, end to end |

---

## 1. Does the planned work resolve the ledger?

All 23 findings (plus sub-IDs) against current state. **Done** = shipped in `5c9e021`.

| ID | Severity | Status | Where |
|---|---|---|---|
| A0 | DEALBREAKER | **done** | doctor: gate-near-timeout check |
| A0a / A0c | DEALBREAKER | **done** | `start_new_session` + `killpg`, mutation-checked |
| A0b | BUG | **done** | endpoints honoured at profile Level 4 |
| A0d | BUG | **partial** | doctor now names the pids and the exact `kill` command; the self-terminating-predecessor half is still open |
| A0e | BUG | **done** | freshness computed live, never read from disk |
| A1 | FRICTION | open | skill 326 → ~100 lines → §4.1 |
| A2 | BUG (docs) | open | skill contradicts USAGE.md on graphify → §4.1 |
| A3 | FRICTION | open | graphify needs `scoped`, said nowhere → §4.1 |
| A4 | — | **done** | resolved during the ledger session |
| A5 | WISH | open | no `PAID:` line → §4.3 |
| A7 | WISH | **done** | `advisory_gates` documented in USAGE.md — green STATUS + red advisory is a measured blindspot |
| A7b | BUG | **done** | run id stamped at submit |
| A8 | — | **done** | resolved during the ledger session |
| A9 | BUG | **done** | doctor check; the tier map (DESIGN §2.2) makes it structural |
| A10 | FRICTION | open | `^cmd\b` allows every subcommand → §4.1 |
| A11 | DEALBREAKER | **done** | teardown half `5c9e021`; transport half root-caused and fixed `f1527b0` → §2.1 |
| A12 | BUG | **done** | `server._inherit` |
| A13 | DEALBREAKER | **done** | shared pre-flight + per-item scheduling |
| A14 | DEALBREAKER | **designed, not built** | all of DESIGN → §3.1–3.3 |
| A15 | FRICTION | **done** | denials allowlisted and split |
| A15b | NOTE | no action | transparency note: `scoped` intercepts above a `yolo` worker |
| A16 | BUG | **done** | mark-not-substring |
| A17 | BUG | **done** | `state: "starting"` before first token |
| A18 | DEALBREAKER | **done** | `NEVER EXECUTED:` |
| A19 | PRODUCT | **done** | another repo's bug; the identical hole in `_SELF_GATE` is closed (`8cb677b`) → §2.2 |
| A20 | DEALBREAKER | **done** | `MOCKED SEAM:` |
| A21 | DEALBREAKER (method) | **not fixable in code** — see §5 | |
| A22 | DEALBREAKER | **done** | `UNCALLED:` |
| A23 | DEALBREAKER | **done** | `challenge_brief`, evidence-verified → §3.0 |

**Score: 20 done, 1 partial, 2 open, 1 designed, 1 methodological.**

Open: A1/A2/A3/A10 (the skill pass, one job) and A5 (`PAID:` line).

Plus five defects found while *designing* the A14 replacement (DESIGN §9). Three exist
today and are not in the ledger at all: **D1** the vacuous-pass guard counts skipped
tests as evidence, **D2** `preflight_expect="red"` cannot tell FAIL from ERROR, **D3**
`run_chain`'s "link 2 builds on link 1's tree" was false under `worktree: "auto"` (all three now fixed — D1 in `8cb677b`, D3/D4/D5 in `4af4936`).

---

## 2. Blockers — things that stop other work

### 2.1 ~~A11 transport~~ — **FIXED** (`f1527b0`)

It was never a protocol or single-flight limitation: **every spawned child inherited the
server's stdin**, which under stdio transport is the JSON-RPC input stream. Verified
directly — the `qwen` CLI consumes inherited stdin *even when given `-p`*. So pre-fix the
executor was in a read race with the server's own reader thread for protocol bytes.

**Corrected from the first write-up of this fix.** I claimed the failure path was
`child eats request → reader sees EOF → main() drains DRAIN_SECONDS → exits`, on the
strength of `DRAIN_SECONDS = 10.0` matching the field log's 10s exactly. That mechanism is
wrong: a child stealing bytes causes **silent request loss, not EOF** — measured, the
parent blocks forever with no EOF at all. The 10s was therefore almost certainly the
*client's* timeout on a request that vanished, and the `DRAIN_SECONDS` match a
coincidence. Same defect, same fix, wrong story about the last step.

**Not reproduced on demand.** Four live attempts against snowy (two gaps × with/without
the fix) all passed: the server's reader thread is parked in a blocking read and normally
wins the race. So this is a real race on the protocol stream that is now closed, but it is
*not* proven to be the field incident's cause. Treat the field report as unexplained
unless it recurs.

The teardown round did not cover it: `start_new_session=True` detaches the process
*group*, not the file descriptors. Fix is `stdin=subprocess.DEVNULL` at all 17 spawn
sites, pinned by `teardown_spec.py::TransportIsolation` — including a test that
demonstrates the mechanism itself, so the spec fails if the premise ever stops holding,
and one asserting *no* spawn site in `qd/` inherits stdin, so the next one added
inherits the rule rather than the bug.

**Concurrency verified live** (snowy, `parallel_max 4`): two `qwen_query` calls, the
second issued while the first was in flight — both answered, server alive, replies 3.0s
apart, overlapped. So DESIGN §8.4 stands: N pipelines cost N async submits, linear and
acceptable. A batch-of-chains shape would still be nicer; it is not load-bearing.

*Remaining under A11's original scope:* nothing for the transport. `STATUS: busy`
(refusing a second call cleanly) was only ever a workaround for this defect, and is no
longer needed.

### 2.2 ~~D1 — the vacuous-pass guard counts skipped tests~~ — **DONE** (`8cb677b`)

The two runners are counted separately now, because their totals mean different things:
unittest's `Ran N` includes skips (subtracted), pytest's `N passed` already excludes them
(not double-discounted). A parse finding no count but *some* skips fails instead of
falling through to "guard inactive".

```
before:  5 skipped, min_tests 5  -> exit 0
after:   SELF-GATE: only 0 test(s) actually ran (5 skipped -- a skip is not
         evidence) -- write a real suite (>= 5 tests)   -> exit 1
```

A skip is deliberately still not a *failure* — `test_a_real_suite_beside_a_skip_still_passes`
pins that direction so this cannot become the next detector that cries wolf.

### 2.3 ~~Chain plumbing~~ — **DONE** (`4af4936`, `95278a7`)

D3/D4/D5 closed. `run_chain` acquires ONE worktree and lends it to every link through
a reserved `_worktree` arg; links commit into it (which makes their files both visible
to the next link AND tracked, so `spec_globs` can protect a gate link 2 is graded by);
the chain keeps the container if any link committed and releases it only when nothing
did. Links after the first no longer share a cached pre-flight. `worktrees.stale()` +
a doctor check report orphaned containers — reported, never removed, because they hold
gated work.

Live: link 1 writes `alpha.txt`, link 2 must read it to build `beta.txt`, and link 2's
gate requires link 1's content. Both green, both receipts naming the same worktree.
Mutated (lending removed): link 2 red — *"GATE SUSPECT: the verify command produced
identical output before"*.

**Batch of chains** landed on top: a batch item may carry `chain`, so N pipelines run
in one call, concurrently, each internally ordered and in its own worktree. Live: 2
pipelines × 2 links, 4/4 green, 2 distinct worktrees, peak 3 workers, 24s. The one
correctness point is a deadlock, not a slowdown — a chain item must take no
batch-level guard, because `run_chain` guards per link.

Hard constraint held: a lone delegation and `qwen_query` are untouched.

---

## 3. The A14 replacement

### 3.0 ~~A23 `challenge_brief`~~ — **DONE** (`e7db573`)

`challenge_brief: true` runs one read-only pass before any building. An objection
blocks **only when it cites a path that exists** — an unverifiable citation is an
opinion, and a run stopped by one teaches callers to switch the feature off.

Live, same repo and worker, module storing integer cents:

| brief | result |
|---|---|
| "the stored value is already in dollars" | **refused**, citing `store.py:1` and `:3` |
| "return the stored integer cents" | **built**, no objection |

It discriminates, which is the half that matters — a checker that blocks everything is
not a checker.

### 3.1 Plumbing (ship independently)

`P1` chain worktree + commits (§2.3 above) · `P2` preflight cache skip · `P3` D1.

### 3.2 Mechanism → parked

The red gate generator and contract pinning, both parked in
[PARKED.md](PARKED.md) §A until the restructure gives them somewhere to land.

*(This entry used to say "extract `_delegate`'s post-run block first". That is no
longer a task: the block is four concerns sharing a region, and it drains across
steps 1, 2 and 5 of [DESIGN-modular-architecture.md](DESIGN-modular-architecture.md)
rather than being lifted whole.)*

### 3.3 Policy

DESIGN §12 items 3–8: tier map, clause coverage as link 1's gate, seam demotion,
`_qwen` naming enforcement, the two playbooks, contract lifecycle check.

*(Item 9, handoff forwarding, shipped early in `14934f9` — it completed the chain rather
than waiting on the pipeline it was filed under. Item 10's continuity grades are
half-built: `none` and `handoff` exist via `carry`; `structured` and `session` do not.)*

---

## 4. Everything else still open

### 4.1 The `delegation` skill (A1, A2, A3, A10) — one pass

326 lines → ~100 hot. Three content bugs to fix while in there: the graphify advice the
skill's own measurement refutes (A2), the unsaid `scoped` requirement (A3), and that
`^cmd\b` permits every subcommand including ones that bill a cloud account (A10).

Do this **after** the doctor checks and the design's receipt lines land — each of those
deletes skill prose, and rewriting first means rewriting twice.

### 4.2 Server lifecycle (A0d) — half done

**Done** (`29b62e6`): the finding names the pids and the exact `kill` command,
oldest first and never the process you are talking to. It used to say "kill the
stale ones" and name none, so a caller had to re-derive the same `pgrep` the check
had just run — and get the pattern right, which is what made this detector
observer-dependent in the first place.

**Still open:** a server that writes pid+version at startup and terminates a stale
predecessor. That is a behaviour change to process management, and terminating
another server is not something to ship on a spec alone — it wants live testing.

### 4.3 Measurement (A5) — ~~A7 done~~

**A5, open:** a `PAID:` receipt line. Needs the receipt registry from restructure
step 3.

~~**A7**~~ **done** (`29b62e6`): `advisory_gates` is documented in `USAGE.md`. The
instrument had already shipped and lived only in the schema and `HLD.md` — attach an
owner-held spec as advisory, run `trust="self"`, and **green STATUS + red advisory is
a measured blindspot**, obtainable no other way. Pairs with the `_qwen` provenance
marker (DESIGN §2.5) as the other half of grading worker tests.

### 4.4 Carried from PENDING (unchanged, listed so they are not lost)

Streaming loses `tools`/`lines_added`; the `usage` fallback has never run live; live
probes P1–P8; `detect_test_cmd` cannot place this repo (the tier map addresses the
symptom, not the detectors).

---

## 5. A21 — the one finding no code will close

PLAN §3 says it and it stays said: two identifiers were interchangeable *by accident*,
nothing asserted they were the same, and nothing noticed when the accident ended. 29
foreign-key violations over ~60 minutes of GPU, and the operator spotted it before any
test did.

> **Delegate modules; gate seams yourself.** A green receipt is evidence about a module
> and is routinely read as evidence about a product.

What the design *does* contribute: A21's first and most general fix is *"two ids that
mean different things must never be obtainable from the same expression."* The contract
requiring an explicit entry point and signature (DESIGN §3) is that discipline applied
to the delegation boundary — it makes the identity a written artifact instead of a
coincidence. That covers delegated work. It does not cover the architect's own edits,
which is exactly where A21 happened.

---

## 6. Decisions owed

- ~~**Version.**~~ **Decided: 0.6.0.** Bumped in `.claude-plugin/plugin.json`
  and `qd/server.py` `SERVER_INFO` (they must stay equal), changelog entry
  written. Remaining per `docs/RELEASING.md`: PR → CI → squash-merge → tag
  `v0.6.0` on master → GitHub release.
- ~~**`reset_worktree()`**~~ **Decided: keep.** Two lines, spec'd, costs
  nothing. Its spec pins `clean -fd` and NEVER `-fdx` -- the `-x` would
  delete gitignored files, i.e. someone's `venv/`. Deleting the function
  deletes that warning with it.
- **`qd/doctor.py` Ollama advice** — `OLLAMA_NUM_PARALLEL`, CONTEXT off `ollama ps`.
  Ollama will not be used again; clean out when next touching doctor.
- **Machine config, outside git:** `~/.qwen-delegate/executors.json` now has
  `snowy: parallel_max 4`.

---

## 6b. The active work: modularity

**Design: [DESIGN-modular-architecture.md](DESIGN-modular-architecture.md)** —
a run becomes an object, a feature becomes a unit, and an 8-step migration where
every step ships green.

Everything not listed here is **parked** in [PARKED.md](PARKED.md) — specified,
designed, not being built until the structure can hold it.

**The measurement.** Two functions hold ~2,000 of the ~9,000 lines, and they are
the two every feature must pass through:

| file | lines | longest function |
|---|---|---|
| `qd/engine.py` | 2,134 | **`_delegate` — 1,111**, 105 distinct locals |
| `qd/verdict.py` | 1,042 | **`render` — 888**, 20 inline receipt branches |
| `qd/server.py` | 937 | `submit_delegate` — 88 |
| `qd/invoke.py` | 836 | `_stream_process` — 130 |
| `qd/gittree.py` | 707 | `mocked_seams` — 58 |

Module *layering* is sound and is not the problem — the import graph is acyclic
(`server → engine → {bootstrap, gittree, invoke, profiles, runlog, verdict}`),
and every other module's longest function is under 130 lines.

**The problem is that a FEATURE has no home.** Adding `challenge_brief` took
five edits across four modules — engine (constant, resolver, an inserted block,
a ctx key, an accumulator call), verdict (wire format, parser key, receipt
branch), schemas (param), runlog (telemetry). Nothing groups them, nothing can
enumerate them, and removing one means rediscovering all five.

**And it compounds.** Today alone: `_delegate` 1,041 → 1,111, `engine.py`
1,947 → 2,134, `delegate()` 58 → 61 graph edges. Each feature makes the next
one harder to add and to remove.

---

## 7. Suggested order

0. ~~**A11 transport**~~ — **done** (`f1527b0`), concurrency unblocked
1. ~~**D1**~~ — **done** (`8cb677b`), skipped tests no longer count as evidence
2. ~~**A23 `challenge_brief`**~~ — **done** (`e7db573`)
3. ~~**Chain plumbing**~~ — **done** (`4af4936`), plus batch-of-chains (`95278a7`)
4. **The modularity restructure** (§6b) — **active**. Everything else is parked
   in [PARKED.md](PARKED.md) until the structure can hold it.
5. ~~Design mechanism, then policy~~ → [PARKED.md](PARKED.md) §A
6. ~~Skill pass~~ → [PARKED.md](PARKED.md) §C
