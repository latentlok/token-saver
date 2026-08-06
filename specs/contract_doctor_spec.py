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
        # `["text"]`, not `[1]`. These assertions used to index the TUPLE this
        # function happened to return, which is how it went four checks without
        # anyone noticing `report()` cannot read a tuple -- see
        # TheFindingIsSHAPEDLikeEveryOtherDoctorFinding below.
        self.assertIn("test_f.py", got[0]["text"])
        self.assertIn("Re-run the step that wrote the gate", got[0]["text"])

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
        self.assertIn("no longer exists", got[0]["text"])


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


class AQuotedFILENAMEStillHasAStalePin(Fixture):
    """The `git ls-files` seam 23cb3f4 fixed in gittree, missed here.

    That commit named this site in its own message as known and not fixed --
    "it only degrades to a skipped advisory, not a missed revert". True, and
    the advisory is skipped for exactly the files git quotes: the raw line is
    `"tab\\tchar_test.py"` including the quotes, so `rel.endswith(".py")` is
    FALSE and the file never reaches `parse_header` at all. Not "reported
    oddly" -- never looked at.

    Measured on git 2.53 before the fix, four stale-pinned files, one plain and
    three named: 1 of 4 reported under the stock `core.quotePath=true`, 2 of 4
    under false (a non-ASCII name is bare there). The check whose whole job is
    to find gates graded against criteria that moved could not see three
    quarters of them.

    ONE direction only, and that is the difference from gittree's six seams:
    `ls-files` is called here with NO pathspec, and `rel` goes to `open()` and
    to receipt text. Nothing is fed back to git as a pathspec, so
    `literal_pathspec` has nothing to attach to -- pinned below with a
    `:(icase)` name, which would be the failing case if a pathspec ever
    appeared.
    """

    NAMES = ("tab\tchar_test.py", "café_test.py", "nl\nline_test.py",
             ":(icase)magic_test.py", "sp ace_test.py")

    def arrange(self):
        for n in self.NAMES:
            self.write(n, contract.header_line("contracts/f.md",
                                               "0" * 16) + "\n")
        self.write("plain_test.py",
                   contract.header_line("contracts/f.md", "0" * 16) + "\n")
        self.commit()

    def test_every_hostile_name_is_still_inspected(self):
        self.arrange()
        for quote in ("true", "false"):
            with self.subTest(quotePath=quote):
                subprocess.run(["git", "config", "core.quotePath", quote],
                               cwd=self.cwd, check=True)
                got = self.findings()
                named = " ".join(f["text"] for f in got)
                for n in self.NAMES:
                    # The decisive question, and it is about DETECTION, not
                    # about wording: was this file's stale pin found at all.
                    self.assertIn(
                        n.split("\n")[0], named,
                        f"{n!r} was never inspected under quotePath={quote}")
                self.assertEqual(len(got), len(self.NAMES) + 1)

    def test_a_newline_in_a_name_does_not_write_lines_into_the_report(self):
        # The surface the decode CREATES, per the standing rule: before it, a
        # newline arrived as the two characters `\n` inside git's quotes and
        # could not break a line; decoded, it is a real one. doctor's report
        # renders each finding as `  {text}`, one indented block per finding.
        self.write("nl\nline_test.py",
                   contract.header_line("contracts/f.md", "0" * 16) + "\n")
        self.commit()
        got = self.findings()
        self.assertEqual(len(got), 1)
        self.assertNotIn("\n", got[0]["text"],
                         "a filename wrote extra lines into a doctor finding")
        self.assertIn("nl", got[0]["text"])


class TheFindingIsSHAPEDLikeEveryOtherDoctorFinding(Fixture):
    """Found while measuring the decode, and worse than it.

    `_stale_contract_pins` returned `("warn", text)` TUPLES. Every other
    project check returns a dict, `project_check` does `out += ` on the same
    list, and `report()` reads `f["severity"]` / `f["id"]` / `f["fixable"]`. So
    the first time this check FIRES -- which is the only time it does anything
    -- `doctor.report(doctor.project_check(cwd))` raises

        TypeError: tuple indices must be integers or slices, not str

    and takes the whole "--- this project ---" section down with it: the
    stale-server count, the gate-misses-specs warning, all of it. `main` does
    not catch it. The check has never once produced a line a caller could read.

    It survived because the gate above it asserted `got[0][1]` -- the shape the
    producer happened to return -- and never drove the CONSUMER. A spec that
    pins its subject's output shape rather than its caller's requirement cannot
    see this class of bug, which is the general lesson and the reason these
    tests go through `report` and `project_check` rather than through
    `_stale_contract_pins` alone.

    Fixing the decode without this would have made it WORSE: more files
    inspected is more findings, and every finding is the crash.
    """

    def test_the_project_report_renders_instead_of_raising(self):
        self.write("test_f.py",
                   contract.header_line("contracts/f.md", "0" * 16) + "\n")
        self.commit()
        text = doctor.report(doctor.project_check(self.cwd))
        self.assertIn("test_f.py", text)
        self.assertIn("contracts/f.md", text)

    def test_it_carries_the_keys_report_reads(self):
        self.write("test_f.py",
                   contract.header_line("contracts/f.md", "0" * 16) + "\n")
        self.commit()
        for f in self.findings():
            for key in ("id", "severity", "fixable", "text"):
                self.assertIn(key, f)
            self.assertIn(f["severity"], ("high", "info"),
                          "doctor's report has two levels, not three")

    def test_the_two_failures_are_told_apart_by_ID(self):
        # "graded against criteria nobody can read" and "graded against an
        # older version" have different remedies, which the existing pair of
        # tests already says. An `id` is how a caller greps for one of them.
        self.write("gone_test.py",
                   contract.header_line("contracts/gone.md", "0" * 16) + "\n")
        self.write("moved_test.py",
                   contract.header_line("contracts/f.md", "0" * 16) + "\n")
        self.commit()
        ids = {f["id"] for f in self.findings()}
        self.assertEqual(ids, {"contract-pin-missing", "contract-pin-stale"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
