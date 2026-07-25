#!/usr/bin/env python3
"""
Spec for qd/graph.py -- graph freshness keyed to git (HLD F5/C4/C2,
LLD "qd/graph.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

graphify OWNS querying; this module owns exactly one thing graphify cannot:
freshness as a git fact. No model tokens here -- staleness is `git diff`, and
the refresh shells out to graphify (stubbed in this gate so it is hermetic and
fast). Load-bearing:

  1. Staleness is pure git: files changed between the indexed sha and HEAD.
  2. Sidecar (C4) transitions atomically: fresh -> indexing -> fresh on a
     zero-exit refresh, -> failed(+reason) on nonzero. No torn JSON ever
     visible (temp + rename).
  3. GRAPH line (C2) reports honestly for every state: none / fresh / stale /
     indexing / failed.
  4. Graceful degradation: graphify absent, or an indexed sha git no longer
     knows (history rewritten), never raises and never blocks a delegation --
     the graph is advisory infrastructure, never a gate on delegation success.
  5. refresh_async does not block the caller (post-verdict, off the hot path).

Public surface pinned here:
    qd.graph.sidecar_path(cwd) -> str
    qd.graph.read_state(cwd) -> dict | None            (C4 sidecar, or None)
    qd.graph.staleness(cwd) -> {"indexed_sha","stale","status"}
    qd.graph.graphify_cmd(cwd, files) -> list[str]     (the M0-probe seam)
    qd.graph.refresh_sync(cwd, files) -> None
    qd.graph.refresh_async(cwd, files) -> threading.Thread
    qd.graph.graph_line(cwd) -> str                    (a C2 GRAPH: line)

The graphify binary is QWEN_DELEGATE_GRAPHIFY (default "graphify"); tests
point it at a stub whose exit code and delay they control.

Run:  python3 specs/graphstate_spec.py
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import graph  # noqa: E402

# Stub graphify: records its argv, honors an exit code + delay from env, and
# (like the real `graphify update`) leaves the tree untouched.
STUB = r"""#!/usr/bin/env python3
import os, sys, time
rec = os.environ.get("STUB_GRAPHIFY_ARGV")
if rec:
    open(rec, "w").write(repr(sys.argv))
d = os.environ.get("STUB_GRAPHIFY_SLEEP")
if d:
    time.sleep(float(d))
sys.exit(int(os.environ.get("STUB_GRAPHIFY_RC", "0")))
"""


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        self.cwd = tempfile.mkdtemp()
        sh(self.cwd, "git", "init", "-q")
        sh(self.cwd, "git", "config", "user.email", "s@t")
        sh(self.cwd, "git", "config", "user.name", "s")
        self.c1 = self._commit("a.py", "x = 1\n", "c1")
        td = tempfile.mkdtemp()
        self.stub = os.path.join(td, "graphify")
        with open(self.stub, "w") as f:
            f.write(STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IEXEC)
        os.environ["QWEN_DELEGATE_GRAPHIFY"] = self.stub
        self.argv_rec = os.path.join(td, "argv.txt")
        os.environ["STUB_GRAPHIFY_ARGV"] = self.argv_rec

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def _commit(self, rel, content, msg):
        with open(os.path.join(self.cwd, rel), "w") as f:
            f.write(content)
        sh(self.cwd, "git", "add", "-A")
        sh(self.cwd, "git", "commit", "-qm", msg)
        return sh(self.cwd, "git", "rev-parse", "HEAD").stdout.strip()

    def seed(self, sha, status="fresh", reason=None):
        graph.refresh_sync  # ensure import ok
        os.makedirs(os.path.dirname(graph.sidecar_path(self.cwd)),
                    exist_ok=True)
        state = {"indexed_sha": sha, "ts": "2026-07-22T00:00:00Z",
                 "status": status}
        if reason:
            state["reason"] = reason
        tmp = graph.sidecar_path(self.cwd) + ".seed"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, graph.sidecar_path(self.cwd))


class Staleness(Fixture):
    def test_fresh_when_indexed_at_head(self):
        self.seed(self.c1)
        s = graph.staleness(self.cwd)
        self.assertEqual(s["indexed_sha"], self.c1)
        self.assertEqual(s["stale"], [])
        self.assertEqual(s["status"], "fresh")

    def test_stale_lists_changed_files(self):
        self.seed(self.c1)
        self._commit("b.py", "y = 2\n", "c2")
        self._commit("a.py", "x = 9\n", "c3")
        s = graph.staleness(self.cwd)
        self.assertEqual(set(s["stale"]), {"a.py", "b.py"})
        self.assertEqual(s["status"], "stale")

    def test_no_sidecar_is_none_status(self):
        s = graph.staleness(self.cwd)
        self.assertIsNone(s["indexed_sha"])
        self.assertEqual(s["status"], "none")

    def test_unknown_sha_degrades_to_none(self):
        self.seed("0" * 40)          # a sha git has never seen
        s = graph.staleness(self.cwd)     # must not raise
        self.assertEqual(s["status"], "none")     # LLD contract: treat as none


class Sidecar(Fixture):
    def test_refresh_success_fresh_at_new_head(self):
        self._commit("b.py", "y = 2\n", "c2")
        head = sh(self.cwd, "git", "rev-parse", "HEAD").stdout.strip()
        graph.refresh_sync(self.cwd, ["b.py"])
        st = graph.read_state(self.cwd)
        self.assertEqual(st["status"], "fresh")
        self.assertEqual(st["indexed_sha"], head)
        self.assertTrue(os.path.isfile(self.argv_rec))   # graphify was run

    def test_refresh_failure_records_reason(self):
        os.environ["STUB_GRAPHIFY_RC"] = "3"
        graph.refresh_sync(self.cwd, ["a.py"])
        st = graph.read_state(self.cwd)
        self.assertEqual(st["status"], "failed")
        self.assertTrue(st.get("reason"))

    def test_missing_graphify_fails_soft(self):
        os.environ["QWEN_DELEGATE_GRAPHIFY"] = "/no/such/graphify-xyz"
        graph.refresh_sync(self.cwd, ["a.py"])   # must not raise
        st = graph.read_state(self.cwd)
        self.assertEqual(st["status"], "failed")

    def test_no_partial_json_and_no_tmp_left(self):
        graph.refresh_sync(self.cwd, ["a.py"])
        d = os.path.dirname(graph.sidecar_path(self.cwd))
        self.assertFalse(any(f.endswith(".tmp") for f in os.listdir(d)))
        json.load(open(graph.sidecar_path(self.cwd)))   # parses whole


class Async(Fixture):
    def test_async_does_not_block_then_completes(self):
        os.environ["STUB_GRAPHIFY_SLEEP"] = "1.0"
        t0 = time.time()
        th = graph.refresh_async(self.cwd, ["a.py"])
        self.assertLess(time.time() - t0, 0.5)          # returned promptly
        self.assertTrue(th.daemon)   # a hung refresh must never block exit
        self.assertEqual(graph.read_state(self.cwd)["status"], "indexing")
        th.join(timeout=10)
        self.assertEqual(graph.read_state(self.cwd)["status"], "fresh")


class GraphifyCmd(Fixture):
    def test_cmd_uses_configured_binary_and_cwd(self):
        cmd = graph.graphify_cmd(self.cwd, ["a.py", "b.py"])
        self.assertEqual(cmd[0], self.stub)
        self.assertIn(self.cwd, cmd)

    def test_cmd_default_binary_is_graphify(self):
        del os.environ["QWEN_DELEGATE_GRAPHIFY"]
        self.assertEqual(graph.graphify_cmd(self.cwd, [])[0], "graphify")

    def test_cmd_forces_structural_no_cluster(self):
        # Load-bearing safety: the auto-refresh must never reach an LLM. A bare
        # `graphify update` would let graphify pick a backend from the env
        # (AWS Bedrock if AWS_PROFILE is set) -- billing prod and egressing the
        # corpus, unseen, since the server runs it outside any approval gate.
        self.assertIn("--no-cluster", graph.graphify_cmd(self.cwd, ["a.py"]))


# Documented cosmetic survivors of the adversarial pass (graph round), left as
# documentation rather than brittle tests: the failure-reason wording and its
# truncation length (a reason is a lead, not parsed), the null-sha display
# fallback in graph_line (unreachable for fresh/stale, which always carry a
# sha), and the git-diff whitespace-line filter (no-op on real diff output).


class GraphLine(Fixture):
    def test_none(self):
        self.assertEqual(graph.graph_line(self.cwd),
                         "GRAPH: none — run graphify once to index")

    def test_fresh(self):
        self.seed(self.c1)
        self.assertEqual(graph.graph_line(self.cwd),
                         f"GRAPH: fresh @ {self.c1[:7]}")

    def test_stale(self):
        self.seed(self.c1)
        self._commit("b.py", "y = 2\n", "c2")
        line = graph.graph_line(self.cwd)
        self.assertEqual(
            line, f"GRAPH: stale (1 files since {self.c1[:7]}) — refresh running")

    def test_indexing(self):
        self.seed(self.c1, status="indexing")
        self.assertEqual(graph.graph_line(self.cwd), "GRAPH: indexing")

    def test_failed(self):
        self.seed(self.c1, status="failed", reason="graphify not installed")
        self.assertEqual(graph.graph_line(self.cwd),
                         "GRAPH: failed: graphify not installed")


if __name__ == "__main__":
    unittest.main(verbosity=1)
