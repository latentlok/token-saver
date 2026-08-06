#!/usr/bin/env python3
"""A1: is link 1's failing test a REAL gate, or just a broken file?

Test-first exists to stop the worst failure this project has measured: a
worker-written gate is the brief restated as an assertion, so a wrong
requirement becomes a green test defending the defect and every signal
downstream reads as success. Writing the gate first, and watching it fail,
breaks that -- but only if the failure means something.

    A red gate is not a test that FAILED.
    It is a test that failed FOR THE REASON IT WAS WRITTEN TO FAIL.

A file with a typo fails. A file importing the wrong module fails. A test whose
own setup divides by zero fails. All three are red, none is a gate, and a
pipeline that accepts "it failed" accepts all of them -- then builds against
them, goes green, and reports success.

**Three checks (DESIGN-v06-test-first.md §6.1).** The fourth, clause coverage,
needs the contract format and stays parked as A4:

  1. it PARSES -- a syntax error is unambiguously the worker's fault
  2. at least one test RAN and none were SKIPPED -- zero collected is not a
     gate, and a skip reads as a pass (D1, A19). In link 1 that is worse than
     usual: the gate the whole chain will be graded against was born off.
  3. it failed LEGIBLY -- an assertion, or a MISSING SYMBOL, which is exactly
     what a correct test-first test does when the code does not exist yet.

**The trap in check 3, recorded because an earlier design draft fell into it.**
That draft required failure "by assertion, not by error". A test-first test
imports something that does not exist, so it fails at import -- ERROR in
unittest, collection error in pytest. The rule meant to catch broken tests
would have rejected every correct greenfield artifact and accepted only the
ones that were already half-built.

**Where this is an approximation, said plainly.** With a contract, check 3
could be exact -- the traceback must name the entry point the contract declares
missing. Without one, "missing symbol" is inferred from the exception type
(ImportError, ModuleNotFoundError, NameError, AttributeError), which admits an
error naming the WRONG missing symbol.

A2 now exists (`qd/core/contract.py`), so the ingredient is here; what is still
missing is the contract stating its ENTRY POINT as a symbol rather than as
prose. Until a contract format declares that, this remains the broad class --
recorded rather than quietly closed, because a check that claims exactness it
does not have is worse than one that admits the gap.
"""

from collections import namedtuple

NAME = "red_gate"

Verdict = namedtuple("Verdict", "ok reason")

# Exceptions a correct test-first test raises: the thing under test is not there
# yet. Anything else failed for a reason the test was not written to check.
_MISSING = ("ImportError", "ModuleNotFoundError", "NameError",
            "AttributeError", "ImportError:")

_SYNTAX = ("SyntaxError", "IndentationError", "TabError")


def _has(text, *needles):
    return any(n in text for n in needles)


def assess(output):
    """Judge one gate run's output. Pure -- text in, verdict out.

    Deliberately a pure function over the runner's TEXT rather than a structured
    result: the gate is whatever command the caller supplied, so there is no
    structured result to have. Both unittest and pytest shapes are recognised
    because both are what people actually run.
    """
    text = output or ""
    if not text.strip():
        # Absence of evidence. Refusing here would block every run whose gate
        # printed nothing -- a broken instrument refusing work.
        return Verdict(True, None)

    if _has(text, *_SYNTAX):
        return Verdict(False, "the test does not parse (SyntaxError) -- that is "
                              "the test's own fault, not evidence about the code")

    if _has(text, "Ran 0 tests", "collected 0 items", "no tests ran"):
        return Verdict(False, "no test ran -- zero collected is not a gate, it "
                              "is an empty file that happens to exit non-zero")

    if _has(text, "skipped=", " skipped", "SKIPPED"):
        return Verdict(False, "a test was skipped -- a skip reads as a pass, so "
                              "the gate this chain will be graded against was "
                              "born switched off")

    failed = _has(text, "FAILED", " failed", "FAIL:", "ERROR:", " error",
                  "errors=", "failures=")
    if not failed:
        if _has(text, "OK", " passed"):
            return Verdict(False, "the gate is GREEN before any code exists -- a "
                                  "pass here proves nothing about work not yet "
                                  "done, which is the whole reason for going "
                                  "test-first")
        return Verdict(True, None)

    if _has(text, "AssertionError", "assert "):
        return Verdict(True, None)
    if _has(text, *_MISSING):
        return Verdict(True, None)

    return Verdict(False, "the gate failed for an UNRELATED reason -- not an "
                          "assertion and not a missing symbol, so the test is "
                          "broken rather than red. A red gate must fail for the "
                          "reason it was written to check")


def check(run):
    """The step-4 gate interface: refuse, or proceed."""
    from qd.features import gates
    # Only when the caller declared test-first. A gate that fires when nobody
    # asked is a gate that gets switched off wholesale, and then it protects
    # nothing at all.
    if getattr(run, "expect", None) != "red":
        return gates.proceed()
    verdict = assess(getattr(run, "gate_output", None))
    if verdict.ok:
        return gates.proceed()
    return gates.refuse(
        f"RED GATE: {verdict.reason}.\n\n"
        "Nothing was built. `preflight_expect=\"red\"` says this run delivers a "
        "FAILING gate first, and the gate it produced is not one that can grade "
        "the work that follows. Fix the test, or drop preflight_expect if this "
        "is not a test-first run.")
