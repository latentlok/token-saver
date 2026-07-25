#!/usr/bin/env python3
"""
Spec for the R3 trust slider (PLAN-v3-l5): both ends, no middle.

Claude-authored gate. Covers: the generated self-gate script's behavior
(green >= min, vacuous fail, failing suite, min override, worker-edit
overwrite), the engine's trust precondition, and the receipt TRUST line.
Engine wiring end-to-end is exercised live, not here (free-token runs).

Run:  python3 specs/trust_spec.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import engine  # noqa: E402


def write_suite(cwd, n_tests, failing=False):
    os.makedirs(os.path.join(cwd, "tests"), exist_ok=True)
    open(os.path.join(cwd, "tests", "__init__.py"), "w").close()
    body = "import unittest\n\nclass T(unittest.TestCase):\n"
    for i in range(n_tests):
        val = "False" if (failing and i == 0) else "True"
        body += f"    def test_{i}(self):\n        self.assertTrue({val})\n"
    with open(os.path.join(cwd, "tests", "test_gen.py"), "w") as f:
        f.write(body)


def run_gate(cwd):
    cmd = engine._ensure_self_gate(cwd)
    p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


class SelfGateScript(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()

    def test_green_suite_passes(self):
        write_suite(self.cwd, 6)
        rc, _ = run_gate(self.cwd)
        self.assertEqual(rc, 0)

    def test_vacuous_suite_fails(self):
        os.makedirs(os.path.join(self.cwd, "tests"), exist_ok=True)
        open(os.path.join(self.cwd, "tests", "__init__.py"), "w").close()
        rc, out = run_gate(self.cwd)
        self.assertNotEqual(rc, 0)

    def test_too_few_tests_fails_with_message(self):
        write_suite(self.cwd, 2)
        rc, out = run_gate(self.cwd)
        self.assertNotEqual(rc, 0)
        self.assertIn("SELF-GATE", out)

    def test_failing_suite_fails(self):
        write_suite(self.cwd, 6, failing=True)
        rc, _ = run_gate(self.cwd)
        self.assertNotEqual(rc, 0)

    def test_min_tests_config_override(self):
        write_suite(self.cwd, 6)
        with open(os.path.join(self.cwd, ".qwen-delegate.json"), "w") as f:
            f.write('{"min_tests": 10}')
        rc, out = run_gate(self.cwd)
        self.assertNotEqual(rc, 0)
        self.assertIn(">= 10", out)

    def test_worker_edit_is_overwritten(self):
        write_suite(self.cwd, 2)  # would fail the real gate
        engine._ensure_self_gate(self.cwd)
        gate = os.path.join(self.cwd, engine._SELF_GATE_PATH)
        with open(gate, "w") as f:
            f.write("#!/bin/bash\nexit 0\n")  # worker games its gate
        rc, _ = run_gate(self.cwd)  # regenerate + run, as the engine does
        self.assertNotEqual(rc, 0)

    def test_gate_dir_is_self_gitignored(self):
        engine._ensure_self_gate(self.cwd)
        with open(os.path.join(self.cwd, ".qwen-delegate", ".gitignore")) as f:
            self.assertEqual(f.read().strip(), "*")

    def test_min_override_ratchets_above_config(self):
        # The incremental ratchet: 6 green tests, bar raised to 7 -> gate red.
        write_suite(self.cwd, 6)
        cmd = engine._ensure_self_gate(self.cwd, min_override=7)
        p = subprocess.run(cmd, shell=True, cwd=self.cwd,
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(">= 7", (p.stdout or "") + (p.stderr or ""))


class TrustPrecondition(unittest.TestCase):
    def test_unknown_trust_refused_naming_both_ends(self):
        r = engine.delegate({"task": "t", "cwd": tempfile.mkdtemp(),
                             "trust": "L3"})
        self.assertEqual(r["status"], "refused")
        self.assertIn('"verified"', r["result_text"])
        self.assertIn('"self"', r["result_text"])


class TrustResolution(unittest.TestCase):
    """R3 slider position resolves: call arg > project .qwen-delegate.json > 'self'.

    Proven hermetically through the refusal branch (which runs before git/worker):
    a bogus position reaches validation only if that source was consulted.
    """

    def _cfg(self, trust_val):
        cwd = tempfile.mkdtemp()
        with open(os.path.join(cwd, ".qwen-delegate.json"), "w") as f:
            f.write('{"trust": "%s"}' % trust_val)
        return cwd

    def test_project_config_sets_the_default(self):
        # No call arg: the config position is used. A bogus value forces the
        # refusal, proving it was read (a real "self" default would proceed).
        r = engine.delegate({"task": "t", "cwd": self._cfg("nope")})
        self.assertEqual(r["status"], "refused")
        self.assertIn('"verified"', r["result_text"])
        self.assertIn('"self"', r["result_text"])

    def test_call_arg_overrides_project_config(self):
        # Valid config position "self", but an explicit bogus call arg wins.
        r = engine.delegate({"task": "t", "cwd": self._cfg("self"),
                             "trust": "nope"})
        self.assertEqual(r["status"], "refused")

    def test_no_config_no_arg_falls_back_to_self(self):
        # Neither source set -> builtin "self" (valid) -> NOT refused for trust.
        # A bare tempdir has no .qwen-delegate.json; delegate proceeds past the
        # trust gate (and later refuses on the non-git cwd, a different reason).
        r = engine.delegate({"task": "t", "cwd": tempfile.mkdtemp()})
        self.assertNotIn("Trust dial", r["result_text"])


class ReceiptTrustLine(unittest.TestCase):
    def test_trust_self_line_present(self):
        from qd import verdict
        cwd = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=cwd)
        os.environ["QWEN_DELEGATE_REGISTRY"] = os.path.join(cwd, "reg.jsonl")
        ctx = {"cwd": cwd, "guard_on": False, "preflight": False,
               "pre_status": {}, "pre_sha": "", "pre_clean": True, "peak": 0,
               "meta": {}, "timeout": 900, "approval_mode": "scoped",
               "task": "t", "verify": "v", "cum": None, "sessions": [],
               "reinjects": 0, "discards": 0, "on_compaction": "reinject",
               "session_hint": None, "trust": "self"}
        out = verdict.render("success", "s", ["attempt 1: VERIFY PASS"],
                             "HANDOFF: done", [], 3, ctx, None)
        self.assertIn("TRUST: self (L5)", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
