---
name: lld-principles
description: Low-level design discipline — conform to existing patterns, spec-ability as the design-readiness test, pin inter-module contracts once, edge-case discipline, minimal public surface, sound non-gameable specs. Loaded when you design a spec (preloaded into the architect); read on demand from the manager.
---

# Low-level design principles

The spec *is* the low-level design — "doing LLD" and "writing the gate" are the same act.
Follow these every time you design a piece, so N units produce one coherent codebase, not
N styles bolted together.

## 1. Conform to what already exists — design is not greenfield by default

Before designing, learn how the codebase already does it — `qwen_query("How is <this kind
of thing> implemented here? Show the pattern for <error handling / config / a similar
module>.", cwd=<repo>)` — then **match** that pattern: layering, naming, error style, test
shape. A clean design that ignores the established one fragments the codebase N ways across
N units. Reuse existing machinery over inventing parallel machinery. Depart from a pattern
only deliberately, and say why.

## 2. The spec is the design — and spec-ability is the readiness test

Your LLD is done when a `<name>_spec.<ext>` pins the **public surface**: module path,
names, signatures, return types, and edge cases with exact expected values. Until that spec
exists and passes on a correct implementation, the design is a sketch. **If you cannot
write the spec, the piece is not designed yet** — think more, or kick the boundary back to
HLD; never hand Qwen something vague and let it guess.

## 3. Pin every inter-module contract once; both sides honour it

The multi-piece failure mode is **contract drift**: each unit passes its own tests but
nothing composes, because two pieces disagree about a shared shape. The fix is single
ownership of the seam, not more testing — a boundary shape (token tuple, request object,
return type) is written **once** and every side's spec references it. Between *units* it
belongs to HLD; inside your unit, to you. Never let two specs independently describe the
same shared shape.

## 4. Spec the edge cases — they are where design lives

The happy path is rarely where a design is right or wrong. Pin, with exact expected values,
the cases easy to get wrong: empty/zero/negative, boundaries, the "teens exception,"
malformed input, the case the obvious implementation gets subtly wrong. A gate that tests
only the happy path will pass wrong code.

## 5. Add only the public surface that was asked for

Every new public name — function, class, exported symbol, error type — is a contract others
can depend on; adding an unrequested one is scope creep a passing gate won't catch (tests
check the asked-for thing works, not that nothing else was added). Design the minimum public
surface that satisfies the requirement. Internal structure — private helpers, variable
names, the algorithm — is the worker's to choose; don't over-specify it, and don't smuggle
extra public surface in as "helpful."

## 6. Make the spec sound and non-gameable

A contradictory or under-constrained spec gets **gamed to green**, and the green hides the
flaw. Before building against an unsure spec, sanity-check it read-only where it can't be
gamed — `qwen_query("Is <spec> implementable as written, or are there contradictions or
assumptions about code that doesn't exist?", cwd=<repo>)` — fix what it surfaces, then
build. Where a single-call test could be satisfied by a trick (fake equality, a hardcoded
literal, hidden state), add a determinism check — `f(x) == f(x)`, call it twice, assert an
invariant — so the trick fails the gate.

## 7. Design at the altitude of one unit

Your LLD covers the piece HLD gave you, no more. If the design won't fit in one spec — if it
keeps sprawling into sub-designs — that's a sign the boundary is wrong, not a reason to
design harder. Surface it to HLD as "this piece wants to be split," with the seam you'd
draw. Keeping a too-large piece whole is how a unit becomes its own monolith.
