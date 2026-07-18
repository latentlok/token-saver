#!/usr/bin/env python3
"""
Spec for the first-use preconditions: is this project actually configured to delegate?

Claude-authored gate (never delegate this file -- it defines what correct means).

The failure this exists to prevent is SILENT. Without QWEN.md the worker's standing rules
are simply not loaded, and measured, it then edits protected spec files, games gates, and
expands scope -- while the run still reports success. So the load-bearing cases, in order
of what would hurt most if broken:

  1. qwen_delegate must REFUSE an unconfigured project, and must not invoke Qwen at all.
     A refusal that still ran the worker would be worse than no check.
  2. A QWEN.md carrying an unreplaced placeholder counts as unconfigured. It is worse
     than an absent one: the worker reads the placeholder as an instruction.
  3. The repo boundary must hold in both directions -- a subdirectory of a configured
     repo IS configured (Qwen loads context hierarchically), a QWEN.md above the repo
     top level is NOT this project's rules.
  4. qwen_query must NOT refuse. It cannot write, so refusing is gratuitous friction on
     the cheapest way to try the system; it warns instead.
  5. init-project.sh must never write a placeholder into QWEN.md, and must never clobber
     or duplicate content in a user's existing CLAUDE.md.

Run:  python3 setup_spec.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
INIT = os.path.join(HERE, "init-project.sh")

_REAL_REGISTRY = None
_REG_DIR = None


def setUpModule():
    """
    Redirect the project registry for the WHOLE module. Anything that drives run_qwen to
    completion calls write_runlog -> register_project, which would otherwise append temp
    paths to the user's real ~/.qwen-delegate/projects.jsonl.
    """
    global _REAL_REGISTRY, _REG_DIR
    _REAL_REGISTRY = server.PROJECT_REGISTRY
    _REG_DIR = tempfile.mkdtemp(prefix="setupspec-reg-")
    server.PROJECT_REGISTRY = os.path.join(_REG_DIR, "projects.jsonl")


def tearDownModule():
    server.PROJECT_REGISTRY = _REAL_REGISTRY
    shutil.rmtree(_REG_DIR, ignore_errors=True)


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)


def make_repo(root, name="proj"):
    """A real git repo with one commit -- the guard needs a HEAD to diff against."""
    p = os.path.join(root, name)
    os.makedirs(p)
    git(p, "init", "-q")
    git(p, "config", "user.email", "spec@test")
    git(p, "config", "user.name", "spec")
    with open(os.path.join(p, "a.py"), "w") as f:
        f.write("x = 1\n")
    git(p, "add", "-A")
    git(p, "commit", "-qm", "init")
    return p


def write_rules(path, body="rules: report honestly\n"):
    with open(os.path.join(path, "QWEN.md"), "w") as f:
        f.write(body)


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="setupspec-")
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)


# ---------- 1. locating the rules ----------


class RulesLocation(Base):
    def test_configured_repo_is_ok(self):
        r = make_repo(self.td)
        write_rules(r)
        self.assertEqual(server.worker_rules_status(r)[0], "ok")

    def test_bare_repo_is_missing(self):
        r = make_repo(self.td)
        state, path = server.worker_rules_status(r)
        self.assertEqual(state, "missing")
        self.assertIsNone(path)

    def test_subdirectory_of_a_configured_repo_is_configured(self):
        """
        Qwen loads context files hierarchically, so delegating into repo/src must not be
        refused because the rules sit at repo/. Refusing here would make the check fire
        on correctly-configured projects, which is how a safety check gets disabled.
        """
        r = make_repo(self.td)
        write_rules(r)
        sub = os.path.join(r, "src", "deep")
        os.makedirs(sub)
        state, path = server.worker_rules_status(sub)
        self.assertEqual(state, "ok")
        self.assertEqual(os.path.realpath(path),
                         os.path.realpath(os.path.join(r, "QWEN.md")))

    def test_rules_above_the_repo_top_level_do_not_count(self):
        """
        The other direction. A QWEN.md in a parent directory belongs to some other project
        (or to $HOME); counting it would let an unrelated file silently satisfy the check
        for every repo underneath it.
        """
        write_rules(self.td)  # sits ABOVE the repo
        r = make_repo(self.td)
        self.assertEqual(server.worker_rules_status(r)[0], "missing")

    def test_non_git_dir_only_counts_its_own_rules(self):
        outer = os.path.join(self.td, "outer")
        inner = os.path.join(outer, "inner")
        os.makedirs(inner)
        write_rules(outer)
        self.assertEqual(server.worker_rules_status(outer)[0], "ok")
        self.assertEqual(server.worker_rules_status(inner)[0], "missing")


class PlaceholderIsNotConfigured(Base):
    def test_edit_me_marker_is_rejected(self):
        r = make_repo(self.td)
        write_rules(r, "- Run tests with: `<EDIT ME: your project's test command>`\n")
        self.assertEqual(server.worker_rules_status(r)[0], "placeholder")

    def test_template_arrow_marker_is_rejected(self):
        """A raw copy of templates/QWEN.md carries this one instead."""
        r = make_repo(self.td)
        write_rules(r, "- Run tests with: `venv/bin/pytest`  <-- EDIT: your real command.\n")
        self.assertEqual(server.worker_rules_status(r)[0], "placeholder")

    def test_a_real_command_is_not_a_placeholder(self):
        r = make_repo(self.td)
        write_rules(r, "- Run tests with: `venv/bin/pytest -q`\n")
        self.assertEqual(server.worker_rules_status(r)[0], "ok")


# ---------- 2. the delegate entry path refuses ----------


class DelegateRefusesUnconfigured(Base):
    """Drive the real run_qwen with a stubbed Qwen and watch whether it is ever called."""

    def drive(self, cwd):
        calls = []
        real_invoke, real_verify = server.invoke_qwen, server.run_verify

        def fake_invoke(task, c, mode, timeout, session_id, **kw):
            calls.append(c)
            return "done", [], "sess-A", None, {"peak": 1, "stats": {}, "blocked": []}

        server.invoke_qwen = fake_invoke
        server.run_verify = lambda v, c: (len(calls) >= 1, "out")
        try:
            out = server.run_qwen({"task": "do a thing", "cwd": cwd,
                                   "approval_mode": "auto-edit", "max_iterations": 1})
        finally:
            server.invoke_qwen, server.run_verify = real_invoke, real_verify
        return out, calls

    def test_missing_rules_refuses_and_never_runs_qwen(self):
        r = make_repo(self.td)
        out, calls = self.drive(r)
        self.assertTrue(out.startswith("STATUS: error"), out[:200])
        self.assertEqual(calls, [], "refusal must happen BEFORE the worker is invoked")

    def test_placeholder_rules_refuse_and_never_run_qwen(self):
        r = make_repo(self.td)
        write_rules(r, "- Run tests with: `<EDIT ME: your test command>`\n")
        out, calls = self.drive(r)
        self.assertTrue(out.startswith("STATUS: error"), out[:200])
        self.assertEqual(calls, [])

    def test_configured_project_is_not_refused(self):
        """The check must not be a blanket refusal -- it has to let real work through."""
        r = make_repo(self.td)
        write_rules(r)
        out, calls = self.drive(r)
        self.assertFalse(out.startswith("STATUS: error\nProject not configured"), out[:200])
        self.assertEqual(calls, [r], "a configured project must reach the worker")

    def test_refusal_names_the_exact_fix(self):
        """
        An unactionable refusal just relocates the friction. It must name the script and
        the path, so the reader can copy one line and be done.
        """
        r = make_repo(self.td)
        out, _ = self.drive(r)
        self.assertIn("init-project.sh", out)
        self.assertIn(r, out)
        self.assertIn("QWEN.md", out)

    def test_refusal_explains_why_it_matters(self):
        r = make_repo(self.td)
        out, _ = self.drive(r)
        low = out.lower()
        self.assertTrue(any(k in low for k in ("spec file", "scope", "rules")),
                        "refusal must say what goes wrong without it, not just 'run this'")


# ---------- 3. the query entry path warns, and does not refuse ----------


class QueryWarnsButProceeds(Base):
    def drive(self, cwd):
        real_invoke = server.invoke_qwen
        real_log = server.write_runlog
        server.invoke_qwen = lambda *a, **kw: (
            "the answer", [], "sess-Q", None, {"peak": 1, "stats": {}, "blocked": []})
        server.write_runlog = lambda c, r: None
        try:
            return server.run_query({"question": "how does x work", "cwd": cwd})
        finally:
            server.invoke_qwen, server.write_runlog = real_invoke, real_log

    def test_unconfigured_query_still_answers(self):
        r = make_repo(self.td)
        out = self.drive(r)
        self.assertTrue(out.startswith("STATUS: ok"), out[:200])
        self.assertIn("the answer", out)

    def test_unconfigured_query_warns(self):
        r = make_repo(self.td)
        self.assertIn("SETUP:", self.drive(r))

    def test_configured_query_does_not_warn(self):
        """A warning on every call is a warning nobody reads."""
        r = make_repo(self.td)
        write_rules(r)
        self.assertNotIn("SETUP:", self.drive(r))


# ---------- 4. init-project.sh ----------


class InitProject(Base):
    def init(self, target, *args, expect_ok=True):
        env = dict(os.environ)
        env["QWEN_DELEGATE_REGISTRY"] = os.path.join(self.td, "registry.jsonl")
        p = subprocess.run([INIT, target, *args], capture_output=True, text=True,
                           env=env, stdin=subprocess.DEVNULL)
        if expect_ok:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def read(self, *parts):
        with open(os.path.join(*parts)) as f:
            return f.read()

    # --- the placeholder must never be written ---

    def test_undetectable_test_command_writes_no_placeholder(self):
        """
        The old behaviour wrote '<EDIT ME: your project's test command>' into QWEN.md.
        The worker reads that as an instruction. It must be an instruction not to guess,
        and the server must accept the result.
        """
        r = make_repo(self.td)
        self.init(r)
        body = self.read(r, "QWEN.md")
        self.assertNotIn("<EDIT ME", body)
        self.assertNotIn("<-- EDIT", body)
        self.assertIn("Do NOT guess", body)
        self.assertEqual(server.worker_rules_status(r)[0], "ok",
                         "a freshly initialised project must satisfy the server's check")

    def test_given_test_command_is_written(self):
        r = make_repo(self.td)
        self.init(r, "--test-cmd", "make check")
        body = self.read(r, "QWEN.md")
        self.assertIn("`make check`", body)
        self.assertNotIn("<EDIT ME", body)
        self.assertEqual(server.worker_rules_status(r)[0], "ok")

    def test_template_banner_is_stripped(self):
        """The banner tells a human to copy and edit the file. Qwen would read it too."""
        r = make_repo(self.td)
        self.init(r, "--test-cmd", "make check")
        self.assertNotIn("TEMPLATE", self.read(r, "QWEN.md"))

    def test_detects_a_python_test_command(self):
        r = make_repo(self.td)
        open(os.path.join(r, "pyproject.toml"), "w").close()
        out = self.init(r).stdout
        self.assertIn("detected", out)
        self.assertIn("pytest", self.read(r, "QWEN.md"))

    # --- CLAUDE.md: never clobber, never duplicate ---

    def test_creates_claude_md_with_the_policy_block(self):
        r = make_repo(self.td)
        self.init(r, "--claude-md")
        body = self.read(r, "CLAUDE.md")
        self.assertIn("## Delegating mechanical work", body)
        self.assertIn("qwen-delegate:begin", body)
        self.assertIn("qwen-delegate:end", body)

    def test_appends_without_touching_existing_content(self):
        r = make_repo(self.td)
        original = "# My Project\n\nRules I care about.\n\n## Style\n\nTabs.\n"
        with open(os.path.join(r, "CLAUDE.md"), "w") as f:
            f.write(original)
        self.init(r, "--claude-md")
        body = self.read(r, "CLAUDE.md")
        self.assertTrue(body.startswith(original),
                        "the user's CLAUDE.md must survive byte-for-byte at the head")
        self.assertIn("## Delegating mechanical work", body)

    def test_rerun_does_not_duplicate_the_block(self):
        r = make_repo(self.td)
        self.init(r, "--claude-md")
        first = self.read(r, "CLAUDE.md")
        self.init(r, "--claude-md")
        again = self.read(r, "CLAUDE.md")
        self.assertEqual(first, again, "a second run must be a no-op")
        self.assertEqual(again.count("## Delegating mechanical work"), 1)

    def test_edited_managed_block_is_still_recognised(self):
        """
        The marker, not the heading, is the block's identity. A user who rewords the
        heading inside the managed block must not get a second copy appended on the next
        run -- the heading check alone would not see it.
        """
        r = make_repo(self.td)
        self.init(r, "--claude-md")
        edited = self.read(r, "CLAUDE.md").replace(
            "## Delegating mechanical work", "## How we delegate around here")
        with open(os.path.join(r, "CLAUDE.md"), "w") as f:
            f.write(edited)
        self.init(r, "--claude-md")
        self.assertEqual(self.read(r, "CLAUDE.md"), edited,
                         "the marker must hold even when the block's heading was edited")

    def test_hand_pasted_block_is_recognised(self):
        """Someone who pasted the snippet themselves has no markers. Do not re-append."""
        r = make_repo(self.td)
        pasted = "# P\n\n## Delegating mechanical work\n\nI edited this myself.\n"
        with open(os.path.join(r, "CLAUDE.md"), "w") as f:
            f.write(pasted)
        self.init(r, "--claude-md")
        body = self.read(r, "CLAUDE.md")
        self.assertEqual(body, pasted, "an edited hand-pasted block must not be touched")

    def test_claude_md_untouched_without_the_flag(self):
        """Non-interactive default: modifying a user's CLAUDE.md is opt-in."""
        r = make_repo(self.td)
        self.init(r)
        self.assertFalse(os.path.exists(os.path.join(r, "CLAUDE.md")))

    # --- preconditions and re-run safety ---

    def test_refuses_a_non_git_project(self):
        plain = os.path.join(self.td, "plain")
        os.makedirs(plain)
        p = self.init(plain, expect_ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not a git repository", p.stderr)
        self.assertFalse(os.path.exists(os.path.join(plain, "QWEN.md")))

    def test_existing_qwen_md_is_not_overwritten(self):
        r = make_repo(self.td)
        write_rules(r, "my own hand-written rules\n")
        self.init(r)
        self.assertEqual(self.read(r, "QWEN.md"), "my own hand-written rules\n")

    def test_legacy_placeholder_is_reported(self):
        """A project set up by the old script is still broken; say so loudly."""
        r = make_repo(self.td)
        write_rules(r, "- Run tests with: `<EDIT ME: your test command>`\n")
        out = self.init(r).stdout
        self.assertIn("placeholder", out.lower())

    def test_force_regenerates_and_backs_up(self):
        r = make_repo(self.td)
        write_rules(r, "- Run tests with: `<EDIT ME: your test command>`\n")
        self.init(r, "--force", "--test-cmd", "make check")
        self.assertIn("<EDIT ME", self.read(r, "QWEN.md.bak"))
        self.assertNotIn("<EDIT ME", self.read(r, "QWEN.md"))
        self.assertEqual(server.worker_rules_status(r)[0], "ok")

    def test_registry_override_is_honoured(self):
        """
        The script hardcoded ~/.qwen-delegate/projects.jsonl while the server honoured
        QWEN_DELEGATE_REGISTRY. They must agree, or a relocated index silently misses
        every project set up by the script.
        """
        r = make_repo(self.td)
        self.init(r)
        reg = os.path.join(self.td, "registry.jsonl")
        self.assertTrue(os.path.isfile(reg))
        self.assertIn(r, self.read(reg))


if __name__ == "__main__":
    unittest.main(verbosity=2)
