---
preflight_expect: red
---
# Make the gate pass

A failing test already exists. Your job is to make it pass **without touching
it**.

## What you are given

- The contract: `{{contract}}`
- The gate you are graded by: `{{test_path}}`

Read both. The test file is the authority on what "done" means; the contract is
why.

## The rule that matters

**Do not modify `{{test_path}}`, and do not modify the contract.** They are
protected — edits are reverted automatically and the attempt fails. That is not
a formality: a gate written by the thing being graded is not a gate, and the
whole value of this step is that the criteria were fixed before you started.

If the test looks wrong, **stop and say so**. That is a real and useful answer.
It is not a reason to edit it.

## How to work

1. Run the gate. Read the actual failure, not what you assume it will be.
2. Write the smallest implementation that addresses that failure.
3. Run it again. Repeat.

If two attempts produce the **same** failure, stop changing the implementation
and re-read the test — you are solving a different problem from the one it
states.

## Scope

Build what the contract asks for and nothing beside it. Extra public functions,
speculative options and second copies of existing modules all get reported, and
each one is something a reviewer has to rule out.

Files you create that the task never mentioned are flagged as debris. If you
need a scratch file, name it `*_qwen.*` — that is the sanctioned convention and
it is how a later reader can tell your tests from the ones that grade you.

## Report

State: which clauses you implemented, anything in the contract you could not
satisfy, and the gate's final output.
