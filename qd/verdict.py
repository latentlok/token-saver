"""Receipt rendering, crane-equal for v1 inputs, behavior frozen by specs/verdict_spec.py."""

from qd.gittree import blast_radius, new_public_symbols, committed_during_run, head_sha, snapshot
from qd.invoke import context_window, compaction_thresholds, cum_zero, compaction_state, truncate
from qd.runlog import write_runlog, leverage_record, digest
from qd import refs


RESULT_CAP = 3000
VERIFY_CAP = 2500

# A run past this many INPUT tokens is reported as heavy. Set where a 27B-class
# local model spends roughly an hour of GPU: enough headroom that ordinary work
# never trips it, low enough that a runaway agentic loop does.
BURN_WARN_TOKENS = 3_000_000


def burn_line(ctx):
    """One BURN: line — input tokens, calls, and the per-call context average.

    On a local endpoint COST is $0.0000 whatever happens, so a run that made 218
    calls averaging 87k of context looked exactly like one that made four. The
    spend is real (GPU minutes, and the queue behind it) and nothing in the
    receipt showed it. Input is reported first because that is where it goes:
    a local endpoint does no cross-call prompt caching that these counters can
    see, so every turn re-sends the whole accumulated context and a long
    agentic loop costs roughly the SQUARE of its length.
    """
    cum = ctx.get("cum") or {}
    tokens = cum.get("tokens") or {}
    tin = int(tokens.get("prompt") or 0)
    tout = int(tokens.get("completion") or 0)
    if not tin and not tout:
        return None
    turns = int(cum.get("turns") or 0)
    parts = [f"BURN: {tin:,} in / {tout:,} out"]
    if turns:
        parts.append(f"{turns} calls, ~{tin // turns:,} ctx/call")
    line = ", ".join(parts)
    if tin >= BURN_WARN_TOKENS:
        line += (" -- HEAVY. Free in money, not in time; a loop this long also "
                 "risks compaction. Split the task into smaller delegations.")
    return line

HANDOFF_SUFFIX = """

---
Finish your reply with exactly these three lines, after any prose:

HANDOFF: <one line: what state the work is in now>
FILES: <comma-separated paths you created or modified, or the word: none>
NEXT: <one line: what a follow-up would know, or the word: nothing>

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


def render(status, session_id, trail, result_text, denials, max_iter, ctx, last_verify=None):
    cwd = ctx["cwd"]
    guard_on = ctx["guard_on"]

    # "verify passed" is only evidence if it was failing beforehand.
    if ctx["preflight"] and status == "success":
        status = "success_but_preflight_passed"

    # R2 (PLAN-v3-l5): a clean green run gets a COMPACT receipt -- diagnostics render
    # only when something needs the manager's judgment. Red/flagged paths stay verbose.
    clean = status == "success"

    body = [f"STATUS: {status}", f"SESSION: {session_id or 'unknown'}"]
    body.append(f"ATTEMPTS: {len(trail)}/{max_iter}")
    if not clean:
        for t in trail:
            body.append(f"  - {t}")

    # The one status that is not a failure of the work: the task was too big to hold.
    # Say what to do about it, because "retry" is the wrong instinct here -- the same
    # task will reach the same wall, and the worker's post-compaction output is the
    # exact thing that must not be trusted or graded.
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
        body.append(blast_radius(cwd, ctx["pre_status"]))
        # Deterministic (no model tokens): new public symbols Qwen introduced -- the
        # design choices that become contracts. The manager reviews this one line
        # instead of reading the whole diff. A passing gate does NOT catch an EXTRA
        # public symbol; this does.
        pubs = new_public_symbols(cwd)
        if pubs:
            flat = ", ".join(
                f"{n} ({f.split('/')[-1]})" for f, ns in pubs.items() for n in ns
            )
            body.append(f"NEW PUBLIC SURFACE: {flat}")

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
    # "ask the manager" half -- Qwen wanted to run these; you decide if they're legit.
    blocked = ctx.get("meta", {}).get("blocked") or []
    if blocked:
        body.append(
            "SHELL APPROVAL NEEDED (judge each on the command alone; approve via "
            "shell_allow + same session_id, deny with the reason in shell_feedback):\n"
            + "\n".join(f"  - {b}" for b in blocked[:12])
            + ("\n  ..." if len(blocked) > 12 else "")
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

    # Qwen is told not to commit, but nothing enforces that in yolo -- and a commit hides
    # its work from `git status`, so CHANGED goes quiet while the tree really moved.
    moved, n_commits, committed_files = (
        committed_during_run(cwd, ctx["pre_sha"]) if guard_on else (False, 0, []))
    if moved:
        shown = ", ".join(committed_files[:10]) + (" ..." if len(committed_files) > 10 else "")
        body.append(
            f"COMMITTED: Qwen moved HEAD during this run ({n_commits} commit(s), "
            f"{ctx['pre_sha']} -> {head_sha(cwd)}). It was told not to. Consequences you "
            f"must account for:\n"
            f"  - CHANGED above is INCOMPLETE: committed files no longer show in "
            f"git status. Actually changed: {shown or '(none)'}\n"
            f"  - the spec guard was still enforced (it diffs against the pre-run sha, "
            f"not HEAD), but review those files yourself\n"
            f"  - rollback needs a reset, not a checkout -- see ROLLBACK below"
        )

    if guard_on and ctx["pre_sha"]:
        if moved:
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
            post_snap = snapshot(cwd)
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
        names = ", ".join(sorted({d.get("tool_name", "?") for d in denials}))
        body.append(
            f"DENIALS: {len(denials)} blocked ({names}) -- Qwen may have worked around "
            f"this; treat the result as suspect."
        )

    # On a clean green, last_verify is a STALE earlier failure (the pass that
    # decided status produced no saved output) -- showing it misreads as red.
    if last_verify and not clean:
        body.append(f"--- final verify output ---\n{truncate(last_verify, VERIFY_CAP)}")

    if status == "unverified":
        body.append("NOTE: no verify command -- this is Qwen's unverified claim.")

    # --- v2 C2 receipt lines ---
    # Build list of (line_text, droppable, drop_priority) tuples.
    # Lower drop_priority = higher priority (never dropped first).
    c2_blocks = []

    notes = ctx.get("notes")
    if notes:
        c2_blocks.append((f"NOTES: {notes[:200]}", True, 0))

    worktree = ctx.get("worktree")
    if worktree:
        c2_blocks.append((f"WORKTREE: {worktree['path']}", False, -1))
        merge = ctx.get("merge")
        if merge == "clean":
            c2_blocks.append(
                (f"MERGE: git merge --no-edit {worktree['branch']} && "
                 f"git worktree remove {worktree['path']} && "
                 f"git branch -d {worktree['branch']}", False, -1))
        elif merge == "conflict":
            c2_blocks.append(
                (f"MERGE: CONFLICT -- contract overlap with "
                 f"{worktree['branch']}; escalate, do not force", False, -1))

    graph_line = ctx.get("graph_line")
    if graph_line:
        c2_blocks.append((graph_line, True, 2))

    refs_added = ctx.get("refs_added") or []
    refs_result = refs.refs_line(refs_added)
    if refs_result:
        c2_blocks.append((refs_result, True, 3))

    cost_usd = float(ctx.get("cost_usd", 0.0))
    executor = ctx.get("executor")
    if cost_usd > 0:
        c2_blocks.append(
            (f"COST: ${cost_usd:.4f} ({executor or 'unknown'})", True, 4))

    burn = burn_line(ctx)
    if burn:
        c2_blocks.append((burn, True, 5))

    # Insert C2 blocks before the "--- qwen result ---" line.
    tail = [f"--- qwen result ---\n{truncate(strip_handoff(result_text), RESULT_CAP)}"]

    def _assembled():
        parts = list(body)
        for blk, _, _ in c2_blocks:
            parts.append(blk)
        parts.extend(tail)
        return "\n".join(parts)

    # Cap enforcement: drop droppable C2 blocks in reverse-priority order.
    def _enforce_cap(receipt):
        if len(receipt) <= 3000:
            return receipt
        # Collect droppable blocks sorted by priority descending (drop highest number first).
        droppable = sorted(
            [(i, line, pri) for i, (line, droppable, pri) in enumerate(c2_blocks) if droppable],
            key=lambda x: -x[2]
        )
        if not droppable:
            return receipt
        # Remove the lowest-priority droppable block and reassemble.
        idx, _, _ = droppable[0]
        c2_blocks.pop(idx)
        return _enforce_cap(_assembled())

    verdict = _enforce_cap(_assembled())

    # Logged LAST: every diff above is taken against the pre-run snapshot, so the log
    # file must not exist in the tree until they are done. (It is gitignored regardless
    # -- belt and braces, because getting this wrong would corrupt the CHANGED report.)
    cum = ctx.get("cum") or cum_zero()
    changed = 0
    if guard_on:
        post = snapshot(cwd)
        changed = len([p for p in post if post.get(p) != ctx["pre_status"].get(p)])
    write_runlog(cwd, leverage_record(
        "qwen_delegate", cwd, status, verdict, cum, ctx.get("peak", 0),
        executor=executor or "qwen-local",
        cost_usd=cost_usd,
        extra={
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
            "denials": len(denials or []),
        },
    ))
    return verdict
