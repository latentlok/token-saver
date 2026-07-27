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
-->

<!-- qwen-delegate:begin (managed block; delete from begin to end to remove) -->
<!-- v: {version} -->

## Delegating mechanical work

This project has the token-saver plugin installed (`qwen-manager` subagent + the
`qwen_delegate` / `qwen_query` MCP tools). A free local model does the typing; you do the
judgment; a command decides whether it worked.

**Before doing mechanical work inline, ask: could I write a command that proves this was
done correctly?** If yes, it belongs to `qwen-manager`, not to you. Spawn it **in the
background** with the goal — not the steps — and carry on with something else. You will
be notified when it reports.

Delegate: bulk or repetitive edits · a rename or signature change across many files ·
adding tests for existing code · boilerplate · codemods · migrations · doc generation ·
fixing every instance of a lint or type error.

Keep for yourself: what gets built and why · the design · the spec and gate that define
correct · anything outward-facing, irreversible, or contested.

Use `qwen_query` for questions about this codebase ("how does X work", "where is Y
handled", "is there already a Z") — it reads and answers on free tokens instead of
spending your context. Treat its answers as leads to verify, not as truth.

**Rules that are load-bearing here, not stylistic:**

- Commit before delegating. Git is the only rollback — there is no sandbox, and the
  worker runs at full user privilege.
- Never accept the worker's own *prose* as evidence — Qwen has claimed tests passed with
  the test runner not installed. The **gate** is the evidence, and the **server runs it**:
  trust the receipt's `STATUS`/`VERIFY` verdict, not Qwen's narration. Do NOT re-run the
  gate yourself — the server already ran it green, so a re-run just respends your tokens on
  a result the receipt already carries. (High-stakes `self` work worth double-checking?
  Run an *independent* audit — different tests — never a re-run of Qwen's own suite.)
- **By default, let Qwen write and grade its own tests (`trust="self"`)** — the cheap path,
  and how this runs unless you're told otherwise. **When you are told to, or the work must
  be right, author the gate yourself (`trust="verified"`)**: then it is `*_spec.*`, and the
  worker can never write or weaken *that* file — auto-reverted if it touches it. (A gate you
  authored is yours alone; a self-graded suite passing is not proof it's correct — see the
  `self` caveat above.)
- A gate you have not tested is a hope. Break the thing it watches and confirm it fails
  before trusting a pass.
- **Tell it where the tests are, once.** Put `"test_dir"` (or the exact
  `"test_command"`) in `.qwen-delegate.json`. Without it the plugin guesses from
  packaging files, and a project it can't place gets a gate that can never pass —
  silently. This is also what makes `trust="self"` work: keep the worker's own
  tests somewhere separate from the files you protect as specs, or they auto-revert
  as fast as it writes them.
- **`STATUS: stopped` means a live limit ended the run — not a worker fault and
  not a code fault.** Nothing was verified and the work on disk is partial. Either
  split the task or raise `burn_budget` / `decode_tps`. Re-running it unchanged
  hits the same wall.
- **`STATUS: compaction_refused` means the task was too big to hold, not that it
  failed.** The worker's history was about to be summarised, so the run was stopped and
  nothing from it is trusted. Do not re-delegate it unchanged — split it into smaller
  units with their own gates.
- **A `STATUS: error` receipt is the worker failing, not a bug here — do not debug it.**
  Truncated output, a dead endpoint, a timeout: the receipt names the cause and the
  setting that fixes it, all of them user-side. Relay it, retry once smaller if it's
  worth it, else do the work inline or hand the line to the user. Reading plugin
  source or probing the endpoint to find out why burns the context this is meant to save.
- **Delegation is asynchronous.** `qwen_delegate` answers in seconds with a run id and
  the path its receipt will land at — start it, do something else, then read that file
  (the response's `WATCH:` line is the wait-for-it one-liner). `wait: true` blocks
  instead, for when you have nothing else to do.
- A red receipt is cheapest to correct with `retry_of=<session_id>` + `retry_message`:
  it replays the stored brief COLD with your correction, no retyping. And when you need
  a value back rather than prose, `result_schema` (on `qwen_delegate` and `qwen_query`)
  makes the worker end with a JSON block the server validates for you.
- A recurring brief belongs in the repo: `brief_file: "playbooks/x.md"` sends a
  markdown document by name (body = task, front matter = gate/scope, `{{slot}}`s from
  `vars`, `chain: true` steps → chain); `amend_brief` on a retry folds the correction
  into the document, versioned by git.
- Prefer `auto-edit`. `scoped` grants a shell and is not a sandbox.
- Bound the blast radius when you know the target: pass `touch_scope=["a.py","b.py"]` and
  any edit to another *existing* file auto-reverts (new files stay allowed). Worth it under
  `self`, which grades the suite but does not constrain which files the worker may change.

<!-- qwen-delegate:end -->

