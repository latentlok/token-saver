# HLD — qwen-delegate v2

High-level design for [archive/DIRECTION-v2.md](archive/DIRECTION-v2.md) (superseded; build stands). Owns: system context,
component boundaries, the delegation lifecycle, and **every cross-module contract**
(pinned once here; module LLDs reference them, never redefine them). Per-module design:
[LLD.md](LLD.md).

## 1. What the system is

Claude is the architect: it writes designs and gates (specs). Qwen is the builder: it
writes code on free local compute. A small Python referee (`server.py` + `qd/`) runs the
builder against the gate and reports a short receipt. The builder's word is never
evidence; the gate decides. Trust level is a two-ended dial — `self` (L5) by default, or `verified`.

**Backend: Python 3, stdlib only.** 98% of delegation wall-clock is model inference
(measured); the referee only launches processes, runs git, and shuffles JSON. It must be
trivially readable and editable by Claude and Qwen — plain Python is a feature.

## 2. Requirements

Functional:
- F1  Delegate a build task against a verify gate; iterate on real failures; receipt.
- F2  Read-only Q&A / codebase mapping / web research (firecrawl via Qwen's own MCP).
- F3  Concurrent calls: parallel queries; parallel delegations across repos; parallel
      delegations within a repo via worktrees; parallel best-of-N.
- F4  Executor profiles: swap the worker (local Qwen now, API-class later) via config.
- F5  Graph freshness: record indexed sha; refresh incrementally post-verdict; report
      staleness from git facts.
- F6  Refs pinning: fetched web references land on disk, reported in the receipt.
- F7  Self-test prefilter: the builder's own tests run before the gate, advisory only.
- F8  Run log v2: tokens, cost (0 for local, *recorded*), worktree/merge/graph facts.

Non-functional:
- N1  Receipt ≤ 3,000 chars; everything returned to Claude is capped and trimmable.
- N2  Zero dependencies; single `python3 server.py` entry; no packaging metadata.
- N3  Zero-trust invariants (archive/DIRECTION-v2.md §Invariants) hold at every stage.
- N4  The crane rule: the current server builds v2 and is deleted only at cutover.
- N5  Every module ships with a spec in `specs/` and survives a mutation pass.

## 3. Components

    Claude Code ──(MCP stdio)──▶ server.py         dispatch: threads, write lock,
                                    │               repo locks, per-endpoint semaphores
                                    ▼
                                 qd/ package        the engine (see LLD.md)
                                    │  profiles → worktrees → engine → invoke → qwen CLI
                                    │  gittree guards · verdict · graph · refs · runlog
                                    ▼
                                 git working tree / worktrees      (the database)

    Claude Code ──(MCP)──▶ graphify server          queries only; we never proxy them
    qwen CLI ──(its own MCP)──▶ firecrawl           web access; invisible to our server

Claude-side: `skills/delegation/` (the canonical loop, loaded by main session and by the
thin `agents/qwen-manager.md` shell alike), `commands/offload.md`, tool descriptions
as the ambient capability map.

## 4. The delegation lifecycle (the "conversation")

Claude speaks at most three times; everything else is free-side.

    0. PRE-FLIGHT (optional, skill-driven, read-only — no new tool):
       qwen_query: "is this spec implementable / grounded / contradiction-free?"
       Reliable because write-less Qwen can't game; this is where Claude's design
       mistakes surface (measured: 0/3 honesty mid-build, clean answers read-only).
    1. BUILD (qwen_delegate):
       preconditions (rules file, git, clean specs)
       → worktree acquire (fan-out / best-of-N; else in-tree under repo lock)
       → gate pre-run (already-green ⇒ pass proves nothing; identical-output ⇒ gate_suspect)
       → loop ≤ max_iterations:
            invoke executor (profile argv)
            → self-test prefilter (F7, advisory — see C8)
            → gate
            → on red: feed real error text (+ prefilter output) back, resume session
       → guards: spec revert, blast radius, new-public-surface scan
    2. RECEIPT: v1 verdict format (frozen) + new lines per C2. NOTES carries what the
       builder flagged — a lead, never a mechanism.
       Post-verdict, async: graph refresh (F5), refs scan (F6), run log (F8).

## 5. Cross-module contracts (single ownership — pinned here)

**C1 — Profile** (produced by `qd.profiles`, consumed by invoke/engine/server/runlog/
bootstrap):

    {"name": str, "argv": [str],            # template; MUST contain "{task}"
     "env": {str: str},                     # merged over os.environ at launch
     "settings_overlay": dict | null,       # PROBED (6a-6d): -m flag and OPENAI_* env
                                            # are silently ignored under settings v4;
                                            # only QWEN_CODE_SYSTEM_SETTINGS_PATH with a
                                            # COMPLETE modelProviders entry switches the
                                            # endpoint/model. Rendered to a temp file at
                                            # invoke; its path exported via env.
     "price_in_per_mtok": num, "price_out_per_mtok": num,
     "endpoint": str,                       # names a C7 endpoint — the shared scarce
                                            # resource (one GPU/proxy/provider account)
     "rules_file": str,                     # default "QWEN.md"
     "altitude": "lld" | "hld",             # relayed to skill; server never interprets
     "defaults": {"workers": int, "max_iterations": int, "timeout": int}}

**C2 — Receipt grammar additions** (produced by `qd.verdict`, relayed verbatim by the
skill). *Amended by R2 (PLAN-v3-l5): the v1-frozen clause is retired — a clean green
receipt is compact (per-attempt trail, CONTEXT, TIME, TOOLS, CONTINUE, NEXT render only
on non-success or flags); red/flagged receipts keep full diagnostics.* The C2 additions
append, in this order, each only when applicable:

    NOTES: <text ≤200 chars>
    WORKTREE: <abs path>
    MERGE: git merge --no-edit <branch> && git worktree remove <path> && git branch -d <branch>
    MERGE: CONFLICT — contract overlap with <branch>; escalate, do not force
    GRAPH: fresh @ <sha7> | stale (<n> files since <sha7>) — refresh running
           | indexing | failed: <reason> | none — run graphify once to index
    REFS: <n> saved (<comma-joined names>)
    COST: $<usd 4dp> (<profile name>)       # emitted only when cost > 0

*Amended by v0.5 (2026-07-27, the field-report round): the receipt is read by the most
expensive model in the system, so it is written like a prompt, not a log.* New BODY
lines (fixed position, before the droppable C2 region), each only when applicable:

    RUN: <n> attempt(s) · peak <p>% ctx · <s>s · <n> out · <n> denied · <n> strays
                                            # every receipt, green included; a part is
                                            # omitted when it would read zero/unknown
    RETRY OF: <session_id>                  # this run is a cold re-run of that session
    TEST DODGE: <path> adds <marks> -- ...  # rendered on GREEN too, never droppable
    GATE SLOW: preflight took <s>s of a <b>s verify budget -- ...     # green too
    PREFLIGHT: green pre-run, declared expected (revision gate).      # expect="green"
    REPORTED: report_dont_fix -- ...        # + "gate GREEN -- the reported problem did
                                            #   not reproduce under this gate."
    FINDINGS: <text>                        # parsed from the reply's machine-read tail
    RESULT: valid (schema) + the ```json block VERBATIM
    RESULT INVALID: <first 4 path-named errors>                      # status result_invalid
    FIXTURES: <paths> lack captured-from provenance -- ...
    SCOPE: <scope_violation paragraph>
         | changed during the run but NOT by a logged worker write (caller co-work?):
           <paths> -- reported, never reverted.
         | out-of-scope change in <paths> NOT auto-reverted -- pre-run content too large
           to snapshot ... Review and revert manually.
    SPEC CHANGED (unattributed): <paths> -- left alone rather than reverted over a
           caller's edit; gate integrity not guaranteed for this run.
    HEAD MOVED: <n> commit(s) <pre_sha> -> <head_now7> -- <attribution clause>
    SHELL APPROVAL NEEDED: <n> blocked in <k> group(s)   # grouped by deny reason, one
                                            # example per group, ≤4 groups; full list in
                                            # the run log's `blocked_commands`

`HEAD MOVED:` is the NEUTRAL attribution grammar (C10): `"caller"` (scoped mode, where
commits are hard-denied to the worker) and `"unknown"` (everywhere else) render it, and
ROLLBACK then says review `git log <pre_sha>..HEAD` first. The v1 `COMMITTED: Qwen moved
HEAD` accusation and its `git reset --hard` advice render only on positive worker
attribution (or a v1-shaped ctx with no attribution key at all).

C2-region blocks gain, in render order: `ADVISORY red: <name> — <first output line>` +
`ADVISORY: <k>/<n> green[, <m> skipped (malformed)]` (the FIRST block; non-droppable
while any gate is red), `STRAYS:` (after NOTES), then `LEDGER:` and `RESUME:` as the
last two. `WORKTREE:` gains the dirty-main caveat (`(main tree had
uncommitted changes at branch time -- they are NOT in this worktree)`). `GRAPH:` says
"refresh running" only when one is actually scheduled (in-tree green run) and gains
` · used <n>x this run` counted from the C10 allow-log. `BURN:` gains a wall-clock
suffix and, on a caching endpoint, binds HEAVY on `prompt − cached` with a cache note
instead of the resend-the-context prose.

    STRAYS: <n> file(s) not named in the task: <paths> -- worker debris; review or rm.
    LEDGER: run #<n> · lifetime <ok> ok / <red> red / <stopped> stopped
            · peak-ctx record <p>%
    RESUME: session_id=<sid> -- a follow-up in this warm session costs a sentence ...
          | not recommended — <n> failed attempt(s) in this session carry their
            confusion forward; re-delegate COLD ... (or retry_of=<sid>).

`RESUME:` is three-way at one priority: the warm line for healthy statuses
(`success`, `success_but_preflight_passed`, `unverified`, `reported`) *and* for any run
with blocked shell commands (the approval loop only works in the same session); the
cold line for every other red; nothing at all on `stopped` / `compaction_refused` /
`error` / `refused`.

Two gate refusals are receipts of their own rather than C2 lines — `GATE UNUSABLE:`
(pre-flight timed out, raised BEFORE any attempt is burned) and `GATE VACUOUS:`
(`preflight_expect="red"` and the gate already passes) — rendered as
`STATUS: refused\n\n<text>` by the engine, which releases any worktree first.

**Cap (N1), amended enforcement order.** Drop droppable C2 blocks by reverse priority
(RESUME 7 → LEDGER 6 → BURN 5 → COST 4 → REFS 3 → GRAPH 2 → STRAYS / all-green
ADVISORY 1 → NOTES 0; on the tie at 1 the stable sort drops the block appended first,
the all-green ADVISORY), THEN truncate the `--- qwen result ---` tail (floor 200),
THEN the final-verify tail (floor 400). WORKTREE/MERGE and a red ADVISORY are never
droppable. The old enforcement stopped at the C2 drops, so body + tails alone could
blow 3,000 chars — 4 of 18 receipts on this repo's own ledger did (max 4,721).

**C3 — Engine→verdict seam:** engine hands verdict a ctx dict with exactly these v2
keys added to the v1 ctx: `notes: str`, `worktree: {path,branch}|None`,
`merge: "clean"|"conflict"|None`, `graph_line: str|None`, `refs_added: [str]`,
`cost_usd: float`, `trust: "verified"|"self"`. Internals of ctx beyond the seam are
engine's.

*Amended by v0.5 (2026-07-27): the engine hands over FACTS, not a tree to re-read, and
the status it hands over is FINAL.* Added keys:

    work_cwd: str                  # the tree the run actually used (worktree or main)
    tree_facts: {post_status, changed, numstat, head_moved, head_now, pubs} | None
                                   # captured from work_cwd BEFORE any worktree
                                   # commit/release. None => verdict recomputes from
                                   # ctx["cwd"] -- the pinned v1 fallback the
                                   # differential oracle depends on
    meta: {blocked, writes, allowed, ...}   # all three ACCUMULATE across attempts
                                   # (order-preserving dedupe); each QGATE log is
                                   # fresh per attempt, so binding the last one alone
                                   # dropped earlier evidence
    writes: [repo-relative path]   # C10 positively-attributed worker writes
    attribution: "hook" | "none"
    scope_unattributed: [path]     # changed, no logged worker write -> reported
    spec_unattributed: [path]      # spec changed, unattributed -> never reverted
    unrestorable: [path]           # over the snapshot cap, not auto-reverted
    strays: [path]                 # created, attributed, unnamed in the task
    dodge: {path: [marker]}        # added skip/xfail/expectedFailure in test-ish files
    findings: str | None           # extracted pre-truncation
    advisory: [{name, ok, ms, head}] | absent      # absent unless gates were supplied
    advisory_skipped: int          # malformed gate entries, counted not raised on
    gate_ms: int, gate_slow: bool, verify_timeout_sec: int, preflight_expect: str
    report: bool, fixtures_unproven: [path], report_gate_green: bool
    result_json: str|None, result_errors: [str]
    retry_of: str|None, run_id: str?, chain: {pos, of}?
    head_moved_attribution: "caller" | "unknown"   # absent = v1 accusation

**Status arrives final.** The `success_but_preflight_passed` demotion (U3.2, decision 4)
happens in the engine, so chains, the run log and every server-side consumer read the
same status the receipt shows. `qd/verdict.py` keeps a *conditioned* render-time
demotion — idempotent for engine callers, intact for direct `render()` callers (the
verdict_spec oracle) — and skips it entirely under `preflight_expect="green"`.

**C4 — graph sidecar** `.qwen-delegate/graph.json`:
`{"indexed_sha": str, "ts": iso8601, "status": "fresh"|"indexing"|"failed",
"reason": str?}`.

**C5 — run-log v2 record:** v1 record (see `specs/runlog_spec.py`) + `executor: str`,
`cost_usd: float`, `worktree: str?`, `branch: str?`, `merge: str?`,
`graph_refresh: {files: int, seconds: num, status: str}?`.

*Amended by v0.5 (2026-07-27): the log finally has readers — `ledger_summary()` renders
the LEDGER line from it, and `runs_in_flight()` answers "did my run die with the
session".* The `extra` map gains, always: `blocked_commands: [str] (≤50)` (the full list
the grouped receipt line elides), `graph_used: int`, `writes_attributed: int`,
`caller_changed: int`, `strays: int`. Conditionally: `run_id: str` (a submitted run),
`retry_of: str`, `verify_timeout_sec` / `preflight_expect` **only when non-default**
(a key that reads 300/"any" in every record hides the runs where somebody turned a
knob), `advisory: {red: int, of: int}` when gates were supplied, `report: bool` +
`findings: bool` on a report run, and `chain: {pos, of, halted?}` — `halted` is derived
from the link's own status, never passed back onto a receipt already rendered.

**The running-record pair (U5.2).** A submit writes an OPEN record at spawn —
`{tool, status: "running", run_id, pid, cwd, ts}` — and the completion record carries
the same `run_id`, which is what closes it. Nothing rewrites the open line: a reader
pairs by `run_id` and marks a run dead when its `pid` is gone (`os.kill(pid, 0)`; EPERM
counts as alive), because daemon threads die with the MCP process. `ledger_summary()`
SKIPS `running` records — counted, they inflated the lifetime total and filed every live
run in the red bucket. A `batch` whose items span repos logs its completions into other
trees and so leaves its marker open until the process ends: accepted, the marker still
answers the question it exists for.

**C6 — naming:** run-id = `r` + 6 lowercase hex; branch `qwen/<run-id>`; worktree dir
`~/.qwen-delegate/worktrees/<project-slug>/<run-id>/`; builder self-tests match
`*_qwen.*`; refs `.qwen-delegate/refs/<slug>.md` (source URL on line 1).

**C7 — executors file** `~/.qwen-delegate/executors.json`:
`{"default": str, "endpoints": {name: {"parallel_max": int >= 1}}, "profiles":
{name: Profile}}`. An **endpoint is the concurrency domain**: several profiles (model
or context-size variants served by the same GPU/proxy) may share one, and its semaphore
serializes across all of them — which also prevents model-swap reloads. Resolution
precedence: call arg > project `.qwen-delegate.json "executor"` > this file's
`default` > builtin `qwen-local` (which renders today's invocation byte-for-byte — the
regression pin — on an implicit endpoint with `parallel_max: 1`).

**C8 — prefilter semantics** (F7): after each invoke, if a test command is known
(bootstrap detection) and changed files match `*_qwen.*`, run the test command on those
files. **Advisory only:** gate red → prefilter output is appended to the retry feedback;
gate green + prefilter red → one NOTES clause (`self-tests failing`); never affects
STATUS. Rationale: self-tests catch the builder's mistakes cheaply, but code and
self-tests encode the same misunderstanding — only the gate is evidence. A broken
self-test must not doom-loop the run (measured failure mode), hence advisory.

**C9 — tool input additions** (`qwen_delegate`): `worktree: "auto"|"off"` (default
"off"), `executor: str`, `trust: str` (accepted: `"verified"` — caller's `verify` is the gate —
or `"self"` — R3: verify optional, server generates a non-vacuous own-suite gate with
the incremental ratchet, rewritten each attempt; anything else → refusal naming both;
L1–L4 remain parked), and `batch: [{task, verify, ...}]` — N
independent delegation items fanned across worktrees *inside* the server (worktree
implied "auto", per-item receipts, endpoint semaphore caps concurrency). Batch is the
**primary fan-out mechanism**. Probe 5 (3 regimes): MCP dispatch serializes per
*agent loop* — one agent's parallel calls serialize even across different servers,
but N subagents multiplex concurrently over one shared connection. So: batch for
overhead-free fan-out, N thin manager subagents as the proven client-side alternative;
never rely on single-loop parallel tool_use.

*Amended by v0.5 (2026-07-27).* **The behavior flip of the round: `qwen_delegate` is
ASYNCHRONOUS by default.** A call SUBMITS and answers in milliseconds with
`STATUS: submitted` + `RUN:` + `RECEIPT:` (+ `PARTIAL:` for chain/batch) + `HEARTBEAT:`
+ `WATCH:`; the receipt lands as a file under `.qwen-delegate/receipts/<run-id>.md`
(temp + rename, so its existence means it is complete). `wait: true` restores the old
blocking call byte-for-byte. `qwen_query` stays synchronous — the answer *is* the
deliverable and arrives in a minute or two. Additive-optional params added this round:

    verify_timeout_sec: int        # arg > project/machine config > 300, clamped 10..3600
    preflight_expect: "red"|"green"|"any"   # unrecognised falls back to "any"
    advisory_gates: [{name, cmd}]  # never touch STATUS, never reach the worker
    chain: [item]                  # dependent, serial, halts on the first non-green;
                                   # mutually exclusive with `batch` (refused by name)
    report_dont_fix: bool          # one attempt, one gate run, status "reported"
    fixture_provenance: bool       # opt-in, default off until probe P7
    result_schema: object          # a non-object is treated as absent, never refused
    wait: bool                     # block instead of submit
    retry_of: str                  # replay a stored brief COLD with a correction
    retry_message: str

Project keys that back call args (U5.6 recipe defaults; a call arg always wins):
`approval_mode`, `shell_allow`, `timeout_sec`, `preflight_expect`,
`verify_timeout_sec`, `fixture_globs`, plus `task_suffix` (appended to the task itself,
so it rides compaction re-injection), `store_briefs: false` (opt out of U5.5 briefs) and
`autoedit_via_hook` (dark until probe P1).

**Additive-evolution clause** (pinned in the `qd/schemas.py` docstring): existing names,
enums and required lists never change — a caller's working call must keep working. New
params are OPTIONAL only, and each lands with a spec proving that its ABSENCE leaves
behavior and receipt identical. Descriptions may change freely; shapes may not.

**C10 — attribution evidence** (produced by `scoped_hook.py`, read by `qd.invoke`,
relayed by the engine): the hook appends one resolved path per ALLOWED write to
`QGATE_WRITELOG` and one line per allowed shell command (plus `ungated:<tool>` for an
allowed unknown tool) to `QGATE_ALLOWLOG`, beside the pre-existing `QGATE_DENYLOG`;
`QGATE_MODE` is `"scoped"` or `"autoedit"`. `qd/invoke.py` reads all three into
`meta["writes"]`/`meta["allowed"]`/`meta["blocked"]`; the engine accumulates them across
attempts and re-expresses writes repo-relative (`ctx["writes"]`, `ctx["attribution"]`).

*Policy — only positively-attributed writes are ever undone.* With a channel active
(`scoped`, or `auto-edit` under `autoedit_via_hook`), touch-scope and spec violations
are intersected with `ctx["writes"]`; the unattributed remainder is REPORTED (`SCOPE:` /
`SPEC CHANGED (unattributed):`), never reverted and never a failed attempt. Caller
co-work during a live run is recorded (`caller_changed` in the run log), never rolled
back. With no channel, the receipt says attribution is unknown rather than accusing —
and `git reset --hard` advice renders only under positive worker attribution.

**C11 — heartbeat sidecar** `.qwen-delegate/progress.json`, written by `qd.limits.Progress`
as the executor streams:

    {"session": str|null, "records": int, "input_tokens": int, "last_type": str|null,
     "updated": iso8601, "attempt": int, "state": "running"|"done"}

Callers poll the file; no MCP surface changes. Two conditions are load-bearing: it is
wired ONLY alongside a burn budget (`limits.compose(burn, progress)`; any `on_line`
switches the executor to stream-json, and the streaming adapter emits no `stats`, so a
heartbeat on a `burn_budget: 0` run would silently cost it the tool counts), and it is
written into the tree the run USES (`work_cwd`), behind `runlog_dir`'s self-ignoring
`.gitignore`, while a submit response names the submit cwd's path — the two differ on a
worktree run. `finish()` writes the terminal snapshot, or a poller cannot tell an ended
run from a wedged one.

## 6. Concurrency model

One reader thread (parse + lifecycle requests); one worker thread per `tools/call`;
`respond()` behind a global write lock, whole-line writes. Per-repo mutex for *in-tree*
delegations ("one actor per tree", structurally); worktree runs skip it; queries never
lock. Concurrency caps are **per-endpoint semaphores** (C7): every profile names its
endpoint, and the endpoint's `parallel_max` is the cap — a local GPU at 1–2 never
throttles an API provider at 20, and two profiles sharing one GPU (model or
context-size variants) serialize against each other, which also prevents model-swap
reloads. The semaphore is held around the **entire executor subprocess** — a whole
delegation or query, never an individual HTTP request — so on a single-slot endpoint
two sessions' turns are never interleaved and the KV cache survives by construction;
no priority logic is needed in the proxy. It gates read-only queries too: an
interleaved query would evict a running build's cache and force full re-prefill each
turn (probe 4). Queueing behind the build is cache-correct scheduling, not a
limitation. For API endpoints the cache question is economic, not temporal — stable
prompt prefixes earn provider cache discounts; the run log's cost field captures it.
Locked shared state: run-log file append, registry append, worktree table. Everything
else per-call.

*Amended by v0.5 (2026-07-27) — a submit is an ENQUEUE.* `qwen_delegate` takes no locks
in the tool call: the endpoint semaphore and the repo lock are acquired INSIDE the
background daemon thread, so the queue is as real as it ever was, it just no longer runs
down the caller's clock. Two consequences. (1) The guard skip is a property of the
HANDLER, not of the tool name — `_run_call` skips `_guards_for` only for a handler
carrying the `@self_guarded` marker, so any other handler registered under that name (a
test double, a future synchronous tool) keeps the guards it always had. (2) The same
move killed a latent self-deadlock: a `batch` used to hold the single endpoint slot for
the whole call while every item asked for it again. Chain and batch take their guards
per link and release them before the next, so a chain holds one slot at a time, not one
for its whole length. Results are files, not responses: every path through the
background body ends in a receipt (raises included), or the `WATCH:` loop handed to the
caller polls forever. Daemon threads die with the process — an MCP server whose session
ends takes its in-flight runs with it, which is what the `running`-record pid check
(C5) exists to report.

## 7. Build method — the plugin builds itself

Specs (from LLD.md) are committed before any module. The current server runs every
build delegation until cutover (N4). Leaf-first:

| # | milestone | units | builder |
|---|---|---|---|
| M0 | probes (§8) | — | manager + human |
| M1 | leaves | profiles, runlog, refs, gittree (port) | Qwen |
| M2 | engine ports | invoke, verdict, bootstrap, queries, engine (+prefilter) | Qwen; Claude reviews seams |
| M3 | dispatch | server.py | Claude (races under-prove on gates) |
| M4 | worktrees | worktrees + engine fan-out | git plumbing Qwen; wiring Claude |
| M5 | graph | graph | Qwen (post-probe) |
| M6 | cutover | old internals deleted; `.mcp.json` unchanged | Claude |
| M7 | Claude-side | skill, thin manager, tool descriptions; 3-arm benchmark | Claude |

Cutover gate: all `specs/*` green against the new entry **and** live end-to-end flows
(delegate, query, best-of-N, bootstrap-on-fresh-repo, scoped) through Claude Code.

## 8. Probes (M0 — no code until answered)

> **Answered 2026-07-22 — results in [archive/PROBES-M0.md](archive/PROBES-M0.md).** Headlines: ollama
> backend works (free index confirmed, `graphifyy[ollama]` extra required); structural
> re-index 1.9s repo-wide; `built_at_commit` recorded natively; client serializes MCP
> dispatch → `batch` param (C9) is the fan-out mechanism; endpoint/model override only
> via full settings overlay (C1).

1. graphify semantic backend accepts the local Qwen endpoint.
2. graphify incremental API shape (file list? auto-diff? records a sha?) → `graphify_cmd`.
3. `graphify query` retrieval quality on a real repo for change-shaped questions.
4. ~~Ollama concurrency~~ — **settled by decision, not measurement** (user, 2026-07-22):
   Ollama stays at 1 worker; the local endpoint's `parallel_max` is 1, a configured
   fact mirroring the backend. Do NOT test parallel Ollama workers. The design carries
   parallelism anyway (per-endpoint caps, worktrees, fan-out), so enabling it later is
   config only: raise `OLLAMA_NUM_PARALLEL` + the endpoint's `parallel_max`, after the
   run log's peak-context column confirms real tasks fit the halved (96k) window.
   Standing facts, no probe needed: a different queued conversation on one slot evicts
   the KV cache (full re-prefill) — the whole-subprocess semaphore already prevents it;
   different *models* on one Ollama force full reloads — heterogeneous executors
   serialize on the shared endpoint, always.
5. Does Claude Code run two >120s MCP calls to one stdio server concurrently? If
   serialized: fan-out falls back to N thin manager subagents (works today).
6. Qwen Code config precedence: do endpoint/model env vars override
   `~/.qwen/settings.json` per process? If not, inject a per-run temp settings file via
   `QWEN_CODE_SYSTEM_SETTINGS_PATH` — the mechanism the scoped hook already proved.
   Either way the user's own Qwen config is never edited.

## 9. Risks

stdout interleaving (write lock, spec'd under load — the one bug that corrupts every
result) · port drift (ported spec suite runs against old and new before cutover) ·
client serialization (probe 5; degrades, doesn't break) · graphify unknowns (isolated
behind `graphify_cmd`; worst case background full re-index) · worktrees vs uncommitted
work (refuse unborn HEAD, warn dirty tree) · Ollama contention (parallel_max is a
configured fact mirroring the backend's worker count — currently 1; raised only with
the hardware, never assumed).
