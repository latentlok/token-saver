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
from qd.verdict import _one_line

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

    # WHAT CAME BACK, not what was asked for. `scope.restore` returns the paths
    # it could NOT put back and this call used to discard them, so the sentence
    # a caller reads to decide whether their blast radius held said
    # "(auto-reverted)" whatever had happened -- the third instance of the bug
    # e35ecbb and 04ba452 fixed in the spec and brief guards.
    #
    # It differs from those two in having a second layer (`scope.restore` files
    # the failures on `ctx["unrestorable"]`, which `render` reports), and the
    # second layer was ALSO wrong: it named the snapshot cap as the cause of a
    # list fed by several. Driven through this function on a real tree, of the
    # four routes into `unrestored`:
    #
    #   over the snapshot cap   APPLIES -- and is the only one for which "too
    #                           large to snapshot" was a true sentence
    #   worker `git add`s its   DOES NOT APPLY: the `pre_tracked` test above
    #     own new file          skips it -- new files are always allowed. That
    #                           route exists for the spec guard because
    #                           `spec_files()` is a LIVE `git ls-files`, so a
    #                           worker can add a path into the protected set;
    #                           `pre_tracked` is frozen at T0, so it cannot
    #                           here. (Tracked-at-T0-but-absent-at-base is
    #                           covered too: staged-new is dirty at T0, so the
    #                           byte snapshot restores it.)
    #   replaced by a directory APPLIES -- `open(full, "wb")` raises
    #   sabotaged, chmod 444    APPLIES -- same raise
    unrestored = scope.restore(violated, base=scope.pre_sha)
    # The complement, because `restore_paths` PARTITIONS the list it is given:
    # every path is either restored or reported, so the successes are exactly
    # what is not in `unrestored`. Derived rather than returned, so this cannot
    # disagree with what the scope recorded.
    restored = [p for p in violated if p not in unrestored]

    # `_one_line` per NAME, for the reason spelt out in specs._named: since
    # paths were decoded (f75572a) a newline in a filename is a real line
    # break, and both slots below are places the worker does not own -- `trail`
    # is the receipt the CALLER reads, `prompt` goes straight to the model. A
    # forged `RESULT: valid (schema)` or `NEXT:` line through either is a fact
    # the reader did not write. Applied to `plan.touch_scope` too: that half is
    # the CALLER's text rather than the worker's, so it is not the finding, but
    # the same slot cannot hold two rules and the guard costs nothing.
    # MESSAGE ONLY -- `scope.restore` above got the real, whole paths.
    def named(paths):
        return ", ".join(_one_line(p) for p in paths)

    names = named(violated)
    if unrestored:
        # The two halves named SEPARATELY rather than under one verdict word,
        # the shape the spec guard settled on: on a partial revert either word
        # is false about half the set, and the reader's next action is opposite
        # in the two cases.
        outcome = f"NOT REVERTED, still on disk: {named(unrestored)}"
        if restored:
            outcome = f"reverted {named(restored)}; " + outcome
        state = f"{named(restored)} have been reverted. " if restored else ""
        state += (f"{named(unrestored)} could NOT be put back and still hold "
                  f"your changes -- they are outside the allowed set and this "
                  f"run does not own them.")
    else:
        outcome = "auto-reverted"
        state = "Those files have been reverted."
    return Violation(
        KIND,
        f"attempt {attempt.n}: TOUCH SCOPE VIOLATION -- edited {names} "
        f"outside scope ({outcome})",
        f"You modified files outside the allowed set: {names}. Those files are "
        f"off-limits. {state} Only modify: "
        f"{named(plan.touch_scope)}. "
        f"You may create new files freely.",
        # A worker that has forgotten the task cannot act on "only modify X".
        rider=True)
