#!/usr/bin/env python3
"""
Run log v2 — behavior frozen by specs/runlog_spec.py.

Ports the v1 logging surface from server.py and adds C5 obligations:
every record always carries executor (str) and cost_usd (float).
"""

import hashlib
import json
import os
import threading
import time

RUNLOG_DIR = ".qwen-delegate"
RUNLOG_FILE = "runs.jsonl"

_WRITE_LOCK = threading.Lock()


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
