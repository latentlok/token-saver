# CLAUDE.md — working ON this repo

Not about using the tool. That is `AGENT.md`.

Every rule below exists because it was violated and cost something. The reason stays
attached to the rule; a rule without its evidence is one the next reader relaxes.

## Building

**Spec first, and it must fail before the fix.** A test written after the code passes
on the first run and has proved nothing. Where the code is already right and only the
test was missing, **the red phase is the mutation**: break the code, watch the new spec
go red, restore, watch it go green.

**Mutation-check everything — and assert the mutation actually applied.** Three times
in one session a mutation reported clean because the harness silently failed to apply
it, or because the payload could not fire. A mutation you did not confirm landed is a
green you invented.

**Never delegate a `specs/*_spec.py`.** The gate must come from a different hand than
the builder. A worker that writes the spec for its own change has restated its own
understanding as an assertion, and green then means nothing.

**Run the full suite as its OWN command, and read the exit code before writing it
down.** `bash ci/run-specs.sh`, alone. Never chain it to the commit — a chained suite's
failure gets swallowed by the next command's success and the commit lands on a red
tree.

**Commit explicit paths. Never `git add -A`.** It picks up scratch files, worktree
debris and other agents' in-flight work, and the diff you reviewed is not the diff you
committed.

**Restore by file copy, never `git checkout`. Never move a file you do not own.**
`git checkout` and `git restore` take the whole path back, including a concurrent
edit somebody else made in the same tree. Copy the bytes you meant to restore.

**Comments explain WHY, with evidence.** A comment asserting a property the code does
not have is a **defect here, not a nit** — the next reader trusts it and stops checking.
If the comment says "refuses on X", something must refuse on X.

**Zero third-party dependencies. Standard library only.** There is no `pyproject.toml`
and nothing to install. A dependency here is a dependency in every user's environment,
and this plugin installs into machines it will never see.

## Three traps that look like something else

**`/tmp` filling up looks exactly like test failures.** The specs `mkdtemp` a git repo
per test and unittest never cleans them up; a day of local runs hit ENOSPC on inodes
and took every session on the box down. `ci/run-specs.sh` routes everything under one
throwaway `TMPDIR` and removes it on exit. **When you run a spec directly, do
`export TMPDIR=$(mktemp -d)` first.**

**A probe that fails to fire looks exactly like a defence that works.** Both produce a
clean result. Before believing a guard held, prove the thing it guards against actually
reached it.

**A deleted spec file makes the suite PASS.** `ci/run-specs.sh` globs `specs/*_spec.py`;
a file that is not there is not a failure. Removing a spec is never how a red suite gets
fixed.

## Releasing

1. Bump the version in **both** `.claude-plugin/plugin.json` and `SERVER_INFO` in
   `qd/server.py`. They are two declarations of the same number and drift silently.
2. Update `CHANGELOG.md`.
3. Open a PR.
4. **Let CI decide. Do not merge on a local green.** A local run has your `HOME`, your
   `/tmp` and your Python; CI is the environment the claim is about.
5. Squash-merge.
6. Tag.

**Merging does NOT publish to existing users by itself.** Users compare the *declared
version string*, so a merge without a version bump reaches nobody — their install stays
exactly where it was. (An earlier version of this document got this wrong; the bump in
step 1 is the release, not the merge.)

Also note that third-party marketplaces do not auto-update: even after a correct
release, a user is on the old version until they run
`/plugin marketplace update token-saver`.

## Documentation

The four root documents have distinct audiences and must not drift into each other:
`README.md` (a human, installing), `ARCHITECTURE.md` (the shape, no paths),
`AGENT.md` (an agent using the tool), `CLAUDE.md` (an agent editing this tree).

Two documents disagreeing is worse than either being wrong, because whichever the
reader met first wins and neither knows. `specs/skill_spec.py` pins the claims that
`skills/delegation/SKILL.md` and `AGENT.md` must agree on — it is a real gate, and
prose is the one part of this repo nothing else executes.

Only `CLAUDE.md` may name paths in this tree. Paths and line numbers in the other three
rot, and a rotted path in a user-facing doc is a wrong answer with a citation.
