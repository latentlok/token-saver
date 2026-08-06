#!/usr/bin/env python3
"""The fixed phase sequence, and the logic inside it that is worth naming.

DESIGN §5. Started, not finished -- and the distinction matters to whoever picks
this up. `core/status.py` and `features/guards/` took the two phases that were
whole ideas; what remains in `_delegate` is largely ORCHESTRATION, which is the
loop's own job and does not improve by being moved.

What DOES belong here is the logic those phases carry that is neither
orchestration nor a feature: decisions with rules of their own, currently
readable only by tracing the loop.

`ratchet_minimum` is the first. The rest of the preflight -- run the gate, share
the verdict across items, time it -- is sequencing, and sequencing that reads
fine where it is.
"""

import re

# Both shapes a runner reports a count in. unittest prints "Ran 12 tests",
# pytest prints "12 passed"; a project may run either, and a ratchet that
# understood one would silently not ratchet under the other.
_COUNT = re.compile(r"Ran (\d+) tests?|(\d+) passed")


def ratchet_minimum(preflight_out):
    """How many tests a self-written gate must require, given what already passes.

    The problem it solves: under `trust="self"` the server writes the gate, and
    an existing suite is ALREADY GREEN. A gate that just runs it proves nothing
    about the new work, and every later feature would come back
    `success_but_preflight_passed` -- a real delivery reported as a vacuous one.

    So the gate binds on the DELTA: require more tests than the preflight found,
    and the preflight re-runs red, which is what makes the eventual pass mean
    something.

    **Summed across files, not taken from the first.** A multi-file suite prints
    one count per file, and a ratchet set from the first line would demand one
    more test than the first FILE contains -- a threshold the suite already
    clears, which is the vacuous pass this exists to prevent, restored by the
    fix meant to remove it.
    """
    found = sum(int(a or b) for a, b in _COUNT.findall(preflight_out or ""))
    return found + 1
