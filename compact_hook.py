#!/usr/bin/env python3
"""
PreCompact/PostCompact hook: record -- and under the refuse policy, try to BLOCK --
a session's history being summarised away.

Compaction is lossy -- past it Qwen has claimed to have read files it never opened. A
resumed session that has been compacted may no longer hold the task it was given, so the
server must re-inject before continuing. This hook is how it finds out, deterministically:
an event fired by the CLI, not a token count inferred after the fact.

Qwen Code passes the event on stdin with `session_id` and `transcript_path`, so the marker
is keyed to the exact session a later call will resume. Verified firing on a real
compaction (both events, trigger="auto").

The marker must OUTLIVE this process -- it is read on a later call, so it goes to a
durable path, never the per-run tempdir the scoped hook uses.

Fail open: a hook that errors must not break the delegation it is observing. Worst case
we miss a marker and re-inject unnecessarily, which is wasteful but safe.
"""

import json
import os
import sys
import time

MARK_DIR = os.environ.get("QCOMPACT_DIR") or os.path.expanduser(
    "~/.delegation/compacted"
)

# "refuse" (the default policy) means: this run must not continue on a summarised
# history. Qwen's own hook reference documents PreCompact exit 2 as "block
# compaction", so we ask -- but the auto-compaction call site reads only the hook's
# additionalContext and swallows what else it returns, so the block is BEST EFFORT
# and must never be the only defence. The marker below is the defence that works:
# the server sees it and ends the run either way.
POLICY = os.environ.get("QCOMPACT_POLICY") or "reinject"


def _record(sid, key, entry):
    """Append *entry* to state[key] for this session. Atomic; never raises."""
    try:
        os.makedirs(MARK_DIR, exist_ok=True)
        path = os.path.join(MARK_DIR, f"{sid}.json")
        try:
            with open(path) as f:
                state = json.load(f)
        except Exception:
            state = {"session_id": sid, "events": [], "acked": 0}
        state.setdefault(key, []).append(entry)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)  # atomic: a torn marker would be unreadable
    except Exception:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    sid = payload.get("session_id")
    event = payload.get("hook_event_name")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # PreCompact goes to its own list. `events` keeps meaning "a summary actually
    # happened" -- the counter re-injection arms on -- so a blocked compaction can
    # never read as a completed one.
    if sid and event == "PreCompact":
        _record(sid, "pending", {"ts": now, "trigger": payload.get("trigger")})
        if POLICY == "refuse":
            sys.stderr.write(
                "supervised-delegation: compaction refused -- this task exceeds the context "
                "budget for one delegation. It must be split, not summarised.\n")
            sys.exit(2)          # documented as "block compaction"

    # PreCompact and PostCompact both fire for one compaction. Record PostCompact
    # here: it carries compact_summary (what survived), and counting both would
    # double every event and permanently arm re-injection.
    if sid and event == "PostCompact":
        summary = payload.get("compact_summary")
        _record(sid, "events", {
            "ts": now,
            "trigger": payload.get("trigger"),
            "transcript": payload.get("transcript_path"),
            # Length only. The summary is the conversation's content and does not
            # belong in a marker file.
            "summary_chars": len(summary) if isinstance(summary, str) else 0,
        })

    print(json.dumps({}))


if __name__ == "__main__":
    main()
