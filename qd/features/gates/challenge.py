#!/usr/bin/env python3
"""A23: let the worker object to the brief BEFORE anything is built.

The failure it exists for: a worker-written gate is the brief restated as an
assertion, so a wrong requirement becomes a green test defending the defect --
and `preflight_expect` is blind to it, because red-before/green-after is also
what a confidently-built mistake looks like.

Refuses ONLY when the objection cites a path that EXISTS. An unverifiable
objection is an opinion, and opinions do not stop runs (PRINCIPLES §I).

The implementation still lives in qd.engine -- moving the executor call is a
step-5/6 concern, since it needs the profile and the timeout, which are scope
and plan. What lives HERE is the decision: given what the challenge pass found,
refuse or proceed.
"""

NAME = "challenge"


def check(run):
    """`run` carries `.objection` -- (why, evidence) or None."""
    from qd.features import gates
    objection = getattr(run, "objection", None)
    if not objection:
        return gates.proceed()
    why, evidence = objection
    return gates.refuse(
        f"BRIEF CHALLENGED: {why}\nEVIDENCE: {evidence}\n\n"
        "Nothing was built. The worker read the code and says the "
        "brief contradicts it -- and it cited a path that exists. "
        "Correct the brief and re-send, or drop `challenge_brief` if "
        "you have already considered this.")
