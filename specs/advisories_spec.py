#!/usr/bin/env python3
"""
Spec for qd/features/advisories.py -- G4, did the run build what was asked?

Claude-authored gate (never delegate this file -- it defines what correct means).

The one question nothing else answers. The gate proves the tests pass; the
detectors prove nothing was left behind, nothing is unwired, no seam was faked.
None of them compare the delivered work against what was ASKED FOR, and that is
the gap an exit code structurally cannot close: a confidently-built
misunderstanding passes its own tests perfectly.

**It must stay advisory.** PRINCIPLES §I -- the verdict is a command's exit code
and never anybody's account of the work. This is a WITNESS, from the same class
of thing that produced the code, and a witness that can refuse has been promoted
to a judge. Step 4 made "can refuse" a property of the TYPE precisely so this
could not acquire the power by being filed in the wrong directory.

Run:  python3 specs/advisories_spec.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from qd.features import advisories  # noqa: E402

FACTS = {"changed": ["a.py", "b.py"], "numstat": {"a.py": (10, 2)}}


def ask(reply):
    return lambda _prompt: reply


class TheVerdict(unittest.TestCase):
    def test_a_match_is_green(self):
        self.assertTrue(advisories.review(ask("MATCHES"), "do x", FACTS)["ok"])

    def test_a_named_gap_is_red(self):
        got = advisories.review(ask("MISSING: the retry clause"), "do x", FACTS)
        self.assertFalse(got["ok"])
        self.assertIn("retry clause", got["head"])

    def test_an_unparseable_answer_is_NOT_a_defect(self):
        # Defaulting the other way would make every parser hiccup look like a
        # finding about the work -- and a red line that is usually wrong is one
        # nobody reads, which costs more than the check is worth.
        self.assertTrue(advisories.review(ask("uhh, seems fine?"), "b", FACTS)["ok"])

    def test_a_broken_pass_never_looks_like_a_finding(self):
        def boom(_p):
            raise RuntimeError("endpoint down")
        got = advisories.review(boom, "b", FACTS)
        self.assertTrue(got["ok"])
        self.assertIn("skipped", got["head"])


class WhenItStaysSilent(unittest.TestCase):
    def test_a_run_that_changed_nothing_has_no_delivery_to_judge(self):
        self.assertIsNone(advisories.review(ask("MATCHES"), "b",
                                            {"changed": []}))

    def test_no_brief_means_nothing_to_compare(self):
        self.assertIsNone(advisories.review(ask("MATCHES"), "", FACTS))


class TheDiffSummary(unittest.TestCase):
    def test_it_sends_shape_not_content(self):
        # The pass answers "was every clause delivered", which is a question
        # about SHAPE. Feeding it the full diff would cost the context of a
        # second delegation to answer it worse.
        out = advisories.summarise(FACTS)
        self.assertIn("a.py", out)
        self.assertIn("+10/-2", out)

    def test_a_huge_change_set_is_capped_and_says_so(self):
        facts = {"changed": [f"f{i}.py" for i in range(60)], "numstat": {}}
        out = advisories.summarise(facts)
        self.assertIn("and 20 more", out)

    def test_an_empty_change_set_reads_as_empty(self):
        self.assertIn("no files changed", advisories.summarise({"changed": []}))


class TheInstruction(unittest.TestCase):
    """The prompt is the mechanism. A reviewer told to judge broadly will."""

    def test_it_asks_for_one_line(self):
        self.assertIn("exactly one line", advisories.PROMPT)

    def test_it_names_the_non_reasons(self):
        # The same lesson challenge_brief paid for live: without naming what
        # does NOT qualify, a reviewer objects on taste and the signal dies.
        low = advisories.PROMPT.lower()
        for non_reason in ("not code quality", "not naming",
                           "not whether you would have done it differently",
                           "still matches"):
            self.assertIn(non_reason, low)

    def test_it_defaults_toward_matches(self):
        self.assertIn("plausibly delivers every clause, say MATCHES",
                      advisories.PROMPT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
