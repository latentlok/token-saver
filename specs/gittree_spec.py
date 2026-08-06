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

Public surface pinned here (all ported verbatim from server.py, except
`unquote_path`, which is new -- see class CQuotedPaths):
    git, is_git_repo, head_sha, status_map, unquote_path, file_sha, snapshot,
    spec_globs, DEFAULT_SPEC_GLOBS, spec_files, violated_specs, revert_specs,
    committed_during_run, new_public_symbols, blast_radius, reset_worktree

CONTRACT CHANGE vs v1 (deliberate, surfaced by the port build): `pre_sha` args
(committed_during_run, violated_specs base, revert_specs base) take the FULL
40-char sha (`git rev-parse HEAD`). v1's engine captured SHORT shas
(server.py:1299 head_sha) -- the M2 engine port MUST capture full shas.

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


class T0Restore(Fixture):
    """snapshot_contents + restore_paths: reverts restore T0 BYTES, never touch
    the index, round-trip binary, and refuse (report) over-cap paths instead of
    guessing at content."""

    def test_pre_dirty_file_restores_to_t0_not_head(self):
        put(self.cwd, "roman.py", "T0_DIRTY = 1\n")          # dirty before run
        pre = gittree.snapshot(self.cwd)
        t0 = gittree.snapshot_contents(self.cwd, pre, tempfile.mkdtemp())
        put(self.cwd, "roman.py", "WORKER = 1\n")            # worker tramples
        rc, out = gittree.git(self.cwd, "rev-parse", "HEAD")
        restored, unrestored = gittree.restore_paths(
            self.cwd, ["roman.py"], base=out.strip(), t0=t0)
        self.assertEqual(restored, ["roman.py"])
        self.assertEqual(unrestored, [])
        with open(os.path.join(self.cwd, "roman.py")) as f:
            self.assertEqual(f.read(), "T0_DIRTY = 1\n")

    def test_restore_never_touches_the_index(self):
        rc, out = gittree.git(self.cwd, "rev-parse", "HEAD")
        put(self.cwd, "roman.py", "WORKER = 1\n")
        gittree.restore_paths(self.cwd, ["roman.py"], base=out.strip(), t0={})
        r = sh(self.cwd, "git", "diff", "--cached", "--name-only")
        self.assertEqual(r.stdout.strip(), "")
        with open(os.path.join(self.cwd, "roman.py")) as f:
            self.assertIn("to_roman", f.read())

    def test_binary_round_trip(self):
        blob = bytes(range(256)) * 3
        with open(os.path.join(self.cwd, "blob.bin"), "wb") as f:
            f.write(blob)
        pre = gittree.snapshot(self.cwd)
        t0 = gittree.snapshot_contents(self.cwd, pre, tempfile.mkdtemp())
        with open(os.path.join(self.cwd, "blob.bin"), "wb") as f:
            f.write(b"clobbered")
        gittree.restore_paths(self.cwd, ["blob.bin"], t0=t0)
        with open(os.path.join(self.cwd, "blob.bin"), "rb") as f:
            self.assertEqual(f.read(), blob)

    def test_over_cap_file_reported_not_reverted(self):
        put(self.cwd, "big.txt", "x")
        pre = gittree.snapshot(self.cwd)
        saved_cap = gittree.SNAPSHOT_FILE_CAP
        gittree.SNAPSHOT_FILE_CAP = 0
        try:
            t0 = gittree.snapshot_contents(self.cwd, pre, tempfile.mkdtemp())
        finally:
            gittree.SNAPSHOT_FILE_CAP = saved_cap
        self.assertEqual(t0["big.txt"], ("toobig", None))
        put(self.cwd, "big.txt", "worker content")
        restored, unrestored = gittree.restore_paths(
            self.cwd, ["big.txt"], t0=t0)
        self.assertEqual(restored, [])
        self.assertEqual(unrestored, ["big.txt"])
        with open(os.path.join(self.cwd, "big.txt")) as f:
            self.assertEqual(f.read(), "worker content")     # untouched

    def test_deleted_at_t0_restores_by_removal(self):
        os.remove(os.path.join(self.cwd, "roman.py"))
        pre = gittree.snapshot(self.cwd)                     # 'D roman.py'
        t0 = gittree.snapshot_contents(self.cwd, pre, tempfile.mkdtemp())
        put(self.cwd, "roman.py", "RESURRECTED = 1\n")       # worker recreates
        gittree.restore_paths(self.cwd, ["roman.py"], t0=t0)
        self.assertFalse(os.path.exists(os.path.join(self.cwd, "roman.py")))


class UntrackedExpansion(Fixture):
    """`git status --porcelain` collapses a new directory into one `dir/`
    entry. Right for a CHANGED summary, wrong for every per-file rule."""

    def test_a_new_directory_is_expanded_to_its_files(self):
        put(self.cwd, "pkg/a.py", "a = 1\n")
        put(self.cwd, "pkg/sub/b.py", "b = 1\n")
        self.assertEqual(gittree.status_map(self.cwd), {"pkg/": "??"})
        self.assertEqual(sorted(gittree.untracked_files(self.cwd)),
                         ["pkg/a.py", "pkg/sub/b.py"])

    def test_tracked_edits_are_not_untracked(self):
        put(self.cwd, "roman.py", "CHANGED = 1\n")
        self.assertEqual(gittree.untracked_files(self.cwd), [])


class CQuotedPaths(Fixture):
    """`git status --porcelain` C-QUOTES paths it considers unusual, and this
    module used to hand those quotes on as if they were part of the filename.

    Measured, git 2.53 (both `core.quotePath` settings, because the flag is not
    the whole story): a path is C-quoted when it contains a SPACE, a double
    quote, a backslash, or a control byte -- and, under the default
    quotePath=true only, any non-ASCII byte, octal-escaped per byte. A single
    quote, `$`, `;`, backtick, `|`, `&`, `*`, `>` and `#` all come back BARE.
    Setting quotePath=false changes exactly one of those cases (non-ASCII is
    emitted raw); it does not disable the quoting.

    Keeping the quotes made every path-consuming caller wrong for those names:
    the string names no file on disk, so file_sha returns None, restore cannot
    find it, and -- the one that turned a cosmetic bug into a hole -- the C8
    prefilter is handed `"my calc_qwen.py"` and grades nothing, which is a
    worker hiding a test file from its own grading by putting a space in the
    name (specs/engine_spec.py, class Prefilter).

    Decoding here rather than at each caller: this is the seam where git's
    output stops being a wire format and starts being a path.
    """

    NAMES = ("my calc_qwen.py",          # space -- the one a worker hits by accident
             'dq"uote.py',               # the escape that also delimits
             "back\\slash.py",           # \\ -- decoding must not eat it
             "tab\tchar.py",             # a control byte, \t
             "café.py",                  # non-ASCII: \303\251 under quotePath=true
             "中文.py")                   # multi-byte, several octal escapes

    def test_status_map_returns_real_filenames(self):
        for n in self.NAMES:
            with self.subTest(name=n):
                cwd = make_repo()
                put(cwd, n, "x = 1\n")
                self.assertEqual(gittree.status_map(cwd), {n: "??"})
                # The proof that it is a real path and not a lookalike string:
                # the file opens. file_sha returned None for every one of these
                # before, which is silently "unreadable, never accuse it".
                self.assertIsNotNone(gittree.file_sha(cwd, n))

    def test_untracked_files_returns_real_filenames(self):
        # Same parse, second site: this one feeds the per-file rules (strays,
        # fixture provenance), so a name it cannot express is a name those
        # rules cannot police.
        cwd = make_repo()
        for n in self.NAMES:
            put(cwd, os.path.join("pkg", n), "x = 1\n")
        self.assertEqual(sorted(gittree.untracked_files(cwd)),
                         sorted(os.path.join("pkg", n) for n in self.NAMES))

    def test_an_ordinary_path_is_untouched(self):
        # The decode must be a no-op on everything git did not quote -- that is
        # what keeps every existing assertion in this file describing the same
        # bytes it always did.
        for raw in ("roman.py", "sub/inner_spec.py", "a'b.py", "a;b.py",
                    "a$b.py", "a&b.py", "a|b.py", "a*b.py"):
            with self.subTest(raw=raw):
                self.assertEqual(gittree.unquote_path(raw), raw)

    def test_a_rename_record_is_left_exactly_as_it_was(self):
        # `R  "a b.py" -> "c d.py"` starts and ends with a quote but is TWO
        # paths in one field, so a decoder that trusted its own first and last
        # character would silently invent a filename. status_map has always
        # stored that record verbatim; this change deliberately does not touch
        # it (it is a separate defect with separate callers), and this pins
        # that the conservatism is intentional rather than accidental.
        cwd = make_repo()
        put(cwd, "a b.py", "x\n")
        commit_all(cwd)
        sh(cwd, "git", "mv", "a b.py", "c d.py")
        self.assertEqual(list(gittree.status_map(cwd)),
                         ['"a b.py" -> "c d.py"'])


class DodgeMarkers(Fixture):
    """U4.2: skip/xfail markers ADDED to test-ish files during a run.

    The failure mode is not a worker that cannot fix a test -- it is one that
    can, by skipping it, and hands back a green gate for the trouble. Only
    ADDED lines count: a marker already in the file is somebody's considered
    decision, and a receipt that cries wolf on those is one nobody reads."""

    def base(self):
        rc, out = gittree.git(self.cwd, "rev-parse", "HEAD")
        return out.strip()

    def test_an_added_marker_is_reported_with_its_file(self):
        put(self.cwd, "test_roman.py", "def test_one():\n    assert True\n")
        commit_all(self.cwd, "tests")
        sha = self.base()
        put(self.cwd, "test_roman.py",
            "import unittest\n\n\n@unittest.skip('later')\n"
            "def test_one():\n    assert True\n")
        self.assertEqual(gittree.dodge_markers(self.cwd, sha),
                         {"test_roman.py": ["@unittest.skip"]})

    def test_a_marker_already_in_the_file_is_not_this_runs_doing(self):
        put(self.cwd, "test_roman.py",
            "import unittest\n\n\n@unittest.skip('old')\n"
            "def test_one():\n    assert True\n")
        commit_all(self.cwd, "tests")
        sha = self.base()
        put(self.cwd, "test_roman.py",
            "import unittest\n\n\n@unittest.skip('old')\n"
            "def test_one():\n    assert True\n\n\ndef test_two():\n"
            "    assert True\n")
        self.assertEqual(gittree.dodge_markers(self.cwd, sha), {})

    def test_prose_and_ordinary_words_do_not_fire(self):
        put(self.cwd, "test_roman.py", "def test_one():\n    pass\n")
        commit_all(self.cwd, "tests")
        sha = self.base()
        put(self.cwd, "test_roman.py",
            "# we skipped the slow ones; skiplist is elsewhere\n"
            "SKIPPED = 0\ndef test_one():\n    pass\n")
        self.assertEqual(gittree.dodge_markers(self.cwd, sha), {})

    def test_every_marker_form_the_field_uses(self):
        put(self.cwd, "test_roman.py", "def test_one():\n    pass\n")
        commit_all(self.cwd, "tests")
        sha = self.base()
        put(self.cwd, "test_roman.py",
            "@skip\n@unittest.skip\n@pytest.mark.xfail\n"
            "@unittest.expectedFailure\n@pytest.mark.skipif(True)\n"
            "def test_one():\n    pass\n")
        found = gittree.dodge_markers(self.cwd, sha)["test_roman.py"]
        for marker in ("@skip", "@unittest.skip", "pytest.mark.xfail",
                       "expectedFailure"):
            self.assertIn(marker, found)

    def test_ordinary_source_is_not_scanned(self):
        sha = self.base()
        put(self.cwd, "roman.py", "@skip\ndef to_roman(n):\n    return 'I'\n")
        self.assertEqual(gittree.dodge_markers(self.cwd, sha), {})

    def test_a_committed_dodge_is_still_caught(self):
        # Same hole the spec guard closes: diffing to HEAD after the worker
        # commits sees nothing at all.
        put(self.cwd, "test_roman.py", "def test_one():\n    pass\n")
        commit_all(self.cwd, "tests")
        sha = self.base()
        put(self.cwd, "test_roman.py",
            "import unittest\n@unittest.skip('x')\ndef test_one():\n    pass\n")
        commit_all(self.cwd, "dodge")
        self.assertIn("test_roman.py", gittree.dodge_markers(self.cwd, sha))

    def test_a_brand_new_test_file_is_read_whole(self):
        sha = self.base()
        put(self.cwd, "tests/test_new.py",
            "import unittest\nclass T(unittest.TestCase):\n"
            "    @unittest.expectedFailure\n    def test_x(self): pass\n")
        self.assertEqual(gittree.dodge_markers(self.cwd, sha),
                         {"tests/test_new.py": ["expectedFailure"]})

    def test_an_untracked_file_that_predates_the_run_is_left_alone(self):
        # It is new to git at pre_sha but not new to this run, and blaming a
        # run for what was already on disk is the false-accusation class.
        sha = self.base()
        put(self.cwd, "test_scratch.py", "import unittest\n@unittest.skip\n"
                                         "def test_x(): pass\n")
        pre = gittree.snapshot(self.cwd)
        self.assertEqual(gittree.dodge_markers(self.cwd, sha, pre), {})
        self.assertIn("test_scratch.py", gittree.dodge_markers(self.cwd, sha))

    def test_a_dodge_inside_a_brand_new_directory_is_found(self):
        # `git status --porcelain` collapses a new directory to one `dir/`
        # entry -- without -uall the file carrying the dodge never appears,
        # and a worker could hide every skip behind one fresh folder.
        sha = self.base()
        put(self.cwd, "tests/inner/test_new.py",
            "import unittest\n\n\n@unittest.skip('hidden')\n"
            "def test_n():\n    assert True\n")
        found = gittree.dodge_markers(self.cwd, sha)
        self.assertIn("tests/inner/test_new.py", found)
        self.assertEqual(found["tests/inner/test_new.py"], ["@unittest.skip"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
