#!/usr/bin/env python3
"""
Spec for qd/refs.py -- fetched-reference pinning (HLD F6/C6, LLD "qd/refs.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

The load-bearing cases:

  1. The refs dir is git-ignored (the .delegation/* self-ignore), so git diff
     CANNOT see it -- detection must come from a filesystem listing diff, and the
     refs must never pollute git status (which would corrupt blast-radius
     attribution of Qwen's work).
  2. New AND modified refs are both reported; untouched ones are not.
  3. The receipt line is single-line and exact (C2 grammar): the skill relays it
     verbatim.

Public surface pinned here:
    qd.refs.snapshot(cwd) -> dict            {relpath: fingerprint} of .delegation/refs/
    qd.refs.added(before, cwd) -> list[str]  new or changed since `before`, sorted
    qd.refs.refs_line(names) -> str | None   C2 "REFS:" line; None when empty

Run:  python3 specs/refs_spec.py
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import refs  # noqa: E402


REFS_REL = os.path.join(".delegation", "refs")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()

    def refs_dir(self):
        d = os.path.join(self.cwd, REFS_REL)
        os.makedirs(d, exist_ok=True)
        # The self-ignore convention the run log already uses.
        gi = os.path.join(self.cwd, ".delegation", ".gitignore")
        if not os.path.exists(gi):
            with open(gi, "w") as f:
                f.write("*\n")
        return d

    def put(self, name, content="x"):
        path = os.path.join(self.refs_dir(), name)
        with open(path, "w") as f:
            f.write(content)
        return path


class Detection(Fixture):
    def test_missing_dir_is_empty_not_error(self):
        self.assertEqual(refs.snapshot(self.cwd), {})
        self.assertEqual(refs.added({}, self.cwd), [])

    def test_new_ref_detected(self):
        before = refs.snapshot(self.cwd)
        self.put("jinja-api.md", "# docs\nhttps://example.com\n")
        self.assertEqual(refs.added(before, self.cwd), ["jinja-api.md"])

    def test_modified_ref_detected(self):
        self.put("a.md", "v1")
        before = refs.snapshot(self.cwd)
        time.sleep(0.01)
        self.put("a.md", "v2 with more bytes")
        self.assertEqual(refs.added(before, self.cwd), ["a.md"])

    def test_untouched_ref_not_reported(self):
        self.put("stable.md", "same")
        before = refs.snapshot(self.cwd)
        self.put("new.md", "fresh")
        self.assertEqual(refs.added(before, self.cwd), ["new.md"])

    def test_multiple_sorted(self):
        before = refs.snapshot(self.cwd)
        self.put("b.md")
        self.put("a.md")
        self.assertEqual(refs.added(before, self.cwd), ["a.md", "b.md"])

    def test_non_md_junk_still_visible(self):
        # Visibility over tidiness: anything landing in refs/ is reported.
        before = refs.snapshot(self.cwd)
        self.put("notes.txt")
        self.assertEqual(refs.added(before, self.cwd), ["notes.txt"])


class GitInvisibility(Fixture):
    def test_refs_never_appear_in_git_status(self):
        subprocess.run(["git", "init", "-q", self.cwd], check=True)
        self.put("fetched.md", "https://example.com\ncontent")
        out = subprocess.run(["git", "status", "--porcelain"], cwd=self.cwd,
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "")


class ReceiptLine(Fixture):
    def test_empty_is_none(self):
        self.assertIsNone(refs.refs_line([]))

    def test_line_format_exact(self):
        self.assertEqual(refs.refs_line(["jinja-api.md", "ollama.md"]),
                         "REFS: 2 saved (jinja-api.md, ollama.md)")

    def test_single(self):
        self.assertEqual(refs.refs_line(["x.md"]), "REFS: 1 saved (x.md)")

    def test_names_sanitized_to_single_line(self):
        line = refs.refs_line(["bad\nname.md", "com,ma.md"])
        self.assertNotIn("\n", line)
        self.assertTrue(line.startswith("REFS: 2 saved ("))
        # A comma inside a name may not masquerade as a separator.
        inner = line[line.index("(") + 1:line.rindex(")")]
        self.assertEqual(len(inner.split(", ")), 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
