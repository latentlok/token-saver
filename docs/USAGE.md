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
- Recommended: paste `templates/CLAUDE-snippet.md` into the project's CLAUDE.md so
  delegation is the default for mechanical work, not something Claude must remember.
- Existing repos you'll work in repeatedly: `graphify update . --no-cluster` (~2s,
  deterministic, no LLM) — the worker locates code via the graph instead of reading;
  the server keeps the index fresh after every delegation. For a large UNFAMILIAR
  codebase, additionally delegate a one-time semantic index (`graphify update .`
  with the local backend) as an offline job.

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
