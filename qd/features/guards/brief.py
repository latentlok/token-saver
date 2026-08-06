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

    restore_paths(scope.work_cwd, [plan.brief_path], base=scope.pre_sha,
                  t0=scope.t0_bytes)
    return Violation(
        KIND,
        f"attempt {attempt.n}: PLAYBOOK EDITED -- {plan.brief_path} "
        f"(auto-reverted)",
        f"You edited the brief document ({plan.brief_path}). That file defines "
        f"the task you were given and has been reverted. Never modify the "
        f"brief: do the work it describes, and if you believe the brief is "
        f"wrong, stop and say so instead of editing it.",
        rider=True)
