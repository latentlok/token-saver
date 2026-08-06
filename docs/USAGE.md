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

## A delegation is a submission, not a wait

`qwen_delegate` answers in **seconds**, before any work happens:

    STATUS: submitted
    RUN: r3f9a2c
    RECEIPT: /path/.qwen-delegate/receipts/r3f9a2c.md — lands on completion
    HEARTBEAT: /path/.qwen-delegate/progress.json
    WATCH: until [ -f …/r3f9a2c.md ]; do sleep 5; done; cat …/r3f9a2c.md

The run continues in the background; the receipt lands as a **file** when it's done
(written atomically — if the file exists, it's complete). So the working rhythm is:
submit, go do something else, read the file later. The `WATCH:` line is the wait-for-it
one-liner for when there's genuinely nothing else to do; `wait: true` restores the old
blocking call outright. Chain/batch submissions add a `PARTIAL:` path where each link's
receipt appears as it lands, so an eight-step overnight chain can be read mid-flight.

Three things follow from the mechanics:

- **Cheap refusals come back in the response, not the file** — a bad argument, a
  non-git cwd, an unknown trust level, a playbook that doesn't parse, an oversized
  brief. Nobody polls for a run that was never spawned. Gate problems that cost real
  time (`GATE UNUSABLE`, `GATE VACUOUS`) are part of the run and land in the receipt.
- **The heartbeat answers "is it hung?" for a file read** — records seen, input
  tokens, attempt number, `state: running|done`. It lives in the cwd you submitted
  from, next to the receipt.
- **In-flight runs die with the session.** The run executes on a thread of the MCP
  server, so ending the Claude session takes unfinished runs with it. A `running`
  record whose pid is gone means exactly that — the receipt will never land; resubmit.

## Install (once per machine)

1. **Prerequisites:** the `qwen` CLI (Qwen Code) configured against your free/local
   endpoint (e.g. Ollama) — that's the worker; `python3` (the server is stdlib-only,
   nothing to pip-install); `git`. Optional: `graphify` for the code graph.
2. **Install the plugin** in Claude Code:
   `/plugin marketplace add latentlok/token-saver` then install `token-saver` from
   it. That registers everything at user scope via the plugin manifest: the two MCP
   tools (`qwen_delegate`, `qwen_query`), the skills (delegation, architect,
   lld-principles, graphify-setup), and the qwen-manager/architect agents. No manual
   MCP config — `.mcp.json` wires the server through `${CLAUDE_PLUGIN_ROOT}`.
3. **Different/bigger worker model?** Add a profile in
   `~/.qwen-delegate/executors.json` (C7) and pass `executor=` per call — nothing
   else changes.
4. **Let it check the machine.** The first session after an install or an update
   runs `qd.doctor` once and reports only what will actually bite. Run it yourself
   with `/token-saver:doctor`, or `python3 -m qd.doctor` from the plugin directory.
   `--fix` writes the one setting it can determine safely (after a `.bak`);
   `--verified <N>` records the context window you read off the endpoint, so the
   "declared but unverified" finding stops firing until one of the two changes.

### Reference setup (what this is developed and measured against)

Not a requirement — any model Qwen Code can reach works. This is the known-good
configuration, recorded so the numbers elsewhere in these docs have a subject:

| | |
|---|---|
| model tag | `qwen3.6:27b-agent-q8-maxctx` (Ollama, q8) |
| context window | 262,144, confirmed against the endpoint |
| max output | 128,000 |
| thinking | on |
| decode rate | ~70 tok/s on a 27B; ~17 tok/s on a 120B-class model |

Two things about this tag are worth copying, whatever model you run:

**Set `maxTokens` explicitly.** This tag normalises to `27b-agent-q8-maxctx` —
qwen-code keeps only the part after the last `:` — which matches no known-limits
pattern, so without an explicit value it silently takes the generic 32,000
default. Declaring it is what makes the cap yours instead of a guess. Renaming the
tag so it normalises to something recognisable (`qwen3.6-...`) works too.

**Confirm the context window rather than declaring it.** Read `CONTEXT` off
`ollama ps` for the loaded model and record it with `python3 -m qd.doctor
--verified <that number>`. Every compaction and context line is computed from the
declared value; if the endpoint actually serves less, receipts read "safe, well
under compaction" while it truncates.

### Working from a clone instead (plugin development)

To edit the plugin itself, skip the marketplace and point Claude Code at a checkout:

    git clone https://github.com/latentlok/token-saver.git ~/projects/token-saver
    claude --plugin-dir ~/projects/token-saver

Changes apply with `/reload-plugins` in the same session; `git pull` is the update.
Don't do both — a marketplace install and a `--plugin-dir` clone load the plugin twice.

## Routing: delegate, follow up, retry, or just do it

The decision table, in the order the questions actually come up:

| situation | route |
|---|---|
| a command could prove it was done (rename, codemod, tests-for-existing, boilerplate, lint sweep) | **delegate** — commit first, then `qwen_delegate` |
| the inline edit would cost under ~500 tokens | **do it yourself** — a delegation round-trip has a floor |
| a question about the code ("how does X work", "is there already a Z") | **`qwen_query`** — read-only, free, answers are leads to verify |
| tight follow-up to a HEALTHY run, same task | **resume warm** — same `session_id` (the receipt's `RESUME:` line offers it); costs a sentence |
| correcting a FAILED run | **`retry_of=<session_id>` + `retry_message`** — replays the stored brief COLD; a failed session argues with corrections, so never resume into one |
| the same brief keeps coming back | **a playbook** — put the brief in the repo, send it by name (below) |
| dependent steps | **`chain=[...]`** — serial on one tree, halts at the first red link |
| independent pieces | **`batch=[...]`** — fans out across worktrees, per-item receipts |
| diagnose only, don't fix | **`report_dont_fix=true`** — one attempt, `FINDINGS:` line, red gate = the reproduction |
| vague task | **`approval_mode="plan"` first** — it cannot write; pick an option, then delegate that warm |
| judgment, design, anything with no objective check | **keep it** — delegation needs a gate to mean anything |

`retry_of` works because every run with a session id stores its brief (task, gate,
scope, mode, trust) under `.qwen-delegate/briefs/`. Pass `task: ""` to reuse the stored
task untyped; any argument you do pass beats the stored one. Project key
`"store_briefs": false` opts out.

## Settings reference (`.qwen-delegate.json`)

Per project, all optional. Machine-wide defaults for the same keys go in
`~/.qwen-delegate/config.json`; the project file wins, and a per-call argument beats
both — the config is what a call *falls back to*, never what overrides it.

| key | default | what it does |
|---|---|---|
| `test_command` | detected | The exact command that runs your tests. Beats every detector — use it when your layout isn't one the detectors guess (they key off `package.json`, `Cargo.toml`, `go.mod`, `Gemfile`, a venv `pytest`, `pyproject.toml`/`setup.py`). |
| `test_dir` | `tests`/`test`/`spec`/`specs` if present | The folder holding your tests, for the discovery fallback. |
| `trust` | `self` | `self` = the worker writes and grades its own suite; `verified` = your `verify` command is the gate; `auto` = refuse a bare call so the orchestrator picks per task. |
| `min_tests` | 5 | Floor for the non-vacuous guard under `trust="self"`. Ratchets automatically against an existing green suite. |
| `spec_globs` | `specs/*`, `*_spec.*`, … | Files the worker may never edit. Its edits to them auto-revert. |
| `approval_mode` | `auto-edit` | Standing mode for every delegation here (`plan`/`auto-edit`/`scoped`/`yolo`). |
| `shell_allow` | unset | Standing extra command regexes for `scoped` mode. |
| `timeout_sec` | 900 | Per-attempt kill time. |
| `verify_timeout_sec` | 300 | Kill time for ONE gate run (clamped 10..3600). A pre-flight that times out refuses the run — every retry would pay it. |
| `preflight_expect` | `any` | What the gate should say before the worker runs: `red` (greenfield — a passing pre-flight refuses the run, the gate could prove nothing) or `green` (revision work — stops the preflight alarm on a suite green by premise). |
| `max_iterations` | 3 | Attempts before giving up (clamped 1..10). |
| `task_suffix` | unset | Your standing worker discipline, appended to EVERY task server-side. Compaction-safe (it rides the task through re-injections, unlike QWEN.md) and never stacks into stored briefs. |
| `store_briefs` | true | Whether runs store their brief for `retry_of`. |
| `fixture_globs` | `fixtures`/`testdata`/`golden`/`snapshots`/`cassettes` | Directory segments policed by `fixture_provenance`. |
| `dispatch` | unset (already serial) | `serial` pins every endpoint to one in-flight request whatever its `parallel_max` says; `parallel` honours the declared capacity. |
| `worktree` | unset (in-tree) | `auto` makes isolation the standing default: every delegation here builds in its own git worktree unless the call says `worktree: "off"`. Set it where co-work is the norm. Only `auto` is recognised — a typo reads as in-tree. |
| `burn_budget` | 10,000,000 | Cumulative input tokens one delegation may spend before it is stopped. `0` disables (also disables the heartbeat — it rides the same stream). |
| `decode_tps` | 15 | Your model's decode rate. The silence budget is derived from it and the declared max output — state the rate, not the seconds. |
| `stall_seconds` | derived | Absolute override for the silence budget, if you'd rather state the answer directly. |
| `compaction_threshold` | 1.0 | Fraction of the window at which the executor may auto-compact. Rarely the binding term — see below. |
| `compaction_at` | unset | Absolute token target for the same thing. Resolves against the reserve rather than silently configuring an unreachable number. |

`task_suffix` deserves a highlight: standing instructions like "do NOT stop after
planning" or "write tests under tests/" used to need repeating in every task text.
State them once here; the server appends them to every task in this repo.

### Where the tests live, and why it matters

If the plugin can't work out how to run your tests, `trust="self"` gets a gate that
can never pass, and nothing says so. `test_command` or `test_dir` fixes it in one line.

`trust="self"` also needs the worker's own tests to land somewhere your `spec_globs`
don't protect — otherwise they auto-revert as fast as it writes them. The convention
is `*_qwen.*` for worker-written tests, kept clear of the files that define correct.
They are never the gate; once the work is accepted they are ordinary regression cover.

### Live limits

Every delegation carries two ceilings. Both only ever end a run early — neither can
turn a failing run green.

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

The heartbeat sidecar (`.qwen-delegate/progress.json`) rides the same stream the
limits watch, so it exists exactly when a burn budget does. One caveat worth knowing:
a limit can only act on records it receives, which only arrive incrementally in
streaming mode. If an executor profile's argv can't be switched to the streaming
output format, the limit is inert — the receipt says so rather than letting a guard
that never watched read as a guard that found nothing.

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
- **The unverified path is opt-in:** omitting both `trust` and `verify` fires
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
  held **machine-wide** via a lock file, so separate Claude sessions queue instead
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

## Playbooks — the brief as a repo file

A brief you'd send twice belongs in the repo, not in the chat. A **playbook** is a
markdown file that carries a delegation; you send it by name:

    qwen_delegate(cwd="...", brief_file="playbooks/add-endpoint.md",
                  vars={"resource": "invoices"})

The document looks like this:

    ---
    verify: python3 -m pytest tests/api/ -q
    touch_scope: ["src/api/routes.py", "src/api/schemas.py"]
    approval_mode: scoped
    ---
    Add a REST endpoint for {{resource}}: list + get by id, following the
    pattern the existing endpoints use. Schemas in src/api/schemas.py.

    ## Amendments
    - 2026-07-27 return 404 as JSON, not HTML

The rules, each of which is enforced rather than hoped for:

- **The body is the task.** A `task` you pass alongside rides as an addendum ("focus
  on X this time"), it never replaces the document.
- **Front matter fills what the call doesn't.** Recognised keys: `verify`,
  `touch_scope`, `shell_allow`, `approval_mode`, `timeout_sec`, `verify_timeout_sec`,
  `preflight_expect`, `advisory_gates`, `max_iterations`, `chain`. Precedence is
  **call args > front matter > project config > machine config**. Unknown keys,
  wrong-shaped values, and an unclosed `---` fence are refused by name — a typo'd
  gate key silently ignored is a gate that never runs. Values parse as JSON when
  they can (`timeout_sec: 1200`, `touch_scope: ["a.py"]`), else as bare strings —
  which has one gotcha: a gate command that *is* a JSON word needs quoting
  (`verify: "true"`, not `verify: true`).
- **`trust`, `executor`, and `worktree` are deliberately NOT front-matter keys.** Who
  is trusted and where the run happens are the caller's decisions; a document that
  could grant itself full trust would be privilege escalation in markdown.
- **`{{slots}}` fill from `vars`** — across the whole file, front matter included
  (`verify: pytest {{mod}}_test.py` works). An unfilled slot and a `vars` key
  matching no slot are both refused by name; `{{` is reserved, no escape.
- **The worker cannot edit its own brief.** An edit to the document reverts like a
  spec edit and fails the attempt; a change with no logged worker write (your own
  concurrent edit) is reported and left alone.
- **The receipt pins the version.** `BRIEF: playbooks/add-endpoint.md @ <digest>
  · ~140 tokens` on every receipt, and the `LEDGER:` line grows a per-document
  tally — `this brief: 4 ok / 1 red` is what tells you to fix the document rather
  than re-roll the worker.

**Corrections go into the document.** On a retry, `amend_brief: true` folds your
`retry_message` into the playbook itself as a dated `## Amendments` line instead of a
one-shot CORRECTION on the task — git versions it, and every later run (and reader)
inherits the lesson. The amendment lands *before* the run's snapshot, so it reads as
pre-existing dirt, never as the worker's change.

**Big briefs: split into steps.** Front matter `chain: true` compiles `## Step <n>`
sections into a chain — each link gets the preamble plus its own step only, so
per-link context stays flat however long the document grows. A step can override
`verify:` / `touch_scope:` on its leading lines:

    ---
    chain: true
    verify: bash ci/gate.sh
    ---
    Shared context every step needs.

    ## Step 1: schema
    verify: python3 -m pytest tests/schema_test.py -q
    Add the column + migration.

    ## Step 2: endpoint
    Expose it in the API.

**Size discipline.** A huge brief costs you a filename but costs the worker peak
context — and under the refuse-compaction policy that converts to dead runs. The
system pushes back in layers: the `BRIEF:` line always shows the size estimate; past
5 amendments it says `consolidate`; and a brief composing to more than a quarter of
the worker's window is refused at submit (`BRIEF TOO BIG`) with the fix named —
split into steps + `chain: true`, or consolidate. Consolidation itself is a
delegable, gateable task: *"fold this playbook's amendments into its body; the
document must still parse"* — git preserves the archaeology. And keep the document
to the **delegation** (task, gate, scope): background and design belong in stable
repo docs the worker reads on demand, not inlined into every run's prompt.

One retry subtlety worth knowing: `retry_of` re-reads the *document* (that's the
point — amendments and edits bind), but any argument the original call passed
explicitly is stored and beats the front matter, exactly like a live call.

## Co-working while a delegation runs

**The norm: if other agents (or you) will touch the tree while a run is live, put
the delegation in a worktree.** Set `"worktree": "auto"` once in the project's
`.qwen-delegate.json` and every delegation isolates by default — the worker builds
on its own `qwen/<id>` branch, nobody's edits can collide with anybody's, and the
receipt hands you the `MERGE:` line. A call arg (`worktree: "off"`) still forces
in-tree for the case that wants it: a quiet, committed tree, or work that must land
directly. The one cost to know: **a worktree branches from HEAD**, so the worker
never sees uncommitted co-work — commit first, or the merge lands against a base
that moved (`classify_merge` probes for that read-only and the receipt says
`conflict` rather than corrupting anything).

In-tree co-work is still safe in the attributing modes — the guards attribute
before they act, and the receipt reports what it couldn't attribute instead of
guessing:

- In `scoped` mode (or with the observe hook on), every worker write is logged, so a
  changed file with **no logged worker write is yours** — the scope guard and spec
  guard report it (`SCOPE: … caller co-work?`, `SPEC CHANGED (unattributed)`) and
  **never revert it**. Rolling back a caller's concurrent edit is the one sin the
  guards are built to avoid.
- `HEAD MOVED` is attributed neutrally: in `scoped` mode commits are hard-denied to
  the worker, so a moved HEAD is yours; elsewhere the receipt says "attribution
  unknown — check git log" rather than accusing. `ROLLBACK:` advice follows suit — a
  blanket reset is only suggested when the commits are positively the worker's.
- In plain `auto-edit` there is no write log, so nothing is attributable and the old
  rule stands: treat it as **one writer per tree**, and re-read any file the worker
  touched before editing it.
- A worktree run (`worktree="auto"`, or the project config default) sidesteps all
  of it: the worker builds on its own branch and the receipt hands you a `MERGE:`
  line.

## Recipes

**Mechanical task (rename, codemod, boilerplate, tests-for-existing, lint sweep):**
commit first, then delegate with a real `verify` and `approval_mode="auto-edit"`.
Rule of thumb from the loss data: if the inline edit would cost less than ~500
tokens, just do it yourself — a delegation round-trip has a floor.

**Question about a codebase:** `qwen_query` — free, read-only, can't invent scope.
Treat answers as leads; precise citations are its weak spot. Need a value, not
prose? Pass `result_schema` and read the JSON block back.

**Building something new (feature after feature):** run the architect loop inline —
per feature: one SHORT LLD (interfaces + behavior + edge cases, a few lines), then
`qwen_delegate(task=goal+LLD, trust="self")`, submit and move on; read the receipt
file when it lands. Put the standing discipline ("do NOT stop after planning…",
environment facts) in `.qwen-delegate.json` `task_suffix` once, not in every task.
Keep the current architecture in `docs/DESIGN.md`, not restated in chat.

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
one-line verdict. Measured: real planted bugs fixed at −43% vs solo. Not sure it's
even a bug? `report_dont_fix=true` first — the red gate is the reproduction, the
`FINDINGS:` line is the diagnosis, and nothing gets "fixed" on a hunch.

**A red receipt:** `retry_of=<session_id>` + `retry_message` — the stored brief
replays COLD with your correction appended; `task: ""` saves the retyping. If the
brief is a playbook, add `amend_brief: true` so the correction lands in the
document instead of evaporating with the session.

**Dependent steps (migrate, then adapt, then clean up):** `chain=[...]` in one call
— serial on the same tree, one endpoint slot at a time, halts at the first
non-green link (the rest render as one-line SKIPPED receipts). Watch it mid-flight
through the `PARTIAL:` file. Recurring chains graduate into a `chain: true`
playbook.

**Parallel/independent pieces:** `batch=[...]` in one call — fans out across
worktrees server-side, per-item receipts, `MERGE:` command included on success.

**Architecture/conformance watchdogs:** `advisory_gates=[{name, cmd}]` — loose
checks that glow red in the receipt but never touch STATUS, never enter the retry
loop, never reach the worker. The place for "does the layering still hold" checks
that shouldn't fail a build.

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
              at 5/s; zero-wait when under the limit."
    )

Note what is NOT in the task: no file names, no function names, no test authored by
Claude — behavior only. Also absent: the standing discipline lines ("locate via
graphify before grepping", "write pytest tests under tests/", "never break the
suite", "do NOT stop after planning") — those live in this project's
`.qwen-delegate.json` as `task_suffix`, appended server-side to every task.

The response arrives in seconds:

    STATUS: submitted
    RUN: r7c21b4
    RECEIPT: /home/you/projects/myapp/.qwen-delegate/receipts/r7c21b4.md — lands on completion
    HEARTBEAT: /home/you/projects/myapp/.qwen-delegate/progress.json
    WATCH: until [ -f …/r7c21b4.md ]; do sleep 5; done; cat …/r7c21b4.md

Claude goes back to whatever you were discussing. A few minutes later it reads the
receipt file:

    STATUS: success
    SESSION: 7f3a…
    ATTEMPTS: 1/3
    RUN: 1 attempt(s) · peak 41% ctx · 312s · 9,214 out
    TRUST: self (L5) -- gate = the delegate's own suite, non-vacuous guard only
    CHANGED: src/client.py, src/ratelimit.py, tests/test_ratelimit.py
    NEW PUBLIC SURFACE: RateLimiter (ratelimit.py)
    HANDOFF: token-bucket limiter wired into ApiClient, 11 tests green
    LEDGER: run #12 · lifetime 9 ok / 2 red / 0 stopped · peak-ctx record 63%
    RESUME: session_id=7f3a… -- a follow-up in this warm session costs a sentence

Claude relays: "Rate limiting is in — new `RateLimiter`, 11 of its own tests green,
suite intact. Want `RateLimiter` public, or internal?" — the one genuine design call
the receipt surfaced.

## Reading a receipt (the whole job at L5)

Green receipt ≈ 8 lines. What to actually look at:

- `STATUS` decides; `TRUST: self` reminds you what the green means (its tests).
- `RUN` — the telemetry line: attempts, peak context, time, output, denials, strays.
- `CHANGED` — filesystem truth: where the work landed.
- `NEW PUBLIC SURFACE` — new names others can depend on; unrequested ones are scope
  creep: re-delegate with a constraint, don't edit.
- `BRIEF` (playbook runs) — which document version briefed it; pair with the
  `LEDGER` per-document tally when deciding whether to amend.
- `RESUME` — the session heuristic, computed for you: a healthy session offers a
  warm follow-up; a failed one says re-delegate COLD (`retry_of`) instead, because
  a session that failed carries its confusion forward.
- Flags that appear only when something needs you: `MISREPORT`, `HEAD MOVED`,
  `SHELL APPROVAL NEEDED` (judge the command alone), `gate_suspect` (fix the gate,
  never iterate), `PREFLIGHT` (the gate proved nothing — tighten it), `TEST DODGE`
  (a skip added beside the delivery), `STRAYS` (files the task never asked for).
- **`stuck_no_progress` + `NO PROGRESS:`** — the run ended with two attempts
  producing byte-identical gate output. Distinct from `verify_failed` because the
  two want opposite responses: a run that failed once and moved may be worth
  another attempt, a run that has stopped moving will return the same receipt.
  Change the brief or the gate. The worker was already told mid-run (it is
  switched to diagnose-before-editing on the first repeat); this is the caller
  being told.
- **`contract`** — path to a criteria document with numbered clauses (`C1:`,
  `- **C2**:`, `### C3 --`). Turns on three things at once: every clause must
  have a delivered test naming it or the attempt fails with `UNCOVERED: C2`;
  the receipt pins `CONTRACT: path @ digest` so a reviewer weeks later can tell
  whether the file they are reading is the file that ran; and a later chain link
  refuses if the contract moved since the gate was written against it. Add the
  path to `spec_globs` too, so the worker cannot edit it.
- **`review_brief`** (default off) — after the run, asks the worker whether the
  diff delivers the brief. Advisory only: it never touches STATUS and never
  reaches the worker. Costs one executor pass on a finished run.
- **`SUPPRESSED:`** names any of the above that did NOT report — either the
  size cap shed it to fit, or the check itself failed. Read it as *this
  receipt is not telling you those checks were clean*, because a missing
  warning and a passed check look identical otherwise. It never fires for
  `RESUME`/`LEDGER` being shed: those cost you nothing you cannot ask for
  again, and a line that fired on every long receipt would be ignored.
- Never re-verify a green gate and never read the diff — that's the token cost this
  system exists to remove.

## Quality control without reading code

When something matters enough to check but not enough for `verified`:

### `advisory_gates` — measure whether self-grading caught anything

**The question `trust="self"` cannot answer about itself:** the worker wrote the
suite, so a green receipt tells you the worker's tests pass. It cannot tell you
whether a gate *you* would have written would also have passed.

`advisory_gates` answers it, and it is the only instrument here that does:

```json
{"advisory_gates": [{"name": "owner-spec", "cmd": "python3 specs/thing_spec.py"}]}
```

Attach a spec **you** hold, run the delegation at `trust="self"`, and read the
two results against each other:

| STATUS | advisory | what you learned |
|---|---|---|
| green | green | the worker's suite and yours agree — the strongest signal available |
| green | **red** | **a measured self-grading blindspot.** The worker's tests pass and yours do not. This is the case you cannot get any other way |
| red | — | the gate already stopped it; the advisory is noise |

Advisory gates **never touch `STATUS` and never reach the worker**. They cannot
turn a red run green or a green run red, and the worker cannot write code aimed
at passing them — it does not know they exist. That is what makes the second row
a measurement rather than another gate.

Cheapest useful shape: keep one small owner-written spec per risky module and
attach it whenever you delegate into that module at `trust="self"`. It costs one
extra command run and it is the difference between trusting self-grading and
having checked it.

- `grade/stage1.py` (token-saver-eval) — deterministic scorecard: interface match
  vs a manifest, unpromised surface, complexity, suite runs.
- One free adversarial pass: `qwen_query` "propose 8 mutations this suite would NOT
  catch" → apply with `grade/mutate.py` → kill rate. Measured: test COUNT is not
  test STRENGTH (78 tests bound no better than 31); this is the metric that tells
  the truth about a self-graded suite.

## Gotchas that are laws

- **Commit before every delegation.** Git is the only rollback.
- **Don't end the session while runs are in flight** — they run on the server's
  threads and die with it. The run log marks them (`dead: true`); resubmit.
- Without attribution (plain `auto-edit`), one writer per tree; re-read any file the
  worker touched before editing it. With it (`scoped`), co-work is safe — reported,
  never rolled back.
- Vague task → `approval_mode="plan"` first (it cannot write), pick an option,
  then delegate that option warm. Never delegate a vague task straight to a
  write-capable mode.
- Critical rules go in `task_suffix` (or the task text), not just QWEN.md —
  compaction eats QWEN.md; the suffix rides every re-injection.
- `scoped` is an allowlist, not a sandbox. `yolo` only when shell IS the work.
- Estimate `timeout_sec` for big tasks (receipt TIME line teaches you); the 900s
  default kills large builds mid-write.
- In a playbook, quote a gate command that is also a JSON word: `verify: "true"`.
