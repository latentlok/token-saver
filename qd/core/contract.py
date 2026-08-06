#!/usr/bin/env python3
"""The contract: what was asked for, in clauses, pinned to a version.

A2 + A4 (DESIGN-v06-test-first §3.3, §6.2). Once the criteria live in their own
document, that document becomes **an input to the gate that can change without
anyone noticing** -- the same defect class as a worker editing a spec, one level
up. Three ways it bites:

  * the worker edits the contract to match what it built
  * it is edited between link 1 and link 2, so the gate was written against one
    document and the implementation graded against another
  * it is edited between the run and review, so the receipt you audit no longer
    describes the criteria that ran

The first is `spec_globs`, config only. The second and third are here.

**Where link 2 reads link 1's digest.** Not from the receipt -- receipts are text
returned to the caller, and there is no state channel between links. In-memory
state would work for one server call and break on retry or resume. So link 1
writes the digest into the artifact it COMMITS: a header line in the test file,
which the chain's shared worktree carries forward by construction.

**What is mechanical here, and what is not.** The PRESENCE of a `C2` tag is a
grep. Whether the test tagged `C2` actually asserts C2 is the worker's claim,
and a test tagged `C2` that asserts something adjacent reads as covered. There
is no mechanical fix for that, which is why the receipt echoes the clause beside
its test rather than a checkmark: it is a prompt for a five-second human check,
not a verdict.
"""

import hashlib
import re

# A clause DECLARATION: "C1: ...", "- **C2** ...", "### C3 -- ...". Deliberately
# anchored to the line start, so a clause merely REFERRED to in prose ("unlike
# C2, this...") does not become a requirement nobody wrote.
_DECL = re.compile(r"^\s*(?:[-*#]+\s*)?\*{0,2}(C\d+)\*{0,2}\s*[:.—-]", re.M)

# A clause REFERENCE, anywhere: this is what a test uses to claim coverage.
_REF = re.compile(r"\bC\d+\b")

HEADER = "# contract:"


def digest(text):
    """16 hex chars of sha256, matching the brief's convention.

    Short on purpose: it goes in a receipt a caller pays for by the character,
    and 16 hex chars is 64 bits -- far past what an accidental collision between
    two versions of one document needs.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def clauses(text):
    """Clause IDs the contract DECLARES, in document order, deduplicated."""
    seen, out = set(), []
    for cid in _DECL.findall(text or ""):
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def covered(text, ids):
    """Which of `ids` the given test text claims. A grep, and honestly so."""
    found = set(_REF.findall(text or ""))
    return [cid for cid in ids if cid in found]


def header_line(path, dig):
    """The line link 1 writes into its test file so link 2 can read it."""
    return f"{HEADER} {path} @ {dig}"


def parse_header(text):
    """(path, digest) from a test file's header, or (None, None).

    Read from anywhere in the file rather than the first line only: formatters
    move comments, and a pin that a reformat can silently unpin is not a pin.
    """
    m = re.search(re.escape(HEADER) + r"\s*(\S+)\s*@\s*([0-9a-f]{6,64})",
                  text or "")
    return (m.group(1), m.group(2)) if m else (None, None)
