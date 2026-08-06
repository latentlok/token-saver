#!/usr/bin/env python3
"""Which suite grades this run. Frozen by specs/tiers_spec.py.

A3 (DESIGN-v06-test-first §2.2). A project declares its suites once:

    "tests": {"unit": "pytest tests/unit -q",
              "integration": "pytest tests/integration -q"}

**Declared, never guessed**, and the asymmetry IS the argument. Guessing a
single gate command fails VISIBLY -- wrong command, obviously broken gate,
noticed on the first run. Guessing a TIER MAP fails SILENTLY: mislabel the
integration suite as `unit` and every delegation either eats the wall clock or
refuses on GATE UNUSABLE; mislabel a subset as "the unit tier" and you
under-gate forever with no symptom at all.

`detect_test_cmd()` is NOT replaced. It stays the fallback for untiered projects
and for the `unit` tier itself. The change is narrow: a declared map wins, and a
project that declares nothing while the work CROSSES A SEAM is refused with the
question rather than gated on a guess.

Refusing with the question has precedent twice here -- `trust: "auto"` refuses a
bare call so the orchestrator picks, and `_shape_refusal` refuses `chain`+`batch`
by name before anything spawns. Over stdio, "ask" means "refuse and say what to
send".
"""

# Cheapest first. The ORDER is the rule, not formatting: a run is gated by the
# cheapest tier that can judge it, because a gate nobody will wait for is a gate
# that gets switched off.
ORDER = ("unit", "integration", "e2e")


def declared(cfg):
    """The project's tier map, or {} -- validated, never trusted.

    A malformed entry is DROPPED rather than raised on: a typo in one tier must
    not cost a caller every delegation, and the tiers that ARE well-formed
    remain better than the guess they replace.
    """
    raw = (cfg or {}).get("tests")
    if not isinstance(raw, dict):
        return {}
    return {k: v.strip() for k, v in raw.items()
            if k in ORDER and isinstance(v, str) and v.strip()}


def gate_for(cfg, crosses_seam=False):
    """Return (command, tier, refusal). At most one of command / refusal is set.

    `crosses_seam` says the work reaches past its own module -- exactly where a
    unit suite is the WRONG grader and nobody can tell from a green receipt.
    """
    tiers = declared(cfg)
    if not tiers:
        if crosses_seam:
            return None, None, (
                "TIERS UNDECLARED: this task crosses a seam, so a unit suite "
                "cannot prove it works -- and this project has not said which "
                "suite is which.\n\n"
                "Nothing was built. Add to .qwen-delegate.json:\n\n"
                '    "tests": {\n'
                '      "unit":        "<command>",\n'
                '      "integration": "<command>"\n'
                "    }\n\n"
                "Declared, never guessed: a wrong single command is obvious on "
                "the first run, but a wrong tier map under-gates silently and "
                "forever.")
        # No map, no seam: the ordinary case, where the existing detector is a
        # better answer than a refusal.
        return None, None, None
    for tier in ORDER:
        if tier not in tiers:
            continue
        if crosses_seam and tier == "unit" and len(tiers) > 1:
            # A declared map that HAS a wider tier: use it. The cheapest tier
            # that can JUDGE this work is not the unit one.
            continue
        return tiers[tier], tier, None
    return tiers[ORDER[0]], ORDER[0], None
