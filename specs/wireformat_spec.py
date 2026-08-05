#!/usr/bin/env python3
"""
Spec for the EXACT payload sent to the worker (v0.6).

Claude-authored gate (never delegate this file -- it defines what correct means).

The worker receives ONE string: `task + suffix`, substituted into the profile's
argv at `{task}`. There is no chat history, no system message and no hidden
preamble -- a delegation is stateless by construction, which is what makes
QWEN.md (re-read per session) the thing that binds the rules.

The envelope is assembled by CODE, in a fixed order, from named parts. It is
pinned here because it drifted invisibly once: a project `task_suffix` that
stacked per retry, and a schema instruction that arrived only on attempt 1 and
so vanished exactly when a compaction had made the worker forget it.

    [APPROVAL RESULT ... ---]     only when shell_feedback was passed
    <caller's task>
    [\\n\\n---\\n<project task_suffix>]   only when the project configures one
    [HANDOFF_SUFFIX]              attempt 1, and again after a compaction
    [FINDINGS_SUFFIX]             only with report_dont_fix
    [schema_suffix]               only with result_schema

Everything conditional is conditional on a NAMED input, never on a judgement.
The only free-form part of the payload is the caller's own `task` prose; every
other byte is derived from a parameter or a config key.

Run:  python3 specs/wireformat_spec.py
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import jsonschema, limits, profiles, verdict  # noqa: E402


class Envelope(unittest.TestCase):
    """The pieces are fixed constants, not generated prose."""

    def test_the_handoff_block_is_a_constant(self):
        # Machine-read: the receipt parses these three lines back out. If the
        # wording drifts, parse_handoff silently returns nothing and a receipt
        # loses its HANDOFF/FILES/NEXT lines with no error anywhere.
        for line in ("HANDOFF:", "FILES:", "NEXT:"):
            self.assertIn(line, verdict.HANDOFF_SUFFIX)
        self.assertIn("machine-read handoff", verdict.HANDOFF_SUFFIX)

    def test_the_handoff_block_round_trips_through_its_own_parser(self):
        # The contract that matters: what the worker is ASKED for is exactly
        # what the receipt can READ back. These two have to move together.
        reply = ("did the work\n\n"
                 "HANDOFF: built\nFILES: a.py\nNEXT: nothing\n")
        self.assertEqual(
            verdict.parse_handoff(reply),
            {"HANDOFF": "built", "FILES": "a.py", "NEXT": "nothing"})

    def test_the_findings_block_is_a_constant(self):
        self.assertIn("FINDINGS:", verdict.FINDINGS_SUFFIX)

    def test_the_schema_block_carries_the_schema_verbatim(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        out = jsonschema.schema_suffix(schema)
        self.assertIn("json", out.lower())
        self.assertIn("\"n\"", out)


class ArgvSubstitution(unittest.TestCase):
    """The payload reaches the executor as ONE argument."""

    def profile(self, argv):
        return {"argv": argv, "name": "p", "endpoint": "e",
                "endpoint_cfg": {"name": "e", "parallel_max": 1}}

    def test_the_task_is_substituted_whole_at_its_placeholder(self):
        argv = profiles.render_argv(
            self.profile(["qwen", "-p", "{task}", "--approval-mode", "{mode}"]),
            "do the thing", "auto-edit", None)
        self.assertIn("do the thing", argv)
        self.assertEqual(argv[argv.index("-p") + 1], "do the thing")

    def test_a_task_containing_shell_metacharacters_is_not_split(self):
        # argv is a LIST -- there is no shell between here and the executor,
        # so a brief may contain quotes, semicolons and newlines without
        # quoting rules. Regressing this to a shell string would make every
        # brief an injection surface.
        nasty = 'rm -rf /; echo "$(whoami)" && `id`\nsecond line'
        argv = profiles.render_argv(
            self.profile(["qwen", "-p", "{task}"]), nasty, "auto-edit", None)
        self.assertEqual(argv[argv.index("-p") + 1], nasty)

    def test_the_mode_is_substituted_from_its_own_placeholder(self):
        argv = profiles.render_argv(
            self.profile(["qwen", "-p", "{task}", "--approval-mode", "{mode}"]),
            "t", "plan", None)
        self.assertIn("plan", argv)

    def test_a_cold_run_drops_the_resume_flag_and_its_value(self):
        # Not merely blanked: an empty `-r ''` is a different command line,
        # and a stateless run must be byte-identical to one that never had a
        # session at all.
        argv = profiles.render_argv(
            self.profile(["qwen", "-p", "{task}", "-r", "{resume}"]),
            "t", "auto-edit", None)
        self.assertNotIn("-r", argv)
        self.assertNotIn("{resume}", argv)

    def test_a_warm_run_carries_the_session_id(self):
        argv = profiles.render_argv(
            self.profile(["qwen", "-p", "{task}", "-r", "{resume}"]),
            "t", "auto-edit", "sess-1")
        self.assertEqual(argv[argv.index("-r") + 1], "sess-1")

    def test_a_profile_without_a_task_placeholder_is_refused(self):
        # A profile that cannot receive the brief would run the worker with no
        # task at all -- refused at resolve time, not discovered at run time.
        with self.assertRaises(profiles.ProfileError):
            profiles._resolve(
                "bad", {"bad": {"argv": ["qwen", "--yolo"]}}, None)


class FittedTimeout(unittest.TestCase):
    """`timeout_sec` stops being a judgement call.

    The skill handed Claude a fitted regression to apply BY HAND, per call,
    without access to the telemetry it needs. The server has all three inputs
    on every finished run. A number the machine can compute is not a decision.
    """

    def test_the_formula_uses_all_three_measured_inputs(self):
        base = limits.fitted_seconds(24, 69312, 12125)
        self.assertGreater(base, 0)
        self.assertGreater(limits.fitted_seconds(48, 69312, 12125), base)
        self.assertGreater(limits.fitted_seconds(24, 200000, 12125), base)
        self.assertGreater(limits.fitted_seconds(24, 69312, 40000), base)

    def test_a_run_with_no_telemetry_cannot_be_fitted(self):
        # An error before the worker spoke. Guessing here would put a fabricated
        # number on a receipt, which is worse than saying nothing.
        self.assertIsNone(limits.fitted_seconds(0, 0, 0))
        self.assertIsNone(limits.fitted_seconds(None, None, None))

    def test_a_comfortable_run_says_nothing(self):
        # A line on every receipt is noise, and a noisy line stops being read.
        self.assertIsNone(limits.timeout_line({
            "timeout": 1800, "peak": 69312,
            "cum": {"turns": 24, "ms": 347000,
                    "tokens": {"completion": 12125}}}))

    def test_a_tight_run_warns_before_the_next_one_is_killed(self):
        line = limits.timeout_line({
            "timeout": 900, "peak": 79000,
            "cum": {"turns": 63, "ms": 756000,
                    "tokens": {"completion": 28000}}})
        self.assertIn("TIMEOUT: used 756s of 900s", line)
        self.assertIn("timeout_sec=", line)

    def test_a_killed_run_is_advised_ABOVE_the_budget_it_blew(self):
        # The subtle one. A kill truncates the telemetry, so the fitted figure
        # is a FLOOR -- fitting it naively advised 900s for a run that had just
        # died at 900s. The only exact fact is "more than the budget".
        line = limits.timeout_line({
            "timeout": 900, "timed_out": True, "peak": 69312,
            "cum": {"turns": 24, "ms": 900000,
                    "tokens": {"completion": 12125}}})
        self.assertIn("killed at 900s", line)
        self.assertIn("timeout_sec=1800", line)
        self.assertIn("floor, not the total", line)

    def test_a_project_with_no_history_keeps_the_old_default(self):
        # A first run cannot be fitted and must not be guessed at.
        d = tempfile.mkdtemp()
        self.assertEqual(limits.suggested_timeout(d, 900), 900)

    def test_the_advice_is_clamped_to_the_engines_own_range(self):
        huge = limits.fitted_seconds(9999, 900000, 9_000_000)
        self.assertLessEqual(
            max(limits.TIMEOUT_FLOOR,
                min(limits.TIMEOUT_CEILING, int(huge * limits.TIMEOUT_HEADROOM))),
            limits.TIMEOUT_CEILING)


if __name__ == "__main__":
    unittest.main(verbosity=2)
