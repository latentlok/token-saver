# Plan — the L5 architect system (v3 execution plan)

Decided with the user 2026-07-22. Executes the premise of
[DIRECTION-v3-architect-model.md](DIRECTION-v3-architect-model.md) at **L5 trust,
deliberately**: that is where the maximum token saving lies, and the saving is the
product. The residual risk of self-grading (measured: a data-loss bug behind a
34/34-green suite, FINDINGS "L5 self-grading") is accepted at L5, not mitigated by
lowering trust; audits exist on demand, outside the loop.

**The premise, in the user's words:** Claude is a software architect. It translates
product requirements to software requirements, then propagates each module down — low
enough that Qwen can do it, high enough to not burn tokens. Goal: a system anyone can
use to leverage open-source models to reduce token costs.

## v2 support evaluation (2026-07-22)

The v2 engine carries this almost as-is:
- **L5 gate works today** with zero server changes: `verify` = the delegate's own suite
  behind a vacuous-pass guard (proven in both scratch probes, `~/scratch/l5-probe-{1,2}`).
- **Capability-slider seam exists:** C1 profile `altitude: "lld"|"hld"` — relayed to the
  skill, never interpreted by the server. Unconsumed until now; the architect skill maps
  it to handoff grain.
- **Any open-source model plugs in** via C7 executors (profiles/endpoints/pricing).
- **Module fan-out exists:** `batch` + worktrees + per-endpoint semaphores.
- **The architect's one cheap check at L5 exists:** read-only `qwen_query` pre-flight.
- **Savings are measurable per run:** run-log v2 tokens/cost (F8/C5).

Gaps → phases below: no architect layer above the engine; `trust` parked at
`"verified"`; the lean-architect teardown (v3 §3) unbuilt; packaging absent.

## Phases

| phase | what | status |
|---|---|---|
| **P1** | **Architect skill** — `skills/architect/` (PRD→SRS→module tree→handoff→verdict loop, altitude rule), `agents/architect.md` shell, `templates/gate_selfsuite.sh`. Claude-side only. | **built (this commit)** |
| **P2** | **Server `trust: "self"`** — unpark the C9 trust field: server auto-generates the own-suite gate (generalized gate_selfsuite), compact receipt, `TRUST:` receipt line. Spec'd per N5, delegable to Qwen. | approved; **paused — user gives the go** |
| **P3** | **Lean architect + slope measurement** — stripped toolset session, deferred schemas, per-module token slope on a real multi-dozen-module L5 build (v3 §8.5). | **needs discussion before any design** |
| P4 | **Packaging for anyone** — executor presets (ollama/llama.cpp/API), install story, quickstart, published benchmark. | later, after the loop is proven |
| — | Overnight runner (queue-drain builds while the user sleeps) | parked by user; do not build |

## Calibration (feeds the skill's altitude rule; grows with every run)

| delegate | grain | scale proven | result |
|---|---|---|---|
| qwen-local | contract (lld) | 3 modules / ~180 LOC | attempt 1 green; 23/25 on post-hoc contract audit |
| qwen-local | outline (hld) | 6 modules / ~400 LOC | attempt 1 green; real design authority (README'd interfaces) |

Known L5 behaviors to design around (FINDINGS, 2026-07-22): plan-then-stall on coarse
tasks (anti-stall line in every task text); self-grade shares the code's blindspot
(accepted residual); contract drift invisible in verdicts (public-surface scan is the
only structural check); vacuous-pass guard required (`unittest discover` exits 0 on an
empty suite).
