#!/usr/bin/env python3
"""
Spec for the seam-risk detectors (v0.6: A18, A20, A22).

Claude-authored gate (never delegate this file -- it defines what correct means).

Sixteen field defects, **zero inside a delegated unit**. Every one lived in a
join between two units. That is structural, not carelessness: a unit brief
describes one module, its gate runs with the rest mocked, and
`preflight_expect` only proves the gate could fail *for that module*. The one
thing the workflow can never assert is "and this is wired to that".

These three detectors do not verify a seam. They make seam RISK visible from
what the run already changed -- three greps, nothing executes:

  UNCALLED       a new public symbol nothing outside its file/tests references
  MOCKED SEAM    a delivered test mocks a module the run also CHANGED
  NEVER EXECUTED a delivered test file the gate command does not run

The design constraint that matters as much as the detection: **they must not
cry wolf.** TEST DODGE was wrong 4 times out of 4 on an ordinary refactor and
the trained response to a noisy line is to stop reading it -- after which it is
ignored on the run where it is right. So every case below that asserts silence
is as load-bearing as the ones that assert a finding.

Run:  python3 specs/seams_spec.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import gittree  # noqa: E402


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True)


class RepoFixture(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        sh(self.cwd, "git", "init", "-q")
        sh(self.cwd, "git", "config", "user.email", "s@t")
        sh(self.cwd, "git", "config", "user.name", "s")
        self.put("README.md", "base\n")
        sh(self.cwd, "git", "add", "-A")
        sh(self.cwd, "git", "commit", "-qm", "base")

    def put(self, rel, body):
        full = os.path.join(self.cwd, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(rel) else None
        with open(full, "w") as f:
            f.write(body)
        return rel


class Uncalled(RepoFixture):
    def test_a_symbol_nothing_references_is_reported(self):
        # The field case: a unit built, gated, merged -- and called by nothing.
        self.put("engine/threads.py", "def run_threads():\n    return 1\n")
        pubs = gittree.new_public_symbols(self.cwd)
        out = gittree.uncalled_symbols(self.cwd, pubs)
        self.assertIn("engine/threads.py", out)
        self.assertIn("run_threads", out["engine/threads.py"])

    def test_a_symbol_its_caller_uses_is_silent(self):
        self.put("engine/threads.py", "def run_threads():\n    return 1\n")
        self.put("engine/dig.py",
                 "from engine.threads import run_threads\n"
                 "def go():\n    return run_threads()\n")
        pubs = gittree.new_public_symbols(self.cwd)
        out = gittree.uncalled_symbols(self.cwd, pubs)
        self.assertNotIn("run_threads",
                         out.get("engine/threads.py", []))

    def test_a_symbol_only_its_own_test_uses_is_still_uncalled(self):
        # THE case worth catching, and the reason tests are excluded from the
        # reference search: the unit and the gate the same run delivered agree
        # with each other, and nothing else in the tree knows the symbol
        # exists. That pair shipped green six times in one build.
        self.put("engine/threads.py", "def run_threads():\n    return 1\n")
        self.put("unit_tests/test_threads_qwen.py",
                 "from engine.threads import run_threads\n"
                 "def test_it():\n    assert run_threads() == 1\n")
        pubs = gittree.new_public_symbols(self.cwd)
        out = gittree.uncalled_symbols(self.cwd, pubs)
        self.assertIn("run_threads", out.get("engine/threads.py", []))

    def test_a_run_that_adds_no_public_surface_is_silent(self):
        self.assertEqual(gittree.uncalled_symbols(self.cwd, {}), {})


class MockedSeam(RepoFixture):
    def test_mocking_a_module_the_run_also_changed_is_reported(self):
        # The field case: the unit mocked the store, so its suite never
        # executed the SQL, so a SELECT of a column that never existed shipped
        # green and crashed on first live contact.
        self.put("engine/store.py", "def get_job_sources():\n    return []\n")
        self.put("unit_tests/test_store_qwen.py",
                 'from unittest import mock\n'
                 'def test_it():\n'
                 '    with mock.patch("engine.store.get_job_sources"):\n'
                 '        pass\n')
        out = gittree.mocked_seams(
            self.cwd, ["engine/store.py", "unit_tests/test_store_qwen.py"])
        self.assertEqual(out, [("unit_tests/test_store_qwen.py",
                                "engine/store.py")])

    def test_monkeypatch_setattr_counts_too(self):
        self.put("engine/store.py", "def q():\n    return []\n")
        self.put("unit_tests/test_s_qwen.py",
                 'def test_it(monkeypatch):\n'
                 '    monkeypatch.setattr("engine.store.q", lambda: [])\n')
        out = gittree.mocked_seams(
            self.cwd, ["engine/store.py", "unit_tests/test_s_qwen.py"])
        self.assertEqual(len(out), 1)

    def test_mocking_an_untouched_third_party_boundary_is_silent(self):
        # Mocking a stable external boundary is ordinary practice. Reporting
        # it would bury the one case that matters -- this is the intersection
        # with CHANGED doing its job.
        self.put("engine/store.py", "def q():\n    return []\n")
        self.put("unit_tests/test_s_qwen.py",
                 'from unittest import mock\n'
                 'def test_it():\n'
                 '    with mock.patch("requests.get"):\n'
                 '        pass\n')
        out = gittree.mocked_seams(
            self.cwd, ["engine/store.py", "unit_tests/test_s_qwen.py"])
        self.assertEqual(out, [])

    def test_a_run_with_no_delivered_tests_is_silent(self):
        self.assertEqual(gittree.mocked_seams(self.cwd, ["engine/store.py"]), [])


class NeverExecuted(RepoFixture):
    """A token counts as a path iff it EXISTS in the tree, so these build the
    directories the gate names."""

    def setUp(self):
        super().setUp()
        for d in ("unit_tests", "gate_tests", "unit_tests/sub", "tests"):
            os.makedirs(os.path.join(self.cwd, d), exist_ok=True)

    def ne(self, changed, cmd):
        return gittree.never_executed(self.cwd, changed, cmd)

    def test_a_delivered_test_the_gate_skips_is_reported(self):
        # The field case: gate_tests/ was authored under green delegations and
        # first executed three weeks later, when one proved unsatisfiable.
        out = self.ne(["gate_tests/test_l6_live.py",
                       "unit_tests/test_a_qwen.py"],
                      "uv run pytest unit_tests -q")
        self.assertEqual(out, ["gate_tests/test_l6_live.py"])

    def test_a_gate_that_names_the_file_is_silent(self):
        self.put("unit_tests/test_a_qwen.py", "def test_x(): pass\n")
        out = self.ne(["unit_tests/test_a_qwen.py"],
                      "uv run pytest unit_tests/test_a_qwen.py -q "
                      "&& uv run pytest unit_tests -q")
        self.assertEqual(out, [])

    def test_a_directory_gate_covers_the_files_under_it(self):
        out = self.ne(["unit_tests/sub/test_a_qwen.py"],
                      "pytest unit_tests -q")
        self.assertEqual(out, [])

    def test_a_whole_suite_runner_naming_no_paths_convicts_nobody(self):
        # Conservative on purpose: guessing at what `make test` covers would
        # manufacture exactly the false accusations that make a receipt line
        # unreadable. Only a gate that names paths can convict.
        for cmd in ("make test", "npm test", "cargo test"):
            self.assertEqual(self.ne(["tests/test_a.py"], cmd), [], cmd)

    def test_no_gate_and_no_tests_are_both_silent(self):
        self.assertEqual(self.ne(["tests/test_a.py"], ""), [])
        self.assertEqual(self.ne([], "pytest tests"), [])

    def test_non_test_files_are_never_reported(self):
        # Shipping a source file the gate does not import is normal.
        self.assertEqual(self.ne(["engine/store.py"], "pytest unit_tests"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
