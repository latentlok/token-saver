#!/usr/bin/env python3
"""The worker edited a file that defines what correct means.

The single most important guard in the system. A gate written by the thing being
graded is not a gate -- PRINCIPLES §I: *if the builder also writes the building
inspection, a misunderstanding lands identically in the wall and in the
checklist. They agree perfectly. They are both wrong.*

Compared against the T0 sha, not against HEAD, and that is load-bearing: if the
worker COMMITTED its edit, HEAD now holds the weakened spec, so restoring from
HEAD would faithfully restore the sabotage. That hole was open and untested
until a mutation sweep found it.

**An unattributed spec change is reported, never reverted.** Under a proxy that
logs the worker's writes (C10), a protected file that moved with no logged write
is somebody else's -- reverting it is how a caller's concurrent work got
destroyed. The cost is a gate whose definition of correct changed under the run,
which is why the receipt has to say so out loud rather than quietly proceeding.
"""

from qd.core.violation import Violation
from qd.gittree import revert_specs, violated_specs

KIND = "spec_violation"


def check(scope, plan, attempt):
    cheated = violated_specs(scope.work_cwd, base=scope.pre_sha)
    if not cheated:
        return None

    notes = []
    if scope.hooked:
        unattributed = [p for p in cheated if p not in attempt.writes]
        fresh = scope.note_spec_unattributed(unattributed)
        if fresh:
            notes.append(
                f"attempt {attempt.n}: SPEC CHANGED (unattributed) -- "
                f"{', '.join(fresh)} differs from its pre-run state with no "
                f"logged worker write; NOT reverted")
        cheated = [p for p in cheated if p in attempt.writes]

    if not cheated:
        # Somebody else's edit only. Nothing to punish, everything to report.
        return Violation(KIND, None, None, False, tuple(notes))

    revert_specs(scope.work_cwd, cheated, base=scope.pre_sha,
                 t0=scope.t0_bytes)
    names = ", ".join(cheated)
    return Violation(
        KIND,
        f"attempt {attempt.n}: SPEC VIOLATION -- edited {names} (auto-reverted)",
        f"You edited a protected specification file ({names}). That file "
        f"defines what correct means and has been reverted. Never modify a "
        f"protected spec file. Fix the implementation code so it satisfies the "
        f"spec as written. If you believe the spec is wrong, stop and say so "
        f"instead of editing it.",
        rider=True, notes=tuple(notes))
