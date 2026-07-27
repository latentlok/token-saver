#!/usr/bin/env python3
"""
Executor invocation, behavior frozen by specs/invoke_spec.py.
"""

import json
import os
import subprocess
import tempfile
import threading
import time

# ---------- compaction markers ----------
COMPACT_DIR = os.environ.get("QCOMPACT_DIR") or os.path.expanduser(
    "~/.qwen-delegate/compacted"
)

# Fraction of the context window at which the executor may auto-compact. 1.0 = "as
# late as this executor permits", which is the only setting consistent with the
# refuse policy: a compaction ends the run, so every token before the trigger is
# work that gets to happen. Upstream's default is 0.85.
#
# This number is rarely what decides the trigger. COMPACTION_RESERVE below is
# subtracted from the window unconditionally, and on any window under ~220k it is
# the binding term -- see compaction_ceiling().
COMPACTION_PCT = 1.0

# SUMMARY_RESERVE (20,000: room to generate the summary) + AUTOCOMPACT_BUFFER
# (13,000), both hardcoded in qwen. No setting reaches them, so no configuration
# can move a compaction later than `window - 33,000`. Raising the *window* is the
# only lever -- and only if the endpoint really serves it.
COMPACTION_RESERVE = 33_000
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


def _read_log(path):
    """Order-preserving, deduped lines from one QGATE_* log, or []. Shared by
    the deny log and C10's allow-side pair -- three readers that differ only in
    which file they open is three places to fix a bug in."""
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


def stream_argv(argv):
    """Switch the executor's output format to the streaming one.

    `-o json` batches every record and prints them at exit, so a run is opaque
    until it ends -- which is why a runaway loop could only ever be reported
    afterwards. `-o stream-json` emits the SAME records (qwen's
    StreamJsonOutputAdapter writes one JSON.stringify(message) per line, built
    by the same buildResultMessage as the batch adapter), just as they happen.

    Deliberately NOT --include-partial-messages: that adds per-token
    `stream_event` records we would only have to filter back out.

    An argv we do not recognise is returned unchanged -- the run then behaves
    exactly as before, batched. Losing incremental delivery is a degradation;
    refusing to run is not an option.
    """
    argv = list(argv)
    for i, a in enumerate(argv[:-1]):
        if a in ("-o", "--output-format") and argv[i + 1] == "json":
            argv[i + 1] = "stream-json"
            break
    return argv


def _terminate(proc):
    """Stop the executor: ask, then insist. Never raises."""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        proc.kill()
    except Exception:
        pass


# How long COMPLETE silence may last before a run is called wedged.
#
# Without --include-partial-messages a record arrives per MESSAGE, not per
# token, so a single long assistant turn is one record emitted at the very end.
# The silence that implies is therefore `max output tokens / decode rate` --
# entirely legitimate, and the thing a naive threshold kills.
#
# So the knob is DECODE RATE, not seconds: it is the number that actually
# changes between setups, and the one an operator knows. A 27B at ~70 tok/s
# needs ~1,830s of headroom for a 128k generation; the same generation on a
# 120B at ~17 tok/s needs ~7,530s. A fixed seconds default cannot serve both,
# and the conservative floor below is deliberately slow so an unconfigured
# machine never kills working work.
DECODE_TPS_FLOOR = 15
STALL_MARGIN = 1.5
STALL_FALLBACK_OUTPUT = 32_000     # when maxTokens is not declared anywhere


def max_output_tokens():
    """Configured max output tokens for the active model, or None."""
    try:
        s = json.load(open(os.path.expanduser("~/.qwen/settings.json")))
        model = (s.get("model") or {}).get("name")
        for provs in (s.get("modelProviders") or {}).values():
            for p in provs if isinstance(provs, list) else []:
                if p.get("id") == model or p.get("name") == model:
                    mt = (p.get("generationConfig") or {}).get("maxTokens")
                    if mt:
                        return int(mt)
    except Exception:
        pass
    return None


def stall_seconds(cwd=None, config=None):
    """Silence budget in seconds: explicit override, else derived from rate.

    Precedence: `stall_seconds` in config (an absolute answer, for anyone who
    would rather state it directly) > `decode_tps` in config, applied to the
    declared maxTokens > DECODE_TPS_FLOOR.
    """
    cfg = config or {}
    explicit = cfg.get("stall_seconds")
    if explicit:
        return max(30, int(explicit))
    tps = cfg.get("decode_tps") or DECODE_TPS_FLOOR
    try:
        tps = max(1.0, float(tps))
    except (TypeError, ValueError):
        tps = DECODE_TPS_FLOOR
    out = max_output_tokens() or STALL_FALLBACK_OUTPUT
    return max(30, int(out / tps * STALL_MARGIN))


def _stream_process(argv, cwd, env, timeout, on_line=None, stall_after=None):
    """Run argv, draining both pipes concurrently. Never raises.

    Returns (stdout, stderr, returncode, err, aborted_reason).

    BOTH pipes get their own thread on purpose. Reading stdout to completion
    while stderr fills its buffer deadlocks the child -- the classic Popen
    mistake, and the reason this is not a one-line swap for subprocess.run.
    The deadlock is load-dependent (it needs ~64KB to accumulate in the pipe
    nobody is reading), so a stub that emits a few hundred bytes proves
    nothing about it.

    `on_line` sees each parsed record as it arrives and may return a string to
    stop the run early; that string comes back as `aborted_reason`.

    `stall_after` is a watchdog, NOT a callback: silence produces no lines, so
    nothing on_line can observe absence. A separate thread compares the clock
    against the last line seen.
    """
    out, err = [], []
    abort = {"reason": None}
    last_line = [None]                # monotonic stamp of the most recent line
    finished = threading.Event()      # set the moment the child is reaped

    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1,
        )
    except FileNotFoundError:
        return None, "", None, f"binary not found ({argv[0]})", None

    def pump_out():
        try:
            for line in proc.stdout:
                out.append(line)
                last_line[0] = time.monotonic()
                if on_line is None:
                    continue
                probe = line.strip()
                if not probe.startswith("{"):
                    continue
                try:
                    record = json.loads(probe)
                except json.JSONDecodeError:
                    continue          # a partial or non-record line, not fatal
                try:
                    reason = on_line(record)
                except Exception:
                    reason = None     # an observer must not kill the run it observes
                if reason and abort["reason"] is None:
                    abort["reason"] = str(reason)
                    _terminate(proc)
        except Exception:
            pass

    def pump_err():
        try:
            for line in proc.stderr:
                err.append(line)
        except Exception:
            pass

    def watchdog():
        # Silence is measured from the last line OR from launch, so a run that
        # never emits anything is caught too -- that is the wedged-at-startup
        # case, and waiting out timeout_sec for it is the whole complaint.
        #
        # Waits on the finished Event rather than sleeping between poll()s: a
        # sleeping watchdog delays teardown by up to its interval on EVERY run,
        # which on a stall budget measured in hours is a multi-second tax on
        # work that never went near the limit.
        started = time.monotonic()
        interval = min(5.0, max(0.05, stall_after / 20.0))
        while not finished.wait(interval):
            quiet = time.monotonic() - (last_line[0] or started)
            if quiet >= stall_after:
                if abort["reason"] is None:
                    abort["reason"] = (
                        f"no output for {int(quiet)}s (stall limit {stall_after}s)")
                    _terminate(proc)
                return

    threads = [threading.Thread(target=pump_out, daemon=True),
               threading.Thread(target=pump_err, daemon=True)]
    for t in threads:
        t.start()
    if stall_after:
        threading.Thread(target=watchdog, daemon=True).start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate(proc)

    finished.set()                       # release the watchdog immediately

    # Bounded join: a reader blocked on a pipe that never closes must not hang
    # the server. Whatever it had already appended is still in `out`/`err`.
    for t in threads:
        t.join(timeout=10)
    for pipe in (proc.stdout, proc.stderr):
        try:
            pipe.close()
        except Exception:
            pass

    if timed_out:
        return "".join(out), "".join(err), proc.returncode, \
            f"timed out after {timeout}s", abort["reason"]
    return "".join(out), "".join(err), proc.returncode, None, abort["reason"]


def run_executor(profile, task, cwd, mode, timeout=None, session_id=None,
                 verify=None, shell_allow=None, suffix="", compaction_policy=None,
                 on_line=None, stall_after=None, observe_hook=False):
    """Invoke the Qwen Code executor and parse the result.

    Return (text, denials, session_id, err, meta).

    `observe_hook` (U1.4, off by default) installs the PreToolUse hook outside
    scoped mode purely for its attribution logs -- same yolo-underneath trick
    scoped already uses. Absent, argv and env are byte-identical to a run that
    never heard of it.
    """
    from qd.profiles import render_argv

    gated = mode == "scoped" or observe_hook
    real_mode = "yolo" if gated else mode
    argv = render_argv(profile, task + suffix, real_mode, session_id)
    # Stream only when someone is watching. Measured: the streaming adapter's
    # result record carries NO `stats` field -- the batch adapter attaches it,
    # the stream one does not -- so streaming costs the tool and line counts.
    # Paying that unconditionally buys nothing for a caller with no on_line;
    # paying it to gain mid-run intervention is the trade this exists for.
    if on_line is not None and profile.get("stream", True):
        argv = stream_argv(argv)
    # A limit can only act on records it receives, and records only arrive
    # incrementally in streaming mode. If a caller attached one and the argv
    # could not be switched, the limit is INERT -- it will never fire, and
    # nothing about the run would say so. Report it rather than let a guard
    # that is doing nothing read as a guard that found nothing.
    limits_inert = on_line is not None and not any("stream-json" in a for a in argv)

    # Build temp settings
    td = tempfile.mkdtemp()
    denylog = writelog = allowlog = None
    hooks_dict = {"hooks": compact_hooks()}
    if gated:
        denylog = os.path.join(td, "denied.log")
        writelog = os.path.join(td, "writes.log")
        allowlog = os.path.join(td, "allowed.log")
        hooks_dict["hooks"]["PreToolUse"] = [
            {"matcher": ".*", "hooks": [
                {"type": "command", "command": f"python3 {HOOK_SCRIPT}"},
            ]},
        ]
    # Where auto-compaction fires, per run. Pushed as late as the setting allows:
    # under the refuse policy a compaction ENDS the run, so every token before the
    # trigger is work that gets to happen, and every token after it is work nobody
    # would have trusted anyway. A profile may override with compaction_threshold.
    threshold = profile.get("compaction_threshold", COMPACTION_PCT)
    target = profile.get("compaction_at")
    if target:
        # An absolute token target beats a fraction: it is what a caller actually
        # means. Unreachable targets are not clamped into silence -- they resolve
        # to the latest the reserve allows, and doctor reports the gap.
        pct, _ = compaction_pct_for(context_window(), target)
        threshold = pct
    if threshold:
        hooks_dict.setdefault("context", {})["autoCompactThreshold"] = float(threshold)

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
    env["QCOMPACT_POLICY"] = compaction_policy or "reinject"
    env["QWEN_CODE_SYSTEM_SETTINGS_PATH"] = sys_settings
    env.update(profile["env"])
    if gated:
        env["QGATE_CWD"] = os.path.realpath(cwd)
        env["QGATE_VERIFY"] = verify or ""
        env["QGATE_DENYLOG"] = denylog
        env["QGATE_WRITELOG"] = writelog
        env["QGATE_ALLOWLOG"] = allowlog
        env["QGATE_EXTRA"] = json.dumps(shell_allow or [])
        env["QGATE_MODE"] = "scoped" if mode == "scoped" else "autoedit"

    # Resolve timeout
    if timeout is None:
        timeout = profile["defaults"]["timeout"]

    # Run subprocess
    stdout, stderr, rc, run_err, aborted = _stream_process(
        argv, cwd, env, timeout, on_line, stall_after)

    # Read before the tempdir goes: these three files ARE the run's evidence,
    # and _cleanup deletes them.
    blocked = _read_log(denylog)
    writes = _read_log(writelog)
    allowed = _read_log(allowlog)
    _cleanup(td)

    if stdout is None:                       # never started
        return None, [], None, run_err, {}

    # Parsed from the accumulated stream, which carries the same records the
    # batch format emitted -- parse_qwen_json's JSONL path already handles the
    # one-object-per-line shape, so nothing downstream changes.
    text, denials, sid = parse_qwen_json(stdout)
    meta = {"peak": peak_context(stdout), "stats": parse_stats(stdout),
            "blocked": blocked, "writes": writes, "allowed": allowed,
            "limits_inert": limits_inert}

    # A run WE stopped reports why, ahead of whatever the partial output looks
    # like -- the executor did not fail, we cut it short, and a receipt that
    # blamed the worker for our decision would send someone debugging it.
    if aborted:
        return None, denials, sid, f"run stopped: {aborted}", meta
    if run_err:
        return None, denials, sid, run_err, meta
    failure = result_error(stdout)
    if failure:
        return None, denials, sid, failure, meta
    if text is None:
        tail = (stderr or stdout or "").strip()[-800:]
        return None, [], sid, f"unparseable output (exit {rc}): {tail}", meta
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


def compaction_thresholds(window, pct=None):
    """
    Mirrors qwen's computeThresholds(window, pct) (chunks/chunk-NJOFRXTM.js):
      SUMMARY_RESERVE=20000, AUTOCOMPACT_BUFFER=13000, WARN_BUFFER=20000.
    Upstream DEFAULT_PCT is 0.85; we configure COMPACTION_PCT, and this mirror
    defaults to the same constant so the receipt's warnings describe the trigger we
    actually set rather than the one we replaced.

    THE PCT IS NOT THE ONLY GATE. An absolute ceiling of `window - 33,000` applies
    too, and below a ~220k window it binds first -- at 196,608 the trigger sits at
    163,608 (83.2%) whether pct is 0.85 or 0.98. Raising pct only moves anything on
    windows large enough for the proportional term to be the smaller of the two.

    Compaction is LOSSY -- it summarizes history away, which can drop QWEN.md rules
    mid-task. Statelessness normally keeps us near 11%, far below these.
    """
    pct = COMPACTION_PCT if pct is None else pct
    pct = min(1.0, max(0.0, pct))
    ceiling = compaction_ceiling(window)
    proportional = pct * window
    auto = min(proportional, ceiling) if ceiling > 0 else proportional
    return max(0, auto - 20000), auto


def compaction_ceiling(window):
    """The LATEST token count at which auto-compaction can be made to fire.

    `window - COMPACTION_RESERVE`, and no setting reaches past it. This is the
    number to answer "can I hold N tokens before it compacts?" with -- the
    configured pct only matters when it lands below this.
    """
    return max(0, window - COMPACTION_RESERVE)


def compaction_pct_for(window, target):
    """(pct, reachable) to put the trigger at `target` tokens on `window`.

    `reachable` is False when the reserve puts `target` out of range whatever the
    pct -- the caller must say so rather than silently configure a number that
    cannot take effect. The window needed for a given target is
    `target + COMPACTION_RESERVE`, actually served, not merely declared.
    """
    if not window or window <= 0:
        return COMPACTION_PCT, False
    return (min(1.0, float(target) / window),
            float(target) <= compaction_ceiling(window))


def peak_context(stdout):
    """
    Peak prompt tokens across assistant turns == context actually used.

    NOT result.usage.input_tokens: that SUMS every API call in the run, including
    Qwen's internal auto-memory-extractor sub-agent. Measured: result reported 31,317
    while true peak context was 20,285 -- a 50% overstatement.
    """
    best = 0
    for m in records(stdout):
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
    for m in reversed(records(stdout)):
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
        if not st:
            # Streaming result records carry no `stats` (measured against
            # -o stream-json: keys are duration_ms, num_turns, usage,
            # permission_denials, result -- no stats at all). The top-level
            # `usage` survives, and it is the SUM across every API call in the
            # run: the wrong number for peak context, the right one for total
            # prefill work. Recorded as its own source so a reader can tell it
            # from a bySource split -- tools/lines are simply unavailable here,
            # not measured as zero.
            u = m.get("usage") or {}
            tok = {"prompt": int(u.get("input_tokens") or 0),
                   "completion": int(u.get("output_tokens") or 0),
                   "total": 0, "cached": 0, "thoughts": 0}
            if tok["prompt"] or tok["completion"]:
                tok["total"] = tok["prompt"] + tok["completion"]
                out["token_source"] = "usage"
                tok_add(out["tokens"], tok)
                tok_add(out["tokens_main"], tok)
            break

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


def compaction_counts(session_id):
    """(completed, attempted) marker counts for a session — an absolute reading.

    `attempted` counts PreCompact, which fires whether or not the block took, so
    the refuse policy holds even on an executor that ignores the block. Compare a
    snapshot taken before an attempt with one taken after: that attributes the
    event to THAT attempt without any acked-counter bookkeeping, and works for a
    fresh session (no marker file, so (0, 0)) exactly as well as a resumed one.
    """
    if not session_id:
        return 0, 0
    try:
        with open(os.path.join(COMPACT_DIR, f"{session_id}.json")) as f:
            state = json.load(f)
        return (len(state.get("events") or []), len(state.get("pending") or []))
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
    """Return (result_text, denials, session_id) from a run's output."""
    msgs = records(stdout)
    if not msgs:
        return None, [], None

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


# Truncation is a CLIENT-side cap, so it is invisible in the endpoint's config:
# qwen-code always sends max_tokens, defaulting to 32k for a model name it does
# not recognise (its normalize() keeps only the part after ":", so an Ollama tag
# like "qwen3.6:27b-agent-q8-maxctx" reads as "27b-agent-q8-maxctx" and matches no
# known-limits pattern). Thinking tokens count against that cap. When the cut
# lands inside a tool call the call is rejected outright, so the run ends with
# no result -- which used to reach the caller as an empty success.
_TRUNCATION_HINT = (
    "The executor hit its OUTPUT TOKEN cap, not a server-side limit -- "
    "qwen-code sends max_tokens itself (32k default for an unrecognised model "
    "name), and thinking tokens count against it. This is an executor/endpoint "
    "setting, NOT a defect in this repo: raise it with QWEN_CODE_MAX_OUTPUT_TOKENS "
    "or `generationConfig.maxTokens` in ~/.qwen/settings.json, or ask for less "
    "output per turn. Report this and move on -- do not debug the plugin."
)


def result_error(stdout):
    """qwen's OWN failure report from the last result record, or None.

    An error record carries `is_error` + `error.message` and NO `result` field,
    so reading it like a success yields "" -- a failed run then arrived as a
    green receipt with an empty answer, and the only honest account of what
    went wrong (truncation, provider error) was dropped on the floor.
    """
    for m in reversed(_result_records(stdout)):
        if not m.get("is_error"):
            return None
        msg = ((m.get("error") or {}).get("message")
               or m.get("subtype") or "unknown error")
        msg = str(msg).strip()[:800]
        out = f"executor reported failure: {msg}"
        low = msg.lower()
        if "truncat" in low or "max_tokens" in low or "max tokens" in low:
            out += f"\n{_TRUNCATION_HINT}"
        return out
    return None


def records(stdout):
    """Every record in a run's stdout, whichever output format produced it.

    THE one parse path. `-o json` emits a single array at exit; `-o stream-json`
    emits the same records one JSON object per line. Every reader goes through
    here, because the alternative is what this replaced: parse_qwen_json had a
    JSONL fallback and peak_context/parse_stats did not, so a streamed run
    parsed to a correct answer with zeroed telemetry -- no error, just a receipt
    quietly reporting 0 context and 0 tokens.
    """
    stdout = (stdout or "").strip()
    if not stdout:
        return []
    try:
        parsed = json.loads(stdout)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        pass
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _result_records(stdout):
    """Every `type: "result"` record in a run's stdout, in order."""
    return [m for m in records(stdout)
            if isinstance(m, dict) and m.get("type") == "result"]


def truncate(s, cap):
    s = s or ""
    if len(s) <= cap:
        return s
    return s[:cap] + f"\n... [truncated {len(s) - cap} chars]"
