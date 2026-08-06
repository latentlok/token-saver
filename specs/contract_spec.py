#!/usr/bin/env python3
"""
Spec for the contract: qd/core/contract.py, the clause guard, the pin gate.

Claude-authored gate (never delegate this file -- it defines what correct means).

A2 + A4 (DESIGN-v06-test-first §3.3, §6.2). Once the criteria live in their own
document, that document becomes an INPUT TO THE GATE THAT CAN CHANGE WITHOUT
ANYONE NOTICING -- the same defect class as a worker editing a spec, one level
up. Three ways it bites, and where each is answered:

    the worker edits it                  -> spec_globs (config, no code)
    edited between link 1 and link 2     -> gates/contract.py  (this file)
    edited between the run and review    -> CONTRACT: in the receipt

Plus A4: every clause has a test naming it, checked as LINK 1'S GATE rather than
an end-of-run note, because an uncovered clause is knowable before anyone pays
for link 2.

Run:  python3 specs/contract_spec.py
"""

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.core import contract  # noqa: E402
from qd.core.attempt import Attempt  # noqa: E402
from qd.core.plan import RunPlan  # noqa: E402
from qd.features import gates, guards  # noqa: E402
from qd.features.guards import clauses as clause_guard  # noqa: E402

DOC = ("# Contract\n"
       "- **C1**: the entry point exists\n"
       "- **C2**: a row is written with status='sent'\n"
       "### C3 -- it does not raise on failure\n"
       "Unlike C9, this sentence merely mentions an id.\n")


class Clauses(unittest.TestCase):
    def test_it_finds_declarations_in_document_order(self):
        self.assertEqual(contract.clauses(DOC), ["C1", "C2", "C3"])

    def test_prose_that_MENTIONS_an_id_declares_nothing(self):
        # A clause referred to in passing ("unlike C9, this...") must not become
        # a requirement nobody wrote -- the gate would then demand coverage of
        # something the contract never asked for.
        self.assertNotIn("C9", contract.clauses(DOC))

    def test_a_test_claims_coverage_by_naming_the_id(self):
        got = contract.covered("def test_entry():  # C1\ndef test_row(): # C3",
                               ["C1", "C2", "C3"])
        self.assertEqual(got, ["C1", "C3"])

    def test_the_digest_changes_when_the_document_does(self):
        self.assertNotEqual(contract.digest(DOC), contract.digest(DOC + "- C4: x\n"))

    def test_the_header_round_trips(self):
        line = contract.header_line("contracts/x.md", contract.digest(DOC))
        self.assertEqual(contract.parse_header("import os\n" + line + "\n"),
                         ("contracts/x.md", contract.digest(DOC)))

    def test_the_pin_is_read_from_anywhere_in_the_file(self):
        # Not the first line only: formatters move comments, and a pin a
        # reformat can silently unpin is not a pin.
        line = contract.header_line("c.md", "a" * 16)
        path, dig = contract.parse_header("x = 1\n\n\n" + line + "\n\nmore\n")
        self.assertEqual((path, dig), ("c.md", "a" * 16))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        self.write("contracts/f.md", DOC)

    def write(self, rel, body):
        path = os.path.join(self.cwd, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)
        return rel

    def plan(self, **over):
        base = dict(task="t", verify="v", touch_scope=None, trust="self",
                    preflight_expect="any", fixture_provenance=False,
                    fixture_segments=(), brief_path=None,
                    contract_path="contracts/f.md")
        base.update(over)
        return RunPlan(**base)

    def scope(self):
        class S:
            work_cwd = self.cwd
            pre_tracked, hooked, pre_sha = set(), False, "T0"
            scope_unattributed = unrestorable = spec_unattributed = []
            def note_spec_unattributed(self, p): return []
            def unproven_fixtures(self, s): return []
        return S()


class ClauseCoverageGatesLinkOne(Fixture):
    """A4. An uncovered clause is knowable the moment link 1 finishes -- before
    anyone pays for link 2 -- so it FAILS THE ATTEMPT and names the gap. The
    failure lands where the fix belongs, which is usually a vague clause rather
    than a lazy worker."""

    def att(self, *changed):
        return Attempt(n=1, of=3, changed=list(changed), writes=[])

    def test_a_missing_clause_fails_the_attempt(self):
        self.write("test_f.py", "def test_a():  # C1\n    pass\n")
        v = clause_guard.check(self.scope(), self.plan(), self.att("test_f.py"))
        self.assertEqual(v.kind, "uncovered_clauses")
        self.assertIn("C2", v.trail)
        self.assertIn("C3", v.trail)
        self.assertIn("1/3 clauses covered", v.trail)

    def test_full_coverage_is_silent(self):
        self.write("test_f.py",
                   "def test_a(): pass  # C1\ndef test_b(): pass  # C2\n"
                   "def test_c(): pass  # C3\n")
        self.assertIsNone(
            clause_guard.check(self.scope(), self.plan(), self.att("test_f.py")))

    def test_it_supplies_link_ones_missing_floor(self):
        # _SELF_GATE's min_tests does not apply to link 1 (it is graded by the
        # red gate), so without this ONE weak test satisfying ONE clause is a
        # green link 1. "Every clause covered" is a better floor than a count,
        # because it is tied to what was ASKED rather than to volume.
        self.write("test_f.py", "def test_only(): pass  # C1\n")
        self.assertIsNotNone(
            clause_guard.check(self.scope(), self.plan(), self.att("test_f.py")))

    def test_the_correction_names_the_gaps_and_how_to_tag(self):
        self.write("test_f.py", "def test_a(): pass  # C1\n")
        v = clause_guard.check(self.scope(), self.plan(), self.att("test_f.py"))
        self.assertIn("C2", v.prompt)
        self.assertIn("name the clause it covers", v.prompt)

    def test_it_refuses_to_let_a_clause_be_tagged_dishonestly(self):
        # The mechanical limit, said out loud in the correction rather than
        # pretended away: a test tagged C2 that asserts something adjacent reads
        # as covered, and no grep can tell. So the instruction tells the worker
        # to STOP rather than tag.
        self.write("test_f.py", "def test_a(): pass  # C1\n")
        v = clause_guard.check(self.scope(), self.plan(), self.att("test_f.py"))
        self.assertIn("stop and say which and why", v.prompt)

    def test_only_test_files_can_claim_coverage(self):
        # A clause id in ordinary source is not a test for it.
        self.write("impl.py", "# handles C1 C2 C3\n")
        self.assertIsNotNone(
            clause_guard.check(self.scope(), self.plan(), self.att("impl.py")))

    def test_no_contract_means_no_check(self):
        self.assertIsNone(clause_guard.check(self.scope(),
                                             self.plan(contract_path=None),
                                             self.att()))

    def test_a_contract_with_no_clauses_asks_nothing(self):
        self.write("contracts/f.md", "# just prose, no clauses\n")
        self.assertIsNone(
            clause_guard.check(self.scope(), self.plan(), self.att()))


class TheCrossLinkPin(Fixture):
    """A2.3 -- the half nothing else covers.

    Without it the pipeline's whole premise, *the gate was frozen before the
    implementation*, is true of the test file and FALSE of the document the test
    file was derived from.
    """

    def run_for(self, tests):
        return gates.GateRun(objection=None, contract_path="contracts/f.md",
                             contract_tests=tests, work_cwd=self.cwd)

    def test_a_matching_pin_proceeds(self):
        self.write("test_f.py",
                   contract.header_line("contracts/f.md", contract.digest(DOC))
                   + "\ndef test_a(): pass\n")
        self.assertTrue(gates.run_all(gates.GATES,
                                      self.run_for(["test_f.py"])).ok)

    def test_a_moved_contract_refuses_the_run(self):
        self.write("test_f.py",
                   contract.header_line("contracts/f.md", "0" * 16)
                   + "\ndef test_a(): pass\n")
        d = gates.run_all(gates.GATES, self.run_for(["test_f.py"]))
        self.assertFalse(d.ok)
        self.assertIn("CONTRACT MOVED", d.reason)
        self.assertIn("not evidence about this one", d.reason)

    def test_an_unpinned_test_is_not_accused(self):
        # Only link 1 writes the pin. Every other test file in the repo is
        # silent about contracts and must stay out of this.
        self.write("test_f.py", "def test_a(): pass\n")
        self.assertTrue(gates.run_all(gates.GATES,
                                      self.run_for(["test_f.py"])).ok)

    def test_a_pin_for_a_DIFFERENT_contract_is_ignored(self):
        self.write("test_f.py",
                   contract.header_line("contracts/other.md", "0" * 16) + "\n")
        self.assertTrue(gates.run_all(gates.GATES,
                                      self.run_for(["test_f.py"])).ok)

    def test_no_contract_declared_means_no_gate(self):
        self.assertTrue(gates.run_all(
            gates.GATES,
            gates.GateRun(objection=None, work_cwd=self.cwd)).ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
