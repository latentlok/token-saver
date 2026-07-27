#!/usr/bin/env python3
"""
Spec for the ASYNC qwen_delegate submit path (U5.2, C9) in qd/server.py.

Claude-authored gate (never delegate this file -- it defines what correct means).

A delegation runs for minutes. Blocking the tool call for them bought the
caller nothing a file could not give it, and cost it everything it could have
done meanwhile. So the call SUBMITS and the receipt lands on disk:

  1. The response is a submission -- run id, receipt path, heartbeat path, and
     the exact shell loop that waits for it. Four fixed lines, because a caller
     that has to guess where its result went is worse off than one that waited.
  2. A submit never blocks on a lock. The endpoint and repo guards are acquired
     INSIDE the background thread, so a busy GPU queues the RUN, not the CALL:
     a submit is an enqueue.
  3. The receipt is published temp+rename. The WATCH loop keys on the file
     EXISTING, so a file that exists must be a whole receipt.
  4. Cheap refusals -- argument shape, trust, non-repo, dirty spec, failed
     bootstrap -- come back in the RESPONSE and file nothing: nobody polls for
     a run that was never spawned. The pre-flight gate is NOT one of them; it
     can cost the whole verify budget, so it belongs to the run and its
     refusals land in the receipt like any other outcome.
  5. Every path ends in a receipt file, including the ones that raise. A run
     that dies without filing anything leaves the caller's WATCH loop spinning
     forever.
  6. The run log gains an OPEN record at submit (`status: running` + the owning
     pid), closed logically by the completion record carrying the same run_id.
  7. chain/batch submit as ONE run: per-link receipts land in `<id>.partial.md`
     as they finish, and `<id>.md` appears only when the whole thing is done --
     the final file IS the completion signal.
  8. `wait: true` is the pre-U5.2 blocking call, unchanged.

Driven with a fake engine (no worker, no subprocess) and bounded poll loops;
the wait-vs-async equivalence, which needs a real run, lives in engine_spec.

Public surface pinned here:
    qd.server.submit_delegate(args) -> str   (the qwen_delegate handler)
    qd.server.receipt_paths(cwd, run_id) -> (final, partial)
    qd.server.self_guarded(fn)

Run:  python3 specs/async_spec.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import runlog  # noqa: E402
from qd import server  # noqa: E402

GREEN = "STATUS: success\nSESSION: s-1\nbody"
CLEAN = {"refusal": None, "bootstrap_note": None}


class Fixture(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        # The guards are real (claim 2 is about them), so they resolve a
        # profile and take lock files -- both pointed at throwaway paths so the
        # spec never reads a developer's config or writes into ~/.qwen-delegate.
        os.environ["QWEN_DELEGATE_LOCKS"] = tempfile.mkdtemp()
        os.environ["QWEN_DELEGATE_EXECUTORS"] = os.path.join(
            tempfile.mkdtemp(), "absent.json")
        os.environ["QWEN_DELEGATE_REGISTRY"] = os.path.join(
            tempfile.mkdtemp(), "reg.jsonl")
        self.cwd = tempfile.mkdtemp()
        self.seen = []
        self.gates = []
        self.pending = []

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    # --- fakes ---------------------------------------------------------
    def patch_engine(self, run=None, precheck=CLEAN):
        from qd import engine
        if run is not None:
            real = engine.run
            engine.run = run
            self.addCleanup(setattr, engine, "run", real)
        if precheck is not None:
            realp = engine.precheck
            engine.precheck = lambda args: precheck
            self.addCleanup(setattr, engine, "precheck", realp)
        # LIFO: this runs BEFORE the restores above. A submitted run outlives
        # its test by design, and one that reaches the restored REAL engine
        # launches an actual executor -- which then holds the process-wide
        # endpoint slot and hangs every test after it. Ask a suite to prove
        # asynchrony and it will find every way you left the door open.
        self.addCleanup(self.drain)

    def drain(self, timeout=15):
        for gate in self.gates:
            gate.set()
        for path in self.pending:
            self.wait_file(path, timeout)

    def gate(self):
        g = threading.Event()
        self.gates.append(g)
        return g

    def recorder(self, text=GREEN, block=None):
        """A fake engine.run: records its args, optionally waits to be let go."""
        def run(args):
            self.seen.append(args)
            if block is not None:
                block.wait(20)
            return text(args) if callable(text) else text
        return run

    # --- reading the submission ----------------------------------------
    def submit(self, **over):
        args = {"task": "t", "cwd": self.cwd}
        args.update(over)
        out = server.submit_delegate(args)
        line = self.field(out, "RECEIPT")
        if line:
            self.pending.append(line.split(" — ")[0])
        return out

    def field(self, text, key):
        for line in text.splitlines():
            if line.startswith(f"{key}: "):
                return line[len(key) + 2:]
        return None

    def receipt_of(self, submission):
        return self.field(submission, "RECEIPT").split(" — ")[0]

    def partial_of(self, submission):
        return self.field(submission, "PARTIAL").split(" — ")[0]

    def wait_file(self, path, timeout=10):
        """Poll for a file, bounded. The fake engine is instant, so the timeout
        only has to outlast a loaded CI box -- it is never the wait itself."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if os.path.exists(path):
                return True
            time.sleep(0.01)
        return os.path.exists(path)

    def wait_started(self, n=1, timeout=10):
        """Wait until the fake engine has been ENTERED n times.

        Tests that hold a run open must know it is really open before they
        assert, and must let it finish before teardown puts the real engine
        back -- otherwise a straggler thread runs a real delegation.
        """
        t0 = time.time()
        while time.time() - t0 < timeout and len(self.seen) < n:
            time.sleep(0.01)
        return len(self.seen)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def log_records(self):
        path = os.path.join(self.cwd, ".qwen-delegate", "runs.jsonl")
        if not os.path.isfile(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f.read().splitlines()]


class Submission(Fixture):
    """What comes back is a receipt for the SUBMISSION, not for the work."""

    def setUp(self):
        super().setUp()
        self.patch_engine(run=self.recorder())

    def test_the_response_is_a_submission_with_all_four_lines(self):
        out = self.submit()
        self.assertTrue(out.startswith("STATUS: submitted\n"), out)
        for key in ("RUN", "RECEIPT", "HEARTBEAT", "WATCH"):
            self.assertIsNotNone(self.field(out, key), f"{key} missing:\n{out}")

    def test_the_run_id_is_the_c6_shape(self):
        run_id = self.field(self.submit(), "RUN")
        self.assertRegex(run_id, r"^r[0-9a-f]{6}$")

    def test_the_receipt_path_is_where_the_receipt_actually_lands(self):
        out = self.submit()
        path = self.receipt_of(out)
        self.assertTrue(self.wait_file(path), f"{path} never appeared")
        self.assertEqual(self.read(path), GREEN)

    def test_the_receipt_lives_under_the_self_ignoring_directory(self):
        # A receipt written anywhere else reads as the worker's own new file to
        # the next run's guards, and trips the dirty-tree precondition.
        path = self.receipt_of(self.submit())
        self.assertEqual(os.path.dirname(path),
                         os.path.join(self.cwd, ".qwen-delegate", "receipts"))
        with open(os.path.join(self.cwd, ".qwen-delegate", ".gitignore")) as f:
            self.assertEqual(f.read(), "*\n")

    def test_the_watch_line_waits_for_that_exact_file(self):
        out = self.submit()
        receipt = self.receipt_of(out)
        watch = self.field(out, "WATCH")
        self.assertEqual(watch,
                         f"until [ -f {receipt} ]; do sleep 5; done; "
                         f"cat {receipt}")

    def test_the_heartbeat_line_names_the_sidecar(self):
        self.assertEqual(self.field(self.submit(), "HEARTBEAT"),
                         os.path.join(self.cwd, ".qwen-delegate",
                                      "progress.json"))

    def test_a_lone_delegation_advertises_no_partial(self):
        self.assertIsNone(self.field(self.submit(), "PARTIAL"))

    def test_a_chain_advertises_both_files(self):
        out = self.submit(chain=[{"task": "a", "cwd": self.cwd}])
        run_id = self.field(out, "RUN")
        self.assertTrue(self.partial_of(out).endswith(f"{run_id}.partial.md"))
        self.assertTrue(self.receipt_of(out).endswith(f"{run_id}.md"))

    def test_the_task_is_not_run_in_the_calling_thread(self):
        # The whole point: the call returns before the work does.
        gate = self.gate()
        self.patch_engine(run=self.recorder(block=gate))
        t0 = time.time()
        out = self.submit()
        elapsed = time.time() - t0
        self.assertTrue(out.startswith("STATUS: submitted"))
        self.assertLess(elapsed, 5.0)
        self.wait_started()
        self.assertFalse(os.path.exists(self.receipt_of(out)))
        gate.set()
        self.assertTrue(self.wait_file(self.receipt_of(out)))


class NeverBlocksOnALock(Fixture):
    """Claim 2: a submit is an ENQUEUE. The queue is real; it just no longer
    runs down the caller's clock."""

    def blocking_guards(self):
        released = self.gate()
        log = []

        class G:
            def acquire(self):
                log.append("acquire")
                released.wait(10)

            def release(self):
                log.append("release")

        real = server._guards_for
        server._guards_for = lambda name, args: [G()]
        self.addCleanup(setattr, server, "_guards_for", real)
        return released, log

    def test_a_busy_endpoint_does_not_delay_the_submit(self):
        released, log = self.blocking_guards()
        self.patch_engine(run=self.recorder())
        t0 = time.time()
        out = self.submit()
        self.assertLess(time.time() - t0, 5.0)
        self.assertTrue(out.startswith("STATUS: submitted"))
        self.assertEqual(self.seen, [])          # nothing ran yet: it queued
        released.set()
        self.assertTrue(self.wait_file(self.receipt_of(out)))
        self.assertEqual(len(self.seen), 1)

    def test_the_guards_wrap_the_run_and_are_released_after_it(self):
        released, log = self.blocking_guards()
        released.set()
        self.patch_engine(run=self.recorder())
        out = self.submit()
        self.assertTrue(self.wait_file(self.receipt_of(out)))
        self.assertEqual(log, ["acquire", "release"])

    def test_a_delegation_handler_guards_itself_and_a_query_does_not(self):
        # _run_call must not guard the submit a second time -- and must go on
        # guarding everything that has not taken its own locks.
        from qd import queries
        tools = server._default_tools()
        self.assertTrue(getattr(tools["qwen_delegate"], "self_guarded", False))
        self.assertIs(tools["qwen_query"], queries.run_query)
        self.assertFalse(getattr(tools["qwen_query"], "self_guarded", False))

    def test_run_call_skips_guards_only_for_a_self_guarded_handler(self):
        calls = []
        real = server._guards_for
        server._guards_for = lambda name, args: calls.append(name) or []
        self.addCleanup(setattr, server, "_guards_for", real)
        responses = []
        real_respond = server.respond
        server.respond = lambda rid, *a, **kw: responses.append((a, kw))
        self.addCleanup(setattr, server, "respond", real_respond)

        server._run_call(1, "qwen_query", {}, lambda a: "sync")
        self.assertEqual(calls, ["qwen_query"])
        server._run_call(2, "qwen_delegate", {}, server.self_guarded(
            lambda a: "submitted"))
        self.assertEqual(calls, ["qwen_query"])   # not called again
        self.assertEqual(len(responses), 2)


class Delivery(Fixture):
    """Claims 3 and 5: what lands on disk, and that something always does."""

    def test_a_file_that_exists_is_a_whole_receipt(self):
        big = "STATUS: success\n" + ("x" * 200_000)
        self.patch_engine(run=self.recorder(text=big))
        path = self.receipt_of(self.submit())
        deadline = time.time() + 10
        while not os.path.exists(path) and time.time() < deadline:
            time.sleep(0.001)                     # tighter than wait_file:
        self.assertEqual(self.read(path), big)    # catch a torn write

    def test_a_raising_run_still_files_a_receipt(self):
        def boom(args):
            raise RuntimeError("worker exploded")
        self.patch_engine(run=boom)
        path = self.receipt_of(self.submit())
        self.assertTrue(self.wait_file(path), "a dead run must not leave the "
                                              "WATCH loop spinning")
        self.assertIn("STATUS: error", self.read(path))
        self.assertIn("worker exploded", self.read(path))

    def test_a_red_receipt_is_filed_like_any_other(self):
        red = "STATUS: verify_failed\nSESSION: s-9\nbody"
        self.patch_engine(run=self.recorder(text=red))
        path = self.receipt_of(self.submit())
        self.assertTrue(self.wait_file(path))
        self.assertEqual(self.read(path), red)

    def test_the_gate_refusals_are_part_of_the_run_not_the_submit(self):
        # GATE UNUSABLE costs up to the whole verify budget, so it is work:
        # it lands in the receipt file, not in the submit response.
        self.patch_engine(run=self.recorder(
            text="STATUS: refused\n\nGATE UNUSABLE: the verify command timed out"))
        out = self.submit(verify="sleep 1")
        self.assertTrue(out.startswith("STATUS: submitted"))
        path = self.receipt_of(out)
        self.assertTrue(self.wait_file(path))
        self.assertIn("GATE UNUSABLE", self.read(path))


class SynchronousRefusals(Fixture):
    """Claim 4: what the call can answer without running anything, it answers
    in the response -- and files nothing."""

    def receipts_dir(self):
        return os.path.join(self.cwd, ".qwen-delegate", "receipts")

    def assertNothingFiled(self):
        d = self.receipts_dir()
        self.assertEqual(os.listdir(d) if os.path.isdir(d) else [], [])

    def test_an_unknown_trust_dial_comes_back_in_the_response(self):
        self.patch_engine(run=self.recorder(), precheck=None)   # REAL precheck
        out = self.submit(trust="mostly")
        self.assertTrue(out.startswith("STATUS: refused"))
        self.assertIn("Trust dial \"mostly\" is unknown", out)
        self.assertEqual(self.seen, [])
        self.assertNothingFiled()

    def test_a_non_git_cwd_comes_back_in_the_response(self):
        self.patch_engine(run=self.recorder(), precheck=None)
        out = self.submit(trust="self")
        self.assertIn("STATUS:", out.splitlines()[0])
        self.assertNotIn("STATUS: submitted", out)
        self.assertEqual(self.seen, [])
        self.assertNothingFiled()

    def test_chain_and_batch_together_is_refused_and_nothing_spawns(self):
        self.patch_engine(run=self.recorder())
        out = self.submit(chain=[{"task": "a", "cwd": self.cwd}],
                          batch=[{"task": "b", "cwd": self.cwd}])
        self.assertTrue(out.startswith("STATUS: error"))
        self.assertIn("mutually exclusive", out)
        self.assertEqual(self.seen, [])
        self.assertNothingFiled()

    def test_a_hand_written_precheck_claim_is_not_believed(self):
        # The tool schema does not carry the reserved arg, but a client can
        # send anything: "I already passed the checks" must be unforgeable, or
        # it is a way around the trust and dirty-spec preconditions.
        from qd import engine
        out = server.submit_delegate(
            {"task": "t", "cwd": self.cwd, "trust": "mostly",
             engine.PRECHECK_ARG: {"refusal": None, "bootstrap_note": None,
                                   "token": "guessed"}})
        self.assertIn("Trust dial", out)
        self.assertNothingFiled()

    def test_the_precheck_never_runs_the_gate(self):
        # If it did, a submit would sit on the caller's clock for the length of
        # the verify budget -- which is the whole thing this replaced.
        from qd import engine
        repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", repo], check=True)
        marker = os.path.join(repo, "gate-ran")
        engine.precheck({"task": "t", "cwd": repo, "trust": "self",
                         "verify": f"touch {marker}"})
        self.assertFalse(os.path.exists(marker))


class RunRegistry(Fixture):
    """Claim 6: the log says what is in flight, and whose process owns it."""

    def test_the_submit_opens_a_running_record(self):
        gate = self.gate()
        self.patch_engine(run=self.recorder(block=gate))
        out = self.submit()
        run_id = self.field(out, "RUN")
        recs = [r for r in self.log_records() if r.get("status") == "running"]
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["tool"], "qwen_delegate")
        self.assertEqual(rec["run_id"], run_id)
        self.assertEqual(rec["pid"], os.getpid())
        self.assertTrue(rec["ts"])
        self.wait_started()
        gate.set()
        self.assertTrue(self.wait_file(self.receipt_of(out)))

    def test_it_reads_as_in_flight_until_a_completion_record_pairs_it(self):
        gate = self.gate()
        self.patch_engine(run=self.recorder(block=gate))
        out = self.submit()
        run_id = self.field(out, "RUN")
        flight = runlog.runs_in_flight(self.cwd)
        self.assertEqual([f["run_id"] for f in flight], [run_id])
        self.assertFalse(flight[0]["dead"])       # this process is alive
        self.wait_started()
        gate.set()
        self.assertTrue(self.wait_file(self.receipt_of(out)))
        # The verdict writes this half for a real run (ctx["run_id"] -> extra).
        runlog.write_runlog(self.cwd, {"tool": "qwen_delegate",
                                       "status": "success",
                                       "run_id": run_id})
        self.assertEqual(runlog.runs_in_flight(self.cwd), [])

    def test_the_run_id_reaches_the_delegation_as_a_reserved_arg(self):
        from qd import engine
        self.patch_engine(run=self.recorder())
        out = self.submit()
        run_id = self.field(out, "RUN")
        self.assertTrue(self.wait_file(self.receipt_of(out)))
        self.assertEqual(self.seen[0].get(engine.RUN_ID_ARG), run_id)

    def test_every_link_of_a_chain_carries_the_one_submission_id(self):
        from qd import engine
        ids = []
        self.patch_engine(run=lambda a: ids.append(a.get(engine.RUN_ID_ARG))
                          or GREEN)
        out = self.submit(chain=[{"task": "a", "cwd": self.cwd},
                                 {"task": "b", "cwd": self.cwd}])
        self.assertTrue(self.wait_file(self.receipt_of(out)))
        self.assertEqual(ids, [self.field(out, "RUN")] * 2)

    def test_the_callers_items_are_not_tagged_in_place(self):
        from qd import engine
        items = [{"task": "a", "cwd": self.cwd}]
        self.patch_engine(run=self.recorder())
        out = server.submit_delegate({"task": "t", "cwd": self.cwd,
                                      "chain": items})
        self.assertTrue(self.wait_file(self.receipt_of(out)))
        self.assertNotIn(engine.RUN_ID_ARG, items[0])

    def test_the_ledger_does_not_count_a_submission_as_a_run(self):
        # `running` is a marker, not a result: counting it would file every
        # in-flight run in the red bucket.
        gate = self.gate()
        self.patch_engine(run=self.recorder(block=gate))
        out = self.submit()
        self.assertIsNone(runlog.ledger_summary(self.cwd))
        self.wait_started()
        gate.set()
        self.assertTrue(self.wait_file(self.receipt_of(out)))


class WaitTrue(Fixture):
    """Claim 8: the escape hatch is the old call, not a new one."""

    def test_wait_true_returns_the_receipt_itself(self):
        self.patch_engine(run=self.recorder())
        out = self.submit(wait=True)
        self.assertEqual(out, GREEN)
        self.assertEqual(len(self.seen), 1)

    def test_wait_true_files_nothing(self):
        self.patch_engine(run=self.recorder())
        self.submit(wait=True)
        d = os.path.join(self.cwd, ".qwen-delegate", "receipts")
        self.assertEqual(os.listdir(d) if os.path.isdir(d) else [], [])

    def test_wait_true_holds_the_guards_for_the_whole_run(self):
        log = []

        class G:
            def acquire(self):
                log.append("acquire")

            def release(self):
                log.append("release")
        real = server._guards_for
        server._guards_for = lambda name, args: [G()]
        self.addCleanup(setattr, server, "_guards_for", real)
        self.patch_engine(run=lambda a: log.append("run") or GREEN)
        self.submit(wait=True)
        self.assertEqual(log, ["acquire", "run", "release"])

    def test_wait_true_opens_no_running_record(self):
        self.patch_engine(run=self.recorder())
        self.submit(wait=True)
        self.assertEqual(runlog.runs_in_flight(self.cwd), [])


class ChainAndBatchPartials(Fixture):
    """Claim 7: a long chain is readable while it runs, and the final file is
    the completion signal."""

    def links(self, n):
        return [{"task": f"step {i}", "cwd": self.cwd} for i in range(1, n + 1)]

    def test_a_finished_link_lands_in_the_partial_file(self):
        gate = self.gate()

        def run(args):
            self.seen.append(args)
            if len(self.seen) == 2:
                gate.wait(10)
            return GREEN
        self.patch_engine(run=run)
        out = self.submit(chain=self.links(2))
        partial = self.partial_of(out)
        self.wait_started(2)
        self.assertTrue(self.wait_file(partial))
        text = self.read(partial)
        self.assertIn("=== chain link 1/2: success ===", text)
        self.assertNotIn("link 2/2", text)
        # ...and the completion signal has NOT been given yet.
        self.assertFalse(os.path.exists(self.receipt_of(out)))
        gate.set()
        self.assertTrue(self.wait_file(self.receipt_of(out)))

    def test_the_final_receipt_holds_every_link(self):
        self.patch_engine(run=self.recorder())
        out = self.submit(chain=self.links(3))
        self.assertTrue(self.wait_file(self.receipt_of(out)))
        text = self.read(self.receipt_of(out))
        for k in (1, 2, 3):
            self.assertIn(f"=== chain link {k}/3: success ===", text)

    def test_the_partial_is_gone_once_the_receipt_lands(self):
        self.patch_engine(run=self.recorder())
        out = self.submit(chain=self.links(2))
        self.assertTrue(self.wait_file(self.receipt_of(out)))
        self.assertFalse(os.path.exists(self.partial_of(out)))

    def test_a_batch_files_its_items_the_same_way(self):
        self.patch_engine(run=self.recorder())
        out = self.submit(batch=self.links(2))
        self.assertTrue(self.wait_file(self.receipt_of(out)))
        self.assertIn("=== batch item ===", self.read(self.receipt_of(out)))

    def test_a_halted_chain_still_files_its_receipt(self):
        red = "STATUS: verify_failed\nbody"
        self.patch_engine(run=self.recorder(
            text=lambda a: red if a["task"] == "step 1" else GREEN))
        out = self.submit(chain=self.links(3))
        self.assertTrue(self.wait_file(self.receipt_of(out)))
        self.assertIn("SKIPPED (chain halted at link 1: verify_failed)",
                      self.read(self.receipt_of(out)))


class Inertness(Fixture):
    """The freeze policy: the blocking core is untouched for anyone calling it
    directly, and `on_partial` absent changes nothing."""

    def test_run_delegate_batch_is_still_the_blocking_call(self):
        self.patch_engine(run=self.recorder())
        out = server.run_delegate_batch({"task": "t", "cwd": self.cwd})
        self.assertEqual(out, GREEN)

    def test_run_chain_without_a_sink_returns_what_it_always_did(self):
        out = server.run_chain([{"task": "a"}, {"task": "b"}],
                               lambda args: GREEN)
        self.assertEqual(out, "=== chain link 1/2: success ===\n" + GREEN
                         + "\n\n=== chain link 2/2: success ===\n" + GREEN)

    def test_run_batch_without_a_sink_returns_what_it_always_did(self):
        out = server.run_batch([{"task": "a"}], lambda args: GREEN)
        self.assertEqual(out, GREEN)


if __name__ == "__main__":
    unittest.main(verbosity=1)
