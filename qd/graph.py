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
import shutil
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


def graphify_bin():
    """The graphify binary: $QWEN_DELEGATE_GRAPHIFY or `graphify` on PATH."""
    return os.environ.get("QWEN_DELEGATE_GRAPHIFY", "graphify")


def available():
    """True if the graphify binary is resolvable (PATH or QWEN_DELEGATE_GRAPHIFY)."""
    return bool(shutil.which(graphify_bin()))


def graphify_cmd(cwd, files=None):
    """Return the command list for the per-delegation refresh: a STRUCTURAL
    `graphify update <cwd> --no-cluster`.

    `graphify update` is LLM-free by design ("no LLM needed" per the CLI -- it only
    re-extracts the AST), so this refresh never reaches a backend and cannot bill a
    cloud LLM. Only `extract`/`label`/`cluster-only` (semantic naming) touch an LLM,
    and the plugin never runs those. `--no-cluster` skips even the local clustering
    pass: the worker's lookups (`explain`/`path`/`affected`) read the raw graph, not
    communities, so clustering would be wasted work every delegation. Semantic naming
    stays a deliberate manual step with an explicit `--backend <local>` -- never bare,
    which auto-selects Bedrock when AWS_PROFILE is set.
    """
    return [graphify_bin(), "update", cwd, "--no-cluster"]


# The read-only subcommands, and ONLY those. `^graphify\b` would also permit
# `graphify update`, which on a repo with a semantic index configured can bill a
# cloud account -- the A10 lesson: a command pattern grants every SUBCOMMAND the
# command has, so the real boundary is the most powerful one reachable through
# it (PRINCIPLES §III).
#
# `update` is deliberately absent even though the plugin itself runs it: the
# plugin runs it AFTER the verdict, on its own terms, where the cost is a
# decision somebody made. A worker that can call it decides for them.
READ_ONLY = ("explain", "query", "affected", "god-nodes", "stats", "show")


def read_only_allow():
    """The `shell_allow` pattern that lets a WORKER read the graph.

    Returned rather than applied, because whether to grant it is a decision
    about the run and belongs to whoever owns the run's permissions. This module
    owns only what graphify is.
    """
    return r"^graphify (?:%s)\b" % "|".join(READ_ONLY)


def bootstrap_line():
    """One SETUP sentence about the code graph, for a first delegation on a fresh repo.

    Installed -> the post-delegation refresh will build/maintain a structural graph;
    absent -> an optional install tip. Structural only; semantic naming is never
    triggered here.
    """
    if available():
        return (
            "graphify is installed, so I'll keep a structural code graph here (free, "
            "local, no LLM) -- from the next run on, the worker locates code through it "
            "instead of reading files."
        )
    return (
        "Tip: graphify isn't installed. Installing it (`uv tool install graphifyy`) "
        "lets the worker locate code without reading it -- optional; the graphify-setup "
        "skill sets it up."
    )


def _do_refresh(cwd, files, prior_sha):
    """Run graphify and write the final sidecar state. Never raises."""
    try:
        result = subprocess.run(
            graphify_cmd(cwd, files),
            capture_output=True,
            text=True, stdin=subprocess.DEVNULL,
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

        # Success — capture current HEAD.
        # "indexed", NOT "fresh": freshness is a comparison against HEAD *now*,
        # so it cannot be stored -- it goes false the next time anyone commits.
        # The field used to read "fresh", and did: after a package conversion
        # it claimed fresh while 32 of 32 files were stale and the whole graph
        # was uncached. Anyone reading the sidecar to answer "is my graph
        # current?" -- the obvious thing to do -- got a confident wrong answer.
        # What IS storable is that an index completed at this SHA; whether that
        # is still current is staleness()'s job, computed live from git.
        rc, head = git(cwd, "rev-parse", "HEAD")
        _write_sidecar(cwd, {
            "indexed_sha": head if rc == 0 else prior_sha,
            "ts": now_iso(),
            "status": "indexed",
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


def graph_line(cwd, will_refresh=False):
    """Return a single C2 GRAPH: status line.

    `will_refresh` must reflect whether a refresh is ACTUALLY about to be
    scheduled (in-tree success). The stale line used to claim "refresh
    running" unconditionally -- on failed and worktree runs nothing refreshes,
    and the receipt was lying.
    """
    state = read_state(cwd)
    if state is None:
        return "GRAPH: none \u2014 run graphify once to index"

    # Only the states that CANNOT be recomputed are read from the file:
    # "indexing" and "failed" are facts about a write, not about the tree.
    # Everything else is derived live below -- never read from disk.
    status = state.get("status", "none")
    if status == "indexing":
        return "GRAPH: indexing"
    if status == "failed":
        reason = state.get("reason") or "unknown"
        return f"GRAPH: failed: {reason}"

    s = staleness(cwd)
    sha_short = (s["indexed_sha"] or "?")[:7]
    if s["status"] == "fresh" or not s["stale"]:
        return f"GRAPH: fresh @ {sha_short}"
    n = len(s["stale"])
    tail = "refresh running" if will_refresh else "run graphify update"
    return f"GRAPH: stale ({n} files since {sha_short}) \u2014 {tail}"
