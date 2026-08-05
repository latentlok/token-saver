#!/usr/bin/env python3
"""Everything a detector needs that is NOT a fact. Scaffolding, with an expiry.

The design says a detector is `facts -> Finding | None`. Five of five detectors
need more than that, and pretending otherwise would mean lying about where the
extra inputs come from:

    scope                     what this run OWNS      -> step 5, DONE
    created                   run attribution         -> step 5, when writes/
                                                         hooked move too
    verify, task, touch_scope what was ASKED FOR      -> step 6

`verify` is the gate command and `task` is the brief: neither is an observation
about the code, so neither belongs in the facts record. `created` depends on
`writes` and `hooked` -- run attribution, which step 5 owns -- so it is computed
by the engine and handed over rather than gathered here.

**It is already shrinking, which is the point.** It began at seven fields;
`work_cwd`, `pre_status` and `pre_sha_full` left together when `core/scope.py`
gave them an owner, and they are now reached through `scope`. Four remain.

**This type is temporary and must stay small.** `core/scope.py` now exists and has already
taken three fields; `core/plan.py` (step 6) takes three more, and when
`created`'s attribution follows them this file is deleted. The danger
is obvious and worth naming: a general-purpose bag passed to every feature is
exactly `ctx` with a nicer name, and rebuilding `ctx` is the one thing the
restructure exists to prevent. Two properties keep it honest -- it is FROZEN, so
a detector cannot add to it, and its field list is CLOSED and short, so adding
one is a visible diff in a single place. If this file starts growing, the answer
is to finish step 5 or 6, never to add a field.
"""

from typing import NamedTuple


class DetectorInputs(NamedTuple):
    scope: object        # RunScope: work_cwd, pre_status, pre_sha   -- OWNED
    created: list        # files this run made, attributed    -> step 5 scope
    verify: str          # the gate command                   -> step 6 plan
    task: str            # the brief, as the caller wrote it  -> step 6 plan
    touch_scope: list    # paths the caller declared in scope -> step 6 plan
