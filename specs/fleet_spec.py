#!/usr/bin/env python3
"""
Spec for fleet concurrency (v0.6: A13 + per-item scheduling).

Claude-authored gate (never delegate this file -- it defines what correct means).

Every executor is an OpenAI-compatible API: self-hosted vLLM and a hosted key
are the same thing to this plugin -- an argv template plus a base URL, i.e. an
executor profile. There is no local/remote distinction to model. An endpoint
serves N requests at once and says so with `parallel_max`; that single number
is the whole concurrency model.

Two v0.5.1 defects are frozen out here:

  1. `run_batch` read items[0]'s policy and applied it to the whole batch, so
     one item on a one-slot endpoint pinned every other item behind it.
  2. A13: every item ran its OWN pre-flight gate concurrently against the same
     base commit -- N full suites on one box, each starving the others toward
     GATE UNUSABLE, with the refusal blaming a gate that was fine serially.
     Items sharing a base and a gate now share the verdict.

Run:  python3 specs/fleet_spec.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import engine, profiles, server  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        self.d = tempfile.mkdtemp()
        os.environ["DELEGATION_EXECUTORS"] = os.path.join(self.d, "ex.json")
        os.environ["DELEGATION_CONFIG"] = os.path.join(self.d, "cfg.json")
        engine._preflight_forget()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        engine._preflight_forget()

    def fleet(self, endpoints, dispatch=None):
        """A machine file with two profiles on distinct endpoints."""
        with open(os.environ["DELEGATION_EXECUTORS"], "w") as f:
            json.dump({
                "default": "gpu",
                "profiles": {
                    "gpu": {"argv": ["x", "-p", "{task}"], "endpoint": "gpu"},
                    "api": {"argv": ["x", "-p", "{task}"], "endpoint": "api"},
                },
                "endpoints": endpoints,
            }, f)
        if dispatch is not None:
            with open(os.environ["DELEGATION_CONFIG"], "w") as f:
                json.dump({"dispatch": dispatch}, f)


class EndpointCapacity(Fixture):
    """`parallel_max` is the one concurrency declaration."""

    def test_capacity_is_declared_per_endpoint(self):
        # Every endpoint is an OpenAI-compatible API -- self-hosted vLLM and a
        # hosted key are the same thing here. Different endpoints simply serve
        # different numbers of requests at once, and each says so itself.
        self.fleet({"gpu": {"parallel_max": 4}, "api": {"parallel_max": 8}})
        gpu = profiles.resolve(self.d, "gpu")
        api = profiles.resolve(self.d, "api")
        self.assertEqual(gpu["endpoint_cfg"]["parallel_max"], 4)
        self.assertEqual(api["endpoint_cfg"]["parallel_max"], 8)
        self.assertEqual(gpu["dispatch"], "parallel")
        self.assertEqual(api["dispatch"], "parallel")

    def test_one_slot_is_how_an_endpoint_says_serial(self):
        # No second knob: capacity IS the policy.
        self.fleet({"gpu": {"parallel_max": 1}, "api": {"parallel_max": 8}})
        self.assertEqual(profiles.resolve(self.d, "gpu")["dispatch"], "serial")
        self.assertEqual(profiles.resolve(self.d, "api")["dispatch"], "parallel")

    def test_unset_is_serial_until_capacity_is_declared(self):
        # Declaring `parallel_max` IS the opt-in. With no endpoints section an
        # endpoint holds one slot, so out of the box everything is serial.
        self.fleet({})
        self.assertEqual(profiles.resolve(self.d, "gpu")["dispatch"], "serial")
        self.fleet({"gpu": {"parallel_max": 4}})
        self.assertEqual(profiles.resolve(self.d, "gpu")["dispatch"], "parallel")

    def test_the_global_switch_is_a_kill_switch(self):
        # Blunt and global on purpose: clamp everything for debugging a box
        # that is misbehaving. It is not the concurrency knob.
        self.fleet({"gpu": {"parallel_max": 4}, "api": {"parallel_max": 8}},
                   dispatch="serial")
        self.assertEqual(profiles.resolve(self.d, "gpu")["dispatch"], "serial")
        self.assertEqual(profiles.resolve(self.d, "api")["dispatch"], "serial")

    def test_a_typo_never_turns_concurrency_on(self):
        self.fleet({"gpu": {"parallel_max": 4}}, dispatch="yes please")
        self.assertEqual(profiles.resolve(self.d, "gpu")["dispatch"], "serial")


class PerItemScheduling(Fixture):
    """items[0] does not decide for the batch; each item queues on the
    endpoint it actually targets."""

    def _overlap_probe(self):
        """(handler, saw_overlap) -- records whether two items were ever
        inside the handler at the same moment."""
        state = {"seen": False, "inside": 0}
        lock = threading.Lock()

        def handler(args):
            with lock:
                state["inside"] += 1
                if state["inside"] > 1:
                    state["seen"] = True
            time.sleep(0.3)
            with lock:
                state["inside"] -= 1
            return f"STATUS: success\n{args['task']}"

        return handler, state

    def test_a_batch_overlaps_on_a_multi_slot_endpoint(self):
        # If the old first-item-decides branch were still here this would run
        # as a for-loop and could never overlap. worktree="auto" is required
        # and not incidental -- see the in-tree test below.
        self.fleet({"gpu": {"parallel_max": 1},
                    "api": {"parallel_max": 4}})
        handler, state = self._overlap_probe()
        out = server.run_batch(
            [{"task": "a", "cwd": self.d, "executor": "api",
              "worktree": "auto"},
             {"task": "b", "cwd": self.d, "executor": "api",
              "worktree": "auto"}], handler)
        self.assertTrue(
            state["seen"],
            "two items on a 4-slot endpoint did not overlap -- the batch is "
            "being scheduled by something other than each item's endpoint")
        self.assertIn("STATUS: success", out)

    def test_in_tree_items_still_serialize_whatever_the_endpoint_allows(self):
        # The repo lock is the other half of the story, and it must NOT be
        # relaxed by any of this: two workers editing one tree at once is
        # corruption, not throughput. Endpoint capacity buys concurrency only
        # for runs that isolate. This is why fan-out and worktree="auto" are
        # the same feature.
        self.fleet({"api": {"parallel_max": 4}})
        handler, state = self._overlap_probe()
        server.run_batch(
            [{"task": "a", "cwd": self.d, "executor": "api"},
             {"task": "b", "cwd": self.d, "executor": "api"}], handler)
        self.assertFalse(
            state["seen"],
            "two IN-TREE items overlapped -- the repo lock is not holding, "
            "and one tree is being written by two workers at once")

    def test_receipt_order_is_submission_order_not_completion_order(self):
        # Threading must not reorder the receipt: the caller reads item N of
        # the list they sent, not of the order things happened to finish.
        self.fleet({"api": {"parallel_max": 4}})

        def handler(args):
            time.sleep(0.3 if args["task"] == "first" else 0.05)
            return f"STATUS: success\n{args['task']}"

        items = [{"task": "first", "cwd": self.d, "executor": "api"},
                 {"task": "second", "cwd": self.d, "executor": "api"}]
        out = server.run_batch(items, handler)
        self.assertLess(out.index("first"), out.index("second"))

    def test_one_item_blowing_up_does_not_sink_the_batch(self):
        self.fleet({"api": {"parallel_max": 4}})

        def handler(args):
            if args["task"] == "bad":
                raise RuntimeError("boom")
            return "STATUS: success\nfine"

        out = server.run_batch(
            [{"task": "bad", "cwd": self.d, "executor": "api"},
             {"task": "ok", "cwd": self.d, "executor": "api"}], handler)
        self.assertIn("STATUS: error", out)
        self.assertIn("STATUS: success", out)


class SharedPreflight(Fixture):
    """A13: same base commit + same gate == one pre-flight, not N."""

    def setUp(self):
        super().setUp()
        self.counter = os.path.join(self.d, "runs")
        self.wt = os.path.join(self.d, "wt")
        os.makedirs(self.wt, exist_ok=True)
        self.cmd = f"printf x >> {self.counter}; true"

    def runs(self):
        try:
            with open(self.counter) as f:
                return len(f.read())
        except FileNotFoundError:
            return 0

    def test_items_sharing_a_base_and_a_gate_run_it_once(self):
        for _ in range(4):
            engine._preflight_once(self.cmd, self.wt, 30, "sha1", True)
        self.assertEqual(self.runs(), 1)

    def test_the_shared_verdict_is_the_real_one(self):
        a = engine._preflight_once(self.cmd, self.wt, 30, "sha1", True)
        b = engine._preflight_once(self.cmd, self.wt, 30, "sha1", True)
        self.assertEqual(a, b)
        self.assertTrue(a[0])                    # the gate passed

    def test_a_different_base_commit_is_a_different_question(self):
        engine._preflight_once(self.cmd, self.wt, 30, "sha1", True)
        engine._preflight_once(self.cmd, self.wt, 30, "sha2", True)
        self.assertEqual(self.runs(), 2)

    def test_a_different_gate_is_a_different_question(self):
        engine._preflight_once(self.cmd, self.wt, 30, "sha1", True)
        engine._preflight_once(f"printf y >> {self.counter}; true",
                               self.wt, 30, "sha1", True)
        self.assertEqual(self.runs(), 2)

    def test_an_in_tree_run_never_shares(self):
        # No worktree means the working tree can have moved between runs, so
        # a shared verdict would be a guess about a tree nobody re-read.
        for _ in range(3):
            engine._preflight_once(self.cmd, self.wt, 30, "sha1", False)
        self.assertEqual(self.runs(), 3)

    def test_a_timeout_is_never_cached(self):
        # A timeout says the BOX was busy, not that the gate is red. Caching
        # it would spread one item's bad luck across the whole batch -- which
        # is the exact failure (every item refused, blaming the gate) that
        # sharing the verdict exists to prevent.
        slow = f"printf x >> {self.counter}; sleep 5"
        a = engine._preflight_once(slow, self.wt, 1, "sha1", True)
        self.assertTrue(a[3])                    # timed out
        engine._preflight_once(slow, self.wt, 1, "sha1", True)
        self.assertEqual(self.runs(), 2)         # re-run, not served stale

    def test_forget_drops_the_verdict(self):
        engine._preflight_once(self.cmd, self.wt, 30, "sha1", True)
        engine._preflight_forget()
        engine._preflight_once(self.cmd, self.wt, 30, "sha1", True)
        self.assertEqual(self.runs(), 2)

    def test_concurrent_callers_still_only_run_it_once(self):
        # The lock is held ACROSS the run on purpose: waiters must wait, not
        # race. Racing is what put N suites on one box simultaneously.
        threads = [threading.Thread(
            target=engine._preflight_once,
            args=(self.cmd, self.wt, 30, "sha1", True)) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.runs(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
