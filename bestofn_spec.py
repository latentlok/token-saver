#!/usr/bin/env python3
"""
Spec for best-of-N (#26): the `workers` breadth axis of the free-executor loop.

Claude-authored gate -- never delegate this file.

`workers` runs up to N INDEPENDENT candidates for one task from the same committed base,
resetting the tree between them, and accepts the FIRST whose gate passes. workers=1
(default) is the single-candidate path -- byte-identical to before. Zero Claude tokens;
the gate -- not any candidate's self-report -- picks the winner, and the loop short-
circuits the moment one passes.

Run:  python3 bestofn_spec.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402


class ResolveWorkers(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="wk-")

    def cfg(self, n):
        with open(os.path.join(self.d, server.PROJECT_CONFIG), "w") as f:
            json.dump({"workers": n}, f)

    def test_default_is_one(self):
        self.assertEqual(server.resolve_workers(self.d, None), 1)

    def test_config_beats_default(self):
        self.cfg(3)
        self.assertEqual(server.resolve_workers(self.d, None), 3)

    def test_arg_beats_config(self):
        self.cfg(3)
        self.assertEqual(server.resolve_workers(self.d, 5), 5)

    def test_clamp_high(self):
        self.assertEqual(server.resolve_workers(self.d, 99), 8)

    def test_zero_falls_through_to_default(self):
        self.assertEqual(server.resolve_workers(self.d, 0), 1)


class BestOfN(unittest.TestCase):
    """Drive the real run_qwen wrapper with a stubbed worker + gate. A `workers` value that
    resolves correctly but doesn't actually fan out (or doesn't stop at a winner) would be a
    silent no-op, so this asserts the candidate count the loop truly runs."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="bon-")
        for a in (["init", "-q"], ["config", "user.email", "t@l"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", self.d] + a, capture_output=True)
        with open(os.path.join(self.d, "a.py"), "w") as f:
            f.write("x = 1\n")
        with open(os.path.join(self.d, "QWEN.md"), "w") as f:
            f.write("rules\n")
        subprocess.run(["git", "-C", self.d, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", self.d, "commit", "-qm", "b"], capture_output=True)

        self._reg = server.PROJECT_REGISTRY
        server.PROJECT_REGISTRY = os.path.join(self.d, "reg.jsonl")
        self._invoke = server.invoke_qwen
        self._verify = server.run_verify
        self._reset = server.reset_worktree

        self.invokes = []   # session_id per invoke; len() == candidate boundary marker
        self.resets = 0

        def fake_invoke(prompt, *a, **k):
            self.invokes.append(a[3])  # (prompt, cwd, mode, timeout, session_id) -> a[3]=sid
            return "out", [], "sess", None, {"peak": 1, "stats": {}, "blocked": []}

        def fake_reset(cwd, sha):
            self.resets += 1

        server.invoke_qwen = fake_invoke
        server.reset_worktree = fake_reset

    def tearDown(self):
        server.invoke_qwen = self._invoke
        server.run_verify = self._verify
        server.reset_worktree = self._reset
        server.PROJECT_REGISTRY = self._reg

    def gate_winner_at(self, winning_candidate):
        """run_verify PASSES only once `winning_candidate` has invoked (n==winner); distinct
        fail text avoids the gate_suspect early-bail. winner=None => never passes."""
        def fv(v, c):
            n = len(self.invokes)
            if winning_candidate is not None and n == winning_candidate:
                return True, "ok"
            return False, f"fail-{n}"
        return fv

    def run_with(self, workers, winner):
        self.invokes = []
        self.resets = 0
        server.run_verify = self.gate_winner_at(winner)
        # Pass a warm session_id so the "each candidate is fresh" override is actually
        # exercised: best-of-N must null it out, a passthrough (workers=1) must keep it.
        return server.run_qwen({"task": "t", "cwd": self.d, "verify": "./gate.sh",
                                "approval_mode": "auto-edit", "max_iterations": 1,
                                "session_id": "warm-caller", "workers": workers})

    def test_workers_1_is_single_candidate(self):
        self.run_with(1, winner=1)
        self.assertEqual(len(self.invokes), 1, "workers=1 must run exactly one candidate")
        self.assertEqual(self.resets, 0, "no reset with a single candidate")

    def test_stops_at_first_winner(self):
        out = self.run_with(3, winner=2)
        self.assertEqual(len(self.invokes), 2, "must stop after the winning candidate")
        self.assertIn("success", out.lower())
        self.assertEqual(self.resets, 1, "exactly one reset, before candidate 2")

    def test_all_fail_runs_every_candidate(self):
        self.run_with(3, winner=None)
        self.assertEqual(len(self.invokes), 3, "no winner -> all candidates run")
        self.assertEqual(self.resets, 2, "reset before candidates 2 and 3")

    def test_each_candidate_is_a_fresh_session(self):
        self.run_with(3, winner=None)
        self.assertTrue(all(sid is None for sid in self.invokes),
                        "every candidate must start with a fresh (None) session")

    def test_no_verify_collapses_to_single(self):
        self.invokes = []
        self.resets = 0
        server.run_verify = self.gate_winner_at(None)
        server.run_qwen({"task": "t", "cwd": self.d, "approval_mode": "auto-edit",
                         "max_iterations": 1, "workers": 3})  # no verify => no selector
        self.assertEqual(self.resets, 0, "without a gate there is no way to pick a winner")


if __name__ == "__main__":
    unittest.main(verbosity=2)
