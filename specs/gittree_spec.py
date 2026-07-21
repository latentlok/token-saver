#!/usr/bin/env python3
"""
Spec for qd/gittree.py -- the ported trust machinery (LLD "qd/gittree.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

This is a PORT gate: it freezes the behavior server.py has today (measured and
mutation-tested there) onto the new module, and adds one new obligation -- every
function must work when `cwd` is a linked git worktree (the v2 fan-out container).

The load-bearing cases:

  1. Spec guard: violated_specs must catch BOTH an uncommitted spec edit and the
     committed-edit hole (worker commits its sabotage; plain diff-to-HEAD sees
     nothing; diffing against the pre-run sha catches it). revert_specs(base=...)
     must restore the PRE-RUN content, not HEAD's.
  2. Snapshot compares content hashes, not just status codes: an already-dirty
     file edited again must register as changed.
  3. new_public_symbols is deterministic scope-creep detection: new top-level
     public defs in non-test files, renames cancelled, untracked new source
     counted whole.
  4. blast_radius reports what the FILESYSTEM says changed, with the exact
     no-change sentence the receipt relies on.
  5. reset_worktree uses clean -fd, never -fdx (a gitignored venv must survive).

Public surface pinned here (all ported verbatim from server.py):
    git, is_git_repo, head_sha, status_map, file_sha, snapshot,
    spec_globs, DEFAULT_SPEC_GLOBS, spec_files, violated_specs, revert_specs,
    committed_during_run, new_public_symbols, blast_radius, reset_worktree

Run:  python3 specs/gittree_spec.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import gittree  # noqa: E402


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def make_repo():
    cwd = tempfile.mkdtemp()
    sh(cwd, "git", "init", "-q")
    sh(cwd, "git", "config", "user.email", "spec@test")
    sh(cwd, "git", "config", "user.name", "spec")
    return cwd


def put(cwd, rel, content):
    full = os.path.join(cwd, rel)
    os.makedirs(os.path.dirname(full) or full or ".", exist_ok=True) \
        if os.path.dirname(rel) else None
    with open(full, "w") as f:
        f.write(content)
    return full


def commit_all(cwd, msg="c"):
    sh(cwd, "git", "add", "-A")
    sh(cwd, "git", "commit", "-qm", msg)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.cwd = make_repo()
        put(self.cwd, "roman_spec.py", "def test_one():\n    assert True\n")
        put(self.cwd, "roman.py", "def to_roman(n):\n    return 'I' * n\n")
        commit_all(self.cwd, "base")


class Basics(Fixture):
    def test_is_git_repo(self):
        self.assertTrue(gittree.is_git_repo(self.cwd))
        self.assertFalse(gittree.is_git_repo(tempfile.mkdtemp()))

    def test_head_sha_is_short_hex(self):
        sha = gittree.head_sha(self.cwd)
        self.assertTrue(sha and len(sha) < 40)
        int(sha, 16)  # raises if not hex

    def test_file_sha_shape_and_absent(self):
        sha = gittree.file_sha(self.cwd, "roman.py")
        self.assertEqual(len(sha), 16)
        self.assertIsNone(gittree.file_sha(self.cwd, "missing.py"))

    def test_status_map_and_snapshot_dirty_only(self):
        self.assertEqual(gittree.status_map(self.cwd), {})
        put(self.cwd, "new.py", "x = 1\n")
        m = gittree.status_map(self.cwd)
        self.assertEqual(m, {"new.py": "??"})
        snap = gittree.snapshot(self.cwd)
        self.assertIn("new.py", snap)
        code, sha = snap["new.py"]
        self.assertEqual(code, "??")
        self.assertEqual(len(sha), 16)

    def test_snapshot_sees_content_change_of_already_dirty_file(self):
        # Case 2: status code stays '??' but the content hash must differ.
        put(self.cwd, "w.py", "v1\n")
        pre = gittree.snapshot(self.cwd)
        put(self.cwd, "w.py", "v2 different\n")
        post = gittree.snapshot(self.cwd)
        self.assertEqual(pre["w.py"][0], post["w.py"][0])
        self.assertNotEqual(pre["w.py"][1], post["w.py"][1])


class SpecGuard(Fixture):
    def test_spec_files_tracked_sorted_nested(self):
        put(self.cwd, "sub/inner_spec.py", "assert True\n")
        commit_all(self.cwd)
        put(self.cwd, "untracked_spec.py", "x\n")  # untracked: not protected
        self.assertEqual(gittree.spec_files(self.cwd),
                         ["roman_spec.py", "sub/inner_spec.py"])

    def test_uncommitted_spec_edit_detected_and_reverted(self):
        put(self.cwd, "roman_spec.py", "def test_one():\n    assert False\n")
        self.assertEqual(gittree.violated_specs(self.cwd), ["roman_spec.py"])
        gittree.revert_specs(self.cwd, ["roman_spec.py"])
        self.assertEqual(gittree.violated_specs(self.cwd), [])

    def test_committed_spec_edit_hole_closed_by_base(self):
        # Case 1: the measured hole -- worker edits AND commits the spec.
        rc, out = gittree.git(self.cwd, "rev-parse", "HEAD")
        pre_sha = out.strip()
        put(self.cwd, "roman_spec.py", "WEAKENED = True\n")
        commit_all(self.cwd, "sabotage")
        self.assertEqual(gittree.violated_specs(self.cwd), [])          # blind
        self.assertEqual(gittree.violated_specs(self.cwd, base=pre_sha),
                         ["roman_spec.py"])                              # caught
        gittree.revert_specs(self.cwd, ["roman_spec.py"], base=pre_sha)
        with open(os.path.join(self.cwd, "roman_spec.py")) as f:
            self.assertIn("assert True", f.read())                       # PRE-RUN content

    def test_spec_globs_default_and_project_override(self):
        self.assertEqual(gittree.spec_globs(self.cwd),
                         gittree.DEFAULT_SPEC_GLOBS)
        put(self.cwd, ".qwen-delegate.json",
            json.dumps({"spec_globs": ["gates/*.py"]}))
        self.assertEqual(gittree.spec_globs(self.cwd), ["gates/*.py"])


class Commits(Fixture):
    def test_no_commit_is_false(self):
        rc, out = gittree.git(self.cwd, "rev-parse", "HEAD")
        moved, n, files = gittree.committed_during_run(self.cwd, out.strip())
        self.assertEqual((moved, n, files), (False, 0, []))

    def test_commit_detected_with_count_and_files(self):
        rc, out = gittree.git(self.cwd, "rev-parse", "HEAD")
        pre = out.strip()
        put(self.cwd, "roman.py", "def to_roman(n):\n    return ''\n")
        commit_all(self.cwd, "sneaky")
        moved, n, files = gittree.committed_during_run(self.cwd, pre)
        self.assertTrue(moved)
        self.assertEqual(n, 1)
        self.assertEqual(files, ["roman.py"])


class Publics(Fixture):
    def test_new_top_level_def_detected(self):
        put(self.cwd, "roman.py",
            "def to_roman(n):\n    return 'I' * n\n\ndef from_roman(s):\n    return len(s)\n")
        self.assertEqual(gittree.new_public_symbols(self.cwd),
                         {"roman.py": ["from_roman"]})

    def test_private_and_methods_skipped(self):
        put(self.cwd, "roman.py",
            "def to_roman(n):\n    return 'I' * n\n\nclass R:\n    def method(self):\n        pass\n\ndef _helper():\n    pass\n")
        got = gittree.new_public_symbols(self.cwd)
        self.assertEqual(got.get("roman.py"), ["R"])  # class yes; method/_helper no

    def test_spec_and_test_files_excluded(self):
        put(self.cwd, "roman_spec.py", "def test_two():\n    assert True\n"
            + open(os.path.join(self.cwd, "roman_spec.py")).read())
        put(self.cwd, "util_test.py", "def helper():\n    pass\n")
        self.assertEqual(gittree.new_public_symbols(self.cwd), {})

    def test_rename_cancels(self):
        put(self.cwd, "roman.py", "def to_roman_v2(n):\n    return 'I' * n\n")
        # to_roman removed, to_roman_v2 added -> only the genuinely-new name reports.
        self.assertEqual(gittree.new_public_symbols(self.cwd),
                         {"roman.py": ["to_roman_v2"]})

    def test_untracked_new_file_counts_whole(self):
        put(self.cwd, "fresh.py", "def alpha():\n    pass\n\ndef _p():\n    pass\n")
        self.assertEqual(gittree.new_public_symbols(self.cwd),
                         {"fresh.py": ["alpha"]})


class BlastRadius(Fixture):
    def test_no_change_exact_sentence(self):
        pre = gittree.snapshot(self.cwd)
        self.assertEqual(gittree.blast_radius(self.cwd, pre),
                         "CHANGED: nothing (Qwen wrote no files)")

    def test_new_and_edited_reported(self):
        pre = gittree.snapshot(self.cwd)
        put(self.cwd, "fresh.py", "x = 1\n")
        put(self.cwd, "roman.py", "def to_roman(n):\n    return 'X'\n")
        out = gittree.blast_radius(self.cwd, pre)
        self.assertIn("CHANGED: 2 file(s)", out)
        self.assertIn("+ fresh.py (new)", out)
        self.assertIn("M roman.py (+", out)


class ResetWorktree(Fixture):
    def test_reset_clean_but_ignored_survives(self):
        put(self.cwd, ".gitignore", "keepme.txt\n")
        commit_all(self.cwd)
        rc, out = gittree.git(self.cwd, "rev-parse", "HEAD")
        base = out.strip()
        put(self.cwd, "keepme.txt", "precious gitignored state")
        put(self.cwd, "junk.py", "x\n")
        put(self.cwd, "roman.py", "edited\n")
        gittree.reset_worktree(self.cwd, base)
        self.assertFalse(os.path.exists(os.path.join(self.cwd, "junk.py")))
        with open(os.path.join(self.cwd, "roman.py")) as f:
            self.assertIn("to_roman", f.read())
        # clean -fd, never -fdx: the ignored file MUST survive.
        self.assertTrue(os.path.exists(os.path.join(self.cwd, "keepme.txt")))


class LinkedWorktree(Fixture):
    """The v2 obligation: everything works when cwd IS a linked worktree."""

    def setUp(self):
        super().setUp()
        self.wt = os.path.join(tempfile.mkdtemp(), "wt")
        r = sh(self.cwd, "git", "worktree", "add", "-q", self.wt, "-b", "spec/wt")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_guards_operate_inside_worktree(self):
        self.assertTrue(gittree.is_git_repo(self.wt))
        self.assertEqual(gittree.spec_files(self.wt), ["roman_spec.py"])
        self.assertEqual(gittree.snapshot(self.wt), {})
        put(self.wt, "roman_spec.py", "tampered\n")
        self.assertEqual(gittree.violated_specs(self.wt), ["roman_spec.py"])
        gittree.revert_specs(self.wt, ["roman_spec.py"])
        self.assertEqual(gittree.violated_specs(self.wt), [])
        put(self.wt, "extra.py", "def beta():\n    pass\n")
        self.assertEqual(gittree.new_public_symbols(self.wt),
                         {"extra.py": ["beta"]})
        # Isolation: nothing leaked into the main tree.
        self.assertEqual(gittree.snapshot(self.cwd), {})


if __name__ == "__main__":
    unittest.main(verbosity=1)
