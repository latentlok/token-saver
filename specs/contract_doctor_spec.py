#!/usr/bin/env python3
"""
Spec for doctor's stale-contract-pin check (A8).

Claude-authored gate (never delegate this file -- it defines what correct means).

The design names three ways a contract bites once it is a separate file. Two are
answered at run time: the worker editing it (`spec_globs`), and it moving
between chain links (`features/gates/contract.py` refuses). The third is
**edited between the run and review, so the receipt you audit no longer
describes the criteria that ran** -- and nothing catches that at run time,
because by then there is no run.

A gate refuses at the moment it matters. This finds the ones that already
drifted and nobody re-ran: a test still passing against criteria that changed
underneath it, which reads exactly like a test that AGREES with the contract.

Run:  python3 specs/contract_doctor_spec.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd import doctor  # noqa: E402
from qd.core import contract  # noqa: E402

DOC = "- **C1**: the entry point exists\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        for c in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                  ["git", "config", "user.name", "t"]):
            subprocess.run(c, cwd=self.cwd, check=True, capture_output=True)
        os.makedirs(os.path.join(self.cwd, "contracts"), exist_ok=True)
        self.write("contracts/f.md", DOC)

    def write(self, rel, body):
        path = os.path.join(self.cwd, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)

    def commit(self):
        subprocess.run(["git", "add", "-A"], cwd=self.cwd, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.cwd, check=True)

    def findings(self):
        return doctor._stale_contract_pins(self.cwd)


class Drift(Fixture):
    def test_a_contract_that_moved_after_the_gate_is_flagged(self):
        self.write("test_f.py",
                   contract.header_line("contracts/f.md", "0" * 16) + "\n")
        self.commit()
        got = self.findings()
        self.assertEqual(len(got), 1)
        self.assertIn("test_f.py", got[0][1])
        self.assertIn("Re-run the step that wrote the gate", got[0][1])

    def test_a_matching_pin_is_silent(self):
        # The control. A check that fires on correct state is one that gets
        # switched off, taking the real findings with it.
        self.write("test_f.py",
                   contract.header_line("contracts/f.md",
                                        contract.digest(DOC)) + "\n")
        self.commit()
        self.assertEqual(self.findings(), [])

    def test_a_vanished_contract_is_flagged_differently(self):
        # "Graded against criteria nobody can read" is a different problem from
        # "graded against an older version", and the remedies differ.
        self.write("test_f.py",
                   contract.header_line("contracts/gone.md", "0" * 16) + "\n")
        self.commit()
        got = self.findings()
        self.assertEqual(len(got), 1)
        self.assertIn("no longer exists", got[0][1])


class Silence(Fixture):
    def test_unpinned_files_are_not_accused(self):
        # Only link 1 writes a pin. Every other file in the repo is silent
        # about contracts and must stay out of this.
        self.write("test_f.py", "def test_a(): pass\n")
        self.write("app.py", "X = 1\n")
        self.commit()
        self.assertEqual(self.findings(), [])

    def test_a_non_repo_returns_nothing_rather_than_raising(self):
        # Doctor is what a confused caller reaches for; a fault in one check
        # must not be the thing that stops them getting the other answers.
        self.assertEqual(doctor._stale_contract_pins(tempfile.mkdtemp()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
