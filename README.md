# qwen-delegate

**Claude architects, a free local model builds — measured: the same work for 18–69%
fewer Claude tokens, at equal quality.** On a 14k-line codebase, four real changes
cost $6.85 solo vs **$2.15 delegated** (hidden acceptance tests passed both arms,
909-test regression green). Greenfield −18%, bug-fixing −43%, existing-code changes
−69% — the saving grows with how much *reading* the task would have forced, because
the architect never reads code and its context stays flat while the codebase grows.

Claude decides and specifies. Qwen executes on free local compute. An objective gate
rules on the result. Nothing reaches Claude's context except a compact receipt. A
**trust dial** picks who authors the gate per task: `verified` (you write the check)
to `self` (the worker writes and grades its own tests — max savings, measured
residual risk). Day-to-day workflow: [docs/USAGE.md](docs/USAGE.md); evidence for
every number and guard: [docs/FINDINGS.md](docs/FINDINGS.md).

## Architecture

```mermaid
flowchart TD
    U([you]) -->|vague goal| M

    subgraph claude["Claude Code (expensive tokens, big context)"]
        M["your session, INLINE (the default)<br/>decides · pins behavior or authors a gate · reads the receipt<br/>(qwen-manager subagent = optional isolation)"]
    end

    M -->|"qwen_delegate(task, cwd, verify, mode)"| S

    subgraph mcp["server.py — MCP server (Python, zero deps)"]
        direction TB
        S["JSON-RPC over stdio"] --> PRE["snapshot git tree<br/>run gate once (pre-flight)"]
        PRE --> RUN["run Qwen as subprocess"]
        RUN --> GATE{"run verify<br/>exit 0?"}
        GATE -->|no| FEED["feed real error back<br/>resume session (-r)"]
        FEED --> RUN
        GATE -->|yes| GUARD["spec guard: revert *_spec.*<br/>blast radius: hash the diff"]
        GUARD --> REP["compact verdict<br/>(≤3000 chars)"]
    end

    RUN -->|"qwen -p '…' --approval-mode auto-edit -o json"| Q

    subgraph local["your hardware (free tokens)"]
        Q["Qwen Code CLI"] -->|Tailscale| O["Ollama<br/>qwen3.6:27b-agent"]
        Q -.reads.-> QMD["QWEN.md<br/>(auto-loaded rules)"]
    end

    Q -->|writes code| WT[("git working tree")]
    GUARD -.->|git checkout / clean| WT
    REP -->|"status · changed · rollback"| M
    M -->|"outcome + proof"| U

    style claude fill:#1e3a5f,stroke:#4a90d9,color:#fff
    style mcp fill:#3d2f1e,stroke:#d9a441,color:#fff
    style local fill:#1e3d2f,stroke:#41d98a,color:#fff
```

The same flow in one screen of text:

    you ──▶ your Claude session, INLINE (the measured default)
              ├── vague idea?  qwen, plan mode → options, writes nothing
              ├── decides; pins BEHAVIOR (trust="self") or authors a gate (trust="verified")
              ├── qwen_delegate → the SERVER runs the gate and iterates on free tokens
              └── reads a ~6-line receipt — never the code, never the diff
        ◀── "here's what landed, here's the proof"

Inline is the default because it measured cheaper — a subagent costs a preamble that
medium-sized work doesn't earn back. **qwen-manager** (the bundled subagent) is the
*optional isolation container* for the same loop: long multi-unit grinds whose
iteration would silt up your session, parallel fan-out, or work you want running in
the background while you keep talking. Same discipline either way — one source of
truth in the `delegation` skill.

**Three systems, each doing the one thing it is good at.** Claude holds judgment and a
big context. `server.py` is a dependency-free broker that runs Qwen and enforces the
gate with git. Qwen types code for free on local hardware. The gate — your own shell
command — is the only thing trusted to say "it worked." Full detail in
[docs/HLD.md](docs/HLD.md).

## Why it's built this way

Every protection here exists because the naive version failed, measurably. One sentence
generates all of them — *put each decision where it cannot be faked* — and
[docs/PRINCIPLES.md](docs/PRINCIPLES.md) works through the corollaries. The specific
failures behind them, with numbers, are in [docs/FINDINGS.md](docs/FINDINGS.md):

- **Qwen fabricates.** Day one it wrote correct `fib()` code and reported "all three
  tests pass ✅" with pytest not installed. Its self-report is never evidence, so a
  shell command decides instead.
- **Never let it grade itself — unless you choose to, eyes open.** Given a vague task it
  rewrote the spec tests and reported 38/38 green. Specs are `*_spec.*`, Claude-authored,
  auto-reverted if touched. The trust dial's `self` end deliberately inverts this for
  the savings: the worker's own suite is the gate (non-vacuous-guarded, ratcheted, and
  the receipt stamps `TRUST: self` so a green is never misread as independent).
- **A gate you haven't tested is a hope.** 909 real tests passed a mutation that changed
  *every error message* the library emits. Mutation-test the gate before trusting it.
- **Vagueness is the root cause of everything.** Well-specified, Qwen is genuinely good
  — it implemented a Jinja AST extension first try, correct on cases outside the spec.
  Vague, it invents confident scope, silently changes public APIs, and even breaks rules
  it followed 11 times before. So vague tasks go to plan mode, where writing is impossible.
- **Context length doesn't degrade it — compaction does.** Identical task at 31k and 91k:
  identical results. But at 147k, compaction fired, deleted 64% of history, and it then
  claimed to have read files it never opened.

**Working with this as an agent?** `context/` holds the Claude-facing reference:
[SYSTEM.md](context/SYSTEM.md) (canonical — what it is, how to drive it, what not to
assume) and [TESTING.md](context/TESTING.md) (state + step-by-step test plan).

## Install

This is a **native Claude Code plugin** — no installer, no symlinks, no `claude mcp add`.
The manifest (`.claude-plugin/plugin.json`) plus a bundled `.mcp.json` register the
`qwen-delegate` MCP server and surface the agent, skill, and command by auto-discovery.

**Local / dev** (this repo, live-editable):

    git clone <this repo> ~/projects/qwen-delegate
    claude --plugin-dir ~/projects/qwen-delegate

Edit a component and pick it up in the same session with `/reload-plugins`. A `git pull`
takes effect on the next session (or `/reload-plugins`) — nothing to reinstall.

**Distribution** (an installed copy, from a marketplace):

    claude plugin install token-saver@<marketplace>

Verify the components loaded without starting a session:

    claude --plugin-dir ~/projects/qwen-delegate plugin details token-saver
    # -> MCP servers (1) qwen-delegate · Agents (2) qwen-manager, architect
    #    Skills (3) delegation, architect, lld-principles

The `.mcp.json` sets a per-server `"timeout": 7200000` (2h). On Claude Code **2.1.203+**
that one field caps the wall clock *and* floors the stdio idle timeout to 2h, so no
`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` env var is needed. On older versions, add it as a
fallback (`~/.claude/settings.json` → `"env": { "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT": "7200000" }`),
because the server blocks silently in `subprocess.run` for a whole delegation and would
otherwise idle out at 30 min.

### What it does NOT install

Qwen's own config lives in `~/.qwen/settings.json` and **contains your API key**, so it
is never in this repo. Configure it once per machine:

- **Model provider** — point Qwen at your Ollama/OpenAI-compatible endpoint.
- **Firecrawl** (optional, web access):

      qwen mcp add -s user -e FIRECRAWL_API_URL=http://localhost:3002 --trust firecrawl npx -- -y firecrawl-mcp

### Per-project setup — none required

**Work in a git repo.** That's the only hard rule (`git init` first). Git history is
the undo button — there is no sandbox — so the server refuses to run where nothing
could be rolled back.

**The first delegation sets the project up by itself.** It writes `QWEN.md` — the
worker's standing rules — and detects your test command (`npm test`, `cargo test`,
`pytest`, …). If it can't detect one, the receipt says so instead of guessing; just
tell Claude what the command is. Why the file matters: Qwen re-reads it every
session, and it's what makes rules like "never touch a spec file" actually bind —
measured, without it the worker breaks them. Want different rules? Any hand-written
`QWEN.md` at the repo root wins.

**Optional, but recommended:** add the delegation policy to the project's
`CLAUDE.md` — copy the block from `templates/CLAUDE-snippet.md`, or just ask Claude
to add it. Without it, Claude only delegates when it happens to remember this plugin
exists; with it, every session starts knowing the rule (mechanical work goes to
Qwen). Safe to paste twice — the block is wrapped in begin/end markers, so it never
duplicates.

**Language-agnostic.** The spec guard protects any tracked file matching `*_spec.*` or
`*.spec.*` — `roman_spec.py`, `calc.spec.ts`, `foo_spec.rb`, `bar_spec.go`. Verified on
a TypeScript project with no config. If your project uses a different convention:

    // .qwen-delegate.json
    { "spec_globs": ["tests/contract/*.ts"] }

## Use

In normal conversation Claude delegates **inline** — the default and the cheapest
path. `/delegate <task or question>` is the explicit fire-and-forget door:

    /delegate how does auth flow from the request handler to the token check?
    /delegate make the CLI in ./tools usable without PYTHONPATH

Questions are answered read-only and cheap; a `/delegate`d build is handed to the
`qwen-manager` subagent so it runs off to the side while you keep working.

Or hand a task straight to the subagent the way you'd hand it to an engineer — the goal,
not the steps:

> "The stuff in `qwen-agent-test` is only usable from Python. Make it usable from the
> command line."

It plans, decides the approach, writes the gate, delegates the build, verifies it, and
reports. It escalates only what genuinely needs a human — direction, outward-facing
changes, hard-to-undo actions. Not "argparse or click?"

Or call the tool directly for something already specified:

    qwen_delegate(
      task="...", cwd="/abs/path",
      approval_mode="auto-edit",           # writes, no shell — best default
      verify="./gate.sh && pytest -q",     # exit 0 == real success
      max_iterations=4)

## Layout

    server.py              thin MCP entry (stdio JSON-RPC, zero deps) over qd/
    qd/                    the engine: gate loop, trust dial, worktrees, graph, run log
    specs/                 the engine's own gate suite (13 spec files)
    scoped_hook.py         allowlist hook for scoped mode
    agents/                qwen-manager (isolation container) · architect (L5 loop)
    skills/                delegation (the loop) · architect (L5) · lld-principles
    commands/delegate.md   the front door — /delegate <task or question>
    templates/             QWEN.md worker rules · CLAUDE-snippet.md policy block
    docs/                  USAGE (day-to-day) · HLD/LLD (design) · FINDINGS (evidence)
                           · PENDING (the v4 queue) · archive/ (history)
    context/               agent-facing reference: SYSTEM.md, TESTING.md
    .claude-plugin/        plugin + marketplace manifests; .mcp.json registers the server

## Two tools

- **`qwen_query`** — read-only Q&A about the code. Ask Qwen open-ended questions ("how
  does X work?", "is there already a Y?", "what breaks if I change Z?") -- it reads and
  answers, cannot write. Multi-turn via `session_id` (warm follow-ups). `format='map'`
  gives a structured codebase map. The answer is a lead to verify, not truth.
- **`qwen_delegate`** — the build tool. Runs Qwen against a gate and returns a verdict.

## What the server gives you

| | |
|---|---|
| **verify gate** | a command decides, not Qwen's prose |
| **iterate loop** | failures fed back as real error text; converges on free compute |
| **spec guard** | `*_spec.*` / `*.spec.*` (any language) auto-reverted if touched; refuses to run if one is dirty |
| **blast radius** | content-hashed: what the *filesystem* says changed |
| **pre-flight** | if the gate was already green, says so — the pass proves nothing |
| **gate_suspect** | identical output before/after ⇒ your gate is broken, not the code |
| **rollback** | exact command, with a safety judgment from pre-run state |
| **handoff** | `HANDOFF/FILES/NEXT`, with `FILES` cross-checked against disk |
| **context** | peak vs the compaction threshold |
| **timing** | actual vs budget, so the estimate can be calibrated |
| **run log** | per-project JSONL: tokens burned vs tokens returned, per call |

## The run log

Every call appends one record to `<project>/.qwen-delegate/runs.jsonl` — tokens burned on
free hardware against tokens returned into Claude's context, plus attempts, timing, tools,
and truncated+hashed task/gate text. **Measured leverage so far: 324.1×** (4.16M free
tokens in, ~12.8k returned, across 25 logged runs — a floor, since crashed runs leave
no record).

The directory self-ignores (a `.gitignore` containing `*`), so the log never shows up in
`git status` — which matters, because the server diffs the working tree to attribute
changes to Qwen, and a visible log file would be counted as Qwen's work.

`~/.qwen-delegate/projects.jsonl` indexes which projects have used the plugin — paths
only, no metrics, so an aggregator can find each project's log. Override with
`QWEN_DELEGATE_REGISTRY`.

## Approval modes (measured, not documented upstream)

| mode | write | shell | use |
|---|---|---|---|
| `plan` | no | no | **any vague task.** Also blocks `agent`/`exit_plan_mode`. |
| `auto-edit` | **yes** | **no** | **default for code.** |
| `scoped` | cwd | allowlist | let Qwen run tests to check its own work |
| `yolo` | yes | yes | only when running something *is* the task |
| `default`, `auto` | no | no | never — headless auto-denies |

`scoped` is `auto-edit` plus a **safe shell**: Qwen may run the exact `verify` command,
a read-only/test allowlist (pytest, git status/diff/log, ls, grep…), and any
`shell_allow` patterns you add. Writes stay inside cwd; `rm`/`curl`/network/`git push`/
compound commands are denied and **surfaced back as `ELICITATION`** so you decide
whether to re-delegate with them allowed. Enforced by a PreToolUse hook injected via a
temp settings file (your repo and `~/.qwen` are untouched). Validated: an out-of-cwd
write and an `rm` were both blocked.

`auto-edit` beats `yolo` for writing code: the iterate loop is server-driven, so Qwen
never needs a shell to converge — measured, it dropped a banned import on attempt 2 with
every shell call denied. Arbitrary execution at user privilege is simply unreachable.

## Requirements

Python 3 (stdlib only) · [Qwen Code](https://github.com/QwenLM/qwen-code) on `PATH` ·
Claude Code · a git repo per workspace
