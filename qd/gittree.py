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
            ["git", *a], cwd=cwd, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
        )
        # rstrip newlines only -- NEVER .strip(). `status --porcelain` encodes state in
        # leading columns (" M path"), so stripping the whole output eats the first
        # line's leading space and shifts its path by one character.
        return p.returncode, (p.stdout or "").rstrip("\n")
    except Exception:
        return 1, ""


def git_bytes(cwd, *a):
    """git() with raw stdout bytes -- for file CONTENT (`git show`), which must
    round-trip binary exactly and must not have its trailing newlines stripped."""
    try:
        p = subprocess.run(["git", *a], cwd=cwd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout or b""
    except Exception:
        return 1, b""


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


def untracked_files(cwd):
    """Every untracked PATH, directories expanded (`-uall`).

    `git status --porcelain` collapses a brand-new directory into a single
    `dir/` entry. That is the right summary for CHANGED, and useless to any
    rule about individual files: a fixture check reading it would police a
    directory name, and a stray line would print one.
    """
    rc, out = git(cwd, "status", "--porcelain", "-uall")
    if rc != 0 or not out:
        return []
    return [line[3:].strip() for line in out.splitlines()
            if line[:2].strip() == "??" and len(line) > 3]


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


# Byte-snapshot caps. Per-file mirrors file_sha's big-file threshold; the total
# bounds one run's disk cost. A path over either cap is recorded as
# NON-RESTORABLE and later reported instead of auto-reverted -- restoring it
# from a commit would destroy pre-run edits, which is worse than leaving an
# out-of-scope change on disk for review.
SNAPSHOT_FILE_CAP = 8_000_000
SNAPSHOT_TOTAL_CAP = 64_000_000


def snapshot_contents(cwd, status, dest_dir):
    """Save the BYTES of every dirty path so a later revert can restore the
    tree exactly as it was at T0 -- not as HEAD had it.

    `git checkout <sha> -- p` restores the COMMITTED content: for a file that
    was already dirty before the run, that destroys the pre-run edits (measured
    in the field: a caller's uncommitted fix, reverted and staged, gone).
    Returns {path: ("file", saved_abs) | ("absent", None) | ("toobig", None)};
    "absent" marks a path dirty-by-deletion at T0, whose restoration is removal.
    """
    saved = {}
    total = 0
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception:
        return {p: ("toobig", None) for p in status}
    for p in status:
        full = os.path.join(cwd, p)
        try:
            if not os.path.isfile(full):
                saved[p] = ("absent", None)
                continue
            size = os.path.getsize(full)
            if size > SNAPSHOT_FILE_CAP or total + size > SNAPSHOT_TOTAL_CAP:
                saved[p] = ("toobig", None)
                continue
            dst = os.path.join(
                dest_dir,
                hashlib.sha256(p.encode("utf-8", "replace")).hexdigest()[:24])
            with open(full, "rb") as src, open(dst, "wb") as out:
                out.write(src.read())
            total += size
            saved[p] = ("file", dst)
        except Exception:
            saved[p] = ("toobig", None)  # unreadable: same policy as too big
    return saved


def restore_paths(cwd, paths, base=None, t0=None):
    """Restore `paths` to their T0 state WITHOUT touching the index.

    T0-dirty paths come back byte-for-byte from the `snapshot_contents` map;
    T0-clean paths from `git show base:path`. The old `git checkout <sha> -- p`
    form did both jobs wrong: it restored the committed content over pre-run
    edits AND staged the result, so the destruction was invisible to a later
    `git diff`. Returns (restored, unrestored) path lists.
    """
    restored, unrestored = [], []
    for p in paths or []:
        full = os.path.join(cwd, p)
        try:
            ent = (t0 or {}).get(p)
            if ent is not None:
                kind, src = ent
                if kind == "file":
                    with open(src, "rb") as f:
                        data = f.read()
                    with open(full, "wb") as out:
                        out.write(data)
                elif kind == "absent":
                    if os.path.exists(full):
                        os.remove(full)
                else:  # non-restorable: report, never guess at content
                    unrestored.append(p)
                    continue
                restored.append(p)
                continue
            rc, data = git_bytes(cwd, "show", f"{base or 'HEAD'}:{p}")
            if rc != 0:
                unrestored.append(p)
                continue
            d = os.path.dirname(full)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(full, "wb") as out:
                out.write(data)
            restored.append(p)
        except Exception:
            unrestored.append(p)
    return restored, unrestored


def _project_config(cwd):
    """Parsed <cwd>/.qwen-delegate.json (the per-project override file), or {}.

    Recognised keys: `spec_globs` (list), `max_iterations` (int, the retry budget),
    `executor` (profile name), `min_tests` (int, self-gate floor), `trust`
    (`"self"`|`"verified"`|`"auto"` — the project's default slider position), and
    `worktree` (`"auto"` — the standing isolation default for a repo where
    co-work is the norm; a call arg still beats it).
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


def revert_specs(cwd, paths, base=None, t0=None):
    """Restore spec files from `base` (default: HEAD).

    Base matters for the same reason: if the worker committed its edit, HEAD now holds
    the WEAKENED spec, so restoring from HEAD would faithfully restore the sabotage.
    Routed through restore_paths so the revert never touches the index -- the old
    `git checkout <sha> --` form also STAGED what it restored.
    """
    if paths:
        restore_paths(cwd, paths, base=base, t0=t0)


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
    # 2. brand-new untracked source files: every public symbol is new.
    # Through untracked_files(), NOT status_map(): `git status --porcelain`
    # collapses a brand-new directory into a single `engine/` entry, so a run
    # that delivered a whole new module package reported ZERO new public
    # surface -- the larger the unit, the more completely it was missed.
    for path in untracked_files(cwd):
        if any(t in path for t in _TESTY):
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


# --- Seam detectors (v0.6) -------------------------------------------------
#
# The three cheapest findings in the whole friction ledger, and they share one
# premise: a unit brief describes ONE module, its gate runs with the rest of
# the system mocked, and `preflight_expect` only proves the gate could fail
# *for that module*. So the workflow can never assert "and this is wired to
# that" -- and sixteen field defects landed with ZERO inside a delegated unit.
# Every one lived in a join.
#
# None of these verifies a seam. They make seam RISK visible, statically, from
# what the run already changed. All three are greps; nothing extra executes.


def uncalled_symbols(cwd, pubs, limit=40):
    """{file: [names]} for new public symbols nothing else references.

    DETERMINISTIC (no model). A symbol defined by this run and mentioned
    nowhere outside its own file and its own tests is, right now, dead code:
    built, gated, merged, and called by nothing. **Six** such units shipped
    green in one field build -- `run_threads`, `run_memos`, `set_job_contract`,
    `write_counter` and two more -- each passing a gate that could not ask the
    only question that mattered.

    This is a claim about the tree as it stands, not about intent: a public
    API meant for an outside caller is legitimately uncalled here, which is
    why the receipt reports it as something to confirm rather than a failure.
    """
    out = {}
    for path, names in (pubs or {}).items():
        dead = [n for n in names[:limit]
                if not _referenced_outside(cwd, n, path)]
        if dead:
            out[path] = dead
    return out


def _referenced_outside(cwd, name, defining_path):
    """True if `name` appears in any file that is not its definition or a test.

    Tests are excluded deliberately: a symbol referenced ONLY by the test the
    same run delivered is exactly the shape being looked for -- the unit and
    its gate agree with each other and nothing else knows the symbol exists.
    """
    rc, out = git(cwd, "grep", "-l", "-w", "--untracked", "-I", "-e", name)
    if rc != 0:                      # 1 = no match, >1 = grep failure
        return False
    for hit in out.splitlines():
        hit = hit.strip()
        if not hit or hit == defining_path:
            continue
        if any(t in hit for t in _TESTY):
            continue
        return True
    return False


# Mock targets: the dotted string forms (`patch("a.b.c")`,
# `monkeypatch.setattr("a.b", ...)`) plus `patch.object(mod, ...)`, whose
# first argument is an imported module name rather than a string.
_MOCK_STR = re.compile(
    r"""(?:mock\.)?patch(?:\.object)?\s*\(\s*["']([\w\.]+)["']"""
    r"""|monkeypatch\.(?:setattr|delattr)\s*\(\s*["']([\w\.]+)["']""")
_MOCK_OBJ = re.compile(r"""(?:mock\.)?patch\.object\s*\(\s*([A-Za-z_][\w\.]*)""")


def mocked_seams(cwd, changed, limit=200):
    """[(test_file, module)] where a delivered test mocks a module this run
    also CHANGED.

    DETERMINISTIC (no model). The rule, stated generally: a self-graded gate
    tests what the worker can observe, so anything it replaces with a mock is
    by construction the thing its gate does not test -- and the mocked seam is
    therefore exactly where the work fails, with a green receipt every time.
    Measured: three live failures in one session, all mocked boundaries, all
    green (SQL against a column that never existed; a config resolved one way
    in the guard and another in production; constants invented for a live
    search the worker could not observe).

    The intersection with CHANGED is what makes it a signal rather than noise.
    Mocking a stable third-party boundary is ordinary and reported nowhere;
    mocking the module you are in the middle of rewriting is the finding.
    """
    changed = list(changed or [])
    tests = [p for p in changed if any(t in p for t in _TESTY)]
    if not tests:
        return []
    # Module paths this run touched, as importable dotted prefixes.
    touched = {}
    for p in changed:
        if p.endswith(".py") and not any(t in p for t in _TESTY):
            touched[p[:-3].replace("/", ".").replace("\\", ".")] = p
    if not touched:
        return []

    found, seen = [], set()
    for tf in tests[:limit]:
        try:
            full = os.path.join(cwd, tf)
            if not os.path.isfile(full) or os.path.getsize(full) > 2_000_000:
                continue
            with open(full, errors="replace") as f:
                body = f.read()
        except Exception:
            continue
        targets = set()
        for m in _MOCK_STR.finditer(body):
            targets.add(m.group(1) or m.group(2))
        targets.update(m.group(1) for m in _MOCK_OBJ.finditer(body))
        for target in targets:
            if not target:
                continue
            for dotted, path in touched.items():
                # `patch("qd.engine.run")` targets module `qd.engine`; a bare
                # `patch.object(engine, ...)` names only the tail.
                if (target == dotted or target.startswith(dotted + ".")
                        or dotted.endswith("." + target.split(".")[0])
                        or dotted == target.split(".")[0]):
                    key = (tf, path)
                    if key not in seen:
                        seen.add(key)
                        found.append(key)
                    break
    return found


def never_executed(cwd, changed, verify_cmd):
    """Delivered test files the gate command does not run.

    DETERMINISTIC (no model). A test file written by the run and not executed
    by its own gate is a test that has never run -- it cannot have passed, and
    a green receipt says nothing about it. In the field this was an entire
    directory (`gate_tests/`, skipped by default) whose files were authored
    under a green delegation and first executed three weeks later, when one
    turned out to be unsatisfiable: its assertions needed live observations
    the worker had no way to make, so its ceiling was zero against a floor of
    three. `preflight_expect="red"` is blind to that by construction -- an
    unsatisfiable gate is red before AND after, which is what an honest
    greenfield gate looks like right up until it runs.

    Conservative on purpose. A gate naming no paths (`make test`, `npm test`)
    is assumed to run everything, because guessing at a build tool's coverage
    would produce exactly the false accusations this receipt has spent a phase
    removing. Only a gate that names paths can convict.
    """
    cmd = (verify_cmd or "").strip()
    tests = [p for p in (changed or []) if any(t in p for t in _TESTY)]
    if not cmd or not tests:
        return []
    # A token is a path iff it EXISTS in the tree. Shape heuristics cannot
    # tell `unit_tests` (a directory) from `pytest` (a program) -- the
    # filesystem can, and it is the same tree the gate will run against.
    named = []
    for tok in re.split(r"[\s;&|]+", cmd):
        tok = tok.strip("'\"")
        if not tok or tok.startswith("-"):
            continue
        if os.path.exists(os.path.join(cwd, tok)):
            named.append(tok)
    if not named:
        return []                    # whole-suite runner: assume full coverage
    out = []
    for t in tests:
        covered = False
        for n in named:
            n = n.rstrip("/")
            if not n:
                continue
            if t == n or t.startswith(n + "/") or n.endswith(t):
                covered = True
                break
        if not covered:
            out.append(t)
    return out


# Test-dodge markers. Case-sensitive and anchored on the DECORATOR/attribute
# forms on purpose: a bare `skip` match also fires on "skipped 3 files" in a
# docstring, and a receipt line that cries wolf on prose is one nobody reads.
# The three alternatives cover `@skip`/`@unittest.skip` (attribute after one
# dot), `pytest.mark.skip|xfail` (two dots, so the first alternative cannot
# reach it), and unittest's `expectedFailure`, which is a name, not a call.
_DODGE = re.compile(r"@\w*\.?(?:skip|xfail)(?!if)\b"
                    r"|pytest\.mark\.(?:skip|xfail)(?!if)\b"
                    r"|expectedFailure")

# `skipif` is the REPAIR, not the dodge -- converting a hard skip into a
# conditional one is what a correct fix looks like -- so the alternatives
# above exclude it explicitly. Without the lookahead every `pytest.mark.skipif`
# read as an added `skip`: measured 4 false positives in a single run whose
# whole purpose was skip -> skipif, on the one line the skill says always to
# read on a green receipt. A detector that cries wolf on the signal you are
# told to trust is worse than no detector.
#
# String and comment tokens are stripped before matching for the same reason:
# the fourth false positive was a test's own guard assertion,
# `text.count("@pytest.mark.skip(")`, which exists to PROVE no skip was added.
_STRINGS = re.compile(r"'''.*?'''|\"\"\".*?\"\"\"|'[^'\n]*'|\"[^\"\n]*\"",
                      re.S)


def _code_only(line):
    """`line` with string literals and trailing comments blanked out."""
    line = _STRINGS.sub("", line)
    return line.split("#", 1)[0]


def dodge_markers(cwd, pre_sha, pre_status=None):
    """{path: [markers]} for skip/xfail markers ADDED to test-ish files.

    A green gate and a delivered `@unittest.skip` on the failing test are the
    same receipt today. Only ADDED lines count: a marker that was already in
    the file at `pre_sha` is somebody's considered decision, while one that
    appears in the same run as the delivery can hide the very failure the task
    was about.

    `pre_status` (the T0 dirty snapshot) is what keeps an UNTRACKED test file
    that was already sitting in the tree from being scanned whole: it is new to
    git at pre_sha but not new to this run, and a false accusation is the class
    of receipt bug this project has spent a phase removing.
    """
    out = {}
    base = [pre_sha] if pre_sha else []
    rc, changed = git(cwd, "diff", "--name-only", *base)
    for path in (changed.splitlines() if rc == 0 else []):
        if not path.strip() or not any(t in path for t in _TESTY):
            continue
        rc2, diff = git(cwd, "diff", *base, "--", path)
        if rc2 != 0:
            continue
        found = []
        for line in diff.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for m in _DODGE.finditer(_code_only(line[1:])):
                if m.group(0) not in found:
                    found.append(m.group(0))
        if found:
            out[path] = found
    # A brand-new untracked test file has no diff to read: every line in it is
    # an added line. Read through untracked_files, because a new test DIRECTORY
    # reaches `git status` as one `tests/` entry and the files inside it -- the
    # ones a dodge would live in -- never appear at all.
    pre = pre_status or {}
    for path in untracked_files(cwd):
        if not any(t in path for t in _TESTY) or path in out:
            continue
        if path in pre or any(path.startswith(d) for d in pre if d.endswith("/")):
            continue
        full = os.path.join(cwd, path)
        try:
            if not os.path.isfile(full) or os.path.getsize(full) > 2_000_000:
                continue
            found = []
            with open(full, errors="replace") as f:
                for line in f:
                    for m in _DODGE.finditer(_code_only(line)):
                        if m.group(0) not in found:
                            found.append(m.group(0))
            if found:
                out[path] = found
        except Exception:
            pass
    return out


def numstat_map(cwd):
    """{path: (added, removed)} from `git diff --numstat`."""
    rc, numstat = git(cwd, "diff", "--numstat")
    lines = {}
    if rc == 0 and numstat:
        for row in numstat.splitlines():
            parts = row.split("\t")
            if len(parts) == 3:
                lines[parts[2]] = (parts[0], parts[1])
    return lines


def blast_radius(cwd, pre):
    """
    What changed during the run. Qwen reports what it *says* it did; this reports
    what the filesystem says. Run 3 of the scope-creep test claimed '104 tests pass'
    (true) while silently adding an unrequested public API -- the tool result gave no
    hint of the sprawl. This closes that.
    """
    return blast_lines(pre, snapshot(cwd), numstat_map(cwd))


def blast_lines(pre, post, lines):
    """The CHANGED report from captured facts -- so the engine can snapshot the
    run's OWN tree before a worktree commit/release makes it unreadable, and the
    renderer never re-reads a tree the run didn't use."""
    touched = sorted(p for p in post if post.get(p) != pre.get(p))
    gone = sorted(p for p in pre if p not in post)
    if not touched and not gone:
        return "CHANGED: nothing (Qwen wrote no files)"

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
