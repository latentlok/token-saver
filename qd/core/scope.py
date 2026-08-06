#!/usr/bin/env python3
"""What one run OWNS, and must dispose of. Frozen by specs/scope_spec.py.

Step 5 of docs/DESIGN-modular-architecture.md §5. One rule:

    A run disposes of what it owns, and never of what it borrows.

Why an owner rather than three call sites. The container's fate was decided in
three places -- the refusal path, the green path and the red path -- each
re-deriving "may I dispose of this?" from a flag, and each correct by
inspection rather than by construction. A rule spread across three sites is one
edit away from disagreeing with itself, and here the disagreement does not leak
a directory: it DELETES WORK.

The sharp case, and why ownership is a first-class idea rather than a boolean
someone remembers to check. A chain's links SHARE one container and commit into
it between links, so link 2 can see link 1's work. The container outlives every
individual link. A link that released it would destroy the committed output of
every link before it -- and the symptom would be an ordinary red link, which is
why a mutation pass, not a green suite, is what found it (step 5a).

**Committing is the one disposal action a borrower must still take.** It is how
the next link sees this one's files, and how those files become TRACKED so the
spec guard can protect them: `spec_files()` is `git ls-files`, so an untracked
new test is unprotected and the next link could rewrite the very gate it is
graded by.

Also owned here: WHERE THE RUN STARTED (`pre_status`, `pre_sha`). Those describe
the tree this run holds, at the moment it took it, so they share its lifetime --
and holding them here is what let `DetectorInputs` drop from seven fields to
four, its scope half having found a real owner.

Not yet owned here: the session id and the per-call telemetry (`CallLog`). They
belong to the same lifetime and the design puts them here, but moving them is a
second change to a different region; this file takes the container and the
starting point first, which is the part with teeth.
"""

from qd import worktrees
from qd.gittree import git

# A run whose gate went GREEN keeps its work. `success_but_preflight_passed` is
# in this set on purpose and it is the expensive detail: the demotion says the
# pass proves less than it looks, NOT that the run failed. Treating it as red
# would silently delete the work of every preflight-passed container run.
_GREEN = ("success", "success_but_preflight_passed")


class RunScope:
    """The container this run works in, and who is allowed to end it."""

    def __init__(self, repo, container=None, owned=True,
                 pre_status=None, pre_sha=None):
        self.repo = repo
        self.container = container
        # WHERE THIS RUN STARTED. Per-run, and about the tree this run owns, so
        # it shares the container's lifetime. Held here rather than threaded
        # through every observer as loose arguments -- that threading is what
        # DetectorInputs was built to carry, and this is the half of it that
        # now has a real owner.
        self.pre_status = pre_status if pre_status is not None else {}
        self.pre_sha = pre_sha
        # A borrowed container (a chain link running in the chain's tree) is
        # USED, never disposed of.
        self.owned = owned and container is not None

    def mark_start(self, pre_status, pre_sha):
        """Record where this run began. Called AFTER acquisition, necessarily.

        Two-phase construction here is forced, not lazy: T0 must be read from
        the tree the run will actually use, and that tree does not exist until
        the container is acquired. Snapshotting before acquisition would record
        the main repo's state and then attribute the caller's concurrent edits
        to the worker.
        """
        self.pre_status = pre_status if pre_status is not None else {}
        self.pre_sha = pre_sha
        # Files this run made, attributed. Run attribution, which is what this
        # run OWNS -- the last field DetectorInputs was carrying on scope's
        # behalf. Set by mark_created() once the tree has been observed.
        self.created = []
        self.pre_tracked = set()
        self.hooked = False

    def mark_attribution(self, pre_tracked, hooked):
        """Who is answerable for a change in this tree.

        `hooked` says a proxy logged the worker's writes, so an unattributed
        change is somebody ELSE's -- a caller working the same tree. Reverting
        those is how a caller's concurrent work got destroyed once, which is why
        attribution is a property of the run rather than a per-guard argument.
        """
        self.pre_tracked = pre_tracked or set()
        self.hooked = hooked

    def unproven_fixtures(self, segments):
        """Delivered fixtures carrying no traceable source (U3.3).

        Asked of the scope because it is a question about the tree this run
        owns, and the guard should not need to know how a tree is read.
        """
        from qd.engine import _fixture_files, _unproven_fixtures
        return _unproven_fixtures(
            self.work_cwd, _fixture_files(self.created, segments))

    def mark_created(self, created):
        """Record which files this run is answerable for. After the facts, by
        necessity: attribution needs the changed set and the write log."""
        self.created = list(created or [])

    @property
    def work_cwd(self):
        """The tree the run actually works in.

        Every observation the receipt makes is taken from here. A run that read
        the main tree instead of its container would report the caller's
        concurrent edits as the worker's -- the false accusation this project
        spent a phase removing.
        """
        return self.container["path"] if self.container else self.repo

    def abandon(self):
        """Give up before finishing -- the refusal path.

        Refusals happen PAST acquisition, so returning without this leaves a
        worktree and a branch behind for every refused run. A borrowed
        container is exempt: the refusal concerns THIS link, and the chain that
        lent the tree is not refused.
        """
        if self.owned:
            worktrees.release(self.repo, self.container["path"],
                              self.container["branch"])

    def dispose(self, status):
        """End the run's claim on its container, according to how it went.

        Returns `{"worktree": {...}|None, "merge": str|None}` -- what the
        receipt needs to say about it. Returning rather than writing into a
        shared bag keeps this testable without a run (§4's rule applied to a
        lifetime instead of to a fact).
        """
        if self.container is None:
            return {"worktree": None, "merge": None}

        if status not in _GREEN:
            self.abandon()
            return {"worktree": None, "merge": None}

        path, branch = self.container["path"], self.container["branch"]
        git(path, "add", "-A")
        git(path, "commit", "-m", f"qwen delegation {branch}")
        out = {"worktree": {"path": path, "branch": branch,
                            "dirty": self.container.get("dirty")},
               "merge": None}
        # Only an owner classifies. classify_merge compares the branch against
        # the MAIN repo, which for an intermediate link describes work the
        # chain has not finished -- run_chain classifies once, at the end,
        # when the answer means something.
        if self.owned:
            out["merge"] = worktrees.classify_merge(self.repo, branch)
        return out
