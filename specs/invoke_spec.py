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

    def test_parse_qwen_json_jsonl_fallback_and_sid(self):
        jsonl = '{"type":"assistant","session_id":"s2"}\n{"broken\n'
        text, denials, sid = invoke.parse_qwen_json(jsonl)
        self.assertIsNone(text)
        self.assertEqual(sid, "s2")

    def test_truncate(self):
        self.assertEqual(invoke.truncate("abc", 10), "abc")
        out = invoke.truncate("x" * 20, 10)
        self.assertTrue(out.startswith("x" * 10))
        self.assertIn("truncated 10 chars", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
