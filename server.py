#!/usr/bin/env python3
"""
qwen-delegate MCP server.

Exposes Qwen Code as a delegation tool for Claude. Claude plans and specifies;
Qwen executes in a scoped workspace; an objective verify command confirms the
result before Claude spends context on it.

Three structural protections, because Qwen's self-report is not evidence:

  1. verify gate   -- an objective command decides success, not Qwen's prose.
  2. spec guard    -- *_spec.py files are Claude-authored. If Qwen edits one, it is
                      auto-reverted and the attempt is failed. Qwen has, in practice,
                      rewritten spec tests to make them pass.
  3. iterate loop  -- on failure, the real verify output is fed back and Qwen retries
                      in the same session. Worker tokens are free, so iterating to
                      green costs latency only.

stdio JSON-RPC 2.0, no dependencies. stdout is the protocol channel -- all
diagnostics go to stderr.
"""

import hashlib
import json
import os
import subprocess
import sys

QWEN_BIN = os.environ.get("QWEN_BIN", "qwen")
RESULT_CAP = 3000
VERIFY_CAP = 2500
DEFAULT_TIMEOUT = 600
DEFAULT_MAX_ITER = 3
SPEC_GLOB = "*_spec.py"

PROTOCOL_VERSION = "2024-11-05"


def log(msg):
    print(f"[qwen-mcp] {msg}", file=sys.stderr, flush=True)


TOOL = {
    "name": "qwen_delegate",
    "description": (
        "Delegate a well-specified coding task to a local Qwen Code agent. Qwen runs "
        "with shell/edit/write enabled (and Firecrawl web access) in the given "
        "workspace, and returns only its final result -- its tool-call noise stays out "
        "of your context.\n\n"
        "Use for mechanical, verifiable work: boilerplate, repetitive refactors, test "
        "writing, doc generation, web research. Do NOT use for tasks needing judgment. "
        "Qwen is a 27B local model: given a vague task it does NOT stop and ask -- it "
        "confidently invents scope and reports success. Specify exact files, symbols, "
        "and expected behavior.\n\n"
        "ALWAYS pass `verify` -- a shell command exiting 0 only if the task truly "
        "succeeded. Qwen has fabricated 'all tests pass' for tests it never ran. Its "
        "claim is not evidence; only the gate is.\n\n"
        "On verify failure the tool automatically feeds the real error output back and "
        "retries in the same session (see max_iterations). Worker tokens are free, so "
        "prefer letting it iterate over returning a failure to you.\n\n"
        "Files matching *_spec.py are treated as YOUR protected specification: if Qwen "
        "edits one, it is auto-reverted and the attempt fails. Write gate tests in "
        "*_spec.py; let Qwen write its own tests in *_qwen.py.\n\n"
        "Re-read any file Qwen touched before editing it yourself -- your cached copy "
        "is stale. Parallel calls MUST use separate `cwd` worktrees."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "The task, specified concretely enough to execute without judgment "
                    "calls. Name exact file paths, symbols, and the expected end state. "
                    "Vague tasks produce confident invented scope, not questions."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Absolute path to the workspace. Qwen runs at full user privilege, "
                    "so scope this to the project or worktree. For parallel fan-out, "
                    "give each call its own git worktree."
                ),
            },
            "verify": {
                "type": "string",
                "description": (
                    "Shell command run in `cwd` after each attempt; exit 0 means real "
                    "success (e.g. 'venv/bin/pytest -q x_spec.py', 'tsc --noEmit'). Its "
                    "output is fed back to Qwen on failure, so make failures legible. "
                    "Strongly recommended -- omit only for read-only/research tasks."
                ),
            },
            "max_iterations": {
                "type": "integer",
                "description": (
                    f"Attempts before giving up (default {DEFAULT_MAX_ITER}, max 10). On "
                    "each failure the verify output is fed back and Qwen retries with "
                    "warm context. Worker tokens are free -- raise this for fiddly tasks."
                ),
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Resume a prior Qwen session (the SESSION value from an earlier call) "
                    "to continue THAT task with warm context, skipping ~17.6k tokens of "
                    "reload. Sessions are cwd-scoped: pass the same cwd or the id will not "
                    "resolve.\n\n"
                    "STATEFUL when the follow-up builds directly on what Qwen just did "
                    "('now add X to the function you wrote', 'fix the edge case you "
                    "missed'). It still has the code and reasoning in context.\n\n"
                    "STATELESS (omit this) for anything else -- this is the default and "
                    "usually correct. A fresh session re-reads QWEN.md, which is what "
                    "makes the rules bind, and prevents one task's reasoning from "
                    "contaminating the next. A long-lived session drifts as its context "
                    "fills and starts silently forgetting the rules."
                ),
            },
            "approval_mode": {
                "type": "string",
                "enum": ["plan", "default", "auto-edit", "auto", "yolo"],
                "description": (
                    "Tool-approval policy. Default 'yolo' auto-approves shell/edit/write "
                    "-- required headless, since 'default' auto-denies with no TTY. Use "
                    "'plan' for read-only analysis."
                ),
            },
            "timeout_sec": {
                "type": "integer",
                "description": f"Kill each attempt after this many seconds (default {DEFAULT_TIMEOUT}).",
            },
        },
        "required": ["task", "cwd"],
    },
}


# ---------- git-backed spec guard ----------


def git(cwd, *a):
    try:
        p = subprocess.run(
            ["git", *a], cwd=cwd, capture_output=True, text=True, timeout=30
        )
        # rstrip newlines only -- NEVER .strip(). `status --porcelain` encodes state in
        # leading columns (" M path"), so stripping the whole output eats the first
        # line's leading space and shifts its path by one character.
        return p.returncode, (p.stdout or "").rstrip("\n")
    except Exception:
        return 1, ""


def is_git_repo(cwd):
    rc, out = git(cwd, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out == "true"


def spec_files(cwd):
    """Tracked *_spec.py paths, repo-relative."""
    rc, out = git(cwd, "ls-files", SPEC_GLOB, f"**/{SPEC_GLOB}")
    if rc != 0 or not out:
        return []
    return [p for p in out.splitlines() if p.strip()]


def violated_specs(cwd):
    """Tracked spec files with uncommitted modifications."""
    specs = spec_files(cwd)
    if not specs:
        return []
    rc, out = git(cwd, "diff", "--name-only", "--", *specs)
    if rc != 0 or not out:
        return []
    return [p for p in out.splitlines() if p.strip()]


def revert_specs(cwd, paths):
    if paths:
        git(cwd, "checkout", "--", *paths)


def head_sha(cwd):
    rc, out = git(cwd, "rev-parse", "--short", "HEAD")
    return out if rc == 0 else None


def status_map(cwd):
    """{path: porcelain status code} for the working tree."""
    rc, out = git(cwd, "status", "--porcelain")
    if rc != 0 or not out:
        return {}
    m = {}
    for line in out.splitlines():
        if len(line) > 3:
            m[line[3:].strip()] = line[:2].strip()
    return m


def file_sha(cwd, path):
    """Content hash, or None if unreadable/absent."""
    try:
        full = os.path.join(cwd, path)
        if not os.path.isfile(full):
            return None
        if os.path.getsize(full) > 8_000_000:
            return f"big:{os.path.getsize(full)}"
        with open(full, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except Exception:
        return None


def snapshot(cwd):
    """
    {path: (status_code, content_sha)} for every dirty path.

    The sha matters: comparing status codes alone is blind to a file that was ALREADY
    dirty and got edited again -- the code stays '??' or 'M' while the content changes.
    That produced a false "CHANGED: nothing" on a session-resume follow-up that had in
    fact rewritten the file.
    """
    return {p: (code, file_sha(cwd, p)) for p, code in status_map(cwd).items()}


def blast_radius(cwd, pre):
    """
    What changed during the run. Qwen reports what it *says* it did; this reports
    what the filesystem says. Run 3 of the scope-creep test claimed '104 tests pass'
    (true) while silently adding an unrequested public API -- the tool result gave no
    hint of the sprawl. This closes that.
    """
    post = snapshot(cwd)
    touched = sorted(p for p in post if post.get(p) != pre.get(p))
    gone = sorted(p for p in pre if p not in post)
    if not touched and not gone:
        return "CHANGED: nothing (Qwen wrote no files)"

    rc, numstat = git(cwd, "diff", "--numstat")
    lines = {}
    if rc == 0 and numstat:
        for row in numstat.splitlines():
            parts = row.split("\t")
            if len(parts) == 3:
                lines[parts[2]] = (parts[0], parts[1])

    out = [f"CHANGED: {len(touched) + len(gone)} file(s)"]
    for p in gone:
        out.append(f"  - {p} (reverted/removed)")
    for p in touched[:20]:
        code = post.get(p, ("?", None))[0]
        if code == "??":
            # Already-untracked files stay '??' when edited, so distinguish by presence
            # in the pre-run snapshot rather than by status code.
            out.append(f"  + {p} (new)" if p not in pre else f"  ~ {p} (edited, untracked)")
        elif p in lines:
            add, rem = lines[p]
            out.append(f"  M {p} (+{add}/-{rem})")
        else:
            out.append(f"  {code} {p}")
    if len(touched) > 20:
        out.append(f"  ... and {len(touched) - 20} more")
    return "\n".join(out)


# ---------- handoff ----------

# Appended to every task. Gives Claude a compact, structured basis for deciding whether
# to continue this session or start fresh -- without reading Qwen's full prose. FILES is
# cross-checked against the filesystem in render(): a mismatch means Qwen misreported
# its own blast radius, which is exactly the class of error the gate exists to catch.
HANDOFF_SUFFIX = """

---
Finish your reply with exactly these three lines, after any prose:

HANDOFF: <one line: what state the work is in now>
FILES: <comma-separated paths you created or modified, or the word: none>
NEXT: <one line: what a follow-up would need to know, or the word: nothing>

Keep each line under 120 characters. This is a machine-read handoff, not prose.
"""


def parse_handoff(text):
    """Pull the HANDOFF/FILES/NEXT lines out of Qwen's reply."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip().lstrip("*# ").strip()
        for key in ("HANDOFF", "FILES", "NEXT"):
            prefix = f"{key}:"
            if line.upper().startswith(prefix):
                out[key] = line[len(prefix):].strip().strip("*`").strip()
    return out


def strip_handoff(text):
    """Remove the handoff lines from prose so they aren't shown twice."""
    keep = []
    for line in (text or "").splitlines():
        probe = line.strip().lstrip("*# ").strip().upper()
        if any(probe.startswith(f"{k}:") for k in ("HANDOFF", "FILES", "NEXT")):
            continue
        keep.append(line)
    return "\n".join(keep).strip()


# ---------- qwen invocation ----------


def invoke_qwen(task, cwd, approval_mode, timeout, session_id):
    cmd = [
        QWEN_BIN, "-p", task + HANDOFF_SUFFIX,
        "--approval-mode", approval_mode, "-o", "json",
    ]
    if session_id:
        cmd += ["-r", session_id]

    env = dict(os.environ)
    env["QWEN_CODE_SUPPRESS_YOLO_WARNING"] = "1"

    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return None, [], None, f"timed out after {timeout}s"
    except FileNotFoundError:
        return None, [], None, f"qwen binary not found (QWEN_BIN={QWEN_BIN})"

    text, denials, sid = parse_qwen_json(proc.stdout)
    if text is None:
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        return None, [], sid, f"unparseable output (exit {proc.returncode}): {tail}"
    return text, denials, sid, None


def run_verify(verify, cwd):
    """Return (passed, combined_output)."""
    try:
        v = subprocess.run(
            verify, cwd=cwd, shell=True, capture_output=True, text=True, timeout=300
        )
        out = ((v.stdout or "") + (v.stderr or "")).strip()
        return v.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "verify command timed out after 300s"


def run_qwen(args):
    task = args["task"]
    cwd = args["cwd"]
    verify = args.get("verify")
    approval_mode = args.get("approval_mode", "yolo")
    timeout = int(args.get("timeout_sec") or DEFAULT_TIMEOUT)
    max_iter = max(1, min(10, int(args.get("max_iterations") or DEFAULT_MAX_ITER)))
    session_id = args.get("session_id")

    if not os.path.isabs(cwd):
        return f"STATUS: error\ncwd must be an absolute path, got: {cwd}"
    if not os.path.isdir(cwd):
        return f"STATUS: error\ncwd does not exist or is not a directory: {cwd}"

    guard_on = is_git_repo(cwd)
    if not guard_on:
        log(f"warning: {cwd} is not a git repo -- spec guard and rollback unavailable")

    # The guard reverts any spec file that differs from HEAD after a run, and cannot
    # tell who edited it. If a spec is ALREADY dirty, that revert would silently
    # destroy Claude's uncommitted work and blame Qwen for it. Refuse instead --
    # this makes "commit before delegating" a precondition rather than a habit.
    if guard_on:
        pre_dirty = violated_specs(cwd)
        if pre_dirty:
            names = ", ".join(pre_dirty)
            return (
                f"STATUS: error\nUncommitted changes in protected spec file(s): {names}\n\n"
                f"The spec guard reverts post-run spec diffs to HEAD and cannot attribute "
                f"them, so running now would destroy this uncommitted work. Commit or stash "
                f"the spec changes first, then delegate."
            )

    # Snapshot pre-run state so the result can report what Qwen actually touched and
    # how to undo it, rather than what Qwen claims it touched.
    pre_status = snapshot(cwd) if guard_on else {}
    pre_sha = head_sha(cwd) if guard_on else None
    pre_clean = guard_on and not pre_status

    # Pre-flight the gate. If verify ALREADY passes, a post-run pass proves nothing --
    # it cannot distinguish "Qwen did the work" from "Qwen did nothing" from "the task's
    # premise was false". The false-premise test passed for exactly this reason.
    preflight = None
    if verify:
        preflight, _ = run_verify(verify, cwd)
        log(f"preflight verify: {'pass' if preflight else 'fail'}")

    prompt = task
    trail = []
    result_text = ""
    denials = []
    ctx = {
        "pre_status": pre_status,
        "pre_sha": pre_sha,
        "pre_clean": pre_clean,
        "preflight": preflight,
        "guard_on": guard_on,
        "cwd": cwd,
    }

    for attempt in range(1, max_iter + 1):
        log(f"attempt {attempt}/{max_iter} cwd={cwd} resume={session_id or '-'}")

        result_text, denials, sid, err = invoke_qwen(
            prompt, cwd, approval_mode, timeout, session_id
        )
        if sid:
            session_id = sid  # resume this session on retry

        if err:
            trail.append(f"attempt {attempt}: {err}")
            return render(
                "error", session_id, trail, result_text or "", denials, max_iter, ctx
            )

        # spec guard: Qwen must never edit Claude's *_spec.py
        cheated = violated_specs(cwd) if guard_on else []
        if cheated:
            revert_specs(cwd, cheated)
            names = ", ".join(cheated)
            trail.append(
                f"attempt {attempt}: SPEC VIOLATION -- edited {names} (auto-reverted)"
            )
            if attempt < max_iter:
                prompt = (
                    f"You edited a protected specification file ({names}). That file "
                    f"defines what correct means and has been reverted. Never modify a "
                    f"*_spec.py file. Fix the implementation code so it satisfies the "
                    f"spec as written. If you believe the spec is wrong, stop and say so "
                    f"instead of editing it.\n\nOriginal task:\n{task}"
                )
                continue
            return render(
                "spec_violation", session_id, trail, result_text, denials, max_iter, ctx
            )

        if not verify:
            trail.append(f"attempt {attempt}: no verify supplied")
            return render(
                "unverified", session_id, trail, result_text, denials, max_iter, ctx
            )

        passed, v_out = run_verify(verify, cwd)
        if passed:
            trail.append(f"attempt {attempt}: VERIFY PASS")
            return render("success", session_id, trail, result_text, denials, max_iter, ctx)

        trail.append(f"attempt {attempt}: verify failed")
        if attempt < max_iter:
            prompt = (
                f"The verification command failed. This is the real output:\n\n"
                f"```\n{truncate(v_out, VERIFY_CAP)}\n```\n\n"
                f"Fix the code so this command passes. Do not modify any *_spec.py file "
                f"-- fix the implementation. Run the command yourself to confirm before "
                f"reporting.\n\nVerify command: {verify}\n\nOriginal task:\n{task}"
            )
            continue

        return render(
            "verify_failed",
            session_id,
            trail,
            result_text,
            denials,
            max_iter,
            ctx,
            last_verify=v_out,
        )

    return render("verify_failed", session_id, trail, result_text, denials, max_iter, ctx)


def render(status, session_id, trail, result_text, denials, max_iter, ctx, last_verify=None):
    cwd = ctx["cwd"]
    guard_on = ctx["guard_on"]

    # "verify passed" is only evidence if it was failing beforehand.
    if ctx["preflight"] and status == "success":
        status = "success_but_preflight_passed"

    lines = [f"STATUS: {status}", f"SESSION: {session_id or 'unknown'}"]
    lines.append(f"ATTEMPTS: {len(trail)}/{max_iter}")
    for t in trail:
        lines.append(f"  - {t}")

    if guard_on:
        lines.append(blast_radius(cwd, ctx["pre_status"]))

    if status == "success_but_preflight_passed":
        lines.append(
            "PREFLIGHT: the verify command ALREADY PASSED before Qwen ran, so this "
            "pass is not evidence the task was done. Either the task was a no-op, its "
            "premise was false, or the gate does not actually test the change. Check "
            "CHANGED above: if nothing changed, Qwen may have correctly declined -- read "
            "its result. Tighten the gate to test the specific new behavior."
        )

    if guard_on and ctx["pre_sha"]:
        if ctx["pre_clean"]:
            lines.append(
                f"ROLLBACK: git checkout . && git clean -fd   "
                f"(safe -- tree was clean at {ctx['pre_sha']} before this run)"
            )
        else:
            lines.append(
                f"ROLLBACK: unsafe to blanket-revert -- the tree was ALREADY dirty at "
                f"{ctx['pre_sha']} before this run, so Qwen's changes are mixed with "
                f"pre-existing ones. Review the diff and revert selectively."
            )

    # Structured handoff, extracted BEFORE truncation so a long reply can't bury it.
    handoff = parse_handoff(result_text)
    if handoff:
        if handoff.get("HANDOFF"):
            lines.append(f"HANDOFF: {handoff['HANDOFF']}")
        if handoff.get("NEXT"):
            lines.append(f"NEXT: {handoff['NEXT']}")

        # Qwen's own account of what it touched vs what the filesystem says. This is the
        # fib-fabrication failure mode in miniature -- trust the filesystem.
        claimed = handoff.get("FILES", "")
        if guard_on and claimed:
            post_snap = snapshot(cwd)
            actual = {p for p in post_snap if post_snap.get(p) != ctx["pre_status"].get(p)}
            said_none = claimed.strip().lower() in ("none", "no files", "-")
            if said_none and actual:
                lines.append(
                    f"MISREPORT: Qwen claims FILES: none but {len(actual)} file(s) "
                    f"changed on disk. Its account is unreliable -- trust CHANGED above."
                )
            elif not said_none and not actual:
                lines.append(
                    f"MISREPORT: Qwen claims it changed '{claimed}' but nothing changed "
                    f"on disk. It may have described intended work it never did."
                )

    if guard_on and status not in ("error",):
        lines.append(
            f"CONTINUE: to follow up on THIS task with warm context, pass "
            f"session_id=\"{session_id}\" and the same cwd (sessions are cwd-scoped). "
            f"Skips ~17.6k tokens of reload. Do NOT reuse it for an unrelated task -- a "
            f"fresh session re-reads QWEN.md, which is what makes the rules bind, and "
            f"keeps one task's reasoning from contaminating the next."
        )

    if denials:
        names = ", ".join(sorted({d.get("tool_name", "?") for d in denials}))
        lines.append(
            f"DENIALS: {len(denials)} blocked ({names}) -- Qwen may have worked around "
            f"this; treat the result as suspect."
        )

    if last_verify:
        lines.append(f"--- final verify output ---\n{truncate(last_verify, VERIFY_CAP)}")

    if status == "unverified":
        lines.append("NOTE: no verify command -- this is Qwen's unverified claim.")

    lines.append(f"--- qwen result ---\n{truncate(strip_handoff(result_text), RESULT_CAP)}")
    return "\n".join(lines)


def parse_qwen_json(stdout):
    """Return (result_text, denials, session_id) from qwen's -o json output."""
    stdout = (stdout or "").strip()
    if not stdout:
        return None, [], None

    try:
        parsed = json.loads(stdout)
        msgs = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        msgs = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("type") == "result":
            return (
                m.get("result") or "",
                m.get("permission_denials") or [],
                m.get("session_id"),
            )

    sid = next(
        (m.get("session_id") for m in msgs if isinstance(m, dict) and m.get("session_id")),
        None,
    )
    return None, [], sid


def truncate(s, cap):
    s = s or ""
    if len(s) <= cap:
        return s
    return s[:cap] + f"\n... [truncated {len(s) - cap} chars]"


def respond(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    log(f"starting (qwen={QWEN_BIN})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method")
        rid = req.get("id")

        if method == "initialize":
            respond(
                rid,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "qwen-delegate", "version": "0.2.0"},
                },
            )
        elif method == "notifications/initialized":
            continue
        elif method == "ping":
            respond(rid, {})
        elif method == "tools/list":
            respond(rid, {"tools": [TOOL]})
        elif method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name != "qwen_delegate":
                respond(rid, error={"code": -32602, "message": f"unknown tool: {name}"})
                continue
            try:
                text = run_qwen(args)
                respond(rid, {"content": [{"type": "text", "text": text}]})
            except Exception as e:
                log(f"error: {e!r}")
                respond(
                    rid,
                    {
                        "content": [{"type": "text", "text": f"STATUS: error\n{e!r}"}],
                        "isError": True,
                    },
                )
        elif rid is not None:
            respond(rid, error={"code": -32601, "message": f"unknown method: {method}"})


if __name__ == "__main__":
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
