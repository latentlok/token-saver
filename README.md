# token-saver

**Claude does the thinking. A free model on your own computer does the typing. A test
decides whether it actually worked.**

A plugin for Claude Code. You keep talking to Claude exactly the way you do now — it
just quietly hands the boring, mechanical parts of the job to a model running on your
own hardware, where the tokens are free. Nothing comes back into your Claude session
except a short receipt saying what changed and whether it passed.

## Why

Most coding work isn't thinking — it's typing. Renaming things across forty files,
writing the tests you already know you need, wiring up boilerplate. Claude is expensive
and brilliant; that work needs neither. Worse, Claude has to *read* a lot of your code
before it can touch any of it, and reading is where the bill really comes from.

So split the job. Claude decides what to build and writes down how you'll know it
worked. The local model builds it. Then a plain shell command — your tests — is the only
thing allowed to say "done." Not the model's opinion of its own work, which is
[measurably unreliable](docs/OVERVIEW.md#why-its-built-this-way). If the test fails, the
local model tries again, for free, until it passes. Claude never sees the mess.

## What it actually saves

Measured on real work, not benchmarks:

| | |
|---|---|
| **18–69%** | fewer Claude tokens for the same work, at equal quality |
| **$6.85 → $2.15** | four real changes on a 14k-line codebase (both versions passed the same hidden tests) |
| **−69%** | on changes to existing code — the best case, because that's where Claude would otherwise read the most |
| **324×** | free local tokens burned for every token that came back into Claude's context |

The saving grows with the size of your codebase, because Claude never reads it.

## Requirements

Nothing to `pip install` — the plugin is Python standard library only.

**Required:**

| | why |
|---|---|
| **Python 3** | Runs the plugin. Already on almost every machine. |
| **Claude Code** | The plugin host. |
| **[Qwen Code](https://github.com/QwenLM/qwen-code)** | The local worker. Must run as `qwen` in your terminal. |
| **A model for it to use** | Typically [Ollama](https://ollama.com) on your own machine. Its settings live in `~/.qwen/settings.json` and hold your API key, so they're per-machine and not part of this repo. |
| **git** | Your project must be a git repo. Git is the undo button — there's no sandbox — so the plugin refuses to run anywhere it couldn't roll changes back. `git init` if needed. |
| **bash, grep, awk** | Standard on Linux and macOS. Used to run your tests and check the result. |

**Your project's own test runner** — pytest, npm, cargo, go, whatever you already use.
The plugin runs your tests; it doesn't supply a test framework. It works out the command
for common layouts, and you can state it outright if yours is unusual.

**Optional:**

| | why |
|---|---|
| **[graphify](https://pypi.org/project/graphifyy/)** | A code map so the worker locates code instead of reading it. Optional, but the biggest measured saving (−69%) runs on it. |

Windows isn't supported as a host — the plugin uses file locks and a bash gate script.
WSL works.

## Install

**Step 1 — add the marketplace.** In your terminal:

    claude plugin marketplace add latentlok/token-saver

**Step 2 — install the plugin.**

    claude plugin install token-saver@token-saver

The name looks doubled because it is: this repo *is* its own marketplace. The id is the
plugin name, then `@`, then the marketplace name — and both happen to be `token-saver`.

**Step 3 — restart Claude Code.** That's the install done.

**Step 4 — check it worked.**

    claude plugin list
    claude plugin details token-saver

You should see version `0.4.0`, status `enabled`, and an inventory of **1 MCP server**
(`qwen-delegate`), **2 agents**, and **5 skills**.

**Step 5 — let it check your machine.** The first Claude Code session after installing
runs a one-off check of your local model setup and speaks up only if something will
actually cause trouble. Silence means you're set. To run it yourself at any time:

    /token-saver:doctor

**Step 6 — on Claude Code older than 2.1.203 only**, add this to
`~/.claude/settings.json`, or long jobs time out after 30 minutes:

    "env": { "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT": "7200000" }

### Update

Two commands, in this order — the first refreshes the marketplace, the second upgrades
the plugin:

    claude plugin marketplace update token-saver
    claude plugin update token-saver@token-saver

Use the full `token-saver@token-saver` here. The bare name fails with
`Plugin "token-saver" not found`. Restart Claude Code to apply the new version. The
machine check re-runs once after an update, so a new requirement never passes silently.

### Uninstall

    claude plugin uninstall token-saver@token-saver
    claude plugin marketplace remove token-saver

## Nothing to set up per project

The first time you delegate in a project, the plugin sets that project up itself —
writes the worker's rulebook and finds your test command. Just use Claude normally, or
say so explicitly:

    /offload how does auth flow from the request handler to the token check?
    /offload make the CLI in ./tools usable without PYTHONPATH

If it can't work out how to run your tests, tell it once in `.qwen-delegate.json` —
see [docs/USAGE.md](docs/USAGE.md).

## Read more

- **[docs/OVERVIEW.md](docs/OVERVIEW.md)** — how it works, the architecture, and why every
  guard in it exists
- **[docs/USAGE.md](docs/USAGE.md)** — day-to-day driving, every setting, working from a
  clone, recipes
- **[docs/CHANGELOG.md](docs/CHANGELOG.md)** — what changed and why
- **[docs/FINDINGS.md](docs/FINDINGS.md)** — the evidence behind every number above
