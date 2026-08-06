#!/usr/bin/env python3
"""
Spec for the PAID: receipt line (A5).

Claude-authored gate (never delegate this file -- it defines what correct means).

Every other number on a receipt is about the WORKER. This is the only one about
the CALLER, and it is the one the product rests on: *judgment stays with the
expensive scarce thing, execution goes to the cheap plentiful thing, and a
command decides who was right.* That trade is either solvent on a given run or
it is not -- and nothing said which.

Run:  python3 specs/paid_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.surface.receipt import paid_line  # noqa: E402


class TheTrade(unittest.TestCase):
    def test_it_states_both_sides(self):
        line = paid_line(1847, 89584, 458)
        self.assertIn("461", line)          # what the caller paid
        self.assertIn("89,584", line)       # what the worker burned

    def test_it_gives_the_ratio(self):
        self.assertIn("195x leverage", paid_line(1847, 89584, 458))

    def test_the_ratio_moves_with_the_receipt(self):
        # A long receipt is a worse trade, and must read as one.
        big = paid_line(3000, 89584, 458)
        small = paid_line(500, 89584, 458)
        self.assertLess(int(big.split("~")[-1].split("x")[0].replace(",", "")),
                        int(small.split("~")[-1].split("x")[0].replace(",", "")))


class HonestyAboutItself(unittest.TestCase):
    def test_it_says_the_count_excludes_its_own_line(self):
        # Including itself would make the number describe a receipt that only
        # exists because the number was added -- a self-referential measurement
        # nobody can check.
        self.assertIn("excluding this line", paid_line(1847, 89584, 458))

    def test_it_marks_the_figure_as_approximate(self):
        # ~4 chars/token. The point is the ORDER of magnitude -- 200x vs 2x --
        # not a figure anyone should try to reconcile.
        self.assertIn("~", paid_line(1847, 89584, 458))


class Silence(unittest.TestCase):
    def test_a_run_that_burned_nothing_makes_no_claim(self):
        # A refusal or a cached run made no trade. A leverage figure over zero
        # work is a number pretending to be a measurement (PRINCIPLES §IV: a
        # zero meaning "nothing happened" and one meaning "nothing was
        # measured" have to stay distinguishable).
        self.assertIsNone(paid_line(1847, 0, 0))

    def test_a_tiny_receipt_never_divides_by_zero(self):
        self.assertIsNotNone(paid_line(1, 1000, 10))


if __name__ == "__main__":
    unittest.main(verbosity=2)
