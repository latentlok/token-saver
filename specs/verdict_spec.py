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
     reverse-priority (BURN first, then COST, then REFS, then GRAPH, then
     NOTES); when nothing droppable remains, the qwen-result tail shrinks to a
     200-char floor, then the verify tail to 400. STATUS/CHANGED/ROLLBACK are
     never dropped.
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
                      cost_usd=1.0, executor="paid",
                      cum={"tokens": {"prompt": 500000, "completion": 4000,
                                      "total": 504000},
                           "turns": 12})
        self.assertLessEqual(len(out), 3000)
        self.assertIn("STATUS:", out)
        self.assertIn("CHANGED:", out)
        # BURN dropped before COST, COST before REFS, then GRAPH, then NOTES.
        if "BURN:" in out:
            self.assertIn("COST:", out)
        if "COST:" in out:
            self.assertIn("REFS:", out)
        if "REFS:" in out:
            self.assertIn("GRAPH:", out)
        if "GRAPH:" in out:
            self.assertIn("NOTES:", out)


class BurnLine(Fixture):
    """U0.7: BURN had zero coverage while being the first block dropped."""

    def test_emitted_with_counts_and_calls(self):
        line = verdict.burn_line(
            {"cum": {"tokens": {"prompt": 1000, "completion": 50},
                     "turns": 4}})
        self.assertTrue(line.startswith("BURN: 1,000 in / 50 out"))
        self.assertIn("4 calls", line)

    def test_absent_at_zero(self):
        self.assertIsNone(verdict.burn_line({"cum": {"tokens": {}}}))
        self.assertIsNone(verdict.burn_line({"cum": None}))

    def test_heavy_threshold_binds_at_3m_input(self):
        heavy = verdict.burn_line(
            {"cum": {"tokens": {"prompt": 3_000_000, "completion": 1},
                     "turns": 1}})
        self.assertIn("HEAVY", heavy)
        light = verdict.burn_line(
            {"cum": {"tokens": {"prompt": 2_999_999, "completion": 1},
                     "turns": 1}})
        self.assertNotIn("HEAVY", light)


class CapEnforcement(Fixture):
    """U0.7: the 3,000 cap must hold even when body+tails alone exceed it --
    measured on this repo's own ledger, 4 of 18 receipts blew it."""

    def test_long_result_truncated_to_cap(self):
        out = verdict.render("success", "s-1", ["attempt 1: VERIFY PASS"],
                             "Z" * 6000, [], 3, self.ctx(self.full_sha), None)
        self.assertLessEqual(len(out), 3000)
        self.assertIn("STATUS: success", out)
        self.assertIn("CHANGED", out)
        self.assertIn("truncated", out)

    def test_verbose_red_with_long_everything_still_capped(self):
        out = verdict.render(
            "verify_failed", "s-1",
            [f"attempt {i}: verify failed" for i in (1, 2, 3)],
            "R" * 5000, [], 3,
            self.ctx(self.full_sha,
                     cum={"tokens": {"prompt": 4_000_000, "completion": 9,
                                     "total": 4_000_009},
                          "turns": 9},
                     notes="n" * 180),
            "V" * 5000)
        self.assertLessEqual(len(out), 3000)
        self.assertIn("--- final verify output ---", out)
        self.assertIn("--- qwen result ---", out)


class TreeFactsRendering(Fixture):
    """U0.4: the renderer reports engine-captured facts when present, and only
    re-reads the tree on a v1-shaped ctx (the pinned oracle fallback)."""

    def v2(self, **ctx_over):
        return verdict.render("success", "s-1", ["attempt 1: VERIFY PASS"],
                              RESULT_TEXT, [], 3,
                              self.ctx(self.full_sha, **ctx_over), None)

    def test_changed_prefers_engine_facts_over_disk(self):
        # Disk has built.py dirty, but the facts (the run's own tree, captured
        # pre-release) name a different file -- the receipt must follow facts.
        facts = {"post_status": {"wt_only.py": ("??", "aaaa")},
                 "changed": ["wt_only.py"], "numstat": {},
                 "head_moved": (False, 0, []), "head_now": "abc1234",
                 "pubs": {}}
        out = self.v2(tree_facts=facts)
        self.assertIn("wt_only.py", out)
        self.assertNotIn("built.py", out)

    def test_rollback_suppressed_for_worktree_runs(self):
        out = self.v2(worktree={"path": "/w/r1", "branch": "qwen/r1"},
                      merge="clean")
        self.assertNotIn("ROLLBACK:", out)

    def test_worktree_dirty_main_caveat(self):
        out = self.v2(worktree={"path": "/w/r1", "branch": "qwen/r1",
                                "dirty": True},
                      merge="clean")
        self.assertIn("NOT in this worktree", out)

    def test_unrestorable_paths_reported(self):
        out = self.v2(unrestorable=["huge.bin"])
        self.assertIn("huge.bin", out)
        self.assertIn("NOT auto-reverted", out)

    def test_scope_violation_paragraph(self):
        out = verdict.render(
            "scope_violation", "s-1",
            ["attempt 1: TOUCH SCOPE VIOLATION -- edited other.py outside "
             "scope (auto-reverted)"],
            RESULT_TEXT, [], 3, self.ctx(self.full_sha), None)
        self.assertIn("SCOPE:", out)
        self.assertIn("touch_scope", out)


class ReceiptDiet(Fixture):
    """Phase 2 (field-report wishes 03/08/10): grouped denials, the RUN
    telemetry line, BURN's time/cache framing, RESUME and LEDGER affordances,
    and graph-usage accountability."""

    def v2(self, status="success", trail=None, denials=None, **ctx_over):
        return verdict.render(status, "s-1",
                              trail or ["attempt 1: VERIFY PASS"],
                              RESULT_TEXT, denials or [], 3,
                              self.ctx(self.full_sha, **ctx_over), None)

    def read_log(self):
        with open(os.path.join(self.cwd, ".qwen-delegate",
                               "runs.jsonl")) as f:
            return [json.loads(line) for line in f.read().splitlines()]

    def test_denials_grouped_by_reason(self):
        blocked = (
            [f"run_shell_command: ls -la dir{i}  (not on the shell allowlist)"
             for i in range(11)]
            + [f"run_shell_command: curl x{i}  (state-changing or network "
               f"command)" for i in range(3)])
        out = self.v2(status="verify_failed",
                      trail=["attempt 1: verify failed"],
                      meta={"stats": {}, "blocked": blocked})
        self.assertIn("SHELL APPROVAL NEEDED: 14 blocked in 2 group(s)", out)
        self.assertIn("11x (not on the shell allowlist)", out)
        self.assertNotIn("dir7", out)          # near-duplicates collapsed

    def test_full_deny_list_lands_in_run_log(self):
        blocked = ["run_shell_command: a  (r1)", "run_shell_command: b  (r1)"]
        self.v2(status="verify_failed", trail=["attempt 1: verify failed"],
                meta={"stats": {}, "blocked": blocked})
        self.assertEqual(self.read_log()[-1]["blocked_commands"], blocked)

    def test_mcp_denials_route_to_mcp_allow_not_shell_allow(self):
        # The approval knob differs by denial kind; the first live MCP denial
        # (C1, 2026-08-01) rendered under SHELL APPROVAL NEEDED and pointed the
        # manager at shell_allow, which does nothing for an MCP tool.
        blocked = [
            "mcp__firecrawl__firecrawl_scrape: ?  "
            "(MCP tool not on the mcp allowlist)",
            "run_shell_command: make  (not on the shell allowlist)",
        ]
        out = self.v2(status="verify_failed",
                      trail=["attempt 1: verify failed"],
                      meta={"stats": {}, "blocked": blocked})
        self.assertIn("MCP APPROVAL NEEDED: 1 call(s) to 1 tool(s)", out)
        self.assertIn("mcp_allow", out)
        self.assertIn("  - mcp__firecrawl__firecrawl_scrape", out)
        self.assertIn("SHELL APPROVAL NEEDED: 1 blocked", out)
        # The MCP line must not sit inside the shell block's count.
        self.assertNotIn("2 blocked", out)

    def test_run_line_on_green_and_red(self):
        self.assertIn("RUN: 1 attempt(s)", self.v2())
        out = self.v2(status="verify_failed",
                      trail=["attempt 1: verify failed",
                             "attempt 2: verify failed"])
        self.assertIn("RUN: 2 attempt(s)", out)

    def test_resume_line_on_success_absent_on_stopped(self):
        self.assertIn("RESUME: session_id=s-1", self.v2())
        out = self.v2(status="stopped",
                      trail=["attempt 1: run stopped: burn"])
        self.assertNotIn("RESUME:", out)

    def test_ledger_line_counts_history(self):
        for st in ("success", "verify_failed", "stopped"):
            verdict.render(st, "s-1", ["a1"], RESULT_TEXT, [], 3,
                           self.ctx(self.full_sha), None)
        out = self.v2()
        self.assertIn("LEDGER: run #4 · lifetime 1 ok / 1 red / 1 stopped",
                      out)

    def test_no_history_no_ledger(self):
        self.assertNotIn("LEDGER:", self.v2())

    def test_graph_used_suffix_counts_worker_queries(self):
        out = self.v2(
            graph_line="GRAPH: fresh @ abc1234",
            meta={"stats": {}, "blocked": [],
                  "allowed": ['run_shell_command: graphify query "x"',
                              "run_shell_command: graphify explain y",
                              "run_shell_command: ls"]})
        self.assertIn("GRAPH: fresh @ abc1234 · used 2x this run", out)

    def test_burn_time_suffix_and_cache_note(self):
        line = verdict.burn_line(
            {"cum": {"tokens": {"prompt": 3_500_000, "completion": 100,
                                "cached": 3_400_000},
                     "turns": 10, "ms": 300000}})
        self.assertIn("min GPU", line)
        self.assertNotIn("HEAVY", line)        # fresh input is under 3M
        self.assertIn("cached", line)

    def test_burn_heavy_binds_on_uncached_remainder(self):
        line = verdict.burn_line(
            {"cum": {"tokens": {"prompt": 6_500_000, "completion": 100,
                                "cached": 3_000_000},
                     "turns": 10}})
        self.assertIn("HEAVY", line)           # 3.5M fresh


class ResumeHeuristic(Fixture):
    """U5.4: RESUME is three-way, because the affordance pointed the wrong way
    on every red receipt. A session that failed carries its confusion forward,
    and a warm follow-up into it argues with the correction instead of
    applying it -- except when the worker was BLOCKED, which is a fence rather
    than confusion, and whose approval loop only works in the same session."""

    def v2(self, status, trail=None, **ctx_over):
        return verdict.render(status, "s-7",
                              trail or ["attempt 1: verify failed",
                                        "attempt 2: verify failed"],
                              RESULT_TEXT, [], 3,
                              self.ctx(self.full_sha, **ctx_over), None)

    def test_healthy_statuses_keep_the_warm_line(self):
        for status, trail in (("success", ["attempt 1: VERIFY PASS"]),
                              ("success_but_preflight_passed",
                               ["attempt 1: VERIFY PASS"]),
                              ("unverified",
                               ["attempt 1: no verify supplied"]),
                              ("reported", ["attempt 1: verify failed"])):
            out = self.v2(status, trail)
            self.assertIn("RESUME: session_id=s-7", out, status)
            self.assertNotIn("not recommended", out)

    def test_a_red_run_is_told_to_start_cold(self):
        out = self.v2("verify_failed")
        self.assertIn("RESUME: not recommended — 2 failed attempt(s) in this "
                      "session carry their confusion forward; re-delegate "
                      "COLD with a corrected brief (or retry_of=s-7).", out)
        self.assertNotIn("session_id=s-7 --", out)

    def test_every_red_status_gets_the_cold_advice(self):
        for status in ("verify_failed", "scope_violation", "spec_violation",
                       "gate_suspect", "fixture_unproven", "result_invalid"):
            self.assertIn("not recommended", self.v2(status), status)

    def test_a_blocked_worker_keeps_the_warm_line(self):
        # The approval loop IS the warm session: shell_allow + the same
        # session_id is how a caller answers SHELL APPROVAL NEEDED.
        out = self.v2("verify_failed",
                      meta={"stats": {},
                            "blocked": ["run_shell_command: pytest  (not on "
                                        "the shell allowlist)"]})
        self.assertIn("RESUME: session_id=s-7", out)
        self.assertNotIn("not recommended", out)

    def test_the_suppressed_statuses_are_still_suppressed(self):
        for status, trail in (("stopped", ["attempt 1: run stopped: burn"]),
                              ("compaction_refused",
                               ["attempt 1: COMPACTION fired"])):
            self.assertNotIn("RESUME:", self.v2(status, trail), status)

    def test_it_stays_droppable_at_the_same_priority(self):
        # Priority 7 = first out under the cap, both ways: an affordance is
        # the cheapest thing to lose when the receipt has to shrink.
        long_result = "y" * 6000
        out = verdict.render("verify_failed", "s-7",
                             ["attempt 1: verify failed"], long_result, [], 3,
                             self.ctx(self.full_sha), "verify output")
        self.assertNotIn("RESUME:", out)
        self.assertLessEqual(len(out), 3000)


class HeadMovedAttribution(Fixture):
    """U1.3/C2: the receipt accuses only under positive worker attribution.

    The old text blamed the worker for EVERY moved HEAD -- roughly eight false
    accusations per project in the field, each one an investigation that found a
    caller's own commit. Worse, it printed `git reset --hard` over commits the
    worker never made."""

    MOVED = {"post_status": {"built.py": ("??", "aaa")}, "changed": ["built.py"],
             "numstat": {}, "head_moved": (True, 2, ["a.py", "b.py"]),
             "head_now": "def5678", "pubs": {}}

    def v2(self, **ctx_over):
        return verdict.render("success", "s-1", ["attempt 1: VERIFY PASS"],
                              RESULT_TEXT, [], 3,
                              self.ctx(self.full_sha, tree_facts=self.MOVED,
                                       **ctx_over), None)

    def test_v1_ctx_keeps_the_accusation_and_the_reset(self):
        # The key is absent on a v1-shaped ctx; the oracle depends on that text.
        out = self.v2()
        self.assertIn("COMMITTED: Qwen moved HEAD during this run", out)
        self.assertIn("git reset --hard", out)

    def test_worker_attribution_keeps_it_too(self):
        out = self.v2(head_moved_attribution="worker")
        self.assertIn("COMMITTED: Qwen moved HEAD during this run", out)
        self.assertIn("git reset --hard", out)

    def test_scoped_run_names_the_caller_and_drops_the_reset(self):
        out = self.v2(head_moved_attribution="caller")
        self.assertIn("HEAD MOVED: 2 commit(s)", out)
        self.assertIn("not the worker", out)
        self.assertNotIn("COMMITTED:", out)
        self.assertNotIn("reset --hard", out)
        self.assertIn("git log --oneline", out)
        self.assertIn("a.py, b.py", out)          # still says what moved

    def test_unknown_attribution_is_neutral_and_still_never_advises_reset(self):
        out = self.v2(head_moved_attribution="unknown")
        self.assertIn("HEAD MOVED: 2 commit(s)", out)
        self.assertIn("attribution unknown", out)
        self.assertNotIn("COMMITTED:", out)
        self.assertNotIn("reset --hard", out)
        self.assertIn("git log --oneline", out)

    def test_a_still_head_renders_nothing_either_way(self):
        for who in (None, "caller", "unknown", "worker"):
            over = {} if who is None else {"head_moved_attribution": who}
            out = verdict.render("success", "s-1", ["attempt 1: VERIFY PASS"],
                                 RESULT_TEXT, [], 3,
                                 self.ctx(self.full_sha, **over), None)
            self.assertNotIn("HEAD MOVED", out, who)
            self.assertNotIn("COMMITTED:", out, who)


class CoWorkLines(Fixture):
    """U1.2/C10 rendering: changes nobody can attribute to the worker are
    reported and explicitly NOT reverted -- the caller has to know its own edits
    survived, and that a spec that moved under the gate voids its guarantee."""

    def v2(self, **ctx_over):
        return verdict.render("success", "s-1", ["attempt 1: VERIFY PASS"],
                              RESULT_TEXT, [], 3,
                              self.ctx(self.full_sha, **ctx_over), None)

    def read_log(self):
        with open(os.path.join(self.cwd, ".qwen-delegate",
                               "runs.jsonl")) as f:
            return [json.loads(line) for line in f.read().splitlines()]

    def test_scope_line_names_the_files_and_the_policy(self):
        out = self.v2(scope_unattributed=["notes.md", "other.py"])
        self.assertIn("SCOPE: changed during the run but NOT by a logged "
                      "worker write (caller co-work?): notes.md, other.py", out)
        self.assertIn("never reverted", out)

    def test_unattributed_spec_change_warns_about_gate_integrity(self):
        out = self.v2(spec_unattributed=["calc_spec.py"])
        self.assertIn("SPEC CHANGED (unattributed): calc_spec.py", out)
        self.assertIn("gate integrity not guaranteed", out)

    def test_absent_keys_render_nothing(self):
        out = self.v2()
        self.assertNotIn("SCOPE:", out)
        self.assertNotIn("SPEC CHANGED", out)

    def test_runlog_counts_worker_writes_and_caller_changes(self):
        self.v2(writes=["a.py", "b.py"], scope_unattributed=["notes.md"])
        rec = self.read_log()[-1]
        self.assertEqual(rec["writes_attributed"], 2)
        self.assertEqual(rec["caller_changed"], 1)

    def test_counts_are_zero_not_missing_without_a_channel(self):
        self.v2()
        rec = self.read_log()[-1]
        self.assertEqual(rec["writes_attributed"], 0)
        self.assertEqual(rec["caller_changed"], 0)


class GateHygieneLines(Fixture):
    """U3.1/U3.2 rendering: a gate that eats its own budget is worth one line
    even on green, and a preflight the caller DECLARED green gets a fact instead
    of the alarm paragraph -- that paragraph fired on every revision task until
    it meant nothing."""

    def v2(self, status="success", **ctx_over):
        return verdict.render(status, "s-1", ["attempt 1: VERIFY PASS"],
                              RESULT_TEXT, [], 3,
                              self.ctx(self.full_sha, **ctx_over), None)

    def read_log(self):
        with open(os.path.join(self.cwd, ".qwen-delegate",
                               "runs.jsonl")) as f:
            return [json.loads(line) for line in f.read().splitlines()]

    def test_gate_slow_names_the_cost_and_the_knob(self):
        out = self.v2(gate_slow=True, gate_ms=210_000, verify_timeout_sec=300)
        self.assertIn("GATE SLOW: preflight took 210s of a 300s verify budget",
                      out)
        self.assertIn("every retry pays it", out)
        self.assertIn("verify_timeout_sec", out)

    def test_gate_slow_is_silent_without_the_flag(self):
        self.assertNotIn("GATE SLOW", self.v2())

    def test_a_declared_green_preflight_is_one_line_not_a_paragraph(self):
        out = self.v2(preflight=True, preflight_expect="green")
        self.assertEqual(out.splitlines()[0], "STATUS: success")
        self.assertIn("PREFLIGHT: green pre-run, declared expected", out)
        self.assertNotIn("ALREADY PASSED", out)

    def test_an_undeclared_green_preflight_still_demotes_and_warns(self):
        # The back-compat path: a ctx that never went through the engine (the
        # v1 oracle's shape) must keep the render-time demotion.
        out = self.v2(preflight=True)
        self.assertIn("STATUS: success_but_preflight_passed", out)
        self.assertIn("ALREADY PASSED", out)

    def test_an_engine_demoted_status_renders_the_same_warning(self):
        # The engine hands the demoted status down now; the renderer's own
        # demotion must be idempotent rather than a second, different opinion.
        out = self.v2(status="success_but_preflight_passed", preflight=True)
        self.assertIn("STATUS: success_but_preflight_passed", out)
        self.assertIn("ALREADY PASSED", out)

    def test_the_runlog_records_only_the_knobs_somebody_turned(self):
        self.v2()
        rec = self.read_log()[-1]
        self.assertNotIn("verify_timeout_sec", rec)
        self.assertNotIn("preflight_expect", rec)
        self.v2(verify_timeout_sec=900, preflight_expect="green",
                preflight=True)
        rec = self.read_log()[-1]
        self.assertEqual(rec["verify_timeout_sec"], 900)
        self.assertEqual(rec["preflight_expect"], "green")


class AdvisoryRendering(Fixture):
    """U3.4/C2: red advisories glow, greens collapse to a count, and neither
    ever touches STATUS. Non-droppable while red -- an indicator the cap
    silently dropped reads as a green one, which is worse than no gate."""

    RED = [{"name": "layering", "ok": False, "ms": 40, "head": "api imports db"},
           {"name": "naming", "ok": True, "ms": 12, "head": ""}]

    def v2(self, result=RESULT_TEXT, **ctx_over):
        return verdict.render("success", "s-1", ["attempt 1: VERIFY PASS"],
                              result, [], 3,
                              self.ctx(self.full_sha, **ctx_over), None)

    def read_log(self):
        with open(os.path.join(self.cwd, ".qwen-delegate",
                               "runs.jsonl")) as f:
            return [json.loads(line) for line in f.read().splitlines()]

    def test_red_is_named_with_its_first_output_line(self):
        out = self.v2(advisory=self.RED)
        self.assertIn("ADVISORY red: layering — api imports db", out)
        self.assertIn("ADVISORY: 1/2 green", out)
        self.assertEqual(out.splitlines()[0], "STATUS: success")

    def test_all_green_collapses_to_one_line(self):
        out = self.v2(advisory=[{"name": "a", "ok": True, "ms": 1, "head": ""},
                                {"name": "b", "ok": True, "ms": 1, "head": ""}])
        self.assertIn("ADVISORY: 2/2 green", out)
        self.assertNotIn("ADVISORY red", out)

    def test_the_block_leads_the_c2_region(self):
        out = self.v2(advisory=self.RED, notes="self-tests failing",
                      graph_line="GRAPH: fresh @ abc1234")
        self.assertLess(out.index("ADVISORY"), out.index("NOTES:"))
        self.assertLess(out.index("ADVISORY"), out.index("GRAPH:"))
        self.assertLess(out.index("ADVISORY"), out.index("--- qwen result ---"))

    def test_malformed_gates_are_counted_out_loud(self):
        out = self.v2(advisory=[{"name": "a", "ok": True, "ms": 1, "head": ""}],
                      advisory_skipped=2)
        self.assertIn("ADVISORY: 1/1 green, 2 skipped (malformed)", out)

    def test_a_red_block_survives_the_cap(self):
        out = self.v2(result="Z" * 6000, advisory=self.RED, notes="n" * 180,
                      graph_line="GRAPH: stale " + "g" * 300,
                      refs_added=[f"ref{i}.md" for i in range(60)],
                      cost_usd=1.0, executor="paid")
        self.assertLessEqual(len(out), 3000)
        self.assertIn("ADVISORY red: layering", out)

    def test_absent_renders_nothing_and_logs_nothing(self):
        out = self.v2()
        self.assertNotIn("ADVISORY", out)
        self.assertNotIn("advisory", self.read_log()[-1])

    def test_the_runlog_counts_red_of_total(self):
        self.v2(advisory=self.RED)
        self.assertEqual(self.read_log()[-1]["advisory"], {"red": 1, "of": 2})


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


class BriefLines(LogSeam):
    """U6: the receipt names the document version that briefed the run, the
    log records it (path + digest only -- digest() policy), and the LEDGER
    grows a per-document tally once the document has history."""

    BRIEF = {"path": "pb.md", "sha256": "ab" * 8, "amended": False,
             "chars": 400, "amendments": 0}

    def brief(self, **over):
        return dict(self.BRIEF, **over)

    def test_brief_line_names_path_digest_and_size(self):
        out = self.v2(brief=self.brief())
        self.assertIn(f"BRIEF: pb.md @ {'ab' * 8} · ~100 tokens", out)
        self.assertNotIn("(amended)", out)
        self.assertNotIn("consolidate", out)

    def test_amended_and_the_consolidate_nudge(self):
        out = self.v2(brief=self.brief(amended=True, amendments=6))
        self.assertIn("(amended)", out)
        self.assertIn("(6 amendments — consolidate)", out)
        # Five or fewer is a document being maintained, not sprawl.
        self.assertNotIn("consolidate",
                         self.v2(brief=self.brief(amendments=5)))

    def test_no_brief_no_line(self):
        self.assertNotIn("BRIEF:", self.v2())

    def test_the_brief_line_survives_on_a_green_receipt_under_the_diet(self):
        # Non-droppable body weight against the N1 cap (R2 pin re-measured):
        # the compact green stays small with the line present.
        out = self.v2(brief=self.brief())
        self.assertIn("STATUS: success", out)
        self.assertLess(len(out), 1000)

    def test_the_log_records_path_and_digest_only(self):
        self.v2(brief=self.brief(addendum="secret text", chars=99))
        rec = self.read_log()[-1]
        self.assertEqual(rec["brief"], {"path": "pb.md", "sha256": "ab" * 8})

    def test_ledger_gains_this_brief_only_with_prior_history(self):
        first = self.v2(brief=self.brief())
        self.assertNotIn("this brief:", first)     # no prior run used it
        second = self.v2(brief=self.brief())
        self.assertIn("this brief: 1 ok / 0 red", second)
        other = self.v2(brief=self.brief(path="other.md"))
        self.assertNotIn("this brief:", other)

    def v2(self, **ctx_over):
        return verdict.render("success", "s-1", ["attempt 1: VERIFY PASS"],
                              RESULT_TEXT, [], 3,
                              self.ctx(self.full_sha, **ctx_over), None)


if __name__ == "__main__":
    unittest.main(verbosity=1)
