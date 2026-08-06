# token-saver

Claude plans the work and decides what "correct" means. A free local model does the
typing. A command you choose decides whether it worked. You read a short receipt
instead of a diff.

For developers already using Claude Code who want mechanical work — bulk edits,
renames, test scaffolding, codemods — done on someone else's tokens.

## Prerequisites

- `python3` — **standard library only. There is nothing to `pip install`.**
- `git`, and every project you delegate into must already be a git repo.
- The `qwen` CLI on your `PATH`.
- A model endpoint the `qwen` CLI is configured against. Those settings live on your
  machine and do not ship with the plugin.

## Install

In Claude Code:
```
/plugin marketplace add latentlok/token-saver
/plugin install token-saver@token-saver
/reload-plugins
```

`/reload-plugins` is not optional. Without it, older Claude Code versions do not pick
up the install in the session you ran it in.

## Update

**Third-party marketplaces do not auto-update by default.** You will sit on the
version you installed until you run:
```
/plugin marketplace update token-saver
```

## First thing to do

1. Check the machine half of the setup — the one step nothing can do for you:

   ```
   /token-saver:doctor
   ```

2. Ask Claude to add token-saver's block to this project's `CLAUDE.md`, and say yes
   when it offers. Nothing installs it uninvited. It is optional; skipping it changes
   nothing except that delegation stops being automatic.

3. Commit your work first — git is the only undo — then ask for something mechanical:

   ```
   /token-saver:offload add tests for the functions in src/parser.py
   ```

## Read next

`AGENT.md` — the reference for an agent driving the tool.
`ARCHITECTURE.md` — how it works, in one page.
