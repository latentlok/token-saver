#!/usr/bin/env python3
"""
Spec for qd/core/pipeline.py.

Claude-authored gate (never delegate this file -- it defines what correct means).

Run:  python3 specs/pipeline_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.core.pipeline import ratchet_minimum  # noqa: E402


class TheSelfGateRatchet(unittest.TestCase):
    """Under trust="self" the server writes the gate, and an existing suite is
    ALREADY GREEN -- so a gate that merely runs it proves nothing about the new
    work, and every later feature reports `success_but_preflight_passed`: a real
    delivery filed as a vacuous one. The gate binds on the DELTA instead."""

    def test_it_demands_one_more_than_already_passes(self):
        self.assertEqual(ratchet_minimum("Ran 12 tests in 0.1s\n\nOK\n"), 13)

    def test_it_understands_pytest_too(self):
        # A project may run either. A ratchet that understood one runner would
        # silently not ratchet under the other.
        self.assertEqual(ratchet_minimum("=== 7 passed in 0.3s ===\n"), 8)

    def test_it_SUMS_across_a_multi_file_suite(self):
        # The bug this rule exists for. A multi-file suite prints one count per
        # file; a ratchet set from the FIRST line would demand one more test
        # than the first file contains -- a threshold the suite already clears,
        # which restores the exact vacuous pass the ratchet was added to
        # prevent.
        out = ("== specs/a_spec.py ==\nRan 4 tests in 0.0s\n\nOK\n"
               "== specs/b_spec.py ==\nRan 9 tests in 0.0s\n\nOK\n")
        self.assertEqual(ratchet_minimum(out), 14)

    def test_an_empty_suite_still_demands_one(self):
        # Zero passing tests means any single test is a delta. Returning 0 would
        # write a gate that requires nothing.
        self.assertEqual(ratchet_minimum(""), 1)
        self.assertEqual(ratchet_minimum("Ran 0 tests in 0.0s\n"), 1)

    def test_unparseable_output_does_not_lower_the_bar(self):
        # A gate whose output nobody can count must not silently become a gate
        # that asks for nothing.
        self.assertEqual(ratchet_minimum("something went sideways"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
