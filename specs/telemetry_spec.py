#!/usr/bin/env python3
"""
Spec for per-phase run telemetry -- where a run's wall-clock GOES (PARKED D).

Claude-authored gate (never delegate this file -- it defines what correct means).

docs/archive/a92e876/PARKED.md:126-128 states the gap: "gate runs and subprocess work are not
calls in the log, so wall-clock attribution stops at the executor boundary."

The defect underneath it is arithmetic, not cosmetic. `duration_ms`
(qd/runlog.py:511) reads as a run total and is not one: it is `ctx["cum"]["ms"]`,
and `cum["ms"]` is fed by exactly two `accum_stats` call sites
(qd/engine.py:1422 challenge, qd/engine.py:1631 attempt), both of them the
executor's own self-reported duration. No end-to-end timer exists anywhere in
qd/engine.py. So the one number a reader would take for "how long did this run
take" is a partial sum wearing a total's name, and the shortfall is SILENT --
there is nothing in the record that says anything is missing.

The phases that spend real time and record none of it, all verified:

  1. the pre-flight / self-gate gate run -- `_run_verify_timed` computes `ms`
     (qd/engine.py:486), `gate_ms` reaches `ctx` (qd/engine.py:1351) and a
     receipt line, and never reaches the record;
  2. the per-attempt gate run -- qd/engine.py:1768 discards `ms` into `_`,
     once per attempt;
  3. the advisory gates -- `_run_advisory` keeps `ms` per gate
     (qd/engine.py:578) and qd/verdict.py:1106 drops it, writing only counts;
  4. the `review_brief` pass (G4) -- a WHOLE EXECUTOR CALL whose `meta` is
     thrown away by the `[0]` in the lambda at qd/engine.py:1909-1910. It burns
     the caller's tokens and records neither the tokens nor the time. This is
     the worst-attributed site in the system;
  5. the C8 prefilter subprocess (qd/engine.py:1750) -- no timing at all.

WHAT IS PINNED HERE

Five new call kinds. `ExecutorCall.kind` is open by design (qd/runlog.py:359-362)
and `CallLog.record(kind, meta, ...)` already tolerates `{"stats": {"ms": n}}`
with the token fields defaulting to 0 -- so this needs NO change to
`ExecutorCall` or `CallLog`. It is recording what already happens, not
redesigning the log:

    "gate_preflight"  one per gate run in the pre-flight block
    "gate_verify"     one per gate run inside the attempt loop
    "gate_advisory"   one per advisory gate that actually ran
    "review_brief"    the G4 executor pass -- TOKENS as well as ms
    "prefilter"       the C8 prefilter subprocess

...and the reconciliation, which is the point of the task. Three fields on every
delegate record, plus the clock they come from:

    ctx["wall_ms"]        end-to-end elapsed of delegate(), one monotonic span
    rec["wall_ms"]        the same figure, in runs.jsonl
    rec["accounted_ms"]   sum(c.ms for c in calls)
    rec["unaccounted_ms"] wall_ms - accounted_ms   -- the NAMED remainder

    accounted_ms + unaccounted_ms == wall_ms

Not clamped at zero, deliberately. A negative remainder is evidence of
double-counting, and hiding it behind a `max(0, ...)` would be this same defect
one level up -- a number that reads as measured when it was corrected.

WHY A REMAINDER AND NOT JUST MORE CALLS. A test asserting "a gate call exists"
leaves the original defect untouched: attribution would still stop somewhere,
just further along. Two tests below are the ones with teeth, and both are
COUNTS or LOWER BOUNDS rather than wall-clock equalities, because
specs/dispatch_spec.py is excluded from CI precisely for making claims a loaded
box can fail honestly:

EXCEPT for one line, and it is named here rather than left to be discovered:
`assertGreater(accounted_ms, unaccounted_ms)` at the end of
`test_a_second_spent_in_the_gate_is_a_second_the_log_can_name` compares two
MEASURED quantities, so a box loaded enough to spend more unrecorded
orchestration time than the two seconds the gate provably burns can fail it
honestly. It is kept anyway, because it is the ONLY assertion in this file that
notices a timed phase added later that is neither a gate run nor an executor
call -- the count spies below cannot see one, and the identity is satisfied by
any remainder at all (measured: an injected 0.5s untimed phase passes, 3s
fails). Its margin is 2000 ms of sleep against orchestration overhead measured
in tens of ms. That is the trade: one load-sensitive line, in exchange for the
only tripwire on the phases nobody thought to spy on.


  * `test_every_gate_run_is_exactly_one_recorded_call` and its executor twin
    spy the real call sites and compare COUNTS. A phase added later and left
    unrecorded fails these -- which is the whole difference between closing
    this gap and papering over it.
  * `test_a_second_spent_in_the_gate_is_a_second_the_log_can_name` runs a gate
    that sleeps and asserts the sleep lands in `accounted_ms`. A lower bound is
    the load-SAFE direction: a busy box can only make the sleep longer.

DELIBERATELY NOT PINNED

`gittree.git()` per invocation -- it runs hundreds of times per run and a call
per git would bloat runs.jsonl past usefulness.

Worktree acquire/release (qd/worktrees.py:83-147, :150-171). My call as gate
author, and the reasoning is the reason the remainder exists: those sites have
no timing today, so recording them means editing a second module in the same
change -- the same objection qd/core/scope.py:33-36 raises against moving the
CallLog, and house rule 6 forbids migrating a thing and changing it in one
commit. What makes leaving them out acceptable is that with a NAMED remainder
an untimed phase inflates `unaccounted_ms` visibly instead of vanishing. That
is the property being bought here: attribution may still be incomplete, but it
can no longer be incomplete in silence.

Moving `CallLog` into `RunScope` -- same rule, separate task.

The batch pre-flight cache (`_preflight_once`, qd/engine.py:520-541) shares one
gate run across N items. How a shared verdict is split between their records is
a question this spec does not answer; every run below is in-tree, where the
cache never engages (`shareable` requires a worktree).

Note for the implementer: the pre-flight runs at qd/engine.py:1233, BEFORE the
ctx holding the CallLog is built at :1298. These tests pin the RECORD, never the
moment of recording -- ordering of calls within the log is not asserted.

Refusals are out of scope: `_refusal` returns an EMPTY ctx by contract
(qd/engine.py:793-806) and run() routes them around the renderer, so a refused
run writes no record for these fields to live on.

Run:  python3 specs/telemetry_spec.py
"""

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from engine_spec import Fixture as EngineFixture  # noqa: E402
from qd import engine, invoke, verdict  # noqa: E402

# The kinds this gate defines. Named here rather than inline so the implementer
# has one list to build against and the tests below cannot drift from it.
GATE_KINDS = ("gate_preflight", "gate_verify", "gate_advisory")
EXECUTOR_KINDS = ("challenge", "attempt", "review_brief")

# What one stub executor call reports about itself. engine_spec's stub reports
# NO tokens and no duration (measured: every call comes back prompt=0, ms=0), so
# a token assertion driven by it passes whether or not the tokens were recorded
# -- vacuous exactly where this task says the hole is. This stub reports both.
CALL_PROMPT = 900
CALL_COMPLETION = 20
# Small on purpose. It is the executor's SELF-reported duration, the same
# convention "challenge" and "attempt" already use, and a real python subprocess
# spawn costs several times this -- so `accounted_ms` stays under the run's true
# elapsed and the remainder stays non-negative for honest reasons.
CALL_MS = 5

STUB = r"""#!/usr/bin/env python3
import json, os, sys
sdir = os.environ["STUB_DIR"]
steps = json.load(open(os.path.join(sdir, "steps.json")))
n_path = os.path.join(sdir, "attempt")
n = int(open(n_path).read()) if os.path.exists(n_path) else 0
open(n_path, "w").write(str(n + 1))
step = steps[min(n, len(steps) - 1)]
open(os.path.join(sdir, "task_%d.txt" % (n + 1)), "w").write(sys.argv[2])
for rel, content in (step.get("write") or {}).items():
    p = os.path.join(os.getcwd(), rel)
    if os.path.dirname(rel):
        os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(content)
# `candidates` is the -o json spelling of the output count (qd/invoke.py
# norm_tokens); bySource is what makes token_source "bySource" rather than a
# blended guess, which is the shape a real run produces.
TOK = {"prompt": __PROMPT__, "candidates": __COMPLETION__, "total": __TOTAL__}
result = {"type": "result",
          "result": step.get("result",
                             "did the work\n\nHANDOFF: ok\nFILES: none\nNEXT: nothing"),
          "session_id": step.get("sid", "t-sess-%d" % (n + 1)),
          "permission_denials": [],
          "duration_ms": __MS__,
          "stats": {"tools": {"totalCalls": 1, "totalFail": 0, "byName": {}},
                    "models": {"stub-model": {
                        "tokens": dict(TOK),
                        "bySource": {"main": {"tokens": dict(TOK)}}}}}}
sys.stdout.write(json.dumps(
    [{"type": "assistant", "message": {"usage": {"input_tokens": 25000}}},
     result]))
""".replace("__PROMPT__", str(CALL_PROMPT)) \
   .replace("__COMPLETION__", str(CALL_COMPLETION)) \
   .replace("__TOTAL__", str(CALL_PROMPT + CALL_COMPLETION)) \
   .replace("__MS__", str(CALL_MS))


class Fixture(EngineFixture):
    """engine_spec's harness, with an executor that reports what it spent."""

    def setUp(self):
        super().setUp()
        # Same path, so the profile written by the parent still points at it.
        with open(self.stub, "w") as f:
            f.write(STUB)

    def read_log(self):
        with open(os.path.join(self.cwd, ".qwen-delegate", "runs.jsonl")) as f:
            return [json.loads(line) for line in f.read().splitlines()]

    def run_and_read(self, **over):
        """One delegation, and the (ctx, record) pair it produced.

        delegate() writes nothing -- qd/verdict.py:1130 is what appends to
        runs.jsonl -- and several claims below are about the ctx and the record
        being the SAME run's account of itself, so both have to come out of one
        call rather than two runs that merely resemble each other.
        """
        d = self.delegate(**over)
        verdict.render(d["status"], d["session_id"], d["trail"],
                       d["result_text"], d["denials"], d["max_iter"],
                       d["ctx"], last_verify=d["last_verify"])
        return d["ctx"], self.read_log()[-1]

    def by_kind(self, rec):
        return rec.get("calls_by_kind") or {}

    def calls(self, rec, kind=None):
        cs = rec.get("calls") or []
        return [c for c in cs if kind is None or c["kind"] == kind]

    def fixed_gate_ms(self, ms=4321):
        """Every gate run reports the same duration, keeping its real verdict.

        An assertion about WHICH gate runs were recorded is then arithmetic
        rather than a race with the clock: 3 x 4321 can only be three recorded
        gate runs. Real timing is used where the claim IS about real time
        (`test_a_second_spent_in_the_gate...`); everywhere else it is noise.
        """
        real = engine._run_verify_timed

        def stub(cmd, cwd, timeout):
            passed, out, _, timed_out = real(cmd, cwd, timeout)
            return passed, out, ms, timed_out

        engine._run_verify_timed = stub
        self.addCleanup(setattr, engine, "_run_verify_timed", real)
        return ms

    def count_gate_runs(self):
        """A live counter of real `_run_verify_timed` invocations."""
        n = [0]
        real = engine._run_verify_timed

        def spy(cmd, cwd, timeout):
            n[0] += 1
            return real(cmd, cwd, timeout)

        engine._run_verify_timed = spy
        self.addCleanup(setattr, engine, "_run_verify_timed", real)
        return n

    def count_executor_calls(self):
        """A live counter of real executor invocations.

        BOTH bindings, because there are two. `_challenge_brief` calls
        `invoke.run_executor` (qd/engine.py:318); the attempt loop
        (qd/engine.py:1590) and the review pass (qd/engine.py:1909) call the
        name engine imported at qd/engine.py:21. Patching one module attribute
        leaves the other's calls uncounted, and the review pass -- the one this
        task exists for -- lives on the side a single patch would miss.
        """
        n = [0]
        real_e, real_i = engine.run_executor, invoke.run_executor

        def spy_e(*a, **kw):
            n[0] += 1
            return real_e(*a, **kw)

        def spy_i(*a, **kw):
            n[0] += 1
            return real_i(*a, **kw)

        engine.run_executor = spy_e
        invoke.run_executor = spy_i
        self.addCleanup(setattr, engine, "run_executor", real_e)
        self.addCleanup(setattr, invoke, "run_executor", real_i)
        return n


class PhasesAreCalls(Fixture):
    """The five phases that spend a run's time and record none of it.

    Each becomes an ordinary `ExecutorCall` under a kind that names it. Nothing
    about the log's shape changes: `kind` is open by design (qd/runlog.py:359-362)
    and `from_meta` already defaults absent token fields to 0, so a gate run is
    `record("gate_verify", {"stats": {"ms": ms}})` and nothing else.
    """

    def test_the_preflight_gate_run_is_a_call_carrying_what_it_cost(self):
        # `gate_ms` already exists in ctx and already drives a receipt line
        # (qd/verdict.py:515). What it has never done is reach the record. The
        # two must be the SAME measurement -- a recorded gate call that does not
        # agree with the number the receipt printed is a second, competing
        # account of one gate run.
        ms = self.fixed_gate_ms()
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        ctx, rec = self.run_and_read(max_iterations=1)
        pre = self.calls(rec, "gate_preflight")
        self.assertEqual(len(pre), 1, f"no preflight call in {rec.get('calls')}")
        self.assertEqual(pre[0]["ms"], ms)
        self.assertEqual(pre[0]["ms"], ctx["gate_ms"])

    def test_every_attempts_gate_run_is_its_own_call(self):
        # qd/engine.py:1768 discards this `ms` into `_`, once per attempt. On a
        # 3-attempt run that is three full gate runs -- often the most expensive
        # thing the run does -- worth nothing at all in the log.
        ms = self.fixed_gate_ms()
        self.steps([{"write": {"out.py": "wrong\n"}},
                    {"write": {"out.py": "still wrong\n"}},
                    {"write": {"out.py": "MARKER\n"}}])
        _, rec = self.run_and_read(max_iterations=3)
        self.assertEqual(len(self.calls(rec, "gate_verify")), 3)
        self.assertEqual(self.by_kind(rec)["gate_verify"]["ms"], 3 * ms)

    def test_every_advisory_gate_is_its_own_call(self):
        # `_run_advisory` computes ms per gate (qd/engine.py:578) and
        # qd/verdict.py:1106 writes only {"red": n, "of": m}. One call each,
        # not one aggregate: two advisory gates are two commands a caller chose
        # to pay for, and "the advisories cost 40s" cannot be split afterwards.
        ms = self.fixed_gate_ms()
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        _, rec = self.run_and_read(
            max_iterations=1,
            advisory_gates=[{"name": "arch", "cmd": "true"},
                            {"name": "layering", "cmd": "echo DRIFT; exit 1"}])
        self.assertEqual(len(self.calls(rec, "gate_advisory")), 2)
        self.assertEqual(self.by_kind(rec)["gate_advisory"]["ms"], 2 * ms)

    def test_a_malformed_advisory_gate_is_not_a_call(self):
        # `_run_advisory` counts and skips malformed items rather than raising.
        # A skipped gate ran no command and spent no time; logging it would put
        # a zero-ms call in the record for work that never happened.
        self.fixed_gate_ms()
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        _, rec = self.run_and_read(
            max_iterations=1,
            advisory_gates=[{"name": "arch", "cmd": "true"}, {"name": "x"}])
        self.assertEqual(len(self.calls(rec, "gate_advisory")), 1)

    def test_the_review_pass_records_the_tokens_it_burned_not_only_its_time(self):
        # The worst-attributed site in the system. `advisories.review` is a real
        # executor call -- qd/features/advisories.py:22-23 says so: "it costs a
        # whole executor pass" -- and the `[0]` at qd/engine.py:1909-1910 throws
        # its meta away. A caller who switched `review_brief` on and paid for it
        # cannot see the charge in BURN, in COST, or in the log.
        self.steps([{"write": {"out.py": "MARKER\n"}}, {}])
        _, rec = self.run_and_read(max_iterations=1, review_brief=True)
        rev = self.by_kind(rec).get("review_brief")
        self.assertIsNotNone(rev, f"the pass ran and paid: {rec.get('calls')}")
        self.assertEqual(rev["calls"], 1)
        self.assertEqual(rev["prompt"], CALL_PROMPT)
        self.assertEqual(rev["completion"], CALL_COMPLETION)
        self.assertEqual(rev["ms"], CALL_MS)

    def test_the_prefilter_subprocess_is_a_call_too(self):
        # qd/engine.py:1750 has no timing whatsoever -- no t0 wraps it. Its
        # budget is 60s, so an unrecorded prefilter can be a full minute of a
        # run's wall-clock attributed to nobody.
        self.enable_prefilter()
        self.steps([{"write": {"out.py": "MARKER\n", "calc_qwen.py": "x\n"}}])
        _, rec = self.run_and_read(max_iterations=1)
        self.assertEqual(len(self.calls(rec, "prefilter")), 1)

    def test_a_phase_that_did_not_run_is_not_a_zero_call(self):
        # The exact set, not a superset. A placeholder call for a phase the run
        # never reached would make `calls_by_kind` report work that did not
        # happen -- and 0 ms would then be indistinguishable from a phase that
        # ran and was measured at nothing, which is the reading this whole task
        # exists to remove.
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        _, rec = self.run_and_read(max_iterations=1)
        self.assertEqual(set(self.by_kind(rec)),
                         {"attempt", "gate_preflight", "gate_verify"})


class Reconciliation(Fixture):
    """The point of the task: the recorded calls must ACCOUNT FOR the run's
    wall-clock rather than silently falling short of it.

    Today `duration_ms` is the sum of two call kinds presented as a run total,
    and nothing anywhere says how much of the run it leaves out. After this,
    the record carries the whole (`wall_ms`), the part it can name
    (`accounted_ms`) and the part it cannot (`unaccounted_ms`) -- and the third
    is what makes a future unrecorded phase VISIBLE instead of absent.
    """

    def test_the_record_states_the_whole_and_the_part_it_cannot_name(self):
        self.steps([{"write": {"out.py": "wrong\n"}},
                    {"write": {"out.py": "MARKER\n"}}])
        _, rec = self.run_and_read(max_iterations=2)
        for k in ("wall_ms", "accounted_ms", "unaccounted_ms"):
            self.assertIn(k, rec)
            self.assertIsInstance(rec[k], int)
        # The identity. Integer arithmetic over one record -- true on an idle
        # box and true on a box running forty of these, which is why it is
        # stated as an accounting relationship and not a duration.
        self.assertEqual(rec["accounted_ms"] + rec["unaccounted_ms"],
                         rec["wall_ms"])
        # ...and it is a sum a reader can re-derive from the same record,
        # rather than a number they must take on faith.
        self.assertEqual(rec["accounted_ms"],
                         sum(v["ms"] for v in self.by_kind(rec).values()))
        # Every phase of one delegation is sequential, so the parts cannot
        # outrun the whole. Not clamped: a negative remainder would be real
        # evidence of double-counting, and max(0, ...) would hide it.
        #
        # "Sequential" is the whole of the argument, and it holds only because
        # every recorded call is time THIS run spent. The one phase a run can
        # hold without executing -- a batch pre-flight verdict served from
        # `_preflight_once`'s cache -- is recorded at 0 ms under its own kind
        # for exactly this reason; billed at the FIRST item's duration it would
        # put more time on a late borrower's record than the borrower's own
        # wall clock contains, and this line would go red saying so. See
        # `test_a_borrowed_preflight_verdict_is_not_billed_to_the_borrower`.
        self.assertGreaterEqual(rec["unaccounted_ms"], 0)

    def test_the_run_measures_itself_end_to_end_not_by_summing_its_calls(self):
        # There is no whole-run timer in qd/engine.py today (the only
        # time.monotonic() calls in the file are the two inside
        # _run_verify_timed). Without one, "wall_ms" could only ever be another
        # sum of the same parts, which would make the remainder identically 0
        # and the reconciliation self-satisfying.
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        ctx, rec = self.run_and_read(max_iterations=1)
        self.assertIsInstance(ctx["wall_ms"], int)
        self.assertEqual(ctx["wall_ms"], rec["wall_ms"])
        # The pre-flight happens inside the run, so the run cannot be shorter
        # than it. A containment claim, not a threshold.
        self.assertGreaterEqual(ctx["wall_ms"], ctx["gate_ms"])

    def test_duration_ms_is_a_part_and_the_record_now_says_which_part(self):
        # duration_ms keeps its meaning -- specs/runlog_spec.py:106 pins it as
        # stats["ms"] and callers read it -- but it stops being the only
        # duration in the record, which is what let it be read as a total.
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        _, rec = self.run_and_read(max_iterations=1)
        self.assertLessEqual(rec["duration_ms"], rec["accounted_ms"])
        self.assertLessEqual(rec["accounted_ms"], rec["wall_ms"])

    def test_a_second_spent_in_the_gate_is_a_second_the_log_can_name(self):
        # The defect, stated as an experiment. A gate that sleeps 1s runs twice
        # here -- once as the pre-flight (out.py absent, so it goes red) and
        # once after the attempt -- so two seconds of this run are provably
        # gate time. Today every millisecond of that lands nowhere.
        #
        # A LOWER bound, deliberately: load can only make a sleep longer, never
        # shorter, so this is the direction that survives a busy box. The slack
        # below 2000 is for sleep granularity only; the failure it is guarding
        # against is off by three orders of magnitude, not by milliseconds.
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        _, rec = self.run_and_read(max_iterations=1,
                                   verify="sleep 1; grep -q MARKER out.py")
        gate_ms = sum(v["ms"] for k, v in self.by_kind(rec).items()
                      if k in GATE_KINDS)
        self.assertGreaterEqual(gate_ms, 1900)
        self.assertGreaterEqual(rec["accounted_ms"], 1900)
        self.assertGreaterEqual(rec["wall_ms"], 1900)
        # And the whole point of the exercise: those two seconds are ATTRIBUTED,
        # not sitting in the remainder with everything else nobody measured.
        self.assertGreater(rec["accounted_ms"], rec["unaccounted_ms"])
        # duration_ms saw none of it -- which is exactly how a run that took
        # two seconds used to report ten milliseconds.
        self.assertLess(rec["duration_ms"], rec["accounted_ms"])

    def test_every_gate_run_is_exactly_one_recorded_call(self):
        # THE discriminating test for the gate side. It counts real
        # `_run_verify_timed` invocations and compares them with the gate calls
        # in the record, so a gate run added later and left unrecorded fails
        # here -- which is the difference between closing this gap and moving
        # it. Counts, not durations: nothing about it can fail under load.
        n = self.count_gate_runs()
        self.steps([{"write": {"out.py": "wrong\n"}},
                    {"write": {"out.py": "MARKER\n"}}])
        _, rec = self.run_and_read(
            max_iterations=2,
            advisory_gates=[{"name": "arch", "cmd": "true"}])
        recorded = [c for c in self.calls(rec) if c["kind"] in GATE_KINDS]
        self.assertEqual(len(recorded), n[0],
                         f"{n[0]} gate runs, {len(recorded)} in the log")
        # A tripwire, not a duplicate: pinning the number too means a gate run
        # added later cannot pass by being recorded QUIETLY either. Adding one
        # is a decision, and this is where it gets declared.
        self.assertEqual(n[0], 4)          # preflight + 2 attempts + advisory

    def test_every_executor_pass_is_exactly_one_recorded_call(self):
        # The same discriminator on the executor side, and the one that catches
        # `review_brief` today: three executor invocations, two recorded. Any
        # later pass that spends the caller's tokens without a `record()` beside
        # it fails here.
        n = self.count_executor_calls()
        self.steps([{"write": {"out.py": "MARKER\n"}}, {}])
        _, rec = self.run_and_read(max_iterations=1, challenge_brief=True,
                                   review_brief=True)
        recorded = [c for c in self.calls(rec) if c["kind"] in EXECUTOR_KINDS]
        self.assertEqual(len(recorded), n[0],
                         f"{n[0]} executor passes, {len(recorded)} in the log")
        self.assertEqual(n[0], 3)          # challenge + attempt + review

    def test_the_log_names_every_phase_the_run_ran_and_invents_none(self):
        # Total accounting, kind-blind: whatever the kinds are called, one
        # recorded call per timed phase and not one more. Stated without the
        # kind lists above so a renaming cannot make it vacuous.
        gates = self.count_gate_runs()
        execs = self.count_executor_calls()
        self.steps([{"write": {"out.py": "wrong\n"}},
                    {"write": {"out.py": "MARKER\n"}}, {}])
        _, rec = self.run_and_read(max_iterations=2, challenge_brief=True,
                                   review_brief=True,
                                   advisory_gates=[{"name": "a", "cmd": "true"}])
        self.assertEqual(len(self.calls(rec)), gates[0] + execs[0])

    def test_a_borrowed_preflight_verdict_is_not_billed_to_the_borrower(self):
        # `_preflight_once` runs ONE gate for a whole batch and hands the same
        # verdict to every item. That tuple carries the FIRST item's `ms`, and
        # an item that started later never spent it -- recorded as that item's
        # own pre-flight, a late cache hit puts more time on the record than the
        # run took and drives `unaccounted_ms` NEGATIVE. Which is this record's
        # double-counting signal firing correctly, at a defect in the recording
        # rather than in the run.
        #
        # Two real delegations rather than a hand-primed dict: the key is
        # (base sha, worktrees dir, gate), and a test that built the key itself
        # would keep passing after the key changed.
        real = engine._run_verify_timed
        expensive = [True]

        def stub(cmd, cwd, timeout):
            passed, out, ms, timed_out = real(cmd, cwd, timeout)
            if expensive[0]:
                # Only the one gate run the batch actually pays for. Ten
                # minutes: far past any wall-clock this test can produce, so a
                # borrower billed for it cannot come out non-negative by luck.
                expensive[0] = False
                return passed, out, 600000, timed_out
            return passed, out, ms, timed_out

        engine._run_verify_timed = stub
        self.addCleanup(setattr, engine, "_run_verify_timed", real)
        # The cache outlives a delegation by design -- the server forgets it
        # when a batch ends -- so this test clears up after itself.
        self.addCleanup(engine._preflight_forget)

        # The first item pays for the gate. It must FAIL: a green worktree run
        # commits, and the base sha is half the cache key.
        self.steps([{"write": {"out.py": "wrong\n"}}])
        self.delegate(max_iterations=1, worktree="auto")

        # The second item borrows the verdict.
        _, rec = self.run_and_read(max_iterations=1, worktree="auto")
        by = self.by_kind(rec)
        self.assertIn("gate_preflight_cached", by,
                      f"the verdict was not shared: {sorted(by)}")
        # Its own kind, not `gate_preflight` at 0 ms: a zero under that name
        # cannot be told from a gate that ran and was measured at nothing.
        self.assertNotIn("gate_preflight", by)
        self.assertEqual(by["gate_preflight_cached"]["ms"], 0)
        # The ten minutes belong to the item that ran the gate, and to no
        # other record.
        self.assertLess(rec["accounted_ms"], 600000)
        self.assertGreaterEqual(rec["unaccounted_ms"], 0)
        # NOTE for a later batch spec: this is the one recorded call that is not
        # a phase the run executed, so the kind-blind count in
        # `test_the_log_names_every_phase_the_run_ran_and_invents_none` is a
        # claim about a single delegation. Neither test covers a batch, and the
        # docstring above says why that question is left open.

    def test_the_three_figures_are_on_every_record_not_the_interesting_ones(self):
        # Unlike `verify_timeout_sec` and `preflight_expect`, which are written
        # only when somebody turned them (qd/verdict.py:1101-1104), these three
        # go on every record. A remainder that is absent reads as zero, and
        # "nothing unaccounted for" is the one claim a missing key must not be
        # able to make.
        self.steps([{"write": {"out.py": "MARKER\n"}}])
        _, rec = self.run_and_read(max_iterations=1)
        for k in ("wall_ms", "accounted_ms", "unaccounted_ms"):
            self.assertIn(k, rec)
        self.assertEqual(rec["accounted_ms"] + rec["unaccounted_ms"],
                         rec["wall_ms"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
