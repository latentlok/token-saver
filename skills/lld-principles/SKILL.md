---
name: lld-principles
description: Low-level design discipline shared by every manager unit — conform to existing patterns, spec-ability as the design-readiness test, pin inter-module contracts once, edge-case discipline, minimal public surface, sound non-gameable specs. Preloaded (always in context) so design discipline is never skipped.
---

# Low-level design principles

Shared design discipline for every manager unit. HLD hands you a bounded scope and the
contracts at its edges; these are how you turn that into a spec Qwen can build against.
The spec *is* the low-level design — so "doing LLD" and "writing the gate" are the same
act. Follow these every time you design a piece, so that N units produce one coherent
codebase, not N styles bolted together.

## 1. Conform to what already exists — design is not greenfield by default

Before you design anything, learn how the codebase already does it:

    qwen_query("How are <this kind of thing> implemented here? Show me the pattern for
      <error handling / config / a similar module>.", cwd=<repo>)

Then design the new piece to **match** that pattern — same layering, same naming
conventions, same error style, same test shape. A clean design that ignores the
established one is a defect: it fragments the codebase, and with multiple units it
fragments it N ways. Reuse existing machinery over inventing parallel machinery (a new
tag built by desugaring into the engine's existing loop node beats hand-rolled codegen).
Only depart from an existing pattern deliberately, and say why.

## 2. The spec is the design — and spec-ability is the readiness test

Your LLD is done when a `<name>_spec.<ext>` pins the piece's **public surface**: module
path, names, signatures, return types, and the edge cases with exact expected values.
Until that spec exists and passes on a correct implementation, the design is not finished
— it is a sketch.

**If you cannot write the spec, the piece is not designed yet.** That is a signal to
think more, or to kick the boundary back up to HLD — never to hand Qwen something vague
and let it guess. "I can spec this" and "I understand this design" are the same
statement.

## 3. Pin every inter-module contract once; both sides honour it

The failure mode with multiple pieces is **contract drift**: each unit passes its own
tests, but nothing composes, because two pieces disagree about a shared data shape. The
fix is not more testing — it is single ownership of the seam. The shape at a boundary
(the token tuple, the request object, the return type) is written **once** and every
side's spec references that one definition. At a boundary between *units*, that
definition belongs to HLD; inside your unit, it belongs to you. Never let two specs
independently describe the same shared shape.

## 4. Spec the edge cases — they are where design lives

The happy path is rarely where a design is right or wrong. Pin, with exact expected
values, the cases that are easy to get wrong: empty/zero/negative, boundaries, the
"teens exception," malformed input, the case the obvious implementation gets subtly
wrong. A gate that tests only the happy path is a gate that will pass wrong code.

## 5. Add only the public surface that was asked for

Every new public name — function, class, exported symbol, error type — is a contract
others can depend on. Adding an unrequested one is scope creep that a passing gate will
not catch (tests check that the asked-for thing works, not that nothing else was added).
Design the minimum public surface that satisfies the requirement. Internal structure —
private helpers, variable names, the algorithm — is the worker's to choose; do not
over-specify it, and do not smuggle extra public surface in under the name of "helpful."

## 6. Make the spec sound and non-gameable

A contradictory or under-constrained spec is not a safe fallback — it gets **gamed to
green**, and the green hides the flaw. Before building against a spec you are unsure of,
sanity-check it read-only, where it cannot be gamed:

    qwen_query("Is <spec> implementable as written, or are there contradictions,
      impossibilities, or assumptions about code that doesn't exist?", cwd=<repo>)

Fix what it surfaces, then build. And where a single-call test could be satisfied by a
trick (fake equality, a hardcoded literal, hidden state), add a property or determinism
check — `f(x) == f(x)`, call it twice, assert an invariant — so the trick fails the gate.

## 7. Design at the altitude of one unit

Your LLD covers the piece HLD gave you, no more. If the design won't fit in one spec —
if it keeps sprawling into sub-designs — that is not a reason to design harder; it is a
sign the boundary is wrong. Surface it to HLD as "this piece wants to be split," with the
seam you'd draw. Keeping a too-large piece whole is how a unit becomes its own monolith.
