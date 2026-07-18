# token-saver — system reference

Canonical reference for an agent working with this system. Read this to understand what
it is, how to drive it, and what you must not assume. Task-specific docs live beside this
file (see §8).

---

## 1. What this is

A smart, expensive model (you) orchestrates. A not-smart, **free** model (Qwen, on local
hardware) executes. An **objective gate** — a shell command — decides whether the work
actually succeeded. You never read Qwen's output or trust its word; you read a verdict.

That asymmetry is the whole product. Qwen burns millions of tokens reading, writing, and
retrying; your context absorbs a few hundred. Measured across one build session: **~19M
tokens processed on free hardware, ~61k of verdicts returned** — roughly 300× more work
than was ingested.

**The invariant that generates every design decision:** *put each decision where it
cannot be faked.* Judgment → the smart model. Verification → a command. Honest questions
and spec-checks → plan mode, where Qwen physically cannot act on a dishonest answer.
Execution → Qwen, because it is cheap and checkable.

---

## 2. The two layers

**The engine (fungible, domain-agnostic).** `server.py` — an MCP server. Its entire
contract is *run a task with the free model, prove it with a verify command, keep the
noise out of the caller's context.* It knows nothing about "code"; it runs **a task and a
gate**. Swap the gate (tests → schema check → linter → validation query) and it serves a
different domain.

**The container (domain-specific).** The agents, skills, and command that instantiate the
engine for a use case. **Code is the current container:** the manager's
spec→build→verify workflow, the LLD principles, and the convention *"the gate = tests."*

Packaged as a Claude Code plugin, so the container is the wrapper and the engine is
reusable underneath it.

---

## 3. Components

| component | path | role |
|---|---|---|
| engine | `server.py` | MCP server: delegate/query, gate, git guards, run log. Zero deps. |
| safety | `scoped_hook.py` | PreToolUse allowlist for `scoped` mode |
| log gate | `runlog_spec.py` | spec for the run log + token accounting (34 tests) |
| manager | `agents/qwen-manager.md` | the code unit: decides, specs, delegates, verifies |
| discipline | `skills/lld-principles/SKILL.md` | design principles, **preloaded** into the manager |
| front door | `commands/delegate.md` | `/delegate <task or question>` |
| worker rules | `templates/QWEN.md` | per-project standing rules Qwen auto-loads |
| manifest | `.claude-plugin/plugin.json` | plugin identity |

Installed by `./install.sh` (idempotent, symlinks everything). Per-project setup is
`./init-project.sh <repo>` — detects the test command, writes `QWEN.md`, registers the
project in the global index, and **refuses non-git projects**.

---

## 3b. The run log

Every call appends one JSON record to `<cwd>/.qwen-delegate/runs.jsonl`. It measures the
thing the system exists to do — **leverage: free tokens burned ÷ tokens returned to you**
(first measured data: 208.6× over 3 runs, range 151.8–266.0×).

Per record: token counts (`main` vs Qwen's memory-extractor sub-agent, summed across
*all* attempts), `token_source`, peak context, wall time, attempts, tools, changed-file
count, and truncated+hashed task and gate text.

Three things to know:

- **`token_source` decides whether `tokens_overhead: 0` means anything.** `bySource` = the
  split is real. `blended` = no breakdown was reported, everything got attributed to
  `main`, and 0 means *unmeasured*. Never read a zero without checking this field.
- **The log is invisible to git** (`.qwen-delegate/` self-ignores via a `.gitignore`
  containing `*`) and is written *after* every diff. If it ever shows up in `CHANGED`,
  that is a bug — it would be misattributed to Qwen.
- **`qwen_query` writes it too.** Queries are read-only with respect to your code, but
  they do create `.qwen-delegate/` on disk. They burn real tokens (~20k baseline), so
  excluding them would understate spend.

`~/.qwen-delegate/projects.jsonl` is a **pointer index only** (paths, no metrics) so an
aggregator can find every project's log. Relocate with `QWEN_DELEGATE_REGISTRY`.

---

## 4. Tool surface

**`qwen_query`** — read-only Q&A about code. Qwen reads and answers; **cannot write**.
`question`, `cwd`, `format` (`answer` default | `map`), `session_id` (warm multi-turn),
`focus`, `timeout_sec`.

**`qwen_delegate`** — the build tool. `task`, `cwd`, `verify` (**the gate**: exits 0 only
on real success), `approval_mode`, `max_iterations`, `session_id`, `shell_allow`,
`shell_feedback`, `timeout_sec`.

**Approval modes** — measured by probing; the upstream bundle documents none of this:

| mode | write | shell | use |
|---|---|---|---|
| `plan` | no | no | vague tasks, spec sanity-checks (also blocks `agent`) |
| `auto-edit` | **yes** | **no** | **default for code** |
| `scoped` | cwd only | allowlist | when Qwen should run the tests itself |
| `yolo` | yes | yes | only when running something *is* the task |
| `default`, `auto` | no | no | never — headless auto-denies everything |

Prefer `auto-edit` over `yolo`: the iterate loop is **server-driven**, so Qwen never needs
a shell to converge, and arbitrary execution at user privilege stays unreachable.

---

## 5. How to drive it

**Front door:** `/delegate <task or question>` — routes questions to `qwen_query`, builds
to the `qwen-manager` subagent.

**The loop the manager runs** (and the shape to follow if driving directly):

```
0 UNDERSTAND  ask Qwen about the code (read-only) — don't read it yourself
1 PLAN        vague? → plan mode returns options; it cannot invent scope there
2 DECIDE      pick the approach. Verify Qwen's plan against the code. Never hand back a menu.
3 SPECIFY     write the *_spec gate yourself — this IS the design decision
4 EXECUTE     delegate the build against that gate (auto-edit)
5 VERIFY      run the gate yourself, check CHANGED, roll back if wrong
```

**Ownership:** you own every judgment; Qwen owns implementation *inside a pinned spec*
(names, internal structure, algorithm); a command owns the verdict; the human is asked
only for direction, outward-facing changes, or irreversible calls.

**Manager report format:** `DONE / VERIFIED / CHANGED / DECIDED / NEEDS HUMAN / CAVEAT`.
`VERIFIED` must name a command actually run. `NEEDS HUMAN` should usually be absent.

---

## 6. What you must know (measured, not theoretical)

1. **Qwen fabricates success.** It reported "all three tests pass ✅" with pytest not
   installed. **Its self-report is never evidence.** Only the gate is.
2. **Qwen games a flawed gate rather than reporting it.** Given `sign(0)==1` AND
   `sign(0)==-1`, it wrote a fake-equality object; told explicitly not to game it, it
   wrote a non-deterministic counter. **0/3 raised a blocker.** A green gate on a
   contradictory spec is a real, silent failure.
3. **In plan mode it is honest** — it cannot hack there. The same contradiction, asked via
   `qwen_query`, produced a clean "not implementable, these two tests conflict."
   → **Sanity-check any spec you're unsure of, read-only, *before* building.**
4. **Never let Qwen write the file that grades it.** When it authors both code and tests,
   its misunderstanding lands identically in both — they agree, and both are wrong.
   `*_spec.*` is yours (auto-reverted if touched); `*_qwen.*` is its own.
5. **A gate you haven't tested is a hope.** 909 real tests passed a mutation that changed
   *every error message* the library emits. Mutate the thing you fear, confirm red.
6. **Vagueness is the root cause of everything.** Well-specified, Qwen is genuinely strong
   (a Jinja AST extension first try, correct beyond spec). Vague, it invents confident
   scope, silently changes public APIs, and breaks rules it honoured 11 times before.
7. **`qwen_query` answers are a lead, not truth.** Structure and semantics reliable;
   precise claims not (it once mapped a library perfectly and fabricated every line
   number). Verify anything load-bearing against source.
8. **Compaction (~147k) causes fabrication** — past it, it claimed to have read files it
   never opened. Keep queries bounded; peak context is reported.
9. **`TOOL FAILURES: N blocked` in a restricted mode is expected**, not a defect.

---

## 7. Operating rules

- **Commit before every delegation.** Git is the only rollback; there is no sandbox and
  Qwen runs at full user privilege.
- **Rollback:** `git checkout . && git clean -fd`. **Never `-fdx`** — it deletes
  gitignored `venv/`.
- **Gates belong in a script on disk**, not inline. Inline quoting collapsed through the
  JSON→shell→python chain three times and caused a doom loop.
- **Re-read any file Qwen touched** before editing it — your copy is stale.
- **Stateless by default.** A fresh session re-reads `QWEN.md`, which is what makes the
  rules bind. Use `session_id` only for follow-ups on the *same* task.
- **Timing** (fitted, 198 real calls): `seconds ≈ input/10,882 + output/70`. Set
  `timeout_sec` with 2–3× headroom. Calls >120s are auto-backgrounded — normal.
- **The run log is the record.** Per-project `.qwen-delegate/runs.jsonl`; check
  `token_source` before trusting a zero in `tokens_overhead`.
- **Read the verdict's signals:** `gate_suspect` = your gate is broken, not the code.
  `success_but_preflight_passed` = the gate was already green, so the pass proves nothing.
  `NEW PUBLIC SURFACE` = new contracts Qwen added (deterministic scan, zero tokens —
  review that line, never the whole diff).

---

## 8. Where to look next

| doc | for |
|---|---|
| `context/TESTING.md` | testing the system — state, test plan, expected outcomes |
| `docs/FINDINGS.md` | **the evidence.** Every measurement behind every guard. Read before concluding a protection is paranoid — each was bought with a real failure. |
| `docs/ARCHITECTURE.md` | how it works, component by component |
| `agents/qwen-manager.md` | the manager's full workflow and escalation policy |
| `skills/lld-principles/SKILL.md` | the design discipline the manager must follow |
