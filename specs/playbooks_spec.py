#!/usr/bin/env python3
"""
Spec for playbooks/write-gate.md and playbooks/implement.md.

Claude-authored gate (never delegate this file -- it defines what correct means).

A7 (DESIGN-v06-test-first §4.2). A playbook is ONE delegation's brief, saved so
it need not be retyped; a chain composes them. These two are the test-first
pipeline: link 1 writes the gate, link 2 makes it pass.

**Why documents get a spec.** The same reason `CHALLENGE_SUFFIX` has one: the
prompt IS the mechanism. Every protection in this repo can be in place and still
produce nothing if the document quietly stops saying the thing that makes the
worker behave. These tests pin the instructions that are load-bearing, not the
prose around them.

Run:  python3 specs/playbooks_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, ROOT)

from qd import playbook  # noqa: E402


def load(name):
    with open(os.path.join(ROOT, "playbooks", name)) as f:
        return f.read()


class TheyLoadAndFill(unittest.TestCase):
    """A playbook nobody can send is not a playbook."""

    def test_both_parse_their_front_matter(self):
        for name in ("write-gate.md", "implement.md"):
            fm = playbook.front_matter(load(name))[0]
            self.assertIsInstance(fm, dict, name)

    def test_both_take_the_same_two_slots(self):
        # They are links in ONE chain and are handed the same `vars`. A slot
        # only one of them declares would make the chain's vars wrong for
        # whichever link does not use it.
        for name in ("write-gate.md", "implement.md"):
            _text, missing, unused = playbook.substitute(
                load(name), {"contract": "c.md", "test_path": "t.py"})
            self.assertEqual(missing, [], f"{name} wants more than the chain gives")
            self.assertEqual(unused, [], f"{name} ignores a var the chain sends")

    def test_no_slot_survives_substitution(self):
        # An unfilled slot sends literal braces to the worker.
        for name in ("write-gate.md", "implement.md"):
            text, _m, _u = playbook.substitute(
                load(name), {"contract": "c.md", "test_path": "t.py"})
            self.assertNotIn("{{", text, name)


class WriteGateForbidsBuilding(unittest.TestCase):
    """Link 1's whole value is that it happens BEFORE the work."""

    def setUp(self):
        self.text = " ".join(load("write-gate.md").lower().split())

    def test_it_says_not_to_implement(self):
        self.assertIn("you are **not** implementing", self.text)

    def test_it_forbids_even_a_stub(self):
        # The subtle one: an empty module makes the import succeed, so the test
        # then fails for a different and weaker reason.
        self.assertIn("not even a stub", self.text)

    def test_it_states_all_three_red_gate_conditions(self):
        # The same three qd/features/gates/red.py enforces. If the document
        # stops asking for them, the gate starts refusing work it caused.
        for rule in ("must parse", "none may be skipped", "code is missing"):
            self.assertIn(rule, self.text, rule)

    def test_it_explains_that_a_broken_test_is_not_a_red_gate(self):
        self.assertIn("red costume", self.text)


class ClauseTaggingIsSpelledOut(unittest.TestCase):
    def setUp(self):
        self.text = load("write-gate.md")

    def test_it_shows_the_tag_syntax(self):
        # Coverage is a grep. A worker that tags differently is uncovered.
        self.assertIn("# C1", self.text)

    def test_it_says_the_tag_is_what_is_checked(self):
        self.assertIn("how coverage is checked", self.text)

    def test_it_asks_for_the_contract_pin(self):
        self.assertIn("# contract:", self.text)

    def test_it_forbids_inventing_a_digest(self):
        # Newline-tolerant: the document wraps, and a spec that breaks on
        # reflowing prose is one that gets "fixed" by deleting the rule.
        flat = " ".join(self.text.lower().split())
        self.assertIn("do not invent one", flat)

    def test_it_prefers_an_honest_gap_to_a_dishonest_tag(self):
        # The mechanical limit: no grep can tell whether a test tagged C2
        # asserts C2. So the instruction has to carry it.
        low = self.text.lower()
        flat = " ".join(low.split())
        self.assertIn("stop and say which clause and why", flat)
        self.assertIn("worse than one honestly marked uncovered", flat)


class ImplementProtectsTheGate(unittest.TestCase):
    def setUp(self):
        self.text = " ".join(load("implement.md").lower().split())

    def test_it_forbids_editing_the_test(self):
        self.assertIn("do not modify", self.text)

    def test_it_says_why_rather_than_just_forbidding(self):
        # A rule a worker understands is one it can apply to the case nobody
        # wrote down.
        self.assertIn("gate written by the thing being graded is not a gate",
                      self.text)

    def test_it_offers_stopping_as_a_real_answer(self):
        # Otherwise the only way out of a wrong test is to edit it.
        self.assertIn("stop and say so", self.text)

    def test_it_names_the_no_progress_rule(self):
        # The same signal `stuck_no_progress` reports to the caller, told to the
        # worker while it can still act on it.
        self.assertIn("same** failure", self.text)

    def test_it_names_the_scratch_file_convention(self):
        self.assertIn("_qwen.", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
