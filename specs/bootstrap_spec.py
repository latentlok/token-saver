#!/usr/bin/env python3
"""
Spec for qd/bootstrap.py -- self-configuration (LLD "qd/bootstrap.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

Port gate, crane-equality style: for identical inputs qd.bootstrap must agree
with the running server byte-for-byte (both read the SAME templates/QWEN.md, so
the v2 rule additions -- refs pinning, the *_qwen.* self-test convention -- are
made in the TEMPLATE, upgrading both sides identically). Plus the v2 seam: the
rules FILENAME is profile-driven (C1 rules_file), defaulting to QWEN.md.

Load-bearing:
  1. detect_test_cmd's table must match the crane exactly -- a wrong detected
     command becomes a wrong standing rule in every future session.
  2. render_worker_rules must never emit a placeholder ("TEMPLATE" banner or
     the un-substituted testing line) -- measured rule: never write a
     placeholder.
  3. The template must now carry the refs rule (.qwen-delegate/refs/) and the
     self-test convention (*_qwen.*) -- the v2 additions land in the template.
  4. bootstrap_worker_rules is atomic, backs up pre-existing files, returns
     (test_cmd, dest) or (None, None), never raises.
  5. rules_file="AGENTS.md" must change the DESTINATION name only, not the
     content.

Public surface pinned here:
    detect_test_cmd(cwd), render_worker_rules(test_cmd),
    bootstrap_worker_rules(cwd, rules_file="QWEN.md"),
    worker_rules_path(cwd, rules_file="QWEN.md"),
    worker_rules_status(cwd, rules_file="QWEN.md"),
    bootstrap_notice(test_cmd, path), unconfigured_notice(cwd, state, path),
    unconfigured_reason(cwd, state, path), nongit_refusal(cwd),
    bootstrap_failed_refusal(cwd, reason)

Run:  python3 specs/bootstrap_spec.py
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import bootstrap  # noqa: E402


def mkrepo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", d], check=True)
    return d


def put(d, rel, content=""):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(rel) else None
    with open(p, "w") as f:
        f.write(content)
    return p


class DetectTable(unittest.TestCase):
    """Case 1: detection returns the expected command for each marker file."""

    def check(self, setup, expected):
        d = tempfile.mkdtemp()
        setup(d)
        self.assertEqual(bootstrap.detect_test_cmd(d), expected)

    def test_npm(self):
        self.check(lambda d: put(d, "package.json",
                                 '{"scripts": {"test": "jest"}}'), "npm test")

    def test_npm_without_test_script_falls_through(self):
        self.check(lambda d: put(d, "package.json", '{"name": "x"}'), "")

    def test_cargo(self):
        self.check(lambda d: put(d, "Cargo.toml", "[package]"), "cargo test")

    def test_go(self):
        self.check(lambda d: put(d, "go.mod", "module x"), "go test ./...")

    def test_gemfile(self):
        self.check(lambda d: put(d, "Gemfile", ""), "bundle exec rspec")

    def test_venv_pytest_needs_exec_bit(self):
        def setup(d):
            p = put(d, "venv/bin/pytest", "#!/bin/sh\n")
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        self.check(setup,
                   'venv/bin/pytest -q -o "python_files=test_*.py *_test.py *_spec.py"')

    def test_pyproject(self):
        self.check(lambda d: put(d, "pyproject.toml", ""),
                   'python -m pytest -q -o "python_files=test_*.py *_test.py *_spec.py"')

    def test_nothing(self):
        self.check(lambda d: None, "")


class RenderRules(unittest.TestCase):
    def test_detected_command_reaches_the_rules_file(self):
        # Was a crane-equality test against the frozen v1. The contract that
        # actually matters: whatever was detected is what the worker is told.
        self.assertIn("npm test", bootstrap.render_worker_rules("npm test"))

    def test_no_placeholder_ever(self):
        for cmd in ("cargo test", ""):
            s = bootstrap.render_worker_rules(cmd)
            first_para = s.split("\n\n")[0]
            self.assertNotIn("TEMPLATE", first_para)
            self.assertNotIn("<-- EDIT", s)

    def test_v2_template_additions_present(self):
        # Case 3: the refs rule and the self-test convention live in the
        # template now, so every rendered rules file carries them.
        s = bootstrap.render_worker_rules("npm test")
        self.assertIn(".qwen-delegate/refs/", s)
        self.assertIn("_qwen.", s)

    def test_no_test_cmd_is_an_instruction_not_a_blank(self):
        s = bootstrap.render_worker_rules("")
        self.assertIn("Do NOT guess", s)


class Bootstrap(unittest.TestCase):
    def test_creates_rules_file_and_returns_cmd_dest(self):
        d = mkrepo()
        put(d, "go.mod", "module x")
        cmd, dest = bootstrap.bootstrap_worker_rules(d)
        self.assertEqual(cmd, "go test ./...")
        self.assertEqual(os.path.basename(dest), "QWEN.md")
        with open(dest) as f:
            body = f.read()
        self.assertIn("go test ./...", body)
        self.assertNotIn(".tmp", os.listdir(d))  # atomic: no leftovers

    def test_backs_up_existing(self):
        d = mkrepo()
        put(d, "QWEN.md", "old contents")
        cmd, dest = bootstrap.bootstrap_worker_rules(d)
        with open(dest + ".bak") as f:
            self.assertEqual(f.read(), "old contents")

    def test_rules_file_param_changes_destination_only(self):
        d = mkrepo()
        cmd, dest = bootstrap.bootstrap_worker_rules(d, rules_file="AGENTS.md")
        self.assertEqual(os.path.basename(dest), "AGENTS.md")
        with open(dest) as f:
            agents = f.read()
        d2 = mkrepo()
        _, dest2 = bootstrap.bootstrap_worker_rules(d2)
        with open(dest2) as f:
            qwen = f.read()
        self.assertEqual(agents, qwen)  # same content, different name

    def test_never_raises_on_unwritable(self):
        d = mkrepo()
        os.chmod(d, 0o555)
        try:
            cmd, dest = bootstrap.bootstrap_worker_rules(d)
        finally:
            os.chmod(d, 0o755)
        self.assertEqual((cmd, dest), (None, None))


class StatusAndNotices(unittest.TestCase):
    """Was crane equality against the frozen v1. Pinned to the contracts the
    engine and the receipts actually read: the STATE, and the load-bearing
    facts each notice must carry. Deliberately not byte-equality on prose --
    a wording change is not a regression, a missing path is."""

    def test_status_across_states(self):
        # missing / ok / subdirectory-of-configured
        d = mkrepo()
        self.assertEqual(bootstrap.worker_rules_status(d), ("missing", None))
        bootstrap.bootstrap_worker_rules(d)
        state, path = bootstrap.worker_rules_status(d)
        self.assertEqual(state, "ok")
        self.assertEqual(os.path.basename(path), "QWEN.md")
        # A deep subdirectory walks up to the SAME configured file: the worker
        # is governed by the repo's rules wherever in the tree it is invoked.
        sub = os.path.join(d, "src", "deep")
        os.makedirs(sub)
        self.assertEqual(bootstrap.worker_rules_status(sub), (state, path))

    def test_bootstrap_notice_names_the_file_and_the_command(self):
        with_cmd = bootstrap.bootstrap_notice("npm test", "/p/QWEN.md")
        self.assertIn("/p/QWEN.md", with_cmd)
        self.assertIn("npm test", with_cmd)
        self.assertIn("uncommitted", with_cmd)
        # No command detected: the notice must ASK rather than leave a blank,
        # because a silently empty test command is a gate that cannot fail.
        without = bootstrap.bootstrap_notice("", "/p/QWEN.md")
        self.assertIn("/p/QWEN.md", without)
        self.assertIn("could not detect a test command", without)

    def test_unconfigured_notice_embeds_the_cwd(self):
        # The message names the directory it is talking about; a notice that
        # says "no QWEN.md governs" without saying where is unactionable.
        d = mkrepo()
        state, path = bootstrap.worker_rules_status(d)
        notice = bootstrap.unconfigured_notice(d, state, path)
        self.assertIn(d, notice)
        self.assertIn("QWEN.md", notice)

    def test_nongit_refusal_is_an_error_receipt_naming_the_path(self):
        nong = tempfile.mkdtemp()
        refusal = bootstrap.nongit_refusal(nong)
        self.assertTrue(refusal.startswith("STATUS: error"))
        self.assertIn(nong, refusal)
        self.assertIn("git init", refusal)   # the fix, not just the diagnosis


class TestLocation(unittest.TestCase):
    """Where a project keeps its tests is a project's own business. Every
    detector here is a guess, so a project only has to be slightly unusual to
    get the wrong command or none at all -- and 'none at all' means the
    self-graded gate falls back to a folder that may not exist and can never
    go green."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def cfg(self, **keys):
        with open(os.path.join(self.d, ".qwen-delegate.json"), "w") as f:
            json.dump(keys, f)

    def test_an_explicit_command_wins_over_every_detector(self):
        os.makedirs(os.path.join(self.d, "tests"))
        with open(os.path.join(self.d, "Cargo.toml"), "w") as f:
            f.write("[package]\n")
        self.cfg(test_command="make check")
        self.assertEqual(bootstrap.detect_test_cmd(self.d), "make check")

    def test_a_named_test_dir_is_used_for_discovery(self):
        os.makedirs(os.path.join(self.d, "t"))
        self.cfg(test_dir="t")
        self.assertEqual(bootstrap.test_dir(self.d), "t")
        self.assertIn("-s t ", bootstrap.detect_test_cmd(self.d))

    def test_a_plain_tests_folder_is_found_without_any_config(self):
        # The common case: stdlib unittest in tests/, no packaging metadata.
        os.makedirs(os.path.join(self.d, "tests"))
        self.assertEqual(bootstrap.test_dir(self.d), "tests")
        self.assertIn("unittest discover -s tests", bootstrap.detect_test_cmd(self.d))

    def test_other_conventional_names_are_recognised(self):
        os.makedirs(os.path.join(self.d, "spec"))
        self.assertEqual(bootstrap.test_dir(self.d), "spec")

    def test_packaging_metadata_still_wins_over_the_folder_guess(self):
        os.makedirs(os.path.join(self.d, "tests"))
        with open(os.path.join(self.d, "pyproject.toml"), "w") as f:
            f.write("[project]\n")
        self.assertIn("pytest", bootstrap.detect_test_cmd(self.d))

    def test_nothing_detectable_still_returns_empty(self):
        self.assertEqual(bootstrap.detect_test_cmd(self.d), "")
        self.assertIsNone(bootstrap.test_dir(self.d))

    def test_a_corrupt_config_does_not_break_detection(self):
        with open(os.path.join(self.d, ".qwen-delegate.json"), "w") as f:
            f.write("{not json")
        os.makedirs(os.path.join(self.d, "tests"))
        self.assertIn("tests", bootstrap.detect_test_cmd(self.d))


if __name__ == "__main__":
    unittest.main(verbosity=1)
