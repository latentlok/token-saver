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

## Before you install

1. **Python 3** — already on almost every machine.
2. **Claude Code.**
3. **[Qwen Code](https://github.com/QwenLM/qwen-code)** — the local worker. It must run
   as `qwen` in your terminal.
4. **A model for Qwen to use** — typically [Ollama](https://ollama.com) on your own
   machine. Qwen's settings live in `~/.qwen/settings.json` and hold your API key, so
   they are not part of this repo. Set that up once per machine.
5. **Your project must be a git repo.** Git is the undo button — there's no sandbox — so
   the plugin refuses to run anywhere it couldn't roll changes back. `git init` if needed.

## Install

There's no installer and nothing to build. Clone it, and point Claude Code at it:

    git clone https://github.com/latentlok/token-saver.git ~/projects/token-saver
    claude --plugin-dir ~/projects/token-saver

That's it. Claude Code finds everything by itself.

Check that it loaded, without starting a session:

    claude --plugin-dir ~/projects/token-saver plugin details token-saver

You should see **1 MCP server** (`qwen-delegate`), **2 agents**, and **4 skills**. If you
edit the plugin, `/reload-plugins` picks it up in the same session.

To install a published copy from a marketplace instead:

    claude plugin install token-saver@<marketplace>

**On Claude Code older than 2.1.203**, also add this to `~/.claude/settings.json`, or
long jobs will time out after 30 minutes:

    "env": { "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT": "7200000" }

## Nothing to set up per project

The first time you delegate in a project, the plugin sets that project up itself —
writes the worker's rulebook and finds your test command. Just use Claude normally, or
say so explicitly:

    /offload how does auth flow from the request handler to the token check?
    /offload make the CLI in ./tools usable without PYTHONPATH

## Read more

- **[docs/OVERVIEW.md](docs/OVERVIEW.md)** — how it works, the architecture, and why every
  guard in it exists
- **[docs/USAGE.md](docs/USAGE.md)** — day-to-day driving, the trust dial, recipes
- **[docs/FINDINGS.md](docs/FINDINGS.md)** — the evidence behind every number above
