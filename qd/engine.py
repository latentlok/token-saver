#!/usr/bin/env python3
"""
The delegation loop — behavior frozen by specs/engine_spec.py.

Ports server.py's _delegate_once / run_qwen / retry_prompt into a clean
two-function surface backed entirely by qd submodules.
"""

import json
import os
import re
import subprocess

from qd.profiles import resolve, cost_usd
from qd.invoke import (
    run_executor, accum_stats, cum_zero,
    was_compacted_since_ack, ack_compaction,
    truncate,
)
from qd.gittree import (
    git, is_git_repo,
    snapshot, violated_specs, revert_specs,
    _project_config, _global_config,
)
from qd.bootstrap import (
    worker_rules_status, bootstrap_worker_rules,
    bootstrap_notice, bootstrap_failed_refusal,
    nongit_refusal, detect_test_cmd,
)
from qd import worktrees
from qd.verdict import render, HANDOFF_SUFFIX, VERIFY_CAP
from qd import refs
from qd import graph

_DEFAULT_MAX_ITER = 3
_DEFAULT_TIMEOUT = 900

_SELF_GATE_PATH = os.path.join(".qwen-delegate", "selfgate.sh")

_SELF_GATE = """#!/bin/bash
# Generated per-attempt by trust="self" (L5): the DELEGATE'S OWN suite is the
# gate; this wrapper only guards the vacuous pass. Worker edits are overwritten.
cd "$(dirname "$0")/.." || exit 1
out=$({suite} 2>&1)
status=$?
echo "$out" | tail -25
[ "$status" -ne 0 ] && exit 1
ran=$(echo "$out" | grep -Eo 'Ran [0-9]+ tests?|[0-9]+ passed' | grep -Eo '[0-9]+' | head -1)
if [ -n "$ran" ] && [ "$ran" -lt {min} ]; then
  echo "SELF-GATE: only $ran tests ran -- write a real suite (>= {min} tests)"
  exit 1
fi
if [ -z "$ran" ]; then
  echo "SELF-GATE NOTE: could not parse a test count; vacuous-pass guard inactive"
fi
exit 0
"""


def _ensure_self_gate(work_cwd, min_override=None):
    """(Re)write the trust="self" gate script; return the verify command.

    Rewritten before every gate run so a worker edit to the script cannot
    survive to the next gate (the same reason spec files auto-revert). Lives
    in .qwen-delegate/ -- self-gitignored, so it never appears in CHANGED.
    Suite: the project's detected test command, else stdlib unittest discovery.
    Vacuous-pass guard: >= min_tests (project .qwen-delegate.json, default 5)
    when a test count is parseable (unittest "Ran N" / pytest "N passed").
    min_override: the incremental ratchet (see delegate()) -- an existing green
    suite of N tests raises the bar to N+1 so the gate binds on the delta.
    """
    min_tests = 5
    try:
        with open(os.path.join(work_cwd, ".qwen-delegate.json")) as f:
            min_tests = int(json.load(f).get("min_tests") or min_tests)
    except Exception:
        pass
    if min_override is not None:
        min_tests = max(min_tests, min_override)
    suite = detect_test_cmd(work_cwd) or \
        "python3 -m unittest discover -s tests -t . -v"
    d = os.path.join(work_cwd, ".qwen-delegate")
    os.makedirs(d, exist_ok=True)
    gi = os.path.join(d, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w") as f:
            f.write("*\n")
    path = os.path.join(work_cwd, _SELF_GATE_PATH)
    with open(path, "w") as f:
        f.write(_SELF_GATE.format(suite=suite, min=min_tests))
    os.chmod(path, 0o755)
    return f"bash {_SELF_GATE_PATH}"


def _run_verify(cmd, cwd):
    """Run verify shell command; return (passed_bool, combined_output_str)."""
    try:
        v = subprocess.run(
            cmd, cwd=cwd, shell=True,
            capture_output=True, text=True, timeout=300,
        )
        out = ((v.stdout or "") + (v.stderr or "")).strip()
        return v.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "verify command timed out after 300s"


def _retry_prompt(session_id, task, verify, v_out, on_compaction, repeated=False):
    """Build the retry prompt for attempt N+1.

    Returns (prompt_text, action) where action is "none" | "reinject" | "discard".
    """
    # Reflexion: force Qwen to diagnose before editing.
    if repeated:
        reflect = (
            "You have failed the SAME check again: your previous edit did not change this "
            "result, so that approach is wrong. Do not retry a variation of it. State in "
            "one or two sentences (1) why the previous approach cannot work and (2) a "
            "DIFFERENT approach to try, then apply it so the command passes."
        )
    else:
        reflect = (
            "Before editing, state in one or two sentences: (1) the ROOT CAUSE of this "
            "specific failure and (2) the fix you will make. Then apply it so the command "
            "passes."
        )

    failure = (
        f"The verification command failed. This is the real output:\n\n"
        f"```\n{truncate(v_out, VERIFY_CAP)}\n```\n\n"
        f"{reflect}"
    )

    if not was_compacted_since_ack(session_id):
        return failure, "none"

    ack_compaction(session_id)

    if on_compaction == "discard":
        return (
            f"A previous attempt at this task was made in a session that has been "
            f"discarded, so you are starting fresh. Work already on disk may be partial "
            f"or wrong -- read the current state rather than assuming.\n\n"
            f"{failure}\n\nVerify command: {verify}\n\nTask:\n{task}"
        ), "discard"

    return (
        f"{failure}\n\n"
        f"Your conversation history was summarised (compacted), so you may have lost the "
        f"original instructions, and any summary of your earlier work may be inaccurate. "
        f"Do not reconstruct what you think you did -- re-read the files and work from "
        f"what follows.\n\n"
        f"Verify command: {verify}\n\nOriginal task:\n{task}"
    ), "reinject"


def delegate(args):
    """Single-candidate delegation loop.

    Returns dict with keys:
        status, session_id, trail, result_text, denials,
        max_iter, last_verify, ctx
    """
    task = args["task"]
    cwd = args["cwd"]
    verify = args.get("verify")
    approval_mode = args.get("approval_mode", "auto-edit")
    max_iter = args.get("max_iterations", _DEFAULT_MAX_ITER)
    timeout = args.get("timeout_sec", _DEFAULT_TIMEOUT)
    if timeout:
        timeout = max(30, min(7200, int(timeout)))
    else:
        timeout = _DEFAULT_TIMEOUT
    session_id = args.get("session_id")
    on_compaction = args.get("on_compaction") or "reinject"
    if on_compaction not in ("reinject", "discard"):
        on_compaction = "reinject"
    worktree_mode = args.get("worktree")
    touch_scope = args.get("touch_scope")

    # --- Precondition: trust (R3: the slider) ---
    # Position resolves like `executor`: call arg > project .qwen-delegate.json
    # 'trust' > machine ~/.qwen-delegate/config.json 'trust' > builtin ("self"/L5).
    # The resolved value is validated below, so a bad config value is refused by
    # name exactly like a bad call arg.
    trust = (args.get("trust")
             or _project_config(cwd).get("trust")
             or _global_config().get("trust")
             or "self")
    if trust == "auto":
        # "auto" has no gate of its own -- the server cannot judge criticality.
        # Refuse the bare call so the orchestrator classifies THIS task and passes
        # a concrete level. A concrete call arg overrides an "auto" default above,
        # so this only fires when nobody chose.
        return {
            "status": "refused",
            "session_id": None,
            "trail": [],
            "result_text": (
                "Trust is \"auto\" — pick per task by criticality and pass it "
                "explicitly. Use trust=\"verified\" for correctness-critical, "
                "irreversible, outward-facing, or security / data-loss / money / "
                "auth work; trust=\"self\" (L5) for low-stakes mechanical or "
                "greenfield work. \"auto\" has no gate of its own (the server "
                "cannot judge criticality), so the orchestrator decides."
            ),
            "denials": [],
            "max_iter": max_iter,
            "last_verify": None,
            "ctx": {},
        }
    if trust not in ("verified", "self"):
        return {
            "status": "refused",
            "session_id": None,
            "trail": [],
            "result_text": (
                f"Trust dial \"{trust}\" is unknown — run refused. Accepted: "
                "\"verified\" (your verify command is the gate) or \"self\" "
                "(L5 full trust — the delegate's own suite is the gate; "
                "verify optional). Intermediate levels are a parked design."
            ),
            "denials": [],
            "max_iter": max_iter,
            "last_verify": None,
            "ctx": {},
        }

    # --- Precondition: git repo ---
    guard_on = is_git_repo(cwd)
    if not guard_on:
        return {
            "status": "refused",
            "session_id": None,
            "trail": [],
            "result_text": nongit_refusal(cwd),
            "denials": [],
            "max_iter": max_iter,
            "last_verify": None,
            "ctx": {},
        }

    # --- Bootstrap rules file ---
    bootstrap_note = None
    rules_state, rules_path = worker_rules_status(cwd)
    if rules_state != "ok":
        cmd, path = bootstrap_worker_rules(cwd)
        if not path:
            return {
                "status": "refused",
                "session_id": None,
                "trail": [],
                "result_text": bootstrap_failed_refusal(cwd, "IO error"),
                "denials": [],
                "max_iter": max_iter,
                "last_verify": None,
                "ctx": {},
            }
        bootstrap_note = bootstrap_notice(cmd, path)

    # --- Precondition: no dirty protected spec ---
    pre_dirty = violated_specs(cwd)
    if pre_dirty:
        return {
            "status": "refused",
            "session_id": None,
            "trail": [],
            "result_text": (
                f"STATUS: error\nUncommitted changes in protected spec file(s): "
                f"{', '.join(pre_dirty)}\n\nCommit or stash the spec changes first, "
                f"then delegate."
            ),
            "denials": [],
            "max_iter": max_iter,
            "last_verify": None,
            "ctx": {},
        }

    # --- Resolve executor profile ---
    profile = resolve(cwd, args.get("executor"))

    # --- Worktree acquisition (M4 seam 1) ---
    work_cwd = cwd
    wt = None
    if worktree_mode == "auto":
        wt = worktrees.acquire(cwd)
        work_cwd = wt["path"]

    # --- trust="self" (R3): server-generated gate over the delegate's own suite ---
    self_gate = trust == "self" and not verify
    if self_gate:
        verify = _ensure_self_gate(work_cwd)

    # --- Pre-run snapshot ---
    _, pre_sha_full = git(work_cwd, "rev-parse", "HEAD")
    pre_status = snapshot(work_cwd)
    pre_clean = not pre_status

    # --- Pre-flight verify ---
    preflight = None
    preflight_out = ""
    self_min = None
    if verify:
        preflight, preflight_out = _run_verify(verify, work_cwd)
        if self_gate and preflight:
            # Incremental ratchet: an existing suite is already green, so this
            # gate proves nothing -- and every later feature would read as
            # success_but_preflight_passed. Require MORE tests than preflight
            # found; the gate now binds on the delta, and preflight re-runs red.
            m = re.search(r"Ran (\d+) tests?|(\d+) passed", preflight_out or "")
            n = int(m.group(1) or m.group(2)) if m else 0
            self_min = n + 1
            verify = _ensure_self_gate(work_cwd, min_override=self_min)
            preflight, preflight_out = _run_verify(verify, work_cwd)

    # --- Refs snapshot (pre-run) ---
    refs_before = refs.snapshot(cwd)

    # --- Shell feedback prefix ---
    feedback = (args.get("shell_feedback") or "").strip()
    prompt = task
    if feedback:
        prompt = (
            "APPROVAL RESULT for shell commands you requested earlier "
            "(from the manager reviewing them):\n"
            f"{feedback}\n"
            "Respect these: do NOT retry a denied command; use the allowed ones or an "
            "alternative. Now continue the task below.\n\n---\n\n"
            + task
        )

    # --- Initial session tracking ---
    sessions = [session_id] if session_id else []
    send_suffix = False

    # --- ctx (C3 shape) ---
    ctx = {
        "cwd": cwd,
        "guard_on": guard_on,
        "preflight": preflight,
        "preflight_out": preflight_out,
        "pre_status": pre_status,
        "pre_sha": pre_sha_full,
        "pre_clean": pre_clean,
        "peak": 0,
        "meta": {},
        "timeout": timeout,
        "approval_mode": approval_mode,
        "task": task,
        "verify": verify,
        "cum": cum_zero(),
        "sessions": sessions,
        "reinjects": 0,
        "discards": 0,
        "on_compaction": on_compaction,
        "session_hint": session_id,
        "bootstrap_note": bootstrap_note,
        "notes": "",
        "worktree": None,
        "merge": None,
        "graph_line": None,
        "refs_added": [],
        "cost_usd": 0.0,
        "executor": args.get("executor"),
        "trust": trust,
    }

    trail = []
    result_text = ""
    denials = []
    last_verify = None
    prev_v_out = None

    for attempt in range(1, max_iter + 1):
        suffix = HANDOFF_SUFFIX if (attempt == 1 or send_suffix) else ""
        send_suffix = False

        # --- Invoke executor ---
        text, denials, sid, err, meta = run_executor(
            profile, prompt, work_cwd, approval_mode,
            timeout=timeout, session_id=session_id,
            verify=verify,
            shell_allow=args.get("shell_allow"),
            suffix=suffix,
        )

        ctx["meta"] = meta or {}
        ctx["peak"] = max(ctx.get("peak", 0), (meta or {}).get("peak", 0))
        accum_stats(ctx["cum"], (meta or {}).get("stats"))

        if sid:
            session_id = sid
            if sid not in ctx["sessions"]:
                ctx["sessions"].append(sid)

        # --- Executor error ---
        if err:
            trail.append(f"attempt {attempt}: {err}")
            break

        result_text = text or ""

        # --- Spec guard ---
        cheated = violated_specs(work_cwd, base=pre_sha_full)
        if cheated:
            revert_specs(work_cwd, cheated, base=pre_sha_full)
            names = ", ".join(cheated)
            trail.append(
                f"attempt {attempt}: SPEC VIOLATION -- edited {names} (auto-reverted)"
            )
            if attempt < max_iter:
                prompt = (
                    f"You edited a protected specification file ({names}). That file "
                    f"defines what correct means and has been reverted. Never modify a "
                    f"protected spec file. Fix the implementation code so it satisfies the "
                    f"spec as written. If you believe the spec is wrong, stop and say so "
                    f"instead of editing it."
                )
                if was_compacted_since_ack(session_id):
                    ack_compaction(session_id)
                    send_suffix = True
                    if on_compaction == "discard":
                        ctx["discards"] += 1
                        session_id = None
                    else:
                        ctx["reinjects"] += 1
                    prompt += (
                        f"\n\nYour conversation history was summarised (compacted), so "
                        f"you may have lost the original instructions and any summary of "
                        f"your earlier work may be inaccurate. Re-read the files; do not "
                        f"reconstruct it.\n\nOriginal task:\n{task}"
                    )
                continue
            break

        # --- No verify: unverified success ---
        if not verify:
            trail.append(f"attempt {attempt}: no verify supplied")
            break

        # --- C8 prefilter (advisory) — after executor, before gate ---
        post_snap = snapshot(work_cwd)
        changed = [
            p for p in set(list(post_snap.keys()) + list(pre_status.keys()))
            if post_snap.get(p) != pre_status.get(p)
        ]

        # --- Touch scope check (M4 seam 2) ---
        if touch_scope is not None and changed:
            violated_paths = []
            for p in changed:
                if p in touch_scope:
                    continue
                rc, _ = git(work_cwd, "ls-files", "--error-unmatch", p)
                if rc != 0:
                    continue
                violated_paths.append(p)
            if violated_paths:
                revert_specs(work_cwd, violated_paths, base=pre_sha_full)
                names = ", ".join(violated_paths)
                trail.append(
                    f"attempt {attempt}: TOUCH SCOPE VIOLATION -- edited {names} outside scope (auto-reverted)"
                )
                if attempt < max_iter:
                    prompt = (
                        f"You modified files outside the allowed set: {names}. "
                        f"Those files are off-limits and have been reverted. "
                        f"Only modify: {', '.join(touch_scope)}. "
                        f"You may create new files freely."
                    )
                    if was_compacted_since_ack(session_id):
                        ack_compaction(session_id)
                        send_suffix = True
                        if on_compaction == "discard":
                            ctx["discards"] += 1
                            session_id = None
                        else:
                            ctx["reinjects"] += 1
                        prompt += (
                            f"\n\nYour conversation history was summarised (compacted), so "
                            f"you may have lost the original instructions and any summary of "
                            f"your earlier work may be inaccurate. Re-read the files; do not "
                            f"reconstruct it.\n\nOriginal task:\n{task}"
                        )
                    continue
                break

        qwen_files = [p for p in changed if "_qwen." in p]
        prefilter_out = None
        prefilter_failed = False
        if qwen_files:
            test_cmd = detect_test_cmd(cwd)
            if test_cmd:
                tc = f"./{test_cmd}" if not test_cmd.startswith(".") else test_cmd
                try:
                    pv = subprocess.run(
                        f"{tc} {' '.join(qwen_files)}",
                        cwd=work_cwd, shell=True,
                        capture_output=True, text=True, timeout=60,
                        env=os.environ,
                    )
                    prefilter_out = (
                        ((pv.stdout or "") + (pv.stderr or "")).strip()
                    )[:2000]
                    prefilter_failed = pv.returncode != 0
                except Exception:
                    prefilter_out = "prefilter timed out or errored"
                    prefilter_failed = True

        # --- Run verify ---
        if self_gate:
            # overwrite any worker edit to the gate, keeping the ratcheted bar
            _ensure_self_gate(work_cwd, min_override=self_min)
        passed, v_out = _run_verify(verify, work_cwd)

        if passed:
            trail.append(f"attempt {attempt}: VERIFY PASS")
            if prefilter_failed:
                ctx["notes"] = "self-tests failing"
            break

        trail.append(f"attempt {attempt}: verify failed")
        last_verify = v_out

        if prefilter_failed and qwen_files:
            cmd_line = f"{tc} {' '.join(qwen_files)}"
            out_display = prefilter_out or "(no output)"
            last_verify = (
                f"{v_out}\n\n"
                f"Also: your own self-tests failed ({cmd_line}):\n"
                f"{out_display}"
            )

        # --- Gate suspect: identical to preflight output ---
        if preflight is False and v_out.strip() == (preflight_out or "").strip():
            trail[-1] = (
                f"attempt {attempt}: verify failed -- output IDENTICAL to preflight"
            )
            break

        # --- Build retry prompt ---
        repeated = (
            prev_v_out is not None
            and v_out.strip() == prev_v_out.strip()
        )
        prev_v_out = v_out

        if attempt < max_iter:
            prompt, action = _retry_prompt(
                session_id, task, verify,
                last_verify, on_compaction,
                repeated=repeated,
            )
            if action != "none":
                ctx[action + "s"] += 1
                send_suffix = True
                if action == "discard":
                    session_id = None
            continue

    # --- Determine status ---
    if not trail:
        status = "error"
    elif trail[-1].endswith(": VERIFY PASS"):
        status = "success"
    elif "SPEC VIOLATION" in trail[-1].upper():
        status = "spec_violation"
    elif "IDENTICAL to preflight" in trail[-1]:
        status = "gate_suspect"
    elif "no verify supplied" in trail[-1]:
        status = "unverified"
    else:
        status = "verify_failed"

    # --- Worktree commit or release (M4 seam 1) ---
    if wt is not None:
        if status == "success":
            git(work_cwd, "add", "-A")
            git(work_cwd, "commit", "-m", f"qwen delegation {wt['branch']}")
            ctx["worktree"] = {"path": wt["path"], "branch": wt["branch"]}
            ctx["merge"] = worktrees.classify_merge(cwd, wt["branch"])
        else:
            worktrees.release(cwd, wt["path"], wt["branch"])

    # --- Compute cost_usd ---
    try:
        t_main = ctx["cum"].get("tokens_main", {})
        tokens_in = t_main.get("prompt", 0) if isinstance(t_main, dict) else 0
        tokens_out = t_main.get("completion", 0) if isinstance(t_main, dict) else 0
        ctx["cost_usd"] = float(cost_usd(profile, tokens_in, tokens_out))
    except Exception:
        ctx["cost_usd"] = 0.0

    # --- Refs added ---
    ctx["refs_added"] = refs.added(refs_before, work_cwd)

    return {
        "status": status,
        "session_id": session_id,
        "trail": trail,
        "result_text": result_text,
        "denials": denials,
        "max_iter": max_iter,
        "last_verify": last_verify,
        "ctx": ctx,
    }


def run(args):
    """Delegate then render the verdict receipt.

    Returns the rendered verdict string.
    """
    d = delegate(args)
    cwd = args["cwd"]
    in_tree = (args.get("worktree") or "off") != "auto"
    try:
        d["ctx"]["graph_line"] = graph.graph_line(cwd)
    except Exception:
        pass
    receipt = render(
        d["status"], d["session_id"], d["trail"],
        d["result_text"], d["denials"],
        d["max_iter"], d["ctx"],
        last_verify=d["last_verify"],
    )
    if d["status"] == "success" and in_tree:
        try:
            post = snapshot(cwd)
            changed = [
                p for p in set(list(post.keys()) + list(d["ctx"]["pre_status"].keys()))
                if post.get(p) != d["ctx"]["pre_status"].get(p)
            ]
            graph.refresh_async(cwd, changed)
        except Exception:
            pass
    return receipt
