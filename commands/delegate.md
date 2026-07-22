---
description: Delegate a coding task or question to the free local model under supervision — spend Qwen's free tokens, not your own context. Use for mechanical work a command could prove — bulk or repetitive edits, a rename or signature change across many files, adding tests for existing code, boilerplate, codemods, migrations, doc generation, fixing every instance of a lint or type error. Also for questions about a codebase (how does X work, where is Y handled, is there already a Z) — those are answered read-only and cheaply. Routes builds to the qwen-manager subagent in the background; answers questions directly.
argument-hint: <a task or question about code; name the repo if it isn't obvious>
---

# token-saver — delegate

Request: $ARGUMENTS

You are the **orchestrator**, and the whole point of this command is that the expensive
model (you) spends as few tokens as possible. **Do not do the work yourself.** Push it
onto the free model (Qwen) and spend your tokens only on the design, judgment, and an
honest relay.

**Load the `delegation` skill** — it is the canonical loop (map → spec → delegate →
verify → relay), the gate discipline, and the fan-out/escalation ladders. Follow it.
This command only adds the routing decision below.

Resolve the repo path first (from the request, the current directory, or by asking if
genuinely unclear). Everything below needs an absolute `cwd`.

## 1. Classify the request

**A question about the code** — "how does X work", "where is Y handled", "is there
already a function for Z", "what would break if I change W":
→ Answer it with `qwen_query` (read-only, free). Verify anything load-bearing against
the source yourself, then relay. **Do not spawn the manager for a question.** For a
follow-up, reuse the returned `session_id` — it's a warm conversation.

**A build or change** — "add X", "fix Y", "make Z usable from the CLI", "refactor W":
→ Run the delegation loop. **Default: inline** — you write the spec/gate and call
`qwen_delegate` directly (the skill's loop). Launching a subagent pays a heavy fixed
preamble every time; a bare call does not, so inline is the cheaper default and the
right one for one or a few delegations.
→ **Spawn the `qwen-manager` subagent (background) only when isolation earns that
preamble:** a multi-unit build whose spec/verdict churn would silt up this session, a
parallel fan-out you want running off to the side, or when you want to keep talking to
the user while it grinds. Then hand it the goal (not the steps) and relay its report on
the notification.

**Genuinely ambiguous about WHAT to build, or an irreversible / outward-facing call**
(delete data, change a public API, pick a product direction):
→ Ask the user one concrete question before dispatching. Do not guess direction.

## 2. Dispatch — do not do the work

- **Question:** one `qwen_query`, verify the load-bearing bits, relay. That's it.
  Questions run **synchronously** — they take ~20s and the user is waiting on the answer.
- **Build:** hand the goal to `qwen-manager` **in the background** and let it own the
  spec and the gate. It returns `DONE / VERIFIED / CHANGED / DECIDED / NEEDS HUMAN`.
  Do not read the whole codebase or write the code yourself when a delegated call can
  do it — that is the token you are here to save.

  Builds take minutes, not seconds: a gate-verified delegation runs plan → spec →
  build → iterate → verify. Blocking on that wastes the user's time for no benefit,
  since you are not going to read the work anyway — you are going to read a verdict.
  Fire it, tell the user it is running and roughly what it will do, and carry on with
  whatever else they need. Relay the report when the notification arrives.

  **Never invent or predict what a running manager will report.** If the user asks
  before it lands, say it is still running.
- If the task is large or spans multiple modules, still hand it to one `qwen-manager`;
  it decomposes and drives the sub-steps. You are not the builder.

## 3. Relay honestly

Report what actually landed, not what was claimed:
- what now works, and **the command the manager ran to prove it**;
- what it changed (files, ±lines) and what it decided and why;
- anything it flagged as `NEEDS HUMAN` — surface that to the user as a real question;
- if a delegation failed, say so with the real error, not a paraphrase.

Never launder Qwen's or the manager's self-report. Pass through only what was verified by
a gate or by you. A truthful "the gate failed, here's why" is a good outcome; a confident
summary of unverified work is the one failure this whole tool exists to prevent.

**If a result carries a `SETUP:` line**, the project was just self-configured on its first
delegation. Relay it and act on its two open questions: if it says the test command could
not be detected, ask the user for it and, once they answer, set it by editing the
`- Run tests with:` line in the project's `QWEN.md`; and offer to add the delegation policy
block to their `CLAUDE.md` so future mechanical work routes here automatically. If they say
yes, append the block from the plugin's `templates/CLAUDE-snippet.md` to their `CLAUDE.md`
yourself — **guard on the `qwen-delegate:begin` marker (and the `## Delegating mechanical
work` heading) so a second run never duplicates it**, and never rewrite their existing
content. Remind them the new `QWEN.md` is uncommitted.
