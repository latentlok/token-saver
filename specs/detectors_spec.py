#!/usr/bin/env python3
"""
Spec for the detectors -- the things that OBSERVE a finished run and report.

Claude-authored gate (never delegate this file -- it defines what correct means).

Step 2 of docs/DESIGN-modular-architecture.md §4. Facts are computed once and
read by everyone; findings are pure functions of facts. This file owns the half
of that rule the facts spec cannot state: what a detector is allowed to cost
when it goes wrong.

The rule:

    A detector's failure costs its own finding. Nothing else.

Why it needs a spec of its own. The detectors are five greps over a tree that
another process was writing a moment ago, so any of them CAN raise -- and the
consequence of one raising was, until this spec, wildly unequal:

  * the three seam greps shared one `try`, so `uncalled_symbols` failing took
    `mocked_seams` and `never_executed` down with it, and
  * `dodge_markers` and `_strays` sat in the OUTER `try` whose handler sets
    `tree_facts = None` -- so one failed grep DISCARDED EVERY FACT. The receipt
    lost CHANGED, COMMITTED and the seam lines at once and fell back to the v1
    re-read path, with nothing anywhere saying why.

That asymmetry is invisible in a green suite: nothing raises on a healthy tree,
so the blast radius of a failure is only ever observed in the field. Pinning it
here is what makes the step-2 registry a faithful move rather than a silent
behaviour change -- a uniform loop isolates each detector naturally, and without
this spec that improvement would arrive disguised as a refactor.

Run:  python3 specs/detectors_spec.py
"""

import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from engine_spec import Fixture  # noqa: E402
from qd import engine  # noqa: E402


def boom(*_a, **_k):
    """A detector that raises. The tree is written by another process while
    these greps run, so this is the realistic failure, not a contrived one."""
    raise RuntimeError("detector exploded")


class OneDetectorsFailure(Fixture):
    """What a single detector is allowed to cost when it raises."""

    def patch(self, name):
        """Replace one detector for the duration of a test.

        The engine imports these by name at module scope, so the module
        attribute IS the call site -- patching it exercises the real handler
        rather than a stand-in for it.
        """
        real = getattr(engine, name)
        setattr(engine, name, boom)
        self.addCleanup(setattr, engine, name, real)

    def test_a_failing_detector_does_not_discard_the_facts(self):
        # The expensive half. `dodge_markers` raising used to land in the
        # handler that sets tree_facts = None, so a receipt lost CHANGED and
        # COMMITTED -- everything the run actually observed -- because one grep
        # over one file failed. The facts were already collected and correct at
        # that point; nothing about a detector's failure makes them untrue.
        self.patch("dodge_markers")
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate()
        self.assertEqual(r["status"], "success")
        self.assertIsNotNone(r["ctx"]["tree_facts"],
                             "a detector's failure discarded the facts")
        self.assertIn("out.py", r["ctx"]["tree_facts"]["changed"])

    def test_a_failing_detector_does_not_silence_the_ones_after_it(self):
        # Ordering must not be load-bearing. `_strays` ran after `dodge_markers`
        # in the same try, so dodge raising skipped strays entirely -- and
        # `strays` has a default of [], which reads on the receipt exactly like
        # "this run left no debris".
        self.patch("dodge_markers")
        self.steps([{"write": {"out.py": "MARKER\n",
                               "scratch_dump.py": "debris = 1\n"}}])
        r = self.delegate()
        self.assertEqual(r["ctx"]["strays"], ["scratch_dump.py"])

    def test_a_failing_seam_grep_does_not_silence_its_neighbours(self):
        # The three seam greps shared one try. They are three independent
        # questions about the tree -- one being unanswerable says nothing about
        # the other two.
        self.patch("uncalled_symbols")
        self.steps([{"write": {"out.py": "MARKER\n",
                               "test_new_qwen.py": "def test_a():\n    pass\n"}}])
        r = self.delegate()
        facts = r["ctx"]["tree_facts"]
        self.assertIsNotNone(facts)
        self.assertEqual(facts.get("never_executed") or [],
                         ["test_new_qwen.py"])

    def test_a_failing_detector_is_reported_as_absent_not_as_empty(self):
        # A finding that could not be computed and a finding that came back
        # empty must not be the same value downstream. PRINCIPLES §IV: a zero
        # meaning "nothing found" and a zero meaning "nothing was measured"
        # have to be distinguishable, or every zero is worthless. The renderer
        # reads these with `or {}` / `or []`, so None renders as silence today
        # -- this pins that the key EXISTS and is None rather than absent, so a
        # later reader can tell the two apart without guessing.
        self.patch("uncalled_symbols")
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate()
        facts = r["ctx"]["tree_facts"]
        self.assertIn("uncalled", facts)
        self.assertIsNone(facts["uncalled"])


class HealthyTreeUnchanged(Fixture):
    """The isolation must not alter what a run with no failures reports."""

    def test_every_detector_still_reports_on_a_healthy_tree(self):
        # The regression that a per-detector try could plausibly introduce:
        # swallowing a real result along with a real error.
        self.write_test_file("test_thing.py",
                             "import unittest\n\n\ndef test_a():\n    pass\n")
        self.steps([{"write": {"out.py": "MARKER\n",
                               "scratch_dump.py": "debris = 1\n",
                               "test_thing.py": "import unittest\n\n\n"
                                                "@unittest.skip('flaky')\n"
                                                "def test_a():\n    pass\n"}}])
        r = self.delegate()
        self.assertEqual(r["ctx"]["dodge"],
                         {"test_thing.py": ["@unittest.skip"]})
        self.assertEqual(r["ctx"]["strays"], ["scratch_dump.py"])
        self.assertIsNotNone(r["ctx"]["tree_facts"])

    def write_test_file(self, name, body):
        path = os.path.join(self.cwd, name)
        with open(path, "w") as f:
            f.write(body)
        subprocess.run(["git", "-C", self.cwd, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.cwd, "commit", "-qm", "tests"],
                       check=True)


class ReachesTheReceipt(Fixture):
    """Each finding must arrive in the text the caller actually reads.

    The gap this class closes. TEST DODGE and STRAYS were already pinned at the
    receipt (engine_spec: "TEST DODGE: ... adds pytest.mark.xfail", "STRAYS: 1
    file(s) not named in the task"). The three SEAM findings were not pinned
    anywhere but seams_spec, which calls the gittree functions directly and so
    proves only that the greps work -- not that anything renders what they
    return. A detector whose finding never reaches the receipt is a detector
    that does not exist, and until now that failure was invisible.

    This matters most for the step-2 move: it is the net under it. A refactor
    that severs the wire between a detector and the renderer fails HERE, and
    nowhere else in the suite.
    """

    def receipt(self, **over):
        args = {"task": "build out.py with MARKER", "cwd": self.cwd,
                "verify": "grep -q MARKER out.py",
                "approval_mode": "auto-edit", "executor": "stub",
                "max_iterations": 3, "challenge_brief": False}
        args.update(over)
        return engine.run(args)

    def test_uncalled_reaches_the_receipt(self):
        # A new public symbol referenced by nothing outside its own file:
        # built and wired to nothing, or built for a caller that does not
        # exist yet. The receipt says which line names it.
        self.steps([{"write": {"out.py": "MARKER\n"
                                         "def run_threads():\n    return 1\n"}}])
        out = self.receipt()
        self.assertIn("STATUS: success", out)
        self.assertIn("UNCALLED:", out)
        self.assertIn("run_threads", out)

    def test_mocked_seam_reaches_the_receipt(self):
        # The field case seams_spec records: the unit mocked the store, the
        # suite never executed the SQL, and a SELECT of a column that never
        # existed shipped green and died on first live contact.
        self.steps([{"write": {
            "out.py": "MARKER\n",
            "store.py": "def get_job_sources():\n    return []\n",
            "test_store_qwen.py": "from unittest import mock\n"
                                  "def test_it():\n"
                                  "    with mock.patch('store.get_job_sources'):\n"
                                  "        pass\n"}}])
        out = self.receipt()
        self.assertIn("MOCKED SEAM:", out)
        self.assertIn("store.py", out)

    def test_never_executed_reaches_the_receipt(self):
        # A delivered test file the gate command does not name. Written, never
        # run, so nothing here proves it passes -- and the gate is green.
        self.steps([{"write": {"out.py": "MARKER\n",
                               "test_new_qwen.py": "def test_a():\n    pass\n"}}])
        out = self.receipt()
        self.assertIn("NEVER EXECUTED:", out)
        self.assertIn("test_new_qwen.py", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
