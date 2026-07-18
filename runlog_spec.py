#!/usr/bin/env python3
"""
Spec for the run log and token accounting.

Claude-authored gate (never delegate this file -- it defines what correct means).

The load-bearing cases, in order of what would hurt most if broken:

  1. The log must be INVISIBLE to git status. snapshot()/blast_radius() attribute working
     tree changes to Qwen; a visible log file would be reported as Qwen's work and would
     trip the "refuses to run when a spec is dirty" precondition.
  2. A logging failure must never fail a delegation.
  3. Tokens must be summed across ALL attempts, not just the last one.
  4. main vs overhead must stay split -- Qwen's memory-extractor sub-agent is a third of
     the spend on a trivial call and is not task work.

Run:  python3 runlog_spec.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402


# The exact shape a live `qwen -p ... -o json` run emits, captured from a real call.
# camelCase, and the output count is named `candidates` -- NOT the snake_case
# `completion` spelling that appears in the CLI bundle (that one is the statusLine hook).
REAL_STATS = {
    "type": "result",
    "duration_ms": 16088,
    "num_turns": 2,
    "stats": {
        "models": {
            "qwen3.6:27b-agent": {
                "api": {"totalRequests": 2, "totalErrors": 0, "totalLatencyMs": 16088},
                "tokens": {"prompt": 29421, "candidates": 124, "total": 29545,
                           "cached": 0, "thoughts": 0},
                "bySource": {
                    "main": {
                        "api": {"totalRequests": 1, "totalErrors": 0,
                                "totalLatencyMs": 10550},
                        "tokens": {"prompt": 18993, "candidates": 32, "total": 19025,
                                   "cached": 0, "thoughts": 0},
                    },
                    "managed-auto-memory-extractor": {
                        "api": {"totalRequests": 1, "totalErrors": 0,
                                "totalLatencyMs": 5538},
                        "tokens": {"prompt": 10428, "candidates": 92, "total": 10520,
                                   "cached": 0, "thoughts": 0},
                    },
                },
            }
        },
        "tools": {"totalCalls": 3, "totalFail": 1, "byName": {"read_file": {}}},
        "files": {"totalLinesAdded": 10, "totalLinesRemoved": 2},
    },
}


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args),
                          capture_output=True, text=True)


_REAL_REGISTRY = None
_SPEC_REGISTRY_DIR = None


def setUpModule():
    """
    Redirect the project registry for the WHOLE module, not just the registry tests.

    Learned the hard way: any test calling write_runlog also calls register_project, so a
    per-class patch left every other test appending temp paths to the user's real
    registry -- ~50 dead /tmp entries per run, which an aggregator would then try to
    walk. Patch once, globally.
    """
    global _REAL_REGISTRY, _SPEC_REGISTRY_DIR
    _REAL_REGISTRY = server.PROJECT_REGISTRY
    _SPEC_REGISTRY_DIR = tempfile.mkdtemp(prefix="qd-registry-")
    server.PROJECT_REGISTRY = os.path.join(_SPEC_REGISTRY_DIR, "projects.jsonl")


def tearDownModule():
    server.PROJECT_REGISTRY = _REAL_REGISTRY


class TokenParsing(unittest.TestCase):
    def test_candidates_maps_to_completion(self):
        t = server.norm_tokens({"prompt": 10, "candidates": 5, "total": 15})
        self.assertEqual(t["completion"], 5)
        self.assertEqual(t["prompt"], 10)

    def test_completion_spelling_also_accepted(self):
        """Survives an upstream rename to the statusLine spelling."""
        t = server.norm_tokens({"prompt": 10, "completion": 7, "total": 17})
        self.assertEqual(t["completion"], 7)

    def test_missing_fields_are_zero_not_crash(self):
        self.assertEqual(server.norm_tokens(None)["total"], 0)
        self.assertEqual(server.norm_tokens({})["prompt"], 0)

    def test_parse_stats_extracts_real_token_totals(self):
        st = server.parse_stats(json.dumps([REAL_STATS]))
        self.assertEqual(st["tokens"]["prompt"], 29421)
        self.assertEqual(st["tokens"]["completion"], 124)
        self.assertEqual(st["tokens"]["total"], 29545)

    def test_main_and_overhead_are_split(self):
        """The memory-extractor is real spend but is not task work. Blending them
        overstates what the task cost by ~35% on a trivial call."""
        st = server.parse_stats(json.dumps([REAL_STATS]))
        self.assertEqual(st["tokens_main"]["prompt"], 18993)
        self.assertEqual(st["tokens_overhead"]["prompt"], 10428)
        self.assertEqual(
            st["tokens_main"]["prompt"] + st["tokens_overhead"]["prompt"],
            st["tokens"]["prompt"],
        )

    def test_absent_bysource_attributes_all_to_main(self):
        """Overstate main rather than silently drop tokens."""
        blob = json.loads(json.dumps(REAL_STATS))
        del blob["stats"]["models"]["qwen3.6:27b-agent"]["bySource"]
        st = server.parse_stats(json.dumps([blob]))
        self.assertEqual(st["tokens_main"]["prompt"], 29421)
        self.assertEqual(st["tokens_overhead"]["prompt"], 0)

    def test_blended_fallback_is_labelled_not_silent(self):
        """overhead==0 must be distinguishable from overhead-unmeasured. A real
        delegate run reported zero overhead; without this label there is no way to tell
        whether the extractor was idle or the breakdown was simply missing."""
        blob = json.loads(json.dumps(REAL_STATS))
        del blob["stats"]["models"]["qwen3.6:27b-agent"]["bySource"]
        self.assertEqual(server.parse_stats(json.dumps([blob]))["token_source"],
                         "blended")

    def test_real_bysource_is_labelled_bysource(self):
        self.assertEqual(server.parse_stats(json.dumps([REAL_STATS]))["token_source"],
                         "bySource")

    def test_no_stats_at_all_is_labelled_none(self):
        self.assertEqual(server.parse_stats("garbage")["token_source"], "none")

    def test_existing_fields_still_parsed(self):
        """Token capture must not regress what parse_stats already reported."""
        st = server.parse_stats(json.dumps([REAL_STATS]))
        self.assertEqual(st["tools"], 3)
        self.assertEqual(st["tool_fail"], 1)
        self.assertEqual(st["ms"], 16088)
        self.assertEqual(st["lines_added"], 10)

    def test_garbage_input_returns_zeros(self):
        st = server.parse_stats("not json at all")
        self.assertEqual(st["tokens"]["total"], 0)


class Accumulation(unittest.TestCase):
    def test_tokens_sum_across_attempts(self):
        """A 3-attempt run costs ~3x a 1-attempt run. Reading only the last attempt
        under-reports the iterate loop, which is exactly where tokens get spent."""
        cum = server.cum_zero()
        st = server.parse_stats(json.dumps([REAL_STATS]))
        for _ in range(3):
            server.accum_stats(cum, st)
        self.assertEqual(cum["tokens"]["total"], 29545 * 3)
        self.assertEqual(cum["tokens_main"]["prompt"], 18993 * 3)
        self.assertEqual(cum["ms"], 16088 * 3)
        self.assertEqual(cum["attempts"], 3)

    def test_tool_names_deduped_not_summed(self):
        cum = server.cum_zero()
        server.accum_stats(cum, {"tool_names": ["read_file", "edit"]})
        server.accum_stats(cum, {"tool_names": ["edit", "write"]})
        self.assertEqual(cum["tool_names"], ["edit", "read_file", "write"])

    def test_one_blended_attempt_taints_the_whole_run(self):
        """A run is only as trustworthy as its weakest attempt: if any attempt lacked
        the breakdown, the run's main/overhead split cannot claim to be real.

        Asserted in BOTH orders on purpose. With the blended attempt last, a naive
        'keep the latest value' implementation passes by coincidence -- mutation
        testing caught exactly that and this test was strengthened in response."""
        cum = server.cum_zero()
        server.accum_stats(cum, {"token_source": "bySource"})
        server.accum_stats(cum, {"token_source": "blended"})
        self.assertEqual(cum["token_source"], "blended", "blended-last not detected")

        cum = server.cum_zero()
        server.accum_stats(cum, {"token_source": "blended"})
        server.accum_stats(cum, {"token_source": "bySource"})
        self.assertEqual(cum["token_source"], "blended",
                         "a later clean attempt must not erase an earlier blended one")

    def test_all_bysource_attempts_stay_bysource(self):
        cum = server.cum_zero()
        for _ in range(3):
            server.accum_stats(cum, {"token_source": "bySource"})
        self.assertEqual(cum["token_source"], "bySource")

    def test_accum_survives_empty_stats(self):
        cum = server.cum_zero()
        server.accum_stats(cum, None)
        self.assertEqual(cum["tokens"]["total"], 0)
        self.assertEqual(cum["attempts"], 1)


class LogInvisibleToGit(unittest.TestCase):
    """The hazard test. If this fails, CHANGED reports are corrupt."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="qd-spec-")
        git(self.dir, "init", "-q")
        git(self.dir, "config", "user.email", "spec@test")
        git(self.dir, "config", "user.name", "spec")
        with open(os.path.join(self.dir, "seed.txt"), "w") as f:
            f.write("seed\n")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "baseline")

    def test_git_status_is_clean_after_logging(self):
        before = git(self.dir, "status", "--porcelain").stdout
        self.assertEqual(before.strip(), "", "fixture should start clean")

        server.write_runlog(self.dir, {"ts": "now", "tool": "qwen_delegate"})

        after = git(self.dir, "status", "--porcelain").stdout
        self.assertEqual(
            after.strip(), "",
            "the run log is visible to git status -- it will be misattributed to Qwen "
            "in CHANGED and will trip the dirty-tree precondition",
        )

    def test_snapshot_does_not_see_the_log(self):
        """The actual function used for blast radius, not just raw git."""
        pre = server.snapshot(self.dir)
        server.write_runlog(self.dir, {"ts": "now", "tool": "qwen_delegate"})
        post = server.snapshot(self.dir)
        changed = [p for p in post if post.get(p) != pre.get(p)]
        self.assertEqual(changed, [], f"log polluted the blast radius: {changed}")

    def test_log_is_actually_written(self):
        """Invisible to git, but really on disk -- the obvious way to fake the test
        above is to not write anything at all."""
        server.write_runlog(self.dir, {"ts": "now", "tool": "qwen_delegate"})
        path = os.path.join(self.dir, server.RUNLOG_DIR, server.RUNLOG_FILE)
        self.assertTrue(os.path.isfile(path), "no log file written")
        with open(path) as f:
            rec = json.loads(f.readline())
        self.assertEqual(rec["tool"], "qwen_delegate")

    def test_appends_rather_than_overwrites(self):
        for i in range(3):
            server.write_runlog(self.dir, {"n": i})
        path = os.path.join(self.dir, server.RUNLOG_DIR, server.RUNLOG_FILE)
        with open(path) as f:
            rows = [json.loads(x) for x in f if x.strip()]
        self.assertEqual([r["n"] for r in rows], [0, 1, 2])


class NeverBreaksADelegation(unittest.TestCase):
    def test_unwritable_target_does_not_raise(self):
        """Best-effort by contract. Losing a log line is acceptable; losing a
        completed delegation because logging failed is not."""
        server.write_runlog("/proc/nonexistent-nope", {"a": 1})  # must not raise

    def test_unserializable_record_does_not_raise(self):
        d = tempfile.mkdtemp(prefix="qd-spec-")
        server.write_runlog(d, {"bad": object()})  # must not raise


class LeverageRecord(unittest.TestCase):
    def test_leverage_is_free_tokens_over_returned_tokens(self):
        stats = server.parse_stats(json.dumps([REAL_STATS]))
        verdict = "x" * 4000  # 4000 chars / 4.0 == 1000 est tokens
        rec = server.leverage_record("qwen_delegate", "/tmp", "success",
                                     verdict, stats, 29421)
        self.assertEqual(rec["verdict_chars"], 4000)
        self.assertEqual(rec["verdict_tokens_est"], 1000)
        self.assertEqual(rec["leverage"], round(29545 / 1000, 1))

    def test_empty_verdict_gives_null_leverage_not_zero_division(self):
        rec = server.leverage_record("qwen_query", "/tmp", "ok", "", {}, 0)
        self.assertIsNone(rec["leverage"])

    def test_record_carries_token_provenance_through(self):
        """The record must report the provenance it was actually given, not a constant.
        Mutation testing caught a hardcoded 'bySource' surviving here."""
        blended = dict(server.cum_zero(), token_source="blended")
        rec = server.leverage_record("qwen_delegate", "/tmp", "success", "abcd",
                                     blended, 0)
        self.assertEqual(rec["token_source"], "blended")

        real = server.parse_stats(json.dumps([REAL_STATS]))
        rec = server.leverage_record("qwen_delegate", "/tmp", "success", "abcd", real, 0)
        self.assertEqual(rec["token_source"], "bySource")

    def test_extra_fields_merged(self):
        rec = server.leverage_record("qwen_delegate", "/tmp", "success", "abcd",
                                     {}, 0, extra={"attempts": 2})
        self.assertEqual(rec["attempts"], 2)
        self.assertEqual(rec["tool"], "qwen_delegate")

    def test_record_is_json_serialisable(self):
        stats = server.parse_stats(json.dumps([REAL_STATS]))
        rec = server.leverage_record("qwen_delegate", "/tmp", "success", "hi",
                                     stats, 1, extra={"task": server.digest("t")})
        json.dumps(rec)  # must not raise


class Digest(unittest.TestCase):
    def test_truncates_head_and_keeps_full_hash(self):
        long = "a" * 5000
        d = server.digest(long)
        self.assertEqual(len(d["head"]), server.TASK_HEAD_CHARS)
        self.assertEqual(d["chars"], 5000)
        self.assertEqual(len(d["sha256"]), 16)

    def test_identical_text_hashes_identically(self):
        self.assertEqual(server.digest("same")["sha256"],
                         server.digest("same")["sha256"])
        self.assertNotEqual(server.digest("a")["sha256"],
                            server.digest("b")["sha256"])

    def test_none_is_safe(self):
        self.assertEqual(server.digest(None)["chars"], 0)


class Registry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qd-reg-")
        self.orig = server.PROJECT_REGISTRY
        server.PROJECT_REGISTRY = os.path.join(self.tmp, "projects.jsonl")

    def tearDown(self):
        server.PROJECT_REGISTRY = self.orig

    def test_suite_never_touches_the_real_registry(self):
        """Regression guard. A previous version patched the registry per-class, so every
        other test appended dead temp paths to the user's real registry."""
        real = os.path.expanduser("~/.qwen-delegate/projects.jsonl")
        self.assertNotEqual(os.path.abspath(server.PROJECT_REGISTRY),
                            os.path.abspath(real),
                            "spec is writing to the real project registry")

    def test_registers_once_not_repeatedly(self):
        for _ in range(3):
            server.register_project("/some/project")
        with open(server.PROJECT_REGISTRY) as f:
            rows = [json.loads(x) for x in f if x.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], "/some/project")

    def test_distinct_projects_both_recorded(self):
        server.register_project("/p/one")
        server.register_project("/p/two")
        with open(server.PROJECT_REGISTRY) as f:
            rows = [json.loads(x) for x in f if x.strip()]
        self.assertEqual({r["path"] for r in rows}, {"/p/one", "/p/two"})

    def test_corrupt_line_does_not_hide_the_rest(self):
        with open(server.PROJECT_REGISTRY, "w") as f:
            f.write("{not json\n")
            f.write(json.dumps({"path": "/p/one"}) + "\n")
        server.register_project("/p/one")  # already known despite the corrupt line
        with open(server.PROJECT_REGISTRY) as f:
            paths = [json.loads(x)["path"] for x in f if x.strip().startswith("{\"")]
        self.assertEqual(paths.count("/p/one"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
