#!/usr/bin/env python3
"""New public symbols that nothing outside their own file references.

Built and wired to nothing, or built for a caller that does not exist yet --
the receipt says which, because only the caller can tell them apart. A green
gate is silent here: a symbol nobody calls passes every test nobody wrote for
it.
"""

from qd.core.findings import Finding
from qd.surface.receipt import Block
from qd.gittree import uncalled_symbols

KIND = "uncalled"


def detect(facts, scope, plan):
    found = uncalled_symbols(scope.work_cwd, facts["pubs"])
    return Finding(KIND, found) if found else None


def block(data):
    """Drop priority 1: a diagnostic, so it yields to STATUS/CHANGED/ROLLBACK
    under the size cap, but it sits ABOVE the accounting lines because a dead
    symbol matters more than a token count.
    """
    flat = [f"{n} ({p})" for p, names in data.items() for n in names]
    return [Block(KIND,
                  f"UNCALLED: {len(flat)} new public symbol(s) referenced by "
                  f"nothing outside their own file/tests: " + ", ".join(flat[:6])
                  + (" ..." if len(flat) > 6 else "")
                  + " -- built and wired to nothing, or intended for a caller "
                    "that does not exist yet. Confirm which.", True, 1)]
