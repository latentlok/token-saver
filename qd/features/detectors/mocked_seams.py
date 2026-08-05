#!/usr/bin/env python3
"""A delivered test that mocks a module this same run changed.

The gate replaced the boundary the run also edited, so the boundary is the one
thing it did not test. The field case: the unit mocked the store, so its suite
never executed the SQL, so a SELECT of a column that never existed shipped
green and died on first live contact.
"""

from qd.core.findings import Finding
from qd.surface.receipt import Block
from qd.gittree import mocked_seams

KIND = "mocked_seams"


def detect(facts, inputs):
    found = mocked_seams(inputs.work_cwd, facts["changed"])
    return Finding(KIND, found) if found else None


def block(data):
    pairs = [f"{t} mocks {m}" for t, m in data]
    return [Block(KIND,
                  f"MOCKED SEAM: " + "; ".join(pairs[:4])
                  + (" ..." if len(pairs) > 4 else "")
                  + " -- the gate replaced a boundary this run also changed, so "
                    "the boundary is the one thing it did not test.", True, 1)]
