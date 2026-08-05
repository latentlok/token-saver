#!/usr/bin/env python3
"""Delivered test files the gate command does not run.

Written, never run, so nothing here proves they pass -- while the receipt is
green, because the gate ran the files it does name. The field case: gate_tests/
was authored under green delegations and first executed three weeks later, when
one of them proved unsatisfiable.

The gate command is an INPUT, not a fact -- it is what the caller asked for, not
something observed about the tree. See inputs.py.
"""

from qd.core.findings import Finding
from qd.gittree import never_executed

KIND = "never_executed"


def detect(facts, inputs):
    found = never_executed(inputs.work_cwd, facts["changed"], inputs.verify)
    return Finding(KIND, found) if found else None
