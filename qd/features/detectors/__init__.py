#!/usr/bin/env python3
"""The registry: every detector, enumerable, in one place.

Step 2 of docs/archive/a92e876/DESIGN-modular-architecture.md. What this buys, concretely --
before it, adding a sixth detector meant finding where the other five were
called (five statements inside a 1,111-line function, with nothing naming them
as a group), and removing one meant proving nothing downstream had come to rely
on what it wrote into the shared record. Now adding one is a file plus a line
here, and removing one is deleting both.

**The detectors have no order.** They are independent questions about the same
facts, so `DETECTORS` is a tuple only because something has to be written down
first. Nothing may come to depend on the order: the renderer looks findings up
by kind, and a detector cannot see another's output at all. If two ever appear
to need ordering, that is a bug -- it means they are competing over something
that should have been computed once, upstream, as a fact (§4).
"""

from . import (dodge, mocked_seams, never_executed, seam_crossed, strays,
               uncalled, unmarked_tests)

DETECTORS = (uncalled, mocked_seams, never_executed, seam_crossed, dodge,
             strays, unmarked_tests)


def run_all(facts, scope, plan):
    """Run every detector against one set of facts.

    Takes `scope` and `plan` directly. The `DetectorInputs` bag that used to sit
    here was step 2 scaffolding, introduced with its risk named out loud -- a
    general-purpose record handed to every feature is `ctx` with a nicer name.
    It went 7 fields -> 4 -> deleted, as each field found a real owner. That was
    the design's claim, and this is it discharged.

    Returns `(findings, failed)` -- the findings that fired, and the KINDs of
    detectors that raised.

    Two lists rather than one, because three outcomes have to stay
    distinguishable and a single list collapses two of them:

        fired    -> a Finding in `findings`
        silent   -> in neither list; the detector ran and found nothing
        broke    -> its KIND in `failed`; nothing is known either way

    Silence and breakage reading the same is the failure PRINCIPLES §IV names:
    a zero meaning "nothing found" and a zero meaning "nothing was measured"
    have to be distinguishable, or every zero becomes worthless as evidence.
    These are greps over a tree another process was writing moments ago, so
    breakage is a real outcome and not a theoretical one.

    Each detector is guarded individually: its failure costs its own finding and
    nothing else. Pinned by specs/detectors_spec.py, which was written against
    the old inline form first -- back then one failed grep discarded every fact
    the run had collected.
    """
    findings, failed = [], []
    for detector in DETECTORS:
        try:
            got = detector.detect(facts, scope, plan)
        except Exception:
            failed.append(detector.KIND)
            continue
        if got is not None:
            findings.append(got)
    return findings, failed


def in_region(region):
    """Every detector rendering in one region, in SLOT order.

    This is what makes "adding a detector is a file plus a line in DETECTORS"
    TRUE. It was not: the renderer named each detector's placement, so a
    detector registered without a matching render line computed a finding
    nobody ever saw -- silently, with the whole suite green.

    Placement cannot simply follow registration order, because it is the size
    cap's TIE-BREAK among equal priorities: among blocks of the same drop
    priority the earliest-appended is shed first. So each detector declares
    where it goes, and the renderer asks rather than lists.
    """
    return tuple(sorted((d for d in DETECTORS if d.REGION == region),
                        key=lambda d: d.SLOT))


def find(findings, kind, default=None):
    """The payload of one kind of finding, or `default` if it did not fire.

    Consumers ask by kind rather than by position -- position is exactly the
    ordering dependency this module exists to remove.
    """
    for f in findings or ():
        if f.kind == kind:
            return f.data
    return default
