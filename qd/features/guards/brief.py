#!/usr/bin/env python3
"""U6: the worker edited the document that defines its own task.

The same offence class as a spec edit -- rewriting the thing you are graded
against -- which is why it lands on the same status rather than inventing a new
one. A brief the worker can edit is a brief that says whatever makes the run
pass.

Compared by CONTENT against the post-amendment capture, never by mtime: an
amendment rewrites the file legitimately, and comparing against pre-amendment
bytes would accuse the engine of the edit it just made itself.

Carries the same C10 attribution split as the spec guard: an unattributed change
is a caller editing their own document on the same tree -- reported, never
reverted.

**A revert that FAILED must not read as one that worked.** This guard called
`restore_paths` for its effect and threw away the `(restored, unrestored)` pair
it returns, then reported "(auto-reverted)" whatever had happened -- the same
defect e35ecbb fixed one guard over, and with less behind it: that one's
`scope.restore` at least records failures on `ctx["unrestorable"]`, while this
one bypasses the scope, so measured end to end the receipt said reverted, the
unrestorable list was empty, and the brief on disk read HIJACKED. It matters
longer than the run: the document is re-read from disk by name at CALL time, so
a brief left holding the worker's text briefs the NEXT delegation too.
"""

from qd.core.violation import Violation
from qd.gittree import file_sha, restore_paths

KIND = "playbook_edited"


def check(scope, plan, attempt):
    if not plan.brief_path:
        return None
    if file_sha(scope.work_cwd, plan.brief_path) == scope.brief_sha0:
        return None

    if scope.hooked and plan.brief_path not in attempt.writes:
        fresh = scope.note_spec_unattributed([plan.brief_path])
        if not fresh:
            return None
        return Violation(KIND, None, None, False, (
            f"attempt {attempt.n}: PLAYBOOK CHANGED (unattributed) -- "
            f"{plan.brief_path} differs from its pre-run content with no "
            f"logged worker write; NOT reverted",))

    # What came back, not what was asked for -- the same discarded return that
    # e35ecbb fixed in the spec guard, one guard over. `restore_paths` has
    # always reported the paths it could not put back; this call dropped the
    # pair and the line below said "(auto-reverted)" whatever had happened.
    #
    # And with less behind it than the spec guard had: `scope.restore` records
    # its failures on `ctx["unrestorable"]`, so touch_scope at least has a
    # second layer. This guard calls `restore_paths` directly, and measured end
    # to end that showed as `ctx["unrestorable"] == []` beside a brief on disk
    # reading HIJACKED. Nothing anywhere contradicted the sentence.
    #
    # Left calling `restore_paths` rather than switched to `scope.restore`
    # DELIBERATELY: that channel renders as "SCOPE: out-of-scope change in ...
    # the pre-run content was too large to snapshot" (qd/verdict.py), which is
    # true of the cap route and false of the other three, and a wrong reason is
    # what this commit is removing.
    #
    # Four routes into `unrestored`, measured against THIS guard -- the spec
    # guard's do not all transfer, and one of its exclusions is open here:
    #   * a brief absent at `base` AND absent from T0 (a gitignored scratch
    #     document: porcelain does not list ignored paths, so no bytes were
    #     saved, and it was never committed, so `git show` has nothing);
    #   * the brief replaced by a DIRECTORY -- `open(full, "wb")` raises;
    #   * the brief sabotaged then chmod'ed read-only -- same raise;
    #   * the T0 "toobig" route, which was ruled OUT for a spec because a dirty
    #     protected spec refuses the run before the worker starts. The brief has
    #     no such precondition -- an amendment deliberately dirties it before T0
    #     -- so a large brief, or any brief in a tree over SNAPSHOT_TOTAL_CAP,
    #     is saved non-restorable and comes back unrestored.
    # The worker `git add` route does NOT apply: `brief_path` is the caller's
    # `brief_file`, not a set derived from `git ls-files`.
    restored, _unrestored = restore_paths(
        scope.work_cwd, [plan.brief_path], base=scope.pre_sha,
        t0=scope.t0_bytes)
    if restored:
        outcome, state = "auto-reverted", " and has been reverted"
    else:
        # Named as STATE rather than under a verdict word, because "reverted"
        # and "still holding the worker's text" are opposite instructions to
        # the reader -- and for the brief the stakes outlast the run: the
        # document is re-read from disk by name at CALL time, so the next
        # delegation against the same `brief_file` is briefed by the worker.
        outcome = "NOT REVERTED, still on disk"
        state = (". It could NOT be put back and still holds your version, so "
                 "this run does not trust it")
    return Violation(
        KIND,
        f"attempt {attempt.n}: PLAYBOOK EDITED -- {plan.brief_path} "
        f"({outcome})",
        f"You edited the brief document ({plan.brief_path}). That file defines "
        f"the task you were given{state}. Never modify the "
        f"brief: do the work it describes, and if you believe the brief is "
        f"wrong, stop and say so instead of editing it.",
        rider=True)
