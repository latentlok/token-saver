#!/usr/bin/env python3
"""
Spec for qd/queries.py -- read-only Q&A (LLD "qd/queries.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

Port of run_query/run_investigate with the v2 seam: the executor comes from the
profile chain (qd.profiles.resolve honoring the per-call "executor" arg), runs
through qd.invoke.run_executor, and the log record carries C5 executor/cost.
The stub profile is injected through the REAL resolution chain via
QWEN_DELEGATE_EXECUTORS -- no test-only backdoors in the module.

Load-bearing:
  1. Queries run in plan mode -- read-only by construction. The stub must
     receive --approval-mode plan.
  2. Unconfigured projects WARN and proceed (reads are safe); never refuse.
  3. format='map' vs 'answer' changes the suffix, the verb, and the block
     label -- and the two suffixes are distinct non-empty ported constants.
  4. Errors (bad cwd, executor failure) return STATUS: error receipts and are
     STILL logged -- a timed-out query burned real tokens.

Public surface pinned here:
    qd.queries.run_query(args) -> str
    qd.queries.run_investigate(args) -> str      (alias: format='map')
    qd.queries.ANSWER_SUFFIX, qd.queries.INVESTIGATE_SUFFIX

Run:  python3 specs/queries_spec.py
"""

import json
import os
import stat
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import queries  # noqa: E402

STUB = r"""#!/usr/bin/env python3
import json, os, sys
out = os.environ["STUB_OUT"]
open(os.path.join(out, "argv.json"), "w").write(json.dumps(sys.argv))
n = int(os.environ.get("STUB_BODY_CHARS", "0"))
body = ("A" * n) if n else "THE ANSWER BODY"
sys.stdout.write(json.dumps([
  {"type": "assistant", "message": {"usage": {"input_tokens": 15000}}},
  {"type": "result", "result": body, "session_id": "q-sess",
   "permission_denials": [],
   "stats": {"tools": {"totalCalls": 3, "totalFail": 0, "byName": {}},
             "models": {}}}]))
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        self.out = tempfile.mkdtemp()
        self.cwd = tempfile.mkdtemp()
        td = tempfile.mkdtemp()
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
                "env": {"STUB_OUT": self.out},
            }}}, f)
        os.environ["QWEN_DELEGATE_EXECUTORS"] = machine
        os.environ["QWEN_DELEGATE_REGISTRY"] = os.path.join(td, "reg.jsonl")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def q(self, **over):
        args = {"question": "how does X work?", "cwd": self.cwd,
                "executor": "stub"}
        args.update(over)
        return queries.run_query(args)

    def argv(self):
        with open(os.path.join(self.out, "argv.json")) as f:
            return json.load(f)

    def log_records(self):
        p = os.path.join(self.cwd, ".qwen-delegate", "runs.jsonl")
        with open(p) as f:
            return [json.loads(l) for l in f.read().splitlines()]


class Basics(Fixture):
    def test_ok_receipt_shape(self):
        out = self.q()
        self.assertIn("STATUS: ok", out)
        self.assertIn("SESSION: q-sess", out)
        self.assertIn("--- answer ---", out)
        self.assertIn("THE ANSWER BODY", out)

    def test_plan_mode_enforced(self):
        self.q()
        argv = self.argv()
        self.assertEqual(argv[argv.index("--approval-mode") + 1], "plan")

    def test_prompt_carries_question_focus_and_suffix(self):
        self.q(focus="auth/", question="where is login?")
        task = self.argv()[2]
        self.assertIn("QUESTION: where is login?", task)
        self.assertIn("FOCUS your reading on: auth/", task)
        self.assertTrue(task.endswith(queries.ANSWER_SUFFIX))

    def test_map_format_distinct(self):
        out = self.q(format="map")
        self.assertIn("--- map ---", out)
        task = self.argv()[2]
        self.assertTrue(task.endswith(queries.INVESTIGATE_SUFFIX))
        self.assertNotEqual(queries.ANSWER_SUFFIX, queries.INVESTIGATE_SUFFIX)
        self.assertTrue(queries.ANSWER_SUFFIX.strip())

    def test_investigate_alias(self):
        out = queries.run_investigate({"question": "map it",
                                       "cwd": self.cwd,
                                       "executor": "stub"})
        self.assertIn("--- map ---", out)

    def test_unconfigured_warns_but_proceeds(self):
        out = self.q()  # fixture cwd has no rules file
        self.assertIn("SETUP:", out)
        self.assertIn("read-only", out)
        self.assertIn("THE ANSWER BODY", out)     # proceeded anyway


class ResultCap(Fixture):
    """The cap is the thing that silently ate real answers -- pin it.

    Nothing asserted on truncation before, so a 4500-char effective cap cut four
    of six measured queries mid-sentence and no spec noticed. A truncated answer
    is indistinguishable from a complete one to the caller, which is exactly the
    silent-failure class this repo exists to catch.
    """

    def body(self, out):
        """The answer block's retained body, minus the truncation marker."""
        section = out.split("--- answer ---\n", 1)[1]
        return section.split("\n... [truncated", 1)[0]

    def test_large_answer_is_not_truncated(self):
        os.environ["STUB_BODY_CHARS"] = "40000"
        out = self.q()
        self.assertNotIn("[truncated", out)
        self.assertEqual(len(self.body(out)), 40000)

    def test_cap_is_result_cap_with_no_hidden_slack(self):
        # The call site used RESULT_CAP + 1500, so the constant did not describe
        # the behavior. Whatever RESULT_CAP says is what the caller gets.
        os.environ["STUB_BODY_CHARS"] = str(queries.RESULT_CAP + 5000)
        out = self.q()
        self.assertIn("[truncated 5000 chars]", out)
        self.assertEqual(len(self.body(out)), queries.RESULT_CAP)

    def test_cap_is_generous_enough_for_a_map(self):
        # A `map` of any real repo runs to tens of thousands of chars.
        self.assertGreaterEqual(queries.RESULT_CAP, 50000)


class Errors(Fixture):
    def test_relative_cwd_refused(self):
        out = self.q(cwd="relative/path")
        self.assertIn("STATUS: error", out)
        self.assertIn("absolute", out)

    def test_missing_cwd_refused(self):
        out = self.q(cwd="/no/such/dir-zzz")
        self.assertIn("STATUS: error", out)

    def test_executor_failure_is_error_receipt_and_logged(self):
        machine = os.environ["QWEN_DELEGATE_EXECUTORS"]
        with open(machine) as f:
            cfg = json.load(f)
        cfg["profiles"]["stub"]["argv"][0] = "/no/such/interp"
        cfg["profiles"]["stub"]["argv"][1] = "x"
        with open(machine, "w") as f:
            json.dump(cfg, f)
        out = self.q()
        self.assertIn("STATUS: error", out)
        recs = self.log_records()
        self.assertEqual(recs[-1]["status"], "error")


class LogSeam(Fixture):
    def test_c5_fields_and_query_shape(self):
        self.q()
        rec = self.log_records()[-1]
        self.assertEqual(rec["tool"], "qwen_query")
        self.assertEqual(rec["executor"], "stub")
        self.assertEqual(rec["cost_usd"], 0.0)
        self.assertEqual(rec["approval_mode"], "plan")
        self.assertTrue(rec["question"]["sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
