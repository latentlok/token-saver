#!/usr/bin/env python3
"""A4: every clause of the contract has a test naming it.

**A gate on link 1, not an end-of-run note.** An uncovered clause is knowable
the moment link 1 finishes -- before anyone pays for link 2 -- so it fails the
attempt and the worker is told which clause is missing. The failure lands where
the fix belongs, which is usually a vague clause rather than a lazy worker.

It also supplies link 1's missing floor. `_SELF_GATE`'s `min_tests` does not
apply there (link 1 is graded by the red gate), so without this ONE weak test
satisfying one clause is a green link 1. "Every clause covered" is a better
floor than a test count anyway, because it is tied to what was ASKED rather than
to volume.

**What this cannot do, stated rather than implied.** The PRESENCE of a `C2` tag
is a grep. Whether the test tagged `C2` actually asserts C2 is the worker's
claim, and a test tagged `C2` asserting something adjacent reads as covered.
There is no mechanical fix; the receipt echoes the clause beside its test so a
human can spend five seconds on it.
"""

import os

from qd.core import contract
from qd.core.violation import Violation

KIND = "uncovered_clauses"


def check(scope, plan, attempt):
    if not plan.contract_path:
        return None
    try:
        with open(os.path.join(scope.work_cwd, plan.contract_path)) as f:
            doc = f.read()
    except OSError:
        return None                      # no contract to be uncovered against
    wanted = contract.clauses(doc)
    if not wanted:
        return None                      # a contract with no clauses asks nothing

    seen = set()
    for path in attempt.changed:
        if not _is_test(path):
            continue
        try:
            with open(os.path.join(scope.work_cwd, path)) as f:
                seen.update(contract.covered(f.read(), wanted))
        except OSError:
            continue

    missing = [c for c in wanted if c not in seen]
    if not missing:
        return None
    names = ", ".join(missing)
    return Violation(
        KIND,
        f"attempt {attempt.n}: UNCOVERED -- {names} "
        f"({len(wanted) - len(missing)}/{len(wanted)} clauses covered)",
        f"The gate does not cover every clause of the contract. Uncovered: "
        f"{names}. Add a test for each, and name the clause it covers in the "
        f"test's name or a comment on it (for example `def test_x():  # {missing[0]}`). "
        f"If a clause cannot be tested as written, stop and say which and why "
        f"instead of tagging a test that does not assert it.",
        rider=True)


def _is_test(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem.startswith("test_") or stem.endswith("_test")
