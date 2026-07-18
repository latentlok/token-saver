#!/usr/bin/env python3
"""
Spec for compaction detection and the resume branch.

Claude-authored gate (never delegate this file -- it defines what correct means).

The behaviour that matters, in order of what would hurt most if broken:

  1. Re-inject EXACTLY ONCE per compaction. Compacted once then resumed twice must
     re-inject on the first resume only -- a boolean marker would re-inject forever, and
     a consumed-and-deleted marker would lose the history.
  2. An intact session must get the DELTA ONLY. No task, no handoff suffix, no restated
     rules -- the session already holds them, and duplicating them grows the very context
     that triggered compaction.
  3. A later compaction must RE-ARM re-injection.
  4. Detection must never read token counts -- it is a file check on a hook-written event.

Run:  python3 compaction_spec.py
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

TASK = "Create widget.py with a public function spin(n)."
VERIFY = "./gate.sh"
V_OUT = "AssertionError: spin(2) == 4, want 6"


def write_marker(sid, events, acked=0):
    os.makedirs(server.COMPACT_DIR, exist_ok=True)
    with open(os.path.join(server.COMPACT_DIR, f"{sid}.json"), "w") as f:
        json.dump({"session_id": sid,
                   "events": [{"ts": f"t{i}", "trigger": "auto"} for i in range(events)],
                   "acked": acked}, f)


def read_marker(sid):
    with open(os.path.join(server.COMPACT_DIR, f"{sid}.json")) as f:
        return json.load(f)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qc-spec-")
        self.orig = server.COMPACT_DIR
        server.COMPACT_DIR = self.tmp

    def tearDown(self):
        server.COMPACT_DIR = self.orig


class Detection(Base):
    def test_no_marker_means_never_compacted(self):
        self.assertEqual(server.compaction_state("nope"), (0, 0))
        self.assertFalse(server.was_compacted_since_ack("nope"))

    def test_unacked_event_is_detected(self):
        write_marker("s1", events=1, acked=0)
        self.assertTrue(server.was_compacted_since_ack("s1"))

    def test_acked_event_is_not_redetected(self):
        write_marker("s1", events=1, acked=1)
        self.assertFalse(server.was_compacted_since_ack("s1"))

    def test_none_session_is_safe(self):
        self.assertFalse(server.was_compacted_since_ack(None))
        server.ack_compaction(None)  # must not raise

    def test_corrupt_marker_does_not_raise(self):
        os.makedirs(server.COMPACT_DIR, exist_ok=True)
        with open(os.path.join(server.COMPACT_DIR, "bad.json"), "w") as f:
            f.write("{not json")
        self.assertEqual(server.compaction_state("bad"), (0, 0))

    def test_detection_reads_no_token_counts(self):
        """Guard against regressing to inference. The signal is a hook-written file."""
        import inspect
        src = inspect.getsource(server.was_compacted_since_ack) + \
            inspect.getsource(server.compaction_state)
        for banned in ("peak", "token", "context_window", "compaction_thresholds"):
            self.assertNotIn(banned, src,
                             f"detection must not depend on {banned}")


class ReinjectExactlyOnce(Base):
    def test_compacted_then_two_resumes_reinjects_once(self):
        """The exact scenario: compacted, resumed twice, no second compaction."""
        write_marker("s1", events=1, acked=0)

        p1, a1 = server.retry_prompt("s1", TASK, VERIFY, V_OUT)
        self.assertEqual(a1, "reinject", "first resume after compaction must re-inject")
        self.assertIn(TASK, p1)

        p2, a2 = server.retry_prompt("s1", TASK, VERIFY, V_OUT)
        self.assertEqual(a2, "none", "second resume must NOT re-inject again")
        self.assertNotIn(TASK, p2, "task must not be duplicated into the session")

    def test_ack_is_persisted_not_just_in_memory(self):
        write_marker("s1", events=1, acked=0)
        server.retry_prompt("s1", TASK, VERIFY, V_OUT)
        self.assertEqual(read_marker("s1")["acked"], 1)

    def test_later_compaction_rearms(self):
        write_marker("s1", events=1, acked=0)
        server.retry_prompt("s1", TASK, VERIFY, V_OUT)          # acks 1
        _, a = server.retry_prompt("s1", TASK, VERIFY, V_OUT)
        self.assertEqual(a, "none")
        write_marker("s1", events=2, acked=1)                   # compacted again
        p, a = server.retry_prompt("s1", TASK, VERIFY, V_OUT)
        self.assertEqual(a, "reinject", "a NEW compaction must re-arm re-injection")
        self.assertIn(TASK, p)

    def test_events_are_not_deleted_by_acking(self):
        """History stays inspectable; acking is a watermark, not a consume."""
        write_marker("s1", events=2, acked=0)
        server.retry_prompt("s1", TASK, VERIFY, V_OUT)
        self.assertEqual(len(read_marker("s1")["events"]), 2)

    def test_multiple_unacked_events_ack_together(self):
        write_marker("s1", events=3, acked=0)
        server.retry_prompt("s1", TASK, VERIFY, V_OUT)
        self.assertEqual(read_marker("s1")["acked"], 3)
        _, a = server.retry_prompt("s1", TASK, VERIFY, V_OUT)
        self.assertEqual(a, "none")


class PolicyIsTheCallersChoice(Base):
    """The manager decides warm-vs-cold; the server must not decide for it."""

    def test_default_is_reinject(self):
        write_marker("s1", events=1, acked=0)
        _, action = server.retry_prompt("s1", TASK, VERIFY, V_OUT)
        self.assertEqual(action, "reinject")

    def test_discard_is_honoured(self):
        write_marker("s1", events=1, acked=0)
        p, action = server.retry_prompt("s1", TASK, VERIFY, V_OUT,
                                        on_compaction="discard")
        self.assertEqual(action, "discard")
        self.assertIn(TASK, p, "a cold session needs the whole task")

    def test_discard_tells_qwen_it_is_starting_fresh(self):
        """Otherwise it assumes continuity it does not have and trusts stale work."""
        write_marker("s1", events=1, acked=0)
        p, _ = server.retry_prompt("s1", TASK, VERIFY, V_OUT, on_compaction="discard")
        self.assertIn("discarded", p.lower())
        self.assertIn("partial", p.lower())

    def test_policy_is_irrelevant_when_not_compacted(self):
        for policy in ("reinject", "discard"):
            p, action = server.retry_prompt("s-clean", TASK, VERIFY, V_OUT,
                                            on_compaction=policy)
            self.assertEqual(action, "none", f"{policy} must not act on an intact session")
            self.assertNotIn(TASK, p)

    def test_both_policies_ack_so_neither_repeats(self):
        for policy in ("reinject", "discard"):
            write_marker(f"s-{policy}", events=1, acked=0)
            _, a1 = server.retry_prompt(f"s-{policy}", TASK, VERIFY, V_OUT,
                                        on_compaction=policy)
            _, a2 = server.retry_prompt(f"s-{policy}", TASK, VERIFY, V_OUT,
                                        on_compaction=policy)
            self.assertNotEqual(a1, "none")
            self.assertEqual(a2, "none", f"{policy} must not act twice for one compaction")

    def test_unknown_policy_falls_back_to_reinject(self):
        """A typo must not silently become 'discard' and throw away a session."""
        write_marker("s1", events=1, acked=0)
        _, action = server.retry_prompt("s1", TASK, VERIFY, V_OUT,
                                        on_compaction="nonsense")
        self.assertEqual(action, "reinject")


class DeltaOnly(Base):
    def test_intact_session_gets_only_the_failure(self):
        p, a = server.retry_prompt("s-intact", TASK, VERIFY, V_OUT)
        self.assertEqual(a, "none")
        self.assertIn(V_OUT, p, "the real error must still be fed back")
        self.assertNotIn(TASK, p, "task is already in the session")
        self.assertNotIn(VERIFY, p, "verify command is already in the session")
        self.assertNotIn("protected spec file", p, "that rule lives in QWEN.md")

    def test_delta_prompt_is_materially_smaller(self):
        write_marker("s2", events=1, acked=0)
        big, _ = server.retry_prompt("s2", TASK, VERIFY, V_OUT)   # re-injecting
        small, _ = server.retry_prompt("s2", TASK, VERIFY, V_OUT)  # now acked
        self.assertLess(len(small), len(big))

    def test_reinjected_prompt_warns_about_lost_history(self):
        write_marker("s3", events=1, acked=0)
        p, _ = server.retry_prompt("s3", TASK, VERIFY, V_OUT)
        self.assertIn("compact", p.lower())
        self.assertIn("do not reconstruct", p.lower())


class DiscardActuallyDiscards(Base):
    """
    retry_prompt only says what to do. What makes a discard real is run_qwen dropping the
    session id, so the next invocation runs without -r. Drive the real loop with a stubbed
    Qwen and watch what session id attempt 2 receives.
    """

    def drive(self, policy):
        seen = []
        real_invoke, real_verify, real_git, real_rules = (
            server.invoke_qwen, server.run_verify, server.is_git_repo,
            server.worker_rules_status)

        def fake_invoke(task, cwd, mode, timeout, session_id, **kw):
            seen.append(session_id)
            # attempt 1 establishes a session and then gets compacted
            if len(seen) == 1:
                write_marker("sess-A", events=1, acked=0)
            return "done", [], "sess-A", None, {"peak": 1, "stats": {}, "blocked": []}

        server.invoke_qwen = fake_invoke
        # Output must DIFFER between preflight and post-run, or gate_suspect correctly
        # bails before any retry happens and this test never exercises the branch.
        server.run_verify = lambda v, c: (len(seen) >= 2, f"boom {len(seen)}")
        server.is_git_repo = lambda c: False                       # skip git machinery
        # cwd is /tmp, which has no QWEN.md, and run_qwen now refuses an unconfigured
        # project before it ever reaches the retry loop this class tests. Satisfy the
        # precondition the same way the git one is satisfied -- setup_spec.py owns
        # proving the refusal itself actually fires.
        server.worker_rules_status = lambda c: ("ok", "/tmp/QWEN.md")
        try:
            server.run_qwen({"task": TASK, "cwd": "/tmp", "verify": VERIFY,
                             "approval_mode": "auto-edit", "max_iterations": 3,
                             "on_compaction": policy})
        finally:
            (server.invoke_qwen, server.run_verify, server.is_git_repo,
             server.worker_rules_status) = (
                real_invoke, real_verify, real_git, real_rules)
        return seen

    def test_discard_clears_the_session_id(self):
        seen = self.drive("discard")
        self.assertGreaterEqual(len(seen), 2, "should have retried")
        self.assertIsNone(seen[1],
                          "attempt 2 must start cold -- a retained id means -r is still "
                          "passed and the compacted history carries over")

    def test_reinject_keeps_the_session_id(self):
        seen = self.drive("reinject")
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(seen[1], "sess-A", "reinject must stay in the warm session")

    def test_discarded_session_still_counted_in_compactions(self):
        """
        Regression: the logged count was read off the FINAL session_id, which a discard
        has already replaced. A run could report discards=1 alongside compactions=0 --
        self-contradictory, and it hid the discarded session's compactions entirely.
        """
        rec = {}
        real_write = server.write_runlog
        server.write_runlog = lambda cwd, r: rec.update(r)
        try:
            self.drive("discard")
        finally:
            server.write_runlog = real_write
        self.assertGreaterEqual(rec.get("discards", 0), 1, "a discard should be recorded")
        self.assertGreaterEqual(
            rec.get("compactions", 0), 1,
            "the discarded session's compactions must still be counted -- "
            f"got compactions={rec.get('compactions')} with discards={rec.get('discards')}")


class HookContract(Base):
    """The hook is what makes detection real; its wiring must not silently drop out."""

    def test_compact_hooks_declared_for_both_events(self):
        h = server.compact_hooks()
        self.assertIn("PreCompact", h)
        self.assertIn("PostCompact", h)

    def test_scoped_mode_still_installs_compact_hooks(self):
        env, _, td = server.scoped_setup(self.tmp, "true", [])
        cfg = json.load(open(env["QWEN_CODE_SYSTEM_SETTINGS_PATH"]))
        self.assertIn("PreToolUse", cfg["hooks"])
        self.assertIn("PostCompact", cfg["hooks"],
                      "scoped mode must not lose compaction detection")
        self.assertEqual(env["QCOMPACT_DIR"], server.COMPACT_DIR)
        server._cleanup(td)

    def test_non_scoped_mode_installs_compact_hooks(self):
        env, td = server.compact_setup()
        cfg = json.load(open(env["QWEN_CODE_SYSTEM_SETTINGS_PATH"]))
        self.assertIn("PostCompact", cfg["hooks"])
        server._cleanup(td)

    def test_hook_records_postcompact_only(self):
        """Both events fire per compaction; counting both would double every event."""
        import subprocess
        payload = {"session_id": "hk", "hook_event_name": "PostCompact",
                   "trigger": "auto", "compact_summary": "x" * 50,
                   "transcript_path": "/tmp/t.jsonl"}
        env = dict(os.environ, QCOMPACT_DIR=self.tmp)
        for ev in ("PreCompact", "PostCompact"):
            payload["hook_event_name"] = ev
            subprocess.run([sys.executable, "compact_hook.py"], input=json.dumps(payload),
                           capture_output=True, text=True, env=env,
                           cwd=os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(len(read_marker("hk")["events"]), 1,
                         "one compaction must record exactly one event")
        self.assertEqual(read_marker("hk")["events"][0]["summary_chars"], 50)

    def test_hook_never_raises_on_garbage(self):
        import subprocess
        env = dict(os.environ, QCOMPACT_DIR=self.tmp)
        r = subprocess.run([sys.executable, "compact_hook.py"], input="not json",
                           capture_output=True, text=True, env=env,
                           cwd=os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(r.returncode, 0, "a failing hook must not break the delegation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
