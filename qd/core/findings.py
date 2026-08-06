#!/usr/bin/env python3
"""What a detector RETURNS, instead of writing into the record it read from.

Step 2 of docs/archive/a92e876/DESIGN-modular-architecture.md §4, the other half of step 1:

    FACTS are computed once, in dependency order, and read by everyone.
    FINDINGS are pure functions of facts. Nothing writes a fact after collect().

A fact is an observation -- `changed`, `pubs`, `head_moved`. Two people looking
at the same tree at the same moment write down the same thing. A finding is a
judgement reached BY reading facts -- "this symbol is referenced by nothing",
"this test mocks the module the run just changed". Facts are the evidence;
findings are the accusations.

Why the separation is load-bearing rather than tidy. While the detectors wrote
their results back into the facts (`tf["uncalled"] = ...`), three things were
true at once: `pubs` (observed) and `uncalled` (concluded) were indistinguish-
able to every later reader; any detector reading a written-back key silently
depended on the ORDER the calls happened to appear in; and removing a detector
meant first proving nothing else had come to rely on its leftovers. A detector
that can only return a value cannot leave anything behind, so the ordering
question stops being managed and starts being absent.

`data` is deliberately untyped -- each detector's payload keeps the shape its
gittree function already returns (a dict for `uncalled` and `dodge`, a list for
the rest), because step 2 MOVES these and must not reshape them in the same
commit. A migration that also changes behaviour cannot be bisected.

No `priority` field, on purpose. The renderer still owns drop-priority and its
tie-break-by-append-order, both load-bearing and both documented at their site
in qd/verdict.py. Putting priority here while `render` still hardcodes its
cascade would create two sources of truth for one step. It lands in step 3,
when the receipt becomes a list and there is exactly one consumer.
"""

from collections import namedtuple

# `kind` is a stable MACHINE name ("uncalled", "mocked_seams"), never the
# receipt's label ("UNCALLED:", "MOCKED SEAM:"). The label is the renderer's
# business and step 3 may change it; a consumer keying off the wire text would
# break on a copy edit.
Finding = namedtuple("Finding", "kind data")
