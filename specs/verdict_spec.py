#!/usr/bin/env python3
"""
Spec for qd/verdict.py -- receipt rendering (LLD "qd/verdict.py", HLD C2/C3).

Claude-authored gate (never delegate this file -- it defines what correct means).

R2 (PLAN-v3-l5) retired crane equality for receipt TEXT: clean green runs render
compact (trail/CONTEXT/TIME/TOOLS/CONTINUE/NEXT only on non-success or flags).
parse_handoff/strip_handoff remain crane-equal. The v2 seams, unchanged:

  1. C2 receipt lines (NOTES/WORKTREE/MERGE/GRAPH/REFS/COST) insert immediately
     BEFORE the "--- qwen result ---" block, in C2 order, each only when
     applicable. With no v2 ctx keys present, output is byte-identical to v1.
  2. N1 cap: the receipt never exceeds 3,000 chars; C2 lines are dropped WHOLE,
     reverse-priority (COST first, then REFS, then GRAPH, then NOTES);
     STATUS/CHANGED/ROLLBACK are never dropped.
  3. C5 log seam: the run-log record written by render carries executor and
     cost_usd from ctx (defaults "qwen-local"/0.0).
  4. Sha normalization: v2 ctx["pre_sha"] is the FULL sha (gittree contract);
     v1 used short. Equality tests feed each its own form and normalize.

Public surface pinned here:
    qd.verdict.render(status, session_id, trail, result_text, denials,
                      max_iter, ctx, last_verify=None) -> str
    qd.verdict.parse_handoff, qd.verdict.strip_handoff  (ports; crane-equal)

Run:  python3 specs/verdict_spec.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _ref_impl as server  # noqa: E402  (frozen v1 oracle, test-only)
from qd import verdict  # noqa: E402


RESULT_TEXT = """I did the work.

HANDOFF: module built and traced against the spec
FILES: qd/example.py
NEXT: nothing
"""


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["QWEN_DELEGATE_REGISTRY"] = os.path.join(
            tempfile.mkdtemp(), "projects.jsonl")
        self.cwd = tempfile.mkdtemp()
        sh(self.cwd, "git", "init", "-q")
        sh(self.cwd, "git", "config", "user.email", "s@t")
        sh(self.cwd, "git", "config", "user.name", "s")
        with open(os.path.join(self.cwd, "base.py"), "w") as f:
            f.write("x = 1\n")
        sh(self.cwd, "git", "add", "-A")
        sh(self.cwd, "git", "commit", "-qm", "base")
        self.full_sha = sh(self.cwd, "git", "rev-parse",
                           "HEAD").stdout.strip()
        self.short_sha = sh(self.cwd, "git", "rev-parse", "--short",
                            "HEAD").stdout.strip()
        # One dirty file so CHANGED has content.
        with open(os.path.join(self.cwd, "built.py"), "w") as f:
            f.write("def made():\n    pass\n")
        self.pre_status = {}  # snapshot taken BEFORE the dirty file appeared

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def ctx(self, sha, **over):
        c = {
            "cwd": self.cwd, "guard_on": True, "preflight": False,
            "pre_status": dict(self.pre_status), "pre_sha": sha,
            "pre_clean": True, "peak": 30000,
            "meta": {"stats": {}, "blocked": []},
            "timeout": 900, "approval_mode": "auto-edit",
            "task": "the task", "verify": "python3 -c pass",
            "cum": None, "sessions": [], "reinjects": 0, "discards": 0,
            "on_compaction": "reinject", "session_hint": None,
        }
        c.update(over)
        return c

    def both(self, status="success", trail=None, result=RESULT_TEXT,
             denials=None, last_verify=None, v1_over=None, v2_over=None):
        trail = trail or ["attempt 1: VERIFY PASS"]
        v1 = server.render(status, "s-1", list(trail), result,
                           list(denials or []), 3,
                           self.ctx(self.short_sha, **(v1_over or {})),
                           last_verify)
        v2 = verdict.render(status, "s-1", list(trail), result,
                            list(denials or []), 3,
                            self.ctx(self.full_sha, **(v2_over or {})),
                            last_verify)
        return (v1.replace(self.short_sha, "SHA"),
                v2.replace(self.full_sha, "SHA").replace(self.short_sha, "SHA"))


class CompactGreen(Fixture):
    """R2 (PLAN-v3-l5): a clean success renders COMPACT -- diagnostics appear
    only when something needs the manager's judgment. Amends the C2 'v1 frozen'
    clause; crane equality for receipt text is retired (helpers below remain
    crane-equal)."""

    def test_green_keeps_the_essentials(self):
        _, v2 = self.both()
        for tag in ("STATUS: success", "SESSION:", "ATTEMPTS: 1/3",
                    "CHANGED", "HANDOFF:", "ROLLBACK:"):
            self.assertIn(tag, v2)

    def test_green_drops_the_boilerplate(self):
        _, v2 = self.both(v2_over={"meta": {"stats": {
            "ms": 60000, "tools": 9, "tool_names": ["edit"], "tool_fail": 2},
            "blocked": []}})
        for tag in ("  - attempt", "CONTEXT:", "TIME:", "TOOLS:",
                    "CONTINUE:", "NEXT:", "TOOL FAILURES"):
            self.assertNotIn(tag, v2)

    def test_green_public_surface_is_one_line(self):
        _, v2 = self.both()
        line = [l for l in v2.splitlines()
                if l.startswith("NEW PUBLIC SURFACE")][0]
        self.assertEqual(line, "NEW PUBLIC SURFACE: made (built.py)")
        self.assertNotIn("names others can depend on", v2)

    def test_green_receipt_is_small(self):
        _, v2 = self.both()
        self.assertLess(len(v2), 900)

    def test_misreport_still_fires_on_green(self):
        _, v2 = self.both(result="done\n\nHANDOFF: ok\nFILES: none\nNEXT: nothing\n")
        self.assertIn("MISREPORT", v2)

    def test_near_compaction_context_shown_even_on_green(self):
        _, v2 = self.both(v2_over={"peak": 150000})
        self.assertIn("APPROACHING COMPACTION", v2)

    def test_stale_verify_output_dropped_on_green(self):
        # Success-after-retry: last_verify holds attempt 1's FAILURE output.
        # Found live (trust=self slugify run): a green receipt carried 1.3k
        # chars of stale red output and read as a failure.
        _, v2 = self.both(trail=["attempt 1: verify failed",
                                 "attempt 2: VERIFY PASS"],
                          last_verify="AssertionError: stale attempt-1 noise")
        self.assertEqual(v2.splitlines()[0], "STATUS: success")
        self.assertNotIn("final verify output", v2)
        self.assertNotIn("stale attempt-1 noise", v2)


class VerboseRed(Fixture):
    """Non-success keeps the full diagnostics."""

    def test_red_keeps_trail_context_and_verify_output(self):
        _, v2 = self.both(status="verify_failed",
                          trail=["attempt 1: verify failed",
                                 "attempt 2: verify failed"],
                          last_verify="AssertionError: expected 3 got 2")
        for tag in ("  - attempt 1: verify failed", "CONTEXT:",
                    "--- final verify output ---", "AssertionError"):
            self.assertIn(tag, v2)

    def test_gate_suspect_explains(self):
        _, v2 = self.both(status="gate_suspect")
        self.assertIn("GATE SUSPECT", v2)
        self.assertIn("Fix the gate before retrying", v2)

    def test_preflight_pass_is_not_clean(self):
        _, v2 = self.both(v2_over={"preflight": True})
        self.assertIn("PREFLIGHT: the verify command ALREADY PASSED", v2)
        self.assertIn("  - attempt", v2)

    def test_dirty_tree_rollback_warns(self):
        _, v2 = self.both(v2_over={"pre_clean": False})
        self.assertIn("unsafe to blanket-revert", v2)

    def test_blocked_shell_compact_but_present_on_green(self):
        over = {"meta": {"stats": {}, "blocked": ["rm -rf x (destructive)"]}}
        _, v2 = self.both(denials=[{"tool_name": "run_shell_command"}],
                          v2_over=over)
        self.assertIn("SHELL APPROVAL NEEDED", v2)
        self.assertIn("rm -rf x (destructive)", v2)
        self.assertIn("DENIALS: 1 blocked", v2)
        self.assertNotIn("APPROVE a command", v2)


class Helpers(Fixture):
    def test_handoff_helpers_crane_equal(self):
        for text in (RESULT_TEXT, "no handoff here", "",
                     "x\nHANDOFF: a\nFILES: f.py\nNEXT: b\n"):
            self.assertEqual(server.parse_handoff(text),
                             verdict.parse_handoff(text))
            self.assertEqual(server.strip_handoff(text),
                             verdict.strip_handoff(text))


class C2Lines(Fixture):
    def v2(self, **ctx_over):
        return verdict.render("success", "s-1", ["attempt 1: VERIFY PASS"],
                              RESULT_TEXT, [], 3,
                              self.ctx(self.full_sha, **ctx_over), None)

    def test_no_v2_keys_no_new_lines(self):
        out = self.v2()
        for tag in ("NOTES:", "WORKTREE:", "MERGE:", "GRAPH:", "REFS:",
                    "COST:"):
            self.assertNotIn("\n" + tag, out)

    def test_c2_lines_in_order_before_result_block(self):
        out = self.v2(notes="self-tests failing",
                      worktree={"path": "/w/r1", "branch": "qwen/r1"},
                      merge="clean",
                      graph_line="GRAPH: stale (3 files since abc1234) -- refresh running",
                      refs_added=["api.md"],
                      cost_usd=0.0123, executor="paid")
        result_at = out.index("--- qwen result ---")
        order = []
        for tag in ("NOTES: self-tests failing",
                    "WORKTREE: /w/r1",
                    "MERGE: git merge --no-edit qwen/r1",
                    "GRAPH: stale (3 files",
                    "REFS: 1 saved (api.md)",
                    "COST: $0.0123 (paid)"):
            at = out.index(tag)
            self.assertLess(at, result_at)
            order.append(at)
        self.assertEqual(order, sorted(order))

    def test_merge_conflict_variant(self):
        out = self.v2(worktree={"path": "/w/r2", "branch": "qwen/r2"},
                      merge="conflict")
        self.assertIn("MERGE: CONFLICT", out)
        self.assertIn("escalate", out)
        self.assertNotIn("git merge --no-edit", out)

    def test_cost_zero_not_emitted(self):
        out = self.v2(cost_usd=0.0, executor="qwen-local")
        self.assertNotIn("COST:", out)

    def test_notes_capped_200(self):
        out = self.v2(notes="N" * 500)
        line = [l for l in out.splitlines() if l.startswith("NOTES:")][0]
        self.assertLessEqual(len(line), 210)

    def test_total_cap_3000_drops_reverse_priority(self):
        out = self.v2(notes="n" * 180,
                      graph_line="GRAPH: stale (999 files since abc1234) -- " + "g" * 400,
                      refs_added=[f"ref{i}.md" for i in range(60)],
                      cost_usd=1.0, executor="paid")
        self.assertLessEqual(len(out), 3000)
        self.assertIn("STATUS:", out)
        self.assertIn("CHANGED:", out)
        # COST dropped before REFS, REFS before GRAPH, GRAPH before NOTES.
        if "COST:" in out:
            self.assertIn("REFS:", out)
        if "REFS:" in out:
            self.assertIn("GRAPH:", out)
        if "GRAPH:" in out:
            self.assertIn("NOTES:", out)


class LogSeam(Fixture):
    def read_log(self):
        with open(os.path.join(self.cwd, ".qwen-delegate",
                               "runs.jsonl")) as f:
            return [json.loads(l) for l in f.read().splitlines()]

    def test_record_carries_executor_and_cost(self):
        verdict.render("success", "s-1", ["a1"], RESULT_TEXT, [], 3,
                       self.ctx(self.full_sha, cost_usd=0.5,
                                executor="paid"), None)
        rec = self.read_log()[-1]
        self.assertEqual(rec["executor"], "paid")
        self.assertEqual(rec["cost_usd"], 0.5)

    def test_defaults_when_ctx_silent(self):
        verdict.render("success", "s-1", ["a1"], RESULT_TEXT, [], 3,
                       self.ctx(self.full_sha), None)
        rec = self.read_log()[-1]
        self.assertEqual(rec["executor"], "qwen-local")
        self.assertEqual(rec["cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
