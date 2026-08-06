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
"""

from collections import namedtuple

Violation = namedtuple("Violation", "kind trail prompt")
