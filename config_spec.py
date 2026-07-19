#!/usr/bin/env python3
"""
Spec for the per-project config and the retry budget (max_iterations).

Claude-authored gate (never delegate this file -- it defines what correct means).

The retry count must be user-settable so wall time can be bounded. Precedence, clamped
to [1, 10]:

    per-call arg  >  <cwd>/.qwen-delegate.json max_iterations  >  built-in default

1 = one shot (no retry). Each attempt is a full worker build, so this directly bounds
wall clock -- the reason it is a knob at all.

Run:  python3 config_spec.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402


class ProjectConfig(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="cfg-")

    def write(self, obj):
        with open(os.path.join(self.d, server.PROJECT_CONFIG), "w") as f:
            json.dump(obj, f)

    def test_absent_config_is_empty(self):
        self.assertEqual(server.project_config(self.d), {})

    def test_corrupt_config_is_empty_not_crash(self):
        with open(os.path.join(self.d, server.PROJECT_CONFIG), "w") as f:
            f.write("{not json")
        self.assertEqual(server.project_config(self.d), {})

    def test_non_dict_config_is_empty(self):
        with open(os.path.join(self.d, server.PROJECT_CONFIG), "w") as f:
            f.write("[1, 2, 3]")
        self.assertEqual(server.project_config(self.d), {})

    def test_reads_keys(self):
        self.write({"max_iterations": 2, "spec_globs": ["x_spec.*"]})
        self.assertEqual(server.project_config(self.d)["max_iterations"], 2)

    def test_spec_globs_still_reads_config(self):
        """Refactor must not break the existing spec_globs override."""
        self.write({"spec_globs": ["tests/*.py"]})
        self.assertEqual(server.spec_globs(self.d), ["tests/*.py"])

    def test_spec_globs_default_when_absent(self):
        self.assertEqual(server.spec_globs(self.d), server.DEFAULT_SPEC_GLOBS)


class ResolveMaxIter(unittest.TestCase):
    """The precedence + clamp logic, unit-level."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mi-")

    def cfg(self, n):
        with open(os.path.join(self.d, server.PROJECT_CONFIG), "w") as f:
            json.dump({"max_iterations": n}, f)

    def test_default_when_nothing_set(self):
        self.assertEqual(server.resolve_max_iter(self.d, None), server.DEFAULT_MAX_ITER)

    def test_config_beats_default(self):
        self.cfg(2)
        self.assertEqual(server.resolve_max_iter(self.d, None), 2)

    def test_arg_beats_config(self):
        self.cfg(2)
        self.assertEqual(server.resolve_max_iter(self.d, 5), 5)

    def test_clamp_high(self):
        self.assertEqual(server.resolve_max_iter(self.d, 99), 10)

    def test_clamp_low_and_zero_falls_through(self):
        # 0 is falsy -> treated as unset -> default (0 attempts is meaningless anyway)
        self.assertEqual(server.resolve_max_iter(self.d, 0), server.DEFAULT_MAX_ITER)
        self.cfg(0)
        self.assertEqual(server.resolve_max_iter(self.d, None), server.DEFAULT_MAX_ITER)


class RetryBudgetDrivesTheLoop(unittest.TestCase):
    """End-to-end: the resolved budget is the number of attempts run_qwen actually makes.
    A number that resolves correctly but doesn't drive the loop would be a silent no-op."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="retry-")
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

        self.attempts = 0
        self.vcount = 0

        def fake_invoke(*a, **k):
            self.attempts += 1
            return "out", [], "sess", None, {"peak": 1, "stats": {}, "blocked": []}

        def fake_verify(v, c):
            # distinct output each call so the gate_suspect early-bail (identical
            # output before/after) never fires and every attempt actually runs
            self.vcount += 1
            return False, f"boom {self.vcount}"

        server.invoke_qwen = fake_invoke
        server.run_verify = fake_verify

    def tearDown(self):
        server.invoke_qwen, server.run_verify = self._invoke, self._verify
        server.PROJECT_REGISTRY = self._reg

    def attempts_for(self, **args):
        self.attempts = 0
        self.vcount = 0
        server.run_qwen({"task": "t", "cwd": self.d, "verify": "false",
                         "approval_mode": "auto-edit", **args})
        return self.attempts

    def test_default_budget_is_the_attempt_count(self):
        self.assertEqual(self.attempts_for(), server.DEFAULT_MAX_ITER)

    def test_one_shot(self):
        self.assertEqual(self.attempts_for(max_iterations=1), 1)

    def test_config_default_drives_the_loop(self):
        with open(os.path.join(self.d, server.PROJECT_CONFIG), "w") as f:
            json.dump({"max_iterations": 2}, f)
        self.assertEqual(self.attempts_for(), 2)

    def test_arg_overrides_config_in_the_loop(self):
        with open(os.path.join(self.d, server.PROJECT_CONFIG), "w") as f:
            json.dump({"max_iterations": 2}, f)
        self.assertEqual(self.attempts_for(max_iterations=4), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
