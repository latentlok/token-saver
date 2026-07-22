#!/usr/bin/env python3
"""
Spec for qd/engine.py -- the delegation loop (LLD "qd/engine.py", HLD §4/C3/C8).

Claude-authored gate (never delegate this file -- it defines what correct means).

Pins the loop's OBSERVABLE behavior through a scenario stub executor injected
via the real profile chain. Load-bearing:

  1. The iterate loop feeds the gate's REAL error output back to the worker on
     retry -- convergence on free tokens is the whole mechanism.
  2. Pre-flight: a gate that was already green proves nothing (ctx flags it).
     Identical gate output before/after attempt 1 => gate_suspect, bail -- a
     broken gate must not doom-loop the worker (measured failure mode).
  3. Spec guard: worker edits to protected files are auto-reverted and the
     attempt fails -- including the crane (server.py is in spec_globs now).
  4. C8 prefilter is ADVISORY: gate green + self-tests red => success + NOTES;
     gate red => prefilter output joins the feedback; no test command or no
     *_qwen.* files => skipped silently. It never affects status.
  5. C3: the result exposes ctx with the v2 keys, trust stubbed to "verified",
     and pre_sha as the FULL 40-char sha (gittree contract).
  6. Refusals never run the worker: non-verified trust, dirty protected spec,
     non-git cwd.

Public surface pinned here:
    qd.engine.delegate(args) -> {"status", "session_id", "trail",
        "result_text", "denials", "max_iter", "last_verify", "ctx"}
    qd.engine.run(args) -> str   (delegate + qd.verdict.render)

Run:  python3 specs/engine_spec.py
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import engine  # noqa: E402

# The scenario stub: each invocation pops the next step from steps.json, writes
# the files that step names, records the task text it received, and prints a
# qwen-shaped result JSON.
STUB = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
sdir = os.environ["STUB_DIR"]
steps = json.load(open(os.path.join(sdir, "steps.json")))
n_path = os.path.join(sdir, "attempt")
n = int(open(n_path).read()) if os.path.exists(n_path) else 0
open(n_path, "w").write(str(n + 1))
step = steps[min(n, len(steps) - 1)]
task = sys.argv[2]
open(os.path.join(sdir, "task_%d.txt" % (n + 1)), "w").write(task)
for rel, content in (step.get("write") or {}).items():
    p = os.path.join(os.getcwd(), rel)
    os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(rel) else None
    open(p, "w").write(content)
# Simulate a concurrent fan-out sibling: move the MAIN tree's HEAD with a
# change that will conflict with this worktree's edit, so classify_merge has
# something real to detect (within one delegate the main HEAD is otherwise
# static and every branch is trivially clean).
if step.get("main_diverge"):
    mr = os.environ["MAIN_REPO"]
    open(os.path.join(mr, "other.py"), "w").write("MAIN_SIDE = 1\n")
    subprocess.run(["git", "-C", mr, "commit", "-qam", "main diverged"])
result = {"type": "result", "result": step.get("result", "did the work\n\nHANDOFF: ok\nFILES: none\nNEXT: nothing"),
          "session_id": step.get("sid", "e-sess-%d" % (n + 1)), "permission_denials": [],
          "stats": {"tools": {"totalCalls": 1, "totalFail": 0, "byName": {}},
                    "models": {}}}
sys.stdout.write(json.dumps([
    {"type": "assistant", "message": {"usage": {"input_tokens": 25000}}}, result]))
"""

PYTEST_STUB = """#!/bin/sh
echo "$@" >> "$STUB_PYTEST_LOG"
cat .pytest_out 2>/dev/null
exit $(cat .pytest_rc 2>/dev/null || echo 0)
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        td = tempfile.mkdtemp()
        self.sdir = tempfile.mkdtemp()
        self.cwd = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", self.cwd], check=True)
        subprocess.run(["git", "-C", self.cwd, "config", "user.email", "s@t"],
                       check=True)
        subprocess.run(["git", "-C", self.cwd, "config", "user.name", "s"],
                       check=True)
        with open(os.path.join(self.cwd, "guard_spec.py"), "w") as f:
            f.write("PROTECTED = 1\n")
        with open(os.path.join(self.cwd, "QWEN.md"), "w") as f:
            f.write("# rules\n")
        with open(os.path.join(self.cwd, "other.py"), "w") as f:
            f.write("ORIGINAL = 1\n")
        subprocess.run(["git", "-C", self.cwd, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.cwd, "commit", "-qm", "base"],
                       check=True)
        os.environ["QWEN_DELEGATE_WORKTREES"] = tempfile.mkdtemp()
        self.stub = os.path.join(td, "stub.py")
        with open(self.stub, "w") as f:
            f.write(STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IEXEC)
        machine = os.path.join(td, "executors.json")
        with open(machine, "w") as f:
            json.dump({"profiles": {"stub": {
                "argv": [sys.executable, self.stub, "-p", "{task}",
                         "--approval-mode", "{mode}", "-o", "json",
                         "-r", "{resume}"],
                "env": {"STUB_DIR": self.sdir, "MAIN_REPO": self.cwd},
            }}}, f)
        os.environ["QWEN_DELEGATE_EXECUTORS"] = machine
        os.environ["QWEN_DELEGATE_REGISTRY"] = os.path.join(td, "reg.jsonl")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def steps(self, steps):
        with open(os.path.join(self.sdir, "steps.json"), "w") as f:
            json.dump(steps, f)

    def task_seen(self, n):
        with open(os.path.join(self.sdir, f"task_{n}.txt")) as f:
            return f.read()

    def delegate(self, **over):
        args = {"task": "build out.py with MARKER", "cwd": self.cwd,
                "verify": "grep -q MARKER out.py", "approval_mode": "auto-edit",
                "executor": "stub", "max_iterations": 3}
        args.update(over)
        return engine.delegate(args)

    def enable_prefilter(self):
        p = os.path.join(self.cwd, "venv", "bin", "pytest")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(PYTEST_STUB)
        os.chmod(p, 0o755)
        self.plog = os.path.join(self.sdir, "pytest.log")
        os.environ["STUB_PYTEST_LOG"] = self.plog


class Loop(Fixture):
    def test_success_first_attempt_c3_shape(self):
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate()
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["trail"], ["attempt 1: VERIFY PASS"])
        ctx = r["ctx"]
        self.assertEqual(len(ctx["pre_sha"]), 40)          # FULL sha
        int(ctx["pre_sha"], 16)
        self.assertEqual(ctx["trust"], "verified")
        self.assertEqual(ctx["notes"], "")
        self.assertIsNone(ctx["worktree"])
        self.assertIsNone(ctx["merge"])
        self.assertEqual(ctx["refs_added"], [])
        self.assertEqual(ctx["cost_usd"], 0.0)

    def test_retry_feeds_real_gate_error_back(self):
        self.steps([{"write": {"out.py": "wrong\n"}},
                    {"write": {"out.py": "MARKER fixed\n"}}])
        r = self.delegate(verify="python3 -c \"import sys; sys.exit(0 if 'MARKER' in open('out.py').read() else print('GATEMSG out.py lacks MARKER') or 1)\"")
        self.assertEqual(r["status"], "success")
        self.assertEqual(len(r["trail"]), 2)
        self.assertIn("GATEMSG out.py lacks MARKER", self.task_seen(2))

    def test_preflight_green_flagged(self):
        with open(os.path.join(self.cwd, "out.py"), "w") as f:
            f.write("MARKER already\n")
        self.steps([{}])
        r = self.delegate()
        self.assertTrue(r["ctx"]["preflight"])

    def test_gate_suspect_bails_on_attempt_1(self):
        self.steps([{"write": {"out.py": "attempt work\n"}}, {}, {}])
        r = self.delegate(verify="echo CONSTANT-OUTPUT && false")
        self.assertEqual(r["status"], "gate_suspect")
        self.assertEqual(len(r["trail"]), 1)

    def test_unverified_without_gate(self):
        self.steps([{"write": {"out.py": "whatever\n"}}])
        r = self.delegate(verify=None)
        self.assertEqual(r["status"], "unverified")


class SpecGuard(Fixture):
    def test_worker_spec_edit_reverted_and_attempt_fails(self):
        self.steps([{"write": {"guard_spec.py": "WEAKENED = 1\n",
                               "out.py": "MARKER\n"}},
                    {"write": {"out.py": "MARKER\n"}}])
        r = self.delegate()
        with open(os.path.join(self.cwd, "guard_spec.py")) as f:
            self.assertEqual(f.read(), "PROTECTED = 1\n")   # reverted
        self.assertTrue(any("SPEC" in t.upper() for t in r["trail"]))

    def test_dirty_protected_spec_refuses_before_running(self):
        with open(os.path.join(self.cwd, "guard_spec.py"), "a") as f:
            f.write("dirty\n")
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate()
        self.assertEqual(r["status"], "refused")
        self.assertFalse(os.path.exists(os.path.join(self.sdir, "task_1.txt")))


class Prefilter(Fixture):
    def test_gate_green_prefilter_red_is_success_with_notes(self):
        self.enable_prefilter()
        self.steps([{"write": {"out.py": "MARKER\n", "calc_qwen.py": "x\n",
                               ".pytest_rc": "1"}}])
        r = self.delegate()
        self.assertEqual(r["status"], "success")            # advisory only
        self.assertIn("self-tests failing", r["ctx"]["notes"])

    def test_gate_red_prefilter_output_joins_feedback(self):
        self.enable_prefilter()
        self.steps([{"write": {"out.py": "wrong\n", "calc_qwen.py": "x\n",
                               ".pytest_rc": "1"}},
                    {"write": {"out.py": "MARKER\n", ".pytest_rc": "0"}}])
        r = self.delegate()
        self.assertEqual(r["status"], "success")
        self.assertIn("calc_qwen.py", self.task_seen(2))    # prefilter surfaced

    def test_prefilter_runs_on_qwen_files_only(self):
        self.enable_prefilter()
        self.steps([{"write": {"out.py": "MARKER\n", "calc_qwen.py": "x\n"}}])
        self.delegate()
        with open(self.plog) as f:
            logged = f.read()
        self.assertIn("calc_qwen.py", logged)
        self.assertNotIn("out.py", logged)

    def test_no_qwen_files_prefilter_skipped(self):
        self.enable_prefilter()
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate()
        self.assertEqual(r["status"], "success")
        self.assertFalse(os.path.exists(self.plog))

    def test_no_test_command_skipped_silently(self):
        self.steps([{"write": {"out.py": "MARKER\n", "calc_qwen.py": "x\n"}}])
        r = self.delegate()
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["ctx"]["notes"], "")


class Worktree(Fixture):
    """M4 seam: worktree='auto' runs the whole loop in an isolated container.
    On success the ENGINE commits the container's work (the worker never
    commits -- the mechanism does; merge needs a committed branch) and
    classifies mergeability read-only. On non-success the container is
    released -- nothing worth merging, nothing leaked."""

    def git_main(self, *a):
        return subprocess.run(["git", "-C", self.cwd] + list(a),
                              capture_output=True, text=True).stdout.strip()

    def test_auto_success_isolated_committed_classified(self):
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate(worktree="auto")
        self.assertEqual(r["status"], "success")
        wt = r["ctx"]["worktree"]
        self.assertTrue(wt["branch"].startswith("qwen/"))
        self.assertTrue(os.path.isfile(os.path.join(wt["path"], "out.py")))
        self.assertFalse(os.path.exists(os.path.join(self.cwd, "out.py")))
        self.assertEqual(self.git_main("status", "--porcelain"), "")
        # The engine committed the work: branch is ahead of base.
        ahead = self.git_main("rev-list", "--count",
                              f"HEAD..{wt['branch']}")
        self.assertEqual(ahead, "1")
        self.assertEqual(r["ctx"]["merge"], "clean")

    def test_auto_merge_conflict_classified_not_hardcoded(self):
        # The worker edits other.py in its worktree; a concurrent fan-out
        # sibling (main_diverge) commits a conflicting other.py to the main
        # tree. classify_merge must REPORT the conflict -- a hardcoded "clean"
        # would ship a false all-good on a real fan-out collision.
        self.steps([{"write": {"out.py": "MARKER\n",
                               "other.py": "WT_SIDE = 1\n"},
                     "main_diverge": True}])
        r = self.delegate(worktree="auto")
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["ctx"]["merge"], "conflict")

    def test_auto_failure_releases_container(self):
        self.steps([{"write": {"out.py": "wrong\n"}}])
        r = self.delegate(worktree="auto", max_iterations=1)
        self.assertEqual(r["status"], "verify_failed")
        self.assertIsNone(r["ctx"]["worktree"])
        wl = self.git_main("worktree", "list")
        self.assertNotIn("qwen/", wl.replace(self.cwd, ""))


class TouchScope(Fixture):
    """M4 seam: per-task allowlist -- modify only named pre-existing files;
    creating NEW files stays free. The spec-guard machinery generalized from
    'never these' to 'only these'."""

    def test_out_of_scope_edit_reverted_attempt_fails_then_converges(self):
        self.steps([{"write": {"other.py": "TAMPERED = 1\n",
                               "out.py": "MARKER\n"}},
                    {"write": {"out.py": "MARKER\n"}}])
        r = self.delegate(touch_scope=["out.py"])
        self.assertEqual(r["status"], "success")
        self.assertEqual(len(r["trail"]), 2)
        self.assertIn("TOUCH SCOPE", r["trail"][0])
        with open(os.path.join(self.cwd, "other.py")) as f:
            self.assertEqual(f.read(), "ORIGINAL = 1\n")     # reverted
        self.assertIn("other.py", self.task_seen(2))          # named in feedback

    def test_new_files_always_allowed(self):
        self.steps([{"write": {"out.py": "MARKER\n",
                               "brand_new_helper.py": "h = 1\n"}}])
        r = self.delegate(touch_scope=["out.py"])
        self.assertEqual(r["status"], "success")
        self.assertEqual(len(r["trail"]), 1)
        self.assertTrue(os.path.exists(
            os.path.join(self.cwd, "brand_new_helper.py")))


class Refusals(Fixture):
    def test_trust_stub_refuses_non_verified(self):
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate(trust="checked")
        self.assertEqual(r["status"], "refused")
        self.assertIn("verified", r["result_text"])
        self.assertFalse(os.path.exists(os.path.join(self.sdir, "task_1.txt")))

    def test_nongit_refused(self):
        r = engine.delegate({"task": "t", "cwd": tempfile.mkdtemp(),
                             "verify": "true", "executor": "stub"})
        self.assertEqual(r["status"], "refused")


class Accounting(Fixture):
    def test_cum_spans_attempts(self):
        self.steps([{"write": {"out.py": "wrong\n"}},
                    {"write": {"out.py": "MARKER\n"}}])
        r = self.delegate()
        self.assertEqual(r["ctx"]["cum"]["attempts"], 2)

    def test_run_returns_receipt_string(self):
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        out = engine.run({"task": "build out.py with MARKER", "cwd": self.cwd,
                          "verify": "grep -q MARKER out.py",
                          "approval_mode": "auto-edit", "executor": "stub"})
        self.assertIn("STATUS: success", out)
        self.assertIn("--- qwen result ---", out)


class MutationHardening(Fixture):
    """Closures for survivors of the Qwen adversarial mutation pilot (7/8 of
    its proposed mutants survived the original suite -- see FINDINGS "Qwen as
    mutation adversary"). Two survivors are documented residuals instead of
    tests: the prefilter subprocess-crash fallback and the reflexion
    repeated-failure comparison mode need fixture machinery that would cost
    more than the risk they guard."""

    def test_timeout_floor_clamped_to_30(self):            # survivor 1
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate(timeout_sec=1)
        self.assertEqual(r["ctx"]["timeout"], 30)

    def test_attempt_1_receives_handoff_suffix(self):      # survivor 6
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        self.delegate()
        self.assertIn("HANDOFF:", self.task_seen(1))
        self.assertIn("FILES:", self.task_seen(1))

    def test_sessions_deduped_on_resume(self):             # survivor 5
        self.steps([{"write": {"out.py": "wrong\n"}, "sid": "same-sid"},
                    {"write": {"out.py": "MARKER\n"}, "sid": "same-sid"}])
        r = self.delegate()
        self.assertEqual(r["ctx"]["sessions"].count("same-sid"), 1)

    def test_on_compaction_default_is_reinject(self):      # survivor 8
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        r = self.delegate()
        self.assertEqual(r["ctx"]["on_compaction"], "reinject")

    def test_prefilter_output_capped_at_2000(self):        # survivor 2
        self.enable_prefilter()
        self.steps([{"write": {"out.py": "wrong\n", "calc_qwen.py": "x\n",
                               ".pytest_rc": "1", ".pytest_out": "z" * 5000}},
                    {"write": {"out.py": "MARKER\n", ".pytest_rc": "0"}}])
        self.delegate()
        fed = self.task_seen(2)
        self.assertIn("z" * 100, fed)              # output did reach feedback
        self.assertNotIn("z" * 2501, fed)          # but capped at ~2000


class GraphWiring(Fixture):
    """M5 seam: engine.run populates ctx['graph_line'] and fires a post-verdict
    graph refresh for IN-TREE successes. Worktree successes leave the main-tree
    graph alone (main HEAD only moves when Claude runs the MERGE line)."""

    def _stub_graphify(self):
        import stat as _stat
        d = tempfile.mkdtemp()
        g = os.path.join(d, "graphify")
        with open(g, "w") as f:
            f.write("#!/usr/bin/env python3\nimport sys; sys.exit(0)\n")
        os.chmod(g, os.stat(g).st_mode | _stat.S_IEXEC)
        os.environ["QWEN_DELEGATE_GRAPHIFY"] = g

    def _wait_sidecar(self, cwd, timeout=8):
        from qd import graph
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = graph.read_state(cwd)
            if st and st["status"] in ("fresh", "failed"):
                return st
            time.sleep(0.05)
        return graph.read_state(cwd)

    def test_run_receipt_carries_graph_line(self):
        self._stub_graphify()
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        out = engine.run({"task": "t", "cwd": self.cwd,
                          "verify": "grep -q MARKER out.py",
                          "approval_mode": "auto-edit", "executor": "stub"})
        self.assertIn("GRAPH:", out)

    def test_intree_success_fires_refresh(self):
        from qd import graph
        self._stub_graphify()
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        engine.run({"task": "t", "cwd": self.cwd,
                    "verify": "grep -q MARKER out.py",
                    "approval_mode": "auto-edit", "executor": "stub"})
        st = self._wait_sidecar(self.cwd)
        self.assertIsNotNone(st)
        self.assertEqual(st["status"], "fresh")

    def test_worktree_success_does_not_refresh_main(self):
        from qd import graph
        self._stub_graphify()
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        engine.run({"task": "t", "cwd": self.cwd,
                    "verify": "grep -q MARKER out.py",
                    "approval_mode": "auto-edit", "executor": "stub",
                    "worktree": "auto"})
        time.sleep(0.5)
        self.assertIsNone(graph.read_state(self.cwd))    # main graph untouched

    def test_failure_does_not_refresh(self):
        from qd import graph
        self._stub_graphify()
        self.steps([{"write": {"out.py": "wrong\n"}}])
        engine.run({"task": "t", "cwd": self.cwd,
                    "verify": "grep -q MARKER out.py",
                    "approval_mode": "auto-edit", "executor": "stub",
                    "max_iterations": 1})
        time.sleep(0.5)
        self.assertIsNone(graph.read_state(self.cwd))


if __name__ == "__main__":
    unittest.main(verbosity=1)
