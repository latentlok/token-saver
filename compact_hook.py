#!/usr/bin/env python3
"""
PreCompact/PostCompact hook: record that a session's history was summarised away.

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
    "~/.qwen-delegate/compacted"
)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    sid = payload.get("session_id")
    # PreCompact and PostCompact both fire for one compaction. Record PostCompact only:
    # it carries compact_summary (what survived), and counting both would double every
    # event and permanently arm re-injection.
    event = payload.get("hook_event_name")
    if sid and event == "PostCompact":
        try:
            os.makedirs(MARK_DIR, exist_ok=True)
            path = os.path.join(MARK_DIR, f"{sid}.json")
            try:
                with open(path) as f:
                    state = json.load(f)
            except Exception:
                state = {"session_id": sid, "events": [], "acked": 0}
            summary = payload.get("compact_summary")
            state["events"].append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "trigger": payload.get("trigger"),
                "transcript": payload.get("transcript_path"),
                # Length only. The summary is the conversation's content and does not
                # belong in a marker file.
                "summary_chars": len(summary) if isinstance(summary, str) else 0,
            })
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, path)  # atomic: a torn marker would be unreadable
        except Exception:
            pass

    print(json.dumps({}))


if __name__ == "__main__":
    main()
