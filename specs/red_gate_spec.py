#!/usr/bin/env python3
"""
Spec for qd/features/gates/red.py -- is link 1's failing test a REAL gate?

Claude-authored gate (never delegate this file -- it defines what correct means).

Parked item A1, unblocked by step 4. The failure it exists for is the one
PRINCIPLES calls the worst: a worker-written gate is the brief restated as an
assertion, so a wrong requirement becomes a green test defending the defect, and
every signal downstream reads as success.

Test-first is the answer -- write the gate, watch it fail, then build. But "it
failed" is worth nothing on its own, because a test can fail for reasons that
have nothing to do with the thing it claims to check. A file with a typo fails.
A file that imports the wrong module fails. Both are red, and neither is a gate.

    A red gate is not a test that FAILED.
    It is a test that failed FOR THE REASON IT WAS WRITTEN TO FAIL.

Three mechanical checks (DESIGN-v06-test-first.md §6.1). The fourth, clause
coverage, needs the contract format and stays parked as A4:

  1. the test PARSES -- a syntax error is unambiguously the worker's fault
  2. at least one test RAN, and none were SKIPPED -- zero collected is not a
     gate, and a skip reads as a pass
  3. it failed LEGIBLY -- an assertion, or a missing-symbol error, which is
     what a correct test-first test does when the code does not exist yet

Check 3 is where an earlier draft of the design was WRONG, and the spec records
it so nobody re-introduces it: the draft demanded failure "by assertion, not by
error". A test-first test imports a symbol that does not exist, so it fails at
import -- ERROR, not FAILURE. That rule would have rejected every correct
greenfield artifact and accepted only the ones that were already half-built.

Run:  python3 specs/red_gate_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.features.gates import red  # noqa: E402


class ARealRedGate(unittest.TestCase):
    """What a correct test-first artifact looks like, in both runners."""

    def test_unittest_missing_symbol_is_a_good_red(self):
        out = ("E\n======\nERROR: test_add (t.TestAdd)\n"
               "Traceback (most recent call last):\n"
               '  File "t.py", line 1, in <module>\n'
               "    from mathlib import add\n"
               "ImportError: cannot import name 'add' from 'mathlib'\n"
               "Ran 1 test in 0.001s\n\nFAILED (errors=1)\n")
        self.assertTrue(red.assess(out).ok, red.assess(out).reason)

    def test_pytest_missing_module_is_a_good_red(self):
        out = ("collected 3 items\n\nt.py::test_a ERROR\n"
               "ModuleNotFoundError: No module named 'mathlib'\n"
               "=== 3 error in 0.12s ===\n")
        self.assertTrue(red.assess(out).ok, red.assess(out).reason)

    def test_a_plain_assertion_failure_is_a_good_red(self):
        out = ("F\n======\nFAIL: test_add (t.TestAdd)\n"
               "AssertionError: 5 != 6\n"
               "Ran 1 test in 0.001s\n\nFAILED (failures=1)\n")
        self.assertTrue(red.assess(out).ok, red.assess(out).reason)


class NotAGateAtAll(unittest.TestCase):
    """Red, and worthless. Each of these would have passed a bare 'did it fail?'."""

    def test_a_syntax_error_is_the_workers_fault(self):
        out = ('  File "t.py", line 3\n    def test_a(:\n           ^\n'
               "SyntaxError: invalid syntax\n")
        d = red.assess(out)
        self.assertFalse(d.ok)
        self.assertIn("parse", d.reason.lower())

    def test_zero_tests_collected_is_not_a_gate(self):
        # Nothing ran. The suite is red because there is no suite.
        out = "collected 0 items\n\n=== no tests ran in 0.01s ===\n"
        d = red.assess(out)
        self.assertFalse(d.ok)
        self.assertIn("no test", d.reason.lower())

    def test_unittest_zero_tests_is_not_a_gate(self):
        d = red.assess("Ran 0 tests in 0.000s\n\nNO TESTS RAN\n")
        self.assertFalse(d.ok)

    def test_a_skipped_test_reads_as_a_pass(self):
        # D1/A19: a skip is how a red suite becomes a green receipt without the
        # failure being fixed. In link 1 it is worse -- the gate the whole
        # chain will be graded against was born switched off.
        out = ("Ran 2 tests in 0.001s\n\nFAILED (failures=1, skipped=1)\n"
               "AssertionError: nope\n")
        d = red.assess(out)
        self.assertFalse(d.ok)
        self.assertIn("skip", d.reason.lower())

    def test_an_unrelated_error_is_a_broken_test(self):
        # The distinction the whole gate turns on. A missing symbol is what a
        # correct test-first test DOES. A ZeroDivisionError in the test's own
        # setup is a bug in the test, wearing the same red costume.
        out = ("E\n======\nERROR: test_a (t.T)\n"
               "ZeroDivisionError: division by zero\n"
               "Ran 1 test in 0.001s\n\nFAILED (errors=1)\n")
        d = red.assess(out)
        self.assertFalse(d.ok)
        self.assertIn("unrelated", d.reason.lower())


class AlreadyGreen(unittest.TestCase):
    def test_a_passing_gate_before_any_code_exists_is_refused(self):
        # The original sin this pipeline exists to prevent: a gate that passes
        # before the work proves nothing about the work.
        d = red.assess("Ran 3 tests in 0.002s\n\nOK\n")
        self.assertFalse(d.ok)
        self.assertIn("green", d.reason.lower())

    def test_pytest_all_passed_is_refused_too(self):
        self.assertFalse(red.assess("=== 3 passed in 0.10s ===\n").ok)


class TheGateInterface(unittest.TestCase):
    """It plugs into step 4's registry like any other gate."""

    def test_it_declares_a_name(self):
        self.assertEqual(red.NAME, "red_gate")

    def test_it_only_applies_when_the_caller_asked_for_red(self):
        # Every other run must be untouched. A gate that fires when nobody
        # asked is a gate that gets switched off wholesale.
        from qd.features import gates
        run = gates.GateRun(objection=None, gate_output="Ran 3 tests\n\nOK\n",
                            expect="any")
        self.assertTrue(red.check(run).ok)

    def test_it_refuses_a_green_gate_when_red_was_asked_for(self):
        from qd.features import gates
        run = gates.GateRun(objection=None, gate_output="Ran 3 tests\n\nOK\n",
                            expect="red")
        d = red.check(run)
        self.assertFalse(d.ok)
        self.assertIn("RED GATE", d.reason)

    def test_no_gate_output_cannot_be_judged_and_does_not_refuse(self):
        # Absence of evidence. Refusing here would block every run whose gate
        # produced nothing, which is a broken instrument refusing work.
        from qd.features import gates
        self.assertTrue(red.check(
            gates.GateRun(objection=None, gate_output="", expect="red")).ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
