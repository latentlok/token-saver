#!/usr/bin/env python3
"""G4: did the run build what the brief asked for?

The one question nothing else in the system answers. The gate proves the tests
pass. The detectors prove nothing was left behind, nothing is unwired, no seam
was faked. **None of them compare the delivered work against what was asked**,
and that is the gap an exit code structurally cannot close: a confidently-built
misunderstanding passes its own tests perfectly.

**It is an ADVISORY and must stay one.** PRINCIPLES §I: the verdict is a
command's exit code and never anybody's account of the work. This pass is a
WITNESS -- a second opinion from the same class of thing that produced the code
-- and a witness that can refuse a run has been promoted to a judge. Step 4 made
"can refuse" a property of the type precisely so this could not acquire the
power by being registered in the wrong list, and it is deliberately NOT in
`features/gates/`.

It rides the existing `advisory_gates` shape (name / ok / ms / head), which
already guarantees the three things that matter: it never touches STATUS, it
never reaches the worker, and its output is one line rather than a log.

OFF by default. It costs a whole executor pass on a run that has already
finished, and a check the caller did not ask for should not spend their GPU.
"""

import time

PROMPT = (
    "Compare the BRIEF against the DIFF SUMMARY below.\n\n"
    "Answer with exactly one line:\n"
    "  MATCHES\n"
    "or\n"
    "  MISSING: <the one clause of the brief the diff does not deliver>\n\n"
    "Judge ONLY whether the brief was delivered. Not code quality, not naming, "
    "not whether you would have done it differently, not tests you would add. "
    "A brief delivered in a way you dislike still MATCHES.\n"
    "If the diff plausibly delivers every clause, say MATCHES.\n\n"
    "BRIEF:\n{brief}\n\nDIFF SUMMARY:\n{diff}\n")

NAME = "brief-vs-diff"


def summarise(facts, limit=40):
    """What changed, as a list the model can read without the whole diff.

    Paths and line counts, never the content: the pass answers "was every
    clause delivered", which is a question about SHAPE, and feeding it the full
    diff would cost the context of a second delegation to answer it worse.
    """
    changed = (facts or {}).get("changed") or []
    numstat = (facts or {}).get("numstat") or {}
    lines = []
    for path in changed[:limit]:
        add, rem = numstat.get(path, (0, 0))[:2] if path in numstat else (0, 0)
        lines.append(f"  {path}  (+{add}/-{rem})")
    if len(changed) > limit:
        lines.append(f"  ... and {len(changed) - limit} more")
    return "\n".join(lines) or "  (no files changed)"


def review(ask, brief, facts):
    """Run the pass. `ask` is a callable taking a prompt and returning text.

    Returns an advisory record, or None when there is nothing to compare --
    a run that changed no files has no delivery to judge, and an advisory that
    fires with nothing to say is one nobody reads.
    """
    if not brief or not (facts or {}).get("changed"):
        return None
    t0 = time.time()
    try:
        reply = ask(PROMPT.format(brief=brief[:4000],
                                  diff=summarise(facts))) or ""
    except Exception as e:
        # A broken advisory must never look like a finding about the work.
        return {"name": NAME, "ok": True, "ms": int((time.time() - t0) * 1000),
                "head": f"skipped ({type(e).__name__})"}
    head = next((l.strip() for l in reply.splitlines() if l.strip()), "")
    return {"name": NAME,
            # Anything that is not an explicit MISSING is treated as a match.
            # An unparseable answer is not evidence of a defect, and defaulting
            # the other way would make every parser hiccup look like one.
            "ok": not head.upper().startswith("MISSING"),
            "ms": int((time.time() - t0) * 1000),
            "head": head[:200]}
