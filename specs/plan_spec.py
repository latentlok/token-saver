#!/usr/bin/env python3
"""
Spec for qd/core/plan.py -- settings resolved once, in one place.

Claude-authored gate (never delegate this file -- it defines what correct means).

Step 6 of docs/DESIGN-modular-architecture.md. One rule, and everything else is
a consequence of it:

    None means "this layer did not answer".
    Every other value -- INCLUDING false, 0 and [] -- is an answer.

Why it needs its own spec rather than being obvious. Precedence was written
`args.get(x) or cfg.get(x)` at ~20 sites. That is correct for every non-falsy
value, which is most of them, which is exactly why it survived: the bug is
invisible until a caller deliberately says no, and saying no is rare.

When it does bite, it bites on the settings where saying no matters most. A
caller passing `shell_allow=[]` -- no extra shell capability at all -- was given
the project's list instead, which in the field could include `rm`. The caller
narrowed a boundary and the resolver widened it, silently, with no line anywhere
saying so. PRINCIPLES §III: the real boundary is the most powerful thing
reachable through what you permit, and here that was decided by a config file
the call had explicitly overridden.

Run:  python3 specs/plan_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.core.plan import RunPlan, setting  # noqa: E402


class Precedence(unittest.TestCase):
    def test_the_call_wins(self):
        self.assertEqual(setting("t", {"t": "call"}, {"t": "proj"}), "call")

    def test_the_project_wins_when_the_call_is_silent(self):
        self.assertEqual(setting("t", {}, {"t": "proj"}, {"t": "mach"}), "proj")

    def test_the_machine_wins_when_both_are_silent(self):
        self.assertEqual(setting("t", {}, {}, {"t": "mach"}), "mach")

    def test_the_builtin_is_the_last_resort(self):
        self.assertEqual(setting("t", {}, {}, {}, default="builtin"),
                         "builtin")

    def test_a_missing_layer_is_skipped_not_an_answer(self):
        self.assertEqual(setting("t", None, {"t": "proj"}), "proj")


class FalsyIsAnAnswer(unittest.TestCase):
    """The whole reason this module exists."""

    def test_false_switches_something_off(self):
        # The bug the engine already documented at ONE site: `or` chaining
        # would fall through `false` and silently re-enable what the caller
        # just switched off.
        self.assertIs(setting("on", {"on": False}, {"on": True}), False)

    def test_an_empty_list_means_none_not_unspecified(self):
        # The instance that mattered. `shell_allow=[]` is the most deliberate
        # answer a caller can give -- no extra capability -- and it was being
        # replaced by whatever the project declared.
        self.assertEqual(setting("allow", {"allow": []}, {"allow": ["^rm"]}),
                         [])

    def test_zero_is_a_number_not_a_silence(self):
        self.assertEqual(setting("budget", {"budget": 0}, {"budget": 900}), 0)

    def test_an_empty_string_is_an_answer(self):
        self.assertEqual(setting("suffix", {"suffix": ""}, {"suffix": "x"}), "")

    def test_only_none_falls_through(self):
        # The distinction the whole rule turns on: saying nothing and saying no
        # are different, and exactly one of them defers to the next layer.
        self.assertEqual(setting("t", {"t": None}, {"t": "proj"}), "proj")


class Narrowing(unittest.TestCase):
    """The security-shaped case, stated as itself rather than as an example."""

    def test_a_caller_can_always_narrow_below_the_project(self):
        project = {"shell_allow": ["^pytest", "^rm"]}
        self.assertEqual(setting("shell_allow", {"shell_allow": []}, project),
                         [])

    def test_a_silent_caller_still_inherits_the_project(self):
        # Silence means "use the project's policy". It must stay a DIFFERENT
        # answer from [], or fixing the widening would break every call that
        # legitimately relies on project defaults.
        project = {"shell_allow": ["^pytest"]}
        self.assertEqual(setting("shell_allow", {}, project), ["^pytest"])


class MergedLayersLoseInformation(unittest.TestCase):
    """Why layers are passed separately instead of pre-merged into one dict.

    The engine used to build `cfg = dict(machine); cfg.update(project)` and
    resolve against that. It gives the right answer for ordinary configs, and it
    was equivalent to the layered form everywhere it mattered -- but merging
    throws away WHICH layer answered, and one case needs that: a project config
    holding an explicit `null`.

    Under a merge, that null overwrites the machine's real value and the setting
    falls all the way to the builtin. Under layers, `null` means "this layer has
    no opinion" and the machine's value stands -- which is what the module's one
    rule says it should mean.

    Narrow, but it is the difference between a rule and a rule-of-thumb, and
    keeping the layers separate costs nothing.
    """

    def test_an_explicit_null_defers_rather_than_erasing(self):
        self.assertEqual(
            setting("expect", {}, {"expect": None}, {"expect": "green"}),
            "green")

    def test_a_merged_dict_would_have_lost_that(self):
        # Stated as the contrast it exists for, so the reason survives the
        # next person who thinks one dict would be simpler.
        merged = dict({"expect": "green"})
        merged.update({"expect": None})
        self.assertIsNone(merged.get("expect"))
        self.assertEqual(setting("expect", {}, merged, default="any"), "any")


class ThePlanRecord(unittest.TestCase):
    """WHAT WAS ASKED FOR, resolved once and frozen.

    The Builder half of step 6. `setting()` removed the duplicated precedence;
    this removes the reason features had to carry loose arguments about the
    caller's intent at all -- which is what finally deleted `DetectorInputs`,
    the step-2 scaffolding introduced with its own risk named out loud.
    """

    def test_it_resolves_every_layer_in_precedence_order(self):
        p = RunPlan.build({"task": "t", "verify": "call"},
                          {"verify": "proj", "trust": "verified"},
                          {"preflight_expect": "red"})
        self.assertEqual(p.verify, "call")
        self.assertEqual(p.trust, "verified")
        self.assertEqual(p.preflight_expect, "red")

    def test_it_falls_back_to_the_builtins(self):
        p = RunPlan.build({"task": "t"}, {}, {})
        self.assertEqual(p.trust, "self")
        self.assertEqual(p.preflight_expect, "any")

    def test_a_narrowed_answer_survives_into_the_plan(self):
        # The falsy rule, carried through the record rather than re-derived.
        p = RunPlan.build({"task": "t", "touch_scope": []},
                          {"touch_scope": ["src/"]}, {})
        self.assertEqual(p.touch_scope, [])

    def test_the_plan_cannot_be_rewritten_downstream(self):
        # A brief that a later feature can edit is not a brief. Same argument as
        # the frozen facts record, applied to intent instead of to evidence: a
        # feature reading plan.verify is reading what the CALLER asked for, not
        # what some earlier feature decided it should now be.
        p = RunPlan.build({"task": "t", "verify": "pytest"}, {}, {})
        with self.assertRaises(AttributeError):
            p.verify = "something else"

    def test_it_stays_small(self):
        """A record that mirrors the whole config is a config parser with extra
        steps, and the next person would add to it out of symmetry rather than
        need. This is the tripwire, and it has already fired once.

        The bar for adding a field: **a FEATURE consumes it.** Not "the engine
        resolves it" -- the engine resolves ~20 settings and they stay where
        they are.

        History, kept so the bar is visible rather than asserted:
          5 -> 7  `fixture_provenance` and `fixture_segments`, when the fixture
                  GUARD moved to features/guards/ and needed to know whether it
                  was switched on and what marks a fixture. Both are answers to
                  "what was asked for", and neither had another owner.
          8 -> 9  `contract_path`, for A2/A4: the clause-coverage GUARD and the
                  contract-pin GATE both need to know which document states the
                  criteria. Its DIGEST and CLAUSES are derived, not stored --
                  they are observations of a file, and a plan that cached them
                  would go stale the moment the file moved.
          7 -> 8  `brief_path`, when the brief GUARD moved. The document that
                  briefed the run is the plainest possible answer to "what was
                  asked for"; its T0 DIGEST went to RunScope instead, because
                  that is an observation of the tree, not a request.

        If this fires and the new field is not consumed by a feature, the answer
        is to leave it in the engine, not to raise the number.
        """
        self.assertEqual(len(RunPlan._fields), 9, RunPlan._fields)

    def test_the_fixture_settings_reached_the_plan(self):
        p = RunPlan.build({"fixture_provenance": True}, {"fixture_globs": ["fx"]},
                          {}, fixture_default=("fixtures",))
        self.assertTrue(p.fixture_provenance)
        self.assertEqual(p.fixture_segments, ["fx"])

    def test_a_project_can_still_switch_path_detection_off(self):
        # The falsy rule, carried into the plan: `[]` is an answer.
        p = RunPlan.build({}, {"fixture_globs": []}, {},
                          fixture_default=("fixtures",))
        self.assertEqual(p.fixture_segments, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
