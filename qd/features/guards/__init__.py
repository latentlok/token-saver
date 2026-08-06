#!/usr/bin/env python3
"""The things that can fail an ATTEMPT. Frozen by specs/guards_spec.py.

The third instance of the same shape, and the one that finally justifies the
first two. `features/detectors/` observe a finished run; `features/gates/` refuse
one before it starts; **guards fail one attempt and send the worker back with a
correction.**

    a gate     refuses the RUN         -- nothing is built
    a guard    fails the ATTEMPT       -- the worker is told and tries again
    a detector reports on the RESULT   -- nobody is stopped

Why they were worth separating from the loop. Each guard was ~50 lines inside
`_delegate`'s attempt loop, and every one repeated the same five steps: detect a
violation, revert it, write a trail line that fails the attempt, compose a
correction for the worker, then `continue` or `break` on the attempt budget.
Only the first and fourth differ. The rest was copied four times, and a rule
copied four times is one edit from being true in three places.

**Control flow stays in the loop.** A guard cannot `continue` a loop it does not
own, so it RETURNS a `Violation` and the loop decides. That is what makes the
retry-or-give-up rule exist once instead of four times.

**Guards are NOT pure, and detectors are.** Most of them revert the offending
paths, which is the whole point -- a spec edit that is merely reported is a spec
edit that stands. The asymmetry is deliberate and is why this is a third
directory rather than a flag on the other two.
"""

from qd.core.violation import Violation  # noqa: F401  (re-exported)

from . import fixtures

GUARDS = (fixtures,)


def first(scope, plan, attempt):
    """The first guard that objects, or None.

    FIRST, not all: an attempt that violated the spec guard AND left an unproven
    fixture is a spec violation. The worker gets one correction at a time,
    because a message listing every complaint at once is one the model triages
    and this project has already measured what a wall of instructions does to a
    27B worker.

    A guard that RAISES is skipped, the detectors' rule again -- a broken guard
    must not fail an attempt that might be fine. The stakes differ from a
    detector's, though: a guard failing OPEN means a violation goes unenforced,
    so anything skipped here is a protection silently not applied. That is why
    `guards_spec` pins each guard's detection separately from this loop.
    """
    for guard in GUARDS:
        try:
            found = guard.check(scope, plan, attempt)
        except Exception:
            continue
        if found is not None:
            return found
    return None
