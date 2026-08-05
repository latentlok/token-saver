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
        """The objection only. `_challenge_brief` also returns the executor
        meta, because the pass is a real call whose tokens must land in the
        run's totals -- see TelemetryIsAccounted."""
        return engine._challenge_brief({"name": "p"}, "do the thing",
                                       self.cwd, 60)[0]

    def meta(self):
        return engine._challenge_brief({"name": "p"}, "do the thing",
                                       self.cwd, 60)[1]

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


class TelemetryIsAccounted(Decision):
    """The pass is a real executor call and now runs on EVERY delegation.

    Discarding its telemetry would put its tokens in nobody's BURN and nobody's
    COST -- the same defect class as a metric that reads 0 without measuring,
    except paid once per run rather than once ever.
    """

    def test_the_meta_comes_back_for_the_caller_to_fold_in(self):
        engine.invoke.run_executor = lambda *a, **k: (
            "CHALLENGE: none", [], "s", None,
            {"stats": {"tokens": {"prompt": 900, "completion": 20}}})
        self.assertEqual(self.meta()["stats"]["tokens"]["prompt"], 900)

    def test_its_tokens_sum_into_the_run_total(self):
        from qd.invoke import cum_zero, accum_stats
        cum = cum_zero()
        engine.invoke.run_executor = lambda *a, **k: (
            "CHALLENGE: none", [], "s", None,
            {"stats": {"tokens": {"prompt": 900, "completion": 20}}})
        accum_stats(cum, self.meta().get("stats"), attempt=False)
        self.assertEqual(cum["tokens"]["prompt"], 900)
        # ...without pretending an attempt happened. The pass is not an attempt.
        self.assertEqual(cum["attempts"], 0)

    def test_an_unverified_objection_is_recorded_even_though_it_cannot_block(self):
        # Otherwise it exists nowhere a human looks: not in the status, not in
        # the trail, and by design not in the refusal.
        self.reply("CHALLENGE: I have a bad feeling\nEVIDENCE: nope.py\n")
        self.assertIn("bad feeling", self.meta()["challenge_unverified"])

    def test_a_clean_pass_records_no_objection(self):
        self.reply("CHALLENGE: none\n")
        self.assertIsNone(self.meta().get("challenge_unverified"))


class Receipt(unittest.TestCase):
    """The pass has to be visible, or a caller cannot tell it ran."""

    def render(self, challenge):
        from qd import verdict
        from qd.invoke import cum_zero
        ctx = {"cwd": ".", "guard_on": False, "preflight": False,
               "cum": cum_zero(), "peak": 0, "challenge": challenge,
               "changed": [], "denials": [], "notes": "", "worktree": None,
               "merge": None, "graph_line": None, "refs_added": [],
               "bootstrap_note": None, "session_hint": None}
        return verdict.render("success", "s1", ["a"], "done", [], 3, ctx)

    def test_a_clean_pass_says_so(self):
        out = self.render({"ran": True, "unverified": None})
        self.assertIn("CHALLENGE: brief reviewed against the code", out)

    def test_an_unverified_objection_reaches_the_receipt(self):
        out = self.render({"ran": True,
                           "unverified": "the module looks wrong to me"})
        self.assertIn("could not cite a real path", out)
        self.assertIn("looks wrong to me", out)

    def test_a_run_that_skipped_the_pass_says_nothing(self):
        # challenge_brief: false must leave the receipt byte-identical.
        self.assertNotIn("CHALLENGE:", self.render(None))


class WarmHandoff(Decision):
    """The build resumes the challenge's session instead of starting cold.

    The builder inherits the reading the challenge just did. NOT a saving --
    measured live at +50% input tokens and +16% wall against a cold build,
    because a resumed session re-sends its whole history on every turn. The
    design predicted the opposite ("one prefill instead of two"); the numbers
    are in _challenge_brief's docstring so the wrong reasoning cannot be
    re-derived from the code.
    """

    def test_the_session_comes_back_for_the_build_to_resume(self):
        engine.invoke.run_executor = lambda *a, **k: (
            "CHALLENGE: none", [], "sess-42", None, {})
        self.assertEqual(
            engine._challenge_brief({"name": "p"}, "t", self.cwd, 60)[2],
            "sess-42")

    def test_the_handoff_line_revokes_the_do_not_build_instruction(self):
        # The challenge prompt ends with "Do NOT build anything yet", and that
        # line is in the history the build inherits. Something must retract it.
        self.assertIn("no longer", engine.CHALLENGE_CLEARED)
        self.assertIn("now building", engine.CHALLENGE_CLEARED.lower())

    def test_the_handoff_line_is_deterministic_not_generated(self):
        # A retraction the model composes for itself is not a retraction. This
        # is a server-stated fact, and it must be a constant to stay one.
        self.assertIsInstance(engine.CHALLENGE_CLEARED, str)
        self.assertTrue(engine.CHALLENGE_CLEARED.startswith("---"))
        self.assertTrue(engine.CHALLENGE_CLEARED.endswith("\n\n"))

    def test_warm_is_the_default_and_false_is_honoured(self):
        w = engine._warm_challenge
        self.assertTrue(w({}, {}))                          # nobody says -> on
        self.assertFalse(w({"challenge_warm": False}, {}))  # caller declines
        self.assertFalse(w({}, {"challenge_warm": False}))  # project declines
        # and `false` is not chained past by a truthy layer behind it
        self.assertFalse(w({"challenge_warm": False}, {"challenge_warm": True}))


class DiagnosisIsExempt(unittest.TestCase):
    """`report_dont_fix` asks why something fails -- a brief that makes no
    claim about the code, so there is nothing for the code to contradict."""

    def test_a_report_run_skips_the_pass(self):
        src = open(os.path.join(ROOT, "qd", "engine.py")).read()
        self.assertIn("if challenge and not report:", src)

    def test_report_is_resolved_before_the_challenge_runs(self):
        # Order matters: `report` is read from args well above, and reading it
        # after would make the exemption a no-op.
        src = open(os.path.join(ROOT, "qd", "engine.py")).read()
        self.assertLess(src.index('report = bool(args.get("report_dont_fix"))'),
                        src.index("if challenge and not report:"))


class DefaultOn(unittest.TestCase):
    """C9: absence leaves behaviour identical."""

    def test_the_schema_says_it_is_on_by_default(self):
        import json
        from qd import schemas
        d = json.dumps(schemas.TOOL["inputSchema"]["properties"]["challenge_brief"])
        self.assertIn("ON by default", d)

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

    def test_false_is_honoured_and_not_chained_past(self):
        # `false` is a real answer. Resolving with `or` would fall through it
        # to the config layer and silently re-enable what the caller just
        # switched off -- the bug that made this an explicit-None chain.
        for explicit, cfgv, glob, want in (
                (False, True, True, False),    # caller says no, config says yes
                (None, False, True, False),    # project says no
                (None, None, False, False),    # machine says no
                (None, None, None, True),      # nobody says -> ON
                (True, False, False, True)):   # caller says yes
            v = explicit
            if v is None:
                v = cfgv
            if v is None:
                v = glob
            if v is None:
                v = True
            self.assertEqual(bool(v), want, (explicit, cfgv, glob))


if __name__ == "__main__":
    unittest.main(verbosity=2)
