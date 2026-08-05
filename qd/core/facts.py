#!/usr/bin/env python3
"""Tree observations for one run, computed once. Frozen by specs/facts_spec.py.

Step 1 of docs/DESIGN-modular-architecture.md §4. The rule this module exists to
establish:

    FACTS are computed once, in dependency order, and read by everyone.
    FINDINGS are pure functions of facts. Nothing writes a fact after collect().

Why that rule. Detectors are not independent -- `never_executed` needs `changed`
AND the gate command; the seam demotion needs `pubs` AND `mocked_seams`. Let each
gather its own and the same `git` call is paid for three times. Let them share a
mutable bag and they acquire ordering dependencies on each other, hidden in the
order somebody happens to call them in. Computing once and handing out a value
removes both.

WHEN this runs is as load-bearing as what it returns, and it is the half a green
suite cannot check. It must be called from the tree the run ACTUALLY used
(worktree or main) and BEFORE any worktree commit-or-release: after a commit the
work reads as a false `COMMITTED` alarm, and after a release there is nothing
left to look at. A fact gathered a moment too late leaves the receipt green and
saying the wrong thing.

FROZEN, as of step 2. The detectors used to write their results back INTO this
dict (`tf["uncalled"] = ...`) -- the exact facts/findings confusion §4 describes,
and the thing extracting this module made visible. They now live in
qd/features/detectors/ and RETURN findings, so nothing writes here any more and
the record says so: a write raises instead of quietly succeeding.

Why enforce it rather than document it. While the write was possible, an
observation (`pubs`) and a judgement (`uncalled`) sat in one dict, indistinguish-
able to every later reader, and any detector reading a written-back key silently
depended on the order the calls happened to appear in. Comments do not stop that
coming back; a TypeError does.
"""

from types import MappingProxyType

from qd.gittree import (
    snapshot, numstat_map, committed_during_run, head_sha, new_public_symbols,
)


def collect(work_cwd, pre_status, pre_sha_full):
    """What is true about `work_cwd` now, against where the run started.

    `pre_status` is the T0 dirty snapshot; `pre_sha_full` the T0 HEAD.

    Returns a READ-ONLY mapping, or raises -- callers decide what an
    unobservable tree means.
    The engine treats it as "no facts" rather than failing the run, because a
    delivered unit whose tree could not be read is still delivered.
    """
    post_status = snapshot(work_cwd)
    # ONE snapshot, and `changed` is derived from it rather than from a second
    # read. Two reads could straddle a write and disagree, and the disagreement
    # would surface as a receipt that lists a file it also says is unchanged.
    changed = sorted(
        p for p in set(list(post_status.keys()) + list(pre_status.keys()))
        if post_status.get(p) != pre_status.get(p))
    # MappingProxyType rather than a NamedTuple: every consumer reads this with
    # [] or .get(), and buying immutability by breaking those would trade a
    # silent failure for a noisy one across the whole renderer. Safe to freeze
    # because nothing serialises this record -- engine.run() returns rendered
    # text, and the per-run telemetry in runs.jsonl is built field by field.
    return MappingProxyType({
        "post_status": post_status,
        "changed": changed,
        "numstat": numstat_map(work_cwd),
        "head_moved": committed_during_run(work_cwd, pre_sha_full),
        "head_now": head_sha(work_cwd),
        "pubs": new_public_symbols(work_cwd),
    })
