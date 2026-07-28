#!/usr/bin/env python3
"""
Spec for qd/limits.py -- the live budget over a streamed run.

Claude-authored gate (never delegate this file -- it defines what correct means).

Scope note: this file pins ONE decision, deliberately -- plus, since U4.4, the
two seams the engine now depends on. The progress writer's own behavior stays
gated by its own suite (qd/limits_qwen.py); what is pinned here is the part a
self-graded suite cannot see: the composition that lets a heartbeat ride the
same stream as the burn limit WITHOUT disarming it, and the snapshot shape C11
promises a polling caller.

THE DECISION: a burn budget counts the SUM of per-call input tokens, never the
max.

Each `assistant` record's usage.input_tokens is the context for that ONE API
call. Summing them is total prefill work -- it is what "that run burned 19M
tokens" means, and it is the number that grows quadratically in an agentic loop
because every turn re-sends everything before it. The max across records is a
different quantity entirely: peak context, which is what qd.invoke.peak_context
already reports and what the CONTEXT line warns on.

This repo has been burned by exactly this distinction before -- see
peak_context's docstring: "result reported 31,317 while true peak context was
20,285 -- a 50% overstatement." Pick max here and a 218-call run averaging 87k
of context reads as 87k against a 3M budget: the limit never fires, and the
receipt implies a guard that is silently dead. That is worse than no limit,
because BURN would look like it were watching something.

Public surface pinned here:
    qd.limits.BurnLimit(budget)      # callable: on_line(record) -> reason|None
        .total                        # cumulative input tokens seen so far
    qd.limits.record_input_tokens(record) -> int
    qd.limits.compose(*callbacks) -> on_line | None
    qd.limits.Progress(cwd, session_id=None)  # .attempt, .state, .finish()
    qd.limits.read_progress(cwd) -> dict | None

Run:  python3 specs/limits_spec.py
"""

import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import limits  # noqa: E402


def assistant(input_tokens, output_tokens=0):
    return {"type": "assistant",
            "message": {"usage": {"input_tokens": input_tokens,
                                  "output_tokens": output_tokens}}}


class TheDecision(unittest.TestCase):
    """Sum, not max. Everything else in this file is edge cases around it."""

    def test_sums_across_calls_rather_than_taking_the_max(self):
        limit = limits.BurnLimit(1_000_000)
        for rec in (assistant(80_000), assistant(85_000), assistant(90_000)):
            limit(rec)
        self.assertEqual(limit.total, 255_000)      # sum
        self.assertNotEqual(limit.total, 90_000)    # NOT the max

    def test_the_measured_runaway_trips_the_budget(self):
        # The shape that cost 19M tokens: many calls, each re-sending a large
        # accumulated context. Under a max reading this never fires.
        limit = limits.BurnLimit(3_000_000)
        fired_at = None
        for i in range(218):
            reason = limit(assistant(87_000))
            if reason and fired_at is None:
                fired_at = i + 1
        self.assertIsNotNone(fired_at, "a 218x87k run never tripped a 3M budget")
        self.assertLess(fired_at, 40, "fired far later than the budget implies")

    def test_a_long_run_under_budget_never_fires(self):
        limit = limits.BurnLimit(3_000_000)
        for _ in range(20):
            self.assertIsNone(limit(assistant(50_000)))
        self.assertEqual(limit.total, 1_000_000)


class Counting(unittest.TestCase):
    def test_result_record_does_not_double_count(self):
        # The result record carries stats totalling the whole run. Counting it
        # on top of the assistant records it summarises doubles the figure.
        limit = limits.BurnLimit(None)
        limit(assistant(10_000))
        limit(json.loads(json.dumps({
            "type": "result", "result": "done",
            "stats": {"models": {"m": {"tokens": {"prompt": 10_000}}}}})))
        self.assertEqual(limit.total, 10_000)

    def test_records_without_usage_contribute_nothing(self):
        limit = limits.BurnLimit(None)
        for rec in ({"type": "system"}, {"type": "assistant"},
                    {"type": "assistant", "message": {}},
                    {"type": "assistant", "message": {"usage": {}}}):
            limit(rec)
        self.assertEqual(limit.total, 0)

    def test_malformed_records_never_raise(self):
        limit = limits.BurnLimit(1000)
        for rec in (None, [], "text", 7, {"type": "assistant", "message": None},
                    {"type": "assistant", "message": {"usage": {"input_tokens": "x"}}}):
            limit(rec)          # must not raise
        self.assertEqual(limit.total, 0)

    def test_record_input_tokens_reads_one_record(self):
        self.assertEqual(limits.record_input_tokens(assistant(1234)), 1234)
        self.assertEqual(limits.record_input_tokens({"type": "result"}), 0)
        self.assertEqual(limits.record_input_tokens(None), 0)


class Firing(unittest.TestCase):
    def test_fires_on_reaching_the_budget_not_only_past_it(self):
        limit = limits.BurnLimit(100_000)
        self.assertIsNone(limit(assistant(99_999)))
        self.assertIsNotNone(limit(assistant(1)))     # total == budget exactly

    def test_reason_names_the_numbers(self):
        # It lands in a receipt; "limit exceeded" with no figures is unactionable.
        reason = limits.BurnLimit(100_000)(assistant(250_000))
        self.assertIsNotNone(reason)
        self.assertIn("250,000", reason)
        self.assertIn("100,000", reason)

    def test_no_budget_disables_the_limit(self):
        for budget in (None, 0):
            limit = limits.BurnLimit(budget)
            self.assertIsNone(limit(assistant(50_000_000)))
            self.assertEqual(limit.total, 50_000_000)   # still counts

    def test_keeps_reporting_once_tripped(self):
        # The stream keeps arriving until the process actually dies; a limit
        # that fires once and then goes quiet lets the tail through unwatched.
        limit = limits.BurnLimit(1000)
        self.assertIsNotNone(limit(assistant(2000)))
        self.assertIsNotNone(limit(assistant(1)))

    def test_is_usable_as_the_on_line_callback(self):
        # The seam it exists for: run_executor calls on_line(record) and stops
        # the run on a truthy return.
        import inspect
        from qd import invoke
        self.assertIn("on_line", inspect.signature(invoke.run_executor).parameters)
        limit = limits.BurnLimit(10)
        self.assertIsInstance(limit(assistant(99)), str)


class Compose(unittest.TestCase):
    """U4.4: one on_line, several observers.

    run_executor takes exactly one callback, so wiring the heartbeat means
    sharing the stream with the burn limit. The claim is that sharing costs the
    limit nothing: it still sees every record and its stop reason still reaches
    the caller."""

    def test_every_observer_sees_every_record(self):
        seen_a, seen_b = [], []
        compose = limits.compose(seen_a.append, seen_b.append)
        compose(assistant(10))
        compose(assistant(20))
        self.assertEqual(len(seen_a), 2)
        self.assertEqual(len(seen_b), 2)

    def test_the_stop_reason_survives_the_composition(self):
        limit = limits.BurnLimit(1000)
        compose = limits.compose(limit, limits.Progress(tempfile.mkdtemp()))
        self.assertIsNone(compose(assistant(10)))
        self.assertIn("Burn limit exceeded", compose(assistant(5000)))

    def test_a_stopping_observer_does_not_shortcut_the_ones_behind_it(self):
        # The heartbeat's LAST snapshot is what a poller reads after the run
        # dies; returning early on the stop would freeze it one record short.
        seen = []
        compose = limits.compose(lambda record: "stop now", seen.append)
        self.assertEqual(compose(assistant(1)), "stop now")
        self.assertEqual(len(seen), 1)

    def test_none_observers_are_skipped(self):
        seen = []
        compose = limits.compose(None, seen.append, None)
        compose(assistant(1))
        self.assertEqual(len(seen), 1)

    def test_nothing_to_watch_is_no_callback_at_all(self):
        # An inert callable would still be "someone watching" to run_executor,
        # which switches the executor to stream-json and loses the batch-only
        # stats -- paid for observing nothing.
        self.assertIsNone(limits.compose())
        self.assertIsNone(limits.compose(None, None))

    def test_a_raising_observer_cannot_disarm_the_limit_behind_it(self):
        # The stream pump catches per-on_line, not per-observer: without a
        # guard here, one buggy watcher silently turns the budget off.
        def boom(record):
            raise RuntimeError("observer bug")

        compose = limits.compose(boom, limits.BurnLimit(10))
        self.assertIn("Burn limit exceeded", compose(assistant(100)))


class SnapshotShape(unittest.TestCase):
    """C11: what a polling caller is promised in progress.json.

    `attempt` and `state` are what make the file answer "is it hung?" -- record
    counts alone cannot tell attempt 3 of 3 from an attempt 1 that wedged, and a
    run that ended leaves its last live snapshot on disk forever."""

    def setUp(self):
        self.cwd = tempfile.mkdtemp()

    def test_every_c11_key_is_present(self):
        p = limits.Progress(self.cwd, session_id="s-9")
        p(assistant(1))
        snap = limits.read_progress(self.cwd)
        for key in ("session", "records", "input_tokens", "last_type",
                    "updated", "attempt", "state"):
            self.assertIn(key, snap, f"missing key {key!r}")

    def test_attempt_is_settable_and_lands_in_the_file(self):
        p = limits.Progress(self.cwd)
        p.attempt = 3
        p(assistant(1))
        self.assertEqual(limits.read_progress(self.cwd)["attempt"], 3)

    def test_a_live_run_reads_running_and_a_finished_one_done(self):
        p = limits.Progress(self.cwd)
        p(assistant(1))
        self.assertEqual(limits.read_progress(self.cwd)["state"], "running")
        p.finish()
        self.assertEqual(limits.read_progress(self.cwd)["state"], "done")

    def test_finish_writes_even_when_no_record_ever_arrived(self):
        # A run that dies before its first record must not leave a caller
        # polling for a file that never appears.
        limits.Progress(self.cwd).finish()
        snap = limits.read_progress(self.cwd)
        self.assertEqual(snap["records"], 0)
        self.assertEqual(snap["state"], "done")

    def test_finish_never_raises(self):
        # It runs on the way out of a delegation that already has its result;
        # a failed heartbeat write must not take that result with it.
        readonly = os.path.join(tempfile.mkdtemp(), "ro")
        os.makedirs(readonly)
        os.chmod(readonly, stat.S_IRUSR)
        try:
            limits.Progress(readonly).finish()
        finally:
            os.chmod(readonly, stat.S_IRWXU)

    def test_session_can_be_learned_after_construction(self):
        # A cold run only learns its session from the first reply; a sidecar
        # whose session stays null cannot be correlated with anything.
        p = limits.Progress(self.cwd)
        p.session_id = "learned-later"
        p(assistant(1))
        self.assertEqual(limits.read_progress(self.cwd)["session"],
                         "learned-later")


if __name__ == "__main__":
    unittest.main(verbosity=1)
