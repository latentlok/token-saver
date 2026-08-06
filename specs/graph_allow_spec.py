#!/usr/bin/env python3
"""
Spec for the worker's graphify permission (qd/graph.py READ_ONLY).

Claude-authored gate (never delegate this file -- it defines what correct means).

**The promise that was not kept.** `graph.bootstrap_line()` tells the caller, on
a first delegation: *"from the next run on, the worker locates code through it
instead of reading files."* Nothing made that true. The worker has no shell
unless one is granted, so the promise held only for callers who happened to wire
`shell_allow` themselves -- and the receipt's GRAPH line counts worker graphify
reads, so the design plainly expected this to work.

**Why the pattern is not `^graphify\\b`.** That grants every SUBCOMMAND graphify
has, including `update`, which on a repo with a semantic index configured can
bill a cloud account. A10, and PRINCIPLES §III: the real boundary is the most
powerful thing reachable through what you permit, not the list.

Run:  python3 specs/graph_allow_spec.py
"""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd import graph  # noqa: E402


class ThePattern(unittest.TestCase):
    def setUp(self):
        self.pat = graph.read_only_allow()

    def allows(self, cmd):
        return bool(re.match(self.pat, cmd))

    def test_it_permits_reading_the_graph(self):
        for cmd in ("graphify explain delegate()",
                    "graphify query 'how are facts computed'",
                    "graphify affected new_public_symbols",
                    "graphify god-nodes --top 10"):
            self.assertTrue(self.allows(cmd), cmd)

    def test_it_does_NOT_permit_update(self):
        # The one that costs money. `update` on a repo with a semantic index
        # configured can bill a cloud account, and the plugin runs it itself
        # AFTER the verdict, on its own terms -- a worker that can call it
        # decides for the caller.
        for cmd in ("graphify update .", "graphify update . --no-cluster",
                    "graphify update --semantic"):
            self.assertFalse(self.allows(cmd), cmd)

    def test_update_is_absent_from_the_list_itself(self):
        # Not merely unmatched by accident of regex: absent by name, so the
        # reason survives someone rewriting the pattern.
        self.assertNotIn("update", graph.READ_ONLY)

    def test_it_does_not_match_a_lookalike_binary(self):
        self.assertFalse(self.allows("graphifyy explain x"))

    def test_it_is_returned_not_applied(self):
        # This module owns WHAT graphify is. Whether to grant it is a decision
        # about the run's permissions and belongs to whoever owns those.
        self.assertIsInstance(graph.read_only_allow(), str)


class TheGrant(unittest.TestCase):
    """Granting it is the engine's call, and it is conditional on two things."""

    def test_the_module_states_both_conditions(self):
        # Read from the engine's own comment rather than re-derived here: a
        # test that restates a rule can agree with a stale version of it.
        with open(os.path.join(os.path.dirname(_HERE), "qd", "engine.py")) as f:
            src = f.read()
        self.assertIn('if approval_mode == "scoped" and graph.read_state(cwd):',
                      src)

    def test_auto_edit_gets_nothing_because_it_has_no_shell(self):
        # Adding patterns under auto-edit would be a permission that reads as
        # granted and does nothing -- worse than absent, because a caller would
        # believe the worker could use the graph.
        with open(os.path.join(os.path.dirname(_HERE), "qd", "engine.py")) as f:
            src = f.read()
        self.assertIn("has no shell at all", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
