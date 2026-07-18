# Principles

The transferable layer. [FINDINGS.md](FINDINGS.md) records *what happened* — every
measurement, with the numbers that bought it. This file records *what it means*, in a form
that survives the details.

These are not maxims about "AI safety." They are the small number of structural rules that
kept producing themselves, from different directions, whenever this system was pushed. The
engine underneath is domain-agnostic — it runs a task and a gate, not "code" — and so are
these.

One sentence generates the rest:

> **Put each decision where it cannot be faked.**

Everything below is a corollary.

---

## I. Where a decision lives determines whether it can lie

**Nobody is a reliable witness to their own success.**
A ship's navigator working by dead reckoning gives you a position that is confident,
internally consistent, and possibly a hundred miles wrong. Not dishonesty — the error is
unobservable from inside the ship. You want a fix taken against something outside it. So
the verdict on a task is never the worker's account of the task; it is a command whose exit
code the worker does not author.

**Two things from one source agreeing is not corroboration.**
If the builder also writes the building inspection, a misunderstanding lands identically in
the wall and in the checklist. They agree perfectly. They are both wrong. This is why the
thing that defines *correct* must come from a different hand than the thing being judged —
not because either party is untrustworthy, but because a shared misconception is invisible
to everyone who shares it.

**Honesty is a property of the situation, not the character.**
The same worker, asked the same question about the same contradictory requirement, answers
honestly when it cannot act and games the requirement when it can. Nothing about it
changed; the room did. People are candid right up to the moment candour costs them
something — so move the question to where acting on a lie is structurally impossible, and
ask it *before* you open the door.

The practical shape: **ask read-only, decide yourself, then let it build against a gate you
wrote.**

**A rule can be the cause of a virtue.**
Guardrails do not make better drivers; they make the cliff unreachable. When a system
behaves well, the interesting question is never "is it well-behaved?" but "which structure
is producing this?" — because that structure is load-bearing, and removing it silently
removes the behaviour. Attributing to character what is actually produced by a rule is how
you end up deleting the rule as redundant.

---

## II. An instrument you have not challenged is decoration

**An untested gate is a hope.**
A smoke detector nobody has ever pressed the button on is not a safety device. It is a
plastic disc that resembles one. The only way to learn that a check works is to break the
thing it watches and confirm it screams. Suites of hundreds of real tests have sailed
through mutations that changed every error message a library emits.

**A green light means nothing if it was green before you started.**
A doctor who takes your temperature only *after* the medicine has learned nothing about the
medicine. Always establish the failing state first; a pass is evidence only in contrast to
a fail.

**When nothing you do moves the needle, suspect the needle.**
Pushing harder on a door marked *pull* is not a diligence problem. If the same input
produces the same output before and after real work, the measurement is broken, and
iterating against it is pure expenditure. Bail and fix the instrument.

**A test can be green and blind.**
An alarm tested only by walking through the front door tells you nothing about the windows.
Passing tests are not evidence of coverage — they are evidence that *the paths you thought
of* behave. What a check does not assert is invisible, including the ordering assumptions
buried inside the check itself. The only cure is adversarial: break it deliberately and
watch what survives.

---

## III. Boundaries are drawn around capabilities, not around words

**A denylist gates strings; an attacker gates outcomes.**
A bouncer holding a list of banned names, working a door whose fire exit is propped open,
is enforcing the list perfectly and accomplishing nothing. Restrictions expressed as
forbidden *commands* are bypassed by any permitted command that can be pointed at
attacker-supplied content. If a worker may write a file, and may run any tool that executes
files, then it may run anything — and no amount of tightening the vocabulary changes that.

Ask of every allowlist: *what is the most powerful thing reachable through the things I
permit?* That, not the list, is the real boundary.

**Ambiguity is filled with confidence, not questions.**
Tell a contractor to "make the kitchen nicer" and leave for a month, and you will return to
*a* kitchen. Underspecified work does not come back as a question; it comes back as
somebody else's decision, already made and already built. An unspecified task also cannot
be gated — there is nothing to check it against — so vagueness defeats every other
protection at once. This is the root cause that most other failures reduce to.

---

## IV. What you do not measure, you are guessing about

**A claim you have never counted is an anecdote.**
A shop certain that business is good, which has never once counted the till, is not lying.
It simply does not know. A headline number measured once, by hand, in favourable
conditions, is a story about the past — not a property of the system.

**An instrument that can fail silently eventually will, and you will not notice.**
A fuel gauge reading *full* because it is disconnected is worse than no gauge, because it
is trusted. Any metric that can be *absent* must be able to say so: a zero that means
"nothing happened" and a zero that means "nothing was measured" have to be
distinguishable, or every zero becomes worthless.

**The observer must not disturb the observed.**
A thermometer with enough mass to cool the water is measuring itself. Instrumentation
written into the same space it reports on — a log inside the tree whose changes it
attributes — corrupts the very reading it exists to take. Record from outside, or make the
record structurally invisible to the thing doing the attributing.

**Records are written by survivors.**
A process that dies mid-flight leaves no entry, so the ledger systematically omits the
runs that went worst. Any measurement taken from completed work is biased toward
completion; treat the totals as a floor, not a figure.

---

## V. Testing something that reads everything

**Your fixture is part of the input.**
A blind taste test with the labels still on the bottles measures label-reading. When the
subject under test reads the file, the comment, the commit message and the history, then
every one of those is your experimental setup — and a fixture that announces its own trick
gets you a result about announcements. This is a failure mode with no analogue in ordinary
testing, where the code under test cannot read your intentions. Assume the subject sees
everything you leave lying around, because it does.

**Distinguish "the system passed" from "the test never ran."**
A protection that never fires because the thing it guards against never happened has not
been demonstrated. To learn whether the second lock works, you must remove the first — and
that experiment is worth running deliberately, in isolation, on something you can throw
away.

---

## VI. What this buys

The point of all of the above is a trade that only works if each piece sits where it cannot
be faked: **judgment stays with the expensive, scarce thing; execution goes to the cheap,
plentiful thing; a command decides who was right.**

When that holds, the expensive party stops reading the work and starts reading verdicts,
and the cost of being thorough collapses — measured on this system at two to three hundred
times more work processed than ingested. When it stops holding, the whole arrangement
inverts: you get a confident report, a green light, and no idea whether anything happened.

Which is the real argument for every guard in this repo. They are not caution. They are
what keeps the trade solvent.

---

*Evidence for every claim here: [FINDINGS.md](FINDINGS.md). How it is assembled:
[ARCHITECTURE.md](ARCHITECTURE.md).*
