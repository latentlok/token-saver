<!--
Paste this into your project's CLAUDE.md to make delegation the default for mechanical
work. Without it, delegation depends on Claude happening to remember the plugin exists;
with it, the trigger is in context every session.

Ask Claude to add it, or paste the block below yourself — everything between the
begin / end markers, the markers included. The markers let a re-add detect the block
and skip it, so it is never duplicated.

Once installed, the block is MANAGED: on the first session after a plugin update the
setup hook rewrites everything between the markers from this template (the `v:` line
says which version wrote it) and touches nothing outside them.

DELIBERATELY SMALL (~120 tokens). It used to be ~520 tokens restating the `delegation`
skill — trust modes, async, retries, playbooks, approval modes, worktrees — so every
session paid for delegation knowledge whether or not it delegated, and the skill then
loaded on first use and said all of it again.

What is left is only what cannot come from anywhere else:

  - the TRIGGER, which must fire before Claude has decided to look at the tools;
  - the two rules that must hold BEFORE the skill loads, because forgetting either
    costs real work — an uncommitted tree has no rollback, and a hand-re-run green
    gate spends exactly the context this plugin exists to save.

Everything else is in the `delegation` skill, which loads on first use. The skill's
own `description:` line is already resident in every session and already carries the
capability map, which is why this block no longer repeats it.
-->

<!-- qwen-delegate:begin (managed block; delete from begin to end to remove) -->
<!-- v: {version} -->

## Delegating mechanical work

token-saver is installed: a free local model types, a command decides. **Before doing
mechanical work inline, ask — could a command prove this was done?** If yes, delegate
it (`qwen_delegate`); codebase questions go to `qwen_query`, free and read-only. Keep
design, specs, gates, and anything irreversible or outward-facing. **Load the
`delegation` skill before first use** — everything else is in there.

Two rules that must hold before that skill loads:

- **Commit first.** Git is the only rollback; there is no sandbox.
- **The gate decides, never the worker's prose.** Trust the receipt's `STATUS`; never
  re-run a green gate or read the diff to check it.

<!-- qwen-delegate:end -->
