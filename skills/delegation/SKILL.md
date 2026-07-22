---
name: delegation
description: Spend Qwen's free local tokens instead of your context — build code against a gate, answer codebase questions, pull docs, map a repo. Use for mechanical/verifiable work a command could prove, and for read-only questions. Routes builds through the qwen_delegate gate loop; questions through qwen_query.
---

# Delegating to Qwen

You have a free local executor (Qwen) behind two MCP tools. The whole point: **its
word is never evidence — a gate decides, and only a short verdict reaches your context.**
One engine, one loop, whether you run it inline or hand it to the qwen-manager subagent.

## The loop

    map → design/spec (you) → delegate (Qwen) → gate verdict → (repeat) → relay

You speak at most three times; everything between is free-side and unseen:

1. **Pre-flight (when unsure of your own spec).** Ask read-only, where Qwen can't game:
   `qwen_query("Is this spec implementable / grounded / contradiction-free?")`. This is
   where your design mistakes surface honestly — a write-capable Qwen games a flawed gate
   to green instead of reporting it.
2. **Build.** `qwen_delegate(task, cwd, verify=<a real gate>, approval_mode="auto-edit")`.
   The gate is a shell command exiting 0 only on true success. The server runs it, feeds
   real failures back, and iterates on free tokens — you are not in that loop.
3. **Relay.** Read the compact receipt (never the diff). On green, **do not read the
   code — the gate already proved it.** Relay the outcome + proof.

## Non-negotiables

- **Always pass `verify`.** No gate = no evidence. Qwen has reported "all tests pass" with
  the test tool uninstalled.
- **Specs are yours, `*_spec.*`, committed before the build.** Qwen's edits to them
  auto-revert. Its own scratch tests are `*_qwen.*` (encouraged, never the gate).
- **Write the minimum spec, pinned to exact behavior and edge cases.** A vague task gets
  confident invented scope; a contradictory spec gets gamed. If you can't write the spec,
  the design isn't done — think more or kick it up, never hand Qwen a guess.
- **Vague task → `approval_mode="plan"`** (read-only; it returns options, cannot write).

## Reading the receipt

`STATUS` decides. Then the deterministic lines, which cost no model tokens and catch what
a green gate can't: `CHANGED` (filesystem truth), `NEW PUBLIC SURFACE` (scope creep —
review the list, not the diff), `GRAPH` (map freshness), `COST`, `ROLLBACK`. `gate_suspect`
means YOUR gate is broken (identical output before/after) — fix it, don't iterate.
`NOTES`/`MISREPORT`/`DENIALS` are leads to check, never trusted.

## Existing codebases: read the map, not the code

Don't read a repo into your context. Query graphify's MCP for the scoped subgraph
("what calls X?"), verify only load-bearing claims against source (INFERRED edges and
semantic summaries are leads; tree-sitter coordinates are trustworthy), then pin the
change as a spec. The receipt's `GRAPH` line tells you if the map is fresh.

## Greenfield = iteration zero

Same loop. Write the HLD, commit every inter-module contract as a `*_spec.*` file
**before any code**, then delegate bottom-up (each unit gated on its own spec + its
dependencies'). Contracts-first is what makes the pieces compose.

## Fan-out (parallel builds)

Pin the contracts, then either:
- **`batch=[{task, verify, ...}, ...]`** — N delegations in ONE call, fanned across
  worktrees server-side. The reliable way to parallelize from one session.
- **`worktree="auto"`** per call — isolates each build on a `qwen/<id>` branch; the
  receipt carries the exact `MERGE:` command. A merge conflict is a design signal (two
  units' contracts overlapped) — escalate, don't force.
Cap is per-endpoint (your hardware); more workers = raise it in the executor profile.

## touch_scope

`touch_scope=["a.py","b.py"]` restricts edits to named pre-existing files (out-of-scope
edits auto-revert); new files stay free. Use it to bound a change to its intended surface.

## Inline vs the manager subagent

Run the loop **inline** for interactive work and small counts. Hand it to the
**qwen-manager** subagent when isolation earns its preamble: a multi-unit build whose
verdicts would silt up this session, parallel fan-out, or work that should run off to the
side while you keep talking to the user.

## Escalation ladder (build won't converge)

Reflexion retries (automatic, in the loop) → best-of-N (`workers=N`) → read the failing
sliver → patch it yourself as last resort. Escalate to the USER only for genuine calls:
direction, outward-facing or hard-to-undo actions, a merge conflict.

## Mutation-test your own gates

After a module's gate first goes green, spend one free `qwen_query` asking Qwen to propose
mutations the spec would miss; apply+judge them with a throwaway harness. Measured: 7/8 of
its proposals survived a hand-tested spec. Adversarial review is read-only, so there's
nothing to game. Green → commit → mutate (never mutate uncommitted work).
