#!/usr/bin/env python3
"""The few things that can REFUSE a run, behind one interface.

Step 4 of docs/DESIGN-modular-architecture.md §5. A gate answers exactly one
question -- **refuse, or proceed?** -- and a gate is a module with:

    NAME            a stable machine name
    check(run)      -> Decision

**What is deliberately NOT a gate.** `advisory_gates` runs commands and reports
pass/fail, so it looks like one, but it can never refuse: it never touches
STATUS and never reaches the worker. §5 keeps gates and detectors apart because
refusing and reporting are different powers, and a single interface would force
the reporting side to carry a veto hook it must always decline to use. That is
a fat interface, and one that invites a reporter to start refusing things. The
same argument applies here, so advisory stays where it is.

That distinction is load-bearing beyond tidiness. G4 (the parked brief-vs-diff
review) is a WITNESS -- an opinion about whether the work matches the brief --
and PRINCIPLES §I is explicit that the verdict is a command's exit code and
never anybody's account of the work. Keeping "can refuse" a property of the
TYPE means a witness cannot quietly acquire the power by being registered in
the wrong list.
"""

from collections import namedtuple

from . import challenge

_Decision = namedtuple("Decision", "ok reason")

# Registration is the whole interface. A1's parked red gate is a file here plus
# one line below; nothing in the engine changes to accept it.
GATES = (challenge,)

# What a gate is asked ABOUT. Scaffolding, exactly like the detectors'
# DetectorInputs: one closed field today, and it grows only until steps 5 and 6
# give it a real owner (`core/scope.py` / `core/plan.py`). A gate must not be
# handed a general-purpose bag -- that is `ctx` with a nicer name, which is the
# thing this restructure exists to remove.
GateRun = namedtuple("GateRun", "objection")


def proceed():
    """Nothing here objects. Carries no reason -- there is nothing to say."""
    return _Decision(True, None)


def refuse(reason):
    """Stop, and tell the caller what to change.

    A reason is REQUIRED. A refusal is the most expensive answer a caller can
    get -- it costs them the whole run and hands back nothing -- so one that
    cannot be acted on wastes exactly the time refusing was meant to save.
    """
    if not (reason or "").strip():
        raise ValueError("a refusal must say why")
    return _Decision(False, reason)


def run_all(registry, run):
    """Ask each gate in turn. The first refusal wins and stops the rest.

    Short-circuit on purpose: gates are expensive (`challenge` spends a whole
    executor pass reading the codebase), and once one has refused, the run is
    not happening -- a second opinion on a dead run is pure cost.

    A gate that RAISES is a broken instrument, not a verdict, so it is skipped
    rather than treated as a refusal. This is the detectors' rule (step 2) with
    higher stakes: a gate is the only thing that can stop a run before a single
    token is spent, so one that failed closed on a crash would refuse every
    delegation until somebody noticed -- and each refusal would look like a
    considered judgement about the brief.
    """
    for gate in registry:
        try:
            decision = gate.check(run)
        except Exception:
            continue
        if not decision.ok:
            return decision
    return proceed()
