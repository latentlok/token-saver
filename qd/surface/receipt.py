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


def paid_line(receipt_chars, tokens_in, tokens_out):
    """A5: the trade, on the run that made it.

    Every other number on a receipt is about the WORKER -- what it burned, how
    long it took, what it changed. This is the only one about the CALLER, and it
    is the one the whole product rests on: *judgment stays with the expensive
    scarce thing, execution goes to the cheap plentiful thing.* That trade is
    either solvent on this run or it is not, and until now nothing said which.

    `receipt_chars` EXCLUDES this line, and the text says so. Including it would
    mean the number described a receipt that only exists because the number was
    added -- and a self-referential measurement is one nobody can check.

    Silent when the worker burned nothing: a refusal or a cached run made no
    trade, and a leverage figure over zero work is a number pretending to be a
    measurement.
    """
    if not tokens_in:
        return None
    # ~4 chars per token is the same rough conversion the brief-size guard
    # uses. Approximate on purpose, and marked `~`: the point is the ORDER of
    # magnitude -- 200x versus 2x -- not a figure anyone should reconcile.
    paid = max(1, receipt_chars // 4)
    return (f"PAID: ~{paid:,} tokens of your context (this receipt, excluding "
            f"this line) for {tokens_in:,} in / {tokens_out:,} out on free "
            f"compute -- ~{(tokens_in + tokens_out) // paid:,}x leverage")
