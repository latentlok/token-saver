#!/usr/bin/env python3
"""
Spec for the v0.6 friction-ledger fixes (A0a/A0c/A11, A0b, A12, A16).

Claude-authored gate (never delegate this file -- it defines what correct means).

Each case here failed on v0.5.1 and was measured in the field before it was a
test. The four are unrelated in code and identical in shape: the plugin knew
the right answer and did not act on it.

  1. TEARDOWN (A0a/A0c/A11) -- a timed-out gate killed only the direct child.
     `uv run pytest` left `uv` and `pytest` running under init, invisible to
     the receipt, one per refused run, competing for CPU with the very timing
     used to set the timeout. Fixed by start_new_session + killpg.
  2. ENDPOINT INHERITANCE (A0b) -- profile resolution Level 4 hardcoded
     `endpoints=None`, so declaring capacity for the builtin profile parsed,
     validated, and did nothing.
  3. BATCH INHERITANCE (A12) -- batch items that omitted `cwd` (a REQUIRED
     top-level parameter every caller passes at the call) came back as a bare
     `KeyError('cwd')` for the whole receipt.
  4. TEST DODGE (A16) -- the detector substring-matched, so `skipif` (the
     opposite mark, and the standard repair) and string literals both fired.
     4 false positives in one run, on the one line the skill says always to
     read on a green receipt.

Run:  python3 specs/teardown_spec.py
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import doctor, engine, gittree, limits, profiles, server, verdict  # noqa: E402


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class GateTeardown(unittest.TestCase):
    """A gate that times out leaves NOTHING running.

    The shape is the one measured in the field: shell -> wrapper -> long
    child. A `subprocess.run(timeout=)` reaps the shell and orphans the rest,
    which is why this asserts on the GRANDCHILD, not the return value.
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _gate(self, body):
        p = os.path.join(self.d, "gate.sh")
        with open(p, "w") as f:
            f.write("#!/bin/bash\n" + body + "\n")
        os.chmod(p, 0o755)
        return f"bash {p}"

    def test_timed_out_gate_kills_its_whole_process_tree(self):
        # The marker file lets us find the grandchild without pgrep pattern
        # collisions from anything else on the box.
        marker = os.path.join(self.d, "pid")
        cmd = self._gate(f"bash -c 'echo $$ > {marker}; sleep 60'")
        passed, out, ms, timed_out = engine._run_verify_timed(cmd, self.d, 2)

        self.assertTrue(timed_out)
        self.assertFalse(passed)
        self.assertIn("timed out after 2s", out)

        with open(marker) as f:
            grandchild = int(f.read().strip())
        deadline = time.monotonic() + 10
        while alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertFalse(
            alive(grandchild),
            f"pid {grandchild} survived the timeout -- the gate leaked its "
            f"process tree, which is the defect this spec exists for")

    def test_a_gate_that_finishes_is_unaffected(self):
        # The teardown must not change the ordinary path: exit code and
        # output still decide, and both pipes are still drained.
        passed, out, ms, timed_out = engine._run_verify_timed(
            self._gate("echo hello; echo problem >&2; exit 0"), self.d, 30)
        self.assertTrue(passed)
        self.assertFalse(timed_out)
        self.assertIn("hello", out)
        self.assertIn("problem", out)      # stderr is captured too

    def test_a_failing_gate_still_reports_failure_not_timeout(self):
        passed, out, ms, timed_out = engine._run_verify_timed(
            self._gate("echo boom; exit 1"), self.d, 30)
        self.assertFalse(passed)
        self.assertFalse(timed_out)        # NOT conflated with a timeout
        self.assertIn("boom", out)


class EndpointsAtLevel4(unittest.TestCase):
    """Capacity declared for the builtin profile is honoured without also
    naming `"default": "qwen-local"` -- the undocumented incantation."""

    def setUp(self):
        self._env = dict(os.environ)
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def _machine_file(self, body):
        p = os.path.join(self.d, "executors.json")
        with open(p, "w") as f:
            f.write(body)
        os.environ["QWEN_DELEGATE_EXECUTORS"] = p

    def test_endpoints_only_file_is_honoured(self):
        self._machine_file('{"endpoints": {"local": {"parallel_max": 4}}}')
        prof = profiles.resolve(self.d, None)
        self.assertEqual(prof["endpoint_cfg"]["name"], "local")
        self.assertEqual(prof["endpoint_cfg"]["parallel_max"], 4)
        self.assertEqual(prof["dispatch"], "parallel")

    def test_no_machine_file_still_defaults_to_one_slot(self):
        os.environ["QWEN_DELEGATE_EXECUTORS"] = os.path.join(self.d, "nope")
        prof = profiles.resolve(self.d, None)
        self.assertEqual(prof["endpoint_cfg"]["parallel_max"], 1)
        self.assertEqual(prof["dispatch"], "serial")


class BatchInheritance(unittest.TestCase):
    """Items inherit the call's run-level fields; an item's own value wins."""

    def test_items_inherit_cwd_from_the_call(self):
        args = {"cwd": "/repo", "batch": [{"task": "a"}, {"task": "b"}]}
        items = server._inherit(args, args["batch"])
        self.assertEqual([i["cwd"] for i in items], ["/repo", "/repo"])

    def test_an_items_own_value_always_wins(self):
        args = {"cwd": "/repo", "trust": "self",
                "batch": [{"task": "a", "cwd": "/other", "trust": "verified"}]}
        items = server._inherit(args, args["batch"])
        self.assertEqual(items[0]["cwd"], "/other")
        self.assertEqual(items[0]["trust"], "verified")

    def test_inheritance_does_not_invent_keys_the_call_never_set(self):
        # Absent stays absent: a key materialising as None would look like an
        # explicit "off" to every reader downstream.
        items = server._inherit({"cwd": "/repo"}, [{"task": "a"}])
        self.assertNotIn("trust", items[0])
        self.assertNotIn("worktree", items[0])

    def test_non_dict_items_pass_through_untouched(self):
        # Shape validation is somebody else's job; this must not raise.
        self.assertEqual(server._inherit({"cwd": "/r"}, ["junk", None]),
                         ["junk", None])

    def test_the_call_is_not_mutated(self):
        args = {"cwd": "/repo", "batch": [{"task": "a"}]}
        server._inherit(args, args["batch"])
        self.assertNotIn("cwd", args["batch"][0])


class TestDodgeDetector(unittest.TestCase):
    """The mark, not the substring -- and code, not string literals."""

    def hits(self, line):
        return bool(gittree._DODGE.search(gittree._code_only(line)))

    def test_real_dodges_still_fire(self):
        for line in ('@pytest.mark.skip(reason="broken")',
                     '@unittest.skip("wip")',
                     '@pytest.mark.xfail',
                     '    @skip',
                     '@unittest.expectedFailure'):
            self.assertTrue(self.hits(line), line)

    def test_skipif_is_not_a_dodge(self):
        # skipif is the REPAIR -- converting a hard skip to a conditional one
        # is what a correct fix looks like. Flagging it trains the reader to
        # ignore the line, and then it is ignored on the run where it is right.
        for line in ('@pytest.mark.skipif(os.environ.get("X") != "1", reason="live")',
                     '@pytest.mark.xfailif(cond)',
                     '    @pytest.mark.skipif(sys.platform == "win32")'):
            self.assertFalse(self.hits(line), line)

    def test_string_literals_are_not_code(self):
        # This exact line -- a guard assertion PROVING no skip was added --
        # was one of the four false positives.
        for line in ('assert text.count("@pytest.mark.skip(") == 0',
                     "banned = '@pytest.mark.skip'",
                     '# adds @pytest.mark.skip somewhere'):
            self.assertFalse(self.hits(line), line)

    def test_a_dodge_hiding_after_a_string_still_fires(self):
        # Blanking strings must not blank the code around them.
        self.assertTrue(self.hits('x = "note"; @pytest.mark.skip'))


class HeartbeatIdentity(unittest.TestCase):
    """A7b/A17: the sidecar must name the run it describes.

    The documented "is it hung?" check read a PREVIOUS run's `"state": "done"`
    and reported success for work that had not started. `session` was null for
    the whole live window (a cold run learns it from the first reply), so
    during the only period the file is the sole signal it carried no
    identifier at all -- and with several servers sharing one
    `.qwen-delegate/`, another run's heartbeat is indistinguishable from yours
    by construction.
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def read(self):
        import json
        with open(os.path.join(self.d, ".qwen-delegate", "progress.json")) as f:
            return json.load(f)

    def test_the_run_id_is_stamped_before_the_first_token(self):
        p = limits.Progress(self.d, session_id=None, run_id="r123")
        p._write()
        snap = self.read()
        self.assertEqual(snap["run"], "r123")
        self.assertEqual(snap["state"], "starting")
        self.assertEqual(snap["records"], 0)

    def test_a_consumer_can_assert_the_file_is_theirs(self):
        limits.Progress(self.d, run_id="mine")._write()
        self.assertEqual(limits.read_progress(self.d)["run"], "mine")
        limits.Progress(self.d, run_id="someone-else")._write()
        self.assertNotEqual(limits.read_progress(self.d)["run"], "mine")

    def test_the_first_record_moves_it_out_of_starting(self):
        p = limits.Progress(self.d, run_id="r1")
        p._write()
        self.assertEqual(self.read()["state"], "starting")
        p({"type": "assistant"})
        self.assertEqual(self.read()["state"], "running")

    def test_finish_still_marks_it_done(self):
        p = limits.Progress(self.d, run_id="r1")
        p({"type": "assistant"})
        p.finish()
        snap = self.read()
        self.assertEqual(snap["state"], "done")
        self.assertEqual(snap["run"], "r1")   # identity survives to the end


class DenialClassification(unittest.TestCase):
    """A15: a denied READ must not invalidate the verdict.

    ~150 denials across twelve builds, every one a well-formed read-only
    search or a test command carrying `2>&1` -- and the receipt said "treat
    the result as suspect" for all of them. That is the plugin undermining its
    own verdict for reasons unrelated to the worker, and the trained response
    is to stop believing the line.
    """

    def test_read_only_tools_are_known_to_the_renderer(self):
        for name in ("grep_search", "read_file", "glob", "list_directory"):
            self.assertIn(name, verdict.READ_ONLY_TOOLS)

    def test_effect_shaped_tools_are_not(self):
        for name in ("run_shell_command", "write_file", "replace", "edit"):
            self.assertNotIn(name, verdict.READ_ONLY_TOOLS)


class ProjectDoctor(unittest.TestCase):
    """Static config traps become findings instead of wasted runs."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", self.d], check=True)

    def cfg(self, body):
        import json
        with open(os.path.join(self.d, ".qwen-delegate.json"), "w") as f:
            json.dump(body, f)

    def ids(self):
        return {f["id"] for f in doctor.project_check(self.d)}

    def test_a_test_command_that_cannot_reach_the_specs_is_flagged(self):
        # A9: the protected-spec convention and the gate convention were
        # silently disconnected. A trust="self" run would grade against a
        # suite that cannot fail for the reason the delegation existed.
        self.cfg({"test_command": "uv run pytest unit_tests -q",
                  "spec_globs": ["specs/*"]})
        self.assertIn("gate-misses-specs", self.ids())

    def test_a_test_command_that_names_the_specs_is_silent(self):
        self.cfg({"test_command": "uv run pytest unit_tests specs -q",
                  "spec_globs": ["specs/*"]})
        self.assertNotIn("gate-misses-specs", self.ids())

    def test_no_test_command_accuses_nobody(self):
        # Nothing to check: the server falls back to detection, and inventing
        # a finding here would be the false-accusation class this project has
        # spent a phase removing.
        self.cfg({"spec_globs": ["specs/*"]})
        self.assertNotIn("gate-misses-specs", self.ids())

    def test_the_server_count_does_not_depend_on_who_is_asking(self):
        # It did: a loose pattern matched the SHELL running the check, so the
        # answer changed with the invocation. A detector whose result depends
        # on the observer is worse than no detector.
        n1, _ = doctor._server_count()
        n2, _ = doctor._server_count()
        self.assertEqual(n1, n2)
        self.assertIsInstance(n1, int)
        self.assertGreaterEqual(n1, 0)

    def test_project_check_never_raises(self):
        # doctor is what a confused caller reaches for; a fault in one check
        # must not deny them the others.
        self.assertIsInstance(doctor.project_check("/nonexistent/path"), list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
