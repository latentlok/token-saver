#!/usr/bin/env python3
"""
Spec for qd/features/guards/ -- the things that fail an ATTEMPT.

Claude-authored gate (never delegate this file -- it defines what correct means).

The third instance of one shape, and the one that justifies the first two:

    a gate     refuses the RUN       -- nothing is built
    a guard    fails the ATTEMPT     -- the worker is told, and tries again
    a detector reports on the RESULT -- nobody is stopped

Each guard was ~50 lines inside `_delegate`'s attempt loop, and every one
repeated the same five steps: detect, revert, write a trail line that fails the
attempt, compose a correction, then `continue` or `break` on the attempt budget.
Only the first and fourth differ. The rest was copied four times -- and a rule
copied four times is one edit away from being true in three places.

**Control flow stays with the loop.** A guard cannot `continue` a loop it does
not own, so it RETURNS a `Violation`. That is what makes the retry-or-give-up
rule exist once rather than once per guard.

**Guards are NOT pure, and detectors are.** Most revert the offending paths,
which is the point -- a spec edit merely reported is a spec edit that stands.
That asymmetry is why this is a third directory and not a flag on the other two.

Run:  python3 specs/guards_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.core.attempt import Attempt  # noqa: E402
from qd.core.plan import RunPlan  # noqa: E402
from qd.core.violation import Violation  # noqa: E402
from qd.features import guards  # noqa: E402
from qd.features.guards import specs as spec_guard  # noqa: E402


class FakeScope:
    def __init__(self, unproven=(), pre_tracked=(), hooked=False):
        self._unproven = list(unproven)
        self.pre_tracked = set(pre_tracked)
        self.hooked = hooked
        self.pre_sha = "T0SHA"
        self.scope_unattributed = []
        self.spec_unattributed = []
        self.work_cwd = "/repo"
        self.t0_bytes = None
        self.unrestorable = []
        self.restored = []

    def unproven_fixtures(self, segments):
        return list(self._unproven)

    def note_spec_unattributed(self, paths):
        fresh = [p for p in paths if p not in self.spec_unattributed]
        self.spec_unattributed.extend(fresh)
        return fresh

    def note_scope_unattributed(self, paths):
        self.scope_unattributed.extend(paths)

    def restore(self, paths, base):
        self.restored.extend(paths)
        return []


def plan(**over):
    base = dict(task="t", verify="v", touch_scope=None, trust="self",
                preflight_expect="any", fixture_provenance=True,
                fixture_segments=("fixtures",), brief_path=None,
                contract_path=None)
    base.update(over)
    return RunPlan(**base)


def attempt(n=1, of=3):
    return Attempt(n=n, of=of, changed=[], writes=[])


class TheRegistry(unittest.TestCase):
    def test_every_guard_is_listed(self):
        self.assertEqual(sorted(g.KIND for g in guards.GUARDS),
                         ["fixture_provenance", "playbook_edited",
                          "spec_violation", "touch_scope",
                          "uncovered_clauses"])

    def test_order_is_precedence(self):
        # As in core/status.py. The worker gets ONE correction, so the order is
        # the rule: a scope violation is work that had to be UNDONE, which
        # outranks a missing provenance comment.
        # Precedence, most serious first: rewriting the thing that grades you,
        # rewriting the thing that briefed you, work that had to be undone,
        # then a missing provenance comment.
        self.assertEqual([g.KIND for g in guards.GUARDS],
                         ["spec_violation", "playbook_edited", "touch_scope",
                          "uncovered_clauses", "fixture_provenance"])

    def test_a_clean_attempt_produces_no_violation(self):
        self.assertIsNone(guards.first(FakeScope(), plan(), attempt()))

    def test_only_the_FIRST_objection_is_returned(self):
        # One correction at a time. A message listing every complaint at once is
        # one the model triages, and this project has measured what a wall of
        # instructions does to a 27B worker.
        calls = []

        class A:
            KIND = "a"
            @staticmethod
            def check(s, p, at):
                calls.append("a")
                return Violation("a", "trail a", "fix a")

        class B:
            KIND = "b"
            @staticmethod
            def check(s, p, at):
                calls.append("b")
                return Violation("b", "trail b", "fix b")

        real = guards.GUARDS
        guards.GUARDS = (A, B)
        try:
            got = guards.first(FakeScope(), plan(), attempt())
        finally:
            guards.GUARDS = real
        self.assertEqual(got.kind, "a")
        self.assertEqual(calls, ["a"], "a second guard ran after the first objected")

    def test_a_raising_guard_does_not_fail_the_attempt(self):
        # A broken guard must not fail an attempt that might be fine. Note the
        # stakes differ from a detector's: this fails OPEN, so a skipped guard
        # is a protection silently not applied -- which is why each guard's
        # detection is pinned separately from this loop.
        class Boom:
            KIND = "boom"
            @staticmethod
            def check(s, p, at):
                raise RuntimeError("guard exploded")

        real = guards.GUARDS
        guards.GUARDS = (Boom,)
        try:
            self.assertIsNone(guards.first(FakeScope(), plan(), attempt()))
        finally:
            guards.GUARDS = real


class FixtureProvenance(unittest.TestCase):
    """U3.3 -- the field's worst defect class."""

    def test_an_untraceable_fixture_fails_the_attempt(self):
        v = guards.first(FakeScope(["tests/fixtures/users.json"]), plan(),
                         attempt())
        self.assertEqual(v.kind, "fixture_provenance")
        self.assertIn("FIXTURE PROVENANCE", v.trail)
        self.assertIn("users.json", v.trail)

    def test_the_correction_names_the_exact_line_to_add(self):
        # A correction the worker has to interpret is one it will interpret
        # differently each time.
        v = guards.first(FakeScope(["a.json"]), plan(), attempt())
        self.assertIn("captured-from:", v.prompt)
        self.assertIn("a.json", v.prompt)
        self.assertIn("first 10 lines", v.prompt)

    def test_it_refuses_to_let_the_worker_invent_a_source(self):
        # The failure mode the whole check exists for: an invented fixture with
        # a plausible provenance line is worse than one with none, because it
        # now LOOKS traced.
        v = guards.first(FakeScope(["a.json"]), plan(), attempt())
        self.assertIn("Do not invent a source", v.prompt)

    def test_it_is_silent_when_switched_off(self):
        self.assertIsNone(guards.first(FakeScope(["a.json"]),
                                       plan(fixture_provenance=False),
                                       attempt()))

    def test_it_does_not_revert_the_fixture(self):
        # Unlike the other guards. The data may be right with only the comment
        # missing, and deleting a caller's fixture to enforce a comment is a
        # cure worse than the disease. Pinned because "guards revert" is the
        # rule and this is the deliberate exception.
        scope = FakeScope(["a.json"])
        guards.first(scope, plan(), attempt())
        self.assertEqual(scope.unproven_fixtures(()), ["a.json"])


class TouchScope(unittest.TestCase):
    """M4 seam 2 -- a promise about blast radius."""

    def scope(self, **kw):
        kw.setdefault("pre_tracked", ["src/a.py", "other.py"])
        return FakeScope(**kw)

    def att(self, changed, writes=()):
        return Attempt(n=1, of=3, changed=list(changed), writes=list(writes))

    def test_editing_outside_the_scope_fails_the_attempt_and_reverts(self):
        sc = self.scope()
        v = guards.first(sc, plan(touch_scope=["src/a.py"]),
                         self.att(["src/a.py", "other.py"]))
        self.assertEqual(v.kind, "touch_scope")
        self.assertIn("other.py", v.trail)
        self.assertIn("auto-reverted", v.trail)
        self.assertEqual(sc.restored, ["other.py"])

    def test_a_file_inside_the_scope_is_fine(self):
        sc = self.scope()
        self.assertIsNone(guards.first(sc, plan(touch_scope=["src/a.py"]),
                                       self.att(["src/a.py"])))
        self.assertEqual(sc.restored, [])

    def test_new_files_are_always_allowed(self):
        # A scope names what may be MODIFIED. A worker that cannot create a
        # file cannot do most jobs.
        sc = self.scope()
        self.assertIsNone(guards.first(sc, plan(touch_scope=["src/a.py"]),
                                       self.att(["brand/new.py"])))
        self.assertEqual(sc.restored, [])

    def test_an_unattributed_change_is_recorded_and_never_reverted(self):
        # C10, and the expensive one. Under a proxy that logs the worker's
        # writes, a changed file with NO logged write belongs to the caller or
        # an agent of theirs working the same tree. Reverting those is how a
        # caller's concurrent work got destroyed.
        sc = self.scope(hooked=True)
        v = guards.first(sc, plan(touch_scope=["src/a.py"]),
                         self.att(["other.py"], writes=[]))
        self.assertIsNone(v, "the caller's own edit failed the worker's attempt")
        self.assertEqual(sc.scope_unattributed, ["other.py"])
        self.assertEqual(sc.restored, [])

    def test_the_workers_own_out_of_scope_edit_still_counts(self):
        # The other half: attribution must not become a blanket excuse.
        sc = self.scope(hooked=True)
        v = guards.first(sc, plan(touch_scope=["src/a.py"]),
                         self.att(["other.py"], writes=["other.py"]))
        self.assertEqual(v.kind, "touch_scope")
        self.assertEqual(sc.restored, ["other.py"])

    def test_no_declared_scope_means_no_check(self):
        self.assertIsNone(guards.first(self.scope(), plan(touch_scope=None),
                                       self.att(["anything.py"])))

    def test_its_correction_asks_for_the_compaction_rider(self):
        # "Only modify X" is useless to a worker that has forgotten what the
        # task was. The guard asks; the loop decides what it costs.
        v = guards.first(self.scope(), plan(touch_scope=["src/a.py"]),
                         self.att(["other.py"]))
        self.assertTrue(v.rider)

    def test_the_fixture_correction_does_not(self):
        # It names every file and the exact line to add, so it stands alone.
        v = guards.first(FakeScope(["a.json"]), plan(touch_scope=None),
                         self.att([]))
        self.assertFalse(v.rider)


class SpecGuard(unittest.TestCase):
    """The most important guard in the system.

    A gate written by the thing being graded is not a gate. PRINCIPLES §I: *if
    the builder also writes the building inspection, a misunderstanding lands
    identically in the wall and in the checklist. They agree perfectly. They are
    both wrong.*
    """

    def setUp(self):
        self.reverted = []
        self._vs, self._rs = spec_guard.violated_specs, spec_guard.revert_specs
        spec_guard.revert_specs = lambda cwd, paths, base, t0: \
            self.reverted.append((tuple(paths), base))
        self.addCleanup(setattr, spec_guard, "violated_specs", self._vs)
        self.addCleanup(setattr, spec_guard, "revert_specs", self._rs)

    def violated(self, *paths):
        spec_guard.violated_specs = lambda cwd, base: list(paths)

    def att(self, writes=()):
        return Attempt(n=1, of=3, changed=[], writes=list(writes))

    def test_a_worker_edit_is_reverted_and_fails_the_attempt(self):
        self.violated("guard_spec.py")
        v = guards.first(FakeScope(), plan(), self.att(["guard_spec.py"]))
        self.assertEqual(v.kind, "spec_violation")
        self.assertIn("SPEC VIOLATION", v.trail)
        self.assertEqual(self.reverted[0][0], ("guard_spec.py",))

    def test_it_reverts_from_T0_not_from_HEAD(self):
        # The hole a mutation sweep found. If the worker COMMITTED its edit,
        # HEAD now holds the WEAKENED spec, so restoring from HEAD would
        # faithfully restore the sabotage -- the guard would run, report itself
        # as having reverted, and hand back a spec the worker wrote.
        self.violated("guard_spec.py")
        sc = FakeScope()
        guards.first(sc, plan(), self.att(["guard_spec.py"]))
        self.assertEqual(self.reverted[0][1], sc.pre_sha)

    def test_an_unattributed_spec_change_is_reported_never_reverted(self):
        # C10. Under a proxy that logs the worker's writes, a protected file
        # that moved with NO logged write is the caller's. Reverting it is how
        # a caller's concurrent work got destroyed.
        self.violated("guard_spec.py")
        sc = FakeScope(hooked=True)
        v = guards.first(sc, plan(), self.att(writes=[]))
        self.assertEqual(self.reverted, [], "the caller's own edit was reverted")
        self.assertEqual(sc.spec_unattributed, ["guard_spec.py"])
        self.assertIsNone(v.trail, "an unattributed change failed the attempt")
        self.assertIn("NOT reverted", v.notes[0])

    def test_it_says_so_only_once_across_attempts(self):
        # A trail repeating the same unattributed file every attempt is noise,
        # and noise is how a real line stops being read.
        self.violated("guard_spec.py")
        sc = FakeScope(hooked=True)
        guards.first(sc, plan(), self.att())
        again = guards.first(sc, plan(), self.att())
        self.assertIsNone(again)

    def test_the_workers_own_edit_still_counts_when_hooked(self):
        # Attribution must not become a blanket excuse.
        self.violated("guard_spec.py")
        v = guards.first(FakeScope(hooked=True), plan(),
                         self.att(writes=["guard_spec.py"]))
        self.assertIn("SPEC VIOLATION", v.trail)

    def test_the_correction_forbids_editing_rather_than_explaining_it(self):
        self.violated("guard_spec.py")
        v = guards.first(FakeScope(), plan(), self.att(["guard_spec.py"]))
        self.assertIn("Never modify a protected spec", v.prompt)
        self.assertIn("stop and say so", v.prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
