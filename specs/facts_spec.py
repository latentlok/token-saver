#!/usr/bin/env python3
"""
Spec for qd/core/facts.py -- the run's tree observations, computed once.

Claude-authored gate (never delegate this file -- it defines what correct means).

Step 1 of docs/DESIGN-modular-architecture.md. Most of this file is about WHEN
and HOW MANY TIMES, not about values, because that is the half that fails
silently: a fact gathered a moment too late, or gathered twice from a moving
tree, leaves the receipt GREEN and saying the wrong thing. Nothing in a passing
suite catches that unless something here asserts it.

Load-bearing:

  1. ONE snapshot. `changed` is derived from the same read that is returned, not
     from a second one -- two reads can straddle a write and disagree, and the
     disagreement surfaces as a receipt listing a file it also calls unchanged.
  2. The result is a VALUE, not a view. Facts taken before a later edit must not
     change when that edit lands; everything downstream assumes it is reading
     the tree as it was at collection time.
  3. `changed` means "differs from T0" in BOTH directions -- created, modified
     and deleted. A deletion the run made is a change the receipt must be able
     to report.
  4. It reads the tree it is GIVEN. A run in a worktree must observe the
     worktree, never the main tree it was cut from.

Run:  python3 specs/facts_spec.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd.core import facts  # noqa: E402
from qd import gittree  # noqa: E402


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        sh(self.cwd, "git", "init", "-q")
        sh(self.cwd, "git", "config", "user.email", "s@t")
        sh(self.cwd, "git", "config", "user.name", "s")
        self.write("kept.py", "KEPT = 1\n")
        sh(self.cwd, "git", "add", "-A")
        sh(self.cwd, "git", "commit", "-qm", "base")
        self.pre_sha = sh(self.cwd, "git", "rev-parse", "HEAD").stdout.strip()
        self.pre_status = gittree.snapshot(self.cwd)

    def write(self, rel, body):
        path = os.path.join(self.cwd, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(rel) else None
        with open(path, "w") as f:
            f.write(body)

    def collect(self):
        return facts.collect(self.cwd, self.pre_status, self.pre_sha)


class Values(Fixture):
    def test_a_created_file_is_changed(self):
        self.write("new.py", "def added():\n    return 1\n")
        self.assertIn("new.py", self.collect()["changed"])

    def test_a_modified_file_is_changed(self):
        self.write("kept.py", "KEPT = 2\n")
        self.assertIn("kept.py", self.collect()["changed"])

    def test_a_deleted_file_is_changed(self):
        # Both directions. A deletion is a change the receipt must report, and
        # a set built only from the POST status would miss it entirely.
        os.remove(os.path.join(self.cwd, "kept.py"))
        self.assertIn("kept.py", self.collect()["changed"])

    def test_a_file_the_run_reverted_to_clean_is_changed(self):
        # The case the T0 half of the union exists for, and the one a mutation
        # pass found uncovered. A DELETION still shows up in the post status
        # (git reports it), so dropping `pre_status` from the union does not
        # break that -- it breaks this: a path dirty at T0 that the run put
        # back. It vanishes from the post status entirely, so only T0 knows it
        # was ever touched, and a receipt would report the revert as nothing
        # having happened.
        self.write("kept.py", "KEPT = 999\n")          # dirty before the run
        pre = gittree.snapshot(self.cwd)
        self.assertIn("kept.py", pre)
        self.write("kept.py", "KEPT = 1\n")            # run puts it back
        got = facts.collect(self.cwd, pre, self.pre_sha)
        self.assertNotIn("kept.py", got["post_status"])
        self.assertIn("kept.py", got["changed"])

    def test_an_untouched_tree_reports_nothing_changed(self):
        self.assertEqual(self.collect()["changed"], [])

    def test_changed_is_sorted_so_receipts_are_stable(self):
        for name in ("c.py", "a.py", "b.py"):
            self.write(name, "x = 1\n")
        got = self.collect()["changed"]
        self.assertEqual(got, sorted(got))

    def test_it_carries_the_keys_every_reader_expects(self):
        # The renderer and the detectors read these by name; dropping one is a
        # silent KeyError-shaped hole in a receipt, not a test failure.
        for key in ("post_status", "changed", "numstat", "head_moved",
                    "head_now", "pubs", "mocked_seams"):
            self.assertIn(key, self.collect(), key)

    def test_a_shared_input_is_computed_once_here_not_per_detector(self):
        """`mocked_seams` is a FACT, and the reason is §4's rule.

        Two detectors need it -- the one that reports mocked seams, and the one
        that reports a new symbol crossing one. Had each gathered its own, they
        would have paid for the same greps twice AND acquired an ordering
        dependency on each other, hidden in whichever order somebody happened to
        register them. This module's docstring named this exact pair before
        either detector existed.
        """
        self.write("store.py", "def get():\n    return []\n")
        self.write("test_store_qwen.py",
                   "from unittest import mock\n"
                   "def test_it():\n"
                   "    with mock.patch('store.get'):\n        pass\n")
        got = self.collect()
        self.assertEqual(got["mocked_seams"],
                         [("test_store_qwen.py", "store.py")])

    def test_a_commit_made_during_the_run_is_seen(self):
        self.write("x.py", "X = 1\n")
        sh(self.cwd, "git", "add", "-A")
        sh(self.cwd, "git", "commit", "-qm", "during")
        moved, count, _files = self.collect()["head_moved"]
        self.assertTrue(moved)
        self.assertEqual(count, 1)

    def test_head_now_is_the_tree_it_was_given(self):
        # `head_sha` is the SHORT form; the T0 sha the engine threads around is
        # the full one. Asserting equality would pin a formatting accident
        # rather than the fact, so this pins the relationship.
        self.assertTrue(self.pre_sha.startswith(self.collect()["head_now"]))


class WhenAndHowManyTimes(Fixture):
    """The half a green suite cannot check."""

    def test_it_takes_exactly_one_snapshot(self):
        # `changed` must derive from the SAME read that is returned. Two reads
        # can straddle a write and disagree, and the disagreement surfaces as a
        # receipt listing a file it also calls unchanged.
        calls = []
        real = facts.snapshot
        facts.snapshot = lambda cwd: (calls.append(cwd), real(cwd))[1]
        try:
            self.write("new.py", "x = 1\n")
            self.collect()
        finally:
            facts.snapshot = real
        self.assertEqual(len(calls), 1, f"snapshot called {len(calls)}x")

    def test_changed_is_derived_from_the_snapshot_it_returns(self):
        # Not merely consistent by luck: every path in `changed` must be
        # explicable from the returned post_status plus the T0 status.
        self.write("new.py", "x = 1\n")
        os.remove(os.path.join(self.cwd, "kept.py"))
        got = self.collect()
        for path in got["changed"]:
            self.assertNotEqual(got["post_status"].get(path),
                                self.pre_status.get(path), path)

    def test_the_result_is_a_value_not_a_view(self):
        # Everything downstream assumes it is reading the tree AS IT WAS at
        # collection time. If a later edit could mutate an already-collected
        # record, a detector's finding would describe a tree nobody observed.
        self.write("first.py", "x = 1\n")
        before = self.collect()
        self.write("second.py", "y = 2\n")
        self.assertIn("first.py", before["changed"])
        self.assertNotIn("second.py", before["changed"])

    def test_it_observes_the_tree_it_is_given_not_the_repo_it_came_from(self):
        # A worktree run must observe the WORKTREE. Reading the main tree would
        # report the caller's edits as the worker's, which is the false
        # accusation this project has spent a phase removing.
        os.environ["QWEN_DELEGATE_WORKTREES"] = tempfile.mkdtemp()
        try:
            from qd import worktrees
            wt = worktrees.acquire(self.cwd)
        finally:
            os.environ.pop("QWEN_DELEGATE_WORKTREES", None)
        with open(os.path.join(wt["path"], "only_here.py"), "w") as f:
            f.write("z = 3\n")
        self.write("only_main.py", "m = 1\n")
        got = facts.collect(wt["path"], gittree.snapshot(wt["path"]) and {},
                            self.pre_sha)
        self.assertIn("only_here.py", got["changed"])
        self.assertNotIn("only_main.py", got["changed"])


class Frozen(Fixture):
    """Step 2: the record is READ-ONLY once collected.

    Not a nicety. The detectors used to write their results back into this
    record -- `tf["uncalled"] = ...` -- and while that was possible, three
    things were true: an observation (`pubs`) and a judgement (`uncalled`) were
    indistinguishable to every later reader; any detector reading a written-back
    key silently depended on the order the calls happened to appear in; and
    removing a detector meant first proving nothing else relied on its
    leftovers. Step 2 moved the detectors out. This makes going back an ERROR
    rather than a style violation.

    The rule a comment cannot enforce: FACTS ARE COMPUTED ONCE. Everything
    downstream reads this as the tree AS IT WAS at collection time, and a
    late write would make some reader's finding describe a tree nobody observed.
    """

    def test_a_fact_cannot_be_overwritten(self):
        got = self.collect()
        with self.assertRaises(TypeError):
            got["changed"] = ["fabricated.py"]

    def test_a_finding_cannot_be_smuggled_in_beside_the_facts(self):
        # The exact regression: this is the line that used to live in the
        # engine, and it is the one this freeze exists to stop.
        got = self.collect()
        with self.assertRaises(TypeError):
            got["uncalled"] = {"out.py": ["run_threads"]}

    def test_it_still_reads_like_a_mapping(self):
        # Frozen must not mean awkward. Every consumer reads it with [] or
        # .get(); breaking those to gain immutability would trade one silent
        # failure for a louder one.
        self.write("new.py", "x = 1\n")
        got = self.collect()
        self.assertIn("new.py", got["changed"])
        self.assertEqual(got.get("nonexistent"), None)
        self.assertIn("changed", got)


class Behaviour(Fixture):
    """It is an extraction: the shape must match what the engine built inline."""

    def test_it_matches_the_inline_computation_it_replaced(self):
        self.write("new.py", "def f():\n    return 1\n")
        self.write("kept.py", "KEPT = 9\n")
        got = self.collect()
        post = gittree.snapshot(self.cwd)
        expect = sorted(
            p for p in set(list(post.keys()) + list(self.pre_status.keys()))
            if post.get(p) != self.pre_status.get(p))
        self.assertEqual(got["changed"], expect)
        self.assertEqual(got["post_status"], post)
        self.assertEqual(got["pubs"], gittree.new_public_symbols(self.cwd))


if __name__ == "__main__":
    unittest.main(verbosity=2)
