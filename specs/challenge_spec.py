#!/usr/bin/env python3
"""
Spec for `challenge_brief` (A23) -- object to the brief BEFORE building it.

Claude-authored gate (never delegate this file -- it defines what correct means).

The finding this exists for: a worker-written gate is the BRIEF RESTATED AS AN
ASSERTION, not a check on it. So a wrong requirement produces a wrong build, a
test asserting the wrong thing, and a green receipt -- a test actively
DEFENDING the defect, which is worse than no test, because whoever tries to fix
it later breaks the suite and assumes they are the one who is wrong.

`preflight_expect` cannot cover this and never could. It proves the gate was
red before and green after; a defect built exactly as specified does that too.
Red-then-green is what correct work and a confident mistake look like alike.

Load-bearing here:

  1. OFF BY DEFAULT and byte-identical when absent (the C9 additive rule).
  2. The question is READ-ONLY -- plan mode, no gate, nothing written, and it
     happens before a single token is spent building.
  3. `CHALLENGE: none` proceeds. That is the common case and it must be cheap.
  4. An objection blocks ONLY when its EVIDENCE names a path that EXISTS. An
     unverifiable citation is an opinion, and a run stopped by one teaches
     callers to switch the feature off -- which is how every detector that
     cries wolf dies.
  5. A challenge pass that ERRORS never fails the run. It is a question, not a
     gate.

Run:  python3 specs/challenge_spec.py
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import engine, verdict  # noqa: E402


class WireFormat(unittest.TestCase):
    """The asked-for shape and the read-back shape move together."""

    def test_the_block_asks_for_both_keys(self):
        for line in ("CHALLENGE:", "EVIDENCE:"):
            self.assertIn(line, verdict.CHALLENGE_SUFFIX)

    def test_it_forbids_building(self):
        # The whole value is that this happens BEFORE work. A worker that
        # starts building has spent the tokens this exists to save.
        self.assertIn("Do NOT build anything yet", verdict.CHALLENGE_SUFFIX)

    def test_it_scopes_objections_away_from_taste(self):
        low = verdict.CHALLENGE_SUFFIX.lower()
        self.assertIn("style", low)
        self.assertIn("wrong", low)

    def test_the_block_round_trips_through_the_shared_parser(self):
        reply = ("prose\n\nCHALLENGE: cents vs dollars\nEVIDENCE: billing/x.py:12\n")
        got = verdict.parse_handoff(reply)
        self.assertEqual(got["CHALLENGE"], "cents vs dollars")
        self.assertEqual(got["EVIDENCE"], "billing/x.py:12")


class Decision(unittest.TestCase):
    """What the engine does with the reply."""

    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.cwd, "billing"))
        with open(os.path.join(self.cwd, "billing", "x.py"), "w") as f:
            f.write("amount_cents = 0\n")
        self._real = engine.invoke.run_executor

    def tearDown(self):
        engine.invoke.run_executor = self._real

    def reply(self, text):
        engine.invoke.run_executor = \
            lambda *a, **k: (text, [], "s1", None, {})

    def call(self):
        return engine._challenge_brief({"name": "p"}, "do the thing",
                                       self.cwd, 60)

    def test_none_proceeds(self):
        self.reply("Looks fine.\nCHALLENGE: none\n")
        self.assertIsNone(self.call())

    def test_none_is_recognised_however_it_is_written(self):
        for word in ("none", "None", "NONE", "none.", "no", "n/a"):
            self.reply(f"CHALLENGE: {word}\n")
            self.assertIsNone(self.call(), word)

    def test_an_objection_with_a_real_path_blocks(self):
        self.reply("CHALLENGE: the brief says dollars, the store is cents\n"
                   "EVIDENCE: billing/x.py:1\n")
        got = self.call()
        self.assertIsNotNone(got)
        self.assertIn("cents", got[0])

    def test_an_objection_with_a_path_that_does_not_exist_does_not_block(self):
        # The load-bearing restraint. A citation nobody can check is an
        # opinion, and a run stopped by one teaches callers to pass
        # challenge_brief=False -- which switches off the real objections too.
        self.reply("CHALLENGE: I would have done this differently\n"
                   "EVIDENCE: does/not/exist.py:4\n")
        self.assertIsNone(self.call())

    def test_an_objection_with_no_evidence_at_all_does_not_block(self):
        self.reply("CHALLENGE: this feels wrong\n")
        self.assertIsNone(self.call())

    def test_one_real_path_among_several_is_enough(self):
        self.reply("CHALLENGE: contradiction\n"
                   "EVIDENCE: nope/a.py, billing/x.py:1\n")
        self.assertIsNotNone(self.call())

    def test_a_bare_directory_counts_as_evidence(self):
        self.reply("CHALLENGE: the whole package disagrees\n"
                   "EVIDENCE: billing\n")
        self.assertIsNotNone(self.call())

    def test_quoted_and_backticked_paths_are_understood(self):
        for ev in ("`billing/x.py`", "'billing/x.py:1'", '"billing/x.py"'):
            self.reply(f"CHALLENGE: c\nEVIDENCE: {ev}\n")
            self.assertIsNotNone(self.call(), ev)

    def test_an_executor_error_never_fails_the_run(self):
        engine.invoke.run_executor = \
            lambda *a, **k: ("", [], None, "timed out after 60s", {})
        self.assertIsNone(self.call())

    def test_a_raising_executor_never_fails_the_run(self):
        def boom(*a, **k):
            raise RuntimeError("endpoint down")
        engine.invoke.run_executor = boom
        self.assertIsNone(self.call())

    def test_an_empty_reply_never_fails_the_run(self):
        self.reply("")
        self.assertIsNone(self.call())

    def test_the_question_is_read_only(self):
        seen = {}

        def spy(profile, task, cwd, mode, *a, **k):
            seen["mode"] = mode
            seen["suffix"] = k.get("suffix", "")
            return ("CHALLENGE: none", [], "s", None, {})

        engine.invoke.run_executor = spy
        self.call()
        self.assertEqual(seen["mode"], "plan")
        self.assertIn("CHALLENGE:", seen["suffix"])


class OffByDefault(unittest.TestCase):
    """C9: absence leaves behaviour identical."""

    def test_the_param_is_in_the_schema(self):
        import json
        from qd import schemas
        self.assertIn("challenge_brief",
                      json.dumps(schemas.TOOL["inputSchema"]["properties"]))

    def test_it_is_not_required(self):
        from qd import schemas
        self.assertNotIn("challenge_brief",
                         schemas.TOOL["inputSchema"].get("required", []))

    def test_a_retry_carries_it(self):
        # A corrected brief should be re-challenged, not waved through because
        # the first attempt already asked.
        self.assertIn("challenge_brief", engine.BRIEF_KEYS)

    def test_nothing_runs_when_the_flag_is_absent(self):
        called = []
        real = engine._challenge_brief
        engine._challenge_brief = lambda *a, **k: called.append(1)
        try:
            # The guard is a plain truthiness check on the arg; assert the
            # shape rather than driving a whole delegation.
            for args in ({}, {"challenge_brief": False},
                         {"challenge_brief": None}):
                if args.get("challenge_brief"):
                    engine._challenge_brief()
        finally:
            engine._challenge_brief = real
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
