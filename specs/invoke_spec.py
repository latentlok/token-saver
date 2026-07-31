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
                 verify=None, shell_allow=None, suffix="", observe_hook=False)
        -> (text, denials, session_id, err, meta)
        # meta: {"peak","stats","blocked","writes","allowed"}
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
import time
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
# Stand in for scoped_hook.py writing its QGATE_* logs during the run: the
# server can only read what the hook left behind in the per-run tempdir.
for env_key, stub_key in (("QGATE_DENYLOG", "STUB_DENIED"),
                          ("QGATE_WRITELOG", "STUB_WRITES"),
                          ("QGATE_ALLOWLOG", "STUB_ALLOWED")):
    target, payload = os.environ.get(env_key), os.environ.get(stub_key)
    if target and payload:
        with open(target, "a") as f:
            for ln in json.loads(payload):
                f.write(ln + "\n")
if os.environ.get("STUB_SLEEP"):
    time.sleep(float(os.environ["STUB_SLEEP"]))
# Streaming mode: one record per line, flushed, with a beat between them, so a
# reader that only sees output at exit is distinguishable from one that does not.
if os.environ.get("STUB_STREAM"):
    for line in json.loads(os.environ["STUB_STREAM"]):
        sys.stdout.write(json.dumps(line) + "\n")
        sys.stdout.flush()
        time.sleep(float(os.environ.get("STUB_GAP", "0.05")))
# Fill stderr NOBODY drains in the naive implementation. Well past a 64KB pipe
# buffer: with a single-pipe reader the child blocks here forever and the test
# hangs. This is the case a small stub would never reach.
if os.environ.get("STUB_STDERR_BYTES"):
    sys.stderr.write("x" * int(os.environ["STUB_STDERR_BYTES"]))
    sys.stderr.flush()
if not os.environ.get("STUB_STREAM"):
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


class AttributionLogs(Fixture):
    """C10/U1.1: the gated run exports the allow-side log paths and reads all
    three back into meta. They are the only evidence the engine has for saying
    'the worker wrote this one' -- and they live in a tempdir this function
    deletes, so reading them late is reading nothing."""

    def settings(self):
        with open(os.path.join(self.out, "settings.json")) as f:
            return json.load(f)

    def gated_profile(self, writes=(), allowed=(), denied=()):
        return self.profile(env={
            "STUB_OUT": self.out, "STUB_STDOUT": RESULT_JSON,
            "STUB_WRITES": json.dumps(list(writes)),
            "STUB_ALLOWED": json.dumps(list(allowed)),
            "STUB_DENIED": json.dumps(list(denied))})

    def test_scoped_exports_both_allow_side_logs(self):
        self.run_exec(mode="scoped", verify="make test")
        env = self.recorded("env.json")
        for key in ("QGATE_WRITELOG", "QGATE_ALLOWLOG"):
            self.assertTrue(env.get(key), key)
        self.assertEqual(env.get("QGATE_MODE"), "scoped")

    def test_meta_carries_writes_and_allowed_back(self):
        _, _, _, err, meta = self.run_exec(
            mode="scoped",
            profile=self.gated_profile(
                writes=["/repo/a.py", "/repo/b.py", "/repo/a.py"],
                allowed=["run_shell_command: graphify query x", "ungated:web"],
                denied=["run_shell_command: rm x  (state-changing)"]))
        self.assertIsNone(err)
        self.assertEqual(meta["writes"], ["/repo/a.py", "/repo/b.py"])  # deduped
        self.assertEqual(meta["allowed"],
                         ["run_shell_command: graphify query x", "ungated:web"])
        self.assertEqual(meta["blocked"],
                         ["run_shell_command: rm x  (state-changing)"])

    def test_ungated_run_reports_empty_logs_not_missing_keys(self):
        # A consumer that has to distinguish "no writes" from "no key" writes
        # the check twice and gets one of them wrong.
        _, _, _, _, meta = self.run_exec(mode="auto-edit")
        self.assertEqual(meta["writes"], [])
        self.assertEqual(meta["allowed"], [])

    def test_observe_hook_runs_auto_edit_as_yolo_with_the_hook(self):
        self.run_exec(mode="auto-edit", observe_hook=True)
        argv = self.recorded("argv.json")
        self.assertEqual(argv[argv.index("--approval-mode") + 1], "yolo")
        self.assertIn("PreToolUse", self.settings().get("hooks", {}))
        env = self.recorded("env.json")
        self.assertEqual(env.get("QGATE_MODE"), "autoedit")
        self.assertTrue(env.get("QGATE_WRITELOG"))

    def test_absent_flag_leaves_argv_and_env_byte_identical(self):
        # U1.4 ships dark: the inertness pin. The temp settings path is a fresh
        # mkdtemp per call, so it is the one value that legitimately differs.
        self.run_exec(mode="auto-edit")
        argv_a, env_a = self.recorded("argv.json"), self.recorded("env.json")
        self.run_exec(mode="auto-edit", observe_hook=False)
        argv_b, env_b = self.recorded("argv.json"), self.recorded("env.json")
        volatile = "QWEN_CODE_SYSTEM_SETTINGS_PATH"
        self.assertEqual(argv_a, argv_b)
        self.assertEqual({k: v for k, v in env_a.items() if k != volatile},
                         {k: v for k, v in env_b.items() if k != volatile})
        self.assertEqual([k for k in env_b if k.startswith("QGATE_")], [])
        self.assertNotIn("PreToolUse", self.settings().get("hooks", {}))


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


class Streaming(Fixture):
    """Phase 1 of streamed execution: records are read AS THEY ARRIVE, and the
    reader can stop the run. Load-bearing claims, worst-first:

      1. Both pipes are drained concurrently. A child that writes more to
         stderr than a pipe buffer holds deadlocks against a single-pipe
         reader -- and only at volume, so the small-output tests all pass
         while a real 19M-token run hangs.
      2. Streaming changes WHEN we read, not WHAT: the accumulated output
         parses to the same result the batch format produced, so every parser
         and every receipt downstream is untouched.
      3. Lines reach the callback before the process exits (otherwise this is
         a refactor with no observable behavior, and nothing to gate).
      4. A callback may stop the run, and the receipt says WE stopped it --
         blaming the executor for our decision sends someone debugging it.
      5. An unrecognised argv degrades to batch rather than refusing to run.
    """

    def stream_profile(self, records, **over):
        env = {"STUB_OUT": self.out, "STUB_STREAM": json.dumps(records)}
        env.update(over.pop("env", {}))
        return self.profile(env=env, **over)

    def test_argv_switches_to_the_streaming_format(self):
        argv = invoke.stream_argv(["qwen", "-p", "T", "-o", "json", "-r", "s"])
        self.assertEqual(argv[4], "stream-json")
        # ...and the long spelling
        self.assertEqual(
            invoke.stream_argv(["qwen", "--output-format", "json"])[2],
            "stream-json")

    def test_unrecognised_argv_is_left_alone(self):
        # Degrade to batch; never refuse to run over an output flag.
        argv = ["qwen", "-p", "T"]
        self.assertEqual(invoke.stream_argv(argv), argv)

    def test_batches_when_nobody_is_watching(self):
        # Streaming costs the tool and line counts (the streaming adapter's
        # result record carries no `stats` at all). A caller with no on_line
        # buys nothing with that, so it must not pay it.
        self.run_exec()
        self.assertNotIn("stream-json", self.recorded("argv.json"))

    def test_streams_when_a_callback_is_attached(self):
        self.run_exec(on_line=lambda r: None)
        self.assertIn("stream-json", self.recorded("argv.json"))

    def test_a_profile_can_opt_out(self):
        self.run_exec(profile=self.profile(stream=False), on_line=lambda r: None)
        self.assertNotIn("stream-json", self.recorded("argv.json"))

    def test_streamed_tokens_fall_back_to_the_result_usage(self):
        # Measured against a real -o stream-json run: the result record has
        # duration_ms / num_turns / usage / permission_denials / result and NO
        # stats. Without this fallback a streamed run reports 0 tokens, which
        # silently voids BURN, COST, and any budget built on them.
        streamed = json.dumps({
            "type": "result", "result": "done", "session_id": "s-1",
            "duration_ms": 48995, "num_turns": 7,
            "usage": {"input_tokens": 1_200_000, "output_tokens": 3_400},
        })
        st = invoke.parse_stats(streamed)
        self.assertEqual(st["tokens"]["prompt"], 1_200_000)
        self.assertEqual(st["tokens"]["completion"], 3_400)
        self.assertEqual(st["ms"], 48995)
        self.assertEqual(st["turns"], 7)
        # Provenance is recorded, so "no tools" is never mistaken for measured.
        self.assertEqual(st["token_source"], "usage")

    def test_the_stats_split_still_wins_when_present(self):
        # Batch runs keep the richer bySource split; the fallback must not
        # shadow it.
        st = invoke.parse_stats(RESULT_JSON)
        self.assertEqual(st["token_source"], "bySource")
        self.assertEqual(st["tokens"]["prompt"], 29421)

    def test_streamed_records_parse_to_the_same_result(self):
        # Case 2: the whole point of phase 1 -- nothing downstream changes.
        records = json.loads(RESULT_JSON)
        text, denials, sid, err, meta = self.run_exec(
            profile=self.stream_profile(records))
        self.assertIsNone(err)
        self.assertEqual(text, "done text")
        self.assertEqual(sid, "sess-9")
        self.assertEqual(meta["peak"], 20285)              # parsed from the stream
        self.assertEqual(meta["stats"]["tools"], 5)

    def test_lines_arrive_before_the_process_exits(self):
        # Case 3. Four records with a gap each; the first must be seen well
        # before the last one is written.
        records = [{"type": "assistant", "n": i} for i in range(4)]
        seen = []
        t0 = time.time()
        self.run_exec(
            profile=self.stream_profile(records, env={"STUB_GAP": "0.3"}),
            on_line=lambda r: seen.append((r.get("n"), time.time() - t0)))
        self.assertEqual([n for n, _ in seen], [0, 1, 2, 3])
        first, last = seen[0][1], seen[-1][1]
        self.assertLess(first, last - 0.5,
                        f"all records arrived together ({first:.2f}..{last:.2f}s)"
                        " -- output was batched, not streamed")

    def test_callback_can_stop_the_run(self):
        records = [{"type": "assistant", "n": i} for i in range(50)]
        seen = []

        def stop_at_two(record):
            seen.append(record)
            return "budget exceeded" if len(seen) >= 2 else None

        t0 = time.time()
        text, _, _, err, _ = self.run_exec(
            profile=self.stream_profile(records, env={"STUB_GAP": "0.1"}),
            on_line=stop_at_two)
        self.assertLess(time.time() - t0, 3.0, "did not stop early")
        self.assertLess(len(seen), 50)
        self.assertIsNone(text)                       # nothing to grade
        self.assertIn("run stopped", err)             # case 4: OUR decision...
        self.assertIn("budget exceeded", err)         # ...and the reason

    def test_a_raising_callback_does_not_kill_the_run(self):
        # An observer must not take down the thing it observes.
        records = json.loads(RESULT_JSON)

        def boom(record):
            raise RuntimeError("observer blew up")

        text, _, _, err, _ = self.run_exec(
            profile=self.stream_profile(records), on_line=boom)
        self.assertIsNone(err)
        self.assertEqual(text, "done text")

    def test_a_stderr_flood_does_not_deadlock(self):
        # Case 1, the one a small stub never reaches. 512KB is comfortably past
        # any pipe buffer; a single-pipe reader hangs here until the timeout.
        records = json.loads(RESULT_JSON)
        prof = self.stream_profile(records, env={"STUB_STDERR_BYTES": "524288"})
        t0 = time.time()
        text, _, _, err, _ = self.run_exec(profile=prof, timeout=20)
        self.assertLess(time.time() - t0, 15, "deadlocked on the undrained pipe")
        self.assertIsNone(err)
        self.assertEqual(text, "done text")

    def test_every_parser_reads_both_formats(self):
        # The divergence this replaced: parse_qwen_json had a JSONL path and
        # peak_context/parse_stats did not, so a streamed run parsed to the
        # right answer with ZEROED telemetry -- no error, just a receipt
        # quietly reporting 0 context and 0 tokens. Pin all of them together.
        batched = RESULT_JSON
        streamed = "\n".join(json.dumps(r) for r in json.loads(RESULT_JSON))
        for name, blob in (("batched", batched), ("streamed", streamed)):
            self.assertEqual(invoke.peak_context(blob), 20285, name)
            st = invoke.parse_stats(blob)
            self.assertEqual(st["tools"], 5, name)
            self.assertEqual(st["tokens"]["prompt"], 29421, name)
            self.assertEqual(invoke.parse_qwen_json(blob)[0], "done text", name)
            self.assertIsNone(invoke.result_error(blob), name)

    def test_records_tolerates_junk_between_records(self):
        # A warning line or a partial write must not cost us the run.
        blob = "\n".join(["not json at all",
                          json.dumps({"type": "assistant", "n": 1}),
                          "",
                          json.dumps({"type": "result", "result": "ok"})])
        got = invoke.records(blob)
        self.assertEqual(len(got), 2)
        self.assertEqual(invoke.parse_qwen_json(blob)[0], "ok")

    def test_stall_watchdog_stops_a_silent_run(self):
        # A callback cannot see this: silence produces no lines, so nothing
        # on_line could ever fire. Hence a watchdog thread.
        prof = self.profile(env={"STUB_OUT": self.out, "STUB_SLEEP": "20",
                                 "STUB_STDOUT": RESULT_JSON})
        t0 = time.time()
        text, _, _, err, _ = self.run_exec(profile=prof, timeout=60,
                                           stall_after=1)
        self.assertLess(time.time() - t0, 15, "watchdog never fired")
        self.assertIsNone(text)
        self.assertIn("run stopped", err)
        self.assertIn("no output", err)

    def test_a_run_that_keeps_talking_is_not_stalled(self):
        # The failure mode that matters: killing a long run that is working.
        records = [{"type": "assistant", "n": i} for i in range(6)]
        records.append({"type": "result", "result": "done", "session_id": "s"})
        prof = self.stream_profile(records, env={"STUB_GAP": "0.2"})
        text, _, _, err, _ = self.run_exec(profile=prof, timeout=60,
                                           stall_after=2, on_line=lambda r: None)
        self.assertIsNone(err, "killed a run that was emitting steadily")
        self.assertEqual(text, "done")

    def test_no_stall_limit_means_no_watchdog(self):
        prof = self.profile(env={"STUB_OUT": self.out, "STUB_SLEEP": "1",
                                 "STUB_STDOUT": RESULT_JSON})
        _, _, _, err, _ = self.run_exec(profile=prof, timeout=30)
        self.assertIsNone(err)

    def test_the_silence_budget_covers_a_full_generation(self):
        # The load-bearing property, at BOTH ends of the hardware range: the
        # budget must exceed max_output_tokens / decode_rate, or the watchdog
        # kills a generation that is working. A 27B at ~70 tok/s needs ~1,830s
        # for 128k; a 120B at ~17 tok/s needs ~7,530s for the same output. A
        # single fixed number cannot serve both -- which is why the knob is the
        # rate. (A previous fixed 1800s default was already under the fast case.)
        saved, invoke.max_output_tokens = invoke.max_output_tokens, lambda: 128_000
        try:
            for tps, need in ((70, 128_000 / 70), (17, 128_000 / 17)):
                budget = invoke.stall_seconds(config={"decode_tps": tps})
                self.assertGreater(budget, need,
                                   f"at {tps} tok/s a full generation is "
                                   f"{need:.0f}s but the budget is {budget}s")
            # Unconfigured falls back to the slow floor, never the fast one.
            self.assertGreater(invoke.stall_seconds(config={}), 128_000 / 17)
        finally:
            invoke.max_output_tokens = saved

    def test_an_explicit_seconds_override_wins(self):
        self.assertEqual(invoke.stall_seconds(config={"stall_seconds": 600}), 600)
        self.assertEqual(
            invoke.stall_seconds(config={"stall_seconds": 600, "decode_tps": 70}),
            600)

    def test_a_nonsense_rate_falls_back_rather_than_dividing_by_zero(self):
        for bad in (0, -5, "fast", None):
            self.assertGreaterEqual(
                invoke.stall_seconds(config={"decode_tps": bad}), 30)

    def test_timeout_still_reports_as_a_timeout(self):
        prof = self.profile(env={"STUB_OUT": self.out, "STUB_SLEEP": "5",
                                 "STUB_STDOUT": RESULT_JSON})
        text, _, _, err, _ = self.run_exec(profile=prof, timeout=1)
        self.assertIsNone(text)
        self.assertIn("timed out after 1s", err)


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

    def test_accum_preserves_usage_provenance(self):
        # Live vLLM (2026-07-31): every delegation streams, so tokens arrive
        # via the result's top-level usage. The ladder dropped that label to
        # "none" -- a measured run indistinguishable from an unmeasured one.
        cum = invoke.cum_zero()
        invoke.accum_stats(cum, {"token_source": "usage",
                                 "tokens": {"prompt": 50, "completion": 5,
                                            "total": 55, "cached": 0,
                                            "thoughts": 0}})
        self.assertEqual(cum["token_source"], "usage")
        # Coarsest wins: a usage attempt poisons even a bySource run's split.
        invoke.accum_stats(cum, {"token_source": "bySource"})
        self.assertEqual(cum["token_source"], "usage")

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
