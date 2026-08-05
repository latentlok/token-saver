#!/usr/bin/env python3
"""
Spec for qd/features/gates/ -- the few things that can REFUSE a run.

Claude-authored gate (never delegate this file -- it defines what correct means).

Step 4 of docs/DESIGN-modular-architecture.md §5. One question, one shape:

    A gate answers REFUSE or PROCEED, and nothing else.

**Why gates are not detectors, and advisory gates are not gates.** §5 keeps the
two roles apart because refusing and reporting are different powers: a single
interface would force ~10 detectors to carry an empty veto hook, which is a fat
interface and one that invites a detector to start refusing things. The same
argument decides where `advisory_gates` belongs -- it is gate-SHAPED (it runs
commands and reports pass/fail) but it can never refuse, never touched STATUS
and never reached the worker. Giving it a refuse hook it must always decline to
use is the exact mistake §5 names. It stays where it is.

The parked work this exists for: A1's red gate needs somewhere to plug in, and
G4's brief-vs-diff pass needs to be told NO -- it must stay advisory, because a
witness that can refuse breaks PRINCIPLES §I, where the verdict is a command's
exit code and never anybody's account of the work.

Run:  python3 specs/gates_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from qd.features import gates  # noqa: E402


class TheDecision(unittest.TestCase):
    """A gate's answer is a value, not a convention."""

    def test_proceed_carries_no_reason(self):
        d = gates.proceed()
        self.assertTrue(d.ok)
        self.assertIsNone(d.reason)

    def test_a_refusal_must_say_why(self):
        # A refusal the caller cannot act on wastes the one thing refusing was
        # supposed to save: their time. Nothing may refuse anonymously.
        with self.assertRaises(ValueError):
            gates.refuse("")

    def test_a_refusal_carries_its_reason(self):
        d = gates.refuse("BRIEF CHALLENGED: it contradicts store.py")
        self.assertFalse(d.ok)
        self.assertIn("store.py", d.reason)

    def test_a_decision_cannot_be_edited_after_the_fact(self):
        # The same rule as the facts record: a verdict that can be rewritten
        # downstream is not a verdict, and "who changed this" becomes a
        # question the code cannot answer.
        d = gates.proceed()
        with self.assertRaises(AttributeError):
            d.ok = False


class TheRegistry(unittest.TestCase):
    """Enumerable, like the detectors -- for the same reason."""

    def test_every_gate_is_listed(self):
        self.assertEqual(sorted(g.NAME for g in gates.GATES), ["challenge"])

    def test_every_gate_answers_the_one_question(self):
        for g in gates.GATES:
            self.assertTrue(callable(getattr(g, "check", None)),
                            f"{g.NAME} cannot be asked")

    def test_a_gate_that_proceeds_lets_the_run_continue(self):
        d = gates.run_all([_Yes()], object())
        self.assertTrue(d.ok)

    def test_the_first_refusal_stops_the_rest(self):
        # Gates are expensive -- `challenge` spends a whole executor pass
        # reading the codebase. Once one has said no, the run is not happening,
        # and paying for a second opinion on a dead run is pure cost.
        asked = []
        d = gates.run_all([_No(asked), _Yes(asked)], object())
        self.assertFalse(d.ok)
        self.assertEqual(asked, ["no"], "a gate ran after the refusal")

    def test_a_gate_that_raises_does_not_refuse_the_run(self):
        # The detectors' rule (step 2) with higher stakes. A gate is the only
        # thing that can stop a run before any tokens are spent, so a crashing
        # gate that FAILS CLOSED silently costs the caller every delegation
        # until someone notices. Broken instrument, not a verdict.
        d = gates.run_all([_Boom(), _Yes()], object())
        self.assertTrue(d.ok)


class _Yes:
    NAME = "yes"

    def __init__(self, log=None):
        self.log = log

    def check(self, _run):
        if self.log is not None:
            self.log.append("yes")
        return gates.proceed()


class _No:
    NAME = "no"

    def __init__(self, log=None):
        self.log = log

    def check(self, _run):
        if self.log is not None:
            self.log.append("no")
        return gates.refuse("no thanks")


class _Boom:
    NAME = "boom"

    def check(self, _run):
        raise RuntimeError("gate exploded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
