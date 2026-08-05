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

KIND = "strays"


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


def detect(facts, inputs):
    found = _strays(inputs.created, inputs.task, inputs.touch_scope)
    return Finding(KIND, found) if found else None
