#!/usr/bin/env python3
"""The fixed phase sequence, and the logic inside it that is worth naming.

DESIGN §5. Started, not finished -- and the distinction matters to whoever picks
this up. `core/status.py` and `features/guards/` took the two phases that were
whole ideas; what remains in `_delegate` is largely ORCHESTRATION, which is the
loop's own job and does not improve by being moved.

What DOES belong here is the logic those phases carry that is neither
orchestration nor a feature: decisions with rules of their own, currently
readable only by tracing the loop.

`ratchet_minimum` is the first. The rest of the preflight -- run the gate, share
the verdict across items, time it -- is sequencing, and sequencing that reads
fine where it is.

Four more followed. Each was found inline in `_delegate` carrying a real rule,
no assertion anywhere in the suite, and a failure mode that prints a plausible
number rather than an error -- which is how they became invisible:

    preflight_shareable   who may reuse a gate verdict another item paid for
    graph_shell_grant     a permission boundary, previously pinned by GREP
    peak_high_water       reported peak context is a MAX, not the last attempt
    gate_is_slow          "slow" is past HALF the budget, not most of it

`preflight_shareable` is the fine line against the paragraph above, and it is
worth stating: SHARING one verdict across items is sequencing and stays in the
loop; whether THIS RUN may be served one is a rule with an invariant of its own,
and the loop is the only place that knows which kind of run this is.

Every rule here is pinned twice on purpose -- specs/pipeline_spec.py for what it
decides, specs/pipeline_wiring_spec.py for the engine actually asking it. A
helper can be perfect and uncalled, and five of the nine bugs found in the round
that produced this module were exactly that.
"""

import re

# The grant owns which pattern it hands out (see `graph_shell_grant`), so the
# module that knows what graphify IS gets imported here. No cycle: qd.graph
# reaches only qd.gittree and qd.runlog, neither of which imports qd.core.
from qd import graph

# Both shapes a runner reports a count in. unittest prints "Ran 12 tests",
# pytest prints "12 passed"; a project may run either, and a ratchet that
# understood one would silently not ratchet under the other.
_COUNT = re.compile(r"Ran (\d+) tests?|(\d+) passed")


def ratchet_minimum(preflight_out):
    """How many tests a self-written gate must require, given what already passes.

    The problem it solves: under `trust="self"` the server writes the gate, and
    an existing suite is ALREADY GREEN. A gate that just runs it proves nothing
    about the new work, and every later feature would come back
    `success_but_preflight_passed` -- a real delivery reported as a vacuous one.

    So the gate binds on the DELTA: require more tests than the preflight found,
    and the preflight re-runs red, which is what makes the eventual pass mean
    something.

    **Summed across files, not taken from the first.** A multi-file suite prints
    one count per file, and a ratchet set from the first line would demand one
    more test than the first FILE contains -- a threshold the suite already
    clears, which is the vacuous pass this exists to prevent, restored by the
    fix meant to remove it.
    """
    found = sum(int(a or b) for a, b in _COUNT.findall(preflight_out or ""))
    return found + 1


def preflight_shareable(has_container, chain_pos):
    """Whether this run may be SERVED a gate verdict another item paid for.

    `_preflight_once` shares one gate run across items keyed on (base sha,
    worktrees dir, gate), and states its own invariant: every item is cut from
    the SAME base commit into its own clean worktree, so the answer is identical
    for every item by construction.

    True for a batch. **FALSE for a chain link after the first**, whose tree
    deliberately holds the earlier links' commits -- that dependency IS the
    chain. Reuse the verdict there and link 2 is graded on link 1's gate run,
    taken against a tree that did not yet contain link 1's work, and the answer
    looks exactly like a real one. Tiers make the collision likelier rather than
    rarer: once projects declare `tests`, every pipeline's gate becomes the same
    command string, so concurrent chains off one base share a key while holding
    genuinely different trees.

    The container half is the other invariant. With no worktree the working tree
    can have moved between two runs, so a cached verdict is a claim about a tree
    nobody re-read.

    **Absence of a position reads as the head of a chain**, and it is normalised
    HERE rather than at the call site so both spellings are correct wherever
    this is called from. A plain delegation and a batch item carry no chain
    position at all; reading that as "not link 1" would switch sharing off for
    every batch -- N gate runs where A13 pays for one, which is the whole cost
    this cache exists to remove.
    """
    return bool(has_container) and (chain_pos or 1) == 1


def graph_shell_grant(approval_mode, has_graph, shell_allow):
    """`shell_allow` with the graph's READ-ONLY subcommands added, or unchanged.

    `graph.bootstrap_line()` PROMISES the caller that the worker will locate
    code through the graph instead of reading files. Nothing made that true: the
    worker has no shell unless one is granted, so the promise was kept only for
    callers who happened to wire `shell_allow` themselves.

    Three conditions, and PRINCIPLES §III asks of every allowlist what the most
    powerful thing reachable through it is:

      scoped only   `auto-edit` has no shell at all, so a pattern granted there
                    is a permission that reads as GIVEN and does nothing --
                    worse than absent, because a caller would believe the worker
                    could use the graph. Every wider mode is a boundary nobody
                    decided to move.
      a graph only  no sidecar, nothing to read.
      never twice   a caller, or a project config, may already carry the
                    pattern. A second copy widens nothing and makes QGATE_EXTRA
                    unreadable at exactly the moment somebody is auditing it.

    **The pattern is not a parameter.** `graph.read_only_allow()` is called from
    inside, so the "never `update`" half of the rule -- on a repo with a semantic
    index `graphify update` can bill a cloud account, and the plugin runs it
    AFTER the verdict on terms somebody chose -- lives with the decision instead
    of back at a call site, which is where it was invisible.

    **Declining returns the input UNCHANGED, including `None` -> `None`.**
    qd/core/plan.py keeps "the caller said nothing" and "the caller said none"
    as different answers, and handing back `[]` would erase that distinction on
    every run that gets no grant.

    Pinned by what it DECIDES. It used to be pinned by grepping qd/engine.py for
    the `if` line, and the mutation run of 2026-08-06 measured what that was
    worth: a widening that kept the grepped line verbatim and added a branch
    granting this pattern under `auto-edit` and `yolo` left the entire suite
    green. The gate could not see the thing it named.

    **The engine tests `scoped` at its call site TOO, and that is not a leftover
    to tidy away.** Asking whether a graph exists is not free and not safe:
    `graph.read_state` reaches `runlog_dir`, which creates `.qwen-delegate/` and
    can raise `PermissionError` from outside its own try. So the cheap half of
    the condition guards the expensive half there, while the rule -- all three
    conditions, and which pattern -- stays here where it can be asserted on.
    """
    if approval_mode != "scoped" or not has_graph:
        return shell_allow
    ro = graph.read_only_allow()
    # `shell_allow or []` and not `shell_allow`: None is a legal input here and
    # the membership test must not raise on it.
    if ro in (shell_allow or []):
        return shell_allow
    # A new list, never `.append`: the caller's list may be a project config's
    # own object, and widening it in place would edit permissions for whoever
    # reads that config next.
    return list(shell_allow or []) + [ro]


def peak_high_water(previous, observed):
    """The largest context any attempt reached -- a MAX, never the latest figure.

    A run whose attempt 1 nearly compacted and whose attempt 3 was small is
    precisely the run a caller needs told about, and taking the last attempt's
    number erases it from all three places the figure exists to be read: the
    APPROACHING COMPACTION warning, the RUN line's `peak N% ctx`, and the
    ledger's peak-ctx record. Every one of them keeps printing a number, so
    nothing looks broken.

    **`None` or 0 on either side is "not known", never "the context shrank".** A
    streamed run whose usage lines were unparseable reports nothing, and letting
    that overwrite the mark would lose it to a parsing gap rather than to a real
    measurement.
    """
    return max(previous or 0, observed or 0)


def gate_is_slow(gate_ms, verify_timeout_sec):
    """Whether the pre-flight burned more than HALF its budget.

    The reason is arithmetic: the same command runs AGAIN after every attempt,
    so at max_iter 3 a gate past half its budget can outlast the work it is
    grading.

    `verify_timeout_sec` is seconds and `gate_ms` is milliseconds, so the
    conversion (x1000) and the halving fold into one constant -- **which is
    exactly why x100 and x1000 would look equally plausible while being wrong by
    a factor of ten in opposite directions**. x100 puts the warning on ordinary
    runs, and a warning that fires on runs that are fine is one nobody reads on
    the run that mattered; x1000 only warns once the gate is about to time out,
    by which point U3.1's refusal handles it and the flag has nothing left to
    say. The engine test that existed stubbed the gate at 90% of budget, where
    all three constants agree, so "half" survived only in prose until
    specs/pipeline_spec.py pinned both sides.

    Past half is STRICT: half itself is not yet slow.

    The threshold and nothing else. `bool(verify)` -- whether there is a gate at
    all -- stays at the call site, because that is orchestration, and keeping
    orchestration out is this module's whole reason to exist.
    """
    return gate_ms > verify_timeout_sec * 500
