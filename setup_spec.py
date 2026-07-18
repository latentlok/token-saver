#!/usr/bin/env python3
"""
Spec for the first-use preconditions: is this project actually configured to delegate?

Claude-authored gate (never delegate this file -- it defines what correct means).

The failure this exists to prevent is SILENT. Without QWEN.md the worker's standing rules
are simply not loaded, and measured, it then edits protected spec files, games gates, and
expands scope -- while the run still reports success. So the load-bearing cases, in order
of what would hurt most if broken:

  1. qwen_delegate must never run an unconfigured project degraded. In a git repo it
     self-configures -- writes QWEN.md (detecting the test command, or instructing the
     worker not to guess one; NEVER a placeholder) and proceeds. In a non-git repo it
     REFUSES and does not invoke Qwen: there is no rollback, so it cannot self-configure
     safely.
  2. A QWEN.md carrying an unreplaced placeholder counts as unconfigured -- the worker
     reads the placeholder as an instruction -- so it is regenerated, not run as-is.
  3. The repo boundary must hold in both directions -- a subdirectory of a configured
     repo IS configured (Qwen loads context hierarchically), a QWEN.md above the repo
     top level is NOT this project's rules.
  4. qwen_query must NOT refuse. It cannot write, so refusing is gratuitous friction on
     the cheapest way to try the system; it warns instead.

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


class DelegateSelfConfigures(Base):
    """Drive the real run_qwen with a stubbed Qwen. New contract: a git repo with no rules
    is self-configured (QWEN.md written) and PROCEEDS; only a non-git repo is refused,
    because it cannot roll back and so cannot be configured safely."""

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

    def test_git_repo_without_rules_is_bootstrapped_and_runs(self):
        r = make_repo(self.td)
        out, calls = self.drive(r)
        self.assertFalse(out.startswith("STATUS: error"), out[:200])
        self.assertEqual(calls, [r], "after self-configuring, the worker must run")
        self.assertTrue(os.path.isfile(os.path.join(r, "QWEN.md")),
                        "a git repo must be configured, not refused")
        self.assertIn("SETUP:", out, "the caller must be told it was configured")

    def test_bootstrapped_rules_carry_no_placeholder(self):
        """The point of the whole change: self-configuration must NOT reintroduce the
        placeholder the refusal existed to prevent."""
        r = make_repo(self.td)
        self.drive(r)
        text = open(os.path.join(r, "QWEN.md")).read()
        for marker in server.RULES_PLACEHOLDERS:
            self.assertNotIn(marker, text)
        self.assertEqual(server.worker_rules_status(r)[0], "ok")

    def test_placeholder_rules_are_regenerated_not_run_asis(self):
        r = make_repo(self.td)
        write_rules(r, "- Run tests with: `<EDIT ME: your test command>`\n")
        out, calls = self.drive(r)
        self.assertEqual(calls, [r])
        self.assertEqual(server.worker_rules_status(r)[0], "ok",
                         "a placeholder file must be regenerated before the worker runs")

    def test_configured_project_runs_without_a_setup_note(self):
        """An already-configured project must NOT be re-bootstrapped or nagged."""
        r = make_repo(self.td)
        write_rules(r)
        out, calls = self.drive(r)
        self.assertEqual(calls, [r], "a configured project must reach the worker")
        self.assertNotIn("SETUP:", out)

    def test_bootstrap_failure_refuses_and_never_runs_qwen(self):
        """If QWEN.md cannot be written (IO error, template drift), the run must refuse
        cleanly -- not crash, and not proceed unconfigured. Regression: the failure branch
        once called a function that no longer existed (NameError) because no test drove it."""
        r = make_repo(self.td)
        real = server.bootstrap_worker_rules
        server.bootstrap_worker_rules = lambda c: (None, None)
        try:
            out, calls = self.drive(r)
        finally:
            server.bootstrap_worker_rules = real
        self.assertTrue(out.startswith("STATUS: error"), out[:200])
        self.assertEqual(calls, [], "a failed bootstrap must not reach the worker")
        self.assertIn("by hand", out, "the refusal must name a manual fix")

    def test_non_git_project_is_refused_and_never_runs_qwen(self):
        d = os.path.join(self.td, "plain")
        os.makedirs(d)
        out, calls = self.drive(d)
        self.assertTrue(out.startswith("STATUS: error"), out[:200])
        self.assertEqual(calls, [], "a non-git repo must be refused BEFORE the worker runs")
        self.assertFalse(os.path.isfile(os.path.join(d, "QWEN.md")),
                         "must not write rules into a dir it cannot roll back")

    def test_non_git_refusal_says_git_init_and_why(self):
        d = os.path.join(self.td, "plain")
        os.makedirs(d)
        out, _ = self.drive(d)
        self.assertIn("git init", out)
        low = out.lower()
        self.assertTrue(any(k in low for k in ("rollback", "sandbox")),
                        "must say WHY git is required, not just 'run this'")


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
