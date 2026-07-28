#!/usr/bin/env python3
"""
Spec for qd.server.run_chain -- dependent multi-step delegation (U4.1, C9).

Claude-authored gate (never delegate this file -- it defines what correct means).

A chain is the DEPENDENT counterpart of `batch`: link 2 builds on the tree link
1 left behind. Everything below follows from that one fact:

  1. Order is the submission order and the execution is always serial -- never
     the dispatch-policy branch batch takes, whose whole point is that its items
     do not depend on each other.
  2. The first link that does not come back green HALTS the rest. Continuing is
     what makes a long chain expensive: N more delegations against a tree whose
     premise already broke, each one producing a receipt somebody must read.
     Green means `success` or `success_but_preflight_passed` -- a demoted pass
     still moved the tree the next link builds on (U3.2, decision 4).
  3. Skipped links still appear, as one line each, so the caller can see where
     the chain stopped without diffing what it sent against what came back.
  4. Guards are taken and RELEASED per link: a chain holds one endpoint slot at
     a time, not one for its whole length.
  5. `chain` + `batch` in one call is refused by name, with nothing run.

Driven with a fake handler returning canned receipts: no worker, no
subprocesses, no wall-clock waits (the concurrency claims that need real time
live in dispatch_spec, which CI skips for exactly that reason).

Public surface pinned here:
    qd.server.run_chain(items, handler) -> str
    qd.server.run_delegate_batch(args)  -> str   (chain/batch routing)

Run:  python3 specs/chain_spec.py
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import server  # noqa: E402
from qd.engine import CHAIN_ARG  # noqa: E402

GREEN = "STATUS: success\nSESSION: s-1\nbody"
RED = "STATUS: verify_failed\nSESSION: s-2\nbody"


class Fixture(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        # The guards are REAL here (that is half the claim), and they resolve a
        # profile and take a lock file. Both are pointed at throwaway paths so
        # the spec never reads a developer's machine config or writes into
        # ~/.qwen-delegate.
        os.environ["QWEN_DELEGATE_LOCKS"] = tempfile.mkdtemp()
        os.environ["QWEN_DELEGATE_EXECUTORS"] = os.path.join(
            tempfile.mkdtemp(), "absent.json")
        self.cwd = tempfile.mkdtemp()
        self.seen = []

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def items(self, n):
        return [{"task": f"step {i}", "cwd": self.cwd} for i in range(1, n + 1)]

    def handler(self, *canned):
        """Fake delegate: records the args it was called with, returns the next
        canned receipt (an Exception is raised instead of returned)."""
        queue = list(canned)

        def h(args):
            self.seen.append(args)
            text = queue.pop(0) if queue else GREEN
            if isinstance(text, Exception):
                raise text
            return text

        return h


class Order(Fixture):
    def test_links_run_in_submission_order(self):
        server.run_chain(self.items(3), self.handler(GREEN, GREEN, GREEN))
        self.assertEqual([a["task"] for a in self.seen],
                         ["step 1", "step 2", "step 3"])

    def test_every_link_is_separated_and_numbered_with_its_status(self):
        out = server.run_chain(self.items(2), self.handler(GREEN, RED))
        self.assertIn("=== chain link 1/2: success ===", out)
        self.assertIn("=== chain link 2/2: verify_failed ===", out)
        self.assertLess(out.index("link 1/2"), out.index("link 2/2"))

    def test_each_receipt_follows_its_own_separator(self):
        out = server.run_chain(self.items(1), self.handler(GREEN))
        self.assertEqual(out, "=== chain link 1/1: success ===\n" + GREEN)

    def test_position_is_injected_for_the_run_log(self):
        server.run_chain(self.items(2), self.handler(GREEN, GREEN))
        self.assertEqual([a[CHAIN_ARG] for a in self.seen],
                         [{"pos": 1, "of": 2}, {"pos": 2, "of": 2}])

    def test_the_callers_items_are_not_mutated(self):
        items = self.items(2)
        server.run_chain(items, self.handler(GREEN, GREEN))
        for item in items:
            self.assertNotIn(CHAIN_ARG, item)

    def test_an_empty_chain_is_empty_not_an_error(self):
        self.assertEqual(server.run_chain([], self.handler()), "")


class HaltOnRed(Fixture):
    """The load-bearing claim: a broken link stops the chain."""

    def test_a_red_link_stops_the_ones_after_it(self):
        out = server.run_chain(self.items(3), self.handler(GREEN, RED, GREEN))
        self.assertEqual([a["task"] for a in self.seen], ["step 1", "step 2"])
        self.assertIn("SKIPPED (chain halted at link 2: verify_failed)", out)

    def test_a_skipped_link_is_one_line_not_a_receipt(self):
        out = server.run_chain(self.items(4), self.handler(RED))
        skipped = [l for l in out.splitlines() if l.startswith("SKIPPED")]
        self.assertEqual(len(skipped), 3)
        for line in skipped:
            self.assertEqual(line,
                             "SKIPPED (chain halted at link 1: verify_failed)")

    def test_a_demoted_pass_is_green_and_the_chain_continues(self):
        # The preflight-passed run still moved the tree link 2 builds on.
        out = server.run_chain(
            self.items(2),
            self.handler("STATUS: success_but_preflight_passed\nbody", GREEN))
        self.assertEqual(len(self.seen), 2)
        self.assertNotIn("SKIPPED", out)

    def test_a_refusal_halts_like_any_other_non_success(self):
        out = server.run_chain(self.items(2),
                               self.handler("STATUS: refused\nGATE UNUSABLE:"))
        self.assertEqual(len(self.seen), 1)
        self.assertIn("SKIPPED (chain halted at link 1: refused)", out)

    def test_a_receipt_without_a_status_line_halts_rather_than_reads_past(self):
        # Nothing here wrote that receipt, so nothing here can vouch for it.
        out = server.run_chain(self.items(2), self.handler("(garbage)"))
        self.assertEqual(len(self.seen), 1)
        self.assertIn("halted at link 1: unknown", out)

    def test_a_raising_handler_becomes_an_error_receipt_and_halts(self):
        out = server.run_chain(self.items(3),
                               self.handler(GREEN, RuntimeError("boom")))
        self.assertEqual(len(self.seen), 2)
        self.assertIn("STATUS: error", out)
        self.assertIn("boom", out)
        self.assertIn("halted at link 2: error", out)


class Guards(Fixture):
    """Per-link acquire/release through the same machinery batch uses."""

    def record(self):
        log = []
        real = server._guards_for

        class G:
            def __init__(self, pos):
                self.pos = pos

            def acquire(self):
                log.append(("acquire", self.pos))

            def release(self):
                log.append(("release", self.pos))

        def fake(name, args):
            pos = (args.get(CHAIN_ARG) or {}).get("pos")
            log.append(("guards_for", name, pos))
            return [G(pos)]

        server._guards_for = fake
        self.addCleanup(setattr, server, "_guards_for", real)
        return log

    def test_each_link_takes_and_releases_its_own_guards(self):
        log = self.record()

        def h(args):
            log.append(("handler", args[CHAIN_ARG]["pos"]))
            return GREEN

        server.run_chain(self.items(2), h)
        self.assertEqual(log, [
            ("guards_for", "qwen_delegate", 1), ("acquire", 1),
            ("handler", 1), ("release", 1),
            ("guards_for", "qwen_delegate", 2), ("acquire", 2),
            ("handler", 2), ("release", 2),
        ])

    def test_a_skipped_link_takes_no_slot(self):
        log = self.record()
        server.run_chain(self.items(3), self.handler(RED))
        self.assertEqual([e for e in log if e[0] == "acquire"], [("acquire", 1)])

    def test_guards_are_released_when_the_handler_raises(self):
        log = self.record()
        server.run_chain(self.items(1), self.handler(RuntimeError("boom")))
        self.assertIn(("release", 1), log)


class Routing(Fixture):
    """run_delegate_batch picks the mode; both modes at once is refused."""

    def spy_engine(self):
        from qd import engine
        calls = []
        real = engine.run
        engine.run = lambda args: calls.append(args) or GREEN
        self.addCleanup(setattr, engine, "run", real)
        return calls

    def test_chain_routes_to_the_serial_path(self):
        calls = self.spy_engine()
        out = server.run_delegate_batch({"chain": self.items(2)})
        self.assertEqual(len(calls), 2)
        self.assertIn("=== chain link 2/2:", out)

    def test_batch_still_routes_to_batch(self):
        calls = self.spy_engine()
        out = server.run_delegate_batch({"batch": self.items(2)})
        self.assertEqual(len(calls), 2)
        self.assertIn("=== batch item ===", out)
        self.assertNotIn("chain link", out)

    def test_both_at_once_is_refused_by_name_and_nothing_runs(self):
        calls = self.spy_engine()
        out = server.run_delegate_batch({"chain": self.items(2),
                                         "batch": self.items(2)})
        self.assertEqual(calls, [])
        self.assertTrue(out.startswith("STATUS: error"))
        self.assertIn("`chain` and `batch` are mutually exclusive", out)
        self.assertIn("Nothing was run", out)

    def test_neither_is_still_one_delegation(self):
        calls = self.spy_engine()
        out = server.run_delegate_batch({"task": "t", "cwd": self.cwd})
        self.assertEqual(len(calls), 1)
        self.assertEqual(out, GREEN)
        self.assertNotIn(CHAIN_ARG, calls[0])


if __name__ == "__main__":
    unittest.main(verbosity=1)
