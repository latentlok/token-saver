#!/usr/bin/env python3
"""Tests for Progress and read_progress in qd.limits."""

import json
import os
import stat as stat_mod
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import limits  # noqa: E402


def assistant(input_tokens):
    return {"type": "assistant",
            "message": {"usage": {"input_tokens": input_tokens,
                                  "output_tokens": 0}}}


class ProgressSnapshot(unittest.TestCase):
    """Snapshot keys and values after a sequence of records."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.p = limits.Progress(self.tmpdir, session_id="test-42")

    def test_all_keys_present(self):
        self.p(assistant(500))
        snap = limits.read_progress(self.tmpdir)
        # attempt/state joined the snapshot with the C11 wiring (U4.4).
        for key in ("session", "records", "input_tokens", "last_type",
                    "updated", "attempt", "state"):
            self.assertIn(key, snap, f"missing key {key!r}")

    def test_session_id(self):
        self.p(assistant(100))
        snap = limits.read_progress(self.tmpdir)
        self.assertEqual(snap["session"], "test-42")

    def test_session_none_when_not_given(self):
        p = limits.Progress(self.tmpdir)
        p(assistant(1))
        snap = limits.read_progress(self.tmpdir)
        self.assertIsNone(snap["session"])

    def test_record_count_accumulates(self):
        self.p(assistant(100))
        self.p(assistant(200))
        self.p(assistant(300))
        snap = limits.read_progress(self.tmpdir)
        self.assertEqual(snap["records"], 3)

    def test_input_tokens_sums_via_record_input_tokens(self):
        self.p(assistant(100))
        self.p(assistant(200))
        self.p({"type": "result"})          # contributes 0
        snap = limits.read_progress(self.tmpdir)
        self.assertEqual(snap["input_tokens"], 300)

    def test_last_type_reflects_most_recent(self):
        self.p(assistant(100))
        self.p({"type": "system"})
        snap = limits.read_progress(self.tmpdir)
        self.assertEqual(snap["last_type"], "system")

    def test_updated_is_utc_timestamp(self):
        self.p(assistant(1))
        snap = limits.read_progress(self.tmpdir)
        self.assertRegex(snap["updated"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

    def test_always_returns_none(self):
        self.assertIsNone(self.p(assistant(1)))
        self.assertIsNone(self.p(None))
        self.assertIsNone(self.p("garbage"))

    def test_creates_directory(self):
        nested = os.path.join(self.tmpdir, "sub", "dir")
        p = limits.Progress(nested)
        p(assistant(1))
        target = os.path.join(nested, ".delegation", "progress.json")
        self.assertTrue(os.path.isfile(target))


class ProgressAtomicity(unittest.TestCase):
    """The progress.json file is always valid JSON (atomic writes)."""

    def test_file_is_valid_json_after_each_record(self):
        tmpdir = tempfile.mkdtemp()
        p = limits.Progress(tmpdir)
        for _ in range(20):
            p(assistant(999))
            with open(os.path.join(tmpdir, ".delegation", "progress.json")) as f:
                json.load(f)  # must not raise


class ProgressNeverRaises(unittest.TestCase):
    """Progress must never raise, regardless of input or FS errors."""

    def test_none_record(self):
        p = limits.Progress(tempfile.mkdtemp())
        self.assertIsNone(p(None))

    def test_non_dict_record(self):
        p = limits.Progress(tempfile.mkdtemp())
        self.assertIsNone(p([1, 2]))
        self.assertIsNone(p("text"))
        self.assertIsNone(p(42))

    def test_malformed_dict(self):
        p = limits.Progress(tempfile.mkdtemp())
        self.assertIsNone(p({"not": "a", "record": True}))

    def test_unwritable_directory(self):
        tmpdir = tempfile.mkdtemp()
        readonly = os.path.join(tmpdir, "no_write")
        os.makedirs(readonly)
        os.chmod(readonly, stat_mod.S_IRUSR)
        p = limits.Progress(readonly)
        self.assertIsNone(p(assistant(1)))  # must not raise
        # Restore permissions so cleanup works
        os.chmod(readonly, stat_mod.S_IRWXU)


class ReadProgress(unittest.TestCase):
    """read_progress behaviour."""

    def test_returns_none_for_missing_file(self):
        tmpdir = tempfile.mkdtemp()
        self.assertIsNone(limits.read_progress(tmpdir))

    def test_returns_none_for_corrupt_file(self):
        tmpdir = tempfile.mkdtemp()
        prog_dir = os.path.join(tmpdir, ".delegation")
        os.makedirs(prog_dir)
        with open(os.path.join(prog_dir, "progress.json"), "w") as f:
            f.write("not-json{{{")
        self.assertIsNone(limits.read_progress(tmpdir))

    def test_returns_parsed_dict(self):
        tmpdir = tempfile.mkdtemp()
        p = limits.Progress(tmpdir, session_id="s1")
        p(assistant(777))
        snap = limits.read_progress(tmpdir)
        self.assertIsInstance(snap, dict)
        self.assertEqual(snap["session"], "s1")
        self.assertEqual(snap["input_tokens"], 777)

    def test_never_raises_on_permission_error(self):
        tmpdir = tempfile.mkdtemp()
        prog_dir = os.path.join(tmpdir, ".delegation")
        os.makedirs(prog_dir)
        os.chmod(prog_dir, 0)
        self.assertIsNone(limits.read_progress(tmpdir))
        os.chmod(prog_dir, stat_mod.S_IRWXU)


if __name__ == "__main__":
    unittest.main(verbosity=1)
