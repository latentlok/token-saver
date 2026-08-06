# Architecture

High level. No file names, no line numbers — those rot. This is the shape.

## Three roles

**Claude plans.** It decides what to build and, more importantly, it defines what
"correct" means for this piece of work: the command that must exit 0, the files that
may change, the shape of the answer it needs back.

**A free local model works.** It reads the code, writes the code, and runs its own
loop until it thinks it is done. Its opinion of its own work is never used.

**A small referee runs the gate and reports.** It is not a model. It is plain code
that runs the caller's command, compares the tree before and after, classifies what
happened, and writes a receipt.

The saving is not that the local model is smarter. It is that judgment stays with the
expensive scarce thing and typing goes to the cheap plentiful thing.

## Why the gate comes from a different hand than the builder

This is the founding idea. Everything else follows from it.

A worker that writes its own test writes your brief restated as an assertion. If the
brief was wrong, the test now defends the defect, and it defends it in green. A worker
that also grades itself has closed the loop on its own understanding, and no amount of
confidence in the reply tells you anything, because the confidence was produced by the
same process as the mistake.

So the gate — the command whose exit code decides the run — is authored by the
planner, not the builder. The worker cannot edit it; edits to protected test files are
reverted and fail the attempt. The worker's prose is never evidence. Only the exit code
is.

The system has a mode where the worker does write its own suite, for low-stakes work.
It is not a loophole, it is a declared trust setting, and any receipt from such a run
says so on its own line. A green receipt there means "the worker's suite passed", which
is a smaller claim, and the receipt makes you read it.

The same rule applies inside this repo, to the people building it: never delegate the
spec for a change to the model making the change.

## Three kinds of check, and they are not interchangeable

Plain English: one says **don't start**, one says **try again**, one says **for your
information**.

**Refusals — before anything is built.** These run before the worker is spawned, or
before its first attempt. They kill the whole run and nothing is built. Some are
argument-shape mistakes answered in milliseconds. Some cost real work: running the
gate once up front to check it is usable, or asking the worker to read the code and
object to the brief. If a refusal fires, you have spent little and learned that the
ask itself was wrong.

**Attempt guards — fail one attempt, the worker retries.** The worker edited a
protected test, or wrote outside the files it was allowed to touch, or produced a
fixture with no stated origin. The offending edits are reverted where reverting is
safe, one correction goes back to the worker, and the loop continues. These are not
verdicts. They are feedback with teeth.

**Advisory gates — report only.** Extra commands you can attach that run once after
the final attempt. They never change the status, never enter a retry prompt, and never
reach the worker. A red one glows in the receipt and nothing else happens. Use them for
things you want to see but do not want to block on.

Confusing the three is the most common way to misread this system. A refusal means
nothing was built. A guard means an attempt was thrown away. An advisory means someone
should look.

## Isolation, and git as the undo

Work can run in an isolated git worktree on its own branch. Nothing lands in your
working tree while the run is happening; the receipt hands you a one-line merge
command if it went green, and the branch is simply dropped if it did not.

Even without a worktree, git is the safety net. There is no sandbox, and the worker
runs at your privilege. That is why an uncommitted tree is dangerous and why the tool
refuses to work in a directory that is not a git repo at all: without history there is
no rollback and no way to tell what the worker changed from what you changed.

## The output is a receipt, not the work

The caller reads a receipt, not a diff. That is the whole economic point — reading the
diff costs exactly the tokens the delegation was supposed to save.

A receipt is a short, hard-capped block of lines: the status, how many attempts, what
changed, what the gate said, what the run cost and what reading the receipt cost you.
When it gets too long, low-value lines are shed first — but lines that make a claim
about safety are never shed, because a caller cannot tell a suppressed warning from no
warning at all. When something is dropped, the receipt says which kinds of check went
silent, so silence is never mistaken for a clean bill.

Calls submit and return in seconds with a run id and the path the receipt will land at.
The acknowledgement is not a result. Reading it and moving on is reading nothing.

## What it does not do

It does not make a weak model into a strong one. It makes a weak model's output
checkable, which is a different and smaller claim. If your gate is weak, the whole
thing is weak — a green receipt on a vacuous test proves the test was vacuous, and the
system will tell you it was vacuous only in the specific cases it can detect. It cannot
tell you the requirement was wrong; the brief-challenge pass tries, and it only refuses
when the worker can point at a real file that contradicts you. It is not a sandbox: the
worker runs with your permissions and can be given shell. It does not manage your
branches, review your design, or decide what is worth building. And it is not free of
your attention — it is designed so that your attention is spent on a receipt instead of
a diff, not so that it is spent on nothing.
