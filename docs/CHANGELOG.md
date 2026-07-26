# Changelog

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
