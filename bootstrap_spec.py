#!/usr/bin/env python3
"""
Spec for self-configuration: detecting the test command and writing QWEN.md unattended.

Claude-authored gate (never delegate this file -- it defines what correct means).

The change this guards: a first delegation into a git repo no longer refuses, it writes
QWEN.md itself. The whole safety argument for that rests on one property -- it must NEVER
write a placeholder. A wrong-but-plausible test command, or an "<EDIT ME>" left in the
file, is precisely the failure the old refusal existed to catch. So, in order of what
would hurt most if broken:

  1. render never emits a placeholder. Detected command -> real command; nothing detected
     -> an INSTRUCTION not to guess, never a blank.
  2. detection is the single source of truth in Python (no bash copy to drift), so its
     results must match the stack conventions for each ecosystem.
  3. the write is atomic and backs up a legacy file -- a torn or silently-lost QWEN.md is
     worse than none.
  4. failure is best-effort: a bootstrap that raises would kill the delegation it is
     trying to enable.

Run:  python3 bootstrap_spec.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402


class Detection(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="boot-det-")

    def touch(self, name, body="x"):
        p = os.path.join(self.d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(name) else None
        with open(p, "w") as f:
            f.write(body)
        return p

    def test_empty_project_detects_nothing(self):
        self.assertEqual(server.detect_test_cmd(self.d), "")

    def test_npm_only_when_test_script_present(self):
        self.touch("package.json", '{"name":"x"}')
        self.assertEqual(server.detect_test_cmd(self.d), "",
                         "a package.json without a test script is not a test command")
        self.touch("package.json", '{"scripts":{"test":"jest"}}')
        self.assertEqual(server.detect_test_cmd(self.d), "npm test")

    def test_rust_go_ruby(self):
        for fname, expected in [("Cargo.toml", "cargo test"),
                                ("go.mod", "go test ./..."),
                                ("Gemfile", "bundle exec rspec")]:
            d = tempfile.mkdtemp(prefix="boot-det-")
            with open(os.path.join(d, fname), "w") as f:
                f.write("x")
            self.assertEqual(server.detect_test_cmd(d), expected)

    def test_venv_pytest_beats_pyproject(self):
        """A real venv binary is a better answer than a bare `python -m pytest`."""
        self.touch("pyproject.toml", "[tool]")
        vb = os.path.join(self.d, "venv", "bin")
        os.makedirs(vb)
        p = os.path.join(vb, "pytest")
        open(p, "w").close()
        os.chmod(p, 0o755)
        self.assertEqual(server.detect_test_cmd(self.d), "venv/bin/pytest -q")

    def test_pyproject_falls_back_to_module_pytest(self):
        self.touch("pyproject.toml", "[tool]")
        self.assertEqual(server.detect_test_cmd(self.d), "python -m pytest -q")


class RenderNeverEmitsAPlaceholder(unittest.TestCase):
    def test_detected_command_is_written_verbatim(self):
        out = server.render_worker_rules("cargo test")
        self.assertIn("Run tests with: `cargo test`", out)

    def test_no_command_becomes_an_instruction_not_a_blank(self):
        out = server.render_worker_rules("")
        self.assertIn("No test command is configured", out)
        self.assertIn("Do NOT guess", out)

    def test_output_never_contains_a_placeholder_marker(self):
        for cmd in ("", "npm test", "venv/bin/pytest -q"):
            out = server.render_worker_rules(cmd)
            for marker in server.RULES_PLACEHOLDERS:
                self.assertNotIn(marker, out, f"placeholder leaked for cmd={cmd!r}")

    def test_template_banner_is_stripped(self):
        out = server.render_worker_rules("npm test")
        self.assertNotIn("TEMPLATE", out.split("\n\n")[0])

    def test_the_core_rules_survive_rendering(self):
        """A rendered QWEN.md that dropped the spec-protection rule would be worse than
        none -- it would look configured while missing the load-bearing rule."""
        out = server.render_worker_rules("npm test")
        low = out.lower()
        self.assertIn("spec", low)
        self.assertIn("honest", low)


class Write(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="boot-w-")
        self.reg = server.PROJECT_REGISTRY
        server.PROJECT_REGISTRY = os.path.join(self.d, "registry.jsonl")

    def tearDown(self):
        server.PROJECT_REGISTRY = self.reg

    def test_writes_a_usable_rules_file(self):
        cmd, path = server.bootstrap_worker_rules(self.d)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(server.worker_rules_status(self.d)[0], "ok")

    def test_detects_and_records_the_command(self):
        with open(os.path.join(self.d, "go.mod"), "w") as f:
            f.write("module x\n")
        cmd, _ = server.bootstrap_worker_rules(self.d)
        self.assertEqual(cmd, "go test ./...")
        self.assertIn("go test ./...", open(os.path.join(self.d, "QWEN.md")).read())

    def test_legacy_placeholder_file_is_backed_up_not_lost(self):
        with open(os.path.join(self.d, "QWEN.md"), "w") as f:
            f.write("- Run tests with: `<EDIT ME>`\nMY HAND EDITS\n")
        server.bootstrap_worker_rules(self.d)
        self.assertTrue(os.path.isfile(os.path.join(self.d, "QWEN.md.bak")),
                        "an existing file must be backed up before regeneration")
        self.assertIn("MY HAND EDITS", open(os.path.join(self.d, "QWEN.md.bak")).read())
        self.assertEqual(server.worker_rules_status(self.d)[0], "ok")

    def test_registers_the_project(self):
        server.bootstrap_worker_rules(self.d)
        with open(server.PROJECT_REGISTRY) as f:
            self.assertIn(os.path.realpath(self.d), f.read())

    def test_no_partial_file_left_on_render_failure(self):
        """If rendering blows up (template drift), there must be NO QWEN.md at all --
        never a truncated one the worker would then read as its rules."""
        real = server.render_worker_rules
        server.render_worker_rules = lambda c: (_ for _ in ()).throw(RuntimeError("drift"))
        try:
            cmd, path = server.bootstrap_worker_rules(self.d)
        finally:
            server.render_worker_rules = real
        self.assertIsNone(path, "a failed bootstrap must report failure, not a path")
        self.assertFalse(os.path.isfile(os.path.join(self.d, "QWEN.md")))

    def test_bootstrap_never_raises(self):
        """Even pointed at a path it cannot write, it returns (None, None)."""
        cmd, path = server.bootstrap_worker_rules("/proc/nonexistent-xyz")
        self.assertIsNone(path)


class Notice(unittest.TestCase):
    def test_detected_command_notice_names_it(self):
        note = server.bootstrap_notice("npm test", "/r/QWEN.md")
        self.assertIn("SETUP:", note)
        self.assertIn("npm test", note)
        self.assertIn("commit", note.lower())

    def test_undetected_notice_asks_for_the_command(self):
        note = server.bootstrap_notice("", "/r/QWEN.md")
        self.assertIn("could not detect", note.lower())
        self.assertIn("tell me", note.lower())

    def test_notice_offers_the_claude_md_block(self):
        note = server.bootstrap_notice("npm test", "/r/QWEN.md")
        self.assertIn("CLAUDE.md", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
