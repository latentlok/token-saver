#!/usr/bin/env python3
"""Created files the task never names (U4.3).

A delegation that leaves a scratch script, a second copy of a module or a debug
dump behind reads exactly like one that did not: the gate is green either way
and CHANGED lists the file without judgement.

Moved here from `qd.engine._strays` in step 2, unchanged. Both of its non-fact
inputs come from the brief (`task`, `touch_scope`), which is why this detector
in particular could never have been `facts -> Finding`.
"""

import os

from qd.core.findings import Finding
from qd.surface.receipt import Block

KIND = "strays"

# WHERE this finding renders. Declared by the detector so adding one is a
# file plus a line in DETECTORS -- placement is the size cap's tie-break
# among equal priorities, so it cannot be left to registration order.
REGION, SLOT = "EARLY", 10


def _strays(created, task, touch_scope):
    """Named in the task (by path OR basename) or listed in touch_scope means
    expected, not debris. `*_qwen.*` files are excluded -- they are the
    sanctioned self-test scratch convention, already surfaced by the C8
    prefilter's NOTES line.
    """
    text = task or ""
    scope = set(touch_scope or [])
    out = []
    for p in created:
        if "_qwen." in p or p in scope:
            continue
        base = os.path.basename(p)
        if p in text or (base and base in text):
            continue
        out.append(p)
    return out


def detect(facts, scope, plan):
    found = _strays(scope.created, plan.task, plan.touch_scope)
    return Finding(KIND, found) if found else None


def block(data):
    """The receipt line. Drop priority 1 is shared with the all-green ADVISORY
    block; the cap's drop loop sorts stably, so on a tie the block appended
    FIRST goes first -- advisory-green, whose absence costs the least.
    """
    return [Block(KIND,
                  f"STRAYS: {len(data)} file(s) not named in the task: "
                  + ", ".join(data[:6]) + (" ..." if len(data) > 6 else "")
                  + " -- worker debris; review or rm.", True, 1)]
