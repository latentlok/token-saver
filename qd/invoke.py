#!/usr/bin/env python3
"""
Executor invocation, behavior frozen by specs/invoke_spec.py.
"""

import json
import os
import subprocess
import tempfile

# ---------- compaction markers ----------
COMPACT_DIR = os.environ.get("QCOMPACT_DIR") or os.path.expanduser(
    "~/.qwen-delegate/compacted"
)
HOOK_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "scoped_hook.py")
HOOK_COMPACT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "compact_hook.py")


def compact_hooks():
    """The PreCompact/PostCompact hook block. Needed in EVERY mode -- any session can be
    compacted, and a missed marker means resuming a session whose task was summarised
    away."""
    entry = [{"hooks": [{"type": "command", "command": f"python3 {HOOK_COMPACT}"}]}]
    return {"PreCompact": entry, "PostCompact": entry}


def _read_denylog(path):
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path) as f:
            seen, out = set(), []
            for line in f:
                line = line.strip()
                if line and line not in seen:
                    seen.add(line)
                    out.append(line)
            return out
    except Exception:
        return []


def _cleanup(td):
    if td and os.path.isdir(td):
        try:
            for root, _, files in os.walk(td, topdown=False):
                for fn in files:
                    os.remove(os.path.join(root, fn))
            os.rmdir(td)
        except Exception:
            pass


def run_executor(profile, task, cwd, mode, timeout=None, session_id=None,
                 verify=None, shell_allow=None, suffix=""):
    """Invoke the Qwen Code executor and parse the result.

    Return (text, denials, session_id, err, meta).
    """
    from qd.profiles import render_argv

    real_mode = "yolo" if mode == "scoped" else mode
    argv = render_argv(profile, task + suffix, real_mode, session_id)

    # Build temp settings
    td = tempfile.mkdtemp()
    denylog = None
    hooks_dict = {"hooks": compact_hooks()}
    if mode == "scoped":
        denylog = os.path.join(td, "denied.log")
        hooks_dict["hooks"]["PreToolUse"] = [
            {"matcher": ".*", "hooks": [
                {"type": "command", "command": f"python3 {HOOK_SCRIPT}"},
            ]},
        ]
    if profile["settings_overlay"] is not None:
        for key, value in profile["settings_overlay"].items():
            hooks_dict[key] = value

    sys_settings = os.path.join(td, "settings.json")
    with open(sys_settings, "w") as f:
        json.dump(hooks_dict, f)

    # Build environment
    env = dict(os.environ)
    env["QWEN_CODE_SUPPRESS_YOLO_WARNING"] = "1"
    env["QCOMPACT_DIR"] = COMPACT_DIR
    env["QWEN_CODE_SYSTEM_SETTINGS_PATH"] = sys_settings
    env.update(profile["env"])
    if mode == "scoped":
        env["QGATE_CWD"] = os.path.realpath(cwd)
        env["QGATE_VERIFY"] = verify or ""
        env["QGATE_DENYLOG"] = denylog
        env["QGATE_EXTRA"] = json.dumps(shell_allow or [])

    # Resolve timeout
    if timeout is None:
        timeout = profile["defaults"]["timeout"]

    # Run subprocess
    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _cleanup(td)
        return None, [], None, f"timed out after {timeout}s", {}
    except FileNotFoundError:
        _cleanup(td)
        return None, [], None, f"binary not found ({argv[0]})", {}

    blocked = _read_denylog(denylog) if denylog else []
    _cleanup(td)

    text, denials, sid = parse_qwen_json(proc.stdout)
    meta = {"peak": peak_context(proc.stdout), "stats": parse_stats(proc.stdout),
            "blocked": blocked}
    if text is None:
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        return None, [], sid, f"unparseable output (exit {proc.returncode}): {tail}", meta
    return text, denials, sid, None, meta


def context_window():
    """Configured context window for the active model, or None."""
    try:
        s = json.load(open(os.path.expanduser("~/.qwen/settings.json")))
        model = (s.get("model") or {}).get("name")
        for provs in (s.get("modelProviders") or {}).values():
            for p in provs if isinstance(provs, list) else []:
                if p.get("id") == model or p.get("name") == model:
                    cw = (p.get("generationConfig") or {}).get("contextWindowSize")
                    if cw:
                        return int(cw)
    except Exception:
        pass
    return None


def compaction_thresholds(window):
    """
    Mirrors qwen's computeThresholds() (chunks/chunk-NJOFRXTM.js):
      DEFAULT_PCT=0.85, SUMMARY_RESERVE=20000, AUTOCOMPACT_BUFFER=13000, WARN_BUFFER=20000
    Compaction is LOSSY -- it summarizes history away, which can drop QWEN.md rules
    mid-task. Statelessness normally keeps us near 11%, far below these.
    """
    effective = max(0, window - 20000)
    ceiling = effective - 13000
    auto = min(0.85 * window, ceiling) if ceiling > 0 else 0.85 * window
    return max(0, auto - 20000), auto


def peak_context(stdout):
    """
    Peak prompt tokens across assistant turns == context actually used.

    NOT result.usage.input_tokens: that SUMS every API call in the run, including
    Qwen's internal auto-memory-extractor sub-agent. Measured: result reported 31,317
    while true peak context was 20,285 -- a 50% overstatement.
    """
    best = 0
    try:
        parsed = json.loads((stdout or "").strip())
        msgs = parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        return 0
    for m in msgs:
        if not isinstance(m, dict) or m.get("type") != "assistant":
            continue
        u = (m.get("message") or {}).get("usage") or {}
        best = max(best, int(u.get("input_tokens") or 0))
    return best


def tok_zero():
    return {"prompt": 0, "completion": 0, "total": 0, "cached": 0, "thoughts": 0}


def tok_add(dst, src):
    for k in dst:
        dst[k] += int(src.get(k) or 0)
    return dst


def accum_stats(cum, st):
    """
    Sum one attempt's telemetry into the run total.

    ctx["meta"] holds only the LAST attempt, so a 3-attempt run costs roughly 3x what a
    last-attempt reading reports. Cost accounting has to see the whole run: the iterate
    loop is precisely where free tokens get spent.
    """
    st = st or {}
    for k in ("tokens", "tokens_main", "tokens_overhead"):
        tok_add(cum[k], st.get(k) or {})
    for k in ("ms", "turns", "tools", "tool_fail", "api_errors",
              "lines_added", "lines_removed"):
        cum[k] = (cum.get(k) or 0) + (st.get(k) or 0)
    for k in ("tool_names", "models"):
        cum[k] = sorted(set(cum.get(k) or []) | set(st.get(k) or []))
    # Worst case wins: one blended attempt makes the whole run's main/overhead split
    # unreliable, so the run must not claim a clean bySource provenance.
    seen = {cum.get("token_source", "none"), st.get("token_source", "none")}
    cum["token_source"] = ("blended" if "blended" in seen
                           else "bySource" if "bySource" in seen else "none")
    cum["attempts"] = (cum.get("attempts") or 0) + 1
    return cum


def cum_zero():
    return {"tokens": tok_zero(), "tokens_main": tok_zero(),
            "tokens_overhead": tok_zero(), "ms": 0, "turns": 0, "tools": 0,
            "tool_fail": 0, "api_errors": 0, "lines_added": 0, "lines_removed": 0,
            "tool_names": [], "models": [], "attempts": 0, "token_source": "none"}


def norm_tokens(t):
    """
    Normalise one `tokens` object to {prompt, completion, total, cached, thoughts}.

    `-o json` emits the INTERNAL camelCase shape, where the output count is named
    `candidates` (verified against a live run, not the bundled schema -- the snake_case
    `completion` spelling in the CLI source belongs to the statusLine hook, a different
    serializer). Both spellings are accepted so this survives an upstream rename.
    """
    t = t or {}
    return {
        "prompt": int(t.get("prompt") or 0),
        "completion": int(t.get("candidates") or t.get("completion") or 0),
        "total": int(t.get("total") or 0),
        "cached": int(t.get("cached") or 0),
        "thoughts": int(t.get("thoughts") or 0),
    }


def parse_stats(stdout):
    """
    Pull the run telemetry out of result.stats.

    tools.totalFail is the valuable one: a run where Qwen's tool calls failed currently
    reports identically to one where they all succeeded. Same class as permission_denials
    -- silent failure dressed as success.

    Tokens are split main vs overhead via stats.models[*].bySource. Qwen runs an internal
    `managed-auto-memory-extractor` sub-agent whose spend is real but is not task work --
    measured at 10,428 of 29,421 prompt tokens (35%) on a one-word prompt. Reporting a
    single blended total would overstate what the task itself cost.
    """
    out = {"tools": 0, "tool_fail": 0, "tool_names": [], "ms": 0, "turns": 0,
           "api_errors": 0, "lines_added": 0, "lines_removed": 0,
           "tokens": tok_zero(), "tokens_main": tok_zero(),
           "tokens_overhead": tok_zero(), "models": [], "token_source": "none"}
    try:
        parsed = json.loads((stdout or "").strip())
        msgs = parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        return out
    for m in reversed(msgs):
        if not isinstance(m, dict) or m.get("type") != "result":
            continue
        out["ms"] = m.get("duration_ms") or 0
        out["turns"] = m.get("num_turns") or 0
        st = m.get("stats") or {}
        t = st.get("tools") or {}
        out["tools"] = t.get("totalCalls") or 0
        out["tool_fail"] = t.get("totalFail") or 0
        out["tool_names"] = sorted((t.get("byName") or {}).keys())
        f = st.get("files") or {}
        out["lines_added"] = f.get("totalLinesAdded") or 0
        out["lines_removed"] = f.get("totalLinesRemoved") or 0
        for mid, mv in (st.get("models") or {}).items():
            out["api_errors"] += ((mv.get("api") or {}).get("totalErrors") or 0)
            out["models"].append(mid)
            tok_add(out["tokens"], norm_tokens(mv.get("tokens")))
            by = mv.get("bySource") or {}
            if by:
                out["token_source"] = "bySource"
                for src, sv in by.items():
                    bucket = "tokens_main" if src == "main" else "tokens_overhead"
                    tok_add(out[bucket], norm_tokens(sv.get("tokens")))
            else:
                # No per-source breakdown: attribute everything to the task rather than
                # silently dropping it. Overstates main, never invents tokens.
                #
                # Recorded, because otherwise this is indistinguishable from a real
                # zero-overhead run -- a metric reading 0 when it actually measured
                # nothing is the silent-failure class this system exists to catch.
                out["token_source"] = "blended"
                tok_add(out["tokens_main"], norm_tokens(mv.get("tokens")))
        break
    return out


def compaction_state(session_id):
    """(events_seen, acked). (0, 0) when there is no marker -- never compacted."""
    if not session_id:
        return 0, 0
    try:
        with open(os.path.join(COMPACT_DIR, f"{session_id}.json")) as f:
            state = json.load(f)
        return len(state.get("events") or []), int(state.get("acked") or 0)
    except Exception:
        return 0, 0


def was_compacted_since_ack(session_id):
    seen, acked = compaction_state(session_id)
    return seen > acked


def ack_compaction(session_id):
    """Mark every compaction seen so far as handled. Idempotent."""
    if not session_id:
        return
    path = os.path.join(COMPACT_DIR, f"{session_id}.json")
    try:
        with open(path) as f:
            state = json.load(f)
        state["acked"] = len(state.get("events") or [])
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except Exception:
        pass


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
