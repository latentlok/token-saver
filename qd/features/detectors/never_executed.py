#!/usr/bin/env python3
"""Delivered test files the gate command does not run.

Written, never run, so nothing here proves they pass -- while the receipt is
green, because the gate ran the files it does name. The field case: gate_tests/
was authored under green delegations and first executed three weeks later, when
one of them proved unsatisfiable.

The gate command is an INPUT, not a fact -- it is what the caller asked for,
not something observed about the tree, so it arrives on the PLAN.
"""

from qd.core.findings import Finding
from qd.surface.receipt import Block
from qd.gittree import never_executed

KIND = "never_executed"


def detect(facts, scope, plan):
    found = never_executed(scope.work_cwd, facts["changed"], plan.verify)
    return Finding(KIND, found) if found else None


def block(data):
    return [Block(KIND,
                  f"NEVER EXECUTED: {len(data)} delivered test file(s) the gate "
                  f"does not run: " + ", ".join(data[:4])
                  + (" ..." if len(data) > 4 else "")
                  + " -- written, never run, so nothing here proves they pass.",
                  True, 1)]
