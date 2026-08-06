#!/usr/bin/env python3
"""M4 seam 2: the worker edited a file the caller declared off-limits.

`touch_scope` is a promise about blast radius, and it is checked BEFORE the
no-verify break because the promise holds whether or not a gate was supplied --
it used to be silently unenforced without one.

Two things it must never do, both learned the expensive way:

  * **New files are always allowed.** A scope names what may be MODIFIED; a
    worker that cannot create a file cannot do most jobs.
  * **An unattributed change is never reverted.** Under a proxy that logs the
    worker's writes (C10), a changed file with no logged write belongs to the
    caller or an agent of theirs working the same tree. Reverting those is how
    a caller's concurrent work got destroyed. They are recorded on the scope
    and the attempt is NOT failed for them.
"""

from qd.core.violation import Violation

KIND = "touch_scope"


def check(scope, plan, attempt):
    if plan.touch_scope is None or not attempt.changed:
        return None

    violated, unattributed = [], []
    for path in attempt.changed:
        if path in plan.touch_scope:
            continue
        if path not in scope.pre_tracked:
            continue                      # new files are always allowed
        if scope.hooked and path not in attempt.writes:
            unattributed.append(path)     # somebody else's work on this tree
            continue
        violated.append(path)
    scope.note_scope_unattributed(unattributed)

    if not violated:
        return None

    scope.restore(violated, base=scope.pre_sha)
    names = ", ".join(violated)
    return Violation(
        KIND,
        f"attempt {attempt.n}: TOUCH SCOPE VIOLATION -- edited {names} "
        f"outside scope (auto-reverted)",
        f"You modified files outside the allowed set: {names}. Those files are "
        f"off-limits and have been reverted. Only modify: "
        f"{', '.join(plan.touch_scope)}. You may create new files freely.",
        # A worker that has forgotten the task cannot act on "only modify X".
        rider=True)
