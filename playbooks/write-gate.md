---
verify: ""
preflight_expect: red
touch_scope: []
---
# Write the gate

You are writing a **failing test** from a contract. You are **not** implementing
anything. The implementation is the next step of this chain, and it will be
graded by the file you write here — so this file is the thing that decides
whether that step's success means anything.

## What you are given

- The contract: `{{contract}}`
- Where the test goes: `{{test_path}}`

Read the contract. Read enough of the code around `{{test_path}}` to match its
conventions — imports, fixtures, how other tests here are named.

## What to write

One test file at `{{test_path}}`, and nothing else.

**Every clause of the contract gets its own test, and each test names the clause
it covers** — in the test's name or in a comment on it:

```python
def test_returns_a_result_object():   # C1
```

That tag is how coverage is checked. A clause with no test tagged for it fails
this step and you will be asked for it by name.

**Put this line at the top of the file, filled in from the contract:**

```
# contract: {{contract}} @ <the digest the receipt shows>
```

If you do not know the digest, write the line without it and say so — do not
invent one.

## What "failing correctly" means here

This test MUST fail when you run it, because the thing it tests does not exist
yet. That is the point. But **not every failure counts**:

- it must PARSE. A syntax error is not a red gate, it is a broken file.
- at least one test must RUN, and none may be skipped. A skipped test reads as
  a pass, and a gate born switched off protects nothing.
- it must fail because the code is MISSING — an import that does not resolve, a
  name that does not exist — or on a real assertion. A test that fails because
  its own setup divides by zero is a bug in the test wearing a red costume.

Run it. Read the failure. If it failed for any other reason, fix the test.

## What not to do

- Do not create the implementation, not even a stub. An empty module makes the
  import succeed and the test fail for a different, weaker reason.
- Do not write a test you cannot explain. If a clause cannot be tested as
  written, **stop and say which clause and why** — do not tag a test that does
  not assert it. A clause marked covered by a test that checks something
  adjacent is worse than one honestly marked uncovered.
- Do not edit the contract. It defines what correct means; if it is wrong, say
  so instead of changing it.

## Report

State: how many clauses the contract has, which test covers each, and the exact
failure the suite produced.
