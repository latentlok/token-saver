# Plan — building toward the takeaway (v3, second pass)

Re-planned with the user 2026-07-22 (supersedes the first-pass phase table below the
line). **The constant everything builds around:**

> A regular Claude session — greenfield, existing codebase, or bug — can hand work to
> Qwen for free, and end up spending FEWER total Claude tokens than if Claude had built
> or read the code itself.

Binding decisions:
- **Target is the regular working session.** Built-in tools stay loaded; v3 §3's
  biggest bloat row (~35–50k resident toolset) is out of scope by definition. The
  achievable lean-down is everything else in that table: MCP schema residency, receipt
  size, and discipline (no re-verify, terse relay — already in the skills).
- **The trust slider needs both ends as a real mechanism, now** — L0 (architect-authored
  gate, today's `verify`) and L5 (delegate self-grades, server-provided gate). A copied
  gate template is a convention, not a slider: nothing selects it per call, nothing
  records it. Intermediate L1–L4: later.
- **L5 residual risk stays accepted** (FINDINGS "L5 self-grading"); audits are
  on-demand, never in the loop.

## Build phases (no end-to-end product runs; each lands as repo code/docs)

| # | what | v3 anchor | status |
|---|---|---|---|
| **R1** | **Tool list, not tool essays.** `qwen_delegate`/`qwen_query` present as one-line descriptors + minimal param schema; every measured-guidance essay moves into the skills (loaded only when delegating). Target: tools/list payload ~2.5k tokens → ~200. | §3 row "MCP schema" | approved — next |
| **R2** | **Compact receipt grammar.** Green path = `STATUS + CHANGED + NEW PUBLIC SURFACE + COST` + at most one NOTES line (~100–150 tokens). CONTINUE/HANDOFF/TIME/TOOLS/CONTEXT and gate-output tails become red-path/flag-only. Amends the C2 "v1 frozen" clause — v3 §3 named this kill explicitly. | §3 row "verdict receipt" | approved |
| **R3** | **Trust slider, both ends.** Unpark C9 `trust`: `"verified"` (L0 end — architect `verify` required, unchanged) and `"self"` (L5 end — `verify` optional; server runs the delegate's own suite with vacuous-pass guard + MIN_TESTS, generalizing `templates/gate_selfsuite.sh` into the engine). Receipt + run log stamp the level. | §4 | approved |
| **R4** | **Skills catch up.** Architect skill: `trust:"self"` replaces the template instructions; delegation skill: absorbs the essays R1 evicts (approval modes, timing model, session rules). | §3 discipline rows | after R1–R3 |

Order: R1 → R2 → R3 → R4. R2/R3 are spec'd server work per N5 (delegable builds);
R1/R4 are description/skill authoring (architect-side prose).

## Parked (explicitly, with what unblocks each)

- **Routing rule + delegation floor** (when NOT to delegate — the +28% small-change
  loss): needs data → unblocked by measurement.
- **A/B measurement** (token-saver-eval matched pairs, per-feature slope, v3 §8.5):
  user will call the go.
- **Intermediate trust L1–L4** (§4) and the **capability slider** outward (§5).
- **Bug-resolution primitive** (§6): the flow already works as L0 usage (repro gate =
  architect-authored failing test); first-class design later.
- **P2-as-was** is absorbed into R3. **P3 discussion**: the regular-session rows landed
  in R1/R2; the dedicated-agent toolset strip remains open for discussion.
- Packaging (P4) · overnight runner · FINDINGS follow-ups items 2–3 (deterministic
  conformance line, same-model cross-examiner).

## Calibration so far (grows with every run)

| delegate | grain | scale proven | result |
|---|---|---|---|
| qwen-local | contract (lld) | 3 modules / ~180 LOC | attempt 1 green; 23/25 on post-hoc contract audit |
| qwen-local | outline (hld) | 6 modules / ~400 LOC | attempt 1 green; real design authority (README'd interfaces) |

Known L5 behaviors to design around (FINDINGS 2026-07-22): plan-then-stall on coarse
tasks (anti-stall line in task text); self-grade shares the code's blindspot (accepted);
contract drift invisible in verdicts; vacuous-pass guard required.

---

## First-pass record (2026-07-22, superseded above)

P1 (architect skill + agent shell + gate template + this doc) — **built**, commit
`32caed6`. The skill's gate-template section will be rewritten by R4 once R3 lands.
P2 (trust unparking) → absorbed into R3. P3 (lean architect) → split: regular-session
rows into R1/R2, rest parked for discussion. P4, overnight: parked.
