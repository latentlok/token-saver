#!/usr/bin/env python3
"""
Spec for qd/core/runnable.py -- one Run, or a ChainOfRuns.

Claude-authored gate (never delegate this file -- it defines what correct means).

Step 7 (Composite), built after DESIGN §8.1 recommended against it. The
objection there was that a batch and a chain are deliberately NOT uniform, so
treating them uniformly loses what makes each correct.

Half of that was wrong, and this module is the correction: **Composite does not
require that nesting be allowed.** It requires that both kinds answer the same
question. Refusing to nest is a rule about CONSTRUCTION, and moving it there
makes it a property of the type instead of a string the dispatcher hands back at
runtime.

The half that survives: they are not interchangeable in BEHAVIOUR, so there is
no shared `.execute()`. Execution stays with `run_chain`, which owns the
worktree sharing, the between-link commits and the handoff forwarding -- none of
which is improved by being reached through a method.

What this unblocks: G2 (whole-chain contradiction) needs to read every link
before any of them runs, and until now a chain was `items` plus a function, with
nothing to hand a gate.

Run:  python3 specs/runnable_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.core import runnable  # noqa: E402


class Classifying(unittest.TestCase):
    def test_a_lone_delegation_is_one_link(self):
        r = runnable.of({"task": "t", "cwd": "/x"})
        self.assertFalse(r.is_chain)
        self.assertEqual(len(r.links), 1)

    def test_a_chain_carries_every_link(self):
        r = runnable.of({"chain": [{"task": "a"}, {"task": "b"}, {"task": "c"}]})
        self.assertTrue(r.is_chain)
        self.assertEqual([l["task"] for l in r.links], ["a", "b", "c"])

    def test_a_non_dict_is_still_a_runnable(self):
        # The dispatcher used to hand these straight to engine.run and let it
        # fail its own way. Classifying rather than special-casing keeps one
        # shape for the caller.
        self.assertFalse(runnable.of("not a dict").is_chain)

    def test_an_empty_chain_is_not_a_chain(self):
        # `chain: []` asks for nothing, and calling it a chain would produce a
        # pipeline of zero links whose receipt describes a run that never
        # happened.
        self.assertFalse(runnable.of({"chain": [], "task": "t"}).is_chain)


class NestingIsRefusedAtConstruction(unittest.TestCase):
    """The step-7 change, stated as itself."""

    def test_a_nested_batch_raises_rather_than_returning_a_receipt(self):
        # A dispatcher that returns an error STRING for a structural mistake has
        # made the caller's shape into control flow, and every caller must then
        # remember to check. An exception cannot be forgotten.
        with self.assertRaises(runnable.NestingRefused):
            runnable.of({"batch": [{"task": "a"}]})

    def test_the_refusal_still_tells_the_caller_what_to_do(self):
        try:
            runnable.of({"batch": [{"task": "a"}]})
        except runnable.NestingRefused as e:
            self.assertIn("nesting is one level", str(e))
            self.assertIn("Nothing was run", str(e))
        else:
            self.fail("nesting was allowed")

    def test_a_chain_inside_a_batch_item_is_fine(self):
        # The distinction the refusal exists to preserve: N independent
        # pipelines, each internally ordered, is exactly what the two words
        # have always meant. A batch inside a batch is the ambiguity.
        r = runnable.of({"chain": [{"task": "a"}, {"task": "b"}]})
        self.assertTrue(r.is_chain)


class ItIsAValue(unittest.TestCase):
    def test_a_chain_can_be_read_before_it_runs(self):
        # What G2 needs, and the reason this module exists at all: a gate must
        # be able to read every link of a chain BEFORE any of them runs, so a
        # link 3 that contradicts link 1 is caught before link 2 has committed.
        r = runnable.of({"chain": [{"task": "add a column"},
                                   {"task": "drop that column"}]})
        self.assertEqual(len(r.links), 2)
        self.assertIn("drop", r.links[1]["task"])

    def test_it_cannot_be_edited_after_classification(self):
        r = runnable.of({"task": "t"})
        with self.assertRaises(AttributeError):
            r.is_chain = True


if __name__ == "__main__":
    unittest.main(verbosity=2)
