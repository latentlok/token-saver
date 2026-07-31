#!/usr/bin/env python3
"""
Spec for scoped_hook.py -- the PreToolUse gate (HLD C10, LLD "scoped_hook.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

The hook is the only thing between a yolo-mode worker and the machine, and it
shipped without a spec. Load-bearing claims, worst-first:

  1. Writes are confined to the project cwd, and every ALLOWED write appends its
     RESOLVED path to QGATE_WRITELOG (C10). That log is the only evidence the
     engine has for telling a worker edit from a concurrent caller's; without it
     the guards revert whatever moved, which destroyed real caller work.
  2. An unknown tool is no longer waved through as "read-only". One carrying
     file_path/path/command/content is DENIED -- otherwise a single unrecognised
     tool name is a hole straight around the write_file check. The executor's own
     read tools that legitimately take `path` are named, because a gate that
     blocks file search is one that gets switched off entirely.
  3. Shell: the manager's exact verify command and the read-only allowlist pass
     and are logged to QGATE_ALLOWLOG; everything else is denied BY NAME, so the
     receipt can ask the manager to adjudicate.
  4. QGATE_MODE="autoedit" (U1.4, observed auto-edit) keeps the write policy and
     denies shell outright -- no manager-approved allowlist reaches that mode.
  5. Fail closed: a malformed payload denies. Every log write is best-effort --
     a logging failure must never fail the tool call it only observes.

Public surface pinned here:
    scoped_hook.decide(tool_name, tool_input) -> (allow: bool, reason: str)
    scoped_hook.main()   -- stdin JSON in, PreToolUse hookSpecificOutput out
    env (read at import): QGATE_CWD, QGATE_VERIFY, QGATE_EXTRA, QGATE_MODE,
                          QGATE_DENYLOG, QGATE_WRITELOG, QGATE_ALLOWLOG

Run:  python3 specs/scoped_hook_spec.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import scoped_hook  # noqa: E402

HOOK = os.path.join(ROOT, "scoped_hook.py")


class Fixture(unittest.TestCase):
    def setUp(self):
        # realpath: the hook resolves every write path, and on a platform where
        # the temp root is a symlink an unresolved cwd matches nothing.
        self.cwd = os.path.realpath(tempfile.mkdtemp())
        logs = tempfile.mkdtemp()
        self.deny = os.path.join(logs, "denied.log")
        self.write = os.path.join(logs, "writes.log")
        self.allow = os.path.join(logs, "allowed.log")
        self._saved = {k: getattr(scoped_hook, k) for k in
                       ("CWD", "VERIFY", "DENYLOG", "WRITELOG", "ALLOWLOG",
                        "MODE", "EXTRA")}

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(scoped_hook, k, v)

    def configure(self, **over):
        """Point the IMPORTED module at this test's cwd and logs. The module
        reads its env at import time (one process per tool call in production),
        which is why the end-to-end cases below re-exec it instead."""
        vals = {"CWD": self.cwd, "VERIFY": "", "DENYLOG": self.deny,
                "WRITELOG": self.write, "ALLOWLOG": self.allow,
                "MODE": "scoped", "EXTRA": []}
        vals.update(over)
        for k, v in vals.items():
            setattr(scoped_hook, k, v)

    def fire(self, tool, tool_input, **env_over):
        """Run the hook the way the executor does: a fresh process, configured
        purely by env, one JSON payload on stdin."""
        env = dict(os.environ, QGATE_CWD=self.cwd, QGATE_DENYLOG=self.deny,
                   QGATE_WRITELOG=self.write, QGATE_ALLOWLOG=self.allow)
        env.update({k: str(v) for k, v in env_over.items()})
        p = subprocess.run(
            [sys.executable, HOOK], env=env, capture_output=True, text=True,
            input=json.dumps({"tool_name": tool, "tool_input": tool_input}))
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        return out["permissionDecision"], out["permissionDecisionReason"]

    def lines(self, path):
        if not os.path.isfile(path):
            return []
        with open(path) as f:
            return [ln for ln in f.read().splitlines() if ln]


class Writes(Fixture):
    def test_write_inside_cwd_is_allowed_and_write_logged(self):
        target = os.path.join(self.cwd, "pkg", "mod.py")
        decision, why = self.fire("write_file",
                                  {"file_path": target, "content": "x = 1\n"})
        self.assertEqual(decision, "allow")
        self.assertEqual(why, "write inside project")
        self.assertEqual(self.lines(self.write), [target])
        self.assertEqual(self.lines(self.deny), [])

    def test_write_outside_cwd_is_denied_and_deny_logged(self):
        outside = os.path.join(os.path.realpath(tempfile.mkdtemp()), "steal.py")
        decision, why = self.fire("write_file",
                                  {"file_path": outside, "content": "x"})
        self.assertEqual(decision, "deny")
        self.assertEqual(why, "write outside the project")
        self.assertIn("write outside the project", "\n".join(self.lines(self.deny)))
        self.assertEqual(self.lines(self.write), [])   # nothing to attribute

    def test_the_logged_path_is_resolved_not_as_written(self):
        # The engine matches these strings against repo-relative paths, so a
        # `./x/../mod.py` spelling that logged verbatim would attribute nothing.
        self.configure()
        ok, _ = scoped_hook.decide(
            "edit", {"file_path": os.path.join(self.cwd, "sub", "..", "mod.py")})
        self.assertTrue(ok)
        self.assertEqual(self.lines(self.write),
                         [os.path.join(self.cwd, "mod.py")])

    def test_edit_and_replace_take_the_same_path(self):
        self.configure()
        for tool in ("edit", "replace"):
            ok, _ = scoped_hook.decide(tool,
                                       {"file_path": os.path.join(self.cwd, "m.py")})
            self.assertTrue(ok, tool)
        self.assertEqual(len(self.lines(self.write)), 2)

    def test_no_cwd_configured_denies_every_write(self):
        # Fail closed: an unset QGATE_CWD must not read as "anywhere is fine".
        decision, _ = self.fire("write_file",
                                {"file_path": os.path.join(self.cwd, "m.py")},
                                QGATE_CWD="")
        self.assertEqual(decision, "deny")


class Shell(Fixture):
    def test_exact_verify_command_is_allowed_and_allow_logged(self):
        # The manager wrote it, so it passes even though it is compound -- the
        # allowlist below would reject the same string.
        verify = "python3 -m unittest discover && echo ok"
        decision, why = self.fire("run_shell_command", {"command": verify},
                                  QGATE_VERIFY=verify)
        self.assertEqual(decision, "allow")
        self.assertEqual(why, "exact verify command")
        self.assertEqual(self.lines(self.allow),
                         [f"run_shell_command: {verify}"])

    def test_graphify_query_is_allowed_and_allow_logged(self):
        # The GRAPH accountability count reads exactly this line shape.
        cmd = 'graphify query "callers of render"'
        decision, _ = self.fire("run_shell_command", {"command": cmd})
        self.assertEqual(decision, "allow")
        self.assertEqual(self.lines(self.allow), [f"run_shell_command: {cmd}"])

    def test_off_allowlist_command_is_denied_by_name(self):
        decision, why = self.fire("run_shell_command", {"command": "make deploy"})
        self.assertEqual(decision, "deny")
        self.assertEqual(why, "not on the shell allowlist")
        self.assertIn("(not on the shell allowlist)",
                      "\n".join(self.lines(self.deny)))
        self.assertEqual(self.lines(self.allow), [])

    def test_state_changing_command_stays_denied_and_unlogged(self):
        for cmd in ("rm -rf build", "git commit -am wip", "curl http://x"):
            decision, _ = self.fire("run_shell_command", {"command": cmd})
            self.assertEqual(decision, "deny", cmd)
        self.assertEqual(self.lines(self.allow), [])


class UnknownTools(Fixture):
    def test_effect_shaped_input_is_denied(self):
        for ti in ({"file_path": "/etc/hosts"}, {"path": "/etc"},
                   {"command": "ls"}, {"content": "payload"}):
            decision, why = self.fire("deploy_thing", ti)
            self.assertEqual(decision, "deny", ti)
            self.assertEqual(why, "unknown tool carrying an effect-shaped input")
        self.assertEqual(self.lines(self.allow), [])

    def test_metadata_only_tool_is_allowed_and_recorded_as_ungated(self):
        # Default-allow survives (denying every unknown tool breaks the web
        # research path) but it is no longer silent.
        decision, why = self.fire("firecrawl_scrape",
                                  {"url": "https://x", "formats": ["markdown"]})
        self.assertEqual(decision, "allow")
        self.assertIn("default-allow", why)
        self.assertEqual(self.lines(self.allow), ["ungated:firecrawl_scrape"])

    def test_read_tools_keep_their_path_argument(self):
        # glob / grep / list_directory take `path` as a SEARCH ROOT. Matching on
        # shape alone would deny file search outright.
        for tool in ("glob", "grep", "search_file_content", "list_directory",
                     "read_file", "read_many_files"):
            decision, why = self.fire(tool, {"path": self.cwd, "pattern": "*.py"})
            self.assertEqual(decision, "allow", tool)
            self.assertEqual(why, "read-only tool", tool)
        self.assertEqual(self.lines(self.allow), [])   # reads are not run noise


class MCPTools(Fixture):
    """mcp__* names in scoped mode: gated by NAME, deny-by-default.

    Proven live (C1, 2026-07-31): a scoped worker called
    mcp__firecrawl__firecrawl_scrape -- input {url}, no effect-shaped key, so
    the shape policy allowed a live network fetch the manager never approved.
    An MCP tool is arbitrary capability behind whatever input shape its server
    chose; only its name says anything, so scoped mode treats it like shell."""

    def test_unlisted_mcp_tool_is_denied_by_name(self):
        decision, why = self.fire("mcp__firecrawl__firecrawl_scrape",
                                  {"url": "https://x"})
        self.assertEqual(decision, "deny")
        self.assertEqual(why, "MCP tool not on the mcp allowlist")
        self.assertEqual(self.lines(self.allow), [])
        self.assertTrue(any("mcp__firecrawl__firecrawl_scrape" in ln
                            for ln in self.lines(self.deny)))

    def test_allowlisted_mcp_tool_passes_and_is_logged(self):
        decision, why = self.fire(
            "mcp__firecrawl__firecrawl_scrape", {"url": "https://x"},
            QGATE_MCP=json.dumps([r"^mcp__firecrawl__"]))
        self.assertEqual(decision, "allow")
        self.assertEqual(why, "MCP tool on the mcp allowlist")
        self.assertEqual(self.lines(self.allow),
                         ["mcp:mcp__firecrawl__firecrawl_scrape"])

    def test_allowlist_is_a_name_match_not_a_prefix_grant(self):
        decision, _ = self.fire(
            "mcp__othertool__do_thing", {"url": "https://x"},
            QGATE_MCP=json.dumps([r"^mcp__firecrawl__"]))
        self.assertEqual(decision, "deny")

    def test_autoedit_mode_keeps_recording_instead_of_gating(self):
        # Observed auto-edit exists to attribute, not to gate: byte-identical
        # to the pre-fence behavior -- metadata-only MCP calls stay
        # ungated-logged, effect-shaped ones stay shape-denied.
        decision, _ = self.fire("mcp__firecrawl__firecrawl_scrape",
                                {"url": "https://x"}, QGATE_MODE="autoedit")
        self.assertEqual(decision, "allow")
        self.assertEqual(self.lines(self.allow),
                         ["ungated:mcp__firecrawl__firecrawl_scrape"])
        decision, _ = self.fire("mcp__x__writer", {"file_path": "/etc/hosts"},
                                QGATE_MODE="autoedit")
        self.assertEqual(decision, "deny")


class ObservedAutoEdit(Fixture):
    """QGATE_MODE=autoedit: the hook is installed for ATTRIBUTION only, so there
    is no manager-approved shell allowlist behind it."""

    def test_shell_is_denied_by_name(self):
        decision, why = self.fire("run_shell_command", {"command": "ls"},
                                  QGATE_MODE="autoedit")
        self.assertEqual(decision, "deny")
        self.assertEqual(
            why,
            "shell is denied under observed auto-edit -- use write/edit tools "
            "or re-delegate with approval_mode=scoped + shell_allow")
        self.assertIn("observed auto-edit", "\n".join(self.lines(self.deny)))

    def test_even_the_verify_command_is_denied(self):
        # Nothing in this mode was adjudicated by a manager; the engine runs the
        # gate itself, so the worker never needs it.
        decision, _ = self.fire("run_shell_command", {"command": "make test"},
                                QGATE_MODE="autoedit", QGATE_VERIFY="make test")
        self.assertEqual(decision, "deny")

    def test_writes_are_confined_and_logged_exactly_as_scoped(self):
        target = os.path.join(self.cwd, "mod.py")
        decision, _ = self.fire("write_file", {"file_path": target},
                                QGATE_MODE="autoedit")
        self.assertEqual(decision, "allow")
        self.assertEqual(self.lines(self.write), [target])
        outside = os.path.join(os.path.realpath(tempfile.mkdtemp()), "x.py")
        decision, _ = self.fire("write_file", {"file_path": outside},
                                QGATE_MODE="autoedit")
        self.assertEqual(decision, "deny")


class FailClosed(Fixture):
    def test_malformed_payload_denies(self):
        env = dict(os.environ, QGATE_CWD=self.cwd, QGATE_DENYLOG=self.deny)
        p = subprocess.run([sys.executable, HOOK], input="not json at all",
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0)         # a crash would be UNGATED
        out = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("hook error", out["permissionDecisionReason"])

    def test_an_unwritable_log_never_fails_the_call(self):
        # The logs observe the run; they must not be able to end it.
        self.configure(WRITELOG=os.path.join(self.cwd, "no", "such", "dir.log"))
        ok, _ = scoped_hook.decide("write_file",
                                   {"file_path": os.path.join(self.cwd, "m.py")})
        self.assertTrue(ok)

    def test_logs_unset_is_not_an_error(self):
        self.configure(WRITELOG="", ALLOWLOG="", DENYLOG="")
        self.assertTrue(scoped_hook.decide(
            "write_file", {"file_path": os.path.join(self.cwd, "m.py")})[0])
        self.assertFalse(scoped_hook.decide(
            "run_shell_command", {"command": "make x"})[0])


if __name__ == "__main__":
    unittest.main(verbosity=1)
