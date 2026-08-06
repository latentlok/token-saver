#!/usr/bin/env python3
"""What a guard returns when an attempt broke a rule.

Its own module for the same reason `Finding` and `Block` have theirs: the record
is shared by the registry and by every guard that produces one, and defining it
in the package `__init__` would make each guard import the package that imports
it.

    kind    a stable machine name
    trail   the attempt-trail line, which is what FAILS the attempt and what
            core/status.py later classifies
    prompt  the correction sent to the worker on its next attempt, or None if
            the violation is terminal and re-asking is pointless
    notes   trail lines that are RECORDED but do not fail the attempt. The
            spec guard needs this: a protected file that moved with no logged
            worker write is somebody else's edit, which the receipt must say
            out loud and must NOT punish the worker for.
    rider   whether the correction needs the compaction re-injection appended.
            A FLAG rather than the text, because applying it mutates session
            state (`discards`/`reinjects`, and possibly dropping the session)
            which belongs to the loop. A guard says "this correction is useless
            to a worker that has forgotten the task"; the loop decides what to
            do about it.
"""

from collections import namedtuple

Violation = namedtuple("Violation", "kind trail prompt rider notes")
# `trail=None` means informational only -- notes to record, nothing to fail.
Violation.__new__.__defaults__ = (False, ())
