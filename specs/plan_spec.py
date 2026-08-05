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

from qd.core.plan import setting  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
