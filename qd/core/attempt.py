#!/usr/bin/env python3
"""What ONE attempt did. The per-attempt analogue of `core/facts.py`.

`facts.py` answers *what is true about the tree now, against where the run
started*. This answers the same question for one turn of the loop, and exists
for the same reason: the guards each re-derived it, so each could disagree with
the others about what the attempt had changed.

Deliberately tiny. It is not a place to put things a guard happens to need --
what the RUN owns is `RunScope`, what was ASKED FOR is `RunPlan`, and anything
that fits neither is a sign the guard is doing too much.
"""

from typing import NamedTuple


class Attempt(NamedTuple):
    n: int          # this attempt's number, 1-based
    of: int         # the attempt budget (max_iterations)
    changed: list   # paths this attempt changed, against the pre-run snapshot
    writes: list    # worker-attributed writes so far, or [] when not hooked

    @property
    def is_last(self):
        """No budget left, so a correction has nowhere to go."""
        return self.n >= self.of
