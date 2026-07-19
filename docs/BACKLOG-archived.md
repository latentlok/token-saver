# Archived backlog — token-saver / qwen-delegate

Archived 2026-07-19 while pivoting to the **knowledge-graph / existing-codebase** direction,
which we judged matters more. Shipped before archiving: #12 (manager stops reading Qwen's
code), #22 (prompt + tool-schema trim, `baf0d21`), #24 (Reflexion, `d0202d2`), #26 (best-of-N
`workers`, `3a849c5`). These pending items are parked, not cancelled — pull any back if the
new direction needs it.

## Context-cost / structural
- **#13 Flatten to one Claude context** — RECONSIDERED: blanket-flatten pollutes the main
  context; keep the isolated subagent but lean, flatten only for fire-and-forget/batch.
- **#22 (remainder) Cold-start context trim** — prompt+schema trim landed; remaining: load
  the `lld-principles` skill on-demand, prune the default toolset in the subagent.
- **#14 Sealed / trimmed verdict return** — shrink Qwen's verdict blob back to the manager.

## Trust / rigor
- **#15 Rigor dial (0–3), hard gate floor** — cascade threshold dialing reflexion depth
  (`max_iterations`) + best-of-N breadth (`workers`); never skips the gate.
- **#16 Risk-scaled gate effort + who authors the gate.**
- **#17 Earned trust from the run log.**
- **#18 Adaptive per-run rigor from server signals.**

## Loop / escalation
- **#19 Server-side retry ladder** — largely subsumed by #24 (reflexion) + #23.
- **#23 Bounded failure-report escalation** (snippet + manager hint) — partly in the manager
  prompt's step-5 ladder + terminal rung.

## Scale / execution
- **#20 Batch fire-and-forget for N similar items** — amortize the fixed preamble across a batch.
- **#27 Parallel execution for best-of-N** (concurrent worktrees) — N× wall time -> ~1×.
- **#21 Ground-truth gate templates.**

## Measurement
- **#25 Three-way cost experiment** — free-local+heavy vs mid-tier-paid+light vs frontier-solo,
  in orchestrator-token terms (~20 tasks, mechanical -> open-ended).
