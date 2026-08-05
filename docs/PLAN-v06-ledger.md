# v0.6 — working the friction ledger

Source: `docs/archive/plugin-improvement.md` (23 findings, all against v0.5.1, none
previously fixed). It is now **evidence**, archived; this plan is the live tracker.
Every finding below quotes what it cost, so the archive rarely needs re-reading.

**The organising principle, and the reason this plan is shaped the way it is:**
almost every finding is the plugin *knowing* something and not *acting* on it. The
server can see the endpoint capacity, the gate timing, the changed files, the mocked
modules, the dispatch mode — and instead of acting, it documents the fact in prose and
asks the caller to remember it. So the fix for most of these is **deterministic code
that acts, replacing prose that instructs.** That also shrinks the skill (§4), because
a rule enforced by the server does not need to be a paragraph in context.

---

## Done (this session)

| Finding | Fix | Evidence |
|---|---|---|
| **A0a / A0c / A11** (teardown) | `start_new_session=True` on both spawn sites + `killpg` TERM→KILL in `invoke._terminate`; `_run_verify_timed` moved off `subprocess.run(timeout=)` onto Popen + group kill | `specs/teardown_spec.py`, mutation-checked: pre-fix leaked 1 orphan, post-fix 0 |
| **A0b** (endpoints ignored) | profile resolution Level 4 passes `endpoints` through | `specs/teardown_spec.py::EndpointsAtLevel4` |
| **A12** (`KeyError('cwd')`) | `server._inherit` — batch/chain items inherit run-level fields, item value wins | `specs/teardown_spec.py::BatchInheritance` |
| **A16** (TEST DODGE noise) | match the mark not the substring (`(?!if)`), strip strings/comments before matching | `specs/teardown_spec.py::TestDodgeDetector` — all 4 field false positives now pass |
| **A0e** (sidecar lies) | the stored status is `"indexed"` — a fact about a write. Freshness is computed live from git and never read from disk | `specs/graphstate_spec.py` |
| **§1.7** (dispatch invisible) | `DISPATCH:` receipt line on any fan-out: mode · endpoint · slots · items, and *"items ran IN ORDER, not concurrently"* when serial | replaced 15 lines of skill prose |
| — | `workers` (best-of-N) removed from schema, `BRIEF_KEYS`, profile defaults, skill | best-of-N deferred until wanted; git history has the v1 loop |
| — | `_ref_impl.py` deleted (2,293 lines); specs rewritten onto their own contracts | full suite green |
| — | CLAUDE.md managed block **524 → 193 tokens** | `specs/setup_spec.py` — budget + discovery doctrine restated |
| **A13** (fan-out starves itself) | items sharing a base commit + gate share one pre-flight verdict; timeouts never cached; dropped when the batch ends | `specs/fleet_spec.py::SharedPreflight` |
| **Fleet scheduling** | `run_batch` no longer lets items[0] pick the policy for everyone — every item threads and queues on its own endpoint's slots | `specs/fleet_spec.py::PerItemScheduling` |
| **Concurrency model** | `parallel_max` per endpoint is the single knob; global `dispatch` demoted to a documented kill switch | `specs/fleet_spec.py::EndpointCapacity` |
| **A22** (`UNCALLED:`) | new public symbols nothing outside their own file/tests references | `specs/seams_spec.py::Uncalled` |
| **A20** (`MOCKED SEAM:`) | a delivered test mocks a module the run also changed | `specs/seams_spec.py::MockedSeam` |
| **A18** (`NEVER EXECUTED:`) | a delivered test file the gate command does not run | `specs/seams_spec.py::NeverExecuted` |
| **(found while building)** | `new_public_symbols` read `status_map`, which collapses a new directory to one entry — a whole new module package reported ZERO public surface | same |
| **A7b / A17** (heartbeat) | `progress.json` stamps the run id at submit and opens in `state: "starting"` | `specs/teardown_spec.py::HeartbeatIdentity` |
| **A15** (denials) | `grep_search` + friends allowlisted; a trailing `2>&1` permitted; `DENIALS:` split into effect-shaped vs read-only | `specs/teardown_spec.py::DenialClassification` |
| **§6 #7** (`timeout_sec`) | the fitted regression is now CODE: unset timeouts fit from the project's own history, and a `TIMEOUT:` line states the number | `specs/wireformat_spec.py::FittedTimeout` |
| **Wire format** | the exact worker payload pinned — envelope order, argv-as-list, handoff round-trip | `specs/wireformat_spec.py` |
| **A0 / A9 / A0d** (doctor) | `project_check()`: gate can't reach specs, gate near timeout, no fan-out capacity, stale servers | `specs/teardown_spec.py::ProjectDoctor` |

Suite: `bash ci/run-specs.sh` exit 0, **875 tests**.

### The doctrine change worth knowing about

The old U2.7 rule — *"a capability surfaces in the CLAUDE.md block as one line or as a
receipt affordance, never only in long-form docs"* — is what grew the block to ~520
resident tokens: it made the block a **catalogue**, restating the schema and the skill.

Restated, and pinned in `specs/setup_spec.py`: the rule is unchanged (**no capability
lives only in long-form docs**) but the surface that satisfies it is the **tool schema**,
which is in context whenever the tools are, plus the **submit response**, which the
caller reads on every run. Verified: `wait`, `retry_of`, `result_schema`, `brief_file`,
`touch_scope` are all in the schema; `WATCH` is in the submit response.
**Discovery does not require residency.**

What is left in the block is only what nothing else can carry: the trigger (it has to
fire before Claude has looked at the tools), the name of the skill to load, and the two
rules that must hold *before* that skill loads — commit first, and the gate decides.
The skill's own `description:` frontmatter is already resident in every session and
already states the capability map, which is why the block no longer repeats it.

---

## 1. Still open — deterministic, small, high value

These are the rest of the "server knows it, server should say it" set. All are static
— nothing extra runs.

**1.1 `UNCALLED:` receipt line (A22).** A new public symbol the run added that nothing
outside its own test file references. **Six instances in one build**, each merged green
and dead. `engine` already computes `new_public_symbols()` and `blast_radius()`, so this
is a grep over `CHANGED` away. *Highest value single item in this plan* — it is the
largest defect class in the ledger and the cheapest to catch.

**1.2 `MOCKED SEAM:` receipt line (A20).** Modules a delivered test mocks
(`unittest.mock`, `MagicMock`, `monkeypatch.setattr`) **that the run also changed**.
Would have caught all three of that session's live failures. The rule the finding
states: a self-graded gate cannot test a boundary it mocks, so the mocked seam is
exactly where the work fails, green every time.

**1.3 `NEVER EXECUTED:` receipt line (A18).** A delivered test file the `verify`
command does not run. The server knows `CHANGED` and it knows the gate command.

> 1.1–1.3 are three greps and would have caught **6 of 10 defects** at delegation time,
> for free. Do these before anything in §2.

*(1.5 sidecar status and 1.7 dispatch stamp were completed this session — see Done.)*

**1.4 `progress.json` identity (A7b / A17).** Stamp the run id at submit, write
`state: "starting"` before the first token. Today: no run id at all, `session` null for
the entire live window, and several servers share one file — so the documented "is it
hung?" watchdog can report a previous run's `"state": "done"` for work that has not
started.

**1.5 `qd.doctor` checks (A0, A4, A9, A0d).** Doctor exists for exactly this class and
currently catches none of it:
- time `test_command` once, compare to effective `verify_timeout_sec` — a project whose
  suite is slower than the timeout is a **statically detectable guarantee** that every
  `trust="self"` run refuses. This burned 25 minutes for zero inference.
- `test_command` that names no protected `specs/*_spec.*` file → the synthesised gate
  cannot run the architect's own spec.
- count running token-saver servers and their versions (11 on one box, one 3 days
  stale, sharing `.qwen-delegate/` state).
- endpoint declares N slots but no executor profile exists → fan-out will serialise.

---

## 2. Open — needs a design decision, not just a patch

**2.1 The self-gate default (A14).** The plugin picks `test_command` as the `trust="self"`
gate; on a healthy repo that suite is already green, so the verdict cannot distinguish
real work from a no-op. Measured: runs 1–9 under the default produced **zero mergeable
units**; runs 10–21 under a worker-written gate + `preflight_expect="red"` produced
**11 of 11 one-shot successes**. Same model, same repo, same day.
The ledger's preferred fix (#3 of four): gate the self-graded run on the test files the
run itself created — the plugin already tracks them in `CHANGED`. Turns the default from
"cannot fail" into "cannot pass without the work".
*Note:* a partial mitigation already exists — `_ensure_self_gate`'s `min_override`
ratchet raises the floor to N+1 tests. Decide whether to extend that or replace it.
**Superseded in design by [DESIGN-v06-test-first.md](DESIGN-v06-test-first.md)** — tiers,
a test-first chain, and a dispatch rule that replaces the default rather than tuning it.
That doc also records five defects found while designing, three of which exist today
(notably: the vacuous-pass guard counts *skipped* tests as evidence).

**2.2 `scoped` denials (A15).** ~150 blocked calls across 12 builds, every one a
well-formed read-only `grep_search` or a test command carrying `2>&1`. Two problems:
allowlist the executor's own read-only built-ins (they cannot write, which is the whole
basis of the mode's safety argument) and permit a trailing `2>&1`. Then **split the
`DENIALS:` line** into "denied, harmless" vs "denied, worker may have routed around it"
— only the second justifies the receipt calling its own verdict suspect, which it
currently does every time.

**2.3 `STATUS: busy` instead of dropping the transport (A11).** A second tool call
while a build is in flight closes the stdio transport; recovery needs a human `/mcp`
reconnect, so a headless session is simply dead. Most of this is harness behaviour, but
refusing the second call cleanly is ours. A refused call costs a retry; a closed
connection costs the session.

**2.4 `challenge_brief: true` (A23).** Ask the worker to object to the brief *before*
building it. The finding: a worker-written gate is **the brief restated as an
assertion**, so a wrong requirement becomes a green test defending the defect, and
`preflight_expect` is blind to it by construction (red before, green after — exactly
what a correct build looks like). One requirement error cost 2 runs and ~35 min of GPU;
the worker could see the contradicting evidence and was never asked.

**2.5 Server lifecycle (A0d).** Write pid+version at startup; a new server terminates a
stale predecessor. At minimum, doctor reports it (1.6).

---

## 3. What is NOT a plugin bug, and should not be treated as one

`A21` and `A22` are the ledger's most important findings and neither is fixable in
code. Sixteen defects; **zero inside a delegated unit**; every one in a join. Six units
built, gated, merged, and called by nothing.

> **Delegate modules; gate seams yourself.** A unit brief describes one module, its gate
> runs in a worktree with the rest mocked, and `preflight_expect` proves the gate could
> fail *for that module*. The one thing the workflow can never assert is "and this is
> wired to that." A green receipt is evidence about a module and is routinely read as
> evidence about a product.

The plugin's job here is to make the seam risk *visible* (§1.1–1.3), not to verify it.
Keep this paragraph in the skill; delete the surrounding theory.

---

## 4. The `delegation` skill — 326 lines → ~100

~5.2k tokens loaded every session because both CLAUDE.md and the tool description say
to. It is written as a **manual, not a procedure**. Three buckets:

**Keep hot (~100 lines)** — genuine judgment, needed on every delegation: the loop
(trimmed), the routing table, non-negotiables, reading the receipt, `trust` choice, the
approval-mode table, the seam rule from §3.

**Convert to server behaviour (~60 lines of prose → deterministic code).** Every line
here tells the caller to remember something the server already knows:

| Skill prose | Becomes |
|---|---|
| ~~"Serial by default" (15 lines explaining resolved config)~~ | **DONE** — `DISPATCH:` receipt line; skill section 15 → 7 lines |
| `timeout_sec` **fitted regression formula the model is asked to apply by hand** | server estimates and warns |
| "Fan-out only worth it on real capacity, else these queue and buy nothing" | server warns on a batch that will serialise (1.6) |
| `STATUS: error` essay (17 lines telling Claude not to go debugging) | the receipt says it — it already names the cause and the fix |
| Heartbeat push/poll subsection (22 lines) | the submit response already advertises `RECEIPT:`/`HEARTBEAT:`; put the advice there |
| "Always pass `verify`" | server warns when it is absent |

**Move to cold reference files, named by the hot path (~110 lines)** — playbooks, the
"ask for these when they fit" parameter catalog (duplicates the schema), mutation
testing, the escalation ladder, compaction detail, the `scoped` mechanics.

**Two content bugs to fix while in there:**
- **A2:** the section *"Existing codebases: read the map, not the code"* tells Claude to
  query graphify's MCP. `docs/USAGE.md` says the opposite and is right — and the same
  skill states two sections earlier that architect-side graphify measured **+64%**. It
  contains both the measurement and the advice the measurement refutes.
- **A3:** nothing in the skill says worker-side graphify requires
  `approval_mode="scoped"`, because `auto-edit` has no shell. A Claude following the
  skill's own default silently gets grep instead of the graph.
- **A10:** document that `^cmd\b` in `shell_allow` allows *every* subcommand — the
  natural `^graphify\b` also permits `graphify update`, which can bill a cloud account
  and egress the source.

The ledger itself is archived (reading it cost ~$0.16/session, more than the
delegations it documented). This plan carries the findings that are still open.

---

## 5. Suggested order

1. §1.1–1.3 (three receipt greps) — largest defect class, cheapest fix, no new runtime
2. §1.4 (`progress.json` identity) — the last state file that cannot name its own run
3. §1.5 (doctor checks) — turns silent traps into startup errors
4. §4 (skill split) — do it *after* §1.5, since doctor checks delete skill prose
5. §2 (design decisions) — 2.1 (self-gate default) first; it is what blocked real work
6. ~~Archive the ledger~~ — done

## 6. Where a MODEL still decides (the non-deterministic surface)

Everything the server does is already deterministic: gates, locks, capacity,
reverts, spec protection, status classification, every receipt line. What follows is
the surface where a *model* still exercises judgement, and whether it can be removed.

### Claude (architect)

| # | Decision | Reducible? |
|---|---|---|
| 1 | Whether to delegate at all | **No** — the routing call is the job. The CLAUDE.md trigger makes it a rule, not a mood |
| 2 | What the `task` prose says | **No** — irreducibly natural language. This is the brief |
| 3 | What `verify` asserts | **No in general** — the gate IS the spec. But the *default* is fixable: §2.1 |
| 4 | `trust` self vs verified | **Partly** — the server can refuse `self` when the pre-flight already passed (§2.1), turning a judgement into an enforced rule |
| 5 | `approval_mode` | **Partly** — the server knows a task needs shell (graphify) and can say so |
| 6 | `touch_scope` | No — it is a scope statement |
| 7 | `timeout_sec` | **YES, fully** — the skill hands Claude a fitted regression to apply by hand. The server has the telemetry; it should compute and warn. *Highest-value removal available* |
| 8 | `max_iterations` | **Partly** — a project default already exists |
| 9 | Whether to split a task | **Partly** — `BURN: HEAVY` is computed already; it could refuse above a threshold instead of advising |
| 10 | Whether to accept a green receipt | **Shrinking** — every seam line moves work from "Claude should check" to "the server says". §1.1–1.3 just moved three |
| 11 | `shell_allow` regexes | **YES** — ship vetted allowlists for known tools. Hand-written `^graphify\b` also permits `graphify update`, which bills a cloud account (A10) |
| 12 | Resume vs cold vs `retry_of` | **YES** — the receipt already computes the recommendation; it could be the default |
| 13 | Whether a `NOTES`/`DENIALS` lead is real | **Partly** — split "denied, harmless" from "denied, may have routed around it" (§2.2) |

### Qwen (worker)

| # | Decision | Reducible? |
|---|---|---|
| 14 | How to implement | **No** — this is the delegation |
| 15 | What tests to write under `trust="self"` | **No**, and it must not be trusted as verification: the gate can only encode the brief (A23). Label it in the receipt |
| 16 | Whether to route around a denial | **No** — but it is now *reported*, and §2.2 makes the report precise |
| 17 | What to mock | **No** — but `MOCKED SEAM:` now names it when it matters |

**The pattern for reducing #4/#7/#9/#11/#12: they are all cases where the server holds
the data and asks Claude to remember a rule.** Each removal deletes skill prose too,
which is why §4 and this section are the same work.

## 7. Concurrency: how it works now

One knob. An endpoint declares `parallel_max` in the `endpoints` section of
`~/.qwen-delegate/executors.json`; that is how many requests it serves at once, held
machine-wide by a file lock so two Claude sessions queue rather than collide. Every
executor is an OpenAI-compatible API — self-hosted vLLM and a hosted key are the same
thing here (argv template + base URL), so there is no local/remote distinction in the
code and no second knob. An endpoint that should be serial gets one slot.

```json
{ "endpoints": { "snowy": { "parallel_max": 4 },
                 "openrouter": { "parallel_max": 8 } } }
```

A `batch` threads every item; each item queues on the endpoint *it* targets, so mixed
batches no longer inherit item[0]'s policy. Items sharing a base commit and a gate run
the pre-flight **once** and share the verdict. The receipt states what happened —
`DISPATCH: parallel · endpoint snowy · 4 slot(s) · 3 item(s)`.

**Concurrency requires isolation.** Endpoint slots only buy overlap for items that
don't share a tree: `worktree="auto"` (already the default in this repo's
`.qwen-delegate.json`). In-tree items still serialize on the repo lock, deliberately —
two workers editing one tree is corruption, not throughput. `specs/fleet_spec.py` pins
both directions.

`~/.qwen-delegate/executors.json` on this machine is now `snowy: parallel_max 4`.

Still capacity-limited from ONE session by §2.3: a second `qwen_*` tool call while a
run is in flight drops the MCP transport, so fan out through `batch` in one call rather
than through separate calls.

## 8. Repo hygiene noted, not yet done

- `reset_worktree()` in `qd/gittree.py` is now called only by its own spec — it existed
  for best-of-N. Keep it (cheap, pinned) or drop it when best-of-N is decided.
- `docs/archive/handoff-v05/` and `docs/archive/DIRECTION-v2.md` still reference
  `_ref_impl.py`. Left as historical records, deliberately.
- `qd/doctor.py` still carries Ollama-shaped advice (context split across
  `OLLAMA_NUM_PARALLEL`, reading CONTEXT off `ollama ps`). Ollama is not in use and
  will not be; clean this out when doing §1.5.
