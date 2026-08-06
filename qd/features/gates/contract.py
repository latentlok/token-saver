#!/usr/bin/env python3
"""A2.3: the gate was written against THIS version of the contract.

The chain-specific half, and the one nothing else covers. Without it the
pipeline's whole premise -- *the gate was frozen before the implementation* --
is true of the test file and false of the document the test file was derived
from.

Link 1 writes `# contract: <path> @ <digest>` into the test file it commits.
Link 2 reads it back from the shared worktree and refuses if the contract has
moved since. **Not from the receipt:** receipts are text returned to the caller,
with no state channel between links, and in-memory state would work for one
server call and break on retry or resume. The artifact link 1 COMMITS is the
only channel that survives both.

Refuses rather than warns, because a gate written against a different document
is not evidence about this one -- and the whole point of spending link 2 is to
be graded by link 1's gate.
"""

import os

from qd.core import contract

NAME = "contract_pin"


def check(run):
    from qd.features import gates
    path = getattr(run, "contract_path", None)
    tests = getattr(run, "contract_tests", None) or ()
    work_cwd = getattr(run, "work_cwd", None)
    if not (path and work_cwd):
        return gates.proceed()
    try:
        with open(os.path.join(work_cwd, path)) as f:
            now = contract.digest(f.read())
    except OSError:
        return gates.proceed()           # nothing to compare against

    for test in tests:
        try:
            with open(os.path.join(work_cwd, test)) as f:
                pinned_path, pinned = contract.parse_header(f.read())
        except OSError:
            continue
        if not pinned or pinned_path != path:
            continue
        if pinned != now:
            return gates.refuse(
                f"CONTRACT MOVED: {path} is now @ {now}, but the gate in "
                f"{test} was written against @ {pinned}.\n\n"
                "Nothing was built. A gate written against a different version "
                "of the contract is not evidence about this one. Re-run the "
                "step that wrote the gate, or revert the contract.")
    return gates.proceed()
