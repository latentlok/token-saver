#!/usr/bin/env python3
"""
Spec for qd/core/prompt.py -- what the worker is actually sent.

Claude-authored gate (never delegate this file -- it defines what correct means).

The Decorator, last of the five patterns in DESIGN §7. What was scattered was
never the STRINGS -- those live sensibly beside the parsers that read them back.
It was the CONDITIONS: five `if` statements across three files, each deciding
whether one layer applies, with nothing able to answer *what is this worker
about to be told?*

Run:  python3 specs/prompt_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.core.prompt import compose, tail  # noqa: E402


class Composing(unittest.TestCase):
    def test_the_task_survives_alone(self):
        self.assertEqual(compose("do the thing"), "do the thing")

    def test_prefixes_come_before_and_suffixes_after(self):
        # Not cosmetic. A prefix is the SITUATION the worker is in and has to be
        # read before the instruction it qualifies; a suffix is the REPORTING
        # CONTRACT and belongs where the worker is about to answer.
        got = compose("TASK", prefixes=["SITUATION\n"], suffixes=["\nCONTRACT"])
        self.assertEqual(got, "SITUATION\nTASK\nCONTRACT")

    def test_layers_keep_their_order(self):
        got = compose("T", prefixes=["A", "B"], suffixes=["Y", "Z"])
        self.assertEqual(got, "ABTYZ")

    def test_empty_layers_contribute_nothing(self):
        # A prompt whose shape depends on which optional layers happened to be
        # present is one nobody can predict from reading the code.
        self.assertEqual(compose("T", prefixes=["", None], suffixes=[None, ""]),
                         "T")


class TheMachineReadTail(unittest.TestCase):
    def test_it_is_absent_when_not_wanted(self):
        self.assertEqual(tail("HANDOFF", wanted=False), "")

    def test_the_riders_go_with_it(self):
        # The rule that was implicit in `if report and suffix`. A machine-read
        # instruction with nowhere to be read is worse than absent: it spends
        # tokens AND creates the impression the contract was stated.
        self.assertEqual(tail("H", wanted=False, findings="F", schema="S"), "")

    def test_findings_ride_the_handoff_block(self):
        # Beside the handoff lines, not instead of them: on a report run the
        # findings line is the deliverable, and it rides the same machine-read
        # tail the parser already reads.
        self.assertEqual(tail("H", wanted=True, findings="F"), "HF")

    def test_the_schema_rides_it_too(self):
        self.assertEqual(tail("H", wanted=True, schema="S"), "HS")

    def test_everything_in_a_fixed_order(self):
        # The parser reads these back; a shifting order is a parser that works
        # by luck.
        self.assertEqual(tail("H", wanted=True, findings="F", schema="S"),
                         "HFS")

    def test_a_bare_tail_is_just_the_handoff(self):
        self.assertEqual(tail("H", wanted=True), "H")


if __name__ == "__main__":
    unittest.main(verbosity=2)
