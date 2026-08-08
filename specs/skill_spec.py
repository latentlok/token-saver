#!/usr/bin/env python3
"""
Spec for skills/delegation/SKILL.md -- the loop, as Claude reads it.

Claude-authored gate (never delegate this file -- it defines what correct means).

Item C. The skill is the only part of this system the ARCHITECT reads, so its
mistakes are not bugs in behaviour -- they are bugs in judgement, made by the
expensive model, on every project that loads it.

**What these tests are for.** Three content bugs were found in it (A2, A3, A10),
and the worst was not a wrong fact: it was the skill CONTRADICTING ITS OWN
MEASUREMENT forty lines later. That class is catchable, and nothing was catching
it, because prose is the one part of the repo nothing executes.

Pins the claims that cost tokens or capability when they drift, not the wording.

Run:  python3 specs/skill_spec.py
"""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, ROOT)


def read(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return " ".join(f.read().split())


class ItDoesNotContradictItself(unittest.TestCase):
    """The bug that motivated this file."""

    def setUp(self):
        self.skill = read("skills/delegation/SKILL.md")

    def test_it_carries_the_measurement_that_settles_the_question(self):
        # A rule without its evidence is one the next reader relaxes.
        self.assertIn("+64%", self.skill)

    def test_and_its_advice_agrees_with_that_measurement(self):
        # It used to say "architect-side graphify costs +64%" and then, forty
        # lines later, "query graphify's MCP" -- which IS architect-side.
        self.assertNotIn("Query graphify's MCP", self.skill)
        self.assertIn("don't query the graph yourself", self.skill.lower())

    def test_it_agrees_with_AGENT_about_whose_tool_the_graph_is(self):
        # Two documents disagreeing is worse than either being wrong, because
        # whichever the reader met first wins and neither knows.
        # The claim moved from docs/USAGE.md to AGENT.md when the pre-0.6.0
        # set was archived; the pairing is what this test protects, not the
        # filename. RED until AGENT.md carries the claim.
        agent_doc = read("AGENT.md")
        self.assertIn("the **worker** uses", agent_doc)
        self.assertIn("the WORKER's tool", self.skill)


class TheTwoThingsThatBite(unittest.TestCase):
    """A3 and A10 -- both silent failures, which is why they need saying."""

    def setUp(self):
        self.skill = read("skills/delegation/SKILL.md")

    def test_it_says_worker_side_shell_needs_scoped(self):
        # `auto-edit` has no shell at all, so a worker told to use the graph
        # under the default silently falls back to grep -- and you pay for the
        # reading you were trying to avoid, with nothing in the receipt saying
        # so.
        self.assertIn('approval_mode="scoped"', self.skill)
        self.assertIn("no shell at all", self.skill)

    def test_it_warns_that_a_command_pattern_allows_every_subcommand(self):
        # `^graphify\\b` permits `graphify update`, which can bill a cloud
        # account. PRINCIPLES §III: ask what the most powerful reachable thing
        # is, not what the list says.
        self.assertIn("not \"let it run cmd\"", self.skill)
        self.assertIn("every subcommand", self.skill)

    def test_it_gives_a_safe_pattern_rather_than_only_a_warning(self):
        # A warning without the fix gets read and not acted on.
        self.assertIn("explain|query|affected", self.skill)


class TheLoadBearingRules(unittest.TestCase):
    """The handful of claims the whole loop rests on."""

    def setUp(self):
        self.skill = read("skills/delegation/SKILL.md")

    def test_a_question_about_code_is_never_a_delegation(self):
        self.assertIn("query, never a delegation", self.skill)

    def test_it_says_to_go_cold_for_repairs(self):
        # A session that failed carries its confusion forward and argues with
        # the correction.
        low = self.skill.lower()
        self.assertIn("go cold for repairs", low)

    def test_it_treats_the_workers_own_words_as_leads(self):
        self.assertIn("leads to check, never trusted", self.skill)


if __name__ == "__main__":
    unittest.main(verbosity=2)
