#!/usr/bin/env python3
"""
Spec for qd/runlog.py -- run log v2 (port + HLD C5, LLD "qd/runlog.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

Port gate for the v1 logging surface plus the v2 obligations:

  1. INVISIBLE to git status (self-ignoring .qwen-delegate/) -- an un-ignored log
     would be attributed to Qwen by snapshot()/blast_radius() and would trip the
     dirty-tree precondition.
  2. A logging failure must never raise -- best-effort by contract.
  3. C5: every record carries `executor` and `cost_usd` -- and 0.0 means
     "priced at zero", never "unmeasured".
  4. Concurrent appends (the v2 threaded server) must yield one valid JSONL line
     per call, no torn lines.
  5. The registry honors QWEN_DELEGATE_REGISTRY at CALL time (deliberate,
     spec-sanctioned deviation from v1's import-time read: testability).

Public surface pinned here:
    qd.runlog.RUNLOG_DIR (".qwen-delegate"), qd.runlog.RUNLOG_FILE ("runs.jsonl")
    qd.runlog.runlog_dir(cwd) -> path          (creates + self-ignores)
    qd.runlog.registry_path() -> path          (env at call time)
    qd.runlog.register_project(cwd)
    qd.runlog.now_iso() -> "YYYY-MM-DDTHH:MM:SSZ"
    qd.runlog.digest(text) -> {"head", "sha256"(16), "chars"}
    qd.runlog.write_runlog(cwd, record)        (never raises)
    qd.runlog.leverage_record(tool, cwd, status, verdict, stats, peak,
                              executor="qwen-local", cost_usd=0.0, extra=None)

Run:  python3 specs/runlog_spec.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import runlog  # noqa: E402


STATS = {
    "tokens": {"prompt": 1000, "completion": 200, "total": 1200,
               "cached": 0, "thoughts": 0},
    "token_source": "bySource",
    "ms": 5000, "turns": 3,
}


class Fixture(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        self.cwd = tempfile.mkdtemp()
        self.registry = os.path.join(tempfile.mkdtemp(), "projects.jsonl")
        os.environ["QWEN_DELEGATE_REGISTRY"] = self.registry

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def log_path(self):
        return os.path.join(self.cwd, runlog.RUNLOG_DIR, runlog.RUNLOG_FILE)


class Invisibility(Fixture):
    def test_dir_self_ignores(self):
        d = runlog.runlog_dir(self.cwd)
        with open(os.path.join(d, ".gitignore")) as f:
            self.assertEqual(f.read().strip(), "*")

    def test_log_never_in_git_status(self):
        subprocess.run(["git", "init", "-q", self.cwd], check=True)
        runlog.write_runlog(self.cwd, {"k": "v"})
        out = subprocess.run(["git", "status", "--porcelain"], cwd=self.cwd,
                             capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "")


class NeverRaises(Fixture):
    def test_unwritable_dir_swallowed(self):
        d = runlog.runlog_dir(self.cwd)
        os.chmod(d, 0o555)
        try:
            runlog.write_runlog(self.cwd, {"k": "v"})  # must not raise
        finally:
            os.chmod(d, 0o755)

    def test_unserializable_record_swallowed(self):
        runlog.write_runlog(self.cwd, {"bad": object()})  # must not raise


class RecordShape(Fixture):
    def rec(self, **kw):
        return runlog.leverage_record("delegate", self.cwd, "success",
                                      "VERDICT " * 10, STATS, 30000, **kw)

    def test_v1_keys_and_math(self):
        r = self.rec()
        for k in ("ts", "tool", "status", "cwd", "tokens", "tokens_main",
                  "tokens_overhead", "token_source", "peak_context",
                  "verdict_chars", "verdict_tokens_est", "leverage",
                  "duration_ms", "turns", "tools", "api_errors",
                  "lines_added", "lines_removed", "models"):
            self.assertIn(k, r)
        self.assertEqual(r["verdict_chars"], 80)
        self.assertEqual(r["verdict_tokens_est"], round(80 / 4.0))
        self.assertEqual(r["leverage"], round(1200 / round(80 / 4.0), 1))
        self.assertEqual(r["token_source"], "bySource")

    def test_token_source_defaults_to_none_string(self):
        r = runlog.leverage_record("q", self.cwd, "ok", "v", {}, 0)
        self.assertEqual(r["token_source"], "none")

    def test_c5_executor_and_cost_always_present(self):
        r = self.rec()
        self.assertEqual(r["executor"], "qwen-local")
        self.assertEqual(r["cost_usd"], 0.0)
        self.assertIsInstance(r["cost_usd"], float)
        r2 = self.rec(executor="paid", cost_usd=1.23)
        self.assertEqual((r2["executor"], r2["cost_usd"]), ("paid", 1.23))

    def test_extra_merges_and_overrides(self):
        r = self.rec(extra={"worktree": "/w/x", "status": "override"})
        self.assertEqual(r["worktree"], "/w/x")
        self.assertEqual(r["status"], "override")

    def test_digest(self):
        d = runlog.digest("A" * 500)
        self.assertEqual(d["head"], "A" * 200)
        self.assertEqual(len(d["sha256"]), 16)
        self.assertEqual(d["chars"], 500)

    def test_now_iso_format(self):
        import re
        self.assertTrue(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                                     runlog.now_iso()))


class Registry(Fixture):
    def test_registered_once(self):
        runlog.write_runlog(self.cwd, {"a": 1})
        runlog.write_runlog(self.cwd, {"a": 2})
        with open(self.registry) as f:
            lines = [json.loads(x) for x in f.read().splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["path"], self.cwd)

    def test_corrupt_line_tolerated(self):
        os.makedirs(os.path.dirname(self.registry), exist_ok=True)
        with open(self.registry, "w") as f:
            f.write("{corrupt\n")
        runlog.register_project(self.cwd)  # must not raise
        with open(self.registry) as f:
            self.assertIn(self.cwd, f.read())

    def test_registry_path_reads_env_at_call_time(self):
        os.environ["QWEN_DELEGATE_REGISTRY"] = "/tmp/other.jsonl"
        self.assertEqual(runlog.registry_path(), "/tmp/other.jsonl")


class ConcurrentAppends(Fixture):
    def test_n_threads_n_valid_lines(self):
        n = 8
        threads = [threading.Thread(
            target=runlog.write_runlog,
            args=(self.cwd, {"i": i, "pad": "x" * 2000})) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with open(self.log_path()) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), n)
        seen = {json.loads(line)["i"] for line in lines}  # every line parses
        self.assertEqual(seen, set(range(n)))


class LedgerSummary(unittest.TestCase):
    """U2.5: the log gets its first reader. Tolerant, delegate-only, never
    raises; None when there is nothing to summarise."""

    def setUp(self):
        self.cwd = tempfile.mkdtemp()

    def seed(self, records, corrupt=False):
        d = os.path.join(self.cwd, ".qwen-delegate")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "runs.jsonl"), "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
            if corrupt:
                f.write("{not json\n")

    def test_none_when_no_history(self):
        self.assertIsNone(runlog.ledger_summary(self.cwd))

    def test_counts_by_status_and_peak_delegate_only(self):
        self.seed([
            {"tool": "qwen_delegate", "status": "success",
             "peak_context": 30000},
            {"tool": "qwen_delegate", "status": "success_but_preflight_passed",
             "peak_context": 60000},
            {"tool": "qwen_delegate", "status": "verify_failed",
             "peak_context": 10000},
            {"tool": "qwen_delegate", "status": "stopped", "peak_context": 0},
            {"tool": "qwen_query", "status": "ok", "peak_context": 999999},
        ])
        s = runlog.ledger_summary(self.cwd)
        self.assertEqual(s, {"n": 4, "ok": 2, "red": 1, "stopped": 1,
                             "peak": 60000})

    def test_corrupt_lines_must_not_hide_the_rest(self):
        self.seed([{"tool": "qwen_delegate", "status": "success",
                    "peak_context": 5}], corrupt=True)
        self.assertEqual(runlog.ledger_summary(self.cwd)["n"], 1)

    def test_an_in_flight_submission_is_not_a_run_yet(self):
        # U5.2: `running` is a submission marker. Counted, it would inflate the
        # lifetime total and file every live run in the red bucket -- the
        # ledger would read worse the busier the project got.
        self.seed([{"tool": "qwen_delegate", "status": "running",
                    "run_id": "rabc123", "pid": os.getpid()},
                   {"tool": "qwen_delegate", "status": "success",
                    "run_id": "rabc123", "peak_context": 10}])
        self.assertEqual(runlog.ledger_summary(self.cwd),
                         {"n": 1, "ok": 1, "red": 0, "stopped": 0, "peak": 10})


class RunsInFlight(LedgerSummary):
    """U5.2: which submitted runs have not come back, and which died trying.

    The log is append-only -- nothing rewrites the `running` line -- so the
    pairing is done at READ time by run_id, and a run whose owning process is
    gone is reported as dead rather than dropped: "it died with your session"
    is exactly what the caller polling for a receipt needs to hear.
    """

    def dead_pid(self):
        """A pid that has certainly exited (reaped, so it is not a zombie)."""
        p = subprocess.Popen([sys.executable, "-c", ""])
        p.wait()
        return p.pid

    def test_no_log_is_no_runs(self):
        self.assertEqual(runlog.runs_in_flight(self.cwd), [])

    def test_an_unpaired_running_record_is_in_flight(self):
        self.seed([{"tool": "qwen_delegate", "status": "running",
                    "run_id": "r000001", "pid": os.getpid(), "ts": "T"}])
        self.assertEqual(runlog.runs_in_flight(self.cwd),
                         [{"run_id": "r000001", "pid": os.getpid(),
                           "ts": "T", "dead": False}])

    def test_a_completion_record_closes_it(self):
        self.seed([{"tool": "qwen_delegate", "status": "running",
                    "run_id": "r000001", "pid": os.getpid()},
                   {"tool": "qwen_delegate", "status": "verify_failed",
                    "run_id": "r000001"}])
        self.assertEqual(runlog.runs_in_flight(self.cwd), [])

    def test_a_dead_owner_is_reported_as_dead_not_dropped(self):
        self.seed([{"tool": "qwen_delegate", "status": "running",
                    "run_id": "r000002", "pid": self.dead_pid()}])
        flight = runlog.runs_in_flight(self.cwd)
        self.assertEqual(len(flight), 1)
        self.assertTrue(flight[0]["dead"])

    def test_records_without_a_run_id_are_none_of_its_business(self):
        self.seed([{"tool": "qwen_delegate", "status": "success"},
                   {"tool": "qwen_query", "status": "ok"}])
        self.assertEqual(runlog.runs_in_flight(self.cwd), [])

    def test_a_corrupt_line_does_not_hide_the_rest(self):
        self.seed([{"tool": "qwen_delegate", "status": "running",
                    "run_id": "r000003", "pid": os.getpid()}], corrupt=True)
        self.assertEqual([f["run_id"] for f in runlog.runs_in_flight(self.cwd)],
                         ["r000003"])


class RunIds(unittest.TestCase):
    def test_the_c6_shape(self):
        for _ in range(20):
            self.assertRegex(runlog.new_run_id(), r"^r[0-9a-f]{6}$")

    def test_ids_are_not_a_counter(self):
        self.assertGreater(len({runlog.new_run_id() for _ in range(50)}), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
