#!/usr/bin/env python3
"""
Spec for qd/queries.py -- read-only Q&A (LLD "qd/queries.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

Port of run_query with the v2 seam: the executor comes from the
profile chain (qd.profiles.resolve honoring the per-call "executor" arg), runs
through qd.invoke.run_executor, and the log record carries C5 executor/cost.
The stub profile is injected through the REAL resolution chain via
DELEGATION_EXECUTORS -- no test-only backdoors in the module.

Load-bearing:
  1. Queries run in plan mode -- read-only by construction. The stub must
     receive --approval-mode plan.
  2. Unconfigured projects WARN and proceed (reads are safe); never refuse.
  3. format='map' vs 'answer' changes the suffix, the verb, and the block
     label -- and the two suffixes are distinct non-empty ported constants.
  4. Errors (bad cwd, executor failure) return STATUS: error receipts and are
     STILL logged -- a timed-out query burned real tokens.

Public surface pinned here:
    qd.queries.run_query(args) -> str            (format='answer' | 'map')
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
body = os.environ.get("STUB_BODY") or (("A" * n) if n else "THE ANSWER BODY")
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
        os.environ["DELEGATION_EXECUTORS"] = machine
        os.environ["DELEGATION_REGISTRY"] = os.path.join(td, "reg.jsonl")

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
        p = os.path.join(self.cwd, ".delegation", "runs.jsonl")
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

    # `test_investigate_alias` was here. `queries.run_investigate` is gone: it
    # set format="map" and called run_query, and its only caller was a dispatch
    # entry (`qwen_investigate`) that no schema declared, so no conformant MCP
    # client could reach it. `test_map_format_distinct` above already pins the
    # capability through the surviving, advertised route.

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
        machine = os.environ["DELEGATION_EXECUTORS"]
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


class ResultSchema(Fixture):
    """U5.1 on the read-only side. A query has no retry loop to spend on a
    violation, so the contract is REPORTED, never enforced: the answer is
    still worth reading, and the caller has to know before it parses it."""

    SCHEMA = {"type": "object", "required": ["files"],
              "properties": {"files": {"type": "array",
                                       "items": {"type": "string"}}}}

    def body(self, text):
        os.environ["STUB_BODY"] = text

    def test_a_conforming_answer_says_so_above_the_answer(self):
        self.body('prose\n\n```json\n{"files": ["a.py"]}\n```')
        out = self.q(result_schema=self.SCHEMA)
        self.assertIn("RESULT: valid (schema)", out)
        self.assertLess(out.index("RESULT: valid"), out.index("--- answer ---"))

    def test_a_violation_is_reported_with_the_first_error(self):
        self.body('prose\n\n```json\n{"files": [1]}\n```')
        out = self.q(result_schema=self.SCHEMA)
        self.assertIn("RESULT: schema INVALID — $.files[0]: expected string",
                      out)
        self.assertIn("--- answer ---", out)      # ...and the answer survives
        self.assertIn("prose", out)

    def test_a_missing_block_is_reported_not_an_error_receipt(self):
        self.body("just prose")
        out = self.q(result_schema=self.SCHEMA)
        self.assertIn("STATUS: ok", out)
        self.assertIn("RESULT: schema INVALID", out)

    def test_the_shape_is_asked_for_in_the_prompt(self):
        self.body('```json\n{"files": []}\n```')
        self.q(result_schema=self.SCHEMA)
        task = self.argv()[2]
        self.assertIn(json.dumps(self.SCHEMA, indent=2), task)
        self.assertTrue(task.endswith("\n"))

    def test_absent_the_param_nothing_is_added(self):
        out = self.q()
        self.assertNotIn("RESULT:", out)
        self.assertTrue(self.argv()[2].endswith(queries.ANSWER_SUFFIX))

    def test_a_malformed_schema_is_ignored_not_fatal(self):
        out = self.q(result_schema=["not", "a", "schema"])
        self.assertIn("STATUS: ok", out)
        self.assertNotIn("RESULT:", out)


class ResultSchemaOutOfSubset(Fixture):
    """A contract this side cannot check is refused before the question is put.

    `validate()` honours five keywords and skips the rest, so a `minimum: 5`
    nested under `properties` is reported as satisfied by a payload of
    {"n": 1} -- and because `schema_suffix` pastes the schema into the prompt,
    the worker usually obeys it and the constraint LOOKS enforced until the day
    it does not. On this surface the damage is worse than on the delegate one:
    there is no retry loop and no gate, so the RESULT line is the caller's only
    evidence about the answer it is about to parse. "RESULT: valid (schema)"
    over an unchecked constraint is the fuel gauge reading full because it is
    disconnected (PRINCIPLES §IV).

    The check is the SAME function qd/engine.py's `_preconditions` calls.
    query never enters engine.py, and a keyword list maintained twice
    drifts on the first edit -- which is exactly the shape of bug this repo
    keeps finding: covered on one surface, silently open on the other.

    Narrowness is pinned by the class above rather than restated here: a
    schema that is not a dict at all stays non-fatal
    (test_a_malformed_schema_is_ignored_not_fatal).
    """

    BAD = {"type": "object",
           "properties": {"n": {"type": "integer", "minimum": 5}}}

    def test_a_schema_the_check_cannot_enforce_is_refused_by_keyword(self):
        # Refused, not "STATUS: error": the call is answerable, it just cannot
        # be answered honestly as written, and the caller fixes it by deleting
        # a line it can only delete if the refusal names it.
        out = self.q(result_schema=self.BAD)
        self.assertTrue(out.startswith("STATUS: refused"), out[:120])
        self.assertIn("minimum", out)

    def test_the_executor_never_ran(self):
        # A refusal that still asks the question spends the tokens it was
        # meant to save, and hands back an answer nobody can trust the shape of.
        self.q(result_schema=self.BAD)
        self.assertFalse(os.path.exists(os.path.join(self.out, "argv.json")))

    def test_nothing_is_written_to_the_leverage_log(self):
        # The inverse of test_executor_failure_is_error_receipt_and_logged: a
        # failed query IS logged because it burned real tokens. This one burned
        # none, and a ledger padded with zero-cost entries stops answering the
        # question it exists for.
        self.q(result_schema=self.BAD)
        self.assertFalse(os.path.exists(
            os.path.join(self.cwd, ".delegation", "runs.jsonl")))

    def test_a_keyword_buried_deeper_is_refused_here_too(self):
        out = self.q(result_schema={"type": "array", "items": {
            "type": "string", "format": "email"}})
        self.assertTrue(out.startswith("STATUS: refused"), out[:120])
        self.assertIn("format", out)

    def test_a_schema_inside_the_subset_is_still_answered(self):
        # The refusal must cost nothing to the callers already using this.
        os.environ["STUB_BODY"] = '```json\n{"files": ["a.py"]}\n```'
        out = self.q(result_schema={
            "type": "object", "required": ["files"],
            "properties": {"files": {"type": "array",
                                     "items": {"type": "string"}}}})
        self.assertIn("STATUS: ok", out)
        self.assertIn("RESULT: valid (schema)", out)


class LogSeam(Fixture):
    def test_c5_fields_and_query_shape(self):
        self.q()
        rec = self.log_records()[-1]
        self.assertEqual(rec["tool"], "query")
        self.assertEqual(rec["executor"], "stub")
        self.assertEqual(rec["cost_usd"], 0.0)
        self.assertEqual(rec["approval_mode"], "plan")
        self.assertTrue(rec["question"]["sha256"])

    def test_the_query_is_logged_as_a_CALL_not_only_as_a_total(self):
        # Step 8's cheap half (DESIGN §8.1). The log became heterogeneous when
        # a delegation learned to separate its challenge pass from its build
        # attempts; a query sat outside that vocabulary, so "what did we spend
        # on queries" could not be asked in the shape every other question
        # about the log uses. Its totals were recorded -- the CALL was not.
        #
        # Pinned HERE rather than against CallLog directly, because the unit is
        # not what broke: mutating the wiring in queries.py left every
        # CallLog test green. Tested logic, untested wiring, again.
        self.q()
        rec = self.log_records()[-1]
        self.assertEqual(len(rec["calls"]), 1, "the query was not logged as a call")
        self.assertEqual(rec["calls"][0]["kind"], "query")
        self.assertIn("query", rec["calls_by_kind"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
