"""
worktree isolation, behavior frozen by specs/worktree_spec.py

Git worktree isolation containers for parallel delegations.  Each acquire
produces a separate, short-lived tree branched from HEAD; release cleans up
both the worktree and its branch.
"""

import hashlib
import os
import random
import shutil
import subprocess
import threading


class WorktreeError(Exception):
    """Raised when worktree operations cannot proceed."""


_lock = threading.Lock()


def _base_dir():
    return os.environ.get(
        "QWEN_DELEGATE_WORKTREES",
        os.path.expanduser("~/.qwen-delegate/worktrees"),
    )


def _slug(repo):
    real = os.path.realpath(repo)
    h = hashlib.sha256(real.encode()).hexdigest()[:8]
    return f"{os.path.basename(real)}-{h}"


def _run_id():
    return "r" + "".join(random.choice("0123456789abcdef") for _ in range(6))


def _main_git_dir(repo):
    """Return the main (common) git dir, resolving linked worktrees."""
    p = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    if p.returncode != 0:
        raise WorktreeError(f"Not a git repository: {repo}")
    return _resolve_toplevel(p.stdout.strip(), repo)


def _resolve_toplevel(common_dir, repo):
    """
    Given --git-common-dir output and the original cwd, resolve the actual
    top-level directory of the main working tree.

    For a normal repo, --git-common-dir returns .git (or its realpath).
    For a linked worktree, it returns the main .git directory path.
    We need the working-tree root, not the .git path.
    """
    # Ask git for the actual top-level of the main working tree.
    p = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    if p.returncode != 0:
        # Fallback: try to derive from common_dir
        common = os.path.normpath(common_dir)
        candidate = str(common).replace("/.git", "").replace("\\.git", "")
        return candidate
    return p.stdout.strip()


def _branch_exists(main_dir, branch):
    p = subprocess.run(
        ["git", "-C", main_dir, "rev-parse", "--verify", branch],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    return p.returncode == 0


def acquire(repo):
    """
    Create an isolated worktree.

    Return dict with keys: path, branch, base_sha, dirty.
    Raise WorktreeError on unborn HEAD.
    """
    with _lock:
        main_dir = _main_git_dir(repo)

        # Check HEAD exists (refuse unborn HEAD).
        p = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=main_dir, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        if p.returncode != 0:
            raise WorktreeError(
                "HEAD unborn — make the first commit before acquiring worktrees"
            )

        base_sha = p.stdout.strip()

        # Dirty check on the main tree.
        p = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=main_dir, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        dirty = bool(p.stdout.strip())

        slug = _slug(main_dir)
        base = _base_dir()

        # Generate a unique id; re-roll on collision.
        for _ in range(64):
            rid = _run_id()
            branch = f"qwen/{rid}"
            wt_path = os.path.join(base, slug, rid)
            if not os.path.exists(wt_path) and not _branch_exists(
                main_dir, branch
            ):
                break
        else:
            raise WorktreeError(
                "Failed to generate unique worktree id after 64 attempts"
            )

        os.makedirs(os.path.join(base, slug), exist_ok=True)

        # Create worktree.
        p = subprocess.run(
            [
                "git", "-C", main_dir,
                "worktree", "add", wt_path, "-b", branch, "HEAD",
            ],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        if p.returncode != 0:
            raise WorktreeError(f"git worktree add failed: {p.stderr.strip()}")

        return {
            "path": wt_path,
            "branch": branch,
            "base_sha": base_sha,
            "dirty": dirty,
        }


def release(repo, path, branch):
    """
    Remove a worktree and its branch. Idempotent — already-gone is success.
    """
    main_dir = _main_git_dir(repo)

    # git worktree remove --force (best-effort, tolerate missing)
    subprocess.run(
        ["git", "-C", main_dir, "worktree", "remove", "--force", path],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )

    # git branch -D (best-effort, tolerate missing)
    subprocess.run(
        ["git", "-C", main_dir, "branch", "-D", branch],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )

    # Clean up leftover directory
    if os.path.isdir(path):
        shutil.rmtree(path)


def merge_lines(res):
    """
    Return the two-line merge protocol string list.

    The server never merges; these lines are executed verbatim by Claude.
    """
    path = res["path"]
    branch = res["branch"]
    return [
        f"WORKTREE: {path}",
        f"MERGE: git merge --no-edit {branch} && git worktree remove {path} && git branch -d {branch}",
    ]


def classify_merge(repo, branch):
    """
    Read-only merge conflict probe.

    Use git merge-tree --write-tree to test whether merging the branch
    would succeed without touching the working tree or merge state.

    Return "clean" if merge would succeed, "conflict" if not.
    """
    main_dir = _main_git_dir(repo)

    # Get HEAD of main tree.
    p = subprocess.run(
        ["git", "-C", main_dir, "rev-parse", "HEAD"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    if p.returncode != 0:
        raise WorktreeError("Cannot resolve HEAD for merge classification")
    head_sha = p.stdout.strip()

    # Get branch sha.
    p = subprocess.run(
        ["git", "-C", main_dir, "rev-parse", branch],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    if p.returncode != 0:
        raise WorktreeError(f"Cannot resolve branch {branch!r}")
    branch_sha = p.stdout.strip()

    # merge-tree --write-tree: simulate merge without touching working tree.
    # Exit 0 = clean merge, nonzero = conflicts.
    p = subprocess.run(
        ["git", "-C", main_dir, "merge-tree", "--write-tree", head_sha, branch_sha],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )

    return "clean" if p.returncode == 0 else "conflict"
