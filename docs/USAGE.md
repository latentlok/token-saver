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

- **Semantic** — adds LLM-derived clusters/labels for orienting in a large *unfamiliar*
  codebase; needs an LLM, so **always name the backend and model explicitly**:

      OLLAMA_BASE_URL=http://<your-endpoint>/v1 \
      OLLAMA_MODEL=<your-model> \
      OLLAMA_API_KEY=<key> \
      GRAPHIFY_MAX_WORKERS=1 \
      graphify update . --backend ollama --model <your-model>   # MAX_WORKERS=1 mandatory on 1-worker Ollama

> **⚠ Never run a semantic / LLM graphify command without an explicit `--backend`.**
> With none, graphify auto-selects one from your environment — notably **AWS Bedrock if
> `AWS_PROFILE` is exported** — which **bills a real cloud account** *and* ships your code
> off-box. Always pass `--backend ollama --model <your-model>` (or your intended local
> backend). The server's per-delegation refresh is safe by construction — it runs
> `--no-cluster`, so it never reaches an LLM and can't pick up a cloud backend.

After the first index you never run it by hand again: the server runs
`graphify update --no-cluster` (structural, never touches an LLM) in the background after
every delegation and tracks freshness in `.qwen-delegate/graph.json`,
keyed to the git SHA. Every receipt carries a `GRAPH:` line — `fresh @ <sha>`,
`stale (N files) — refresh running`, `indexing`, `failed: <reason>`, or `none`.

**How it plugs in — the graph belongs to the WORKER, not Claude.** The one thing measured
hard, and easy to get backwards:

- The **worker (Qwen)** locates through the graph — `graphify explain "<symbol>"`,
  `graphify path "A" "B"`, `graphify diagnose` — *before* grepping. To enable it, delegate
  in **`approval_mode="scoped"`**: its shell allowlist includes exactly those three read
  queries (`update`/`add`/`install` are blocked as state-changing). The `QWEN.md`
  graph-before-grep rule does the steering; in `auto-edit` (no shell) the worker greps
  instead — still correct, just less cheap.
- **Claude does NOT locate through graphify.** Measured: Claude querying the graph in its
  own shell cost **+64%** (every shell call is a turn that bloats context) versus one
  compact `qwen_query` receipt. So Claude locates via `qwen_query`; the graph is the
  builder's tool for finding the code it will edit.
- Keep your LLD **behavior-only** — never name files or functions. Location-pinning from
  structure backfires (+64% retries); let the worker locate for itself and read where the
  change landed from the receipt's `CHANGED` line.

**What's written / committed.** `graphify update` writes `graphify-out/`; the freshness
sidecar lives in self-gitignored `.qwen-delegate/`. Committing the structural
`graphify-out/graph.json` gives teammates a warm index (this repo does); dated semantic
snapshots (`graphify-out/2026-*/`) are gitignored.

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
      cwd  = "/home/dev-vishal/projects/myapp",
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
