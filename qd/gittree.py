"""
Ported trust machinery from server.py (LLD "qd/gittree.py").

Behavior is frozen by specs/gittree_spec.py.
"""

import hashlib
import json
import os
import re
import subprocess

DEFAULT_SPEC_GLOBS = ["*_spec.*", "*.spec.*"]
PROJECT_CONFIG = ".qwen-delegate.json"


def git(cwd, *a):
    try:
        p = subprocess.run(
            ["git", *a], cwd=cwd, capture_output=True, text=True, timeout=30
        )
        # rstrip newlines only -- NEVER .strip(). `status --porcelain` encodes state in
        # leading columns (" M path"), so stripping the whole output eats the first
        # line's leading space and shifts its path by one character.
        return p.returncode, (p.stdout or "").rstrip("\n")
    except Exception:
        return 1, ""


def is_git_repo(cwd):
    rc, out = git(cwd, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out == "true"


def head_sha(cwd):
    rc, out = git(cwd, "rev-parse", "--short", "HEAD")
    return out if rc == 0 else None


def status_map(cwd):
    """{path: porcelain status code} for the working tree."""
    rc, out = git(cwd, "status", "--porcelain")
    if rc != 0 or not out:
        return {}
    m = {}
    for line in out.splitlines():
        if len(line) > 3:
            m[line[3:].strip()] = line[:2].strip()
    return m


def file_sha(cwd, path):
    """Content hash, or None if unreadable/absent."""
    try:
        full = os.path.join(cwd, path)
        if not os.path.isfile(full):
            return None
        if os.path.getsize(full) > 8_000_000:
            return f"big:{os.path.getsize(full)}"
        with open(full, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except Exception:
        return None


def snapshot(cwd):
    """
    {path: (status_code, content_sha)} for every dirty path.

    The sha matters: comparing status codes alone is blind to a file that was ALREADY
    dirty and got edited again -- the code stays '??' or 'M' while the content changes.
    That produced a false "CHANGED: nothing" on a session-resume follow-up that had in
    fact rewritten the file.
    """
    return {p: (code, file_sha(cwd, p)) for p, code in status_map(cwd).items()}


def _project_config(cwd):
    """Parsed <cwd>/.qwen-delegate.json (the per-project override file), or {}.

    Recognised keys: `spec_globs` (list), `max_iterations` (int, the retry budget),
    `executor` (profile name), `min_tests` (int, self-gate floor), and `trust`
    (`"self"`|`"verified"`|`"auto"` — the project's default slider position).
    A missing or corrupt file is treated as no overrides, never an error."""
    try:
        p = os.path.join(cwd, PROJECT_CONFIG)
        if os.path.isfile(p):
            with open(p) as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def _global_config():
    """Parsed machine-level ~/.qwen-delegate/config.json (or $QWEN_DELEGATE_CONFIG), or {}.

    The machine-wide defaults file — lowest-precedence override, below any per-project
    .qwen-delegate.json. Recognised key: `trust` (`"self"`|`"verified"`|`"auto"`), the
    standing slider position for every project on this machine. Same env-override +
    tolerant-read convention as the executors/registry machine files.
    A missing or corrupt file is treated as no overrides, never an error."""
    try:
        p = os.environ.get("QWEN_DELEGATE_CONFIG") or os.path.expanduser(
            "~/.qwen-delegate/config.json")
        if os.path.isfile(p):
            with open(p) as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def spec_globs(cwd):
    """Protected-spec patterns for this project. Per-project config wins."""
    globs = _project_config(cwd).get("spec_globs")
    if isinstance(globs, list) and globs:
        return [str(g) for g in globs]
    return DEFAULT_SPEC_GLOBS


def spec_files(cwd):
    """Tracked protected-spec paths, repo-relative. Language-agnostic."""
    pats = []
    for g in spec_globs(cwd):
        pats += [g, f"**/{g}"]
    rc, out = git(cwd, "ls-files", *pats)
    if rc != 0 or not out:
        return []
    return sorted({p for p in out.splitlines() if p.strip()})


def violated_specs(cwd, base=None):
    """
    Tracked spec files that differ from `base` (default: HEAD, i.e. uncommitted edits).

    Pass the PRE-RUN sha as base when checking after a run. Plain `git diff` compares the
    working tree to HEAD, so a worker that edits a spec and then COMMITS it moves HEAD
    with it and the diff comes back empty -- the guard sees nothing and the weakened spec
    survives. Measured: same edit, uncommitted -> ['calc_spec.py'], committed -> [].
    Diffing against the sha the run started from closes that.
    """
    specs = spec_files(cwd)
    if not specs:
        return []
    args = ["diff", "--name-only"] + ([base] if base else []) + ["--"] + specs
    rc, out = git(cwd, *args)
    if rc != 0 or not out:
        return []
    return [p for p in out.splitlines() if p.strip()]


def revert_specs(cwd, paths, base=None):
    """Restore spec files from `base` (default: HEAD).

    Base matters for the same reason: if the worker committed its edit, HEAD now holds
    the WEAKENED spec, so restoring from HEAD would faithfully restore the sabotage.
    """
    if paths:
        git(cwd, "checkout", *([base] if base else []), "--", *paths)


def committed_during_run(cwd, pre_sha):
    """(moved, commit_count, files) for commits made since pre_sha. ([] if HEAD is same.)

    The worker is told not to commit -- QWEN.md says so, and `scoped` hard-denies it --
    but in `yolo` nothing enforces it, and it has been observed committing anyway. That
    matters beyond tidiness: a commit hides the change from `git status`, so CHANGED
    reports nothing while work really happened, and it invalidates the printed rollback.
    """
    if not pre_sha:
        return False, 0, []
    rc, now_full = git(cwd, "rev-parse", "HEAD")
    now_full = now_full if rc == 0 and now_full else None
    if not now_full or now_full == pre_sha:
        return False, 0, []
    rc, out = git(cwd, "rev-list", "--count", f"{pre_sha}..HEAD")
    count = int(out) if rc == 0 and out.strip().isdigit() else 0
    rc, out = git(cwd, "diff", "--name-only", pre_sha, "HEAD")
    files = [p for p in (out or "").splitlines() if p.strip()] if rc == 0 else []
    return True, count, files


# Public-definition patterns per language. Applied to a diff line's content. A new
# top-level match = new public surface (a name others can depend on -- a contract).
PUBLIC_DEF = [
    (r"^def\s+([a-zA-Z]\w*)", False),              # python def (top-level only)
    (r"^class\s+([A-Za-z]\w*)", False),            # python/other class
    (r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z]\w*)", True),  # JS/TS function
    (r"^export\s+(?:const|let|var|class|interface|type|enum)\s+([A-Za-z]\w*)", True),
    (r"^func\s+\(?[^)]*\)?\s*([A-Z]\w*)", False),  # go exported func/method
    (r"^type\s+([A-Z]\w*)", False),                # go exported type
    (r"^pub\s+(?:fn|struct|enum|trait|type)\s+([A-Za-z]\w*)", True),  # rust
    (r"^public\s+.*?\b([A-Z]\w*)\s*\(", True),     # java/c# method (rough)
]
_TESTY = ("_spec.", ".spec.", "_qwen.", "_test.", "test_", "/tests/", "/test/", "conftest")


def _publics_in_line(content):
    """Public symbol names defined on this (de-plussed) diff line, or []."""
    indented = content[:1].isspace()
    body = content.strip()
    out = []
    for pat, allow_indented in PUBLIC_DEF:
        if indented and not allow_indented:
            continue  # a top-level def that's now indented is a method -- skip
        m = re.match(pat, body)
        if m:
            name = m.group(1)
            if not name.startswith("_"):  # private by convention
                out.append(name)
    return out


def new_public_symbols(cwd):
    """
    DETERMINISTIC (no model). New public symbols Qwen introduced vs the pre-run commit:
    the design choices that become contracts. The tree was clean before the run, so
    `git diff` is exactly Qwen's changes; untracked new source files are all-new surface.
    Test/spec files are excluded (new symbols there are expected). Returns {file: [names]}.
    """
    out = {}
    # 1. tracked changes: added publics minus removed publics (cancels renames/moves)
    rc, changed = git(cwd, "diff", "--name-only")
    for path in (changed.splitlines() if rc == 0 else []):
        if not path.strip() or any(t in path for t in _TESTY):
            continue
        rc2, diff = git(cwd, "diff", "--", path)
        if rc2 != 0:
            continue
        added, removed = [], []
        for line in diff.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added += _publics_in_line(line[1:])
            elif line.startswith("-"):
                removed += _publics_in_line(line[1:])
        net = [s for s in dict.fromkeys(added) if added.count(s) > removed.count(s)]
        if net:
            out[path] = net
    # 2. brand-new untracked source files: every public symbol is new
    for path, code in status_map(cwd).items():
        if code != "??" or any(t in path for t in _TESTY):
            continue
        full = os.path.join(cwd, path)
        try:
            if os.path.isfile(full) and os.path.getsize(full) < 2_000_000:
                names = []
                with open(full, errors="replace") as f:
                    for line in f:
                        names += _publics_in_line(line)
                if names:
                    out[path] = list(dict.fromkeys(names))
        except Exception:
            pass
    return out


def blast_radius(cwd, pre):
    """
    What changed during the run. Qwen reports what it *says* it did; this reports
    what the filesystem says. Run 3 of the scope-creep test claimed '104 tests pass'
    (true) while silently adding an unrequested public API -- the tool result gave no
    hint of the sprawl. This closes that.
    """
    post = snapshot(cwd)
    touched = sorted(p for p in post if post.get(p) != pre.get(p))
    gone = sorted(p for p in pre if p not in post)
    if not touched and not gone:
        return "CHANGED: nothing (Qwen wrote no files)"

    rc, numstat = git(cwd, "diff", "--numstat")
    lines = {}
    if rc == 0 and numstat:
        for row in numstat.splitlines():
            parts = row.split("\t")
            if len(parts) == 3:
                lines[parts[2]] = (parts[0], parts[1])

    out = [f"CHANGED: {len(touched) + len(gone)} file(s)"]
    for p in gone:
        out.append(f"  - {p} (reverted/removed)")
    for p in touched[:20]:
        code = post.get(p, ("?", None))[0]
        if code == "??":
            # Already-untracked files stay '??' when edited, so distinguish by presence
            # in the pre-run snapshot rather than by status code.
            out.append(f"  + {p} (new)" if p not in pre else f"  ~ {p} (edited, untracked)")
        elif p in lines:
            add, rem = lines[p]
            out.append(f"  M {p} (+{add}/-{rem})")
        else:
            out.append(f"  {code} {p}")
    if len(touched) > 20:
        out.append(f"  ... and {len(touched) - 20} more")
    return "\n".join(out)


def reset_worktree(cwd, sha):
    """Reset the working tree to a committed base so the next best-of-N candidate starts
    clean and independent. clean -fd, NEVER -fdx (-fdx destroys a gitignored venv)."""
    git(cwd, "reset", "--hard", sha)
    git(cwd, "clean", "-fd")
