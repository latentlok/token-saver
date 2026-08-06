#!/usr/bin/env python3
"""
Spec for qd/core/status.py -- what a finished run IS.

Claude-authored gate (never delegate this file -- it defines what correct means).

The first piece of `core/pipeline.py` (DESIGN §5), taken out on its own because
it is the cleanest seam in the run loop: a PURE function of the attempt trail
and three flags, buried inside a 1,135-line function that could only be tested
by driving a whole delegation through a stub executor.

**Why this is the piece worth taking first.** Every step of this restructure so
far has extracted a NOUN -- facts, findings, scope, plan, blocks, gates -- and
that is exactly why `_delegate` never shrank: the nouns left and the sequence
that orders them stayed. This is the first VERB, and it is the one with no
dependencies at all.

The rules it carries, none of which were previously stated anywhere but as the
order of an elif chain:

  1. ORDER IS PRECEDENCE. Every branch is a more specific diagnosis than the
     one below it. A stalled run that also violated scope is a scope violation
     first -- the worker touching the gate is the more serious fact.
  2. `reported` overrides only the GATE-OUTCOME statuses. A stopped, compacted
     or fixture-unproven run really did end for that reason, and calling it
     "reported" would hide it.
  3. The preflight demotion happens HERE, not at render time -- chains, the run
     log and every server-side consumer read this status, and a receipt-only
     demotion left all of them believing a vacuous pass was a clean success.

Run:  python3 specs/status_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.core.status import classify  # noqa: E402


def s(*trail, **kw):
    return classify(list(trail), **kw)


class FromTheTrail(unittest.TestCase):
    def test_no_attempts_at_all_is_an_error(self):
        self.assertEqual(classify([]), "error")

    def test_a_passing_gate_is_success(self):
        self.assertEqual(s("attempt 1: VERIFY PASS"), "success")

    def test_each_diagnosis_has_its_own_status(self):
        for line, expect in (
                ("attempt 1: RESULT SCHEMA invalid", "result_invalid"),
                ("attempt 1: run stopped: budget", "stopped"),
                ("attempt 1: COMPACTION refused", "compaction_refused"),
                ("attempt 1: SPEC VIOLATION -- edited x", "spec_violation"),
                ("attempt 1: PLAYBOOK EDITED", "spec_violation"),
                ("attempt 1: TOUCH SCOPE VIOLATION", "scope_violation"),
                ("attempt 1: verify failed -- output IDENTICAL to preflight",
                 "gate_suspect"),
                ("attempt 1: FIXTURE PROVENANCE missing", "fixture_unproven"),
                ("attempt 1: no verify supplied", "unverified")):
            self.assertEqual(s(line), expect, line)

    def test_an_ordinary_failure_is_verify_failed(self):
        self.assertEqual(s("attempt 1: verify failed"), "verify_failed")


class OrderIsPrecedence(unittest.TestCase):
    """The rule the elif chain encoded and nothing stated."""

    def test_a_spec_violation_outranks_a_stall(self):
        # A stalled run that ALSO touched the gate is the gate being touched.
        # That is the more serious fact and the one the caller must act on.
        self.assertEqual(s("attempt 3: SPEC VIOLATION -- edited guard_spec.py",
                           no_progress=True), "spec_violation")

    def test_a_stall_outranks_a_plain_failure(self):
        self.assertEqual(s("attempt 3: verify failed", no_progress=True),
                         "stuck_no_progress")

    def test_a_pass_outranks_everything(self):
        self.assertEqual(s("attempt 1: VERIFY PASS", no_progress=True),
                         "success")


class ReportRuns(unittest.TestCase):
    def test_a_report_runs_red_gate_is_the_deliverable(self):
        # `report_dont_fix` asks why something fails. A red gate there is the
        # ANSWER, and "verify_failed" would read as the worker having failed at
        # a job it was told not to do.
        self.assertEqual(s("attempt 1: verify failed", report=True), "reported")

    def test_a_report_run_that_was_stopped_still_says_so(self):
        # The override is deliberately narrow: a stopped, compacted or
        # fixture-unproven run really did end for that reason, and calling it
        # "reported" would hide it.
        for line, expect in (("attempt 1: run stopped: budget", "stopped"),
                             ("attempt 1: COMPACTION refused",
                              "compaction_refused"),
                             ("attempt 1: FIXTURE PROVENANCE missing",
                              "fixture_unproven")):
            self.assertEqual(s(line, report=True), expect, line)


class ThePreflightDemotion(unittest.TestCase):
    def test_a_gate_that_was_already_green_is_demoted(self):
        self.assertEqual(s("attempt 1: VERIFY PASS", preflight=True),
                         "success_but_preflight_passed")

    def test_declared_revision_work_is_not_demoted(self):
        # preflight_expect="green" is revision work: a passing gate beforehand
        # is the premise, not a warning, and crying wolf on every such task is
        # how a signal stops being read.
        self.assertEqual(s("attempt 1: VERIFY PASS", preflight=True,
                           preflight_expect="green"), "success")

    def test_only_a_success_is_demoted(self):
        self.assertEqual(s("attempt 1: verify failed", preflight=True),
                         "verify_failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
