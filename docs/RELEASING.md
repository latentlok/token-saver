# Releasing

The repo is its own marketplace (`.claude-plugin/marketplace.json` points at `./`),
so **merging to `master` is publishing** — there is no separate publish step, and no
artefact to upload. Anyone running `claude plugin update` gets whatever `master` says.

That is convenient and it is also the reason to tag: without one, "which commit is
0.4.0?" has no answer, and a user reporting a bug against a version cannot be matched
to a tree.

## Steps

1. **Bump the version** in `.claude-plugin/plugin.json` and `SERVER_INFO` in
   `qd/server.py`. Keep them equal — `claude plugin details` shows the first and the
   MCP handshake reports the second, and a mismatch is the kind of thing nobody
   notices until it matters.

   The bump is not cosmetic: `qd/setup.py` stamps the version it last ran for, so a
   change is what makes the machine check re-run once for existing installs. A release
   that adds a new check and does not bump reaches new users only.

2. **Write the changelog entry** in `docs/CHANGELOG.md` before opening the PR, while
   the reasons are still in hand. Record why each change exists, not just what moved;
   the what is in the diff.

3. **Open a PR and let CI decide.** `ci/run-specs.sh` runs on every pull request.
   Do not merge on a local green — the local run has your machine's config in reach.

4. **Squash-merge**, delete the branch.

5. **Tag the merge commit on `master`** and push the tag:

       git checkout master && git pull
       git tag -a v0.4.0 -m "0.4.0 — the executor tells the truth"
       git push origin v0.4.0

   Annotated (`-a`), not lightweight: it carries a date, an author and a message, and
   it is what `git describe` reports.

6. **Cut a GitHub release** from the tag, with the changelog section as its body:

       gh release create v0.4.0 --title "..." --notes-file <(...)

   This is what gives a version a URL people can link to in an issue.

7. **Confirm what the outside world sees**, not what your checkout says:

       curl -s https://raw.githubusercontent.com/latentlok/token-saver/master/.claude-plugin/plugin.json

## Conventions

- Tags are `vMAJOR.MINOR.PATCH`, matching the plugin version exactly.
- One release commit per version is the older convention (`release: v0.3.0`); folding
  the bump into the release PR is fine as long as the tag lands on the merge commit.
- Tags start at v0.4.0. Earlier versions shipped untagged and are not worth
  reconstructing — a tag pointing at a guess is worse than no tag.
