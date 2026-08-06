#!/usr/bin/env python3
"""A6: test files the worker created without the `_qwen` provenance marker.

Tier and provenance are orthogonal axes (DESIGN-v06-test-first.md §2.5). The
directory decides WHEN a test runs; the `_qwen` suffix decides WHOSE it is.

Why the suffix is load-bearing rather than cosmetic, in three places that
already depend on it:

  * `_strays` treats `*_qwen.*` as the sanctioned scratch convention and stays
    quiet about it. A worker test WITHOUT the marker is reported as debris --
    which is the wrong complaint, so the caller learns to ignore STRAYS.
  * `ci/run-specs.sh` runs `*_qwen.py` deliberately, because a test that ran
    once and is never run again protects nothing. An unmarked worker test is
    invisible to that convention.
  * Most importantly: a worker test named like a Claude-authored one is a test
    whose PROVENANCE has been lost. The gate coming from a different hand is
    what makes a green receipt mean anything (PRINCIPLES §I), and a file nobody
    can attribute is a file that quietly stops being evidence of that.

Splitting by author into separate DIRECTORIES would be the `NEVER EXECUTED:`
defect by construction, which is why the marker is a suffix and not a folder.

This detector is the whole point of steps 2 and 3 stated as an artifact: one
file, one line in DETECTORS, and it renders. Nothing in the engine or the
renderer changed to accept it.
"""

import os

from qd.core.findings import Finding
from qd.surface.receipt import Block

KIND = "unmarked_tests"

# WHERE this finding renders. Declared by the detector so adding one is a
# file plus a line in DETECTORS -- placement is the size cap's tie-break
# among equal priorities, so it cannot be left to registration order.
REGION, SLOT = "LATE", 40

# Deliberately narrow. A file is a test only if its NAME says so -- inferring
# from content would fire on fixtures, helpers and anything that imports
# unittest, and a detector that cries wolf gets switched off wholesale (the
# lesson TEST DODGE paid for: wrong 4 times out of 4 on an ordinary refactor).
_TEST_NAME = ("test_", "_test")


def _is_test(path):
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    if ext not in (".py", ".js", ".ts", ".rb", ".go"):
        return False
    return stem.startswith(_TEST_NAME[0]) or stem.endswith(_TEST_NAME[1])


def detect(facts, scope, plan):
    found = sorted(
        p for p in (scope.created or [])
        if _is_test(p) and "_qwen." not in os.path.basename(p))
    return Finding(KIND, found) if found else None


def block(data):
    """Drop priority 1 -- a diagnostic, ranked with the other seam-risk lines.

    It sits below UNCALLED and MOCKED SEAM in usefulness (a misnamed test still
    tests something) but above the accounting, because provenance decides what
    a later green receipt is allowed to claim.
    """
    return [Block(KIND,
                  f"UNMARKED TEST: {len(data)} worker-written test file(s) "
                  f"without the `_qwen` provenance marker: "
                  + ", ".join(data[:4]) + (" ..." if len(data) > 4 else "")
                  + " -- indistinguishable from a Claude-authored gate. Rename "
                    "to *_qwen.* so a later reader can tell whose test it is.",
                  True, 1)]
