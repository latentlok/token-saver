---
name: delegation
description: Spend Qwen's free local tokens instead of your context — build code against a gate, answer codebase questions, pull docs, map a repo. Use for mechanical/verifiable work a command could prove, and for read-only questions. Routes builds through the qwen_delegate gate loop; questions through qwen_query.
---

# Delegating to Qwen

You have a free local executor (Qwen) behind two MCP tools. The whole point: **its
word is never evidence — a gate decides, and only a short verdict reaches your context.**
One engine, one loop, whether you run it inline or hand it to the qwen-manager subagent.

## The loop

    map → design/spec (you) → submit (Qwen) → read the receipt file → (repeat) → relay

You speak at most three times; everything between is free-side and unseen:

1. **Pre-flight (when unsure of your own spec).** Ask read-only, where Qwen can't game:
   `qwen_query("Is this spec implementable / grounded / contradiction-free?")`. This is
   where your design mistakes surface honestly — a write-capable Qwen games a flawed gate
   to green instead of reporting it.
2. **Submit.** `qwen_delegate(task, cwd, verify=<a real gate>, approval_mode="auto-edit")`
   **does not block** — it answers in seconds with a claim ticket:

       STATUS: submitted
       RUN: <id>
       RECEIPT: <cwd>/.qwen-delegate/receipts/<id>.md — lands on completion
       HEARTBEAT: <cwd>/.qwen-delegate/progress.json
       WATCH: until [ -f <receipt> ]; do sleep 5; done; cat <receipt>

   The build runs on a background thread; the receipt file appears only when it is
   complete (chain/batch also give a `PARTIAL:` path that fills in link by link). So:
   **go do something else and read the receipt file later**, or paste the `WATCH:`
   one-liner into Bash when you have nothing else to do. `wait: true` blocks and returns
   the receipt in the response instead — worth it only for a run short enough that
   switching costs more than waiting. The gate is a shell command exiting 0 only on true
   success; the server runs it, feeds real failures back, and iterates on free tokens —
   you are not in that loop. `qwen_query` is unchanged: synchronous, answer in the
   response. (Ending your session kills its in-flight runs — they are threads of this
   MCP server, not detached jobs.)
3. **Relay.** Read the receipt file (never the diff). On green, **do not read the
   code — the gate already proved it.** Relay the outcome + proof.

## Route first: delegate, resume, or just do it

Overhead is roughly CONSTANT — a brief plus a capped receipt, ~700–3,000 of your tokens
whether the task is 20 lines or 500. That makes the size of the work the deciding factor,
not its difficulty:

    ≤20 lines · you already know the file · a fast gate exists  → edit it yourself, then
                                                                  run that gate
    aligned follow-up on a run that went green                  → warm resume (session_id)
    correction that CONTRADICTS what the session believes       → cold, or
                                                                  retry_of=<sid> + retry_message
    new unit · tests for existing code · >50 lines · many files → cold delegation, brief
                                                                  as a POINTER (paths and
                                                                  end state, not a design)
    a question about the code                                   → qwen_query, never a delegation

Do NOT go mapping the repo yourself to prepare a small edit: architect-side `graphify`
shell calls measured **+64% total cost** versus locating through the worker — every call
is a turn whose output stays in your context. Locate with one `qwen_query`, or from what
you already know.

**Resume vs cold (the heuristic the receipt now states for you).** Resume for
follow-ups: the next thing, same task, same cwd, nothing contradicted. Go cold for
repairs — a session that failed carries its confusion forward and will argue with your
correction rather than follow it; `retry_of=<session_id>` replays its stored brief cold
with `retry_message` appended, so you retype nothing. **The exception is the
shell-approval loop**: a run that came back with `SHELL APPROVAL NEEDED` is fenced, not
confused, and `shell_allow`/`shell_feedback` only reach the worker in the SAME session —
resume that one. The receipt's `RESUME:` line already picks a side; follow it.

## Non-negotiables

- **Always pass `verify`.** No gate = no evidence. Qwen has reported "all tests pass" with
  the test tool uninstalled.
- **Specs are yours, `*_spec.*`, committed before the build.** Qwen's edits to them
  auto-revert. Its own scratch tests are `*_qwen.*` (encouraged, never the gate).
- **Write the minimum spec, pinned to exact behavior and edge cases.** A vague task gets
  confident invented scope; a contradictory spec gets gamed. If you can't write the spec,
  the design isn't done — think more or kick it up, never hand Qwen a guess.
- **Vague task → `approval_mode="plan"`** (read-only; it returns options, cannot write).

## Reading the receipt

`STATUS` decides. Then the deterministic lines, which cost no model tokens and catch what
a green gate can't: `RUN` (attempts · peak ctx · wall · denials · strays), `CHANGED`
(filesystem truth), `NEW PUBLIC SURFACE` (scope creep — review the list, not the diff),
`TEST DODGE` (a skip added in the tests being delivered — the one line worth reading on a
GREEN receipt), `STRAYS`, `SCOPE` (co-work: changed but not by the worker — reported,
never reverted), `GRAPH`, `COST`, `ROLLBACK`. `gate_suspect` means YOUR gate is broken
(identical output before/after) — fix it, don't iterate. `scope_violation`,
`fixture_unproven`, `result_invalid` and `reported` say exactly which contract ended the
run. `NOTES`/`MISREPORT`/`DENIALS`/`FINDINGS` are leads to check, never trusted.

## Existing codebases: read the map, not the code

Don't read a repo into your context. Query graphify's MCP for the scoped subgraph
("what calls X?"), verify only load-bearing claims against source (INFERRED edges and
semantic summaries are leads; tree-sitter coordinates are trustworthy), then pin the
change as a spec. The receipt's `GRAPH` line tells you if the map is fresh.

## Greenfield = iteration zero

Same loop. Write the HLD, commit every inter-module contract as a `*_spec.*` file
**before any code**, then delegate bottom-up (each unit gated on its own spec + its
dependencies'). Contracts-first is what makes the pieces compose.

## Serial by default (`dispatch`)

One local endpoint is one GPU. Concurrent requests do not each get a private
context — on Ollama the loaded context is split across parallel slots — so fan-out
buys wall-clock at the price of a shorter effective context per request, which is
how a turn comes back truncated mid tool-call. Out of the box every endpoint holds
**one slot**, and that slot is held machine-wide (a file lock), not just within
this session — two Claude sessions pointed at one box now queue instead of
colliding.

Set `"dispatch": "serial"` in `.qwen-delegate.json` (or `~/.qwen-delegate/config.json`)
to pin that even where an endpoint declares `parallel_max > 1`: batches then run
their items in order, and every call queues for the single slot. `"parallel"`
honours the declared capacity. Anything else reads as serial — a typo must not
turn concurrency on.

## Fan-out (parallel builds)

Only worth it on real capacity (`parallel_max > 1` and `dispatch` not serial);
otherwise these queue and buy nothing. Pin the contracts, then either:
- **`batch=[{task, verify, ...}, ...]`** — N delegations in ONE call, fanned across
  worktrees server-side. The reliable way to parallelize from one session.
- **`worktree="auto"`** per call — isolates each build on a `qwen/<id>` branch; the
  receipt carries the exact `MERGE:` command. A merge conflict is a design signal (two
  units' contracts overlapped) — escalate, don't force.
Cap is per-endpoint (your hardware); more workers = raise it in the executor profile.

## touch_scope

`touch_scope=["a.py","b.py"]` restricts edits to named pre-existing files (out-of-scope
edits auto-revert); new files stay free. Use it to bound a change to its intended surface.

## Parameter facts (the schema lists them; the measured details live here)

**Approval modes (probed):**

    plan       write NO   shell NO         any vague task — can't invent scope it can't write
    auto-edit  write YES  shell NO         ← default for code; the SERVER runs verify and
                                             feeds failures back, so Qwen converges shell-less
    scoped     write cwd  shell ALLOWLIST  when Qwen should run its own tests; NOT a sandbox
    yolo       write YES  shell YES        only when shell IS the work (build, migration, git)
    default / auto — deny everything headless; never use

`scoped` allows the exact `verify` command, a read-only/test allowlist, and your
`shell_allow` regexes. Blocked commands surface as SHELL APPROVAL NEEDED — judge each
**on the command alone**; approve by adding its pattern to `shell_allow` and re-delegating
with the same `session_id`, or deny with the reason in `shell_feedback` (a reasonless
denial just makes it guess). Compound/redirect/network commands are rejected regardless.

**Vague work, two-phase:** `approval_mode="plan"` (no verify needed) → options + SESSION →
user/you pick one → re-delegate that item warm (`session_id`) with `auto-edit` + a real
gate. The chosen plan item becomes the spec.

**timeout_sec:** the 900 default kills large tasks mid-write. Fitted model (198 calls):
`seconds ≈ turns×avg_context/10,882 + output_tokens/70`, avg_context ≈ (22k + peak)/2.
Estimate, then set ~3× — p90 ran 3× median, and over-setting costs nothing.

**Sessions:** stateless (omit `session_id`) is the default and usually right — a fresh
session re-reads QWEN.md, which is what makes the rules bind. Resume only for a tight
follow-up on the SAME task, same cwd; for a correction use `retry_of` instead (see the
resume heuristic above).

**on_compaction:** compaction is the documented fabrication trigger, so the default is
**`refuse`** — the run STOPS the moment one is attempted, nothing from it is graded, and
you get the call back. That receipt is not a retry signal: the same task will hit the
same wall. **Split it into smaller units with their own gates**, or narrow the scope.
The plugin also asks the executor to block the compaction (PreCompact exit 2, which
qwen documents but does not reliably honour) — treat that as a bonus, not the mechanism;
the stop is what you can count on. `reinject` (continue on the summarised history) and
`discard` (cold restart) are still there if you deliberately want them. For any run you
choose to continue after a compaction, put critical rules in the TASK TEXT — QWEN.md
does not survive one.

**workers (best-of-N):** N independent candidates, first gate-pass wins. Free tokens but
N× wall time; needs `verify` and a committed base.

**qwen_query:** keep questions bounded to a few files — a forced whole-repo read pushes
Qwen past compaction, after which it fabricates having read things. Structure and
semantics are reliable; precise citations are not (measured: a perfect library map with
every line number fabricated). The VERIFY list says what to confirm.

**trust:** `"self"` (default) — L5 full trust: omit `verify`, the server gates on the
delegate's OWN suite behind a non-vacuous guard (`min_tests` in `.qwen-delegate.json`,
default 5) and stamps `TRUST: self` in the receipt. Max token saving, max reliance: a
self-graded suite can share the code's blindspot (measured — see FINDINGS "L5
self-grading"). `"verified"` — your `verify` command is the gate; pass it for stakes you
must know rather than trust.

**Choosing per task (the `auto` discipline):** classify each task and pass `trust`
explicitly — `"verified"` when it is correctness-critical, irreversible, outward-facing,
or touches security / data-loss / money / auth; `"self"` for low-stakes mechanical or
greenfield work. When in doubt, `"verified"`. If the standing default (project
`.qwen-delegate.json` or machine `~/.qwen-delegate/config.json`) is `"auto"`, the server
*enforces* this — it refuses a bare call, so there is no silent fallback and you must
make the criticality call on every delegation.

**Hygiene:** re-read any file Qwen touched before editing it yourself (your cached copy
is stale); parallel delegations need separate worktrees — `batch` handles that for you.

## Playbooks (briefs as repo files)

A recurring or heavyweight brief belongs in the repo, not retyped per call:
`brief_file: "playbooks/x.md"` sends the document by name — its body is the task (your
`task` rides along as an addendum), `---` front matter carries verify/touch_scope/
timeouts where the call is silent (your args always win), and `{{slot}}`s fill from
`vars` (both directions refused by name on a mismatch). The receipt pins
`BRIEF: path @ digest`, the ledger tallies per document, and the worker editing the
document reverts like a spec edit. On a retry, `amend_brief: true` folds your
`retry_message` into the document as a dated `## Amendments` line — git versions the
correction, so the next reader inherits it. Big document? `chain: true` compiles
`## Step <n>` sections into a chain (each link gets the preamble + its own step, so
per-link context stays flat); past ~5 amendments the receipt says consolidate. Keep the
document to the DELEGATION (task, gate, scope) — background belongs in stable repo docs
the worker reads on demand.

## Ask for these when they fit

- **`brief_file` (+ `vars`, `amend_brief`)** — the brief as a versioned repo document;
  see Playbooks above.
- **`preflight_expect`** — `"red"` (greenfield) refuses a gate that already passes;
  `"green"` (revision) stops the preflight alarm on a suite that was green by premise.
- **`verify_timeout_sec`** — kill time for ONE gate run (default 300); a pre-flight that
  times out refuses the run (`GATE UNUSABLE`) instead of blaming the worker.
- **`advisory_gates=[{name, cmd}]`** — loose conformance/placement/drift checks that glow
  red in the receipt and never touch `STATUS`, the retry loop, or the worker.
- **`chain=[...]`** — DEPENDENT steps in one call, serial on the same tree, halting at the
  first non-green link (`batch` is the independent one; both together is refused).
- **`report_dont_fix=true`** — diagnose, don't repair: one attempt, status `reported`,
  a `FINDINGS:` line, and a red gate as the deliverable.
- **`result_schema={...}`** — a VALUE back instead of prose: the worker ends with a JSON
  block, violations are fed back by path, the receipt carries it verbatim (also on
  `qwen_query`, reported once rather than retried).
- **`retry_of=<session_id>` + `retry_message`** — replay that run's stored brief COLD with
  your correction (`task: ""` reuses the stored task).
- **`fixture_provenance=true`** — created fixtures must carry `captured-from:` (a `.src`
  sidecar for binaries); imagined fixtures pass any gate written against them.
- **Project `.qwen-delegate.json`** — `task_suffix` appends your standing worker discipline
  to every task server-side (compaction-safe, unlike QWEN.md); `approval_mode` /
  `shell_allow` / `timeout_sec` / `preflight_expect` / `verify_timeout_sec` are defaults a
  call arg still beats.
- **`.qwen-delegate/progress.json`** — heartbeat: records, input tokens, attempt, state.
  Answers "is it hung?" for a file read instead of a turn.

## Inline vs the manager subagent

Run the loop **inline** for interactive work and small counts — and note that "run it off
to the side while I keep talking" is no longer a reason to spawn anything: a submit
already does that for free. Hand it to the **qwen-manager** subagent when isolation earns
its preamble: a multi-unit build whose specs and verdicts would silt up this session, or
a fan-out with its own iteration to babysit.

## `STATUS: error` is the executor, not this repo — do not go debugging

An error receipt means the *worker* failed (output-token truncation, endpoint
down, timeout, unparseable run) — it is not a symptom of the code you were asked
to change, and it is not a bug in the plugin to be traced. The receipt names the
cause and the setting that fixes it. **Relay it and move on**: retry once with a
tighter scope or a longer `timeout_sec`, otherwise do the work inline or hand the
line to the user. Reading plugin source, tailing logs, or probing the endpoint to
"find out why" spends exactly the context this system exists to save.

Truncation specifically: the cap is **client-side** — qwen-code sends `max_tokens`
itself (32k default for a model name it does not recognise; its normaliser keeps
only the part after `:`, so an Ollama tag like `qwen3.6:27b-agent` reads as
`27b-agent` and matches nothing), and thinking tokens count against it. Nothing on
the inference server shows this. Fix is `QWEN_CODE_MAX_OUTPUT_TOKENS` or
`generationConfig.maxTokens` in `~/.qwen/settings.json`, or ask for less output per
turn — all user-side.

## Escalation ladder (build won't converge)

Reflexion retries (automatic, in the loop) → best-of-N (`workers=N`) → read the failing
sliver → patch it yourself as last resort. Escalate to the USER only for genuine calls:
direction, outward-facing or hard-to-undo actions, a merge conflict.

## Mutation-test your own gates

After a module's gate first goes green, spend one free `qwen_query` asking Qwen to propose
mutations the spec would miss; apply+judge them with a throwaway harness. Measured: 7/8 of
its proposals survived a hand-tested spec. Adversarial review is read-only, so there's
nothing to game. Green → commit → mutate (never mutate uncommitted work).
