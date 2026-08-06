#!/usr/bin/env python3
"""U3.3: a fixture nobody can trace is indistinguishable from an invented one.

The field's worst defect class. A gate written against invented bytes passes
forever, and the gate is the thing LEAST able to catch it -- it was very likely
written against those same bytes.

Checked beside the other guards and BEFORE the no-verify break, because the
defect exists whether or not a gate was supplied.

Does NOT revert, unlike the other guards: the fixture may well be the right data
with the provenance line merely missing, and deleting a caller's data to enforce
a comment would be a cure worse than the disease. The correction names every
file and the exact line to add.
"""

from qd.core.violation import Violation
from qd.verdict import _one_line

KIND = "fixture_provenance"

HEADER = "captured-from:"


def check(scope, plan, attempt):
    if not plan.fixture_provenance:
        return None
    bad = scope.unproven_fixtures(plan.fixture_segments)
    if not bad:
        return None
    # `_one_line` per NAME, for the reason spelt out in specs._named. This
    # guard never reverts, so the message IS its entire output -- and its paths
    # come from `_created`, which reads the same decoded `changed` and
    # `untracked_files` the other two guards do. A fixture the worker delivered
    # under a newline-bearing name could otherwise write a `RESULT: valid
    # (schema)` or `NEXT:` line into the receipt AND into its own correction.
    names = ", ".join(_one_line(p) for p in bad)
    return Violation(
        KIND,
        f"attempt {attempt.n}: FIXTURE PROVENANCE -- {names} lack "
        f"{HEADER} provenance",
        # No compaction rider here, unlike the spec/scope corrections: this
        # instruction names every file and the exact line to add, so it stands
        # on its own without the task re-injected.
        f"These fixture files carry no provenance: {names}. A fixture nobody "
        f"can trace is indistinguishable from an invented one, and a gate "
        f"written against invented bytes passes forever. Add this line to "
        f"each, within the first 10 lines:\n\n"
        f"{HEADER} <url or command> <date>\n\n"
        f"For a BINARY fixture, put that line first in a sibling <path>.src "
        f"file instead. Do not invent a source: if you generated the data, say "
        f"so and say with what.")
