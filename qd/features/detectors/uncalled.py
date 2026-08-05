#!/usr/bin/env python3
"""New public symbols that nothing outside their own file references.

Built and wired to nothing, or built for a caller that does not exist yet --
the receipt says which, because only the caller can tell them apart. A green
gate is silent here: a symbol nobody calls passes every test nobody wrote for
it.
"""

from qd.core.findings import Finding
from qd.gittree import uncalled_symbols

KIND = "uncalled"


def detect(facts, inputs):
    found = uncalled_symbols(inputs.work_cwd, facts["pubs"])
    return Finding(KIND, found) if found else None
