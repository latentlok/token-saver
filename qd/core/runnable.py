#!/usr/bin/env python3
"""A runnable is one Run, or a ChainOfRuns. Frozen by specs/runnable_spec.py.

Step 7 (Composite). An earlier assessment in DESIGN §8.1 recommended against
this, on the grounds that Composite treats leaf and composite uniformly while
a batch and a chain are deliberately NOT uniform -- a batch is unordered and
parallel, a chain is ordered and shares one worktree.

**That objection was partly wrong and this module is the correction.** Composite
does not require that nesting be ALLOWED; it requires that both kinds answer the
same question. Refusing to nest is a rule about CONSTRUCTION, and moving it here
makes it a property of the type rather than a string the dispatcher returns at
runtime -- which is the actual thing the design asked for.

What survives from the objection: the two are not interchangeable in BEHAVIOUR,
so this deliberately does not give them a shared `.execute()`. It gives them a
shared shape -- `.links`, `.is_chain` -- so a caller can ask what it is holding
without a type check, and a GATE can be handed a whole chain and read every link
before any of them runs (G2, which had nowhere to attach until now).

Execution stays where it is. `run_chain` owns the worktree sharing, the
between-link commits and the handoff forwarding, and none of that is improved by
being reached through a method.
"""

from collections import namedtuple

_Runnable = namedtuple("Runnable", "links is_chain")

NEST_REFUSAL = (
    "a batch item may not contain `batch` -- nesting is one level. Flatten the "
    "items, or use `chain` inside the item for an ordered pipeline. Nothing was "
    "run.")


class NestingRefused(ValueError):
    """Raised at CONSTRUCTION, which is the whole point of step 7.

    Nesting is refused because a batch inside a batch item says nothing `batch`
    does not already say, and would make the receipt's structure depend on how
    deeply the caller happened to nest. That reason has not changed -- what
    changed is where it is enforced.
    """


def of(args):
    """Classify one item. Raises NestingRefused rather than returning a receipt.

    A dispatcher that returns an error STRING for a structural mistake has made
    the caller's shape into control flow, and every caller must then remember to
    check. An exception at construction cannot be forgotten.
    """
    if not isinstance(args, dict):
        return _Runnable((args,), False)
    if args.get("batch"):
        raise NestingRefused(NEST_REFUSAL)
    chain = args.get("chain")
    if chain:
        return _Runnable(tuple(chain), True)
    return _Runnable((args,), False)
