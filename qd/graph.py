#!/usr/bin/env python3
"""
Graph freshness keyed to git; behavior frozen by specs/graphstate_spec.py.

graphify owns querying; this module owns exactly one thing graphify cannot:
freshness as a git fact. The sidecar (.qwen-delegate/graph.json) tracks the
last-indexed SHA and status; staleness is a plain `git diff` between that SHA
and HEAD. refresh_sync/refresh_async shell out to graphify to update the index.
"""

import json
import os
import subprocess
import threading

from qd.gittree import git
from qd.runlog import now_iso, runlog_dir


def sidecar_path(cwd):
    """Path to .qwen-delegate/graph.json."""
    return os.path.join(runlog_dir(cwd), "graph.json")


def read_state(cwd):
    """Parse the sidecar JSON, or return None if absent/unparseable."""
    path = sidecar_path(cwd)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_sidecar(cwd, state):
    """Atomically write *state* as JSON to the sidecar path."""
    path = sidecar_path(cwd)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def staleness(cwd):
    """Return staleness snapshot keyed to the indexed SHA.

    Returns {"indexed_sha": str|None, "stale": list[str], "status": str}.
    """
    state = read_state(cwd)
    if state is None:
        return {"indexed_sha": None, "stale": [], "status": "none"}

    sha = state.get("indexed_sha")
    rc, out = git(cwd, "diff", "--name-only", sha, "HEAD")
    if rc != 0:
        # History rewritten / unknown SHA — degrade gracefully.
        return {"indexed_sha": sha, "stale": [], "status": "none"}
    files = [f for f in out.splitlines() if f]
    if not files:
        return {"indexed_sha": sha, "stale": [], "status": "fresh"}
    return {"indexed_sha": sha, "stale": sorted(files), "status": "stale"}


def graphify_cmd(cwd, files=None):
    """Return the command list for *graphify update <cwd>*."""
    bin_ = os.environ.get("QWEN_DELEGATE_GRAPHIFY", "graphify")
    return [bin_, "update", cwd]


def _do_refresh(cwd, files, prior_sha):
    """Run graphify and write the final sidecar state. Never raises."""
    try:
        result = subprocess.run(
            graphify_cmd(cwd, files),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            reason = (result.stderr or "nonzero exit").splitlines()[-1]
            _write_sidecar(cwd, {
                "indexed_sha": prior_sha,
                "ts": now_iso(),
                "status": "failed",
                "reason": reason[:200],
            })
            return

        # Success — capture current HEAD
        rc, head = git(cwd, "rev-parse", "HEAD")
        _write_sidecar(cwd, {
            "indexed_sha": head if rc == 0 else prior_sha,
            "ts": now_iso(),
            "status": "fresh",
        })
    except FileNotFoundError:
        _write_sidecar(cwd, {
            "indexed_sha": prior_sha,
            "ts": now_iso(),
            "status": "failed",
            "reason": "graphify not installed",
        })
    except Exception as exc:
        _write_sidecar(cwd, {
            "indexed_sha": prior_sha,
            "ts": now_iso(),
            "status": "failed",
            "reason": str(exc)[:200],
        })


def refresh_sync(cwd, files=None):
    """Synchronously refresh the graph index.

    Never raises — advisory infrastructure must not block a delegation.
    """
    path = sidecar_path(cwd)
    prior_sha = None
    if os.path.isfile(path):
        try:
            with open(path) as f:
                prior_sha = json.load(f).get("indexed_sha")
        except Exception:
            pass

    # Phase 1: mark indexing (visible immediately)
    _write_sidecar(cwd, {
        "indexed_sha": prior_sha,
        "ts": now_iso(),
        "status": "indexing",
    })

    _do_refresh(cwd, files, prior_sha)


def refresh_async(cwd, files=None):
    """Start a background thread running refresh_sync; return the thread.

    Writes "indexing" on the main thread first so callers observe the state
    immediately after this returns.
    """
    path = sidecar_path(cwd)
    prior_sha = None
    if os.path.isfile(path):
        try:
            with open(path) as f:
                prior_sha = json.load(f).get("indexed_sha")
        except Exception:
            pass

    # Mark indexing on main thread — visible immediately to the caller.
    _write_sidecar(cwd, {
        "indexed_sha": prior_sha,
        "ts": now_iso(),
        "status": "indexing",
    })

    th = threading.Thread(target=_do_refresh, args=(cwd, files, prior_sha),
                          daemon=True)
    th.start()
    return th


def graph_line(cwd):
    """Return a single C2 GRAPH: status line."""
    state = read_state(cwd)
    if state is None:
        return "GRAPH: none \u2014 run graphify once to index"

    status = state.get("status", "none")
    if status == "indexing":
        return "GRAPH: indexing"
    if status == "failed":
        reason = state.get("reason") or "unknown"
        return f"GRAPH: failed: {reason}"

    # fresh or stale
    s = staleness(cwd)
    sha_short = (s["indexed_sha"] or "?")[:7]
    if s["status"] == "fresh" or not s["stale"]:
        return f"GRAPH: fresh @ {sha_short}"
    n = len(s["stale"])
    return f"GRAPH: stale ({n} files since {sha_short}) \u2014 refresh running"
