#!/usr/bin/env python3
"""
Run log v2 — behavior frozen by specs/runlog_spec.py.

Ports the v1 logging surface from server.py and adds C5 obligations:
every record always carries executor (str) and cost_usd (float).
"""

import hashlib
import json
import os
import random
import threading
import time

RUNLOG_DIR = ".qwen-delegate"
RUNLOG_FILE = "runs.jsonl"
BRIEFS_DIR = "briefs"

_WRITE_LOCK = threading.Lock()


def new_run_id():
    """A run id in the C6 shape: "r" + 6 lowercase hex.

    Minted once per SUBMITTED delegation (U5.2) and carried by both halves of
    its run-log pair, so a reader can match the `running` record to the
    completion record that closes it. qd/worktrees.py mints its own id in the
    same shape for branch names: deliberately not shared, because that one
    names a container and this one names a run, and most runs have no
    container at all.
    """
    return "r" + "".join(random.choice("0123456789abcdef") for _ in range(6))


def registry_path():
    """Return the global project registry path, reading env at call time."""
    return os.environ.get("QWEN_DELEGATE_REGISTRY") or os.path.expanduser(
        "~/.qwen-delegate/projects.jsonl"
    )


def runlog_dir(cwd):
    """
    Create <cwd>/.qwen-delegate/ holding a self-ignoring .gitignore.

    The `*` pattern makes git ignore every file in the directory INCLUDING the .gitignore
    itself, so `git status --porcelain` never reports it. That is load-bearing: snapshot()
    and blast_radius() diff the working tree to attribute changes to Qwen, and an
    un-ignored log file would show up as Qwen's work -- and would also trip the
    "refuses to run if the tree is dirty" precondition. Self-ignoring leaves the
    project's own .gitignore untouched.
    """
    d = os.path.join(cwd, RUNLOG_DIR)
    os.makedirs(d, exist_ok=True)
    gi = os.path.join(d, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w") as f:
            f.write("*\n")
    return d


def register_project(cwd):
    """Add cwd to the global pointer index if absent. Paths only -- an aggregator reads
    this to find the per-project logs. Deliberately not a metrics store: the numbers
    stay with the project that produced them."""
    try:
        path = registry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        known = set()
        if os.path.isfile(path):
            with open(path) as f:
                for line in f:
                    try:
                        known.add((json.loads(line) or {}).get("path"))
                    except Exception:
                        continue  # a corrupt line must not hide the rest
        if cwd in known:
            return
        with open(path, "a") as f:
            f.write(json.dumps({"path": cwd, "first_seen": now_iso()}) + "\n")
    except Exception:
        pass


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def digest(text):
    """Truncated head + full-text hash. Enough to identify and group runs without
    parking whole prompts (which can embed real source) on disk."""
    text = text or ""
    return {
        "head": text[:200],
        "sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16],
        "chars": len(text),
    }


def write_runlog(cwd, record):
    """Append one run record. Best-effort by contract -- never raises."""
    try:
        with _WRITE_LOCK:
            path = os.path.join(runlog_dir(cwd), RUNLOG_FILE)
            with open(path, "a") as f:
                f.write(json.dumps(record, sort_keys=True) + "\n")
            register_project(cwd)
    except Exception:
        pass


def ledger_summary(cwd):
    """Aggregate of this project's qwen_delegate history, for the LEDGER line.

    The log had no reader: `leverage` was computed and never surfaced, and a
    dedicated logging agent existed in the field solely because receipts had
    no memory. Returns {"n", "ok", "red", "stopped", "peak"} or None when
    there is no history. Tolerant line-by-line parse; never raises.
    """
    try:
        path = os.path.join(cwd, RUNLOG_DIR, RUNLOG_FILE)
        if not os.path.isfile(path):
            return None
        n = ok = red = stopped = 0
        peak = 0
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue  # a corrupt line must not hide the rest
                if not isinstance(rec, dict) or rec.get("tool") != "qwen_delegate":
                    continue
                status = rec.get("status") or ""
                # A `running` record is a SUBMISSION marker (U5.2), not a run
                # that finished. Counting it would inflate the lifetime total
                # and file every in-flight run in the red bucket -- the ledger
                # would read worse the busier the project is.
                if status == "running":
                    continue
                n += 1
                if status in ("success", "success_but_preflight_passed"):
                    ok += 1
                elif status in ("stopped", "compaction_refused"):
                    stopped += 1
                else:
                    red += 1
                try:
                    peak = max(peak, int(rec.get("peak_context") or 0))
                except (TypeError, ValueError):
                    pass
        if n == 0:
            return None
        return {"n": n, "ok": ok, "red": red, "stopped": stopped, "peak": peak}
    except Exception:
        return None


def briefs_dir(cwd):
    """Where this project's stored briefs live (created)."""
    d = os.path.join(runlog_dir(cwd), BRIEFS_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def brief_path(cwd, session_id):
    """One session's brief. The id is reduced to safe characters: it arrives
    from the executor on one side and from a caller's `retry_of` on the other,
    and neither should be able to name a path outside this directory."""
    safe = "".join(c for c in str(session_id or "")
                   if c.isalnum() or c in "-_") or "unnamed"
    return os.path.join(cwd, RUNLOG_DIR, BRIEFS_DIR, f"{safe}.json")


def save_brief(cwd, session_id, brief):
    """Store the resolved call behind `session_id`, so a retry costs a sentence.

    A deliberate tension with digest(), which keeps only a head and a hash of
    every prompt precisely so whole prompts -- which embed real source -- do
    not accumulate in a permanent log. A brief is the opposite kind of object:
    a working file for ONE session, written beside the source it quotes, under
    the self-ignoring .qwen-delegate/, never committed, and switchable off
    with `store_briefs: false`. The log stays a log.

    Best-effort by contract: a brief that cannot be written costs a future
    retry its retyping, which must never be worth failing a finished run over.
    """
    try:
        briefs_dir(cwd)
        path = brief_path(cwd, session_id)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"session": session_id, "ts": now_iso(),
                       "args": brief}, f, sort_keys=True)
        os.replace(tmp, path)
        return path
    except Exception:
        return None


def load_brief(cwd, session_id):
    """The stored brief for a session, or None. Never raises."""
    try:
        with open(brief_path(cwd, session_id)) as f:
            brief = json.load(f)
        return brief if isinstance(brief, dict) else None
    except Exception:
        return None


def _pid_alive(pid):
    """Whether a pid is still running. Unknowable => alive.

    Signal 0 is the standard liveness probe. A pid owned by another user
    answers EPERM, which is proof it EXISTS -- reading that as death would
    report every run of a second session on the machine as dead.
    """
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    except (TypeError, ValueError):
        return True
    return True


def runs_in_flight(cwd):
    """Submitted delegations with no completion record yet (U5.2).

    A submit appends `{"status": "running", "run_id", "pid"}` and the verdict's
    ordinary record -- carrying the same `run_id` -- closes it LOGICALLY. The
    first line is never rewritten: an append-only log a reader can trust is
    worth more than a tidy one, and a crashed run cannot rewrite anything
    anyway.

    Which is the staleness rule: the delegation runs on a daemon thread of the
    MCP server process, so a session that ends takes its in-flight runs with
    it, leaving a `running` record whose pid is gone. Those are returned with
    `dead: True` rather than dropped -- "it died with your session" is the
    answer the caller polling for a receipt is looking for.

    Returns [{"run_id", "pid", "ts", "dead"}] in log order; never raises.
    """
    out = []
    try:
        path = os.path.join(cwd, RUNLOG_DIR, RUNLOG_FILE)
        if not os.path.isfile(path):
            return []
        running, closed = [], set()
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue  # a corrupt line must not hide the rest
                if not isinstance(rec, dict):
                    continue
                rid = rec.get("run_id")
                if not rid:
                    continue
                if rec.get("status") == "running":
                    running.append(rec)
                else:
                    closed.add(rid)
        for rec in running:
            if rec["run_id"] in closed:
                continue
            out.append({"run_id": rec["run_id"], "pid": rec.get("pid"),
                        "ts": rec.get("ts"),
                        "dead": not _pid_alive(rec.get("pid"))})
    except Exception:
        return out
    return out


def _tok_zero():
    return {"prompt": 0, "completion": 0, "total": 0, "cached": 0, "thoughts": 0}


def leverage_record(tool, cwd, status, verdict, stats, peak,
                    executor="qwen-local", cost_usd=0.0, extra=None):
    """
    Assemble the common half of a run record.

    `verdict` is the exact string handed back to Claude, so verdict_chars measures the
    real context cost -- the denominator of the whole thesis.
    """
    tokens = stats.get("tokens") or _tok_zero()
    v_chars = len(verdict or "")
    v_tokens = round(v_chars / 4.0)
    rec = {
        "ts": now_iso(),
        "tool": tool,
        "status": status,
        "cwd": cwd,
        "tokens": tokens,
        "tokens_main": stats.get("tokens_main") or _tok_zero(),
        "tokens_overhead": stats.get("tokens_overhead") or _tok_zero(),
        "token_source": stats.get("token_source") or "none",
        "peak_context": peak,
        "verdict_chars": v_chars,
        "verdict_tokens_est": v_tokens,
        "leverage": round(tokens["total"] / v_tokens, 1) if v_tokens else None,
        "duration_ms": stats.get("ms") or 0,
        "turns": stats.get("turns") or 0,
        "tools": {
            "calls": stats.get("tools") or 0,
            "fail": stats.get("tool_fail") or 0,
            "names": stats.get("tool_names") or [],
        },
        "api_errors": stats.get("api_errors") or 0,
        "lines_added": stats.get("lines_added") or 0,
        "lines_removed": stats.get("lines_removed") or 0,
        "models": stats.get("models") or [],
        "executor": executor,
        "cost_usd": float(cost_usd),
    }
    if extra:
        rec.update(extra)
    return rec
