#!/usr/bin/env python3
"""
Spec for qd/core/tiers.py -- which suite grades this run.

Claude-authored gate (never delegate this file -- it defines what correct means).

A3 (DESIGN-v06-test-first §2.2). **Declared, never guessed**, and the asymmetry
is the whole argument:

    guessing a single gate COMMAND fails VISIBLY -- wrong command, obviously
    broken gate, noticed on the first run

    guessing a TIER MAP fails SILENTLY -- mislabel the integration suite as
    `unit` and every delegation either eats the wall clock or refuses on GATE
    UNUSABLE; mislabel a subset as "the unit tier" and you under-gate forever
    with no symptom at all

That is why a project which declares nothing, on work that crosses a seam, is
REFUSED with the question rather than gated on a guess.

Run:  python3 specs/tiers_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.core import tiers  # noqa: E402

MAP = {"tests": {"unit": "pytest tests/unit -q",
                 "integration": "pytest tests/integration -q"}}


class Declaring(unittest.TestCase):
    def test_a_declared_map_is_read(self):
        self.assertEqual(sorted(tiers.declared(MAP)), ["integration", "unit"])

    def test_nothing_declared_is_an_empty_map_not_an_error(self):
        for cfg in ({}, {"tests": None}, {"tests": "pytest"}, None):
            self.assertEqual(tiers.declared(cfg), {}, repr(cfg))

    def test_a_malformed_tier_is_dropped_not_raised_on(self):
        # A typo in ONE tier must not cost a caller every delegation, and the
        # tiers that are well-formed are still better than the guess they
        # replace.
        got = tiers.declared({"tests": {"unit": "pytest u", "integration": 7,
                                        "nonsense": "x", "e2e": "   "}})
        self.assertEqual(got, {"unit": "pytest u"})


class Choosing(unittest.TestCase):
    def test_ordinary_work_gets_the_cheapest_tier(self):
        # A gate nobody will wait for is a gate that gets switched off.
        cmd, tier, refusal = tiers.gate_for(MAP)
        self.assertEqual(tier, "unit")
        self.assertIsNone(refusal)

    def test_work_that_crosses_a_seam_gets_a_wider_one(self):
        # The case a unit suite CANNOT judge, and where nobody can tell from a
        # green receipt that it did not.
        cmd, tier, _ = tiers.gate_for(MAP, crosses_seam=True)
        self.assertEqual(tier, "integration")

    def test_a_unit_only_project_still_gets_its_unit_tier(self):
        # Declaring one tier is a statement that it is the only one. Refusing
        # here would punish a project for being honest about having no
        # integration suite.
        cmd, tier, refusal = tiers.gate_for({"tests": {"unit": "pytest u"}},
                                            crosses_seam=True)
        self.assertEqual(tier, "unit")
        self.assertIsNone(refusal)


class RefusingWithTheQuestion(unittest.TestCase):
    """Precedent: `trust: "auto"` and `_shape_refusal` both refuse by name
    before anything spawns. Over stdio, "ask" means "refuse and say what to
    send"."""

    def test_undeclared_plus_a_seam_is_refused(self):
        cmd, tier, refusal = tiers.gate_for({}, crosses_seam=True)
        self.assertIsNone(cmd)
        self.assertIn("TIERS UNDECLARED", refusal)

    def test_the_refusal_shows_the_config_to_paste(self):
        # A refusal the caller has to go and research costs more than the run
        # it saved.
        refusal = tiers.gate_for({}, crosses_seam=True)[2]
        self.assertIn('"tests"', refusal)
        self.assertIn('"integration"', refusal)

    def test_it_says_WHY_guessing_is_worse_than_asking(self):
        # Otherwise this reads as bureaucracy and gets configured away.
        refusal = tiers.gate_for({}, crosses_seam=True)[2]
        self.assertIn("under-gates silently", refusal)

    def test_ordinary_work_with_no_map_is_NOT_refused(self):
        # The narrow change: an untiered project keeps working. detect_test_cmd
        # remains the fallback, and refusing every untiered project would be a
        # migration nobody asked for.
        cmd, tier, refusal = tiers.gate_for({})
        self.assertIsNone(refusal)
        self.assertIsNone(cmd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
