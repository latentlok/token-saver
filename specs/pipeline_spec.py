#!/usr/bin/env python3
"""
Spec for qd/core/pipeline.py.

Claude-authored gate (never delegate this file -- it defines what correct means).

The module's own docstring states the rule this file grades against: what
belongs in pipeline.py is "the logic those phases carry that is neither
orchestration nor a feature -- decisions with rules of their own, currently
readable only by tracing the loop". `ratchet_minimum` was the first, and it is
also the warning: its "summed across files, not taken from the first" semantics
lived in a COMMENT WITH NO TEST, and getting it wrong silently restored the
exact vacuous pass the rule existed to prevent.

Four more decisions of that shape are pinned below. Each was found in _delegate
carrying a real rule and no assertion:

  preflight_shareable  -- reuse a cached gate verdict only from a clean base
  graph_shell_grant    -- a permission boundary, previously pinned by GREP
  peak_high_water      -- reported peak context is a MAX, not the last attempt
  gate_is_slow         -- "slow" means past HALF the budget, not most of it

This file pins the RULES. That _delegate actually consults them -- the wiring,
which is where five of nine bugs in this round lived -- is pinned separately in
specs/pipeline_wiring_spec.py, because a helper with a perfect unit test that
the loop never calls is precisely the gap being closed here.

Run:  python3 specs/pipeline_spec.py
"""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd import graph  # noqa: E402
from qd.core.pipeline import (  # noqa: E402
    gate_is_slow,
    graph_shell_grant,
    peak_high_water,
    preflight_shareable,
    ratchet_minimum,
)


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


class ASharedPreflightVerdictNeedsACleanBase(unittest.TestCase):
    """`_preflight_once` keys one gate run on (base sha, worktrees dir, gate)
    and states its own invariant: "every item is cut from the SAME base commit
    into its own clean worktree, so that answer is identical for every item by
    construction".

    True for a batch. FALSE for a chain link after the first, whose tree
    deliberately holds the earlier links' commits -- that dependency IS the
    chain. Reuse the verdict there and link 2 is graded on link 1's gate run,
    against a tree link 1 never saw. And tiers make the collision likelier, not
    rarer: once projects declare `tests`, every pipeline's gate is the same
    command string, so concurrent chains off one base share a key while holding
    genuinely different trees."""

    def test_a_run_holding_its_own_worktree_at_the_head_of_a_chain_may_share(self):
        self.assertTrue(preflight_shareable(True, 1))

    def test_a_chain_link_after_the_first_may_not_share(self):
        # The whole reason the rule exists. Position 2 onward runs in a tree
        # that already holds the previous links' commits, so the cached verdict
        # answers a question about a DIFFERENT tree.
        self.assertFalse(preflight_shareable(True, 2))
        self.assertFalse(preflight_shareable(True, 7))

    def test_no_container_never_shares_whatever_the_position(self):
        # In-tree: the working tree can have moved between two runs, so a
        # shared verdict is a guess about a tree nobody re-read. Already the
        # engine's behaviour (`wt is not None`) and pinned here so the position
        # half cannot be "fixed" by dropping the container half.
        self.assertFalse(preflight_shareable(False, 1))
        self.assertFalse(preflight_shareable(False, 2))

    def test_no_position_at_all_reads_as_the_head_of_a_chain(self):
        # A plain delegation and a batch item carry no CHAIN_ARG, so the
        # position arrives absent. Reading absence as "not link 1" would switch
        # sharing off for every batch -- N gate runs where A13 pays for one,
        # which is the cost this cache was added to remove.
        self.assertTrue(preflight_shareable(True, None))
        self.assertTrue(preflight_shareable(True, 0))


class TheGraphShellGrantIsAPermissionBoundary(unittest.TestCase):
    """`graph.bootstrap_line()` promises the worker will locate code through the
    graph instead of reading files. The worker has no shell unless one is
    granted, so the promise held only for callers who wired `shell_allow`
    themselves. This grants the READ-ONLY subcommands where they can be used.

    Three conditions, each with a reason, and PRINCIPLES §III asks of every
    allowlist what the most powerful thing reachable through it is:
      scoped only  -- `auto-edit` has no shell at all, so a pattern there is a
                      permission that reads as granted and does nothing
      graph only   -- no sidecar, nothing to read
      never update -- on a repo with a semantic index it can bill a cloud
                      account; the plugin runs it AFTER the verdict, on terms
                      somebody chose

    Until now this was pinned by GREPPING qd/engine.py for the `if` line, which
    a widening that KEEPS that line and adds a branch below it passes untouched.
    See specs/graph_allow_spec.py, where that assertion is replaced."""

    def test_scoped_with_a_graph_gets_the_read_only_pattern(self):
        self.assertEqual(graph_shell_grant("scoped", True, None),
                         [graph.read_only_allow()])

    def test_it_is_added_to_what_the_caller_asked_for_not_instead_of_it(self):
        got = graph_shell_grant("scoped", True, ["^make$"])
        self.assertIn("^make$", got)
        self.assertIn(graph.read_only_allow(), got)

    def test_every_other_mode_gets_nothing_because_it_has_no_shell(self):
        # The widening mutation dies here: granting the graph under a mode with
        # no shell is a permission a caller would believe in and the worker
        # could never use -- and under a mode that DOES have a wider shell it
        # is a boundary nobody decided to move.
        for mode in ("auto-edit", "default", "plan", "yolo", "auto"):
            self.assertEqual(graph_shell_grant(mode, True, ["^make$"]),
                             ["^make$"], mode)
            self.assertIsNone(graph_shell_grant(mode, True, None), mode)

    def test_no_graph_means_no_grant_even_under_scoped(self):
        self.assertEqual(graph_shell_grant("scoped", False, ["^make$"]),
                         ["^make$"])
        self.assertIsNone(graph_shell_grant("scoped", False, None))

    def test_saying_nothing_stays_nothing_rather_than_becoming_an_empty_list(self):
        # qd/core/plan.py: "the caller said nothing" and "the caller said none"
        # are different answers and the codebase keeps them different. A grant
        # that declines to fire must hand back what it was given, unchanged.
        self.assertIsNone(graph_shell_grant("auto-edit", False, None))

    def test_it_is_not_granted_twice(self):
        # A caller (or a project config) may already carry the pattern. Adding
        # a second copy widens nothing but makes QGATE_EXTRA unreadable and any
        # equality assertion on it drift by one.
        already = [graph.read_only_allow()]
        self.assertEqual(graph_shell_grant("scoped", True, already), already)
        self.assertEqual(
            graph_shell_grant("scoped", True, ["^make$", graph.read_only_allow()]),
            ["^make$", graph.read_only_allow()])

    def test_what_it_grants_can_read_the_graph_and_cannot_update_it(self):
        # The grant is pinned by what the pattern PERMITS, not by its text: a
        # rewrite to `^graphify\b` would keep every test above green and hand
        # the worker the one subcommand that spends money.
        pat = graph_shell_grant("scoped", True, None)[0]
        self.assertTrue(re.match(pat, "graphify explain delegate()"))
        self.assertTrue(re.match(pat, "graphify query 'where is x'"))
        self.assertFalse(re.match(pat, "graphify update ."))
        self.assertFalse(re.match(pat, "graphify update --semantic"))


class ReportedPeakIsAHighWaterMark(unittest.TestCase):
    """`peak` is the largest context any attempt reached, not the last one's.

    A run whose attempt 1 nearly compacted and whose attempt 3 was small is
    exactly the run a caller needs told about, and taking the last attempt's
    figure erases it from all three places the number exists to be read: the
    APPROACHING COMPACTION warning, the RUN line's `peak N% ctx`, and the
    ledger's peak-ctx record. Every one of them keeps reporting a number, so
    nothing looks wrong."""

    def test_an_earlier_larger_attempt_survives_a_smaller_later_one(self):
        # The bug this rule exists for.
        self.assertEqual(peak_high_water(150_000, 5_000), 150_000)

    def test_a_larger_later_attempt_raises_the_mark(self):
        self.assertEqual(peak_high_water(5_000, 150_000), 150_000)

    def test_the_first_attempt_sets_it_from_nothing(self):
        self.assertEqual(peak_high_water(0, 40_000), 40_000)

    def test_an_attempt_that_reported_no_peak_does_not_lower_it(self):
        # A streamed run whose usage lines were unparseable reports 0. That is
        # "not known", never "the context shrank" -- and letting it overwrite
        # would lose the mark to a parsing gap rather than to a real number.
        self.assertEqual(peak_high_water(40_000, 0), 40_000)
        self.assertEqual(peak_high_water(40_000, None), 40_000)
        self.assertEqual(peak_high_water(None, 40_000), 40_000)
        self.assertEqual(peak_high_water(None, None), 0)


class ASlowGateIsOnePastHalfItsBudget(unittest.TestCase):
    """The threshold is HALF the budget, and the reason is arithmetic: the same
    command runs again after every attempt, so at max_iter 3 a gate past half
    its budget can outlast the work it is grading.

    `verify_timeout_sec` is seconds and `gate_ms` is milliseconds, so the
    conversion and the halving are folded into one constant (x500) -- which is
    exactly why it can be wrong by a factor of ten and still look plausible.
    The existing engine test stubs the gate at 90% of budget, where x100, x500
    and x1000 all agree; both discriminating sides are pinned here."""

    def test_a_gate_past_half_the_budget_is_slow(self):
        self.assertTrue(gate_is_slow(60_000, 100))     # 60% of 100s
        self.assertTrue(gate_is_slow(270_000, 300))    # 90% of the 300s default

    def test_a_gate_well_inside_half_is_not(self):
        # Kills a x100 threshold: 30s of a 100s budget is a third, and the
        # warning exists to be rare enough to mean something.
        self.assertFalse(gate_is_slow(30_000, 100))
        self.assertFalse(gate_is_slow(1_000, 300))

    def test_exactly_half_is_not_yet_slow(self):
        # Kills a x1000 threshold in the other direction, and fixes the
        # boundary: "past half" is strict, so half itself is fine.
        self.assertFalse(gate_is_slow(50_000, 100))
        self.assertTrue(gate_is_slow(50_001, 100))

    def test_a_gate_that_cost_nothing_is_never_slow(self):
        self.assertFalse(gate_is_slow(0, 10))


if __name__ == "__main__":
    unittest.main(verbosity=2)
