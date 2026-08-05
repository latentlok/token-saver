#!/usr/bin/env python3
"""The receipt as a LIST of blocks, not a cascade of branches.

Step 3 of docs/DESIGN-modular-architecture.md §5. Before this, every feature
that wanted a line edited an 888-line function, and the receipt's droppable
region was 20 `if X: c2_blocks.append((f"...", True, 1))` statements whose text,
droppability and priority were all inline. The measured consequence is in §1:
adding one feature took five edits across four modules, and `verdict.render`
became the second god function by exactly this route.

A block carries four things, and separating them is the point:

    kind        a stable machine name, so the cap can report WHAT it dropped
                rather than only how much (the parked SUPPRESSED: line, G1)
    text        the rendered line(s) -- owned by the FEATURE, not the renderer
    droppable   whether the size cap may discard it
    priority    drop order; LOWER is kept longer (-1 outranks 7)

Two rules the old tuples encoded only by accident, written down here because
step 3 is where they became load-bearing rather than incidental:

  1. **Order of appending is order of rendering**, and it is also the cap's
     tie-break: the drop loop sorts by priority with a STABLE sort, so among
     equal priorities the block appended FIRST is dropped FIRST. That is why
     the all-green ADVISORY block is appended before STRAYS at the same
     priority -- advisory-green is the one whose absence costs least.
  2. **A non-droppable block is a claim about safety**, not importance. Red
     ADVISORY and TEST DODGE are non-droppable because their ABSENCE READS AS
     GREEN: a caller cannot tell a suppressed warning from no warning, so a
     dropped one is worse than one that was never computed.
"""

from collections import namedtuple

Block = namedtuple("Block", "kind text droppable priority")
