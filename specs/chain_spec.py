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


class BatchOfChains(Fixture):
    """N independent pipelines in one call, each internally ordered.

    `chain` + `batch` at the CALL level stays refused -- one call cannot be
    both ordered and unordered. A batch OF chains is not that ambiguity, and
    without it N pipelines cost N separate submits.
    """

    def chained(self, n_items, links=2):
        return [{"cwd": self.cwd,
                 "chain": [{"task": f"p{i}s{j}"} for j in range(1, links + 1)]}
                for i in range(1, n_items + 1)]

    def test_items_are_chains_and_links_stay_ordered(self):
        from qd import engine
        seen = []
        real = engine.run
        engine.run = lambda a: (seen.append(a["task"]), GREEN)[1]
        try:
            out = server.run_delegate_batch(
                {"cwd": self.cwd, "batch": self.chained(2)})
        finally:
            engine.run = real
        # Both pipelines ran, and each one's links kept their order.
        self.assertEqual(sorted(seen), ["p1s1", "p1s2", "p2s1", "p2s2"])
        self.assertLess(seen.index("p1s1"), seen.index("p1s2"))
        self.assertLess(seen.index("p2s1"), seen.index("p2s2"))
        self.assertEqual(out.count("=== chain link 1/2:"), 2)

    def test_a_red_link_halts_only_its_own_pipeline(self):
        from qd import engine
        real = engine.run
        engine.run = lambda a: RED if a["task"] == "p1s1" else GREEN
        try:
            out = server.run_delegate_batch(
                {"cwd": self.cwd, "batch": self.chained(2)})
        finally:
            engine.run = real
        # p1 halted at link 1; p2 is untouched by it -- that is the whole
        # point of the items being independent.
        self.assertIn("SKIPPED (chain halted at link 1", out)
        self.assertIn("=== chain link 2/2: success ===", out)

    def test_a_chain_item_takes_no_batch_level_guard(self):
        # The correctness point: run_chain takes a guard per LINK. A guard
        # taken here as well would have the item holding the endpoint slot its
        # own first link then waits for -- a deadlock on a one-slot endpoint,
        # not a slowdown.
        log = []
        real = server._guards_for

        class G:
            def acquire(self): log.append("acquire")
            def release(self): log.append("release")

        server._guards_for = lambda name, args: [G()]
        realrun = None
        try:
            from qd import engine
            realrun = engine.run
            engine.run = lambda a: GREEN
            server.run_batch(self.chained(1, links=2), server._batch_item)
        finally:
            server._guards_for = real
            if realrun:
                from qd import engine
                engine.run = realrun
        # Two links -> two acquire/release pairs, not three.
        self.assertEqual(log.count("acquire"), 2)

    def test_a_plain_item_still_takes_its_guard(self):
        log = []
        real = server._guards_for

        class G:
            def acquire(self): log.append("acquire")
            def release(self): log.append("release")

        server._guards_for = lambda name, args: [G()]
        try:
            from qd import engine
            realrun = engine.run
            engine.run = lambda a: GREEN
            try:
                server.run_batch([{"cwd": self.cwd, "task": "solo"}],
                                 server._batch_item)
            finally:
                engine.run = realrun
        finally:
            server._guards_for = real
        self.assertEqual(log, ["acquire", "release"])

    def test_nesting_is_one_level(self):
        out = server._batch_item({"cwd": self.cwd, "batch": [{"task": "x"}]})
        self.assertIn("STATUS: error", out)
        self.assertIn("nesting is one level", out)

    def test_call_level_chain_plus_batch_is_still_refused(self):
        out = server.run_delegate_batch(
            {"cwd": self.cwd, "chain": [{"task": "a"}],
             "batch": [{"task": "b"}]})
        self.assertIn("mutually exclusive", out)


class SharedWorktree(Fixture):
    """One container for the whole chain -- the dependency the shape promises.

    Before this, every link called `worktrees.acquire` for itself and, on
    success, committed to its OWN branch without merging back. So under
    `worktree: "auto"` link 2 opened a clean checkout of HEAD holding none of
    link 1's files, and run_chain's own "link 2 builds on link 1's tree" was
    true only under `"off"` -- the mode that takes the repo lock and serializes
    every concurrent chain. Isolation XOR dependency, pick one.
    """

    def repo(self):
        import subprocess
        d = tempfile.mkdtemp()
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", d] + a, capture_output=True)
        with open(os.path.join(d, "seed.txt"), "w") as f:
            f.write("seed\n")
        subprocess.run(["git", "-C", d, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", d, "commit", "-qm", "init"],
                       capture_output=True)
        with open(os.path.join(d, ".qwen-delegate.json"), "w") as f:
            f.write('{"worktree": "auto"}')
        return d

    def test_every_link_is_lent_the_same_tree(self):
        from qd.engine import WT_ARG
        d = self.repo()
        items = [{"task": f"s{i}", "cwd": d} for i in (1, 2, 3)]
        server.run_chain(items, self.handler(GREEN, GREEN, GREEN))
        lent = [a.get(WT_ARG) for a in self.seen]
        self.assertTrue(all(lent), "a link ran without the chain's tree")
        self.assertEqual(len({w["path"] for w in lent}), 1,
                         "links did not share one container")
        self.assertNotEqual(lent[0]["path"], d, "the chain ran in the main tree")

    def test_link_two_sees_what_link_one_wrote(self):
        # The end-to-end claim, and the only one that proves the plumbing:
        # a real file, written by link 1 into the lent tree and committed,
        # readable by link 2. Everything else here is bookkeeping.
        from qd.engine import WT_ARG
        d = self.repo()
        seen_by_two = {}

        def h(args):
            wt = args[WT_ARG]["path"]
            if args["task"] == "s1":
                open(os.path.join(wt, "from_one.txt"), "w").write("hello\n")
                import subprocess
                subprocess.run(["git", "-C", wt, "add", "-A"], capture_output=True)
                subprocess.run(["git", "-C", wt, "commit", "-qm", "link1"],
                               capture_output=True)
            else:
                p = os.path.join(wt, "from_one.txt")
                seen_by_two["exists"] = os.path.isfile(p)
                seen_by_two["body"] = open(p).read() if os.path.isfile(p) else None
            return GREEN

        server.run_chain([{"task": "s1", "cwd": d}, {"task": "s2", "cwd": d}], h)
        self.assertTrue(seen_by_two.get("exists"),
                        "link 2 could not see link 1's file -- the chain is "
                        "not a chain")
        self.assertEqual(seen_by_two.get("body"), "hello\n")

    def test_a_kept_container_reports_how_to_merge_it(self):
        d = self.repo()
        out = server.run_chain([{"task": "s1", "cwd": d}], self.handler(GREEN))
        self.assertIn("=== chain worktree:", out)
        self.assertIn("MERGE: git merge", out)

    def test_a_halted_chain_keeps_what_already_passed(self):
        # Link 1 delivered and its gate went green. Link 3 failing does not
        # retract that, and releasing the container would delete it unseen.
        d = self.repo()
        items = [{"task": f"s{i}", "cwd": d} for i in (1, 2)]
        out = server.run_chain(items, self.handler(GREEN, RED))
        self.assertIn("chain halted, kept anyway", out)
        branch = out.split("=== chain worktree: ")[1].split(" ===")[0]
        import subprocess
        p = subprocess.run(["git", "-C", d, "branch", "--list", branch],
                           capture_output=True, text=True)
        self.assertIn(branch, p.stdout, "the kept branch is gone")

    def test_a_chain_that_committed_nothing_leaves_no_container(self):
        d = self.repo()
        out = server.run_chain([{"task": "s1", "cwd": d}], self.handler(RED))
        self.assertNotIn("=== chain worktree:", out)
        base = os.path.expanduser("~/.qwen-delegate/worktrees")
        import subprocess
        p = subprocess.run(["git", "-C", d, "branch", "--list", "qwen/*"],
                           capture_output=True, text=True)
        self.assertEqual(p.stdout.strip(), "",
                         "a chain that delivered nothing left a branch behind")

    def test_an_in_tree_chain_is_untouched(self):
        # `worktree: "off"` is the long-standing default and keeps the repo
        # lock path. Nothing here may change it.
        from qd.engine import WT_ARG
        d = self.repo()
        with open(os.path.join(d, ".qwen-delegate.json"), "w") as f:
            f.write('{"worktree": "off"}')
        server.run_chain([{"task": "s1", "cwd": d}], self.handler(GREEN))
        self.assertNotIn(WT_ARG, self.seen[0])

    def test_a_lone_delegation_is_never_lent_a_tree(self):
        # The hard constraint on this change: a single delegation and
        # qwen_query must behave exactly as before. A lone run acquires and
        # disposes of its own container; only a chain lends one. Driven through
        # the real routing seam with engine.run stubbed, so this fails if the
        # lone path ever starts reading WT_ARG.
        from qd import engine
        from qd.engine import WT_ARG
        d = self.repo()
        seen = []
        real = engine.run
        engine.run = lambda a: (seen.append(a), GREEN)[1]
        try:
            server.run_delegate_batch({"cwd": d, "task": "solo"})
        finally:
            engine.run = real
        self.assertEqual(len(seen), 1)
        self.assertNotIn(WT_ARG, seen[0])

    def test_an_empty_chain_acquires_nothing(self):
        d = self.repo()
        server.run_chain([], self.handler())
        import subprocess
        p = subprocess.run(["git", "-C", d, "branch", "--list", "qwen/*"],
                           capture_output=True, text=True)
        self.assertEqual(p.stdout.strip(), "")


class ChainPreflight(Fixture):
    """The shared pre-flight verdict must not cross a chain link boundary.

    `_preflight_once` keys on (base sha, worktrees dir, gate) and justifies
    itself with "every item is cut from the SAME base commit into its own clean
    worktree, so that answer is identical for every item by construction".
    True for a batch; false for chain link 2, whose tree deliberately holds
    link 1's commits.
    """

    def test_sharing_off_actually_bypasses_the_cache(self):
        # The mechanism the fix relies on: `isolated=False` must RUN the gate,
        # not answer from the shared dict. If this ever silently starts caching
        # regardless, chain link 2 would be graded on link 1's verdict and the
        # bug returns with the fix still in place.
        from qd import engine
        d = tempfile.mkdtemp()
        engine._preflight_forget()
        runs = []
        real = engine._run_verify_timed
        engine._run_verify_timed = lambda c, w, t: (
            runs.append(c), (True, "", 1, False))[1]
        try:
            for _ in range(2):
                engine._preflight_once("gate", d, 5, "abc123", False)
            self.assertEqual(len(runs), 2, "an unshared pre-flight was cached")
            runs.clear()
            for _ in range(2):
                engine._preflight_once("gate", d, 5, "abc123", True)
            self.assertEqual(len(runs), 1, "a shared pre-flight stopped sharing")
        finally:
            engine._run_verify_timed = real
            engine._preflight_forget()

    def test_a_chain_link_after_the_first_is_marked_unshareable(self):
        # And the wiring: the engine decides from CHAIN_ARG's position, so the
        # positions run_chain injects are what actually reach that decision.
        from qd.engine import CHAIN_ARG
        d = tempfile.mkdtemp()
        server.run_chain([{"task": "a", "cwd": d}, {"task": "b", "cwd": d}],
                         self.handler(GREEN, GREEN))
        self.assertEqual([a[CHAIN_ARG]["pos"] for a in self.seen], [1, 2])


if __name__ == "__main__":
    unittest.main(verbosity=1)
