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

    def test_counts_are_summed_across_a_multi_file_suite(self):
        # A suite that runs many files prints one count per file. Reading only
        # the first compares the bar against a single file's total, so the gate
        # can demand more tests than any one file holds and never be
        # satisfiable -- self-grading then silently never works for anyone
        # whose suite is more than one file.
        write_suite(self.cwd, 3)
        with open(os.path.join(self.cwd, ".delegation.json"), "w") as f:
            f.write('{"min_tests": 7, "test_command": '
                    '"python3 -m unittest discover -s tests -t . -v; '
                    'python3 -m unittest discover -s tests -t . -v; '
                    'python3 -m unittest discover -s tests -t . -v"}')
        rc, out = run_gate(self.cwd)
        # 3 files x 3 tests = 9, which clears a bar of 7. Reading only the
        # first would see 3 and fail.
        self.assertEqual(rc, 0, out)

    def test_an_unparseable_count_still_says_so(self):
        # The summing must not turn "no count found" into a silent zero: the
        # vacuous-pass guard being INACTIVE is something the reader must see.
        with open(os.path.join(self.cwd, ".delegation.json"), "w") as f:
            f.write('{"test_command": "echo no counts here"}')
        rc, out = run_gate(self.cwd)
        self.assertEqual(rc, 0)
        self.assertIn("vacuous-pass guard inactive", out)

    # --- D1: a skip is not evidence -------------------------------------
    #
    # unittest's "Ran N tests" COUNTS the skipped ones and then prints
    # "OK (skipped=N)", so a suite of nothing but skips satisfied the floor
    # exactly. pytest prints "N skipped" with no "passed" clause at all, which
    # matched neither alternative, so the count came back empty and the script
    # took its "guard inactive" branch and exited 0 anyway. Both parsers folded
    # a skip into green -- the A19 shape, inside the guard written to catch it.

    def _skipping_suite(self, n):
        os.makedirs(os.path.join(self.cwd, "tests"), exist_ok=True)
        open(os.path.join(self.cwd, "tests", "__init__.py"), "w").close()
        body = "import unittest\n\nclass T(unittest.TestCase):\n"
        for i in range(n):
            body += (f'    @unittest.skip("no creds")\n'
                     f"    def test_{i}(self):\n        pass\n")
        with open(os.path.join(self.cwd, "tests", "test_gen.py"), "w") as f:
            f.write(body)

    def test_an_all_skipped_unittest_suite_fails(self):
        self._skipping_suite(6)              # comfortably over the floor of 5
        rc, out = run_gate(self.cwd)
        self.assertNotEqual(rc, 0, f"6 skipped tests satisfied the floor:\n{out}")
        self.assertIn("skip", out.lower())

    def test_skips_are_subtracted_from_a_mixed_unittest_suite(self):
        write_suite(self.cwd, 6)
        with open(os.path.join(self.cwd, "tests", "test_skip.py"), "w") as f:
            f.write("import unittest\n\nclass S(unittest.TestCase):\n"
                    '    @unittest.skip("x")\n    def test_s(self):\n        pass\n')
        with open(os.path.join(self.cwd, ".delegation.json"), "w") as f:
            f.write('{"min_tests": 7}')      # 7 ran, 1 skipped -> 6 real
        rc, out = run_gate(self.cwd)
        self.assertNotEqual(rc, 0, f"a skipped test counted toward the floor:\n{out}")

    def test_a_real_suite_beside_a_skip_still_passes(self):
        # The other direction, and the one that matters for not crying wolf: a
        # skip is not a failure, it just is not evidence. 6 real tests clear a
        # floor of 5 whether or not something beside them skipped.
        write_suite(self.cwd, 6)
        with open(os.path.join(self.cwd, "tests", "test_skip.py"), "w") as f:
            f.write("import unittest\n\nclass S(unittest.TestCase):\n"
                    '    @unittest.skip("x")\n    def test_s(self):\n        pass\n')
        rc, out = run_gate(self.cwd)
        self.assertEqual(rc, 0, out)

    def test_an_all_skipped_pytest_suite_fails(self):
        # pytest's summary is "5 skipped" -- no "passed" clause, so the old
        # parse found no count and fell through to "guard inactive" + exit 0.
        with open(os.path.join(self.cwd, ".delegation.json"), "w") as f:
            f.write('{"test_command": "echo \'===== 5 skipped in 0.01s =====\'"}')
        rc, out = run_gate(self.cwd)
        self.assertNotEqual(rc, 0, f"a fully-skipped pytest run passed:\n{out}")

    def test_pytest_passed_counts_are_not_double_discounted(self):
        # pytest's "N passed" already EXCLUDES skips, so subtracting them again
        # would fail a suite that genuinely passed enough tests.
        with open(os.path.join(self.cwd, ".delegation.json"), "w") as f:
            f.write('{"test_command": "echo \'6 passed, 3 skipped in 0.1s\'"}')
        rc, out = run_gate(self.cwd)
        self.assertEqual(rc, 0, out)

    def test_min_tests_config_override(self):
        write_suite(self.cwd, 6)
        with open(os.path.join(self.cwd, ".delegation.json"), "w") as f:
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
        with open(os.path.join(self.cwd, ".delegation", ".gitignore")) as f:
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
    """R3 slider resolves: call arg > project .delegation.json > machine
    ~/.delegation/config.json > builtin 'self'. A resolved 'auto' is refused,
    steering the orchestrator to pick 'self'/'verified' per task.

    Proven hermetically through the refusal branch (which runs before git/worker):
    a position reaches validation only if that source was consulted. "Trust dial"
    fingerprints the unknown-value refusal; "criticality" the 'auto' refusal.
    """

    def setUp(self):
        # Point the machine config at a nonexistent file so the global tier is
        # empty unless a test sets it -- never the real ~/.delegation/config.json.
        self._saved = os.environ.get("DELEGATION_CONFIG")
        os.environ["DELEGATION_CONFIG"] = os.path.join(
            tempfile.mkdtemp(), "none.json")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("DELEGATION_CONFIG", None)
        else:
            os.environ["DELEGATION_CONFIG"] = self._saved

    def _proj(self, trust_val):
        cwd = tempfile.mkdtemp()
        with open(os.path.join(cwd, ".delegation.json"), "w") as f:
            f.write('{"trust": "%s"}' % trust_val)
        return cwd

    def _set_global(self, trust_val):
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(p, "w") as f:
            f.write('{"trust": "%s"}' % trust_val)
        os.environ["DELEGATION_CONFIG"] = p

    # --- project tier ---
    def test_project_config_sets_the_default(self):
        # No call arg: the project position is used. A bogus value forces the
        # refusal, proving it was read (a real "self" default would proceed).
        r = engine.delegate({"task": "t", "cwd": self._proj("nope")})
        self.assertEqual(r["status"], "refused")
        self.assertIn("Trust dial", r["result_text"])

    def test_call_arg_overrides_project_config(self):
        # Valid project position "self", but an explicit bogus call arg wins.
        r = engine.delegate({"task": "t", "cwd": self._proj("self"),
                             "trust": "nope"})
        self.assertEqual(r["status"], "refused")
        self.assertIn("Trust dial", r["result_text"])

    # --- machine (global) tier ---
    def test_global_config_sets_the_default(self):
        # No call arg, no project file: the machine position is used.
        self._set_global("nope")
        r = engine.delegate({"task": "t", "cwd": tempfile.mkdtemp()})
        self.assertEqual(r["status"], "refused")
        self.assertIn("Trust dial", r["result_text"])

    def test_project_overrides_global(self):
        # Global "nope" would refuse; project "self" wins -> past the trust gate.
        self._set_global("nope")
        r = engine.delegate({"task": "t", "cwd": self._proj("self")})
        self.assertNotIn("Trust dial", r["result_text"])
        self.assertNotIn("criticality", r["result_text"])

    def test_no_config_no_arg_falls_back_to_self(self):
        # Nothing set -> builtin "self" (valid) -> not refused for trust (it later
        # refuses on the non-git cwd, a different reason).
        r = engine.delegate({"task": "t", "cwd": tempfile.mkdtemp()})
        self.assertNotIn("Trust dial", r["result_text"])
        self.assertNotIn("criticality", r["result_text"])

    # --- auto ---
    def test_auto_default_refuses_bare_call_asking_for_a_choice(self):
        self._set_global("auto")
        r = engine.delegate({"task": "t", "cwd": tempfile.mkdtemp()})
        self.assertEqual(r["status"], "refused")
        self.assertIn("criticality", r["result_text"])
        self.assertIn("verified", r["result_text"])
        self.assertIn("self", r["result_text"])

    def test_project_auto_also_refuses(self):
        r = engine.delegate({"task": "t", "cwd": self._proj("auto")})
        self.assertEqual(r["status"], "refused")
        self.assertIn("criticality", r["result_text"])

    def test_auto_is_overridden_by_explicit_call_arg(self):
        # Under an auto default a concrete per-call trust proceeds -- no auto
        # refusal (it later refuses on the non-git cwd instead).
        self._set_global("auto")
        r = engine.delegate({"task": "t", "cwd": tempfile.mkdtemp(),
                             "trust": "self"})
        self.assertNotIn("criticality", r["result_text"])


class ReceiptTrustLine(unittest.TestCase):
    def test_trust_self_line_present(self):
        from qd import verdict
        cwd = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=cwd)
        os.environ["DELEGATION_REGISTRY"] = os.path.join(cwd, "reg.jsonl")
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
