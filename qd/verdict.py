"""Receipt rendering, crane-equal for v1 inputs, behavior frozen by specs/verdict_spec.py."""

from qd.gittree import (
    blast_radius, blast_lines, new_public_symbols, committed_during_run,
    head_sha, snapshot,
)
from qd.invoke import context_window, compaction_thresholds, cum_zero, compaction_state, truncate
from qd.runlog import (
    write_runlog, leverage_record, digest, ledger_summary, brief_summary,
)
from qd import limits, refs
from qd.features import detectors
from qd.features.detectors import find as _finding
from qd.surface.receipt import Block


RESULT_CAP = 3000
VERIFY_CAP = 2500

# Tool names that cannot write. A denial of one of these has a legal
# substitute the worker can reach on its own, so it costs time rather than
# trust. Mirrors scoped_hook.READ_ONLY, kept here rather than imported because
# the hook is a standalone script the server never loads into its own process.
READ_ONLY_TOOLS = frozenset((
    "read_file", "read_many_files", "glob", "grep", "grep_search",
    "search_file_content", "list_directory", "ls", "find_files",
    "read_folder", "list_dir",
))

# A run past this many INPUT tokens is reported as heavy. Set where a 27B-class
# local model spends roughly an hour of GPU: enough headroom that ordinary work
# never trips it, low enough that a runaway agentic loop does.
BURN_WARN_TOKENS = 3_000_000


def burn_line(ctx):
    """One BURN: line — tokens, calls, per-call context, and the time domain.

    On a local endpoint COST is $0.0000 whatever happens, so a run that made 218
    calls averaging 87k of context looked exactly like one that made four. The
    spend is real (GPU minutes, and the queue behind it) and nothing in the
    receipt showed it. Time is appended because time IS the local cost axis.
    When the endpoint reports cached prompt tokens, input is NOT re-paid in
    full each turn -- HEAVY then binds on the un-cached remainder, and the line
    says why (a raw multi-million input count on a caching endpoint alarmed
    callers into diagnosing perfectly healthy runs).
    """
    cum = ctx.get("cum") or {}
    tokens = cum.get("tokens") or {}
    tin = int(tokens.get("prompt") or 0)
    tout = int(tokens.get("completion") or 0)
    cached = int(tokens.get("cached") or 0)
    if not tin and not tout:
        return None
    turns = int(cum.get("turns") or 0)
    parts = [f"BURN: {tin:,} in / {tout:,} out"]
    if turns:
        parts.append(f"{turns} calls, ~{tin // turns:,} ctx/call")
    ms = int(cum.get("ms") or 0)
    if ms >= 90_000:
        parts.append(f"{ms / 60000.0:.1f} min GPU")
    elif ms:
        parts.append(f"{ms / 1000.0:.0f}s GPU")
    line = ", ".join(parts)
    fresh = max(0, tin - cached)
    if fresh >= BURN_WARN_TOKENS:
        line += (" -- HEAVY. Free in money, not in time; a loop this long also "
                 "risks compaction. Split the task into smaller delegations.")
    elif cached and tin >= BURN_WARN_TOKENS:
        line += f" ({cached:,} cached -- prefill largely reused, not re-paid)"
    return line

HANDOFF_SUFFIX = """

---
Finish your reply with exactly these three lines, after any prose:

HANDOFF: <one line: what state the work is in now>
FILES: <comma-separated paths you created or modified, or the word: none>
NEXT: <one line: what a follow-up would know, or the word: nothing>

Keep each line under 120 characters. This is a machine-read handoff, not prose.
"""


FINDINGS_SUFFIX = """

---
Add one more line, after those:

FINDINGS: <what you found -- one line per finding, semicolon-separated>

Under 300 characters. Report the problem; do NOT fix it.
"""


# A23. Asked BEFORE any building, read-only. The finding this exists for: a
# worker-written gate is the brief restated as an assertion, so a wrong
# requirement becomes a green test DEFENDING the defect -- and preflight_expect
# is blind to it by construction, because "red before, green after" is what a
# confidently-built defect looks like too.
#
# EVIDENCE is required and is CHECKED (the path must exist in the tree), which
# is what separates this from a general invitation to complain. A worker that
# cannot point at the contradiction has an opinion, not an objection, and only
# an objection is worth stopping a run for.
CHALLENGE_SUFFIX = """

---
Do NOT build anything yet. Read the code first and answer only this:

CHALLENGE: none
  -- or --
CHALLENGE: <one line: the FALSE claim, or the ambiguity>
EVIDENCE: <repo-relative path[:line] that shows it>

ONE TEST, and it is the only one: can you build something that satisfies this
brief as written? If yes, answer `CHALLENGE: none` -- even if you would have
designed it differently.

Object ONLY when one of these is true:
  1. The brief states something about this code that is FALSE. (It says values
     are stored in dollars; they are stored in cents.)
  2. The brief is ambiguous enough that two honest readings produce programs
     that BEHAVE differently at runtime -- not that look different.

These are NOT objections. Every one of them is still buildable:
  - a name you would have chosen differently, or that implies more than it does
  - the work duplicating or overlapping something that already exists
  - a simpler, cleaner or more general design you would prefer
  - missing tests, missing docs, style, structure, layering
  - anything you would raise in code review rather than refuse to start

You are answering "is this buildable?", not "is this how I would do it?".
A wrong objection costs a whole run and teaches the caller to switch this
question off, which loses the objections that mattered.

EVIDENCE must be a real path in this repository; an objection you cannot point
at will be discarded.
"""

# The machine-read tail of a reply. FINDINGS joins the handoff keys rather than
# getting a parser of its own: one reader means a worker that formats the line
# oddly (bolded, hashed, back-ticked) is understood the same way in both.
_TAIL_KEYS = ("HANDOFF", "FILES", "NEXT", "FINDINGS", "CHALLENGE", "EVIDENCE")


def parse_handoff(text):
    """Pull the HANDOFF/FILES/NEXT/FINDINGS lines out of Qwen's reply."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip().lstrip("*# ").strip()
        for key in _TAIL_KEYS:
            prefix = f"{key}:"
            if line.upper().startswith(prefix):
                out[key] = line[len(prefix):].strip().strip("*`").strip()
    return out


def parse_findings(text):
    """The FINDINGS line of a report run, or None."""
    return parse_handoff(text).get("FINDINGS")


def strip_handoff(text):
    """Remove the handoff lines from prose so they aren't shown twice."""
    keep = []
    for line in (text or "").splitlines():
        probe = line.strip().lstrip("*# ").strip().upper()
        if any(probe.startswith(f"{k}:") for k in _TAIL_KEYS):
            continue
        keep.append(line)
    return "\n".join(keep).strip()


# The kinds that are FINDINGS -- claims about the delivered work, whose absence
# a caller reads as "nothing found". Derived from the registry so a detector
# added tomorrow is covered without editing this file.
_FINDING_KINDS = frozenset(d.KIND for d in detectors.DETECTORS)


def _suppressed_line(dropped, failed):
    """One line naming every check that did NOT report, and why.

    G1. Two causes, one consequence: a finding the size cap shed (the detector
    ran and had something to say, and there was no room) and a detector that
    raised (nothing is known either way). Both leave the receipt silent, and a
    reader takes silence for a clean result -- PRINCIPLES §IV, where a zero
    meaning "nothing found" and a zero meaning "nothing was measured" have to
    stay distinguishable or every zero stops being evidence.

    Findings ONLY. RESUME and LEDGER are shed by any long receipt and cost a
    caller nothing they cannot ask for again; naming those would fire this line
    constantly, and a warning that fires always is one nobody reads.
    """
    if not dropped and not failed:
        return None
    bits = ([f"{k} (size)" for k in dropped]
            + [f"{k} (failed)" for k in failed])
    return ("SUPPRESSED: " + ", ".join(bits)
            + " -- these checks did not report, so their silence is not "
              "evidence of a clean run.")


def _emit(into, feature, ctx, text_only=False):
    """Append one feature's receipt block(s), if its finding fired.

    The split this exists to make: the FEATURE owns its text, its droppability
    and its priority; the RENDERER owns where the block goes. Position is not
    cosmetic -- it is the cap's tie-break among equal priorities (see
    qd/surface/receipt.py rule 1), which is why the call sites below sit where
    the inline branches used to and not wherever is tidiest.

    `text_only` for the fixed region, which is a list of strings rather than of
    blocks: those lines are never dropped, so droppability and priority have
    nothing to act on there.
    """
    data = _finding(ctx.get("detections"), feature.KIND)
    if data is None:
        return
    for b in feature.block(data):
        into.append(b.text if text_only else b)


def render(status, session_id, trail, result_text, denials, max_iter, ctx, last_verify=None):
    cwd = ctx["cwd"]
    guard_on = ctx["guard_on"]
    # C3: tree facts captured by the engine from the tree the run actually used
    # (worktree or main), BEFORE any worktree commit/release. When absent
    # (v1-shaped ctx), every consumer below re-reads `cwd` -- the pinned
    # fallback the differential oracle depends on.
    facts = ctx.get("tree_facts")

    # "verify passed" is only evidence if it was failing beforehand. U3.2 moved
    # this decision into the engine (decision 4), so for an engine-rendered
    # receipt this is a no-op on a status that is already final; it stays for
    # direct render() callers whose ctx never went through the engine. A
    # declared "green" preflight is revision work, where a passing gate
    # beforehand is the premise rather than a warning.
    if (ctx["preflight"] and status == "success"
            and ctx.get("preflight_expect") != "green"):
        status = "success_but_preflight_passed"

    # R2 (PLAN-v3-l5): a clean green run gets a COMPACT receipt -- diagnostics render
    # only when something needs the manager's judgment. Red/flagged paths stay verbose.
    clean = status == "success"

    body = [f"STATUS: {status}", f"SESSION: {session_id or 'unknown'}"]
    body.append(f"ATTEMPTS: {len(trail)}/{max_iter}")

    # G3: the obvious response to a failed run is another attempt, and here
    # that is precisely the wrong one -- the last two attempts produced
    # byte-identical gate output, so a third produces this same receipt. The
    # remedy is upstream, in the brief or the gate.
    #
    # In the FIXED region and beside the status it explains, because a caller
    # who reads `stuck_no_progress` without this line will answer it by
    # retrying, which is the exact behaviour the status exists to stop.
    if status == "stuck_no_progress":
        body.append(
            "NO PROGRESS: the last two attempts produced identical gate "
            "output -- the worker is not converging on this brief. Change the "
            "brief or the gate; another attempt returns this same receipt.")

    # RUN: one fixed telemetry line on every receipt -- the numbers a caller
    # otherwise pieced together from four scattered sections (or investigated).
    run_parts = [f"{len(trail)} attempt(s)"]
    _peak = ctx.get("peak", 0)
    _win = context_window()
    if _peak and _win:
        run_parts.append(f"peak {100.0 * _peak / _win:.0f}% ctx")
    elif _peak:
        run_parts.append(f"peak {_peak:,} ctx")
    _cum = ctx.get("cum") or {}
    if _cum.get("ms"):
        run_parts.append(f"{_cum['ms'] / 1000.0:.0f}s")
    _tout = int((_cum.get("tokens") or {}).get("completion") or 0)
    if _tout:
        run_parts.append(f"{_tout:,} out")
    _denied = len(denials or []) + len(ctx.get("meta", {}).get("blocked") or [])
    if _denied:
        run_parts.append(f"{_denied} denied")
    _strays = _finding(ctx.get("detections"), "strays") or []
    if _strays:
        run_parts.append(f"{len(_strays)} strays")
    body.append("RUN: " + " · ".join(run_parts))

    # U5.5: this run is a corrected re-run of an earlier session, started COLD.
    # Said out loud on every receipt, green included: it otherwise reads as a
    # first attempt, and the session named here is where the attempt it
    # corrects can be found.
    if ctx.get("retry_of"):
        body.append(f"RETRY OF: {ctx['retry_of']}")

    # U6: which document version briefed this run. On every receipt, green
    # included -- the path @ digest is what lets a caller pair a result with
    # the exact git-versioned brief that produced it. The size estimate and
    # the consolidate nudge are the cheap half of brief-size discipline: an
    # append-only amendment list that contradicts itself is session confusion
    # in document form.
    brief = ctx.get("brief")
    if isinstance(brief, dict) and brief.get("path"):
        line = f"BRIEF: {brief['path']} @ {brief.get('sha256')}"
        if brief.get("amended"):
            line += " (amended)"
        chars = int(brief.get("chars") or 0)
        if chars:
            line += f" · ~{max(1, chars // 4):,} tokens"
        n_amend = int(brief.get("amendments") or 0)
        if n_amend > 5:
            line += f" ({n_amend} amendments — consolidate)"
        body.append(line)

    if not clean:
        for t in trail:
            body.append(f"  - {t}")

    # The one status that is not a failure of the work: the task was too big to hold.
    # Say what to do about it, because "retry" is the wrong instinct here -- the same
    # task will reach the same wall, and the worker's post-compaction output is the
    # exact thing that must not be trusted or graded.
    # A run WE ended. Not a failure of the work and not a failure of the gate --
    # no gate ran at all -- so say so, or the natural reading is "the worker
    # broke" and the natural response is to retry the same thing.
    if status == "stopped":
        body.append(
            "STOPPED: this run hit a live limit and was ended before it "
            "finished, so nothing here has been verified and any work on disk "
            "is partial. It is not a defect in the worker or in your code. "
            "Either the task is too big for one delegation -- split it -- or "
            "the limit is too tight for this project: raise `burn_budget` / "
            "`decode_tps` in .qwen-delegate.json. Re-running it unchanged will "
            "hit the same wall."
        )

    if status == "compaction_refused":
        blocked = ctx.get("compaction_blocked")
        body.append(
            "COMPACTION: this run hit the executor's context limit and was STOPPED "
            + ("before the summary was made. "
               if blocked else
               "after the history was summarised (the block was not honoured). ")
            + "Nothing here is trusted -- no gate was run and any work on disk is "
              "partial. Do NOT re-delegate this task unchanged: split it into "
              "smaller units with their own gates, or narrow its scope. "
              "`on_compaction=\"reinject\"` restores the old continue-anyway "
              "behaviour if you truly want it."
        )

    # R3: what a green here MEANS depends on who authored the gate -- say so.
    if ctx.get("trust") == "self":
        body.append("TRUST: self (L5) -- gate = the delegate's own suite, "
                    "non-vacuous guard only")

    # Prominent: the project was just self-configured. Relay it and act on the two open
    # questions (test command if undetected, CLAUDE.md policy block).
    if ctx.get("bootstrap_note"):
        body.append(ctx["bootstrap_note"])

    if guard_on:
        if facts:
            body.append(blast_lines(ctx["pre_status"], facts["post_status"],
                                    facts["numstat"]))
        else:
            body.append(blast_radius(cwd, ctx["pre_status"]))
        # Deterministic (no model tokens): new public symbols Qwen introduced -- the
        # design choices that become contracts. The manager reviews this one line
        # instead of reading the whole diff. A passing gate does NOT catch an EXTRA
        # public symbol; this does.
        pubs = facts["pubs"] if facts else new_public_symbols(cwd)
        if pubs:
            flat = ", ".join(
                f"{n} ({f.split('/')[-1]})" for f, ns in pubs.items() for n in ns
            )
            body.append(f"NEW PUBLIC SURFACE: {flat}")

    # U4.2 test dodge. Rendered on GREEN too, and deliberately not droppable:
    # a skip added in the same run that delivers the tests is exactly the case
    # where the receipt is otherwise indistinguishable from honest work, and
    # suppressing it on the compact green path would hide it precisely when it
    # matters most.
    _emit(body, detectors.dodge, ctx, text_only=True)

    # Context used, so Claude can size the next delegation.
    peak = ctx.get("peak", 0)
    win = context_window()
    if peak and win:
        warn_at, auto_at = compaction_thresholds(win)
        pct = 100.0 * peak / win
        if peak >= warn_at:
            body.append(
                f"CONTEXT: peak {peak:,}/{win:,} ({pct:.0f}%) -- APPROACHING COMPACTION "
                f"at {auto_at:,.0f}. Compaction is lossy and can summarize QWEN.md's "
                f"rules away mid-task. Split the work or start a fresh session."
            )
        elif not clean:
            body.append(
                f"CONTEXT: peak {peak:,}/{win:,} ({pct:.0f}%), compaction at "
                f"{auto_at:,.0f} ({100.0*auto_at/win:.0f}%) -- ample headroom"
            )
    elif peak and not clean:
        body.append(f"CONTEXT: peak {peak:,} tokens (window unknown)")

    # Scoped-shell elicitation: commands the hook blocked. Surfacing them is the
    # "ask the manager" half -- Qwen wanted to run these; you decide if they're
    # legit. Grouped by deny reason: the manager adjudicates at the REASON
    # granularity, and the raw list ran to 12 lines of near-duplicates (~40% of
    # a red receipt). The full list is in the run log.
    blocked = ctx.get("meta", {}).get("blocked") or []
    # MCP denials get their own block: the approval route is mcp_allow, and a
    # receipt that says "approve via shell_allow" for them sends the manager to
    # a knob that does nothing (seen on the first live denial, C1 2026-08-01).
    mcp_blocked = [b for b in blocked
                   if b.endswith("(MCP tool not on the mcp allowlist)")]
    if mcp_blocked:
        blocked = [b for b in blocked if not
                   b.endswith("(MCP tool not on the mcp allowlist)")]
        names = sorted({b.split(":", 1)[0] for b in mcp_blocked})
        body.append(
            f"MCP APPROVAL NEEDED: {len(mcp_blocked)} call(s) to "
            f"{len(names)} tool(s) (judge on the tool name; approve via a "
            "mcp_allow name regex and re-delegate; full list in "
            ".qwen-delegate/runs.jsonl):\n"
            + "\n".join(f"  - {n}" for n in names[:6]))
    if blocked:
        groups = {}
        for b in blocked:
            reason = "other"
            if b.endswith(")") and "(" in b:
                reason = b[b.rfind("(") + 1:-1]
            groups.setdefault(reason, []).append(b)
        lines = []
        for reason, items in sorted(groups.items(),
                                    key=lambda kv: -len(kv[1]))[:4]:
            if len(items) == 1:
                lines.append(f"  - {items[0]}")
            else:
                example = items[0]
                if example.endswith(")") and "(" in example:
                    example = example[:example.rfind("(")].rstrip()
                lines.append(f"  - {len(items)}x ({reason}) -- e.g. {example}")
        if len(groups) > 4:
            lines.append(f"  ... and {len(groups) - 4} more group(s)")
        body.append(
            f"SHELL APPROVAL NEEDED: {len(blocked)} blocked in {len(groups)} "
            "group(s) (judge on the command alone; approve via shell_allow + "
            "same session_id, deny with the reason in shell_feedback; full "
            "list in .qwen-delegate/runs.jsonl):\n" + "\n".join(lines)
        )

    st = ctx.get("meta", {}).get("stats") or {}
    if st.get("ms") and ctx.get("peak"):
        secs = st["ms"] / 1000.0
        budget = ctx.get("timeout", 0)
        if budget:
            used = 100.0 * secs / budget
            if used > 70:
                body.append(
                    f"TIME: {secs:.0f}s of {budget}s budget ({used:.0f}%) -- close to "
                    f"timeout; raise timeout_sec for tasks like this")
            elif not clean:
                body.append(f"TIME: {secs:.0f}s of {budget}s budget ({used:.0f}%)")
    if st.get("tools") and not clean:
        tl = f"TOOLS: {st['tools']} call(s)"
        if st.get("tool_names"):
            tl += f" ({', '.join(st['tool_names'][:6])})"
        if st.get("ms"):
            tl += f", {st['ms']/1000:.0f}s"
        body.append(tl)
    if st.get("tool_fail"):
        # In restricted modes, denied shell/edit calls are the design, not a defect --
        # measured: auto-edit runs show 3/9 "failures" that are just blocked shell
        # attempts, while the gate passes. Only flag as suspect where tools were free.
        if ctx.get("approval_mode") in ("plan", "auto-edit", "default", "auto"):
            if not clean:
                body.append(
                    f"TOOL FAILURES: {st['tool_fail']} of {st['tools']} tool call(s) were "
                    f"blocked -- expected under approval_mode="
                    f"'{ctx.get('approval_mode')}' (Qwen tried tools this mode denies). Not "
                    f"a defect on its own; the gate is what decides."
                )
        else:
            body.append(
                f"TOOL FAILURES: {st['tool_fail']} of {st['tools']} tool call(s) FAILED. "
                f"Qwen may have worked around this or reported success anyway -- treat the "
                f"result as suspect and check CHANGED."
            )
    if st.get("api_errors"):
        body.append(f"API ERRORS: {st['api_errors']} request(s) errored during this run.")

    if ctx.get("reinjects") or ctx.get("discards"):
        if ctx.get("discards"):
            body.append(
                f"COMPACTED: this session was compacted mid-run and DISCARDED "
                f"({ctx['discards']}x); work restarted cold against the same tree. The "
                f"corrupted summary is gone, but so is everything it had learned -- and "
                f"the files on disk are from the abandoned attempt. Check CHANGED."
            )
        else:
            body.append(
                f"COMPACTED: this session was compacted mid-run ({ctx['reinjects']}x) "
                f"and the task was re-injected into the WARM session. The compaction "
                f"summary is still in its history and that summary is exactly what has "
                f"been observed to fabricate -- treat any claim about work done before "
                f"the compaction as unverified, and check CHANGED, not the narrative. "
                f"Re-delegate with on_compaction='discard' if you need a clean slate."
            )

    # U3.1: rendered on green too. A gate that eats half its own budget is paid
    # again by every attempt, and the one run where that is cheap to fix is the
    # one that passed -- on a red receipt the same line arrives as an excuse.
    if ctx.get("gate_slow"):
        body.append(
            f"GATE SLOW: preflight took {(ctx.get('gate_ms') or 0) / 1000.0:.0f}s "
            f"of a {ctx.get('verify_timeout_sec') or 300}s verify budget -- every "
            f"retry pays it; speed the gate up or raise verify_timeout_sec."
        )

    if status == "gate_suspect":
        body.append(
            "GATE SUSPECT: the verify command produced identical output before and after "
            "Qwen ran, so nothing it did moves this gate. Almost always the gate itself is "
            "wrong -- malformed quoting, a bad path, or it tests something the task never "
            "touches. Check CHANGED above: Qwen may have done the work correctly while the "
            "gate was broken. Fix the gate before retrying; iterating cannot help."
        )

    if status == "success_but_preflight_passed":
        body.append(
            "PREFLIGHT: the verify command ALREADY PASSED before Qwen ran, so this "
            "pass is not evidence the task was done. Either the task was a no-op, its "
            "premise was false, or the gate does not actually test the change. Check "
            "CHANGED above: if nothing changed, Qwen may have correctly declined -- read "
            "its result. Tighten the gate to test the specific new behavior."
        )

    # U3.2: under a declared "green" expectation the paragraph above is noise --
    # revision work runs against a suite that already passes, and the alarm
    # fired on every one of them. The fact still belongs on the receipt, as one
    # line rather than a warning nobody can act on.
    elif ctx.get("preflight") and ctx.get("preflight_expect") == "green":
        body.append(
            "PREFLIGHT: green pre-run, declared expected (revision gate).")

    # U4.2 report_dont_fix: the run was never asked to make the gate green, so
    # the gate's output is the deliverable rather than a verdict on the work.
    # Said out loud because every other red-ish status here means "the work
    # failed", and this one means "the work was a diagnosis".
    if status == "reported":
        body.append(
            "REPORTED: report_dont_fix -- one attempt, one gate run, no retry "
            "loop; the worker was told to diagnose and NOT to fix. The gate "
            "output below is the finding, not a failure of the work."
        )
        if ctx.get("report_gate_green"):
            body.append("gate GREEN -- the reported problem did not reproduce "
                        "under this gate.")

    # Extracted by the engine BEFORE truncation, for the same reason HANDOFF is:
    # on a report run this one line is the entire product of the delegation.
    if ctx.get("findings"):
        body.append(f"FINDINGS: {ctx['findings']}")

    # U5.1 result contract. In the BODY, never the droppable region and never
    # the truncated tail: the caller asked for this payload by schema, so it is
    # the one part of the receipt that is not commentary on the work -- it IS
    # a deliverable. Verbatim, as the worker wrote it.
    if ctx.get("result_json"):
        body.append("RESULT: valid (schema)")
        body.append(f"```json\n{ctx['result_json']}\n```")
    elif status == "result_invalid":
        errs = ctx.get("result_errors") or []
        body.append(
            "RESULT INVALID: the reply's final JSON block does not conform to "
            "result_schema after every attempt -- "
            + "; ".join(errs[:4]) + (" ..." if len(errs) > 4 else "")
            + ". Read STATUS above for the work itself: what failed here is "
              "the machine-read result, which cannot be consumed as sent."
        )

    # U3.3: a fixture nobody can trace is indistinguishable from an invented
    # one, and a gate written against invented bytes passes forever.
    unproven = ctx.get("fixtures_unproven") or []
    if unproven:
        body.append(
            "FIXTURES: "
            + ", ".join(unproven[:10])
            + (" ..." if len(unproven) > 10 else "")
            + " lack captured-from provenance -- imagined fixtures were the "
              "field's worst defect class; capture real ones or mark their "
              "source."
        )

    if status == "scope_violation":
        body.append(
            "SCOPE: the run ended on a touch_scope violation -- the worker "
            "modified files outside the allowed set (named in the trail above) "
            "and those edits were reverted. In-scope work may still be on disk: "
            "check CHANGED, and widen touch_scope on re-delegation if the edits "
            "were actually needed."
        )

    # C10 co-work: files that moved during the run with no logged worker write.
    # They are NOT the worker's and were never reverted -- the caller edits this
    # replaced were being destroyed silently, so the receipt names them instead.
    co_work = ctx.get("scope_unattributed") or []
    if co_work:
        body.append(
            "SCOPE: changed during the run but NOT by a logged worker write "
            "(caller co-work?): "
            + ", ".join(co_work[:10])
            + (" ..." if len(co_work) > 10 else "")
            + " -- reported, never reverted."
        )

    spec_co_work = ctx.get("spec_unattributed") or []
    if spec_co_work:
        body.append(
            "SPEC CHANGED (unattributed): "
            + ", ".join(spec_co_work[:10])
            + (" ..." if len(spec_co_work) > 10 else "")
            + " differs from its pre-run state with no logged worker write, so "
            "it was left alone rather than reverted over a caller's edit -- but "
            "gate integrity not guaranteed for this run: what the gate means "
            "may have changed under it. Check the diff before trusting STATUS."
        )

    unrestorable = ctx.get("unrestorable") or []
    if unrestorable:
        body.append(
            "SCOPE: out-of-scope change in "
            + ", ".join(unrestorable[:10])
            + (" ..." if len(unrestorable) > 10 else "")
            + " NOT auto-reverted -- the pre-run content was too large to "
            "snapshot, and restoring from a commit would destroy pre-run "
            "edits. Review and revert manually."
        )

    # Qwen is told not to commit, but nothing enforces that in yolo -- and a commit hides
    # its work from `git status`, so CHANGED goes quiet while the tree really moved.
    if facts:
        moved, n_commits, committed_files = facts["head_moved"]
        head_now = facts["head_now"]
    else:
        moved, n_commits, committed_files = (
            committed_during_run(cwd, ctx["pre_sha"]) if guard_on else (False, 0, []))
        head_now = head_sha(cwd) if moved else None
    # U1.3: WHO moved it. Absent (v1 ctx) or "worker" keeps the accusation; a
    # run whose mode hard-denies commits (scoped) knows it was not the worker,
    # and everything else says it does not know. The old unconditional
    # accusation fired on every co-working caller's commit -- ~8 per project in
    # the field, each one an investigation that found nothing.
    who = ctx.get("head_moved_attribution")
    if moved:
        shown = ", ".join(committed_files[:10]) + (" ..." if len(committed_files) > 10 else "")
        incomplete = (f"  - CHANGED above is INCOMPLETE: committed files no longer "
                      f"show in git status. Actually changed: {shown or '(none)'}")
        if who == "caller":
            body.append(
                f"HEAD MOVED: {n_commits} commit(s) {ctx['pre_sha']} -> {head_now} "
                f"-- not the worker (commits are hard-denied in scoped mode); a "
                f"caller in this repo committed during the run.\n{incomplete}"
            )
        elif who == "unknown":
            body.append(
                f"HEAD MOVED: {n_commits} commit(s) {ctx['pre_sha']} -> {head_now} "
                f"-- by this session's caller or the worker (attribution "
                f"unknown); check git log.\n{incomplete}"
            )
        else:
            body.append(
                f"COMMITTED: Qwen moved HEAD during this run ({n_commits} commit(s), "
                f"{ctx['pre_sha']} -> {head_now}). It was told not to. Consequences you "
                f"must account for:\n"
                f"{incomplete}\n"
                f"  - the spec guard was still enforced (it diffs against the pre-run sha, "
                f"not HEAD), but review those files yourself\n"
                f"  - rollback needs a reset, not a checkout -- see ROLLBACK below"
            )

    # ROLLBACK advice targets the tree the run used; a worktree run's discard
    # path is its MERGE/worktree-remove line, not a main-tree rollback.
    if guard_on and ctx["pre_sha"] and not ctx.get("worktree"):
        if moved and who in ("caller", "unknown"):
            # Reset advice only under positive worker attribution: a
            # `reset --hard` over commits the worker did not make destroys
            # somebody else's work, and the receipt would have told them to.
            body.append(
                f"ROLLBACK: review `git log --oneline {ctx['pre_sha']}..HEAD` "
                f"before any rollback -- "
                + ("the commits are not the worker's; a blanket reset would "
                   "destroy a caller's work."
                   if who == "caller" else
                   "who made the commits is unknown; a blanket reset would "
                   "destroy work this run did not do.")
            )
        elif moved:
            # `git checkout .` cannot undo a commit; advising it here would leave the
            # commits in place and read as a successful rollback.
            safety = ("safe -- tree was clean" if ctx["pre_clean"]
                      else "CAUTION: tree was ALREADY dirty")
            body.append(
                f"ROLLBACK: git reset --hard {ctx['pre_sha']} && git clean -fd   "
                f"({safety} at {ctx['pre_sha']} before this run. A plain "
                f"`git checkout .` will NOT undo the commit(s) above.)"
            )
        elif ctx["pre_clean"]:
            body.append(
                f"ROLLBACK: git checkout . && git clean -fd   "
                f"(safe -- tree was clean at {ctx['pre_sha']} before this run)"
            )
        else:
            body.append(
                f"ROLLBACK: unsafe to blanket-revert -- the tree was ALREADY dirty at "
                f"{ctx['pre_sha']} before this run, so Qwen's changes are mixed with "
                f"pre-existing ones. Review the diff and revert selectively."
            )

    # Structured handoff, extracted BEFORE truncation so a long reply can't bury it.
    handoff = parse_handoff(result_text)
    if handoff:
        if handoff.get("HANDOFF"):
            body.append(f"HANDOFF: {handoff['HANDOFF']}")
        if handoff.get("NEXT") and not clean:
            body.append(f"NEXT: {handoff['NEXT']}")

        # Qwen's own account of what it touched vs what the filesystem says. This is the
        # fib-fabrication failure mode in miniature -- trust the filesystem.
        claimed = handoff.get("FILES", "")
        if guard_on and claimed:
            post_snap = facts["post_status"] if facts else snapshot(cwd)
            actual = {p for p in post_snap if post_snap.get(p) != ctx["pre_status"].get(p)}
            said_none = claimed.strip().lower() in ("none", "no files", "-")
            if said_none and actual:
                body.append(
                    f"MISREPORT: Qwen claims FILES: none but {len(actual)} file(s) "
                    f"changed on disk. Its account is unreliable -- trust CHANGED above."
                )
            elif not said_none and not actual:
                body.append(
                    f"MISREPORT: Qwen claims it changed '{claimed}' but nothing changed "
                    f"on disk. It may have described intended work it never did."
                )

    # (R2: the old CONTINUE paragraph is gone -- the SESSION line plus the delegation
    # skill's session rules carry the same information at ~1/20th the tokens.)

    if denials:
        # Split by whether the denial could have changed the RESULT. A denied
        # read has an obvious legal substitute -- the worker greps another way
        # and reaches the same place -- so it costs time, not trust. A denied
        # WRITE or state-changing command may have been routed around, and only
        # that justifies distrusting the verdict.
        #
        # It used to be one bucket, and the line said "treat the result as
        # suspect" for all of them. Measured: ~150 denials across twelve builds,
        # every one a well-formed read-only search or a test command carrying
        # `2>&1` -- so the receipt invalidated its own verdict on twelve runs
        # that were fine, which is the plugin undermining itself for reasons
        # that have nothing to do with the worker.
        harmless, material = [], []
        for d in denials:
            name = d.get("tool_name", "?")
            (harmless if name in READ_ONLY_TOOLS else material).append(name)
        if material:
            names = ", ".join(sorted(set(material)))
            body.append(
                f"DENIALS: {len(material)} blocked ({names}) -- effect-shaped, so "
                f"Qwen may have worked around this; treat the result as suspect."
            )
        if harmless:
            names = ", ".join(sorted(set(harmless)))
            body.append(
                f"DENIALS (read-only): {len(harmless)} blocked ({names}) -- cost "
                f"the worker time, not the result its trust. Widen `shell_allow` "
                f"to speed the next run."
            )

    # On a clean green, last_verify is a STALE earlier failure (the pass that
    # decided status produced no saved output) -- showing it misreads as red.
    verify_idx = None
    if last_verify and not clean:
        verify_idx = len(body)
        body.append(f"--- final verify output ---\n{truncate(last_verify, VERIFY_CAP)}")

    if status == "unverified":
        body.append("NOTE: no verify command -- this is Qwen's unverified claim.")

    # --- v2 C2 receipt lines ---
    # Build list of (line_text, droppable, drop_priority) tuples.
    # Lower drop_priority = higher priority (never dropped first).
    c2_blocks = []

    # ADVISORY (U3.4): loose gates that indicate rather than gate -- they never
    # touched STATUS and never reached the worker. First block in the region and
    # NON-droppable while any is red: a red indicator the cap silently dropped
    # is worse than none at all, because its absence reads as green.
    advisory = ctx.get("advisory")
    if advisory is not None:
        adv_red = [a for a in advisory if not a.get("ok")]
        adv_lines = []
        for a in adv_red:
            head = (a.get("head") or "").strip()
            adv_lines.append(f"ADVISORY red: {a.get('name')}"
                             + (f" — {head}" if head else ""))
        summary = f"ADVISORY: {len(advisory) - len(adv_red)}/{len(advisory)} green"
        skipped = ctx.get("advisory_skipped") or 0
        if skipped:
            summary += f", {skipped} skipped (malformed)"
        adv_lines.append(summary)
        c2_blocks.append(Block("advisory", "\n".join(adv_lines),
                               not adv_red, -1 if adv_red else 1))

    notes = ctx.get("notes")
    if notes:
        c2_blocks.append(Block("notes", f"NOTES: {notes[:200]}", True, 0))

    # U4.3 strays. The LINE lives with the detector (qd/features/detectors/);
    # what the renderer owns is WHERE it goes -- and its position here is
    # load-bearing, see qd/surface/receipt.py rule 1.
    _emit(c2_blocks, detectors.strays, ctx)

    # CHALLENGE: the pre-build objection pass (A23), on by default. Two things
    # are worth a line and neither is visible otherwise: that the pass RAN (a
    # green receipt means more when something tried to stop it), and an
    # objection that could not be verified -- recorded but deliberately not
    # blocking, so without this line it would exist nowhere a human looks.
    ch = ctx.get("challenge")
    if isinstance(ch, dict) and ch.get("ran"):
        if ch.get("unverified"):
            c2_blocks.append(Block(
                "challenge",
                f"CHALLENGE: worker objected but could not cite a real path, "
                f"so the run proceeded -- \"{truncate(ch['unverified'], 160)}\"",
                False, -1))
        else:
            c2_blocks.append(Block("challenge",
                                   "CHALLENGE: brief reviewed against the code, "
                                   "no objection", False, -1))

    worktree = ctx.get("worktree")
    if worktree:
        wt_line = f"WORKTREE: {worktree['path']}"
        if worktree.get("dirty"):
            wt_line += (" (main tree had uncommitted changes at branch time -- "
                        "they are NOT in this worktree)")
        c2_blocks.append(Block("worktree", wt_line, False, -1))
        merge = ctx.get("merge")
        if merge == "clean":
            c2_blocks.append(Block(
                "merge",
                f"MERGE: git merge --no-edit {worktree['branch']} && "
                f"git worktree remove {worktree['path']} && "
                f"git branch -d {worktree['branch']}", False, -1))
        elif merge == "conflict":
            c2_blocks.append(Block(
                "merge",
                f"MERGE: CONFLICT -- contract overlap with "
                f"{worktree['branch']}; escalate, do not force", False, -1))

    # TIMEOUT: the budget, fitted from this run's own telemetry. Priority 0 --
    # above the diagnostics, because a killed run's remedy is a single number
    # and without it the caller re-runs into the same wall.
    try:
        t_line = limits.timeout_line(ctx)
    except Exception:
        t_line = None
    if t_line:
        c2_blocks.append(Block("timeout", t_line, True, 0))

    # --- Seam risk (v0.6) ---
    # A green receipt is evidence about a MODULE and is routinely read as
    # evidence about a product. These three lines say where that reading is
    # unsupported. Drop priority 1: they are diagnostics, so they yield to
    # STATUS/CHANGED/ROLLBACK under the size cap, but they sit ABOVE the
    # accounting lines because a dead symbol matters more than a token count.
    _emit(c2_blocks, detectors.uncalled, ctx)
    _emit(c2_blocks, detectors.mocked_seams, ctx)
    _emit(c2_blocks, detectors.never_executed, ctx)

    # DISPATCH: what the fan-out ACTUALLY did. The capacity a call gets is
    # resolved from three files (call arg > project > machine) and was visible
    # nowhere, so a batch that silently serialised looked exactly like one that
    # fanned out -- N x wall-clock for 1x throughput, with the skill asking the
    # caller to hold the whole precedence chain in their head to predict it.
    # Only on a fan-out: a single delegation has nothing to serialise.
    dispatch = ctx.get("dispatch")
    if dispatch and ctx.get("batch_size", 0) > 1:
        endpoint = ctx.get("endpoint") or {}
        slots = endpoint.get("parallel_max", 1)
        line = (f"DISPATCH: {dispatch} · endpoint {endpoint.get('name', '?')} "
                f"· {slots} slot(s) · {ctx['batch_size']} item(s)")
        if dispatch == "serial" and ctx["batch_size"] > 1:
            line += " -- items ran IN ORDER, not concurrently"
        c2_blocks.append(Block("dispatch", line, True, 2))

    # GRAPH accountability: count the worker's graphify reads (C10 allow-log)
    # so a groomed-but-never-consulted graph is visible instead of invisible.
    graph_used = sum(
        1 for a in (ctx.get("meta", {}).get("allowed") or [])
        if a.startswith("run_shell_command: graphify "))
    graph_line = ctx.get("graph_line")
    if graph_line:
        if graph_used:
            graph_line += f" · used {graph_used}x this run"
        c2_blocks.append(Block("graph", graph_line, True, 2))

    refs_added = ctx.get("refs_added") or []
    refs_result = refs.refs_line(refs_added)
    if refs_result:
        c2_blocks.append(Block("refs", refs_result, True, 3))

    cost_usd = float(ctx.get("cost_usd", 0.0))
    executor = ctx.get("executor")
    if cost_usd > 0:
        c2_blocks.append(Block(
            "cost", f"COST: ${cost_usd:.4f} ({executor or 'unknown'})", True, 4))

    burn = burn_line(ctx)
    if burn:
        c2_blocks.append(Block("burn", burn, True, 5))

    # LEDGER: one line of project history -- the log finally gets a reader.
    ledger = ledger_summary(cwd)
    if ledger:
        _lw = context_window()
        pk = ledger["peak"]
        pk_txt = f"{100.0 * pk / _lw:.0f}%" if pk and _lw else f"{pk:,}"
        ledger_txt = (
            f"LEDGER: run #{ledger['n'] + 1} · lifetime {ledger['ok']} ok / "
            f"{ledger['red']} red / {ledger['stopped']} stopped · "
            f"peak-ctx record {pk_txt}")
        # U6: the same document's own record, only when prior runs used it --
        # "this brief has cried red twice" is what tells a caller to amend the
        # document rather than re-roll the worker.
        if isinstance(ctx.get("brief"), dict) and ctx["brief"].get("path"):
            bsum = brief_summary(cwd, ctx["brief"]["path"])
            if bsum:
                ledger_txt += (f" · this brief: {bsum['ok']} ok / "
                               f"{bsum['red']} red")
        c2_blocks.append(Block("ledger", ledger_txt, True, 6))

    # RESUME: the affordance is cheaper than the education -- session resume
    # existed for 45 field delegations and went unused because nothing said so.
    # U5.4 makes it three-way, because the affordance was pointing the wrong
    # way half the time: a session that FAILED carries its confusion forward,
    # and resuming into it buys an argument with the correction instead of a
    # fix. Two exceptions keep the warm line on red: a run that was BLOCKED was
    # fenced rather than confused, and the approval loop (shell_allow +
    # shell_feedback) only works in the same session.
    if session_id and status not in ("stopped", "compaction_refused",
                                     "error", "refused"):
        healthy = status in ("success", "success_but_preflight_passed",
                             "unverified", "reported")
        was_blocked = bool(ctx.get("meta", {}).get("blocked"))
        if healthy or was_blocked:
            c2_blocks.append(Block(
                "resume",
                f"RESUME: session_id={session_id} -- a follow-up in this warm "
                f"session costs a sentence, not a re-brief (same cwd, pass "
                f"session_id)", True, 7))
        else:
            c2_blocks.append(Block(
                "resume",
                f"RESUME: not recommended — {len(trail)} failed attempt(s) in "
                f"this session carry their confusion forward; re-delegate COLD "
                f"with a corrected brief (or retry_of={session_id}).",
                True, 7))

    # Insert C2 blocks before the "--- qwen result ---" line.
    caps = {"result": RESULT_CAP, "verify": VERIFY_CAP}
    # G1: what the cap shed, and what never ran. Recomputed into the receipt on
    # every pass of the loop below, so the line reflects the FINAL set of drops
    # rather than a snapshot taken partway through.
    suppressed = []
    failed_detectors = list(ctx.get("detections_failed") or [])

    def _assembled():
        parts = list(body)
        for blk in c2_blocks:
            parts.append(blk.text)
        _sup = _suppressed_line(suppressed, failed_detectors)
        if _sup:
            # NON-DROPPABLE by construction: it is not in c2_blocks at all, so
            # the loop cannot shed the one line that reports shedding. A
            # self-defeating warning is worse than none -- its absence is
            # precisely what it exists to deny.
            parts.append(_sup)
        parts.append(
            f"--- qwen result ---\n"
            f"{truncate(strip_handoff(result_text), caps['result'])}")
        return "\n".join(parts)

    # Cap enforcement (N1): drop droppable C2 blocks reverse-priority, THEN
    # shrink the qwen-result tail (floor 200), then the verify tail (floor
    # 400). The old enforcement stopped at C2 drops, so body+tails alone could
    # -- and on this repo's own ledger, 4 of 18 receipts did -- blow the cap.
    verdict = _assembled()
    while len(verdict) > 3000:
        droppable = sorted(
            [(i, b.priority) for i, b in enumerate(c2_blocks) if b.droppable],
            key=lambda x: -x[1])
        if droppable:
            gone = c2_blocks.pop(droppable[0][0])
            if gone.kind in _FINDING_KINDS:
                suppressed.append(gone.kind)
        elif caps["result"] > 200:
            caps["result"] = max(200, caps["result"] - (len(verdict) - 3000))
        elif verify_idx is not None and caps["verify"] > 400:
            caps["verify"] = max(400, caps["verify"] - (len(verdict) - 3000))
            body[verify_idx] = (f"--- final verify output ---\n"
                                f"{truncate(last_verify, caps['verify'])}")
        else:
            break
        verdict = _assembled()

    # Logged LAST: every diff above is taken against the pre-run snapshot, so the log
    # file must not exist in the tree until they are done. (It is gitignored regardless
    # -- belt and braces, because getting this wrong would corrupt the CHANGED report.)
    cum = ctx.get("cum") or cum_zero()
    changed = 0
    if guard_on:
        if facts:
            changed = len(facts["changed"])
        else:
            post = snapshot(cwd)
            changed = len(
                [p for p in post if post.get(p) != ctx["pre_status"].get(p)])
    extra = {
        "session": session_id,
        "approval_mode": ctx.get("approval_mode"),
        "attempts": len(trail),
        "max_iterations": max_iter,
        "task": digest(ctx.get("task")),
        "gate": {
            "cmd": digest(ctx.get("verify")),
            "preflight_passed": ctx.get("preflight"),
        },
        "trust": ctx.get("trust"),
        "changed_files": changed,
        "head_moved": moved,
        "commits_by_worker": n_commits,
        "resumed": bool(ctx.get("session_hint")),
        "compactions": sum(compaction_state(s)[0] for s in ctx.get("sessions", [])),
        "sessions": len(ctx.get("sessions", [])),
        "reinjections": ctx.get("reinjects", 0),
        "discards": ctx.get("discards", 0),
        "on_compaction": ctx.get("on_compaction"),
        "blocked_shell": len(ctx.get("meta", {}).get("blocked") or []),
        "blocked_commands": (ctx.get("meta", {}).get("blocked") or [])[:50],
        "denials": len(denials or []),
        "graph_used": graph_used,
        # C10: how much of this run's blast radius the worker owns, and how
        # much a caller was doing at the same time.
        "writes_attributed": len(ctx.get("writes") or []),
        "caller_changed": len(ctx.get("scope_unattributed") or []),
        "strays": len(_finding(ctx.get("detections"), "strays") or []),
        # G1: the receipt is capped; the log is not. A finding that did not
        # fit still happened, and this is where it survives.
        "detections_suppressed": list(suppressed),
        "detections_failed": list(failed_detectors),
    }
    # U5.2: the id the submit minted. This record is what CLOSES the `running`
    # one written at spawn -- without the id here, a reader could not tell an
    # in-flight run from one that died with its session.
    if ctx.get("run_id"):
        extra["run_id"] = ctx["run_id"]
    if ctx.get("retry_of"):
        extra["retry_of"] = ctx["retry_of"]
    # U6: path + digest only -- enough for brief_summary to group runs by
    # document without the log accumulating brief text (digest() policy).
    if isinstance(ctx.get("brief"), dict) and ctx["brief"].get("path"):
        extra["brief"] = {"path": ctx["brief"]["path"],
                          "sha256": ctx["brief"].get("sha256")}
    # C5, non-defaults only: one line per run is read whole by ledger_summary
    # and by people, and a key that says "300"/"any" in every record is noise
    # that hides the runs where somebody actually turned a knob.
    if ctx.get("verify_timeout_sec") not in (None, 300):
        extra["verify_timeout_sec"] = ctx["verify_timeout_sec"]
    if ctx.get("preflight_expect") not in (None, "any"):
        extra["preflight_expect"] = ctx["preflight_expect"]
    if advisory is not None:
        extra["advisory"] = {"red": len(adv_red), "of": len(advisory)}
    if ctx.get("report"):
        extra["report"] = True
        extra["findings"] = bool(ctx.get("findings"))
    chain = ctx.get("chain")
    if chain:
        # `halted` is DERIVED, not passed: run_chain stops at the first link
        # whose receipt is not green, so a failing link IS the halt point by
        # construction -- and by the time the halt is known this receipt has
        # already been rendered, so nothing could retro-write the flag onto it.
        rec = {"pos": chain.get("pos"), "of": chain.get("of")}
        if status not in ("success", "success_but_preflight_passed"):
            rec["halted"] = True
        extra["chain"] = rec
    # Per-call telemetry: the run log gets it, the RECEIPT does not. The receipt
    # is context the caller pays for on every run; a per-call breakdown is
    # analysis you do later, over many runs, from a file that costs nothing to
    # write and nothing to read until you ask.
    calls = ctx.get("calls")
    if calls is not None and hasattr(calls, "as_record"):
        # The profile prices each KIND, so the log answers "what did challenge
        # passes cost us" in money and not only in tokens -- which is the form
        # the question gets asked in.
        extra.update(calls.as_record(ctx.get("profile")))
    write_runlog(cwd, leverage_record(
        "qwen_delegate", cwd, status, verdict, cum, ctx.get("peak", 0),
        executor=executor or "qwen-local",
        cost_usd=cost_usd,
        extra=extra,
    ))
    return verdict
