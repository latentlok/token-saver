<!--
Paste this into your project's CLAUDE.md to make delegation the default for mechanical
work. Without it, delegation depends on Claude happening to remember the plugin exists;
with it, the policy is in context every session.

Ask Claude to add it, or paste the block below yourself — everything between the
begin / end markers, the markers included. The markers let a re-add detect the block
and skip it, so it is never duplicated.

Once installed, the block is MANAGED: on the first session after a plugin update the
setup hook rewrites everything between the markers from this template (the `v:` line
says which version wrote it) and touches nothing outside them. That is how a new
capability reaches the agents that would otherwise never hear about it.

The block is a ~350-token capability map, resident in every session — the measured
details live in the `delegation` skill, which loads only when a session delegates.
-->

<!-- qwen-delegate:begin (managed block; delete from begin to end to remove) -->
<!-- v: {version} -->

## Delegating mechanical work

token-saver is installed: a free local model types, you judge, a command decides.
**Before doing mechanical work inline, ask: could a command prove this was done?**
If yes, delegate — `qwen_delegate`, or the `qwen-manager` subagent for long
multi-unit grinds: bulk edits, renames, codemods, tests-for-existing, boilerplate,
lint sweeps. Keep design, specs, gates, anything irreversible or outward-facing.
Codebase questions go to `qwen_query` (free, read-only; answers are leads, not
truth). Load the `delegation` skill before first use.

- **Commit first** — git is the only rollback; no sandbox.
- **The gate decides, never the worker's prose**: the server runs `verify`; trust
  the receipt's STATUS. Never re-run a green gate or read the diff.
- `trust="self"` is the default (worker grades its own suite). Work that must be
  right: `trust="verified"` + your own `*_spec.*` gate — the worker can never edit
  it, and an untested gate is a hope: break the thing it watches once and confirm
  it fails. Tell it where tests live once (`test_dir` in `.qwen-delegate.json`).
- **Async**: the response is a run id + receipt path — do other work, read the file
  when it lands (its `WATCH:` line waits on it; `wait: true` blocks).
- `stopped` / `compaction_refused` = task too big — split it; a rerun hits the same
  wall. `error` = the executor, not this repo — relay it, don't debug.
- Red receipt → `retry_of=<session>` + `retry_message` (replays the brief cold).
  Recurring brief → `brief_file: "playbooks/x.md"`, a git-versioned document (front
  matter = gate/scope, `{{slots}}` from `vars`, `chain: true` steps, `amend_brief`
  folds corrections in). Value back → `result_schema`.
- Prefer `auto-edit`; `touch_scope=[...]` bounds edits to named files (new files
  stay allowed); `scoped` is a shell allowlist, not a sandbox.

<!-- qwen-delegate:end -->
