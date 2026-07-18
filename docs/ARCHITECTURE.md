# Architecture

How this actually works, component by component. Pair it with
[FINDINGS.md](FINDINGS.md), which records *why* each protection exists — this file is
the *how*.

The one-line thesis: **`server.py` sits between a model that lies and a caller that
needs truth, and converts one into the other by running a command instead of believing
a report.** Everything below defends that conversion.

---

## 1. The integration surface: MCP over stdio

Claude Code talks to external tools through the **Model Context Protocol (MCP)**. A
stdio MCP server is just a program that speaks **JSON-RPC 2.0 over stdin/stdout**.

At startup Claude Code reads `~/.claude.json`, finds:

```json
"qwen-delegate": {
  "type": "stdio",
  "command": "python3",
  "args": ["/home/you/projects/qwen-delegate/server.py"],
  "timeout": 7200000
}
```

…and launches `python3 server.py` as a child process. From then on:

- Claude writes a JSON request to the process's **stdin**.
- The server writes a JSON response to its **stdout**.
- Everything the server prints to **stderr** is logging — it never touches the protocol.

The handshake is three messages (`initialize` → `notifications/initialized` →
`tools/list`). After `tools/list`, the server's single tool — `qwen_delegate` — appears
in Claude's toolbox exactly like a built-in. There is no network listener, no daemon,
no port. It is one program Claude keeps alive and pipes JSON at.

`server.py` is **~750 lines of standard-library Python with zero dependencies**, so it
runs anywhere Python does and cannot break from a package upgrade.

---

## 2. What one `qwen_delegate` call does

Parameters: `task`, `cwd`, `verify`, `approval_mode`, `max_iterations`, `session_id`,
`timeout_sec`.

```
┌── pre-flight ─────────────────────────────────────────────┐
│ 1. refuse if a *_spec.* file is already uncommitted        │  ← protects your WIP
│ 2. snapshot the git tree (status + content hash per file)  │  ← the "before" picture
│ 3. run `verify` ONCE, now, before Qwen touches anything    │  ← is the gate already green?
└────────────────────────────────────────────────────────────┘
        │
        ▼   for attempt in 1..max_iterations:
┌── execute ────────────────────────────────────────────────┐
│ 4. run:  qwen -p "<task>" --approval-mode <mode> -o json   │  ← the local model, as a subprocess
│          (resumed with -r <session> on retries)            │
│ 5. parse the JSON stream, keep only the final result       │  ← Qwen's prose is discarded here
│ 6. spec guard: did any *_spec.* file change? revert it,    │
│    fail the attempt, tell Qwen to fix the CODE instead     │
│ 7. run `verify` again → exit code 0?                       │  ← THE decision. not Qwen's claim.
│      yes → done                                            │
│      no  → feed the real error text back, loop (step 4)    │  ← free retries on free compute
└────────────────────────────────────────────────────────────┘
        │
        ▼
┌── report ─────────────────────────────────────────────────┐
│ 8. diff the git tree vs the snapshot (blast radius)        │  ← what the FILESYSTEM says changed
│ 9. build a compact verdict (≤3000 chars) and return it     │  ← all Claude ever sees
└────────────────────────────────────────────────────────────┘
```

**Step 4 is the economic engine.** The `qwen` subprocess burns ~22k tokens loading its
prompt, reading files, thinking, and retrying. **None of it crosses back into Claude's
context.** Claude pays for a 3000-character verdict; Qwen's verbosity stays on Qwen's
side of the process boundary. Since Qwen's tokens are free (local Ollama), the whole
inner loop costs Claude nothing but latency.

**Step 7 is the trust engine.** Qwen has fabricated "all tests pass" for tests it never
ran. So its claim is never read as evidence — a shell command's exit code is. That single
substitution is what makes free-but-unreliable compute usable.

---

## 3. The worker: Qwen Code → Ollama

The subprocess in step 4 is [Qwen Code](https://github.com/QwenLM/qwen-code), the local
model's agentic CLI. Flags that matter:

- `-p "<task>"` — one-shot, non-interactive (no TTY).
- `-o json` — emit a machine-readable stream of `{type: assistant|result|system}` records.
- `--approval-mode <mode>` — the safety dial (see §5).
- `-r <session_id>` — resume a prior conversation with warm context.

Qwen Code is configured entirely by **its own** file, `~/.qwen/settings.json`, which
points it at an Ollama server (here: `qwen3.6:27b-agent` over Tailscale) and holds the
API key. **That file is machine-specific and contains a secret — it is never in this
repo.** `server.py` knows nothing about the model, endpoint, or key; it just runs `qwen`
and reads stdout.

### QWEN.md — the worker's standing rules

Each project has a `QWEN.md` that Qwen Code **auto-loads at the start of every session**.
It carries the rules the worker must always follow: never edit spec files, report
honestly, prefer search over whole-file reads, finish with a structured handoff. Because
it reloads on every call, the rules bind on every call — which is precisely why
delegations are **stateless by default** (a fresh session re-reads the rules; a
long-lived one lets them drift out of context).

---

## 4. Git is the substrate for everything defensive

There is no sandbox. Qwen runs at your full user privilege. The safety model is entirely
git-based, which is why **every workspace must be a git repo**:

| protection | git mechanism |
|---|---|
| **spec guard** | record tracked `*_spec.*` files before; if any differs after, `git checkout -- <file>` reverts it and the attempt fails |
| **pre-dirty precondition** | refuse to run if a spec file is *already* modified — the guard reverts to HEAD and cannot tell your edit from Qwen's, so it would eat your work |
| **blast radius** | `git status --porcelain` + a content hash of each changed file, before vs after → the true list of what changed, immune to Qwen misreporting |
| **rollback** | the tree was clean before, so `git checkout . && git clean -fd` is a complete undo; the server hands you that exact command with a safety note |

`git clean -fd` (never `-fdx` — that would delete the gitignored `venv/`) removes files
Qwen *created*; `git checkout` reverts files it *modified*. Together they walk back any
delegation.

---

## 5. Approval modes — the safety dial (measured, not documented)

`--approval-mode` decides which tools Qwen may use. Behaviour was probed empirically; the
upstream bundle does not document it:

| mode | write files | run shell | use |
|---|---|---|---|
| `plan` | no | no | **any vague task** — also blocks `agent`/`exit_plan_mode` |
| `default` | no | no | never (headless auto-denies everything) |
| **`auto-edit`** | **yes** | **no** | **default for code** |
| `auto` | no | no | never (useless headless, same as `default`) |
| `yolo` | yes | yes | only when running something *is* the task |

The important result is **`auto-edit`**: Qwen can write code but has **no shell**. This is
safe *because the retry loop lives in `server.py`, not in Qwen*. The server runs the gate
and feeds failures back, so Qwen never needs to run anything itself to converge. Measured:
told to use a banned import in `auto-edit`, Qwen failed the gate, read the fed-back error,
and passed on the next attempt — with every shell call it tried denied. Same convergence
as `yolo`, but arbitrary command execution at your privilege is simply unreachable.

---

## 6. The three-tier org: who decides what

```
you (direction, irreversible calls)
  │
  ▼  Agent tool spawns a subagent
qwen-manager  ── a Claude subagent: agents/qwen-manager.md
  │  owns: what to build, the design, the SPEC, the gate,
  │        correcting Qwen's plan, iterating, rollback
  ▼  qwen_delegate()
Qwen  ── owns: how the code is written inside a spec it was handed
```

**`qwen-manager`** is a Markdown file — YAML frontmatter (name, description, allowed
tools) plus instructions. Claude Code reads it and can spawn a *fresh copy of Claude*
that runs those instructions in **its own context window**. That subagent does the
judgment Qwen can't:

1. For a vague task, delegate in **`plan` mode** first (Qwen physically cannot write, so
   it returns options instead of inventing scope).
2. **Verify the plan against the code** — Qwen's investigation is strong but its
   conclusions are often wrong in plausible ways (measured: 3 of 5 options on one real
   plan rested on a misread of control flow).
3. **Decide**, and **write the spec test itself** — this *is* the design decision, because
   the spec pins the names, signatures, and behaviour Qwen must satisfy.
4. Delegate the build in `auto-edit` with the spec as the gate; let the server iterate.
5. **Verify independently** — run the gate itself, read the diff — and report.

Its entire conversation with Qwen happens outside the main Claude's context. The main
loop gets only the final report: `DONE / VERIFIED / CHANGED / DECIDED / NEEDS HUMAN`.

**The division of labour:** Claude decides, `server.py` + a shell command check, Qwen
types. A human is asked only about things genuinely theirs — direction, outward-facing
changes, irreversible actions — never "argparse or click?".

---

## 7. Reliability features in the server

Beyond the core loop, the verdict carries signals learned from real failures:

- **pre-flight verify** — if the gate was *already green* before Qwen ran, a green gate
  after proves nothing. Reported as `success_but_preflight_passed`.
- **gate_suspect** — if the gate emits byte-identical output before and after, nothing
  Qwen did moves it: the gate is broken (bad path, bad quoting), not the code. Bails on
  attempt 1 instead of letting Qwen thrash against an impossible target.
- **blast radius with content hashing** — comparing git *status codes* alone misses a
  file that was already dirty and got edited again; hashing catches it.
- **handoff cross-check** — Qwen is asked to end with `FILES: …`; the server checks that
  claim against the actual git diff and flags a mismatch (`MISREPORT`).
- **context + timing** — reports peak context vs the compaction threshold, and actual
  time vs budget, so the timeout estimate can be calibrated.
- **mode-aware failures** — denied tool calls in a restricted mode are expected (the mode
  denied them), not flagged as suspect.

---

## 7a. The run log — measuring the thing the system exists to do

Every `qwen_delegate` and `qwen_query` call appends one JSON object to
`<cwd>/.qwen-delegate/runs.jsonl`. The point is the **leverage ratio**: free tokens burned
by Qwen against tokens returned into Claude's context. That was the product's headline
claim on the strength of one hand-measured session; the log makes it continuously
measured (first data: **208.6x** across 3 runs).

Each record carries token counts (split `main` vs Qwen's internal memory-extractor
sub-agent, with a `token_source` field marking whether that split is real or unavailable),
peak context, wall time, attempts, tool calls and failures, changed-file count, and
truncated-plus-hashed task and gate text. Counts are accumulated **across attempts** —
`ctx["meta"]` holds only the last one, and the iterate loop is where tokens actually go.

Two invariants this code must not break:

1. **A logging failure must never fail a delegation.** Every write is best-effort; a
   failure logs a stderr warning and returns.
2. **The log must be invisible to git.** `snapshot()`/`blast_radius()` attribute working
   tree changes to Qwen, so a visible log file would be reported as Qwen's own work and
   would trip the dirty-tree precondition. `.qwen-delegate/` holds a `.gitignore`
   containing `*`, which ignores the whole directory including itself; the record is also
   written after every diff is taken.

Logs are **per-project**, because the plugin is used in real projects and the numbers
belong with the code they describe. `~/.qwen-delegate/projects.jsonl` is a **pointer index
only** — paths, no metrics — written by `init-project.sh` at setup and by the server on
first write, so an aggregator can find every project's log. Override its location with
`QWEN_DELEGATE_REGISTRY`.

The gate for all of this is `runlog_spec.py` (34 tests, mutation-tested: 13/13 caught).

---

## 7b. `qwen_investigate` — reading, offloaded

The server exposes a second tool for the cheap half of the work: **understanding a
codebase**. Qwen's tokens are free and it is genuinely good at investigation (glob →
grep → targeted read), so instead of the manager spending its scarce context reading ten
files, it delegates the reading and gets back a compressed map.

Mechanically it is `qwen_delegate`'s inner call with three differences: forced `plan`
mode (read-only, so always safe and no gate needed), a different prompt suffix that
demands a structured map, and no git snapshot (nothing changes). The response is
**MAP / KEY SYMBOLS / CONNECTIONS / ANSWER / VERIFY**, plus a peak-context line.

Two design constraints, both from measurement:

- **Bounded and stateless.** Each call asks a focused question over a few files. A
  "read the whole repo" call pushes Qwen past its compaction threshold, after which it
  fabricates having read things it did not. The response reports peak context and warns
  if compaction likely fired, so an over-broad read is visible rather than silently
  wrong.
- **A lead, not truth.** Qwen's *structure and semantics* are reliable; its *precise
  claims* are not. On an unseen library it produced a perfect function inventory and
  correct composition relationships — and fabricated every line number, with false
  confidence ("confirmed by reading directly"). So the tool asks for grep-able symbol
  names rather than line numbers (it gets names right, numbers wrong), and the VERIFY
  section lists what the caller must confirm against source. The map says *where to
  look*; the manager still reads the load-bearing lines itself. That is the whole
  saving — Qwen turns "read thirty files" into "read three."

## 8. Language-agnostic by design

Nothing in the mechanism is Python-specific:

- The spec guard matches `*_spec.*` and `*.spec.*` (any language: `foo_spec.py`,
  `calc.spec.ts`, `bar_spec.rb`, `baz_spec.go`), overridable per-project in
  `.qwen-delegate.json` → `{"spec_globs": [...]}`.
- The gate is *your* shell command — `pytest`, `npm test`, `cargo test`, `go test`, a
  script. The server only reads its exit code.
- `init-project.sh` detects the test command for common ecosystems and writes `QWEN.md`;
  it refuses non-git projects, because git is the only rollback.

Verified end-to-end on an unseen TypeScript project and an unseen Python library, neither
read beforehand by Claude or Qwen.

---

## 9. Repository layout

```
server.py               the MCP server — the whole broker, stdlib only
runlog_spec.py           gate for the run log + token accounting (Claude-authored)
agents/qwen-manager.md   the manager subagent: judgment, spec authoring, escalation
templates/QWEN.md        per-project worker rules (copied + edited per project)
docs/ARCHITECTURE.md     this file — how it works
docs/FINDINGS.md         the measurements every design decision rests on
install.sh               per-machine setup: register MCP, set timeouts, symlink agent
init-project.sh          per-project setup: detect test cmd, write QWEN.md, require git
```

Written at runtime, not in the repo:

```
<project>/.qwen-delegate/runs.jsonl   per-project run log (self-ignoring dir)
~/.qwen-delegate/projects.jsonl       pointer index of projects that have used the plugin
```

## 10. Lifecycle summary

```
once per machine:   ./install.sh            → registers the MCP server + agent
once per project:   ./init-project.sh <dir> → writes QWEN.md, requires git
per task:           ask Claude to hand a goal to the qwen-manager subagent
                    (or call qwen_delegate directly for already-specified work)
```

Machine config lives in `~/.claude.json` (the MCP registration + 2h wall-clock timeout)
and `~/.claude/settings.json` (the 90-min stdio idle timeout — the real ceiling, since
the server blocks silently in `subprocess.run` for the whole delegation and looks idle).
Qwen's own config and API key live in `~/.qwen/settings.json` and never enter this repo.
