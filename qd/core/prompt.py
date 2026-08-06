#!/usr/bin/env python3
"""What the worker is actually sent. Frozen by specs/prompt_spec.py.

The Decorator, and the last of the five patterns in DESIGN §7. What was
scattered was never the STRINGS -- those live sensibly in `qd/verdict.py`
beside the parsers that read them back. It was the CONDITIONS: five separate
`if` statements across three files, each deciding whether one layer applies, and
no single place that could answer *what is this worker about to be told?*

    prefix layers   shell feedback, the challenge clearance, the chain preamble
    the task        what the caller wrote
    suffix layers   HANDOFF, FINDINGS, the result schema

Two rules that were implicit in the order of those `if`s and are now stated:

  1. **A suffix layer rides the HANDOFF tail or it is not sent at all.** FINDINGS
     and the schema are machine-read lines appended to the same block the parser
     already reads. Sending them without the handoff tail would put a
     machine-read instruction somewhere nothing looks.
  2. **The tail is sent on attempt 1 and after a compaction, and otherwise not.**
     Those are exactly the moments the worker has no other way to know what
     shape its answer must take. Re-sending it every attempt is tokens spent
     re-teaching something already known.

Prefixes go BEFORE the task and suffixes after, and that is not cosmetic: a
prefix is the SITUATION the worker is in (its last command was denied; the
brief was cleared to build) and has to be read before the instruction it
qualifies. A suffix is the REPORTING CONTRACT and belongs at the end, where the
worker is about to answer.
"""


def compose(task, prefixes=(), suffixes=()):
    """The whole prompt, in one place.

    Empty layers are dropped rather than joined, so nothing contributes stray
    separators -- a prompt whose shape depends on which optional layers happened
    to be present is one nobody can predict from reading the code.
    """
    parts = [p for p in prefixes if p]
    parts.append(task or "")
    parts.extend(s for s in suffixes if s)
    return "".join(parts)


def tail(handoff, *, wanted, findings=None, schema=None):
    """The machine-read block appended to the worker's instructions.

    `wanted` is the attempt-1-or-after-compaction decision, made by the loop
    that knows the attempt number and the compaction state. Passing it in keeps
    this a pure function of what to send rather than of when.

    Returns "" when not wanted -- and the rider layers go with it, because a
    machine-read instruction with nowhere to be read is worse than absent: it
    spends tokens and creates the impression the contract was stated.
    """
    if not wanted:
        return ""
    out = handoff
    if findings:
        out += findings
    if schema:
        out += schema
    return out
