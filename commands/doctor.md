---
description: Audit this machine's Qwen executor settings — the per-machine half of the setup that does not travel with the plugin. Checks the settings that decide whether a delegation returns whole work or a truncated fragment, and can write the safe fixes.
argument-hint: (no arguments; add --fix to write the settings-side fixes)
---

# supervised-delegation — doctor

Run the audit and report it. Nothing else — this is a 30-second command, not an
investigation.

```
python3 -m qd.doctor $ARGUMENTS
```

(Run it from the plugin directory, or with the plugin root on `PYTHONPATH`.)

## What it is for

Every other part of this plugin ships in the repo. `~/.qwen/settings.json` does
not: it holds the model name, the endpoint, the context window this plugin's
compaction warnings are computed from, and the output cap that decides whether a
long turn comes back whole. A plugin installed on a second machine inherits none
of it, which is the usual reason the same task succeeds on one box and returns a
truncated fragment on another.

## Reading the output

- **`unrecognised-model-name`** — qwen-code keeps only what follows the last `:`
  when it looks up a model's limits, so an Ollama tag like `qwen3.6:27b-agent`
  reads as `27b-agent`, matches nothing, and takes the 32k default output cap
  instead of its family's 64k. qwen-code sends that cap itself, so **nothing on
  the inference server shows it** — this is exactly the finding that gets
  mistaken for a server-side problem. Fix: `--fix` (sets an explicit
  `maxTokens`), or rename the tag so it normalizes to `qwen3.6-...`.
- **`thinking-against-output-cap`** — reasoning tokens are spent against the same
  cap as the answer.
- **`context-window-unverified`** — `contextWindowSize` is a *declaration*, and
  every `CONTEXT:` / compaction line in a receipt is computed from it. If the
  endpoint serves less (Ollama `num_ctx`, or a window split across
  `OLLAMA_NUM_PARALLEL` slots), receipts will read "safe, well under compaction"
  while the endpoint truncates. Confirm on the box with `ollama show <model>` and
  `ollama ps`, then make the two numbers agree. This one the doctor cannot fix
  for you — it cannot see the server.

`--fix` writes only `maxTokens`, after copying the file to `.bak`. It will not
invent a context window it has not measured.
