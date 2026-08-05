#!/usr/bin/env python3
"""What was ASKED FOR: settings resolved once, in one place.

Step 6 of docs/DESIGN-modular-architecture.md §5. Four layers of precedence --

    call argument  >  project .qwen-delegate.json  >  machine config  >  builtin

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
