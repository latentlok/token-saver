#!/usr/bin/env python3
"""
Spec for the four qd/core/pipeline.py rules AS THE LOOP USES THEM.

Claude-authored gate (never delegate this file -- it defines what correct means).

**Why this file exists at all.** specs/pipeline_spec.py pins the rules. This one
pins that `_delegate` asks them. Five of the nine bugs found in this round were
WIRING: a mechanism with a good passing test, and nothing asserting the engine
was connected to it. The pattern has a name in this repo already --
specs/chain_spec.py carries a test called
`test_a_chain_link_after_the_first_is_marked_unshareable` whose body asserts
only that `run_chain` INJECTS chain positions. Nothing in the suite asserted
that `_delegate` turns position 2 into `shareable=False`; the rule the test is
named after was never reached. Extracting these four into named functions makes
that gap easy to re-open -- a helper can be perfect and uncalled -- so every
rule below is observed through a real `engine.delegate()` run.

What is observed, and why that observation is the honest one:

  pre-flight sharing  the `isolated` argument `_delegate` hands `_preflight_once`
                      -- the single value that decides whether this run's gate
                      verdict comes from its own tree or another item's
  graph grant         QGATE_EXTRA, the env the worker's hook actually reads.
                      A permission boundary is what reaches the boundary, not
                      what a source line says (see specs/graph_allow_spec.py,
                      where the grep this replaces used to live)
  peak                ctx["peak"] after a multi-attempt run -- the value the
                      compaction warning, the RUN line and the ledger all read
  gate_slow           ctx["gate_slow"] with the gate stubbed to a known cost

Driven through specs/engine_spec.py's Fixture (its stub executor, its scenario
steps, its throwaway repo) -- the same seam telemetry_spec and detectors_spec
use, so these runs are the real loop and not a reconstruction of it.

Run:  python3 specs/pipeline_wiring_spec.py
"""

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from engine_spec import Fixture as EngineFixture  # noqa: E402
from qd import engine, graph  # noqa: E402
from qd.engine import CHAIN_ARG  # noqa: E402

# A gate whose RED output differs before and after attempt 1, so the run retries
# instead of bailing as gate_suspect: with no out.py the interpreter raises
# (traceback), with a wrong out.py it prints GATEMSG. engine_spec drives the
# retry path with exactly this command; a constant-output gate would end every
# run below on attempt 1 and no accumulation could be observed.
RETRY_GATE = ("python3 -c \"import sys; sys.exit(0 if 'MARKER' in "
              "open('out.py').read() else print('GATEMSG out.py lacks MARKER') "
              "or 1)\"")


class ThePreflightVerdictIsSharedOnlyFromACleanBase(EngineFixture):
    """`_preflight_once` shares one gate run across items keyed on (base sha,
    worktrees dir, gate). Its invariant -- "every item is cut from the SAME base
    commit into its own clean worktree" -- is the batch's, and a chain link
    after the first violates it by design: its tree holds the previous links'
    commits. The engine is the only place that knows which of the two this run
    is, so this is the assertion that matters.

    Observed on the `isolated` argument rather than on cache behaviour: whether
    a shared verdict is served is `_preflight_once`'s business (pinned by
    specs/fleet_spec.py SharedPreflight and specs/chain_spec.py ChainPreflight,
    both of which pass the boolean in BY HAND). What was never pinned is which
    boolean this run computes."""

    def shared_flag(self, **over):
        """The `isolated` argument this delegation hands the shared pre-flight."""
        seen = []
        real = engine._preflight_once

        # Trailing *rest/**kw on purpose: `_preflight_once` has grown optional
        # arguments before (`served`) and a spy pinned to today's arity fails
        # with a TypeError that says nothing about the rule it was written for.
        # Only the five positional arguments this test is about are named.
        def spy(cmd, work_cwd, timeout, base_sha, isolated, *rest, **kw):
            seen.append(isolated)
            return (False, "", 3, False)

        engine._preflight_once = spy
        self.addCleanup(setattr, engine, "_preflight_once", real)
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        self.delegate(**over)
        self.assertEqual(len(seen), 1, "the pre-flight did not run exactly once")
        return seen[0]

    def test_a_run_holding_its_own_worktree_shares_its_verdict(self):
        # A13's whole point: N items off one base pay for one gate run.
        self.assertTrue(self.shared_flag(worktree="auto"))

    def test_a_chain_link_after_the_first_does_not(self):
        # The rule. Without it link 2 is graded on link 1's verdict, taken
        # against a tree that did not yet contain link 1's commits -- and the
        # answer looks exactly like a real one.
        self.assertFalse(
            self.shared_flag(worktree="auto", **{CHAIN_ARG: {"pos": 2, "of": 3}}))

    def test_the_first_link_of_a_chain_still_shares(self):
        # The other side, so the rule cannot be "satisfied" by refusing to
        # share for anything carrying a chain position at all.
        self.assertTrue(
            self.shared_flag(worktree="auto", **{CHAIN_ARG: {"pos": 1, "of": 3}}))

    def test_an_in_tree_run_never_shares(self):
        # "off" is the default here. No container means the working tree may
        # have moved between runs, so a cached verdict describes a tree nobody
        # re-read.
        self.assertFalse(self.shared_flag())
        self.assertFalse(self.shared_flag(**{CHAIN_ARG: {"pos": 1, "of": 2}}))


class TheGraphGrantReachesTheWorkersGate(EngineFixture):
    """The behavioural half of what specs/graph_allow_spec.py's TheGrant used to
    assert by reading qd/engine.py as text.

    QGATE_EXTRA is what scoped_hook.py consults when the worker asks to run a
    command, so it is the boundary itself. A widening that keeps the grepped
    source line and adds a branch under another mode passes a text assertion and
    fails here."""

    def with_a_graph(self):
        # graph.read_state() parses .qwen-delegate/graph.json; sidecar_path
        # creates the (self-ignoring) directory, so this leaves the tree clean
        # and the dirty-tree precondition unbothered.
        with open(graph.sidecar_path(self.cwd), "w") as f:
            json.dump({"indexed_sha": "0" * 40, "files": 1}, f)

    def extra(self, n=1):
        return json.loads(self.env_seen(n)["QGATE_EXTRA"])

    def test_scoped_with_a_graph_hands_the_worker_the_read_only_pattern(self):
        # The promise bootstrap_line() makes to the caller, finally kept.
        self.with_a_graph()
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        self.delegate(approval_mode="scoped")
        self.assertIn(graph.read_only_allow(), self.extra())

    def test_auto_edit_gets_nothing_even_with_a_graph(self):
        # auto-edit has no shell at all, so a pattern here is a permission that
        # reads as granted and does nothing -- worse than absent, because a
        # caller would believe the worker could use the graph. This is the
        # assertion the source-text grep could not make: a widening branch that
        # leaves the grepped `if` line untouched shows up right here.
        #
        # `autoedit_via_hook` is turned ON so the allowlist channel EXISTS to be
        # inspected. engine_spec's harness opts the machine layer out of it, and
        # without a hook there is no QGATE_EXTRA at all -- under which this test
        # would pass by looking at nothing, which is the failure mode the whole
        # task is about. The project layer beats the machine layer (engine.py
        # merges project over global), so committing it here is enough.
        self.commit_cfg({"challenge_brief": False, "autoedit_via_hook": True})
        self.with_a_graph()
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate(approval_mode="auto-edit")
        self.assertEqual(r["ctx"]["attribution"], "hook",
                         "the hook was off, so QGATE_EXTRA proves nothing")
        self.assertNotIn(graph.read_only_allow(), self.extra())
        self.assertEqual(self.extra(), [])

    def test_scoped_without_a_graph_gets_nothing(self):
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        self.delegate(approval_mode="scoped")
        self.assertNotIn(graph.read_only_allow(), self.extra())

    def test_it_is_not_handed_over_twice(self):
        # A project may already declare it. Two copies widen nothing and make
        # the allowlist unreadable at exactly the moment somebody is auditing it.
        self.with_a_graph()
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        self.delegate(approval_mode="scoped",
                      shell_allow=[graph.read_only_allow()])
        self.assertEqual(self.extra(), [graph.read_only_allow()])

    def test_the_callers_own_patterns_are_kept(self):
        # The grant ADDS; it must not replace what the caller approved.
        self.with_a_graph()
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        self.delegate(approval_mode="scoped", shell_allow=["^make$"])
        self.assertIn("^make$", self.extra())
        self.assertIn(graph.read_only_allow(), self.extra())


class TheReportedPeakSurvivesTheAttemptThatFollowsIt(EngineFixture):
    """ctx["peak"] is the largest context any attempt reached.

    Taking the last attempt's figure instead loses the run that matters most --
    attempt 1 nearly compacting, attempt 3 small -- from all three readers of
    the number (the APPROACHING COMPACTION warning, the RUN line's `peak N%
    ctx`, and the ledger's peak-ctx record). Each keeps printing a number, so
    nothing looks broken. Nothing in the suite asserted on ctx["peak"] at all;
    the accumulation was reachable only by tracing the loop."""

    def two_attempts(self, first, second):
        self.steps([{"write": {"out.py": "wrong\n"}, "usage": first},
                    {"write": {"out.py": "MARKER fixed\n"}, "usage": second}])
        r = self.delegate(verify=RETRY_GATE)
        self.assertEqual(r["status"], "success")
        self.assertEqual(len(r["trail"]), 2, "the run did not take two attempts")
        return r["ctx"]["peak"]

    def test_a_smaller_second_attempt_does_not_erase_the_first_ones_spike(self):
        # The bug. The run really did come close to compaction, and the caller
        # is entitled to know that whichever attempt happened to finish it.
        self.assertEqual(self.two_attempts(90_000, 5_000), 90_000)

    def test_a_larger_second_attempt_raises_the_mark(self):
        # The other side, so "always keep the first" is not a passing answer.
        self.assertEqual(self.two_attempts(5_000, 90_000), 90_000)


class TheSlowGateFlagIsSetAtHalfTheBudget(EngineFixture):
    """`gate_slow` warns that the pre-flight burned more than HALF its budget --
    because the same command runs again after every attempt, so at max_iter 3
    the gate alone can outlast the work it is grading.

    The existing engine_spec test stubs the gate at 90% of budget, where x100,
    x500 and x1000 all say "slow". Half exists only in the comment. Both
    discriminating sides are driven here."""

    def gate_costing(self, per_second_ms):
        # The stub returns a gate cost proportional to the budget it was given:
        # `timeout * 1000` is the whole budget, so 300 is 30% and 600 is 60%.
        real = engine._run_verify_timed
        engine._run_verify_timed = lambda cmd, cwd, timeout: (
            False, "red", int(timeout * per_second_ms), False)
        self.addCleanup(setattr, engine, "_run_verify_timed", real)
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        return self.delegate(max_iterations=1, verify_timeout_sec=100)["ctx"]

    def test_a_gate_at_thirty_percent_of_its_budget_is_not_slow(self):
        # Kills a x100 threshold, which would call a third of the budget slow
        # and put the warning on runs that are fine -- and a warning that fires
        # on ordinary runs is one nobody reads on the run that mattered.
        ctx = self.gate_costing(300)
        self.assertEqual(ctx["gate_ms"], 30_000)
        self.assertFalse(ctx["gate_slow"])

    def test_a_gate_at_sixty_percent_of_its_budget_is_slow(self):
        # Kills a x1000 threshold, which would only warn once the gate was
        # about to time out -- by which point U3.1's refusal handles it and the
        # flag has nothing left to say.
        ctx = self.gate_costing(600)
        self.assertEqual(ctx["gate_ms"], 60_000)
        self.assertTrue(ctx["gate_slow"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
