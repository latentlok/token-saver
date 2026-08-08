#!/usr/bin/env python3
"""What was ASKED FOR: settings resolved once, in one place.

Step 6 of docs/archive/a92e876/DESIGN-modular-architecture.md §5. Four layers of precedence --

    call argument  >  project .delegation.json  >  machine config  >  builtin

-- were resolved inline at ~20 sites, and two of them (`trust`,
`preflight_expect`) were resolved TWICE, once in the preconditions and again in
the run. Duplicated precedence is not a tidiness problem: the two copies are one
edit away from disagreeing, and nothing would report the disagreement.

**The rule the whole module exists for:**

    None means "this layer did not answer".
    Every other value -- INCLUDING false, 0 and [] -- is an answer.

Most of the old sites were written `args.get(x) or cfg.get(x)`, which is correct
right up until the caller's answer is falsy, and then silently replaces a
deliberate choice with a default. The engine already knew this in one place --
`challenge_brief` resolves with explicit None checks and says why: *"`false` is a
real answer, and `or` chaining would fall through it to the next layer and
silently re-enable what the caller just switched off."*

The permission lists never got that treatment, and for them `[]` is the most
deliberate answer a caller can give -- *no extra capability at all*. A caller
passing `shell_allow=[]` against a project declaring `["^rm\\b"]` received
`["^rm\\b"]`: an explicitly narrowed boundary, silently widened. PRINCIPLES §III
asks of every allowlist *what is the most powerful thing reachable through the
things I permit* -- and there, the answer was "whatever the project happened to
declare", regardless of what the call said.

Resolving through `setting()` makes that class of bug unwritable rather than
merely known about.
"""

from typing import NamedTuple

_UNSET = object()


def setting(name, *layers, default=None):
    """One setting, resolved through the layers in precedence order.

    `layers` are dicts, most-specific first. A layer that is None or missing the
    key is skipped; a layer holding None for the key has NOT answered, which is
    how "the caller said nothing" stays distinct from "the caller said no".
    """
    for layer in layers:
        if not layer:
            continue
        value = layer.get(name, _UNSET)
        if value is not _UNSET and value is not None:
            return value
    return default


class RunPlan(NamedTuple):
    """WHAT WAS ASKED FOR, resolved once and frozen.

    The Builder half of step 6. `setting()` above removed the duplicated
    precedence; this removes the reason features had to carry loose arguments
    around at all.

    Why frozen, and why a record rather than the dict it is built from: this is
    the run's brief, and a brief that can be edited downstream is not a brief.
    The same argument as `core/facts.py` -- an observation nobody can rewrite --
    applied to intent instead of evidence. A feature reading `plan.verify` is
    reading what the CALLER asked for, not what some earlier feature decided it
    should now be.

    Deliberately small. It holds what features actually consume, not all ~20
    settings the engine resolves: a record that mirrors the whole config is a
    config parser with extra steps, and the next person to add a setting would
    add it here out of symmetry rather than need.
    """
    task: str            # the brief, as the caller wrote it
    verify: str          # the gate command
    touch_scope: list    # paths the caller declared in scope
    trust: str           # "self" | "verified"
    preflight_expect: str  # "red" | "green" | "any"
    fixture_provenance: bool   # U3.3 check on, or off
    fixture_segments: tuple    # path segments that mark a fixture
    brief_path: str            # the document that briefed this run, or None
    contract_path: str         # the criteria document, or None (A2/A4)

    @classmethod
    def build(cls, args, project, machine, fixture_default=(),
              brief_path=None):
        """Resolve every layer once, in precedence order.

        A classmethod rather than a separate Builder class: the pattern's value
        here is one place that knows the layers, and a class whose only method
        is `build()` is a function wearing a costume.
        """
        return cls(
            task=args.get("task"),
            verify=setting("verify", args, project, machine),
            touch_scope=setting("touch_scope", args, project, machine),
            trust=setting("trust", args, project, machine, default="self"),
            preflight_expect=setting("preflight_expect", args, project, machine,
                                     default="any"),
            fixture_provenance=bool(setting("fixture_provenance", args, project,
                                            machine, default=False)),
            fixture_segments=setting("fixture_globs", project, machine,
                                     default=fixture_default),
            brief_path=brief_path,
            contract_path=setting("contract", args, project, machine),
        )
