<!--
Paste this into your project's CLAUDE.md to make delegation the default for mechanical
work. Without it, delegation depends on Claude happening to remember the plugin exists;
with it, the policy is in context every session.

Ask Claude to add it, or paste the block below yourself — everything between the
begin / end markers, the markers included. The markers let a re-add detect the block
and skip it, so it is never duplicated.
-->

<!-- qwen-delegate:begin (managed block; delete from begin to end to remove) -->

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
- Never accept the worker's own report as evidence. It has claimed tests passed with the
  test runner not installed. Only the gate counts, and you run it yourself.
- Never let the worker write the file that grades it. Gates are `*_spec.*`, authored by
  you, auto-reverted if touched.
- A gate you have not tested is a hope. Break the thing it watches and confirm it fails
  before trusting a pass.
- Prefer `auto-edit`. `scoped` grants a shell and is not a sandbox.

<!-- qwen-delegate:end -->

