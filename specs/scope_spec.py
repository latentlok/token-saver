#!/usr/bin/env python3
"""
Spec for qd/core/scope.py -- what one run OWNS, and must dispose of.

Claude-authored gate (never delegate this file -- it defines what correct means).

Step 5 of docs/DESIGN-modular-architecture.md §5. One rule:

    A run disposes of what it owns, and never of what it borrows.

Why this needs an owner rather than three call sites. The container's fate was
decided in three places -- the refusal path, the green path and the red path --
each re-deriving "may I dispose of this?" from a flag, and each getting it right
by inspection rather than by construction. A rule spread across three sites is
one edit away from disagreeing with itself, and the disagreement is expensive:
these branches do not leak a directory when they are wrong, they DELETE WORK.

The sharp case, and the reason ownership is a first-class idea here. A chain's
links SHARE one container and commit into it between links, so link 2 can see
link 1's work. The container therefore outlives every individual link. A link
that released it would destroy the committed output of every link before it,
and the symptom would be an ordinary red link.

Run:  python3 specs/scope_spec.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from qd.core.scope import RunScope  # noqa: E402
from qd import worktrees  # noqa: E402


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        sh(self.repo, "git", "init", "-q")
        sh(self.repo, "git", "config", "user.email", "s@t")
        sh(self.repo, "git", "config", "user.name", "s")
        with open(os.path.join(self.repo, "base.py"), "w") as f:
            f.write("BASE = 1\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "base")
        os.environ["QWEN_DELEGATE_WORKTREES"] = tempfile.mkdtemp()
        self.addCleanup(os.environ.pop, "QWEN_DELEGATE_WORKTREES", None)

    def container(self):
        return worktrees.acquire(self.repo)

    def work(self, wt, name="made.py"):
        with open(os.path.join(wt["path"], name), "w") as f:
            f.write("MADE = 1\n")


class Borrowed(Fixture):
    """The case that was unpinned until step 5a, and the reason for the type."""

    def scope(self, wt):
        return RunScope(self.repo, container=wt, owned=False)

    def test_a_borrowed_container_is_never_released(self):
        wt = self.container()
        s = self.scope(wt)
        self.work(wt)
        s.dispose("verify_failed")
        self.assertTrue(os.path.exists(wt["path"]),
                        "a borrowed container was disposed of")

    def test_a_borrowed_container_survives_abandonment_too(self):
        # The refusal path. A refusal concerns THIS link; the chain that lent
        # the tree is not refused and its earlier work is not forfeit.
        wt = self.container()
        s = self.scope(wt)
        s.abandon()
        self.assertTrue(os.path.exists(wt["path"]))

    def test_a_borrowed_container_is_still_committed_on_green(self):
        # Committing is the one disposal action a borrower MUST take: it is how
        # link 2 sees link 1's files, and how they become tracked so the spec
        # guard can protect them (spec_files is `git ls-files` -- an untracked
        # new test is unprotected, and the next link could rewrite the very
        # gate it is graded by).
        wt = self.container()
        s = self.scope(wt)
        self.work(wt)
        s.dispose("success")
        out = sh(wt["path"], "git", "status", "--porcelain").stdout
        self.assertEqual(out.strip(), "", "the link's work was left uncommitted")

    def test_a_borrower_never_classifies_the_merge(self):
        # classify_merge compares the branch against the MAIN repo, which for
        # an intermediate link describes work the chain has not finished.
        wt = self.container()
        self.work(wt)
        self.assertIsNone(self.scope(wt).dispose("success")["merge"])


class Owned(Fixture):
    def scope(self, wt):
        return RunScope(self.repo, container=wt, owned=True)

    def test_a_red_run_releases_what_it_owns(self):
        wt = self.container()
        self.scope(wt).dispose("verify_failed")
        self.assertFalse(os.path.exists(wt["path"]))

    def test_a_refusal_releases_what_it_owns(self):
        # Refusals happen PAST acquisition, so returning without releasing
        # leaves a worktree and a branch behind for every refused run.
        wt = self.container()
        self.scope(wt).abandon()
        self.assertFalse(os.path.exists(wt["path"]))

    def test_a_green_run_keeps_and_commits_its_work(self):
        wt = self.container()
        self.work(wt)
        got = self.scope(wt).dispose("success")
        self.assertTrue(os.path.exists(wt["path"]))
        self.assertEqual(got["worktree"]["path"], wt["path"])

    def test_a_demoted_green_run_is_still_green(self):
        # The expensive one. `success_but_preflight_passed` is a run whose gate
        # DID go green -- the demotion is about what the pass proves, not about
        # whether work happened. Treating it as red would silently DELETE the
        # work of every preflight-passed worktree run.
        wt = self.container()
        self.work(wt)
        got = self.scope(wt).dispose("success_but_preflight_passed")
        self.assertTrue(os.path.exists(wt["path"]),
                        "a demoted-but-green run had its work deleted")
        self.assertIsNotNone(got["worktree"])

    def test_an_owner_classifies_the_merge(self):
        wt = self.container()
        self.work(wt)
        self.assertIsNotNone(self.scope(wt).dispose("success")["merge"])


class NoContainer(Fixture):
    """An in-tree run owns nothing and must not pretend to."""

    def test_disposal_is_a_no_op(self):
        s = RunScope(self.repo, container=None)
        got = s.dispose("success")
        self.assertIsNone(got["worktree"])
        self.assertIsNone(got["merge"])

    def test_abandoning_is_a_no_op(self):
        RunScope(self.repo, container=None).abandon()

    def test_the_work_directory_is_the_repo_itself(self):
        self.assertEqual(RunScope(self.repo, container=None).work_cwd,
                         self.repo)


class WhereTheWorkHappens(Fixture):
    def test_a_container_run_works_inside_the_container(self):
        # The fact every observation depends on. A run that reported on the
        # main tree instead of its container would attribute the caller's
        # concurrent edits to the worker -- the false accusation this project
        # spent a phase removing.
        wt = self.container()
        s = RunScope(self.repo, container=wt, owned=True)
        self.assertEqual(s.work_cwd, wt["path"])
        self.assertNotEqual(s.work_cwd, self.repo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
