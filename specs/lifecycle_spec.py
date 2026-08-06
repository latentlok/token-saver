#!/usr/bin/env python3
"""
Spec for qd/core/lifecycle.py -- one live server per machine.

Claude-authored gate (never delegate this file -- it defines what correct means).

A0d, second half. The first half shipped: doctor counts running servers, names
their pids and prints the exact `kill`. This is the part that PREVENTS rather
than reports.

**The failure it exists for.** Claude Code respawns the MCP server on reload and
the old process does not always die. Two servers then share one machine's
endpoint semaphore, repo locks and run log -- and those locks are per-PROCESS,
so the two do not queue behind each other. They double the endpoint's real
concurrency while each believes it is honouring `parallel_max`. And an OLD build
keeps serving: the version a caller gets is whichever process won, which is how
a fixed bug appears to come back.

Run:  python3 specs/lifecycle_spec.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.core import lifecycle  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()

    def supersede(self):
        return lifecycle.supersede(self.base, os.getpid(), "0.6.0")


class Liveness(Fixture):
    def test_this_process_is_alive(self):
        self.assertTrue(lifecycle.alive(os.getpid()))

    def test_a_finished_process_is_not(self):
        child = subprocess.Popen(["sleep", "30"])
        child.kill()
        child.wait()
        self.assertFalse(lifecycle.alive(child.pid))

    def test_nonsense_is_not_alive_rather_than_an_error(self):
        # A corrupt record must not be able to crash a starting server.
        for bad in (None, "not-a-pid", -1, 0):
            self.assertFalse(lifecycle.alive(bad), repr(bad))


class Superseding(Fixture):
    def test_a_first_start_claims_the_machine(self):
        self.assertEqual(self.supersede(), "none")
        self.assertEqual(lifecycle.read(self.base)["pid"], os.getpid())

    def test_a_dead_predecessor_is_just_litter(self):
        # By far the common case: the old process already exited and the record
        # is stale.
        child = subprocess.Popen(["sleep", "30"])
        child.kill()
        child.wait()
        lifecycle.write(self.base, child.pid, "0.5.0")
        self.assertEqual(self.supersede(), "stale")

    def test_a_live_predecessor_is_REPORTED_and_left_alone(self):
        # The correction that specs/serialize_spec.py forced. An earlier draft
        # SIGTERMed a live predecessor -- and that spec exists to prove the repo
        # lock and endpoint slot hold ACROSS server processes, i.e. two servers
        # on one machine is a SUPPORTED configuration. Killing one would break a
        # real guarantee to tidy up an accident.
        child = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (child.kill(), child.wait()))
        lifecycle.write(self.base, child.pid, "0.5.0")
        self.assertEqual(self.supersede(), "live")
        self.assertTrue(lifecycle.alive(child.pid), "a live server was killed")

    def test_nothing_in_this_module_can_signal_anything(self):
        """Stated as its own test because the temptation will return: the whole
        point of a pid record is that it makes killing easy.

        Checks the CODE, not the prose -- the docstring deliberately explains
        the SIGTERM that an earlier draft had, and a spec that broke on
        explaining a decision would get "fixed" by deleting the explanation.
        """
        import ast as _ast
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "qd", "core", "lifecycle.py")
        with open(path) as f:
            tree = _ast.parse(f.read())

        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                names = [a.name for a in getattr(node, "names", [])]
                self.assertNotIn("signal", names + [getattr(node, "module", "")],
                                 "the signal module is back")
            if isinstance(node, _ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name == "kill":
                    # The only permitted kill is the liveness PROBE: signal 0,
                    # which asks the kernel and delivers nothing.
                    arg = node.args[1] if len(node.args) > 1 else None
                    self.assertIsInstance(arg, _ast.Constant, "kill with a variable signal")
                    self.assertEqual(arg.value, 0, "kill with a real signal")

    def test_the_record_becomes_ours_either_way(self):
        for setup in (lambda: None,
                      lambda: lifecycle.write(self.base, 999999, "0.5.0")):
            setup()
            self.supersede()
            self.assertEqual(lifecycle.read(self.base)["pid"], os.getpid())

    def test_seeing_our_own_record_signals_nothing(self):
        # A re-entrant start must not send SIGTERM to itself.
        self.supersede()
        self.assertEqual(self.supersede(), "self")


class TheRecord(Fixture):
    def test_a_corrupt_record_reads_as_absent(self):
        # A half-written file must not be able to stop a server from starting.
        with open(lifecycle.record_path(self.base), "w") as f:
            f.write("{not json")
        self.assertIsNone(lifecycle.read(self.base))
        self.assertEqual(self.supersede(), "none")

    def test_a_record_without_a_pid_reads_as_absent(self):
        with open(lifecycle.record_path(self.base), "w") as f:
            f.write('{"version": "0.5.0"}')
        self.assertIsNone(lifecycle.read(self.base))

    def test_it_carries_the_version_so_doctor_can_name_it(self):
        self.supersede()
        self.assertEqual(lifecycle.read(self.base)["version"], "0.6.0")

    def test_the_write_is_atomic(self):
        # os.replace, not a truncate-and-write: a reader that catches the file
        # mid-write would see a torn record and treat a LIVE server as absent,
        # which is the exact double-server this prevents.
        self.supersede()
        leftovers = [f for f in os.listdir(self.base) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
