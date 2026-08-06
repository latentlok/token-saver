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
from qd.features import detectors  # noqa: E402


def boom(*_a, **_k):
    """A detector that raises. The tree is written by another process while
    these greps run, so this is the realistic failure, not a contrived one."""
    raise RuntimeError("detector exploded")


class OneDetectorsFailure(Fixture):
    """What a single detector is allowed to cost when it raises."""

    def patch(self, kind, symbol):
        """Break one detector for the duration of a test.

        Patches the gittree function INSIDE its detector module -- that module
        attribute is the real call site, so the failure travels the same path a
        genuine one would, through `run_all`'s guard.
        """
        mod = getattr(detectors, kind)
        real = getattr(mod, symbol)
        setattr(mod, symbol, boom)
        self.addCleanup(setattr, mod, symbol, real)

    def found(self, r, kind, default=None):
        """The payload one detector reported, by kind."""
        return detectors.find(r["ctx"]["detections"], kind, default)

    def test_a_failing_detector_does_not_discard_the_facts(self):
        # The expensive half. `dodge_markers` raising used to land in the
        # handler that sets tree_facts = None, so a receipt lost CHANGED and
        # COMMITTED -- everything the run actually observed -- because one grep
        # over one file failed. The facts were already collected and correct at
        # that point; nothing about a detector's failure makes them untrue.
        self.patch("dodge", "dodge_markers")
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
        self.patch("dodge", "dodge_markers")
        self.steps([{"write": {"out.py": "MARKER\n",
                               "scratch_dump.py": "debris = 1\n"}}])
        r = self.delegate()
        self.assertEqual(self.found(r, "strays"), ["scratch_dump.py"])

    def test_a_failing_seam_grep_does_not_silence_its_neighbours(self):
        # The three seam greps shared one try. They are three independent
        # questions about the tree -- one being unanswerable says nothing about
        # the other two.
        self.patch("uncalled", "uncalled_symbols")
        self.steps([{"write": {"out.py": "MARKER\n",
                               "test_new_qwen.py": "def test_a():\n    pass\n"}}])
        r = self.delegate()
        self.assertIsNotNone(r["ctx"]["tree_facts"])
        self.assertEqual(self.found(r, "never_executed"),
                         ["test_new_qwen.py"])

    def test_a_failing_detector_is_reported_as_absent_not_as_empty(self):
        # A finding that could not be computed and a finding that came back
        # empty must not look the same downstream. PRINCIPLES §IV: a zero
        # meaning "nothing found" and a zero meaning "nothing was measured"
        # have to be distinguishable, or every zero is worthless as evidence.
        # `detections_failed` is what keeps them apart -- a silent detector is
        # absent from BOTH lists, a broken one is named here. The renderer
        # treats them alike today (both render nothing); the parked
        # SUPPRESSED: line is what will finally tell a caller which happened.
        self.patch("uncalled", "uncalled_symbols")
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate()
        self.assertIn("uncalled", r["ctx"]["detections_failed"])
        self.assertIsNone(self.found(r, "uncalled"))


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
        self.assertEqual(detectors.find(r["ctx"]["detections"], "dodge"),
                         {"test_thing.py": ["@unittest.skip"]})
        self.assertEqual(detectors.find(r["ctx"]["detections"], "strays"),
                         ["scratch_dump.py"])
        self.assertIsNotNone(r["ctx"]["tree_facts"])

    def write_test_file(self, name, body):
        path = os.path.join(self.cwd, name)
        with open(path, "w") as f:
            f.write(body)
        subprocess.run(["git", "-C", self.cwd, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.cwd, "commit", "-qm", "tests"],
                       check=True)


class FindingsAreNotFacts(Fixture):
    """§4's rule, made checkable: a feature may READ facts and never WRITE them.

    Step 1 extracted the facts and, in doing so, exposed that the detectors were
    writing their results straight back into the record they read from
    (`tf["uncalled"] = ...`). That is the confusion the whole design turns on:
    `pubs` was OBSERVED and `uncalled` was CONCLUDED, and once they sit in the
    same dict nobody downstream can tell which is which. It also gives the
    detectors a hidden ordering -- anything reading a written-back key only
    works if the detector that wrote it happened to run first.

    A detector that can only RETURN cannot leave anything behind for another
    detector to depend on, so the ordering problem disappears rather than being
    managed. That is what makes adding and removing one a local change.
    """

    def test_the_facts_record_carries_no_findings(self):
        self.steps([{"write": {"out.py": "MARKER\n"
                                         "def run_threads():\n    return 1\n"}}])
        facts = self.delegate()["ctx"]["tree_facts"]
        for kind in ("uncalled", "mocked_seams", "never_executed"):
            self.assertNotIn(kind, facts,
                             f"{kind} is a finding living inside the facts")

    def test_the_facts_record_still_carries_every_fact(self):
        # The other half: proving findings left must not prove observations did.
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        facts = self.delegate()["ctx"]["tree_facts"]
        for key in ("post_status", "changed", "numstat", "head_moved",
                    "head_now", "pubs"):
            self.assertIn(key, facts, key)

    def test_findings_arrive_as_a_list_the_caller_can_enumerate(self):
        self.steps([{"write": {"out.py": "MARKER\n"
                                         "def run_threads():\n    return 1\n"}}])
        got = self.delegate()["ctx"]["detections"]
        self.assertIn("uncalled", [f.kind for f in got])
        payload = [f.data for f in got if f.kind == "uncalled"][0]
        self.assertEqual(payload, {"out.py": ["run_threads"]})

    def test_a_detector_with_nothing_to_report_yields_no_finding(self):
        # Silence is the absence of a finding, not an empty one. A list that
        # carries a finding per detector regardless would make "did anything
        # fire?" a question about payload contents rather than list membership.
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        got = self.delegate()["ctx"]["detections"]
        self.assertNotIn("strays", [f.kind for f in got])

    def test_every_detector_is_enumerable(self):
        # The property the old shape could not offer at any price: five calls
        # spread over one function, with nothing to list them. Adding the sixth
        # meant knowing where the other five were.
        from qd.features import detectors
        self.assertEqual(
            sorted(d.KIND for d in detectors.DETECTORS),
            ["dodge", "mocked_seams", "never_executed", "strays", "uncalled",
             "unmarked_tests"])


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


class TheFeatureOwnsItsLine(Fixture):
    """Step 3's rule: the renderer no longer knows what a finding SAYS.

    Before this, every feature that wanted a line edited an 888-line function,
    and that is measurably how `verdict.render` became the second god function
    -- 20 inline branches, each carrying its own text, droppability and drop
    priority. Now a detector is one file holding all three (`KIND`, `detect`,
    `block`), and the renderer holds only WHERE the block goes.

    Where still matters and is deliberately still the renderer's: position is
    the size cap's tie-break among equal priorities (qd/surface/receipt.py rule
    1), so it is a property of the receipt as a whole, not of any one feature.
    """

    def test_the_receipt_prints_what_the_feature_says(self):
        # The proof that the text moved rather than being duplicated: change
        # what the feature returns, and the receipt changes. If the renderer
        # still held a copy, this would print the old wording.
        from qd.surface.receipt import Block
        real = detectors.uncalled.block
        detectors.uncalled.block = lambda data: [
            Block("uncalled", "UNCALLED: rewritten by the feature", True, 1)]
        self.addCleanup(setattr, detectors.uncalled, "block", real)
        self.steps([{"write": {"out.py": "MARKER\n"
                                         "def run_threads():\n    return 1\n"}}])
        out = engine.run({"task": "t", "cwd": self.cwd,
                          "verify": "grep -q MARKER out.py",
                          "approval_mode": "auto-edit", "executor": "stub",
                          "challenge_brief": False})
        self.assertIn("UNCALLED: rewritten by the feature", out)

    def test_every_detector_can_render_what_it_detects(self):
        # A detector with `detect` but no `block` would compute a finding that
        # can never reach a caller -- step 2's registry made that possible to
        # write, so step 3 has to make it impossible to forget.
        for d in detectors.DETECTORS:
            self.assertTrue(callable(getattr(d, "block", None)),
                            f"{d.KIND} has no block()")


class UnmarkedWorkerTests(Fixture):
    """A6: a worker test without the `_qwen` marker has lost its provenance.

    Tier and provenance are orthogonal (DESIGN-v06-test-first.md §2.5): the
    directory says WHEN a test runs, the suffix says WHOSE it is. Three things
    already depend on the suffix -- `_strays` stays quiet about `*_qwen.*`,
    `ci/run-specs.sh` deliberately runs them, and most of all a worker test
    named like a Claude-authored one is a file nobody can attribute. The gate
    coming from a different hand is what makes a green receipt mean anything
    (PRINCIPLES §I).

    This class is also the restructure's headline claim, checked as an artifact
    rather than asserted in a design doc: A6 is ONE new file plus ONE line in
    DETECTORS. Nothing in the engine or the renderer changed to accept it.
    """

    def test_a_worker_test_without_the_marker_is_named(self):
        self.steps([{"write": {"out.py": "MARKER\n",
                               "test_thing.py": "def test_a():\n    pass\n"}}])
        r = self.delegate()
        self.assertEqual(detectors.find(r["ctx"]["detections"], "unmarked_tests"),
                         ["test_thing.py"])

    def test_the_sanctioned_convention_is_silent(self):
        # The control that stops this firing on every correct run.
        self.steps([{"write": {"out.py": "MARKER\n",
                               "test_thing_qwen.py": "def test_a():\n    pass\n"}}])
        r = self.delegate()
        self.assertIsNone(
            detectors.find(r["ctx"]["detections"], "unmarked_tests"))

    def test_ordinary_source_is_not_a_test(self):
        # Deliberately narrow: a file is a test only if its NAME says so.
        # Inferring from content would fire on fixtures and helpers, and a
        # detector that cries wolf gets switched off wholesale -- the lesson
        # TEST DODGE paid for, wrong 4 times out of 4 on an ordinary refactor.
        self.steps([{"write": {"out.py": "MARKER\nimport unittest\n"}}])
        r = self.delegate()
        self.assertIsNone(
            detectors.find(r["ctx"]["detections"], "unmarked_tests"))

    def test_it_reaches_the_receipt(self):
        self.steps([{"write": {"out.py": "MARKER\n",
                               "test_thing.py": "def test_a():\n    pass\n"}}])
        out = engine.run({"task": "t", "cwd": self.cwd,
                          "verify": "grep -q MARKER out.py",
                          "approval_mode": "auto-edit", "executor": "stub",
                          "challenge_brief": False})
        self.assertIn("UNMARKED TEST:", out)
        self.assertIn("test_thing.py", out)


class AddingOneIsLocal(Fixture):
    """The restructure's headline claim, checked rather than asserted.

    "Adding a detector is one file plus one line in DETECTORS" was FALSE until
    the SLOT mechanism landed, and building A6 is what exposed it: the renderer
    named each detector's placement, so a detector with the registry entry but
    no render line computed a finding nobody ever saw -- silently, whole suite
    green. That is the exact failure shape this round kept finding.

    Placement could not simply follow registration order, because it is the size
    cap's TIE-BREAK among equal priorities. So each detector DECLARES where it
    goes and the renderer asks.

    This test registers a detector that exists nowhere in qd/verdict.py and
    requires its line on the receipt. If someone reintroduces a named emit, this
    still passes -- so it is paired with the inventory test below, which fails
    the moment a registered detector has no region.
    """

    def test_a_detector_the_renderer_has_never_heard_of_still_renders(self):
        from qd.core.findings import Finding
        from qd.surface.receipt import Block

        class Invented:
            KIND = "invented"
            REGION, SLOT = "LATE", 99
            @staticmethod
            def detect(facts, scope, plan):
                return Finding("invented", ["proof.py"])
            @staticmethod
            def block(data):
                return [Block("invented", "INVENTED: " + ", ".join(data),
                              True, 1)]

        real = detectors.DETECTORS
        detectors.DETECTORS = real + (Invented,)
        self.addCleanup(setattr, detectors, "DETECTORS", real)

        self.steps([{"write": {"out.py": "MARKER\n"}}])
        out = engine.run({"task": "t", "cwd": self.cwd,
                          "verify": "grep -q MARKER out.py",
                          "approval_mode": "auto-edit", "executor": "stub",
                          "challenge_brief": False})
        self.assertIn("INVENTED: proof.py", out,
                      "a registered detector produced a finding nobody rendered")

    def test_every_registered_detector_declares_where_it_goes(self):
        # The other half. A detector without a region renders nowhere, and the
        # test above would not catch that for the OTHER detectors.
        for d in detectors.DETECTORS:
            self.assertIn(getattr(d, "REGION", None), ("FIXED", "EARLY", "LATE"),
                          f"{d.KIND} declares no region")
            self.assertIsInstance(getattr(d, "SLOT", None), int, d.KIND)


if __name__ == "__main__":
    unittest.main(verbosity=2)
