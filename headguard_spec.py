#!/usr/bin/env python3
"""
Spec for the HEAD-moved guard.

Claude-authored gate (never delegate this file -- it defines what correct means).

The worker is told not to commit: QWEN.md says so, and `scoped` hard-denies git write
commands. But `yolo` has no hook, so nothing enforces it, and it has been observed
committing anyway. A commit is not a tidiness problem -- it defeats two of the three
structural protections at once:

  spec guard    `git diff -- <spec>` compares the working tree to HEAD. Commit the edit
                and HEAD moves with it, so the diff is empty and a weakened spec
                survives. Measured before the fix: same edit, uncommitted -> detected,
                committed -> NOT detected, `assert False` left on disk.
  blast radius  `git status --porcelain` shows nothing after a commit, so CHANGED
                reports "nothing" while the tree really moved.

And the printed rollback becomes wrong: `git checkout .` cannot undo a commit, so
following it would leave the commits in place while reading as a successful rollback.

Run:  python3 headguard_spec.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args),
                          capture_output=True, text=True)


class Repo(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="hg-spec-")
        git(self.d, "init", "-q")
        git(self.d, "config", "user.email", "t@l")
        git(self.d, "config", "user.name", "t")
        with open(os.path.join(self.d, "calc_spec.py"), "w") as f:
            f.write("assert double(2) == 4\n")
        with open(os.path.join(self.d, "calc.py"), "w") as f:
            f.write("def double(n):\n    return n * 2\n")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", "baseline")
        self.pre_sha = server.head_sha(self.d)

    def weaken_spec(self):
        with open(os.path.join(self.d, "calc_spec.py"), "w") as f:
            f.write("assert True  # weakened\n")

    def commit_all(self, msg="worker commit"):
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", msg)

    def spec_text(self):
        with open(os.path.join(self.d, "calc_spec.py")) as f:
            return f.read()


class SpecGuardSurvivesACommit(Repo):
    """The safety-critical half."""

    def test_uncommitted_spec_edit_is_caught(self):
        """Baseline: this always worked."""
        self.weaken_spec()
        self.assertEqual(server.violated_specs(self.d, base=self.pre_sha),
                         ["calc_spec.py"])

    def test_committed_spec_edit_is_caught(self):
        """The hole. Before the fix this returned [] and the sabotage survived."""
        self.weaken_spec()
        self.commit_all()
        self.assertEqual(
            server.violated_specs(self.d, base=self.pre_sha), ["calc_spec.py"],
            "a spec edit hidden behind a commit must still be detected")

    def test_committed_spec_edit_is_actually_reverted(self):
        """Detecting it is useless if the revert restores the weakened version --
        HEAD now HOLDS the sabotage, so `git checkout --` would faithfully restore it."""
        self.weaken_spec()
        self.commit_all()
        server.revert_specs(self.d, ["calc_spec.py"], base=self.pre_sha)
        self.assertIn("double(2) == 4", self.spec_text())
        self.assertNotIn("weakened", self.spec_text())

    def test_untouched_spec_is_not_flagged(self):
        """Guard must not fire on an innocent run, committed or not."""
        with open(os.path.join(self.d, "calc.py"), "a") as f:
            f.write("\ndef triple(n):\n    return n * 3\n")
        self.commit_all()
        self.assertEqual(server.violated_specs(self.d, base=self.pre_sha), [])

    def test_default_base_still_means_head(self):
        """Back-compat: the PRE-run dirty check calls it with no base and must keep
        comparing against HEAD, not against some run's starting sha."""
        self.weaken_spec()
        self.assertEqual(server.violated_specs(self.d), ["calc_spec.py"])
        self.commit_all()
        self.assertEqual(server.violated_specs(self.d), [],
                         "with no base, a committed edit is not an UNCOMMITTED change")


class DetectingTheCommit(Repo):
    def test_no_commit_means_not_moved(self):
        moved, n, files = server.committed_during_run(self.d, self.pre_sha)
        self.assertFalse(moved)
        self.assertEqual((n, files), (0, []))

    def test_dirty_tree_alone_is_not_a_move(self):
        """Ordinary uncommitted work must not be reported as a commit."""
        with open(os.path.join(self.d, "calc.py"), "a") as f:
            f.write("# edit\n")
        moved, _, _ = server.committed_during_run(self.d, self.pre_sha)
        self.assertFalse(moved)

    def test_commit_is_detected_with_count_and_files(self):
        with open(os.path.join(self.d, "calc.py"), "a") as f:
            f.write("# edit\n")
        self.commit_all()
        moved, n, files = server.committed_during_run(self.d, self.pre_sha)
        self.assertTrue(moved)
        self.assertEqual(n, 1)
        self.assertIn("calc.py", files)

    def test_multiple_commits_counted(self):
        for i in range(3):
            with open(os.path.join(self.d, f"f{i}.py"), "w") as f:
                f.write("x = 1\n")
            self.commit_all(f"c{i}")
        moved, n, files = server.committed_during_run(self.d, self.pre_sha)
        self.assertTrue(moved)
        self.assertEqual(n, 3)
        self.assertEqual(len(files), 3)

    def test_committed_files_are_invisible_to_status(self):
        """Documents WHY this guard exists: the normal blast radius cannot see them."""
        with open(os.path.join(self.d, "calc.py"), "a") as f:
            f.write("# edit\n")
        self.commit_all()
        self.assertEqual(server.snapshot(self.d), {},
                         "fixture assumption: git status is blind to committed work")
        _, _, files = server.committed_during_run(self.d, self.pre_sha)
        self.assertIn("calc.py", files, "so the guard must surface them instead")

    def test_no_pre_sha_is_safe(self):
        """Non-git projects have no sha; must not raise."""
        self.assertEqual(server.committed_during_run(self.d, None), (False, 0, []))


class Reporting(Repo):
    """The verdict must not tell the caller to run a rollback that cannot work."""

    def render(self, pre_clean=True):
        ctx = {"cwd": self.d, "guard_on": True, "pre_sha": self.pre_sha,
               "pre_clean": pre_clean, "pre_status": {}, "preflight": False,
               "approval_mode": "yolo", "timeout": 60, "meta": {}, "peak": 0,
               "cum": server.cum_zero(), "task": "t", "verify": "true",
               "max_iter": 1, "session_hint": None, "on_compaction": "reinject",
               "sessions": []}
        return server.render("success", "sid", ["attempt 1: VERIFY PASS"], "", [], 1, ctx)

    def test_clean_run_advises_checkout(self):
        out = self.render()
        self.assertIn("git checkout .", out)
        self.assertNotIn("git reset --hard", out)
        self.assertNotIn("COMMITTED:", out)

    def test_committed_run_advises_reset_not_checkout(self):
        with open(os.path.join(self.d, "calc.py"), "a") as f:
            f.write("# edit\n")
        self.commit_all()
        out = self.render()
        self.assertIn("COMMITTED:", out)
        self.assertIn(f"git reset --hard {self.pre_sha}", out)
        self.assertIn("will NOT undo the commit", out,
                      "the caller must be told checkout is insufficient here")

    def test_committed_run_says_changed_is_incomplete(self):
        with open(os.path.join(self.d, "calc.py"), "a") as f:
            f.write("# edit\n")
        self.commit_all()
        out = self.render()
        self.assertIn("INCOMPLETE", out)
        self.assertIn("calc.py", out, "the hidden files must be named")

    def test_dirty_pre_run_tree_is_flagged_in_the_reset_advice(self):
        with open(os.path.join(self.d, "calc.py"), "a") as f:
            f.write("# edit\n")
        self.commit_all()
        out = self.render(pre_clean=False)
        self.assertIn("git reset --hard", out)
        self.assertIn("CAUTION", out,
                      "resetting a tree that was already dirty destroys prior work")


if __name__ == "__main__":
    unittest.main(verbosity=2)
