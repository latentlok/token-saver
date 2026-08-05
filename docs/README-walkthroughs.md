# Walkthroughs

Plain-language end-to-end runs of each delegation shape. For the reasoning behind them see
[DESIGN-v06-test-first.md](DESIGN-v06-test-first.md); for the tool surface see
[USAGE.md](USAGE.md).

---

## Chain

*A two-link test-first pipeline: one worker writes the gate, a different worker satisfies it.*
**Status: designed, not built.** See DESIGN §11 for what still has to land.

### Once, when you set the repo up

You tell the plugin where your tests live, so it stops guessing:

```json
"tests": {
  "unit":        "pytest tests/unit -q",
  "integration": "pytest tests/integration -q"
}
```

### 1. You get a task

> "Notifications should actually get sent, and we should be able to see that they were."

### 2. You don't know this part of the code

You ask, for free — read-only, no gate, and it costs you a paragraph instead of a file:

```
qwen_query("What does the notification path talk to? Which module owns it?")

→ notifications/ writes to Postgres via db.session,
  sends via an HTTP client in clients/push.py
  VERIFY: notifications/service.py:14
```

You now know it touches a database. You never opened a file.

### 3. You write down what "done" means

Not code — criteria. This is architect work, and it needs no code reading. Save it as
`contracts/notifications-send.md`, committed to git:

```
C1  send_notification(user_id: int, message: str) -> NotificationResult
    lives in notifications/service.py
C2  after a successful call, a row exists in `notifications`
    with status='sent' for that user
C3  on transport failure, the row exists with status='failed'
    and the call does not raise
```

Writing C2 is what tells the system a database is involved. Nothing had to guess, and
nothing had to predict.

C1 matters more than it looks: naming the entry point is what stops link 1 inventing
`send_notification` while link 2 builds `notify_user`, and it is what lets the gate in step 5
tell "not built yet" from "test is broken."

### 4. One call, two steps

```
qwen_delegate(chain=[
  { brief_file: "playbooks/write-gate.md",
    vars: { contract: "contracts/notifications-send.md" } },
  { brief_file: "playbooks/implement.md",
    vars: { contract: "contracts/notifications-send.md" } },
])
```

The call returns in seconds with a run id. Go do something else.

### 5. Qwen writes the test

A fresh session reads the contract and writes a test. It does not implement anything.

The plugin then checks the test is a real gate, not a broken file that merely looks red:

- the test file itself parses and collects
- at least one test actually ran — none skipped
- every clause has a test naming it (no `C3` with nothing behind it)
- it fails for a legible reason — an assertion, or a missing symbol that C1 says does not
  exist yet. A typo in the test file is not a red gate.

Then the test is committed. **From here it is frozen.**

### 6. A different Qwen implements it

New session, no memory of step 5. Its gate is the test from step 5 — and it **cannot edit
that test**. Any attempt is reverted automatically, the same way spec files are.

So there is exactly one way to go green: build the thing.

### 7. You read about ten lines

```
STATUS: success
CONTRACT: contracts/notifications-send.md @ a3f9c1
GATE CONTRACT: 3 clauses
  C1 entry point            → test_signature_matches      ✓  assert isinstance(r, NotificationResult)
  C2 row status='sent'      → test_sent_row_is_written    ✓  assert row.status == "sent"
  C3 no raise on failure    → test_failure_is_swallowed   ✓  send_notification(...) called bare
+ 5 further assertions, no clause
```

You wrote three criteria; you see three lines, each with the assertion behind it. Reading
them takes five seconds and needs no implementation code.

That last check is yours, not the machine's. The plugin can prove a test *ran* and *is
sensitive to the work*. Only you know whether it asserts what you meant — which is why the
assertion text sits next to the clause rather than a checkmark alone.

**Total cost to you:** a contract, one call, ten lines. You never read the implementation.

### When it goes wrong

The pipeline stops at the first red link and tells you which one:

| What you see | What happened | What to do |
|---|---|---|
| `link 1: failed` + `UNCOVERED: C3` | the test misses a clause | link 2 never ran, nothing wasted — usually the contract clause was vague |
| `link 1: failed` + `NOT A GATE` | the delivered test errors for a reason C1 does not explain | the test file is broken, not the code |
| `link 2: skipped` | link 1 halted the chain | fix link 1 first; a chain never builds on a broken premise |
| `GATE VACUOUS` | the test passed *before* any work | the test does not test the new behaviour |
| `CONTRACT ... changed` | the contract file was edited between links | link 1's gate was written against a different document |

---

## Single delegation

*One worker, one gate, no pipeline.* This is the default and most work should use it.

```
qwen_delegate(task="Fix the off-by-one in parse_range so an inclusive
                    upper bound is honoured.")
```

No contract, so no pipeline and no ceremony. The worker builds, the configured gate decides,
you read a receipt.

**The one difference from today:** if that change turns out to touch a database, an HTTP
client or another process while only unit tests gated it, the receipt says

```
SEAM CROSSED, UNIT-GATED ONLY
```

and does not report a clean pass. You choose the cheap path freely; the plugin tells you
afterwards when the cheap path was the wrong one. Nothing has to be remembered up front.

---

## Query

*Read-only. No gate, because nothing is written.*

```
qwen_query(question="How does the retry policy decide when to give up?",
           cwd="/path/to/repo")
```

Free tokens instead of your context. Answers are **leads to verify, not truth** — every
answer carries a `VERIFY:` list of the files and lines it claims to be reading, and that list
is the point of the tool.

Under the chain design this gets *more* useful, not less: it is how an architect learns the
terrain well enough to write a contract without buying the code into context.
