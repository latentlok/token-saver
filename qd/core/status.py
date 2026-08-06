#!/usr/bin/env python3
"""What a finished run IS. Frozen by specs/status_spec.py.

The first piece of `core/pipeline.py` (DESIGN §5), and the first VERB this
restructure has extracted. Every step before it took a NOUN -- facts, findings,
scope, plan, blocks, gates -- which is precisely why `_delegate` never shrank:
the nouns left and the sequence that orders them stayed behind.

A pure function of the attempt trail and three flags. It was an elif chain
inside a 1,135-line function, testable only by driving a whole delegation
through a stub executor, which meant its rules were asserted incidentally by
tests about other things.

**ORDER IS PRECEDENCE**, and that was encoded nowhere but in the order of the
branches. Every one is a more specific diagnosis than the one below it: a
stalled run that also violated scope is a scope violation first, because the
worker touching the gate is the more serious fact and the one the caller has to
act on.
"""

# Statuses a report run overrides. Deliberately narrow: a stopped, compacted or
# fixture-unproven run really did end for that reason, and calling it "reported"
# would hide it.
_REPORTABLE = ("success", "success_but_preflight_passed", "verify_failed",
               "stuck_no_progress", "gate_suspect", "unverified")

# Trail marker -> status, in PRECEDENCE order. A list rather than a dict because
# the order is the rule, and a dict would leave it to insertion-order accident.
_MARKERS = (
    ("RESULT SCHEMA invalid", "result_invalid"),
    ("run stopped:", "stopped"),
    ("COMPACTION", "compaction_refused"),
    ("SPEC VIOLATION", "spec_violation"),
    ("PLAYBOOK EDITED", "spec_violation"),
    ("TOUCH SCOPE VIOLATION", "scope_violation"),
    ("IDENTICAL to preflight", "gate_suspect"),
    ("FIXTURE PROVENANCE", "fixture_unproven"),
    ("no verify supplied", "unverified"),
)


def classify(trail, no_progress=False, report=False, preflight=False,
             preflight_expect="any"):
    """The run's final status.

    `no_progress` -- the last two attempts produced identical gate output (G3).
    `report`       -- a `report_dont_fix` run, where a red gate is the answer.
    `preflight`    -- the gate was already passing before the worker ran.
    """
    if not trail:
        status = "error"
    elif trail[-1].endswith(": VERIFY PASS"):
        status = "success"
    else:
        last = trail[-1]
        upper = last.upper()
        status = None
        for marker, mapped in _MARKERS:
            # A couple of markers are matched case-insensitively because they
            # are emitted in more than one casing; the rest are literal.
            if marker in last or (marker in ("COMPACTION", "SPEC VIOLATION")
                                  and marker in upper):
                status = mapped
                break
        if status is None:
            # G3: a subtype of verify_failed, and LAST because every branch
            # above is a more specific diagnosis.
            status = "stuck_no_progress" if no_progress else "verify_failed"

    # U4.2: a report run's status says what it IS, not what the gate said -- a
    # red gate there is the deliverable, and "verify_failed" would read as the
    # worker having failed at a job it was told not to do.
    if report and status in _REPORTABLE:
        return "reported"

    # U3.2 (decision 4): the demotion happens HERE, not at render time. Chains,
    # the run log and every server-side consumer read this status, and a
    # receipt-only demotion left all of them believing a vacuous pass was a
    # clean success. A declared "green" preflight is revision work, where a
    # passing gate beforehand is the premise rather than a warning.
    if status == "success" and preflight and preflight_expect != "green":
        return "success_but_preflight_passed"
    return status
