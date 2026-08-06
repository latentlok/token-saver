#!/usr/bin/env python3
"""
Read-only queries, behavior frozen by specs/queries_spec.py.
"""

import os

import qd.bootstrap
import qd.invoke
import qd.jsonschema
import qd.profiles
import qd.runlog
import qd.verdict

DEFAULT_TIMEOUT = 900
MAX_TIMEOUT = 7200

# The answer IS the deliverable here, unlike a delegate receipt where the diff is
# the deliverable and the text is commentary -- so this cap is generous and exists
# only to stop a runaway. It was 3000 (+1500 at the call site, so 4500 effective),
# which silently cut four of six measured queries mid-sentence: a `map` of any real
# repo, or any answer with more than a handful of findings, does not fit in 4500
# chars. A truncated answer reads as a complete one -- the caller sees a fluent
# paragraph and no reason to doubt it.
RESULT_CAP = 50000

# Freeform read-only answer -- the default query format. Grounded (cite file:symbol),
# with a VERIFY section, because Qwen's conclusions are often plausibly wrong.
ANSWER_SUFFIX = """

---
You are in read-only mode. Do NOT write, edit, or propose code changes -- only read
(glob/grep/targeted reads) and answer. Do not read whole files "to be thorough"; stay
small.

Answer the question directly and concretely. Cite evidence by NAME and file
(`validate_token in auth/tokens.py`), never by line number -- you do not track line
numbers reliably, and a name can be grepped while a guessed line cannot. If you are
inferring rather than confirming, say so. Finish with:

VERIFY: <the specific claims a decision should not rest on until checked against source,
each with the symbol/path to grep. If you did not actually read something, say so here
rather than asserting you confirmed it.>
"""

INVESTIGATE_SUFFIX = """

---
You are in read-only investigation mode. Do NOT propose changes or write code. Use
glob/grep/targeted reads; do not read whole files "to be thorough" -- stay small.

Return ONLY this structure, nothing else:

MAP:
- <path> — <one line: what it is / what it exposes>
  (one bullet per relevant file; skip irrelevant files)

KEY SYMBOLS:
- <name> in <path> — <what it does>
  (the functions/classes/types that matter for the question; omit if not applicable)

CONNECTIONS:
- <how the relevant pieces call/depend on each other; the seams that matter>

ANSWER: <2-4 sentences directly answering the question you were asked>

VERIFY (load-bearing claims the caller must confirm against source before relying on
them — be honest about what you inferred vs. read directly):
- <claim> — <the symbol/path to grep for to check it>

Reference symbols by NAME and file (e.g. `dasherize in inflection/__init__.py`), never
by line number. You do not track line numbers reliably and a wrong number is worse than
none — a name can be grepped, a guessed line cannot. If you did not actually read
something, say so under VERIFY instead of asserting you confirmed it.
"""


def run_query(args):
    question = args["question"]
    cwd = args["cwd"]
    focus = args.get("focus")
    fmt = args.get("format") or "answer"
    session_id = args.get("session_id")
    timeout = max(30, min(MAX_TIMEOUT, int(args.get("timeout_sec") or DEFAULT_TIMEOUT)))

    if not os.path.isabs(cwd):
        return f"STATUS: error\ncwd must be an absolute path, got: {cwd}"
    if not os.path.isdir(cwd):
        return f"STATUS: error\ncwd does not exist or is not a directory: {cwd}"

    rules_state, rules_path = qd.bootstrap.worker_rules_status(cwd)

    profile = qd.profiles.resolve(cwd, args.get("executor"))

    # U5.1: a query can be asked for a machine-read answer too. There is no
    # retry loop here to spend on a violation, so the check is REPORTED rather
    # than enforced -- the answer is still worth having, and the caller needs
    # to know before it parses it.
    result_schema = args.get("result_schema")
    if not isinstance(result_schema, dict):
        result_schema = None

    # U5.1 accept-time check, same function _preconditions calls in
    # qd/engine.py (that module never gets entered from here, so a keyword
    # list kept twice would drift on the first edit). This surface has no
    # retry loop to spend on a violation and no gate to bounce it off, so a
    # constraint the checker cannot enforce has to stop the call before the
    # executor runs -- otherwise "RESULT: valid (schema)" over an unchecked
    # `minimum` is a fuel gauge reading full because it is disconnected.
    if result_schema is not None:
        schema_text = qd.jsonschema.schema_refusal(result_schema)
        if schema_text:
            return f"STATUS: refused\n\n{schema_text}"

    suffix = INVESTIGATE_SUFFIX if fmt == "map" else ANSWER_SUFFIX
    if result_schema is not None:
        suffix += qd.jsonschema.schema_suffix(result_schema)
    verb = "Map this codebase to answer" if fmt == "map" else "Answer this question about the code"
    prompt = f"{verb}.\n\nQUESTION: {question}"
    if focus:
        prompt += f"\n\nFOCUS your reading on: {focus}"

    text, denials, sid, err, meta = qd.invoke.run_executor(
        profile, prompt, cwd, "plan", timeout, session_id, suffix=suffix
    )

    # Step 8, cheap half: a query is ONE executor call, and until now the run
    # log recorded its totals without recording it as a CALL. That mattered
    # once the log became heterogeneous -- a delegation's record distinguishes
    # its challenge pass from its build attempts, and a query sat outside that
    # vocabulary entirely, so "what did we spend on queries" could not be asked
    # in the same shape as every other question about the log.
    #
    # NOT the full fold of query into the run pipeline (DESIGN §8.1): this is
    # the whole user-visible benefit of that step at a fraction of its risk.
    calls = qd.runlog.CallLog()
    calls.record("query", meta, session=sid, err=err)

    def _log_query(status, verdict):
        stats = meta.get("stats") or {}
        peak = meta.get("peak", 0)
        tokens = stats.get("tokens") or {}
        tokens_in = (tokens or {}).get("prompt", 0)
        tokens_out = (tokens or {}).get("completion", 0)
        cost = qd.profiles.cost_usd(profile, tokens_in, tokens_out)
        qd.runlog.write_runlog(cwd, qd.runlog.leverage_record(
            "qwen_query", cwd, status, verdict,
            stats, peak,
            executor=profile["name"],
            cost_usd=cost,
            extra={
                "session": sid,
                "approval_mode": "plan",
                "format": fmt,
                "question": qd.runlog.digest(question),
                "focus": focus or None,
                "resumed": bool(session_id),
                **calls.as_record(profile),
            },
        ))

    # Errors are logged too: a timed-out or unparseable query still burned the tokens.
    if err:
        verdict = f"STATUS: error\n{err}"
        _log_query("error", verdict)
        return verdict

    lines = ["STATUS: ok", f"SESSION: {sid or 'unknown'}"]

    # Warn, do not refuse: see unconfigured_notice().
    if rules_state != "ok":
        lines.append(qd.bootstrap.unconfigured_notice(cwd, rules_state, rules_path))

    # Compaction is the failure mode for a read that got too big: past it Qwen fabricates.
    peak = meta.get("peak", 0)
    win = qd.invoke.context_window()
    if peak and win:
        _, auto_at = qd.invoke.compaction_thresholds(win)
        pct = 100.0 * peak / win
        if peak >= auto_at:
            lines.append(
                f"CONTEXT: peak {peak:,}/{win:,} ({pct:.0f}%) -- COMPACTION LIKELY FIRED. "
                f"This read was too big; parts of the answer may be fabricated. Re-run with "
                f"a tighter `focus`, or split into smaller questions."
            )
        elif pct >= 60:
            lines.append(
                f"CONTEXT: peak {peak:,}/{win:,} ({pct:.0f}%) -- getting large; narrow "
                f"`focus` if you need more depth."
            )
        else:
            lines.append(f"CONTEXT: peak {peak:,}/{win:,} ({pct:.0f}%) -- safe, well under compaction")
    elif peak:
        lines.append(f"CONTEXT: peak {peak:,} tokens")

    st = meta.get("stats") or {}
    if st.get("tools"):
        lines.append(f"READS: {st['tools']} tool call(s), {st.get('ms', 0) / 1000:.0f}s")

    # Above the answer, not below it: a caller that parses the block needs to
    # know it is unparseable BEFORE it reads past this line.
    if result_schema is not None:
        value, _, err = qd.jsonschema.last_json_block(text)
        errors = [err] if err else qd.jsonschema.validate(value, result_schema)
        # The same constant the delegate receipt stamps and qd/server.py's
        # chain reads back. A third hand-written copy of the string is how the
        # constant stops being one: a query receipt is not carried between
        # links today, but the wording is now load-bearing somewhere, and
        # "somewhere else still says it a third way" is the drift the constant
        # exists to make impossible.
        lines.append(f"RESULT: schema INVALID — {errors[0]}" if errors
                     else qd.verdict.RESULT_VALID_LINE)

    label = "map" if fmt == "map" else "answer"
    lines.append(f"--- {label} ---\n{qd.invoke.truncate(text, RESULT_CAP)}")
    verdict = "\n".join(lines)
    _log_query("ok", verdict)
    return verdict


def run_investigate(args):
    """Back-compat alias: the codebase map is now qwen_query(format='map')."""
    a = dict(args)
    a["format"] = "map"
    return run_query(a)
