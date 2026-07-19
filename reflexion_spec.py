#!/usr/bin/env python3
"""
Spec for Reflexion in the free-executor retry loop (#24, addition A).

Claude-authored gate -- never delegate this file; it defines what "reflexion" means.

On a gate failure the server no longer just re-sends the raw error and says "fix it".
It makes Qwen self-diagnose (ROOT CAUSE -> fix) before editing, and when the SAME failure
recurs across attempts it escalates to "that approach is wrong, try a DIFFERENT one"
instead of another variation. This raises the first-pass gate-pass rate at ZERO Claude-
token cost -- the manager is never in this loop, and the gate still decides, so a wrong
self-diagnosis simply fails again and the loop moves on (no new trust is extended).

Run:  python3 reflexion_spec.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

TASK = "implement the thing"
VERIFY = "./gate.sh"
V_OUT = "AssertionError: add(2, 3) == 5"


class ReflexionPrompt(unittest.TestCase):
    """What the retry prompt asks for, at the unit level."""

    def clean(self, **kw):
        # 's-intact' has no compaction marker -> action 'none', delta-only prompt.
        return server.retry_prompt("s-intact-refl", TASK, VERIFY, V_OUT, **kw)[0]

    def test_asks_for_root_cause_not_just_retry(self):
        self.assertIn("root cause", self.clean().lower(),
                      "reflexion must make Qwen diagnose, not blindly retry")

    def test_still_feeds_back_the_objective_error(self):
        # The diagnosis is steering; the gate output is the evidence. Never drop it.
        self.assertIn(V_OUT, self.clean())

    def test_not_repeated_does_not_escalate(self):
        self.assertNotIn("different approach", self.clean().lower())

    def test_repeated_forces_a_different_approach(self):
        p = self.clean(repeated=True).lower()
        self.assertIn("different approach", p)
        self.assertIn("same", p, "must tell Qwen it hit the SAME wall again")

    def test_repeated_still_feeds_back_the_objective_error(self):
        self.assertIn(V_OUT, self.clean(repeated=True))

    def test_reflexion_survives_compaction(self):
        """The reinject/discard branches embed the same failure text, so the ROOT CAUSE
        ask must still be there after a compaction -- otherwise a compacted retry silently
        drops the reflexion."""
        w, a = server.was_compacted_since_ack, server.ack_compaction
        server.was_compacted_since_ack = lambda sid: True
        server.ack_compaction = lambda sid: None
        try:
            for policy in ("reinject", "discard"):
                p = server.retry_prompt("s-c", TASK, VERIFY, V_OUT,
                                        on_compaction=policy)[0].lower()
                self.assertIn("root cause", p, f"{policy} branch dropped the reflexion ask")
        finally:
            server.was_compacted_since_ack, server.ack_compaction = w, a


class ReflexionLoop(unittest.TestCase):
    """Integration: the real loop must FLIP to the 'different approach' prompt once the
    same failure repeats, and must NOT flip when failures differ. A prompt that resolves
    correctly but never reaches invoke_qwen would be a silent no-op."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="refl-loop-")
        for a in (["init", "-q"], ["config", "user.email", "t@l"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", self.d] + a, capture_output=True)
        with open(os.path.join(self.d, "a.py"), "w") as f:
            f.write("x = 1\n")
        with open(os.path.join(self.d, "QWEN.md"), "w") as f:
            f.write("rules\n")  # configured -> no bootstrap detour
        subprocess.run(["git", "-C", self.d, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", self.d, "commit", "-qm", "b"], capture_output=True)

        self._reg = server.PROJECT_REGISTRY
        server.PROJECT_REGISTRY = os.path.join(self.d, "reg.jsonl")
        self._invoke, self._verify = server.invoke_qwen, server.run_verify

        self.prompts = []

        def fake_invoke(prompt, *a, **k):
            self.prompts.append(prompt)
            return "out", [], "sess", None, {"peak": 1, "stats": {}, "blocked": []}

        server.invoke_qwen = fake_invoke

    def tearDown(self):
        server.invoke_qwen, server.run_verify = self._invoke, self._verify
        server.PROJECT_REGISTRY = self._reg

    def drive(self, verify_fn):
        self.prompts = []
        server.run_verify = verify_fn
        server.run_qwen({"task": "t", "cwd": self.d, "verify": "./gate.sh",
                         "approval_mode": "auto-edit", "max_iterations": 3})
        return self.prompts

    def test_repeated_identical_failure_escalates(self):
        # call 1 = preflight (distinct, so gate_suspect never fires); every attempt = SAME.
        state = {"n": 0}

        def verify_same(v, c):
            state["n"] += 1
            return (False, "PREFLIGHT-BASELINE") if state["n"] == 1 else (False, "SAME-ERR")

        ps = self.drive(verify_same)
        self.assertEqual(len(ps), 3, "should run all 3 attempts")
        # attempt 2 (first retry, nothing to repeat yet) -> normal reflexion
        self.assertIn("root cause", ps[1].lower())
        self.assertNotIn("different approach", ps[1].lower())
        # attempt 3 (after the 2nd identical failure) -> escalation
        self.assertIn("different approach", ps[2].lower())

    def test_distinct_failures_never_escalate(self):
        state = {"n": 0}

        def verify_distinct(v, c):
            state["n"] += 1
            return (False, "PREFLIGHT") if state["n"] == 1 else (False, f"err-{state['n']}")

        ps = self.drive(verify_distinct)
        self.assertEqual(len(ps), 3)
        for p in ps:
            self.assertNotIn("different approach", p.lower(),
                             "distinct errors must not trip the repeated-failure escalation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
