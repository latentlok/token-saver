#!/usr/bin/env python3
"""
CI-covered serialization spec for qd/server.py -- the load-robust half of
specs/dispatch_spec.py.

Claude-authored gate (never delegate this file -- it defines what correct means).

dispatch_spec.py is excluded from CI: its OVERLAP assertions ("these two ran
concurrently") are wall-clock claims that flake under load, because a loaded
box can serialize genuinely concurrent work. SERIALIZATION assertions have
the opposite character -- load only ever separates serialized intervals
further, never interleaves them -- so they hold on any box. This file
carries exactly those, so the locks that keep two agents off one tree or one
GPU are enforced by CI rather than by whoever remembers to run dispatch_spec
by hand (the 0.4.0 cross-process endpoint slot shipped with zero CI
coverage; docs/PENDING.md carried the item).

Claims:

  1. Same-repo in-tree delegations serialize WITHIN one server process even
     on an endpoint with capacity to overlap them -- the repo lock, not the
     endpoint slot, is what serializes.
  2. The endpoint slot holds across server PROCESSES (two sessions, one GPU).
  3. The repo lock holds across server PROCESSES: two sessions delegating
     in-tree into one repo through DIFFERENT endpoints serialize. This is
     the hole the machine-wide repo lock closed -- per-process, it held only
     while the shared single-slot endpoint masked it.
  4. dispatch:"serial" pins a multi-slot endpoint to one in-flight request.
  5. Queries gate on the endpoint slot too.
  6. Guard STRUCTURE, no wall clock at all: a worktree="auto" call -- by arg
     or by project config -- takes no repo lock; an in-tree call takes it.
     The engine's isolation decision and the server's lock decision cannot
     disagree, because both ask engine.worktree_mode.

Shares dispatch_spec's harness (Server, Fixture) by import: one stub server
launcher, one fixture, two files with different CI fates.

Run:  python3 specs/serialize_spec.py
"""

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

from dispatch_spec import Fixture, Server  # noqa: E402


class SameProcess(Fixture):
    def test_same_repo_in_tree_serializes_on_a_capacity_2_endpoint(self):
        # p2a/p2b share endpoint e2 (parallel_max 2): the endpoint slot alone
        # would let these overlap, so serialization here proves the repo lock.
        self.srv.call(10, "qwen_delegate",
                      {"tag": "sp1", "sleep": 1.5, "cwd": self.repo_a,
                       "executor": "p2a"})
        self.srv.call(11, "qwen_delegate",
                      {"tag": "sp2", "sleep": 1.5, "cwd": self.repo_a,
                       "executor": "p2b"})
        self.srv.wait(10, timeout=25)
        self.srv.wait(11, timeout=25)
        self.assert_serialized("sp1", "sp2")


class CrossProcess(Fixture):
    """Every Claude session runs its own MCP server process; these claims are
    the machine-wide halves of the two locks."""

    def second_server(self):
        srv2 = Server(self.sdir, env_extra=self.env_extra)
        self.addCleanup(lambda: srv2.proc.kill())
        return srv2

    def test_endpoint_slot_holds_across_server_processes(self):
        srv2 = self.second_server()
        self.srv.call(20, "qwen_delegate",
                      {"tag": "xe1", "sleep": 1.5, "cwd": self.repo_a,
                       "executor": "p1a"})
        srv2.call(21, "qwen_delegate",
                  {"tag": "xe2", "sleep": 1.5, "cwd": self.repo_b,
                   "executor": "p1b"})
        self.srv.wait(20, timeout=25)
        srv2.wait(21, timeout=25)
        self.assert_serialized("xe1", "xe2")

    def test_repo_lock_holds_across_server_processes(self):
        # DIFFERENT endpoints (e2 and e3, both capacity 2), one repo, both
        # in-tree: no endpoint slot is shared, so only the repo lock's flock
        # can serialize these. A per-process repo lock overlaps them.
        srv2 = self.second_server()
        self.srv.call(30, "qwen_delegate",
                      {"tag": "xr1", "sleep": 1.5, "cwd": self.repo_a,
                       "executor": "p2a"})
        srv2.call(31, "qwen_delegate",
                  {"tag": "xr2", "sleep": 1.5, "cwd": self.repo_a,
                   "executor": "p3"})
        self.srv.wait(30, timeout=25)
        srv2.wait(31, timeout=25)
        self.assert_serialized("xr1", "xr2")


class SerialPolicy(Fixture):
    def serial_repo(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ".qwen-delegate.json"), "w") as f:
            json.dump({"dispatch": "serial"}, f)
        return d

    def test_serial_config_overrules_a_multi_slot_endpoint(self):
        a, b = self.serial_repo(), self.serial_repo()
        self.srv.call(40, "qwen_delegate",
                      {"tag": "pc1", "sleep": 1.0, "cwd": a, "executor": "p2a"})
        self.srv.call(41, "qwen_delegate",
                      {"tag": "pc2", "sleep": 1.0, "cwd": b, "executor": "p2b"})
        self.srv.wait(40, timeout=25)
        self.srv.wait(41, timeout=25)
        self.assert_serialized("pc1", "pc2")

    def test_queries_gate_on_the_endpoint_slot(self):
        self.srv.call(42, "qwen_query",
                      {"tag": "qs1", "sleep": 0.8, "cwd": self.repo_a,
                       "executor": "p1a"})
        self.srv.call(43, "qwen_query",
                      {"tag": "qs2", "sleep": 0.8, "cwd": self.repo_b,
                       "executor": "p1b"})
        self.srv.wait(42, timeout=25)
        self.srv.wait(43, timeout=25)
        self.assert_serialized("qs1", "qs2")


class GuardShape(Fixture):
    """The guard LIST asked directly -- structural, zero wall-clock. These
    bind the worktree_mode seam: the config default must reach the server's
    lock decision, not only the engine's isolation."""

    def guards(self, args):
        from qd import server as qs
        return qs._guards_for("qwen_delegate", args)

    def test_in_tree_takes_endpoint_and_repo_lock(self):
        g = self.guards({"cwd": self.repo_a, "executor": "p2a"})
        self.assertEqual(len(g), 2)

    def test_worktree_auto_arg_skips_the_repo_lock(self):
        g = self.guards({"cwd": self.repo_a, "executor": "p2a",
                         "worktree": "auto"})
        self.assertEqual(len(g), 1)

    def test_worktree_config_default_skips_the_repo_lock(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ".qwen-delegate.json"), "w") as f:
            json.dump({"worktree": "auto"}, f)
        self.assertEqual(len(self.guards({"cwd": d, "executor": "p2a"})), 1)

    def test_worktree_config_typo_keeps_the_repo_lock(self):
        # "off" is the long-standing default; an unrecognised value must not
        # silently isolate work the caller expected to land in the tree.
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ".qwen-delegate.json"), "w") as f:
            json.dump({"worktree": "Auto"}, f)
        self.assertEqual(len(self.guards({"cwd": d, "executor": "p2a"})), 2)

    def test_arg_off_beats_a_config_auto(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ".qwen-delegate.json"), "w") as f:
            json.dump({"worktree": "auto"}, f)
        g = self.guards({"cwd": d, "executor": "p2a", "worktree": "off"})
        self.assertEqual(len(g), 2)

    def test_query_holds_the_endpoint_slot_only(self):
        from qd import server as qs
        g = qs._guards_for("qwen_query",
                           {"cwd": self.repo_a, "executor": "p2a"})
        self.assertEqual(len(g), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
