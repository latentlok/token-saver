#!/usr/bin/env python3
"""
Spec for qd/worktrees.py -- isolation containers + merge protocol (HLD C6/C2,
LLD "qd/worktrees.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

Load-bearing:

  1. Isolation is the point: a run inside worktree A must be invisible to the
     main tree and to worktree B -- "one actor per tree" made structural.
  2. Uncommitted main-tree work is NOT in a worktree (branched from HEAD);
     acquire must SAY so (dirty flag) because it surprises people.
  3. Unborn HEAD refuses with instructions -- there is nothing to branch from.
  4. Concurrent acquires never collide (unique ids under a module lock).
  5. release is idempotent and leaves `git worktree list` and the branch list
     clean -- leaked worktrees accumulate forever.
  6. merge_lines emits the C2 strings BYTE-EXACT: the server never merges;
     Claude executes these commands verbatim.
  7. classify_merge is READ-ONLY (git merge-tree --write-tree): "clean" vs
     "conflict" without ever touching the main tree's state.

Public surface pinned here:
    qd.worktrees.WorktreeError
    qd.worktrees.acquire(repo) -> {"path","branch","base_sha","dirty"}
    qd.worktrees.release(repo, path, branch)
    qd.worktrees.merge_lines(res) -> list[str]
    qd.worktrees.classify_merge(repo, branch) -> "clean" | "conflict"

Base directory honors QWEN_DELEGATE_WORKTREES (default
~/.qwen-delegate/worktrees/), same env-override pattern as the registry.

Run:  python3 specs/worktree_spec.py
"""

import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import worktrees  # noqa: E402


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["QWEN_DELEGATE_WORKTREES"] = tempfile.mkdtemp()
        self.repo = tempfile.mkdtemp()
        sh(self.repo, "git", "init", "-q")
        sh(self.repo, "git", "config", "user.email", "s@t")
        sh(self.repo, "git", "config", "user.name", "s")
        with open(os.path.join(self.repo, "a.py"), "w") as f:
            f.write("x = 1\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "base")
        self.head = sh(self.repo, "git", "rev-parse", "HEAD").stdout.strip()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)


class Acquire(Fixture):
    def test_shape_naming_and_base(self):
        r = worktrees.acquire(self.repo)
        self.assertEqual(r["base_sha"], self.head)
        self.assertFalse(r["dirty"])
        self.assertRegex(r["branch"], r"^qwen/r[0-9a-f]{6}$")
        self.assertTrue(os.path.isfile(os.path.join(r["path"], "a.py")))
        self.assertTrue(r["path"].startswith(
            os.environ["QWEN_DELEGATE_WORKTREES"]))

    def test_dirty_main_tree_flagged_and_excluded(self):
        with open(os.path.join(self.repo, "uncommitted.py"), "w") as f:
            f.write("y = 2\n")
        r = worktrees.acquire(self.repo)
        self.assertTrue(r["dirty"])
        self.assertFalse(os.path.exists(
            os.path.join(r["path"], "uncommitted.py")))

    def test_unborn_head_refused(self):
        empty = tempfile.mkdtemp()
        sh(empty, "git", "init", "-q")
        with self.assertRaises(worktrees.WorktreeError) as ctx:
            worktrees.acquire(empty)
        self.assertIn("commit", str(ctx.exception).lower())

    def test_acquire_from_inside_a_worktree_resolves_to_main(self):
        r1 = worktrees.acquire(self.repo)
        r2 = worktrees.acquire(r1["path"])       # nested request
        self.assertNotEqual(r1["path"], r2["path"])
        self.assertEqual(r2["base_sha"], self.head)

    def test_concurrent_acquires_distinct(self):
        results, errs = [], []
        def go():
            try:
                results.append(worktrees.acquire(self.repo))
            except Exception as e:
                errs.append(e)
        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errs, [])
        self.assertEqual(len({r["path"] for r in results}), 8)
        self.assertEqual(len({r["branch"] for r in results}), 8)


class Isolation(Fixture):
    def test_writes_invisible_across_trees(self):
        r1 = worktrees.acquire(self.repo)
        r2 = worktrees.acquire(self.repo)
        with open(os.path.join(r1["path"], "made.py"), "w") as f:
            f.write("z = 3\n")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "made.py")))
        self.assertFalse(os.path.exists(os.path.join(r2["path"], "made.py")))
        self.assertEqual(sh(self.repo, "git", "status",
                            "--porcelain").stdout.strip(), "")


class Release(Fixture):
    def test_release_cleans_worktree_and_branch_idempotently(self):
        r = worktrees.acquire(self.repo)
        worktrees.release(self.repo, r["path"], r["branch"])
        self.assertFalse(os.path.exists(r["path"]))
        wl = sh(self.repo, "git", "worktree", "list").stdout
        self.assertNotIn(r["path"], wl)
        branches = sh(self.repo, "git", "branch", "--list",
                      r["branch"]).stdout.strip()
        self.assertEqual(branches, "")
        worktrees.release(self.repo, r["path"], r["branch"])  # idempotent


class MergeProtocol(Fixture):
    def test_merge_lines_byte_exact(self):
        res = {"path": "/w/p/r1a2b3c", "branch": "qwen/r1a2b3c",
               "base_sha": self.head, "dirty": False}
        self.assertEqual(worktrees.merge_lines(res), [
            "WORKTREE: /w/p/r1a2b3c",
            "MERGE: git merge --no-edit qwen/r1a2b3c && "
            "git worktree remove /w/p/r1a2b3c && git branch -d qwen/r1a2b3c",
        ])

    def test_classify_clean_vs_conflict_read_only(self):
        r = worktrees.acquire(self.repo)
        with open(os.path.join(r["path"], "new.py"), "w") as f:
            f.write("fresh = 1\n")
        sh(r["path"], "git", "add", "-A")
        sh(r["path"], "git", "commit", "-qm", "wt work")
        self.assertEqual(worktrees.classify_merge(self.repo, r["branch"]),
                         "clean")

        r2 = worktrees.acquire(self.repo)
        with open(os.path.join(r2["path"], "a.py"), "w") as f:
            f.write("x = 999\n")
        sh(r2["path"], "git", "add", "-A")
        sh(r2["path"], "git", "commit", "-qm", "conflicting")
        with open(os.path.join(self.repo, "a.py"), "w") as f:
            f.write("x = 111\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "main moved")
        self.assertEqual(worktrees.classify_merge(self.repo, r2["branch"]),
                         "conflict")
        # Read-only: classification left no merge state behind.
        self.assertEqual(sh(self.repo, "git", "status",
                            "--porcelain").stdout.strip(), "")
        self.assertFalse(os.path.exists(
            os.path.join(self.repo, ".git", "MERGE_HEAD")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
