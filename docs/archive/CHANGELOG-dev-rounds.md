# Changelog

## 0.5.0-dev — the field-report round

Forty-five real delegations produced a field report: seven gotchas and ten wishes,
ranked by what each one cost the caller. Every gotcha was confirmed in the code, and
looking for them turned up several bugs nobody had reported — two of which destroyed
work. One principle organises the whole round: **the receipt is read by the most
expensive model in the system, so it should be optimized like a prompt, not written
like a log.**

Two commits: phases 0–4 (safety, receipt diet, attribution, gate hygiene, features) and
phase 5 (async by default, result contracts, `retry_of`, a self-updating capability
map). The local endpoint was busy throughout, so all of it was built against the
hermetic spec harness — 706 tests — and the eight things that need a live worker are
named at the end and ship dark until they are answered. Version stays 0.4.1 until
release; this section is `dev` because the bump happens in the release PR.

### A revert could destroy uncommitted work that was never the worker's

The out-of-scope revert ran `git checkout <sha> -- <path>`, which does two wrong things
at once: it restores what HEAD holds — not what the tree held before the run — and it
**stages** the result, so the destruction does not show in `git diff`. A caller's
uncommitted fix, sitting in a file the worker then touched, was overwritten and the
evidence tidied away.

Reverts now restore bytes. Every dirty file is copied at T0 (8 MB per file, 64 MB per
run) and restored from that copy; T0-clean files come back from the commit. The index is
never touched. Files over the cap are **reported and left alone** — `SCOPE: ... NOT
auto-reverted` — because guessing at content is worse than saying so.

The same guard also had its tense wrong: violations were classified against files
tracked *now*, so a worker that created a file and `git add`ed it turned its own new
file into a "violation" whose revert then silently did nothing. It now asks what was
tracked at the pre-run sha.

### `touch_scope` was silently unenforced without a gate

The loop broke out on "no verify command" *before* it checked the scope, so a run
without a gate promised a blast radius it never enforced. The order is fixed, and a
scope violation on the final attempt now reports `scope_violation` instead of
`verify_failed` — it was reporting the wrong failure to the one reader who has to decide
what to do about it.

### The receipt stopped accusing the worker of the caller's work

`COMMITTED: Qwen moved HEAD during this run. It was told not to.` fired whenever HEAD
moved for any reason — including the human committing in another terminal, roughly eight
times per project in the field, each one an investigation that found nothing. Worse, it
recommended `git reset --hard`.

There is now an attribution channel: the PreToolUse hook logs every write it allows and
every shell command it allows, and the engine reads those logs back. With a channel
active, **only positively attributed writes are ever reverted**. Files that changed
during the run with no logged worker write are reported as co-work — `SCOPE: changed
during the run but NOT by a logged worker write ... reported, never reverted` — and a
changed spec file with no worker write is left alone with a gate-integrity caveat rather
than reverted over somebody's edit. `HEAD MOVED:` is neutral about who did it (in
`scoped` mode it knows the worker *can't* have: commits are hard-denied), and
`reset --hard` advice appears only under positive worker evidence.

### Receipts that tell the truth about themselves

- **The 3,000-character cap was not enforced.** It dropped optional blocks and then gave
  up; 4 of 18 receipts in this repo's own log exceeded it, the largest at 4,721. It now
  drops blocks by a pinned priority, then trims the worker's result, then the verify
  output — with floors, so the two things worth reading survive.
- **`GRAPH:` said "refresh running" when nothing was going to refresh** — on failed runs
  and worktree runs, which is most of the ones you would check it on. It says so only
  when a refresh is actually scheduled, and now also reports ` · used <n>x this run`, so
  a graph the worker never consulted is visible instead of invisible.
- **The tree facts came from the wrong tree.** The renderer re-read the *main* repo even
  for worktree runs, and did it after the work had been committed or the worktree
  deleted. The engine captures them from the tree the run actually used, at the moment
  before that happens.
- **12 near-identical `SHELL APPROVAL NEEDED` lines** (~40% of a red receipt) became
  `n blocked in k group(s)` with one example per reason; the full list moved to the run
  log.
- **New fixed line: `RUN:`** — attempts, peak context, wall time, output tokens,
  denials, strays. The numbers a caller used to reassemble from four sections.
- **`BURN:` gained time and cache sense.** On a local endpoint the money is always
  $0.0000, so the cost axis is GPU minutes; and on a caching endpoint the HEAVY warning
  now binds on the un-cached remainder, because a raw multi-million input count was
  sending callers to diagnose perfectly healthy runs.
- **`LEDGER:`** finally reads the run log nothing had ever read: run number, lifetime
  ok/red/stopped, peak-context record.
- **`RESUME:`** — session resume has existed all along and all 45 field delegations
  started cold, because nothing ever mentioned it. The line now says so on a healthy
  run, and says the opposite on a failed one: a session that failed carries its
  confusion forward, so the repair path is a cold re-run, not an argument.

### The gate is a thing that can fail too

- **`verify_timeout_sec`** (default 300, clamped 10..3600) replaces a bare literal, and
  a pre-flight that times out **refuses the run before any attempt is burned**
  (`GATE UNUSABLE`). The field case: a gate that hit the network timed out before and
  after the run, and a perfectly good delivery was filed as `gate_suspect`. The timeout
  is now its own return value rather than something inferred from the output text, so a
  gate that *prints* the word cannot refuse its own run.
- **`preflight_expect`** says what the gate should say beforehand. `"red"` (greenfield)
  refuses a gate that already passes — it cannot prove the work happened. `"green"`
  (revision work on a passing suite) stops `success_but_preflight_passed` crying wolf on
  every task whose suite was green by premise. That demotion also moved into the engine,
  so chains and the run log see the same status the receipt shows.
- **`GATE SLOW:`** when the pre-flight eats more than half its budget — rendered on
  green too, because the run that passed is the cheapest one to fix it on.
- **`advisory_gates`** are loose checks (conformance, placement, design drift) that run
  once at the end and *indicate* rather than gate: they never touch `STATUS`, never
  enter a retry prompt, and never reach the worker. Red ones sit at the top of the
  receipt and cannot be dropped by the cap — an indicator whose absence reads as green
  is worse than no indicator.

### Work that isn't "make this command pass"

- **`chain`** runs dependent steps in one call, in order, on the same tree, halting at
  the first link that does not come back green (the rest return one-line `SKIPPED`
  receipts). `batch` remains the independent one; sending both is refused by name.
- **`report_dont_fix`** buys a diagnosis instead of a repair: one attempt, one gate run,
  status `reported`, and a `FINDINGS:` line from the worker. A red gate is the
  deliverable — it is the reproduction.
- **`TEST DODGE:`** flags a `skip` / `xfail` / `expectedFailure` added to the very tests
  being delivered. It renders on green and cannot be dropped, because that is exactly
  the case where an honest red and a quiet green look identical.
- **`STRAYS:`** names files the run created that the task never mentioned — the scratch
  script, the second copy of a module, the debug dump.
- **`fixture_provenance`** (opt-in) requires created fixtures to carry a
  `captured-from:` line, or a `.src` sidecar for binaries. Imagined fixtures were the
  field report's worst defect class, and a gate written against invented bytes passes
  forever.

### Delegating no longer means waiting

`qwen_delegate` **submits**. The call comes back in milliseconds with a run id, the path
its receipt will land at, a heartbeat file and a shell one-liner that waits for it; the
delegation continues on a background thread and files the receipt when it is done.
Blocking for minutes bought the caller nothing it could not get from a file and cost it
everything it could have done meanwhile. `wait: true` restores the old call byte for
byte, and `qwen_query` was left synchronous — there, the answer *is* the deliverable.

Consequences worth knowing:

- Refusals that can be decided from the call alone — a bad `trust`, a dirty spec file, a
  `retry_of` with no stored brief — are answered in the response, not filed as a receipt
  nobody is waiting on yet. Anything that costs real time (the pre-flight gate) is part
  of the run and lands in the receipt.
- A chain or batch writes a `.partial.md` beside its receipt, rewritten as each link
  lands, so an eight-step chain can be read while it runs. (Its links are prechecked
  individually as they run, so their refusals arrive as receipts; only a lone
  delegation's preconditions are answered in the submit response.)
- A submit takes no locks; the endpoint and repo guards are acquired inside the
  background thread, which makes a submit an enqueue — and incidentally fixed a latent
  self-deadlock where a batch held the single endpoint slot while each of its items
  asked for it again.
- The run log gains an open `running` record with the owning pid. Background threads die
  with the process, so a run whose pid is gone died with its session — that is now a
  question the log can answer instead of a mystery.
- `.qwen-delegate/progress.json` gets a live snapshot (records, input tokens, attempt,
  state) while the run streams, so "is it hung?" costs a file read rather than a turn.

### Getting a value back, and correcting a run without retyping it

- **`result_schema`** asks for a shape, not prose: the worker is told to end with a
  fenced JSON block, a small validator checks it against a subset of JSON Schema
  (type/required/properties/items/enum) and feeds every violation back by path like a
  failed gate. The conforming block is rendered verbatim in the receipt body — never in
  the droppable region, never in the truncated tail. It is on `qwen_query` too, where
  there is no retry loop, so the check is simply reported.
- **`retry_of`** re-runs an earlier delegation's stored brief with `retry_message`
  appended as a correction, and runs it **cold** on purpose. Briefs live in
  `.qwen-delegate/briefs/`; `"store_briefs": false` opts out. Corrections stack across
  retries, and the stored task never accumulates the project's standing suffix.
- **Recipe defaults.** `.qwen-delegate.json` can now set `approval_mode`, `shell_allow`,
  `timeout_sec`, `preflight_expect` and `verify_timeout_sec` once instead of every call
  repeating them (a call argument always wins), plus **`task_suffix`** — the standing
  worker discipline, appended to the task server-side, where it survives a compaction
  that would have eaten it out of `QWEN.md`.

### How a capability reaches the agent that would use it

Not through this file. A changelog is read by people; the agents doing the delegating
learn what the plugin can do from two places only — the managed block in a project's
`CLAUDE.md` and the receipt itself. So that block is now **managed**: version-stamped,
rewritten in place from the template once per plugin version, byte-identical no-op when
current, and a strict no-op when the file or its markers are absent. The rule the round
adopted: every new capability lands either as one line in that block or as a receipt
affordance (the way `RESUME:` did) — never only in long-form docs, which is how a
feature stays undiscovered for 45 delegations.

### Known gaps

- Eight live probes are deferred until the endpoint is free, and the features that
  depend on them ship dark: attribution outside `scoped` mode (`autoedit_via_hook`,
  off), fixture provenance (opt-in), the cached-token branch of `BURN`, and the
  heartbeat's write cadence. `docs/PENDING.md` lists all eight with what each gates.
- Delete handling — `allow_delete` and stray auto-clean — is designed and **not built**:
  strays are reported only. A delete parser guessing at command phrasings is the one bug
  class with no rollback, so it waits for the probe.
- `workers` (best-of-N) is still advertised in the schema and the skill and still not
  implemented. Untouched deliberately: closing it is a build or a deletion, not a
  documentation pass.
- The heartbeat is written only when a burn budget is live (the default), because any
  streaming callback costs the run its richer batch-mode statistics — the same 0.4.0
  trade, unresolved.
- A submitted run is a thread of this MCP server, so ending the session kills it. The
  log reports that honestly; a detached runner that survives is not built.

## 0.4.1 — the other truncation

0.4.0 opened with "a day of debugging one truncated query" and fixed the
executor's side of it: a truncated run now says so instead of arriving as an
empty success. It did not find the second cap, which was ours.

`qwen_query` capped its answer at **4,500 characters** — `RESULT_CAP = 3000`
plus an undocumented `+ 1500` at the call site — and then handed the caller a
fluent paragraph with a truncation marker at the end of it. Measured across the
six logged queries in this repo, four came back at exactly 4,693/4,694 chars
against 4k–19k completion tokens actually generated. The answers were being cut
mid-sentence and the caller had no reason to doubt them.

The cap is now **50,000**, and the constant is the whole of it — the `+ 1500`
is gone, so `RESULT_CAP` describes what the caller gets. It exists to stop a
runaway, not to budget context: for a query the answer *is* the deliverable,
unlike a delegate receipt where the diff is the deliverable and the text is
commentary (`verdict.py` keeps its own, smaller cap for that reason).
`format='map'` was the worst affected — MAP + KEY SYMBOLS + CONNECTIONS +
ANSWER + VERIFY for any real repo does not fit in 4,500 chars, so the
orient-yourself format was the one most reliably truncated.

Worth being explicit about what this was *not*, since the first guess was wrong
in an instructive way: not plan mode, which imposes no output limit, and not
qwen-code's client-side `max_tokens` — the cap 0.4.0 documented. Peak context on
those runs was 36k–40k against a 262k window, nowhere near compaction. Every
suspect outside the repo was innocent.

`specs/queries_spec.py` never asserted on truncation, which is why a cap this
tight survived a release *about* truncation. It does now: a large answer must
survive intact, the effective cap must equal `RESULT_CAP` with no slack at the
call site, and the constant must stay large enough for a map.

## 0.4.0 — the executor tells the truth

A day of debugging one truncated query turned into a run of silent-failure fixes.
The theme: several guards reported *success* or *zero* when they had actually
measured nothing. A metric reading 0 because it never ran looks identical to a
clean result, which is the failure class this whole system exists to catch.

### A failed run no longer reports as an empty success

qwen's error record carries `is_error` and `error.message` and **no `result`
field**. The parser read it like a success, so `m.get("result") or ""` yielded
`""` and the run arrived as `STATUS: ok` with a blank answer — with the
executor's own account of what went wrong dropped on the floor. That is what
sends a fresh session hunting for a bug that is not in the repo.

Failures now carry the executor's message. When it names truncation, the receipt
also names the cause, because the cause is genuinely counter-intuitive:

**The output cap is client-side.** qwen-code sends `max_tokens` itself, and picks
it by looking the model up in a table after normalising the name — a normaliser
that keeps only the part after the last `:`. An Ollama tag like
`qwen3.6:27b-agent` therefore reads as `27b-agent`, matches nothing, and takes
the generic 32,000 default instead of its family's 64,000. Thinking tokens count
against the same budget. **Nothing on the inference server shows this**, which is
why "there is no output cap set" and "the reply was truncated" were both true at
once.

### Runs are visible while they cost money

- **`BURN:`** on every receipt — input/output tokens, calls, context per call.
  On a local endpoint `COST` is `$0.0000` whatever happens, so a run that made
  218 calls averaging 87k of context looked exactly like one that made four.
  Output tokens become prompt tokens for every later turn, so a long agentic loop
  costs roughly the square of its length: measured, 19M tokens for a 67-line file.
- **Two live limits, on by default.** A **spend** ceiling (10M cumulative input
  tokens) and a **silence** ceiling. Both are deliberately loose: a limit that
  fires on legitimate work is worse than no limit, because the first false
  positive is the one that gets it switched off.
- **`STATUS: stopped`** when either fires, with a receipt saying *we* ended the
  run — nothing verified, work on disk partial, not a defect in the worker or the
  code. It does not retry; the same task into the same ceiling spends it twice.

The silence budget is derived from `decode_tps`, not stated in seconds. Without
per-token records a record arrives per *message*, so one long generation is
legitimately silent for `max output ÷ decode rate` — ~1,830s at 70 tok/s, ~7,530s
at 17 tok/s on the same output. No fixed default serves both. (The first attempt
used a fixed 1800s, which was already under the *fast* case.)

### Compaction is refused rather than survived

Compaction is the documented fabrication trigger, so `on_compaction` now defaults
to **`refuse`**: the run stops on the attempt it happens, its output is discarded
before reaching a gate or a spec check, and the receipt says to split the task.
`reinject` and `discard` remain for anyone who wants them.

The PreCompact hook also exits 2, which qwen documents as "block compaction" —
best effort only, since the auto-compaction call site reads just the hook's
additional context. The stop is the mechanism that holds.

**Auto-compaction cannot be turned off.** The threshold is a fraction of the
window with a 0.01 floor, and a hardcoded reserve of 33,000 tokens (20,000 to
write the summary, 13,000 buffer) is subtracted before any threshold applies. So
the latest a compaction can fire is `window − 33,000`, whatever you configure.

### One job at a time, across sessions

The endpoint slot was a per-process semaphore, and every Claude session runs its
own MCP server — so N sessions each saw a free slot and fired at one GPU
together. It is now a lock file, so the slot count holds machine-wide. Concurrent
requests do not each get a private context; on Ollama the loaded context is split
across parallel slots, which is itself a truncation cause.

`dispatch: "serial"` pins an endpoint to one request whatever its `parallel_max`
says. Unset is already serial out of the box.

### Streaming, and the telemetry it nearly cost

Reading the executor's output as a stream is what makes mid-run intervention
possible at all — a blocking read means a runaway can only ever be reported
afterwards. The records are identical; only *when* they arrive changes.

But the streaming adapter's result record carries **no `stats` field at all**,
so the first streamed delegation reported 0 tokens, 0 tools, 0 lines and $0 —
silently voiding `BURN`, `COST` and any budget built on them. Two fixes: stream
only when a limit is attached (until then it buys nothing), and fall back to the
result's top-level `usage` for token totals, recorded under its own provenance so
it is never mistaken for the richer per-source split.

Also unified behind one parse path. `parse_qwen_json` had a JSONL fallback and
`peak_context`/`parse_stats` did not, so a streamed run parsed to the right answer
with zeroed telemetry.

### The machine half of the setup

Everything else in the plugin travels with the repo. `~/.qwen/settings.json` does
not, and the settings that decide whether a delegation returns whole work or a
fragment live there — which is why the same task succeeded on one machine and
returned a fragment on another.

`qd doctor` audits it: the model name qwen-code will fail to recognise, thinking
spending the same budget as the answer, and the context window every compaction
line is computed from but nothing verifies. `--fix` writes the one setting it can
determine safely, after a backup; it will not invent a window it cannot measure.
`--verified <N>` records a value read off the endpoint, and the finding returns
if the two ever stop matching.

Claude Code has no post-install hook, so a `SessionStart` hook runs it once per
plugin version and stamps the result. A version bump un-stamps it: install-and-
update semantics, silent otherwise. It never fails and says nothing unless a
finding is serious, because anything it prints is injected into the context this
plugin exists to conserve.

### Tests: location, and being run at all

- **`test_command` / `test_dir`** in `.qwen-delegate.json`. Every detector is a
  guess keyed off packaging metadata, so a project only has to be slightly unusual
  to get the wrong command or none at all. "None at all" is the bad one: the
  self-graded gate then falls back to a directory that may not exist, can never go
  green, and nothing says why. A plain `tests/` folder is now detected on its own.
- **The vacuous-pass guard counted only the first file.** A multi-file suite
  prints one count per file, so the bar was compared against a single file's
  total — it could demand more tests than any one file holds, leaving the gate
  unsatisfiable. Both counters now sum.
- **And it captured only the last command's output.** In `$(a; b 2>&1)` the
  redirect binds to `b` alone, so a compound test command lost everything the
  earlier commands wrote — which for unittest is where results go. Found by the
  test written for the fix above, which failed for an unrelated reason.
- **Worker-written tests (`*_qwen.py`) now run in the suite.** They were collected
  by nothing: they ran once as part of a delegation's gate and never again. Still
  never a *gate* — that stays a hand-written spec, because a self-graded suite can
  share the code's blindspot — but once the work is accepted they are ordinary
  regression cover.

### Fixed: the spec runner ate developer credentials

`ci/run-specs.sh` wrote a stub `~/.qwen/settings.json` and a `--global` git
identity. Harmless on a CI runner; run locally it silently overwrote a real qwen
config — API key, endpoint and all. It now runs under a throwaway `HOME`.

### Known gaps

- Tool and line counts are unavailable in streaming mode (the adapter omits
  `stats`). Tokens are recovered; those two read 0 with no way to distinguish that
  from a measured zero.
- The `usage` fallback is spec-covered but has not yet run against a real streamed
  delegation.
- The timing-sensitive dispatch spec is excluded from the automated suite because
  it flakes under load, so the cross-process locking tests are run by hand.
- `workers` (best-of-N) is advertised in the tool schema and the delegation skill
  and is not implemented in the engine. Predates this release.
- Per-token records would give sub-second stall detection and make `decode_tps`
  unnecessary; they are deliberately not requested today because they would have
  to be filtered back out of both the callback and the accumulated buffer.
