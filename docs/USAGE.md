# Day-to-day usage

How to actually use this while working. Every claim here is measured — sources in
[FINDINGS.md](FINDINGS.md); benchmark data in `~/projects/token-saver-eval/`.

| work shape | solo Claude | delegated | saving |
|---|---|---|---|
| greenfield product, feature by feature | $1.65 | $1.36 | −18% |
| customer bug from a symptom | $1.36 | $0.78 | −43% |
| changes to a 14k-line codebase | $6.85 | $2.15 | −69% |

Quality was identical in every benchmark (hidden acceptance + full regression, both
arms). The saving grows with how much *reading* the task would force on Claude —
delegation pays most exactly where sessions hurt most. The trade: wall-clock. The
local worker is slow; your tokens are the scarce thing, its time is not.

## Install (once per machine)

1. **Prerequisites:** the `qwen` CLI (Qwen Code) configured against your free/local
   endpoint (e.g. Ollama) — that's the worker; `python3` (the server is stdlib-only,
   nothing to pip-install); `git`. Optional: `graphify` for the code graph.
2. **Install the plugin** in Claude Code:
   `/plugin marketplace add latentlok/token-saver` then install `token-saver` from
   it. That registers everything at user scope via the plugin manifest: the two MCP
   tools (`qwen_delegate`, `qwen_query`), the skills (delegation, architect,
   lld-principles), and the qwen-manager/architect agents. No manual MCP config —
   `.mcp.json` wires the server through `${CLAUDE_PLUGIN_ROOT}`.
3. **Different/bigger worker model?** Add a profile in
   `~/.qwen-delegate/executors.json` (C7) and pass `executor=` per call — nothing
   else changes.
4. **Let it check the machine.** The first session after an install or an update
   runs `qd.doctor` once and reports only what will actually bite. Run it yourself
   with `/token-saver:doctor`, or `python3 -m qd.doctor` from the plugin directory.
   `--fix` writes the one setting it can determine safely (after a `.bak`);
   `--verified <N>` records the context window you read off the endpoint, so the
   "declared but unverified" finding stops firing until one of the two changes.

### Working from a clone instead (plugin development)

To edit the plugin itself, skip the marketplace and point Claude Code at a checkout:

    git clone https://github.com/latentlok/token-saver.git ~/projects/token-saver
    claude --plugin-dir ~/projects/token-saver

Changes apply with `/reload-plugins` in the same session; `git pull` is the update.
Don't do both — a marketplace install and a `--plugin-dir` clone load the plugin twice.

## Settings reference (`.qwen-delegate.json`)

Per project, all optional. Machine-wide defaults for the same keys go in
`~/.qwen-delegate/config.json`; the project file wins.

| key | default | what it does |
|---|---|---|
| `test_command` | detected | The exact command that runs your tests. Beats every detector — use it when your layout isn't one the detectors guess (they key off `package.json`, `Cargo.toml`, `go.mod`, `Gemfile`, a venv `pytest`, `pyproject.toml`/`setup.py`). |
| `test_dir` | `tests`/`test`/`spec`/`specs` if present | The folder holding your tests, for the discovery fallback. |
| `trust` | `self` | `self` = the worker writes and grades its own suite; `verified` = your `verify` command is the gate; `auto` = refuse a bare call so the orchestrator picks per task. |
| `min_tests` | 5 | Floor for the non-vacuous guard under `trust="self"`. Ratchets automatically against an existing green suite. |
| `spec_globs` | `specs/*`, `*_spec.*`, … | Files the worker may never edit. Its edits to them auto-revert. |
| `dispatch` | unset (already serial) | `serial` pins every endpoint to one in-flight request whatever its `parallel_max` says; `parallel` honours the declared capacity. |
| `burn_budget` | 10,000,000 | Cumulative input tokens one delegation may spend before it is stopped. `0` disables. |
| `decode_tps` | 15 | Your model's decode rate. The silence budget is derived from it and the declared max output — state the rate, not the seconds. |
| `stall_seconds` | derived | Absolute override for the silence budget, if you'd rather state the answer directly. |
| `compaction_threshold` | 1.0 | Fraction of the window at which the executor may auto-compact. Rarely the binding term — see below. |
| `compaction_at` | unset | Absolute token target for the same thing. Resolves against the reserve rather than silently configuring an unreachable number. |

### Where the tests live, and why it matters

If the plugin can't work out how to run your tests, `trust="self"` gets a gate that
can never pass, and nothing says so. `test_command` or `test_dir` fixes it in one line.

`trust="self"` also needs the worker's own tests to land somewhere your `spec_globs`
don't protect — otherwise they auto-revert as fast as it writes them. The convention
is `*_qwen.*` for worker-written tests, kept clear of the files that define correct.
They are never the gate; once the work is accepted they are ordinary regression cover.

### Live limits

Every delegation now carries two ceilings. Both only ever end a run early — neither
can turn a failing run green.

- **Spend.** Cumulative input tokens across the whole delegation (not per attempt).
  Default 10M against ~560k for a real measured delegation, so it clears ordinary work
  by an order of magnitude. Lower it once you know where your own normal sits.
- **Silence.** Time since the last record, or since launch. Derived from `decode_tps`
  because a record arrives per *message*, not per token: one long generation is
  legitimately silent for `max output ÷ decode rate`. At 70 tok/s a 128k generation is
  ~1,830s; at 17 tok/s it is ~7,530s. A fixed seconds default cannot serve both.

A run either one stops gets `STATUS: stopped` and a receipt saying **we** ended it —
nothing verified, work on disk partial, not a defect in the worker or your code. It
does not retry: the same task into the same ceiling just spends it twice.

One caveat worth knowing: a limit can only act on records it receives, which only
arrive incrementally in streaming mode. If an executor profile's argv can't be
switched to the streaming output format, the limit is inert — the receipt says so
rather than letting a guard that never watched read as a guard that found nothing.

### Compaction is refused, not survived

The worker's session compacting is the documented fabrication trigger, so the default
is to STOP rather than build on a summarised history — full detail under the trust
dial below. It cannot be disabled outright: a hardcoded reserve of 33,000 tokens is
subtracted from the window before any threshold applies, so the latest a compaction
can fire is `window − 33,000`. On a 262,144 window that is 229,144 tokens (87.4%).

## Per project (new or existing) — near zero

- **Mandatory: nothing.** The server bootstraps the worker's rules file (QWEN.md)
  on the first delegation, and refuses cleanly if the directory isn't a git repo —
  `git init` first; git is the rollback.
- Recommended: put the delegation policy block in the project's CLAUDE.md so
  delegation is the default for mechanical work, not something Claude must remember.
  Easiest: in a session inside the project, say *"add the delegation policy block
  from the plugin's CLAUDE-snippet template to this project's CLAUDE.md"* — Claude
  appends it (creating CLAUDE.md if needed) and skips if it's already there. Verify
  with `grep qwen-delegate:begin CLAUDE.md`, then commit. Manual alternative: copy
  `templates/CLAUDE-snippet.md` from `begin` to `end` marker inclusive and paste at
  the end of CLAUDE.md. You'll also be OFFERED this after the first delegation into
  a fresh project (the `SETUP:` receipt line) — just say yes.
- Existing repos you'll work in repeatedly: `graphify update . --no-cluster` (~2s,
  deterministic, no LLM) — the worker locates code via the graph instead of reading;
  the server keeps the index fresh after every delegation. For a large UNFAMILIAR
  codebase, additionally delegate a one-time semantic index (`graphify update .`
  with the local backend) as an offline job.

## The code graph (graphify) — optional, but the −69% case runs on it

`graphify` is an external code-graph tool the **worker** uses to locate code without
reading it. Fully optional: with it absent, every delegation still runs — the server
just stamps `GRAPH: failed: graphify not installed` and Qwen falls back to grep. But on
a large existing codebase it is where the biggest saving comes from, because locating is
the expensive part and the graph turns a read into a lookup.

**Turn on automatic worker-side graph use — three steps, then it's hands-off:**

1. **Install** graphify once per machine (below).
2. **Index** the repo once — `graphify update . --no-cluster`. The server re-indexes
   after every delegation from then on, so this is the only manual index you run.
3. **Delegate in `approval_mode="scoped"`** — this is what gives the worker the shell to
   query the graph. (`auto-edit` has no shell, so the worker greps instead.)

That's everything you set. The rest is automatic: the server injects the `QWEN.md`
graph-before-grep rule the worker auto-loads, keeps the index fresh, and stamps the
`GRAPH:` line on each receipt. Claude never queries the graph itself — it stays on
`qwen_query`. Nothing indexed / graphify missing → delegations just fall back to grep.

**Or hands-off:** say *"set up graphify here"* and the **`graphify-setup`** skill runs this
exact sequence — install → structural index → confirm — and refuses any bare LLM command
(the `--backend`/Bedrock trap below).

**Install (once per machine).**

    uv tool install "graphifyy[ollama]"     # pip works too. Package: `graphifyy`; CLI: `graphify`

The package is `graphifyy` (github.com/Graphify-Labs/graphify); the installed command is
`graphify`. The `[ollama]` extra pulls the OpenAI client the semantic backend needs —
install it even if you only want the structural graph, so you can add semantics later.
Point the server at a non-default binary with `QWEN_DELEGATE_GRAPHIFY=/path/to/graphify`.

**Index a repo (once — the server keeps it fresh after that).**

- **Structural** — fast, deterministic, no LLM; the default for repos you work in
  repeatedly:

      graphify update . --no-cluster        # ~2s, writes graphify-out/graph.json

- **Semantic** (optional) — LLM-named clusters for a human-readable report; **not needed
  for delegation** (the worker reads the raw structural graph). A separate step *after* the
  structural build — `cluster-only` / `label` / `extract` — and it reaches an LLM, so name a
  LOCAL backend explicitly (exact flags vary by version — `graphify --help`):

      OLLAMA_BASE_URL=http://<endpoint>/v1 OLLAMA_MODEL=<model> OLLAMA_API_KEY=<key> \
      GRAPHIFY_MAX_WORKERS=1 \
      graphify cluster-only . --backend ollama    # MAX_WORKERS=1 mandatory on 1-worker Ollama

> **⚠ Only three subcommands reach an LLM — `extract`, `label`, `cluster-only` — and each
> must name its backend.** Bare, graphify auto-selects from the environment (**AWS Bedrock
> if `AWS_PROFILE` is exported**), **billing a real cloud account** *and* egressing your
> code. Always pass `--backend ollama` (local). Everything token-saver itself runs —
> `update` (server refresh) and `explain`/`path`/`affected`/`query` (worker) — is **local
> and LLM-free**, so the plugin can never hit a cloud backend; the risk is only a manual
> semantic run.

After the first index you never run it by hand again: the server runs
`graphify update --no-cluster` (structural, never touches an LLM) in the background after
every delegation and tracks freshness in `.qwen-delegate/graph.json`,
keyed to the git SHA. Every receipt carries a `GRAPH:` line — `fresh @ <sha>`,
`stale (N files) — refresh running`, `indexing`, `failed: <reason>`, or `none`.

**How it plugs in — the graph belongs to the WORKER, not Claude.** The one thing measured
hard, and easy to get backwards:

- The **worker (Qwen)** locates through the graph — `explain`, `affected` (every call site
  that depends on a symbol — key before a rename), `path`, `query`, `diagnose`, `god-nodes`
  — *before* grepping. To enable it, delegate in **`approval_mode="scoped"`**: its shell
  allowlist includes those LLM-free reads (`update`/`add`/`install` and the LLM steps
  `extract`/`label`/`cluster-only` are blocked). The `QWEN.md` graph-before-grep rule does
  the steering; in `auto-edit` (no shell) the worker greps instead — still correct, cheaper
  with the graph.
- **Claude does NOT locate through graphify.** Measured: Claude querying the graph in its
  own shell cost **+64%** (every shell call is a turn that bloats context) versus one
  compact `qwen_query` receipt. So Claude locates via `qwen_query`; the graph is the
  builder's tool for finding the code it will edit.
- Keep your LLD **behavior-only** — never name files or functions. Location-pinning from
  structure backfires (+64% retries); let the worker locate for itself and read where the
  change landed from the receipt's `CHANGED` line.

**What's written.** `graphify update` writes `graphify-out/` — **git-ignored**, since it's a
generated, machine-specific cache (it hard-codes absolute paths). The freshness sidecar
lives in self-ignored `.qwen-delegate/`. Each clone builds its own structural graph on the
first delegation (~2s), so there's nothing to commit or share.

## The trust dial — the one decision per task

- **`trust="self"` (L5, max savings — the default for most work):** you pass NO
  verify. The server runs the worker's own test suite as the gate, guards against
  empty/vacuous suites, and ratchets the required test count on repos with an
  existing green suite so the gate always binds on the delta. You never write or
  read a test. Known residual, measured: the worker's tests can share its code's
  blindspot (a data-loss bug once shipped behind 34/34 green) — accepted at this
  dial setting; audits exist on demand (below).
- **`trust="verified"` (you author the gate):** for stakes — payments, auth,
  data-loss paths, anything you must *know* rather than trust. Write the check as
  `<name>_spec.py` (auto-protected), pass it as `verify`. Mutation-test a gate you
  intend to rely on.

Pick by stakes, not by habit. A settings toggle is `self`; a billing calculation is
`verified`.

### Configuring it

- **Per call (highest precedence):** `trust="self"` or `trust="verified"` on
  `qwen_delegate`. Anything else is refused by name (L1–L4 are a parked design).
- **Per project (the slider position):** set `"trust"` in `.qwen-delegate.json` —
  the standing default for every delegation in that repo, overridden by a per-call
  `trust`. You never touch code to move the slider.
- **Machine-wide default:** set `"trust"` in `~/.qwen-delegate/config.json` — the
  standing position for *every* project, below per-project and per-call. The full
  chain: **per-call `trust` > project `.qwen-delegate.json` > `~/.qwen-delegate/config.json`
  > built-in `"self"`** (L5). The server re-reads it each call, so a change takes
  effect on the next delegation — no restart.
- **`auto` — let the model pick per task:** set the default (project or machine)
  to `"auto"` and there is *no silent fallback* — the server refuses a bare call, so
  the orchestrator must classify each task and pass `trust` explicitly: `"verified"`
  for correctness-critical / irreversible / outward-facing / security·data·money·auth
  work, `"self"` for low-stakes mechanical or greenfield work. Criticality is a
  judgement only the model can make, so `"auto"` routes it there rather than guessing
  server-side. Turn it on machine-wide with `{"trust": "auto"}` in
  `~/.qwen-delegate/config.json`.
- **The unverified path is now opt-in:** omitting both `trust` and `verify` fires
  the L5 self-gate (a real gate), not an *unverified claim*. The only route to
  `STATUS: unverified` is asking for `trust="verified"` with no `verify` — for
  stakes, pass both.
- **Tuning the `self` gate** — project `.qwen-delegate.json`:
  - `"min_tests": N` — floor for the non-vacuous guard (default 5). On a repo
    with an existing green suite you don't need to touch it: the server
    *ratchets* automatically (suite green with N tests at preflight → the gate
    demands N+1, so it always binds on the delta).
  - The suite command is auto-detected (npm/cargo/go/pytest/venv), else stdlib
    `unittest discover -s tests`.
- **Compaction is refused, not survived.** Default `on_compaction="refuse"`: when the
  worker's session tries to compact, the run stops there, its output is discarded
  ungraded, and the receipt says to split the task. Compaction is the documented
  fabrication trigger — a summarised history is precisely the state whose output
  cannot be vouched for, so continuing on it buys a result with no provenance. The
  plugin also asks the executor to *block* the compaction (`PreCompact` exit 2, which
  qwen documents but its auto-compaction path does not reliably honour), and there is
  **no way to disable auto-compaction outright** — qwen's `autoCompactThreshold` only
  moves the trigger (fraction of the window, floor 0.01). So the stop is the real
  mechanism; the block is best-effort. Set `on_compaction="reinject"` or `"discard"`
  to get the old continue-anyway behaviour.
- **The trigger is pushed as late as the executor permits** — `COMPACTION_PCT = 1.0`
  in `qd/invoke.py`, written into every run's settings. Under the refuse policy a
  compaction ends the run, so every token before the trigger is work that gets to
  happen. Override per profile with `compaction_threshold` (a fraction) or
  `compaction_at` (an absolute token count, which is usually what you mean).
- **A hardcoded reserve, not the threshold, sets the real limit.** qwen subtracts
  `SUMMARY_RESERVE` (20,000 — room to generate the summary) + `AUTOCOMPACT_BUFFER`
  (13,000) from the window *before* applying any threshold, so the latest a
  compaction can possibly fire is **`window − 33,000`**. No setting reaches past it.
  On the common 196,608 window that is **163,608 tokens (83.2%)** — identical at
  pct 0.85, 0.98 or 1.0. To hold N tokens before compaction you need a window of
  **N + 33,000 actually served by the endpoint**: 194,000 needs 227,000. `qd doctor`
  prints this number for your machine (`compaction-ceiling`), and
  `compaction_at` resolves against it rather than silently configuring a target the
  reserve makes unreachable.
- **Setup runs itself on install and update.** A `SessionStart` hook
  (`hooks/hooks.json` → `qd/setup.py`) runs the doctor once per plugin version and
  stamps `~/.qwen-delegate/setup/<version>.json`. A version bump re-runs it; every
  other session it does nothing and prints nothing. It only speaks up for HIGH
  findings, because anything it prints is injected into the session's context.
- **Serial vs parallel dispatch** — `"dispatch": "serial" | "parallel"` in project
  `.qwen-delegate.json` or `~/.qwen-delegate/config.json`. Default (unset) is already
  serial: with no `endpoints` section every endpoint holds one slot, and that slot is
  now held **machine-wide** via a lock file, so separate Claude sessions queue instead
  of hitting one GPU at once. Set `"serial"` explicitly to pin that even where an
  endpoint declares `parallel_max > 1` (batches then run in order); `"parallel"` honours
  the declared capacity. Concurrency is not free on a local box — parallel requests
  divide the loaded context, and a request with less context than promised is one that
  comes back truncated.
- **Tuning `verified`:** your gate is whatever shell command you pass as
  `verify`; files matching `*_spec.*` are auto-protected from worker edits
  (extend via `"spec_globs"` in `.qwen-delegate.json`). Put fiddly gates in a
  script on disk, not inline — quoting through JSON→shell has broken three times.
- **What the receipt tells you:** at `self`, a `TRUST: self (L5)` line is stamped
  so a green can never be misread as independently verified; the run log records
  the trust level per run.
- **Bounding the blast radius (`touch_scope`) — independent of `trust`:** pass
  `touch_scope=["a.py","b.py"]` on `qwen_delegate` to allow edits only to those
  **existing** files. An out-of-scope edit to a tracked file auto-reverts and the
  attempt fails; **new files are always allowed** (so a `self`-mode worker can still
  write its own test suite). Off by default — unset means the worker may modify any
  file, and the gate (self-suite or your `verify`) is the only check. Composes with
  any trust level: `self` + `touch_scope` is "self-graded **and** kept to this
  surface." Claude passes it when you name a target surface or ask; it is not applied
  automatically. Note it constrains *modifications*, not creation — pair it with a
  gate if new files also matter.

## Recipes

**Mechanical task (rename, codemod, boilerplate, tests-for-existing, lint sweep):**
commit first, then delegate with a real `verify` and `approval_mode="auto-edit"`.
Rule of thumb from the loss data: if the inline edit would cost less than ~500
tokens, just do it yourself — a delegation round-trip has a floor.

**Question about a codebase:** `qwen_query` — free, read-only, can't invent scope.
Treat answers as leads; precise citations are its weak spot.

**Building something new (feature after feature):** run the architect loop inline —
per feature: one SHORT LLD (interfaces + behavior + edge cases, a few lines), then
`qwen_delegate(task=goal+LLD, trust="self")`, then read the receipt. Include in
every task text: "do NOT stop after planning — a plan is not a deliverable" and the
worker's environment facts (they must survive compaction). Keep the current
architecture in `docs/DESIGN.md`, not restated in chat.

**Changing an existing codebase (the −69% case):** same loop, two additions —
`approval_mode="scoped"` (lets the worker run the suite and query the graph), and
the iron rule: **your LLD pins behavior only — never name files or functions to
edit.** Location-pinning from structure measurably backfires (+64% in retries); the
worker locates for itself via graph-before-grep. You learn where the change landed
from the receipt's CHANGED line.

**A reported bug (symptom only):** delegate the symptom verbatim with
`trust="self"`, `approval_mode="scoped"`, plus: "FIRST write a failing regression
test under tests/ reproducing the report, confirm it fails, then find the cause and
fix it; keep the suite green." The ratchet forces a net-new test; you read a
one-line verdict. Measured: real planted bugs fixed at −43% vs solo.

**Parallel/independent pieces:** `batch=[...]` in one call — fans out across
worktrees server-side, per-item receipts, `MERGE:` command included on success.

## Worked example (one full round-trip)

User says: *"add rate limiting to the API client — 5 requests/sec max, burst up to 10."*

Claude's moves, in order:

    git commit -am "checkpoint before delegation"        <- Claude's move, automatic

    qwen_delegate(
      cwd  = "/home/you/projects/myapp",
      trust = "self",                     # Qwen writes code AND the tests that grade it
      approval_mode = "scoped",           # it may run the suite + query the graph
      task = "Add rate limiting to the API client.
              Behavior: at most 5 requests started per second, bursts up to 10
              allowed (token-bucket semantics); a call past the limit BLOCKS until
              a slot frees, never raises; limits configurable at client
              construction, defaults 5/10. Edge cases: burst drains then refills
              at 5/s; zero-wait when under the limit.
              Locate the relevant code via graphify before grepping. Write pytest
              tests for this under tests/. Never break the existing suite.
              Do NOT stop after planning — a plan is not a deliverable."
    )

Note what is NOT in the task: no file names, no function names, no test authored by
Claude — behavior only. The receipt that comes back:

    STATUS: success
    SESSION: 7f3a…
    ATTEMPTS: 1/3
    TRUST: self (L5) -- gate = the delegate's own suite, non-vacuous guard only
    CHANGED: src/client.py, src/ratelimit.py, tests/test_ratelimit.py
    NEW PUBLIC SURFACE: RateLimiter (ratelimit.py)
    HANDOFF: token-bucket limiter wired into ApiClient, 11 tests green

Claude relays: "Rate limiting is in — new `RateLimiter`, 11 of its own tests green,
suite intact. Want `RateLimiter` public, or internal?" — the one genuine design call
the receipt surfaced.

> The last four instruction lines in the task (graphify-before-grep, tests under
> tests/, don't break the suite, don't stop after planning) are STANDING WORKER
> DISCIPLINE, not task content — they belong in Qwen's workflow (server-injected,
> compaction-safe), not in every architect task text. Moving them there is a pending
> item ([PENDING.md](PENDING.md)); until it lands, include them manually.

## Reading a receipt (the whole job at L5)

Green receipt ≈ 6 lines. What to actually look at:

- `STATUS` decides; `TRUST: self` reminds you what the green means (its tests).
- `CHANGED` — filesystem truth: where the work landed.
- `NEW PUBLIC SURFACE` — new names others can depend on; unrequested ones are scope
  creep: re-delegate with a constraint, don't edit.
- Flags that appear only when something needs you: `MISREPORT`, `COMMITTED`,
  `SHELL APPROVAL NEEDED` (judge the command alone), `gate_suspect` (fix the gate,
  never iterate), `PREFLIGHT` (the gate proved nothing — tighten it).
- Never re-verify a green gate and never read the diff — that's the token cost this
  system exists to remove.

## Quality control without reading code

When something matters enough to check but not enough for `verified`:

- `grade/stage1.py` (token-saver-eval) — deterministic scorecard: interface match
  vs a manifest, unpromised surface, complexity, suite runs.
- One free adversarial pass: `qwen_query` "propose 8 mutations this suite would NOT
  catch" → apply with `grade/mutate.py` → kill rate. Measured: test COUNT is not
  test STRENGTH (78 tests bound no better than 31); this is the metric that tells
  the truth about a self-graded suite.

## Gotchas that are laws

- **Commit before every delegation.** Git is the only rollback.
- One writer per tree; re-read any file the worker touched before editing it.
- Vague task → `approval_mode="plan"` first (it cannot write), pick an option,
  then delegate that option warm. Never delegate a vague task straight to a
  write-capable mode.
- Critical rules go in the TASK TEXT, not just QWEN.md — compaction eats QWEN.md.
- `scoped` is an allowlist, not a sandbox. `yolo` only when shell IS the work.
- Estimate `timeout_sec` for big tasks (receipt TIME line teaches you); the 900s
  default kills large builds mid-write.
