# AGENT.md — supervised-delegation

Part I is using the tool, Part II is working on this repository. A reference, not a tutorial: read
§1, then look things up.

# Part I — using the tool

## 1. Three statuses that read as success and are not

| Status | Actually means |
|---|---|
| `success_but_preflight_passed` | the gate was **already green before the worker touched anything**. Passing it again proves nothing about the work. |
| `unverified` | **no gate ran.** What you have is the worker's own claim. |
| `reported` | **nothing was built.** A `report_dont_fix` run; the gate output is the deliverable. |

> **`success` and `success_but_preflight_passed` both count as green.** Not "green to you" — green
> to the machine: both continue a chain to the next link, both commit the worktree, both trigger
> the code-graph refresh.

So the question is never "did it say success" but: did a gate run (`unverified` — no); was it red
before the worker started (`success_but_preflight_passed` — no); was anything built (`reported` —
no)? And `STATUS: submitted` is not a result at all — a run id and a path — so never relay a run
whose receipt has not landed. Full list: §9.

## 2. The two tools

**`query`** — a read-only question about a codebase. Plan mode by construction: it cannot
write, needs no gate, and runs **synchronously**, so the answer is the deliverable. Required
`question` and `cwd`; optional `format` (`answer` default, `map` for a structured repo map),
`focus`, `session_id`, `timeout_sec`, `result_schema`. No `executor`.

**`delegate`** — build something. Required `task` and `cwd`, everything in §3 optional; the
call submits and the receipt lands later. A question about code is a `query`, **never** a
delegation: that spends a build loop on something with no gate. `cwd` must be a committed git repo —
git is the only rollback.

## 3. `delegate` arguments

| Argument | What it does | Default |
|---|---|---|
| `task`, `cwd` | the brief, and an absolute path to a committed git repo | required |
| `verify` | shell command run in `cwd`; exit 0 is a pass. Runs **before** the worker (the pre-flight) and after **every** attempt; failures are fed back as the next correction | none — and none means `unverified` |
| `preflight_expect` | what `verify` should say before the worker runs. `red` (greenfield) refuses a gate that already passes and turns on the red-gate quality checks; `green` (revision work) suppresses the `success_but_preflight_passed` demotion | `any` |
| `trust` | `verified`: your `verify` is the gate — use it for correctness-critical, irreversible, outward-facing, security, money or auth work. `self`: the worker writes *and* grades its own suite | `self` |
| `touch_scope` | pre-existing files the worker may modify; an edit outside the list auto-reverts and fails the attempt (on the last attempt, `scope_violation`) | everything |
| `approval_mode` | `auto-edit` write, no shell · `plan` read-only, for any vague task · `scoped` auto-edit plus allowlisted shell · `yolo` full shell, only when shell *is* the work. **`default` and `auto` deny everything headless** | `auto-edit` |
| `shell_allow`, `mcp_allow` | scoped mode only: regex allowlists for commands and MCP tool names | project config |
| `max_iterations`, `timeout_sec`, `verify_timeout_sec` | attempts (project config, else 3, clamped 1..10); the per-attempt kill, which unset is **fitted from this project's own finished runs**, so identical calls in different repos get different budgets (900 with no history, clamped 30..7200); and the kill time for one `verify` run (300, clamped 10..3600) — a pre-flight that times out refuses the whole run before an attempt is burned | see cell |
| `worktree` | `auto` isolates the run in a git worktree on a `qwen/<id>` branch and the receipt hands you the `MERGE:` command; uncommitted changes in your tree are not in it | `off` |
| `wait` | block and return the receipt in the response instead of a submission ack | false |
| `report_dont_fix` | diagnose, do not repair: one attempt, one gate run, no retry loop. The gate output is the deliverable (a red gate is the reproduction) plus a `FINDINGS:` line | false |
| `result_schema` | the shape you need back (§8) | none |
| `chain`, `batch`, `carry` | dependent steps, independent fan-out, and what crosses between links (§7) | — |
| `brief_file`, `vars` | a repo-relative markdown playbook becomes the brief; your `task` rides along as an addendum, `{{slot}}`s fill from `vars`, and the receipt pins `BRIEF: <path> @ <digest>` (§6) | none |
| `retry_of`, `retry_message` | re-run that session's **stored** brief — task, gate, scope, mode, trust — **cold**, with one line of correction appended as `CORRECTION`; session resume is stripped even if you pass one, because a session that failed argues with the correction. `task: ""` reuses the stored task; any argument you pass beats the stored one. `amend_brief: true` writes the correction into the playbook as a dated `## Amendments` line instead, so git versions it | none |
| `challenge_brief` | a read-only objection pass before building (§6). `challenge_warm` resumes its session for the build: +2% input tokens, same wall clock, off only because nothing has measured that the continuity *helps* | **true** |
| `session_id`, `executor` | warm resume of a prior delegation (same `cwd`), and the executor profile to run it on. Omit `session_id` for anything but a tight follow-up: fresh sessions re-read the rules file, and you go cold for repairs | cold; machine default |
| `on_compaction` | `refuse` · `reinject` · `discard`. Compaction is the documented fabrication trigger; only `refuse` declines to build on it | `refuse` |
| `advisory_gates` | `[{"name": …, "cmd": …}]` run once after the final attempt. They **never** affect status, never enter a retry prompt and never reach the worker; a red one just glows in the receipt | none |

**A `shell_allow` pattern matches the command, not the subcommand** — `^tool\b` means every
subcommand `tool` has — and `[]` means "no extra capability", not "the project's list".
**`touch_scope` does not stop new files** (a scope names what may be *modified*) **and never reverts
an unattributed change**: a file that changed with no worker write logged for it belongs to you or
another agent in the same tree, and reverting those destroyed a caller's concurrent work once. And
**a gate is meaningful only when it was red before, green after, and red for the reason you care
about**: one that runs no tests, whose tests are all skipped, or that fails on an unrelated import
error is not a gate — the red-gate checks under `preflight_expect: "red"` refuse on all three.
**An unrecognised value for an enum-shaped argument** — `preflight_expect`, `on_compaction`,
`worktree` — **degrades silently to the default rather than refusing.** A typo does not bounce;
it runs with a setting you did not ask for and the receipt reads normal.

## 4. Combinations that refuse

- `chain` with `batch`; `batch` inside a batch item (nesting is one level); `brief_file` beside a
  call-level `chain`/`batch`.
- `preflight_expect: "red"` when the pre-flight already passes (`GATE VACUOUS`); `preflight_expect:
  "green"` with `trust: "self"` and no `verify`, since the generated gate ratchets so the pre-flight
  comes back red; a `trust` default of `auto`, since the server cannot judge criticality.
- a `verify` pre-flight that times out (`GATE UNUSABLE`), before any attempt is burned — every retry
  would have paid that cost; a `result_schema` keyword outside the enforced five (§8); `carry:
  "session"` (§7).
- an executor endpoint that is provably down (`EXECUTOR UNREACHABLE`): the profile's base URL gets
  one GET before the first executor call, and a connection error, a timeout or a 5xx refuses the run
  — any answer, 401 included, proceeds. Probed once per call (a chain or batch probes each distinct
  endpoint once at the head), never cached across calls, skipped when the profile declares no base
  URL; on a submitted run the refusal lands in the receipt, like `GATE UNUSABLE`.
- a playbook front-matter key outside the ten, a wrong-shaped value, a non-`key: value` line, an
  unclosed `---` fence, an unfilled `{{slot}}`, a `vars` key matching no slot; `amend_brief` without
  `retry_of` + `brief_file` + `retry_message`; `retry_of` with no stored brief; `BRIEF TOO BIG`; a
  non-git `cwd`.

Argument-shape and precondition refusals answer synchronously; a chain runner's refusal lands in the
receipt file instead, because the call already answered `STATUS: submitted`. `wait: true` brings
those back inline.

## 5. One worked example

`greet/core.py` holds `greet(name)` and `shout(name)`, with two passing tests under `tests/`. The
call, and the answer it gets in milliseconds — **real output shape, run against a scratch repo**:

```json
{"task": "Add farewell(name) to greet/core.py returning 'Goodbye, <name>.'",
 "cwd": "/abs/path/to/greet", "verify": "python3 -m pytest tests -q",
 "preflight_expect": "red", "touch_scope": ["greet/core.py"]}
```
```
STATUS: submitted
RUN: r5fc3ab
RECEIPT: /abs/path/to/greet/.delegation/receipts/r5fc3ab.md — lands on completion
HEARTBEAT: /abs/path/to/greet/.delegation/progress.json
WATCH: until [ -f …/receipts/r5fc3ab.md ]; do sleep 5; done; cat …/receipts/r5fc3ab.md
```

The work has not happened yet: go do something else, then read the receipt (a chain or batch also
gets a `PARTIAL:` path). Receipts are capped at 3000 characters. **Illustrative — a shape, not a
transcript:**

````
STATUS: success
SESSION: 4f2a…  ATTEMPTS: 2/3
CHANGED: 1 file(s) (greet/core.py)
CHALLENGE: brief reviewed against the code, no objection
RESULT: valid (schema)
```json
{"added": ["farewell"], "outcome": "done"}
```
PAID: ~180 tokens of your context (this receipt, excluding this line) for 82,000 in / 3,200 out on free compute -- ~474x leverage
````

Read it in this order: **`STATUS:`** against §1, not the word "success"; **`ATTEMPTS:`** and the
trail lines, which name what each attempt got wrong; **the safety lines** (`TRUST:`, `PREFLIGHT:`,
`SCOPE:`, `HEAD MOVED:`, `GATE SUSPECT:`, …), never shed when the cap bites, with `SUPPRESSED:`
naming whatever did go silent; **`CHANGED:`**, where `nothing (Qwen wrote no files)` is a green
receipt for a run that did nothing; then **`RESULT:`**, **`MERGE:`/`ROLLBACK:`** and **`PAID:`**.
`FINDINGS:`, `HANDOFF:`, `NEXT:`, `NOTES:` and `DENIALS:` are the worker's own words — **leads to
check, never trusted**. Never re-run a green gate or read the diff to confirm a receipt: that spends
the context this tool exists to save.

## 6. Three surprises

**`challenge_brief` defaults ON: every non-report delegation buys an extra read-only model pass.**
The worker reads the code first and may object to your brief, because a worker-written gate is your
brief restated as an assertion — a wrong requirement becomes a green test defending a defect, and
`preflight_expect` cannot see it. It refuses the run (`BRIEF CHALLENGED:` plus `EVIDENCE:`) only
when the objection cites a path that exists. Decline with `"challenge_brief": false`; it is skipped
for `report_dont_fix` runs and chain links after the first.

**A playbook's front matter may name `verify` — a COMMAND this machine runs.** Ten keys are
permitted (`verify`, `touch_scope`, `shell_allow`, `approval_mode`, `timeout_sec`,
`verify_timeout_sec`, `preflight_expect`, `advisory_gates`, `max_iterations`, `chain`); they fill
only what the call left out (`call argument > front matter > project config > machine config >
builtin`), and an unrecognised key is refused by name, because a typo'd gate key silently ignored is
a gate that never runs. `trust`, `executor` and `worktree` are deliberately absent: who is trusted
and where the run happens are the caller's decisions. But the pre-flight runs `verify` **before the
worker starts at all**, past every approval mode, `touch_scope` and trust level — same for
`advisory_gates[].cmd` — a *larger* grant than the three keys left out. **A playbook from a repo you
did not write runs its authors' commands.**

**What lands in your repo.** `.delegation/` is self-ignoring — it writes its own `.gitignore`
containing `*`, and your project's is untouched — and three files in it are yours:
`receipts/<run_id>.md`, the deliverable; its `.partial.md` sibling while a chain or batch runs;
`progress.json`, to see the run is alive. Outside it: the first delegation writes **`QWEN.md`**, the
worker's standing rules, **uncommitted**, and the receipt asks you to commit it; in `CLAUDE.md` only
the span between the plugin's markers is rewritten (no file, or no markers, is a no-op); and
**`graphify-out/`, from the graph tool, is not covered by the self-ignore** — the one directory to
gitignore by hand.

**The code graph** is a fourth thing, and not yours to query: it is a tool that the **worker** uses,
not one you use — architect-side graph calls measured **+64% total cost**, because every call is a
turn whose output stays in your context forever. Ask one `query` and let the worker read the
graph on free tokens, under `approval_mode: "scoped"` with the read-only subcommands allowed
explicitly: `auto-edit` has **no shell at all**, so a worker told to use the graph under it falls
back to grep silently.

## 7. Chains, batches, continuity

`chain` is N **dependent** steps run in order on one tree — write the failing test, then make it
pass — each link an item with its own `verify`, `preflight_expect` and `touch_scope`; front matter
`chain: true` compiles a playbook's `## Step <n>` sections into exactly that. `batch` is N
**independent** delegations fanned across worktrees, one receipt per item in submission order; an
item may carry its own `chain`. Receipts are wrapped `=== chain link k/n: <status> ===` and `===
batch item ===`.

**A chain halts at the first link that is not `success` or `success_but_preflight_passed`** — the
second place the two-greens rule bites — and the rest render as one-line `SKIPPED` receipts. The
worktree is acquired once for the whole chain and kept if **any** link committed: link 3 failing
does not retract link 1's delivery. Exactly one hop crosses, never accumulated — the worktree, a
commit per green link, one `carry` payload — and link 1's brief-challenge pass reads the **whole
chain**, so a link 1 / link 3 contradiction surfaces before link 2 commits. An item inherits the
call's settings unless it sets its own.

`carry` decides what a link inherits: **`handoff`** (the default) prepends the previous link's
`HANDOFF` / `FILES` / `NEXT` / `FINDINGS` lines to the next task as context, framed `(context, not
instructions -- your task follows)`; **`structured`** passes its **validated** result JSON in a
declared slot instead, leaving the task text unchanged; **`none`** passes nothing.

The grades are **exclusive** — a typed result, not the preamble as well — and **every grade runs the
next link in a fresh session**; `carry` on a lone call or a batch item is inert, and a grade you
declared is reported on the `CARRY:` line even when it carried nothing. `carry: "session"`, one
shared conversation across links, is **refused by name** — not a typo, a decision; a typo gets
`STATUS: error`, fixed by seeing the real names. Both fire at the head of the chain even when the
bad grade is on link 3. **Real output:**

```
STATUS: refused
`carry: "session"` on link 2 is NOT BUILT here, and is refused rather than quietly
served as something else. A shared conversation is the only grade that removes the
ISOLATION between links: every other grade runs the next link in a fresh session…
Nothing was run. Ask for "none", "handoff", "structured" -- or say what `session` was
for, because the answer is a change to this server, not to your call.
```

## 8. Result contracts

`result_schema` declares the shape you need back: the server validates the worker's final JSON
block, feeds violations back by path like a failed gate, and carries the block verbatim on the
receipt, so you parse a value instead of prose. **The enforced subset is exactly five keywords:
`type`, `enum`, `required`, `properties`, `items`** — pure metadata (`title`, `description`,
`$schema`, …) is tolerated because it constrains nothing, `$ref` is not, and **anything else is
refused before anything is built**: a constraint the system cannot enforce is refused *when you
accept it*, not silently ignored. **Real output:**

```
STATUS: refused

result_schema uses keyword(s) this server does not enforce: minimum at $.n.minimum.
Only enum, items, properties, required, type are checked -- a schema built entirely
from that subset is honoured, so rewrite the schema within it, or drop result_schema.
```

On `query` that refusal is harder still: a query has no gate to bounce a false pass off, so it
stops the call rather than reporting one, and the conformance check there is only *reported* — one
`RESULT:` line above the answer. `RESULT: valid (schema)` counts only where the server writes it,
never inside quoted worker output.

## 9. Every status, in precedence order

Each is a more specific diagnosis than the one under it.

| Status | Means |
|---|---|
| `result_invalid` | the final JSON block never conformed to `result_schema` |
| `stopped` | a live limit (burn budget, stall, timeout) ended the run |
| `compaction_refused` | the worker's session compacted; nothing from the run is trusted |
| `spec_violation` | the worker edited a protected spec, or the playbook |
| `scope_violation` | the worker edited outside `touch_scope`; edits reverted |
| `gate_suspect` | gate output identical before and after — your gate is probably broken |
| `fixture_unproven` | fixtures lack `captured-from` provenance |
| `unverified` | no `verify` supplied — **false green** |
| `stuck_no_progress` | the last two attempts produced byte-identical gate output |
| `verify_failed` | the gate is red |
| `reported` | a `report_dont_fix` diagnosis — **nothing was built** |
| `success_but_preflight_passed` | passed, but the gate already passed beforehand — **false green** |
| `success` | the last attempt ended `VERIFY PASS` — **the only real green** |
| `error` | empty trail, an argument-shape mistake, or an exception |
| `submitted` | the async acknowledgement — a run id and a path, **not a result** |
| `refused` | the server understood the ask and declined |

**`error` means you made a mistake in the call; `refused` means the call was understood and
declined.** Every block above marked **real output** was produced with no model at all —
argument-shape and precondition refusals are answered before anything is spawned — and anything
needing a live worker is described, not demonstrated. If you have not run it, say it is
illustrative; relay what a receipt says, not what you expected.

# Part II — working ON this repo

Every rule below exists because it was violated and cost something; the reason stays attached,
because a rule without its evidence is one the next reader relaxes.

## Building

**Spec first, and it must fail before the fix.** A test written after the code passes on the first
run and has proved nothing. Where the code is already right and only the test was missing, **the red
phase is the mutation**: break the code, watch the spec go red, restore, watch it go green. And
**assert the mutation actually applied** — three times in one session one reported clean because the
harness silently failed to apply it, and a mutation you did not confirm landed is a green you
invented.

**Never delegate a `specs/*_spec.py`.** The gate must come from a different hand than the builder: a
worker that writes the spec for its own change has restated its own understanding as an assertion.
**Run the full suite as its OWN command, and read the exit code before writing it down** — `bash
ci/run-specs.sh`, alone, because chained to the commit a suite failure gets swallowed by the next
command's success and the commit lands on a red tree.

**Commit explicit paths. Never `git add -A`.** It picks up scratch files, worktree debris and other
agents' in-flight work, and the diff you reviewed is not the diff you committed. **Restore by file
copy, never `git checkout`, and never move a file you do not own**: `git checkout` and `git restore`
take the whole path back, including a concurrent edit somebody else made in the same tree.
**Comments explain WHY, with evidence** — a comment asserting a property the code does not have is a
**defect here, not a nit**, because the next reader trusts it and stops checking. **Zero third-party
dependencies, standard library only**: there is nothing to install, and a dependency here is a
dependency in every user's environment, on machines this plugin will never see.

## Three traps that look like something else

**`/tmp` filling up looks exactly like test failures.** The specs `mkdtemp` a git repo per test and
unittest never cleans them up; a day of local runs hit ENOSPC on inodes and took every session on
the box down. `ci/run-specs.sh` routes everything under one throwaway `TMPDIR` and removes it on
exit. **Running a spec directly? `export TMPDIR=$(mktemp -d)` first.**

**A probe that fails to fire looks exactly like a defence that works.** Both produce a clean result,
so before believing a guard held, prove the thing it guards against actually reached it. And **a
deleted spec file makes the suite PASS**: `ci/run-specs.sh` globs `specs/*_spec.py`, and a file that
is not there is not a failure. Removing a spec is never how a red suite gets fixed.

## Releasing

1. Bump the version in **both** `.claude-plugin/plugin.json` and `SERVER_INFO` in `qd/server.py` —
   two declarations of the same number, and they drift silently.
2. Update `CHANGELOG.md`, open a PR.
3. **Let CI decide; do not merge on a local green.** A local run has your `HOME`, your `/tmp` and
   your Python; CI is the environment the claim is about.
4. Squash-merge, then tag.

**Merging does NOT publish to existing users by itself.** Users compare the *declared version
string*, so a merge without a version bump reaches nobody — step 1 is the release, not the merge.
And third-party marketplaces do not auto-update: a user stays on the old version until they run
`/plugin marketplace update supervised-delegation`.

## Documentation

Three root documents, distinct audiences, must not drift: `README.md` (a human, installing),
`ARCHITECTURE.md` (the shape, no paths), and this file (an agent — Part I to use the tool, Part II
to edit this tree). Two documents disagreeing is worse than either being wrong, because whichever
the reader met first wins and neither knows. `specs/skill_spec.py` pins what
`skills/delegation/SKILL.md` and this file must agree on — a real gate, and prose is the one part of
this repo nothing else executes.

Only Part II may name paths in this tree. Paths and line numbers in `README.md`, `ARCHITECTURE.md`
and Part I rot, and a rotted path in a user-facing doc is a wrong answer with a citation.
