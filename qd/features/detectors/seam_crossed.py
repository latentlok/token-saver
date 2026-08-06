#!/usr/bin/env python3
"""A5: a new symbol that touches a seam, reached by the tests only through a mock.

The backstop for plain delegation (DESIGN-v06-test-first §7). The plugin cannot
know BEFORE a run which files will change. It knows after:

    a NEW symbol that itself references a seam (a client, session, connection,
    subprocess) AND the module it lives in is mocked by the delivered tests
    -> SEAM CROSSED, UNIT-GATED ONLY

The orchestrator picks the cheap path; this is the plugin disagreeing when the
cheap path was wrong. It costs nothing until it is, needs no pre-knowledge, and
reuses facts already computed -- `pubs` and the `mocked_seams` finding.

**The predicate is the new SYMBOL, not the file**, and that is the whole design
decision. "Lives in a file that imports a driver" would fire on a pure helper
added beside one, and this project has spent a phase deleting false accusations
of exactly that shape -- the `skipif` false positives, TEST DODGE crying wolf
four times out of four on an ordinary refactor. **A detector that fires on the
line you are told to read is worse than no detector**, because it teaches the
reader to skip the whole region.
"""

import os
import re

from qd.core.findings import Finding
from qd.surface.receipt import Block

KIND = "seam_crossed"
REGION, SLOT = "LATE", 25

# What "touches a seam" means, narrowly. Names that appear in the symbol's own
# body and imply it reaches OUT of the process: a network client, a database
# session, a live connection, a subprocess. Deliberately not "imports anything"
# -- that is the file-level predicate this detector exists to avoid.
_SEAM = re.compile(
    r"\b(?:requests|httpx|urllib|socket|subprocess|psycopg|sqlite3|boto3|"
    r"session|connection|cursor|client|engine\.connect|Popen)\b", re.I)


def _body(source, name):
    """The lines of `name`'s definition. Crude on purpose: an indentation walk
    beats an AST here because the same rule has to hold for the non-Python
    files `new_public_symbols` already reports."""
    lines = source.splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"\s*(?:def|class|func|function)\s+{re.escape(name)}\b", line):
            indent = len(line) - len(line.lstrip())
            out = [line]
            for nxt in lines[i + 1:]:
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                out.append(nxt)
            return "\n".join(out)
    return ""


def detect(facts, scope, plan):
    mocked = {module for _test, module in (facts.get("mocked_seams") or [])}
    if not mocked:
        return None
    crossed = []
    for path, names in (facts.get("pubs") or {}).items():
        if path not in mocked:
            continue
        try:
            with open(os.path.join(scope.work_cwd, path)) as f:
                source = f.read()
        except OSError:
            continue
        for name in names:
            if _SEAM.search(_body(source, name)):
                crossed.append(f"{name} ({path})")
    return Finding(KIND, crossed) if crossed else None


def block(data):
    return [Block(KIND,
                  f"SEAM CROSSED, UNIT-GATED ONLY: {', '.join(data[:4])}"
                  + (" ..." if len(data) > 4 else "")
                  + " -- new symbol(s) that reach outside the process, whose "
                    "module the delivered tests mock. The gate exercised the "
                    "mock, not the seam.", True, 1)]
