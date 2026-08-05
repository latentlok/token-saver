#!/usr/bin/env python3
"""
Spec for qd/doctor.py -- the per-machine executor audit.

Claude-authored gate (never delegate this file -- it defines what correct means).

Why this module is gated at all: it is the ONLY part of the plugin whose subject
does not travel with the repo. Everything else is code we ship; `~/.qwen/settings.json`
belongs to the machine, and the two settings that decide whether a delegation
returns whole work or a truncated fragment live there. The load-bearing cases:

  1. normalize_model reproduces qwen-code's `split(":").pop()`. If this drifts,
     the audit clears a name that qwen-code will silently cap at 32k -- a false
     all-clear is worse than no check, because it ends the investigation.
  2. An unrecognised name is HIGH: it is the difference between the 64k a
     qwen3.6 gets and the 32k default an Ollama-tagged one gets.
  3. An explicit maxTokens silences the cap findings -- the user has decided,
     and a doctor that keeps nagging gets ignored on the run that matters.
  4. A declared contextWindowSize is always reported: the receipt's compaction
     lines are computed from it and nothing verifies it.
  5. --fix writes ONLY maxTokens, keeps a backup, and never invents a context
     window it cannot measure.
  6. summary_line is silent unless something is HIGH (it lands in receipts).

Run:  python3 specs/doctor_spec.py
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import doctor  # noqa: E402


def settings(model="qwen3.6:27b-agent", gen=None):
    return {
        "model": {"name": model},
        "modelProviders": {"openai": [
            {"id": model, "name": model, "generationConfig": gen or {}},
        ]},
    }


def ids(findings):
    return [f["id"] for f in findings]


def by_id(findings, fid):
    return next(f for f in findings if f["id"] == fid)


class Normalize(unittest.TestCase):
    """Case 1: the exact upstream rule, colon included."""

    def test_ollama_tag_keeps_only_what_follows_the_colon(self):
        self.assertEqual(doctor.normalize_model("qwen3.6:27b-agent"), "27b-agent")
        self.assertEqual(doctor.normalize_model("qwen3.6-27b-agent"),
                         "qwen3.6-27b-agent")

    def test_path_and_pipe_prefixes_are_stripped(self):
        self.assertEqual(doctor.normalize_model("hf.co/org/Qwen3.6-27B"),
                         "qwen3.6-27b")
        self.assertEqual(doctor.normalize_model("openai|qwen3.6-27b"),
                         "qwen3.6-27b")

    def test_output_limit_recognised_vs_default(self):
        self.assertEqual(doctor.output_limit("qwen3.6-27b"), (64_000, True))
        self.assertEqual(doctor.output_limit("qwen2.5-coder"), (32_768, True))
        # The tagged form loses its family and takes the generic default.
        self.assertEqual(doctor.output_limit("qwen3.6:27b-agent"),
                         (doctor.DEFAULT_OUTPUT_TOKEN_LIMIT, False))


class Findings(unittest.TestCase):
    def test_unrecognised_name_is_high_and_says_the_server_cannot_show_it(self):
        f = by_id(doctor.check(settings()), "unrecognised-model-name")
        self.assertEqual(f["severity"], "high")
        self.assertTrue(f["fixable"])
        self.assertIn("27b-agent", f["text"])
        self.assertIn("32,000", f["text"])

    def test_recognised_name_is_only_an_info_note(self):
        found = doctor.check(settings(model="qwen3.6-27b"))
        self.assertNotIn("unrecognised-model-name", ids(found))
        self.assertEqual(by_id(found, "no-explicit-max-tokens")["severity"],
                         "info")

    def test_explicit_max_tokens_silences_the_cap_findings(self):
        found = doctor.check(settings(gen={"maxTokens": 16384}))
        self.assertNotIn("unrecognised-model-name", ids(found))
        self.assertNotIn("no-explicit-max-tokens", ids(found))

    def test_thinking_without_a_cap_is_high(self):
        found = doctor.check(settings(
            gen={"extra_body": {"enable_thinking": True}}))
        self.assertEqual(by_id(found, "thinking-against-output-cap")["severity"],
                         "high")

    def test_thinking_with_a_cap_is_not_reported(self):
        found = doctor.check(settings(
            gen={"maxTokens": 8192, "extra_body": {"enable_thinking": True}}))
        self.assertNotIn("thinking-against-output-cap", ids(found))

    def test_declared_context_window_is_always_flagged_unverified(self):
        f = by_id(doctor.check(settings(gen={"contextWindowSize": 196608})),
                  "context-window-unverified")
        self.assertEqual(f["severity"], "high")
        self.assertIn("196,608", f["text"])
        # The finding has to tell the reader HOW to check the number, not just
        # that it is unchecked -- an unactionable "high" is noise. Asserted as
        # "names the recording command", not as a vendor's shell incantation:
        # the old assertion pinned `ollama ps`, so retiring Ollama broke a spec
        # that was never about Ollama.
        self.assertIn("--verified", f["text"])

    def test_a_verified_window_stops_being_a_high_finding(self):
        cfg = os.path.join(tempfile.mkdtemp(), "config.json")
        os.environ["QWEN_DELEGATE_CONFIG"] = cfg
        try:
            s = settings(gen={"contextWindowSize": 196608})
            self.assertIn("context-window-unverified", ids(doctor.check(s)))
            doctor.record_verified(196608)
            found = doctor.check(s)
            self.assertNotIn("context-window-unverified", ids(found))
            self.assertEqual(by_id(found, "context-window-verified")["severity"],
                             "info")
        finally:
            os.environ.pop("QWEN_DELEGATE_CONFIG", None)

    def test_a_verified_value_that_stops_matching_flags_again(self):
        # The declaration was raised (or the box reloaded smaller) after someone
        # checked. Silence here would be worse than never having checked.
        cfg = os.path.join(tempfile.mkdtemp(), "config.json")
        os.environ["QWEN_DELEGATE_CONFIG"] = cfg
        try:
            doctor.record_verified(196608)
            f = by_id(doctor.check(settings(gen={"contextWindowSize": 227000})),
                      "context-window-unverified")
            self.assertEqual(f["severity"], "high")
            self.assertIn("no longer", f["text"])
        finally:
            os.environ.pop("QWEN_DELEGATE_CONFIG", None)

    def test_record_verified_preserves_other_config_keys(self):
        cfg = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(cfg, "w") as f:
            json.dump({"trust": "verified", "dispatch": "serial"}, f)
        os.environ["QWEN_DELEGATE_CONFIG"] = cfg
        try:
            doctor.record_verified(196608)
            with open(cfg) as f:
                got = json.load(f)
            self.assertEqual(got["trust"], "verified")
            self.assertEqual(got["dispatch"], "serial")
            self.assertEqual(got["verified_context_window"], 196608)
        finally:
            os.environ.pop("QWEN_DELEGATE_CONFIG", None)

    def test_compaction_ceiling_is_stated_in_tokens(self):
        # The question this answers is "how late can it compact?", and the answer
        # is not the threshold anyone configured.
        f = by_id(doctor.check(settings(gen={"contextWindowSize": 196608})),
                  "compaction-ceiling")
        self.assertIn("163,608", f["text"])
        self.assertIn("33,000", f["text"])
        self.assertIn("227,000", f["text"])       # what 194k would require

    def test_missing_model_is_a_single_high_finding(self):
        found = doctor.check({"model": {}})
        self.assertEqual(ids(found), ["no-model"])

    def test_high_findings_sort_first(self):
        found = doctor.check(settings(gen={"contextWindowSize": 196608}))
        sev = [f["severity"] for f in found]
        self.assertEqual(sev, sorted(sev, key=lambda s: 0 if s == "high" else 1))


class Fix(unittest.TestCase):
    """Case 5: write the one fix that is knowable, and only that one."""

    def test_fix_sets_max_tokens_on_the_active_provider(self):
        s, changed = doctor.apply_fix(settings())
        self.assertTrue(changed)
        prov, _ = doctor.active_provider(s)
        self.assertEqual(prov["generationConfig"]["maxTokens"],
                         doctor.RECOMMENDED_MAX_TOKENS)

    def test_fix_is_idempotent(self):
        s, _ = doctor.apply_fix(settings())
        _, changed = doctor.apply_fix(s)
        self.assertFalse(changed)

    def test_fix_never_invents_a_context_window(self):
        s, _ = doctor.apply_fix(settings())
        prov, _ = doctor.active_provider(s)
        self.assertNotIn("contextWindowSize", prov["generationConfig"])

    def test_fix_preserves_unrelated_settings(self):
        base = settings(gen={"extra_body": {"enable_thinking": True}})
        base["security"] = {"auth": {"selectedType": "openai"}}
        s, _ = doctor.apply_fix(base)
        self.assertEqual(s["security"], {"auth": {"selectedType": "openai"}})
        prov, _ = doctor.active_provider(s)
        self.assertTrue(prov["generationConfig"]["extra_body"]["enable_thinking"])

    def test_cli_fix_writes_a_backup_and_valid_json(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "settings.json")
        with open(path, "w") as f:
            json.dump(settings(), f)
        rc = doctor.main(["--settings", path, "--fix"])
        self.assertIn(rc, (0, 1))
        self.assertTrue(os.path.isfile(path + ".bak"))
        with open(path) as f:
            written = json.load(f)
        prov, _ = doctor.active_provider(written)
        self.assertEqual(prov["generationConfig"]["maxTokens"],
                         doctor.RECOMMENDED_MAX_TOKENS)
        with open(path + ".bak") as f:                 # original recoverable
            self.assertNotIn("maxTokens",
                             json.load(f)["modelProviders"]["openai"][0]
                             ["generationConfig"])

    def test_fix_accepts_an_explicit_budget(self):
        # A thinking model spends this same budget reasoning, so the right number
        # is the caller's, not a constant baked in here.
        d = tempfile.mkdtemp()
        path = os.path.join(d, "settings.json")
        with open(path, "w") as f:
            json.dump(settings(), f)
        doctor.main(["--settings", path, "--fix", "100000"])
        with open(path) as f:
            prov, _ = doctor.active_provider(json.load(f))
        self.assertEqual(prov["generationConfig"]["maxTokens"], 100000)

    def test_default_budget_is_never_below_a_recognised_models_limit(self):
        # --fix must not leave a model worse off than correct naming would have.
        self.assertGreaterEqual(doctor.RECOMMENDED_MAX_TOKENS,
                                doctor.output_limit("qwen3.6-27b")[0])

    def test_cli_on_a_missing_file_says_it_is_per_machine(self):
        rc = doctor.main(["--settings", "/nonexistent/settings.json"])
        self.assertEqual(rc, 2)


class Summary(unittest.TestCase):
    """Case 6: a receipt line must be silent unless it is worth the words."""

    def write(self, obj):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "settings.json")
        with open(p, "w") as f:
            json.dump(obj, f)
        return p

    def test_silent_when_nothing_is_high(self):
        self.assertIsNone(doctor.summary_line(
            self.write(settings(model="qwen3.6-27b", gen={"maxTokens": 8192}))))

    def test_names_the_findings_and_denies_a_repo_bug(self):
        line = doctor.summary_line(self.write(settings()))
        self.assertIn("unrecognised-model-name", line)
        self.assertIn("not a repo bug", line)

    def test_unreadable_settings_are_silent_never_an_exception(self):
        self.assertIsNone(doctor.summary_line("/nonexistent/settings.json"))


class StaleServerHint(unittest.TestCase):
    """A finding that names the problem and leaves the reader to reconstruct
    the query is a finding they postpone. `_server_count` already ran the
    pgrep; the message should carry its answer."""

    def test_it_names_the_pids_to_kill(self):
        out = doctor._kill_hint([111, 222, 333])
        self.assertIn("kill", out)
        self.assertIn("111", out)
        self.assertIn("222", out)

    def test_it_never_names_this_process(self):
        # The newest server is the one the caller is talking to. Telling them to
        # kill it would end the session that asked.
        me = os.getpid()
        self.assertNotIn(str(me), doctor._kill_hint([me]))

    def test_it_keeps_the_newest_of_several(self):
        # pgrep lists oldest first, so the LAST is the newest -- kill the rest.
        out = doctor._kill_hint([111, 222, 333])
        self.assertNotIn("333", out)

    def test_unknown_pids_still_give_an_instruction(self):
        for pids in ([], None):
            self.assertIn("Kill the stale ones", doctor._kill_hint(pids))

    def test_a_single_stale_server_is_still_named(self):
        # With one other server, "all but the newest" would be empty -- the
        # caller would get an instruction with nothing to act on.
        self.assertIn("111", doctor._kill_hint([111]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
