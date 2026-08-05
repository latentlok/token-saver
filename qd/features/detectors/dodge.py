#!/usr/bin/env python3
"""Skip/xfail markers ADDED to delivered test files during this run (U4.2).

How a red suite becomes a green receipt without the failure being fixed. A skip
added in the same run that delivers the tests is exactly the case where the
receipt is otherwise indistinguishable from honest work.

Compared against the T0 snapshot, not the current tree: a marker that was
already there is not this run's doing.
"""

from qd.core.findings import Finding
from qd.gittree import dodge_markers

KIND = "dodge"


def detect(facts, inputs):
    found = dodge_markers(inputs.work_cwd, inputs.pre_sha_full,
                          inputs.pre_status)
    return Finding(KIND, found) if found else None
