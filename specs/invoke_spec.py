#!/usr/bin/env python3
"""
Spec for qd/invoke.py -- executor invocation + stats parsing (LLD "qd/invoke.py").

Claude-authored gate (never delegate this file -- it defines what correct means).

Port gate for v1's invoke_qwen/parse machinery plus the v2 seams. Load-bearing:

  1. ONE merged temp settings file, exported via QWEN_CODE_SYSTEM_SETTINGS_PATH:
     compaction hooks in EVERY mode (any session can compact), the scoped
     PreToolUse hook when mode='scoped', and the profile's settings_overlay
     (probe 6: the ONLY working endpoint/model switch) -- all merged, because
     they share a single env var.
  2. The command line comes from profiles.render_argv -- task text lands
     verbatim as one argv element; scoped runs as yolo underneath (the hook
     does the gating).
  3. parse_stats splits main vs overhead via bySource and RECORDS provenance
     ("bySource"/"blended"/"none") -- a zero that means "unmeasured" must never
     read as a real zero. accum_stats sums across attempts, worst-case
     provenance wins.
  4. peak_context is the MAX over assistant turns, never the summed
     result.usage (measured 50% overstatement).
  5. Timeout and missing-binary produce structured errors, not exceptions.

Public surface pinned here:
    run_executor(profile, task, cwd, mode, timeout=None, session_id=None,
                 verify=None, shell_allow=None, suffix="")
        -> (text, denials, session_id, err, meta)   # meta: {"peak","stats","blocked"}
    parse_qwen_json, parse_stats, norm_tokens, accum_stats, cum_zero,
    tok_zero, tok_add, peak_context, context_window, compaction_thresholds,
    truncate

Run:  python3 specs/invoke_spec.py
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qd import invoke  # noqa: E402

STUB = r"""#!/usr/bin/env python3
import json, os, shutil, sys, time
out = os.environ["STUB_OUT"]
open(os.path.join(out, "argv.json"), "w").write(json.dumps(sys.argv))
open(os.path.join(out, "env.json"), "w").write(json.dumps(dict(os.environ)))
sp = os.environ.get("QWEN_CODE_SYSTEM_SETTINGS_PATH")
if sp and os.path.isfile(sp):
    shutil.copy(sp, os.path.join(out, "settings.json"))
if os.environ.get("STUB_SLEEP"):
    time.sleep(float(os.environ["STUB_SLEEP"]))
sys.stdout.write(os.environ.get("STUB_STDOUT", "[]"))
"""

RESULT_JSON = json.dumps([
    {"type": "assistant",
     "message": {"usage": {"input_tokens": 20285, "output_tokens": 40}}},
    {"type": "assistant",
     "message": {"usage": {"input_tokens": 18000, "output_tokens": 10}}},
    {"type": "result", "result": "done text", "session_id": "sess-9",
     "permission_denials": [{"tool": "run_shell_command"}],
     "duration_ms": 1234, "num_turns": 2,
     "stats": {
         "tools": {"totalCalls": 5, "totalFail": 1,
                   "byName": {"edit": {}, "read_file": {}}},
         "files": {"totalLinesAdded": 7, "totalLinesRemoved": 2},
         "models": {"m1": {
             "api": {"totalErrors": 1},
             "tokens": {"prompt": 29421, "candidates": 124, "total": 29545,
                        "cached": 0, "thoughts": 0},
             "bySource": {
                 "main": {"tokens": {"prompt": 18993, "candidates": 100,
                                     "total": 19093}},
                 "managed-auto-memory-extractor": {
                     "tokens": {"prompt": 10428, "candidates": 24,
                                "total": 10452}}}}}}},
])


class Fixture(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.out = tempfile.mkdtemp()
        self.stub = os.path.join(self.td, "stub.py")
        with open(self.stub, "w") as f:
            f.write(STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IEXEC)

    def profile(self, **over):
        p = {
            "name": "stub", "endpoint": "stub-ep",
            "argv": [sys.executable, self.stub, "-p", "{task}",
                     "--approval-mode", "{mode}", "-o", "json",
                     "-r", "{resume}"],
            "env": {"STUB_OUT": self.out, "STUB_STDOUT": RESULT_JSON},
            "settings_overlay": None,
            "price_in_per_mtok": 0, "price_out_per_mtok": 0,
            "rules_file": "QWEN.md", "altitude": "lld",
            "defaults": {"workers": 1, "max_iterations": 3, "timeout": 30},
            "endpoint_cfg": {"name": "stub-ep", "parallel_max": 1},
        }
        p.update(over)
        return p

    def run_exec(self, **kw):
        args = dict(profile=self.profile(), task="TASK BODY", cwd=self.td,
                    mode="auto-edit")
        args.update(kw)
        return invoke.run_executor(**args)

    def recorded(self, name):
        with open(os.path.join(self.out, name)) as f:
            return json.load(f)


class Invocation(Fixture):
    def test_result_tuple_and_meta(self):
        text, denials, sid, err, meta = self.run_exec()
        self.assertIsNone(err)
        self.assertEqual(text, "done text")
        self.assertEqual(sid, "sess-9")
        self.assertEqual(len(denials), 1)
        self.assertEqual(meta["peak"], 20285)          # max, not sum
        self.assertEqual(meta["stats"]["turns"], 2)

    def test_task_verbatim_suffix_appended_mode_substituted(self):
        nasty = 'line1\n"quoted" $(sh) TASK'
        self.run_exec(task=nasty, suffix="\n---SUFFIX---")
        argv = self.recorded("argv.json")
        self.assertEqual(argv[2], nasty + "\n---SUFFIX---")  # ONE element
        self.assertEqual(argv[argv.index("--approval-mode") + 1], "auto-edit")
        self.assertNotIn("-r", argv)                    # no resume -> flag dropped

    def test_session_id_appends_resume(self):
        self.run_exec(session_id="abc-123")
        argv = self.recorded("argv.json")
        self.assertEqual(argv[argv.index("-r") + 1], "abc-123")

    def test_profile_env_and_suppress_yolo_reach_subprocess(self):
        self.run_exec()
        env = self.recorded("env.json")
        self.assertEqual(env.get("STUB_OUT"), self.out)
        self.assertEqual(env.get("QWEN_CODE_SUPPRESS_YOLO_WARNING"), "1")


class SettingsMerge(Fixture):
    def settings(self):
        with open(os.path.join(self.out, "settings.json")) as f:
            return json.load(f)

    def test_compact_hooks_in_every_mode(self):
        self.run_exec(mode="auto-edit")
        s = self.settings()
        self.assertIn("PreCompact", s.get("hooks", {}))
        self.assertIn("PostCompact", s.get("hooks", {}))
        self.assertNotIn("PreToolUse", s.get("hooks", {}))   # not scoped

    def test_overlay_merges_with_hooks_in_one_file(self):
        overlay = {"modelProviders": {"openai": [{"id": "m", "baseUrl": "http://x/v1"}]},
                   "model": {"name": "m", "baseUrl": "http://x/v1"}}
        self.run_exec(profile=self.profile(settings_overlay=overlay))
        s = self.settings()
        self.assertIn("PreCompact", s.get("hooks", {}))       # hooks survived
        self.assertEqual(s["model"]["name"], "m")             # overlay present
        self.assertIn("openai", s["modelProviders"])
        env = self.recorded("env.json")
        self.assertTrue(env.get("QWEN_CODE_SYSTEM_SETTINGS_PATH"))

    def test_scoped_runs_yolo_with_gate_hook_and_env(self):
        self.run_exec(mode="scoped", verify="python3 -c 'pass'",
                      shell_allow=["^make$"])
        argv = self.recorded("argv.json")
        self.assertEqual(argv[argv.index("--approval-mode") + 1], "yolo")
        s = self.settings()
        self.assertIn("PreToolUse", s.get("hooks", {}))
        self.assertIn("PreCompact", s.get("hooks", {}))
        env = self.recorded("env.json")
        self.assertEqual(env.get("QGATE_CWD"), os.path.realpath(self.td))
        self.assertEqual(env.get("QGATE_VERIFY"), "python3 -c 'pass'")
        self.assertEqual(json.loads(env.get("QGATE_EXTRA", "[]")), ["^make$"])

    def test_compaction_threshold_reaches_the_executor_settings(self):
        self.run_exec()
        s = self.recorded("settings.json")
        self.assertEqual(s["context"]["autoCompactThreshold"],
                         invoke.COMPACTION_PCT)

    def test_profile_can_override_the_threshold(self):
        self.run_exec(profile=self.profile(compaction_threshold=0.6))
        s = self.recorded("settings.json")
        self.assertEqual(s["context"]["autoCompactThreshold"], 0.6)

    def test_temp_settings_cleaned_up_after_run(self):
        self.run_exec()
        env = self.recorded("env.json")
        self.assertFalse(os.path.exists(env["QWEN_CODE_SYSTEM_SETTINGS_PATH"]))


class Failures(Fixture):
    def test_timeout_is_structured_error(self):
        p = self.profile(env={"STUB_OUT": self.out, "STUB_SLEEP": "5",
                              "STUB_STDOUT": RESULT_JSON})
        text, _, _, err, _ = self.run_exec(profile=p, timeout=1)
        self.assertIsNone(text)
        self.assertIn("timed out after 1s", err)

    def test_timeout_default_comes_from_profile(self):
        p = self.profile(env={"STUB_OUT": self.out, "STUB_SLEEP": "5"},
                         defaults={"workers": 1, "max_iterations": 3,
                                   "timeout": 1})
        text, _, _, err, _ = self.run_exec(profile=p)
        self.assertIn("timed out after 1s", err)

    def test_missing_binary_names_it(self):
        p = self.profile(argv=["/no/such/binary-xyz", "-p", "{task}",
                               "--approval-mode", "{mode}"])
        text, _, _, err, _ = self.run_exec(profile=p)
        self.assertIsNone(text)
        self.assertIn("binary-xyz", err)

    def test_unparseable_output_reports_tail(self):
        p = self.profile(env={"STUB_OUT": self.out,
                              "STUB_STDOUT": "not json at all"})
        text, _, _, err, _ = self.run_exec(profile=p)
        self.assertIsNone(text)
        self.assertIn("unparseable", err)


class CompactionHook(unittest.TestCase):
    """compact_hook.py end-to-end: it is the only thing inside the executor that
    can see a compaction coming, and the refusal policy rests on it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.hook = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "compact_hook.py")

    def fire(self, event, policy="reinject", sid="s-1", **extra):
        payload = {"hook_event_name": event, "session_id": sid,
                   "trigger": "auto"}
        payload.update(extra)
        env = dict(os.environ, QCOMPACT_DIR=self.dir, QCOMPACT_POLICY=policy)
        return subprocess.run([sys.executable, self.hook], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)

    def state(self, sid="s-1"):
        with open(os.path.join(self.dir, f"{sid}.json")) as f:
            return json.load(f)

    def test_precompact_under_refuse_exits_2_to_block(self):
        # 2 is qwen's documented "block compaction" code. Best effort upstream,
        # but sending anything else guarantees the compaction proceeds.
        p = self.fire("PreCompact", policy="refuse")
        self.assertEqual(p.returncode, 2)
        self.assertIn("refused", p.stderr.lower())

    def test_precompact_under_reinject_does_not_block(self):
        self.assertEqual(self.fire("PreCompact", policy="reinject").returncode, 0)

    def test_precompact_records_pending_not_events(self):
        # `events` means a summary HAPPENED -- it arms re-injection. A blocked
        # compaction recorded there would make the server treat surviving history
        # as summarised.
        self.fire("PreCompact", policy="refuse")
        st = self.state()
        self.assertEqual(len(st["pending"]), 1)
        self.assertEqual(st.get("events"), [])

    def test_postcompact_records_events_and_never_blocks(self):
        p = self.fire("PostCompact", policy="refuse", compact_summary="x" * 40)
        self.assertEqual(p.returncode, 0)          # too late to block
        st = self.state()
        self.assertEqual(len(st["events"]), 1)
        self.assertEqual(st["events"][0]["summary_chars"], 40)

    def test_summary_text_itself_is_never_written_to_the_marker(self):
        secret = "CUSTOMER_SECRET_" + "y" * 30
        self.fire("PostCompact", compact_summary=secret)
        with open(os.path.join(self.dir, "s-1.json")) as f:
            self.assertNotIn("CUSTOMER_SECRET", f.read())

    def test_counts_read_both_lists(self):
        from qd import invoke as inv
        saved, inv.COMPACT_DIR = inv.COMPACT_DIR, self.dir
        try:
            self.assertEqual(inv.compaction_counts("s-1"), (0, 0))   # no marker
            self.fire("PreCompact", policy="refuse")
            self.assertEqual(inv.compaction_counts("s-1"), (0, 1))
            self.fire("PostCompact")
            self.assertEqual(inv.compaction_counts("s-1"), (1, 1))
            self.assertEqual(inv.compaction_counts(None), (0, 0))
        finally:
            inv.COMPACT_DIR = saved

    def test_a_malformed_payload_never_breaks_the_run(self):
        env = dict(os.environ, QCOMPACT_DIR=self.dir, QCOMPACT_POLICY="refuse")
        p = subprocess.run([sys.executable, self.hook], input="not json",
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0)


class PureFunctions(unittest.TestCase):
    def test_norm_tokens_both_spellings(self):
        self.assertEqual(invoke.norm_tokens({"prompt": 1, "candidates": 2,
                                             "total": 3})["completion"], 2)
        self.assertEqual(invoke.norm_tokens({"prompt": 1, "completion": 5,
                                             "total": 6})["completion"], 5)
        self.assertEqual(invoke.norm_tokens(None)["total"], 0)

    def test_parse_stats_by_source_split(self):
        st = invoke.parse_stats(RESULT_JSON)
        self.assertEqual(st["token_source"], "bySource")
        self.assertEqual(st["tokens"]["total"], 29545)
        self.assertEqual(st["tokens_main"]["prompt"], 18993)
        self.assertEqual(st["tokens_overhead"]["prompt"], 10428)
        self.assertEqual(st["tools"], 5)
        self.assertEqual(st["tool_fail"], 1)
        self.assertEqual(st["tool_names"], ["edit", "read_file"])
        self.assertEqual(st["api_errors"], 1)

    def test_parse_stats_blended_when_no_by_source(self):
        payload = json.loads(RESULT_JSON)
        del payload[2]["stats"]["models"]["m1"]["bySource"]
        st = invoke.parse_stats(json.dumps(payload))
        self.assertEqual(st["token_source"], "blended")
        self.assertEqual(st["tokens_main"]["total"], 29545)

    def test_accum_worst_case_provenance_and_attempts(self):
        cum = invoke.cum_zero()
        invoke.accum_stats(cum, {"token_source": "bySource",
                                 "tokens": {"prompt": 10, "completion": 1,
                                            "total": 11, "cached": 0,
                                            "thoughts": 0}})
        invoke.accum_stats(cum, {"token_source": "blended"})
        self.assertEqual(cum["token_source"], "blended")  # worst case wins
        self.assertEqual(cum["attempts"], 2)
        self.assertEqual(cum["tokens"]["total"], 11)

    def test_peak_context_is_max_not_sum(self):
        self.assertEqual(invoke.peak_context(RESULT_JSON), 20285)
        self.assertEqual(invoke.peak_context("garbage"), 0)

    def test_compaction_thresholds_match_measured(self):
        warn, auto = invoke.compaction_thresholds(196608)
        self.assertEqual(int(auto), 163608)
        self.assertEqual(int(warn), 143608)

    def test_absolute_ceiling_binds_below_a_220k_window(self):
        # The headline case: at 196,608 the configured pct is IRRELEVANT because
        # window-33,000 is the smaller term. A reading of this that credits the pct
        # would report a trigger ~29k tokens later than the real one.
        for pct in (0.85, 0.98, 1.0):
            _, auto = invoke.compaction_thresholds(196608, pct)
            self.assertEqual(int(auto), 163608, f"pct={pct}")

    def test_pct_binds_on_a_large_enough_window(self):
        _, at_85 = invoke.compaction_thresholds(1_000_000, 0.85)
        _, at_98 = invoke.compaction_thresholds(1_000_000, 0.98)
        self.assertEqual(int(at_85), 850_000)
        self.assertEqual(int(at_98), 967_000)      # ceiling is 967,000 here

    def test_default_pct_is_the_one_we_configure_not_upstreams(self):
        # If these drift apart, every CONTEXT warning describes a trigger the
        # executor is not using.
        self.assertEqual(invoke.compaction_thresholds(1_000_000),
                         invoke.compaction_thresholds(1_000_000,
                                                      invoke.COMPACTION_PCT))

    def test_pct_is_clamped_not_trusted(self):
        _, high = invoke.compaction_thresholds(1_000_000, 5.0)
        self.assertEqual(int(high), 967_000)       # clamped to 1.0, then ceiling
        _, low = invoke.compaction_thresholds(1_000_000, -1)
        self.assertEqual(int(low), 0)

    def test_ceiling_is_the_answer_to_how_late_can_it_compact(self):
        self.assertEqual(invoke.compaction_ceiling(196_608), 163_608)
        self.assertEqual(invoke.compaction_ceiling(227_000), 194_000)
        self.assertEqual(invoke.compaction_ceiling(10_000), 0)   # never negative

    def test_a_reachable_target_resolves_to_its_exact_pct(self):
        pct, ok = invoke.compaction_pct_for(227_000, 194_000)
        self.assertTrue(ok)
        _, auto = invoke.compaction_thresholds(227_000, pct)
        self.assertEqual(int(auto), 194_000)

    def test_an_unreachable_target_is_reported_not_silently_clamped(self):
        # 194,000 on a 196,608 window: the reserve makes it impossible, and a
        # caller that believes it was configured will size tasks on a number the
        # executor never uses.
        pct, ok = invoke.compaction_pct_for(196_608, 194_000)
        self.assertFalse(ok)
        _, auto = invoke.compaction_thresholds(196_608, pct)
        self.assertEqual(int(auto), 163_608)       # what it will REALLY be

    def test_target_with_no_known_window_is_unreachable_not_a_crash(self):
        pct, ok = invoke.compaction_pct_for(None, 194_000)
        self.assertFalse(ok)
        self.assertEqual(pct, invoke.COMPACTION_PCT)

    def test_parse_qwen_json_jsonl_fallback_and_sid(self):
        jsonl = '{"type":"assistant","session_id":"s2"}\n{"broken\n'
        text, denials, sid = invoke.parse_qwen_json(jsonl)
        self.assertIsNone(text)
        self.assertEqual(sid, "s2")

    def test_error_result_is_a_failure_not_an_empty_success(self):
        # An error record carries is_error + error.message and NO "result"
        # field. Read like a success it yields "" -- the run reads GREEN with
        # an empty answer and qwen's own account of why is dropped. That is
        # the silent-failure class this system exists to catch.
        out = json.dumps([{"type": "result", "subtype": "error_during_execution",
                           "is_error": True, "session_id": "s-9",
                           "error": {"message": "boom in the executor"}}])
        err = invoke.result_error(out)
        self.assertIn("boom in the executor", err)
        self.assertIn("executor reported failure", err)

    def test_truncation_error_names_the_cause_and_forbids_debugging(self):
        # The cap is CLIENT-side (qwen-code sends max_tokens itself), so a
        # reader who trusts the endpoint config concludes "no cap set" and
        # goes hunting for a bug in the repo. The receipt must say where the
        # limit lives and that it is not this codebase.
        out = json.dumps({"type": "result", "is_error": True,
                          "error": {"message": "response truncated: max_tokens"}})
        err = invoke.result_error(out)
        self.assertIn("QWEN_CODE_MAX_OUTPUT_TOKENS", err)
        self.assertIn("do not debug the plugin", err.lower())

    def test_successful_result_is_not_an_error(self):
        self.assertIsNone(invoke.result_error(RESULT_JSON))
        self.assertIsNone(invoke.result_error(""))
        self.assertIsNone(invoke.result_error("not json at all"))

    def test_truncate(self):
        self.assertEqual(invoke.truncate("abc", 10), "abc")
        out = invoke.truncate("x" * 20, 10)
        self.assertTrue(out.startswith("x" * 10))
        self.assertIn("truncated 10 chars", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
