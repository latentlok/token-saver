#!/usr/bin/env python3
"""
Self-configuration ported from server.py.

Detects the project's test command, renders a QWEN.md from the template, and
writes it atomically. The `rules_file` parameter lets the caller pick the
destination filename; default is the project-standard "QWEN.md".

Never raises — returns (None, None) on failure so the caller can fall back
to a refusal instead of killing the delegation.
"""

import os
import re
import shlex
import shutil
import sys

from qd.gittree import git, _project_config
from qd.runlog import register_project

# ---------- constants (byte-identical to server.py) ----------

RULES_PLACEHOLDERS = ("<EDIT ME", "<-- EDIT")

TEMPLATE_TESTING_OLD = (
    "- Run tests with: `venv/bin/pytest`      <-- EDIT: your project's real test command.\n"
    "  Never a bare `pytest` unless it is genuinely on PATH."
)


# ---------- helpers ----------


def _log(msg):
    print(f"[qwen-mcp] {msg}", file=sys.stderr, flush=True)


# ---------- detection ----------


def test_dir(cwd):
    """Where this project keeps its tests: `test_dir` in config, else a
    conventional folder that actually exists, else None."""
    cfg = _project_config(cwd)
    named = cfg.get("test_dir")
    if named:
        return str(named)
    for guess in ("tests", "test", "spec", "specs"):
        if os.path.isdir(os.path.join(cwd, guess)):
            return guess
    return None


def detect_test_cmd(cwd):
    """Return a test command string for this project, or '' if nothing detected.

    Config wins over detection. `test_command` in .qwen-delegate.json is an
    exact answer for a project whose layout no detector can guess -- and every
    detector here is a guess, so a project only has to be slightly unusual to
    get a wrong one or none at all. `test_dir` names the folder for the
    discovery fallbacks.

    Ordered detectors after that; first match wins.
    """
    cfg = _project_config(cwd)
    explicit = cfg.get("test_command")
    if explicit:
        return str(explicit)

    j = lambda *p: os.path.join(cwd, *p)  # noqa: E731
    try:
        pkg = j("package.json")
        if os.path.isfile(pkg):
            with open(pkg, errors="replace") as f:
                if '"test"' in f.read():
                    return "npm test"
    except Exception:
        pass
    if os.path.isfile(j("Cargo.toml")):
        return "cargo test"
    if os.path.isfile(j("go.mod")):
        return "go test ./..."
    if os.path.isfile(j("Gemfile")):
        return "bundle exec rspec"
    # pytest's default `python_files` collects test_*.py / *_test.py but NOT
    # *_spec.py -- which is our gate convention. A `*_spec` gate run through a bare
    # pytest therefore collects nothing and passes vacuously (the server flags it
    # `success_but_preflight_passed`). Force collection of both so a gate can't be
    # silently skipped.
    if os.access(j("venv", "bin", "pytest"), os.X_OK):
        return 'venv/bin/pytest -q -o "python_files=test_*.py *_test.py *_spec.py"'
    if os.access(j(".venv", "bin", "pytest"), os.X_OK):
        return '.venv/bin/pytest -q -o "python_files=test_*.py *_test.py *_spec.py"'
    if os.path.isfile(j("pyproject.toml")) or os.path.isfile(j("setup.py")):
        return 'python -m pytest -q -o "python_files=test_*.py *_test.py *_spec.py"'
    # Plain-stdlib layout: a tests folder and no packaging metadata at all.
    # Common enough that returning "" here -- and leaving the caller to guess
    # a directory that may not exist -- is the wrong default.
    d = test_dir(cwd)
    if d:
        # `-t .` makes unittest REQUIRE the start directory to be an importable
        # package, and this branch is reached precisely when there is no
        # packaging metadata -- so it crashed before collecting anything:
        #   ImportError: Start directory is not importable: '.../specs'
        # Measured 2026-08-06 (py3.14) on four shapes; the plain `tests/` +
        # `test_*.py` layout -- the majority case this branch exists for --
        # crashed identically to this repo's `specs/`. The default top level is
        # the start directory itself, which is legal for any folder.
        #
        # Keep `-t .` for the ONE shape where it is both legal and load-bearing:
        # inside a package it is what makes intra-suite relative imports
        # resolve. Same measurement -- dropping it on a tests/ dir holding an
        # __init__.py turns `from .helpers import VALUE` into
        # "ImportError: attempted relative import with no known parent package".
        top = " -t ." if os.path.isfile(j(d, "__init__.py")) else ""
        # `*.py`, not unittest's default `test*.py`, for the same reason the
        # pytest branches above override python_files: the gate convention here
        # is `*_spec.py`, which `test*.py` matches NONE of. No single glob spans
        # test_*.py / *_test.py / *_spec.py, so the choice is which way to be
        # wrong, and collecting too little is the worse way -- it exits 0 having
        # graded a SUBSET, the silent skip the comment above says must not
        # happen, wearing a green receipt.
        #
        # The cost is real and is accepted deliberately: `*.py` also imports
        # every NON-test module in the directory. Measured 2026-08-06 on a
        # package-shaped tests/ holding a helpers.py that fails to import --
        # `-t . -v` gave "Ran 1, OK, exit 0"; `-t . -p "*.py" -v` gives
        # "FAILED (errors=1), exit 1", and __init__.py is imported TWICE, so any
        # side effect in it runs twice. A conftest.py gets imported too. So a
        # broken helper now turns the gate red instead of sitting unnoticed.
        # That is the right trade for a GATE -- a loud false red gets looked at,
        # a silent partial grade does not -- but it is a behaviour change, not a
        # free win. Pinned by DetectedCommandActuallyRuns's package fixtures.
        #
        # Everything interpolated here is quoted, because everything built here
        # is eventually handed to a shell (qd/engine.py runs the detected
        # command with shell=True). The pattern is a constant but would be
        # glob-expanded against the project root before unittest ever saw it.
        # `d` is the dangerous one: it comes from `test_dir` in
        # .qwen-delegate.json, so it is ATTACKER-CONTROLLED for anyone who
        # delegates into a repo carrying a hostile config, and unquoted it was
        # arbitrary command execution, not merely a spaces bug --
        #
        #   test_dir = 'tests; touch /tmp/PWNED ;'
        #     -> python3 -m unittest discover -s tests; touch /tmp/PWNED ; -p ...
        #     -> marker created (reproduced 2026-08-06)
        #
        # shlex.quote and not an f-string '"{d}"': it adds quotes only when the
        # value actually needs them, so the ordinary names ("tests", "specs",
        # "t") pass through byte-identical and the gate's existing string
        # assertions keep pinning the exact command a real project gets, rather
        # than being loosened to accommodate the fix.
        #
        # `top` is a computed literal (" -t ." or ""), and the `-p` value is a
        # constant, so those two carry no taint. The only other config-derived
        # value in this function is `test_command` at the top, which is returned
        # verbatim BY DESIGN -- it is the project declaring its own command, not
        # a fragment interpolated into someone else's.
        return (f'python3 -m unittest discover -s {shlex.quote(d)}{top}'
                f' -p "*.py" -v')
    return ""


# ---------- rendering ----------


def render_worker_rules(test_cmd):
    """Return the QWEN.md text for this project: the template with its testing block
    resolved and its human-facing banner stripped. `test_cmd=''` means the project
    declares it has no tests, which is written as an instruction, not a blank."""
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "templates", "QWEN.md")
    s = open(src, errors="replace").read()
    if test_cmd:
        testing = (
            f"- Run tests with: `{test_cmd}`\n"
            "  Use exactly that command. Do not substitute a bare `pytest`/`npm test` you\n"
            "  assume is on PATH."
        )
    else:
        testing = (
            "- No test command is configured for this project. Do NOT guess one and do NOT\n"
            "  invent a test runner. If the task requires running tests, say so plainly in\n"
            "  your report and stop."
        )
    if TEMPLATE_TESTING_OLD not in s:
        raise RuntimeError("QWEN.md template drifted: testing block not found")
    s = s.replace(TEMPLATE_TESTING_OLD, testing)
    s = re.sub(r"^#\n# TEMPLATE .*?stateless by default\.\n", "", s, flags=re.S | re.M)
    if "TEMPLATE" in s.split("\n\n")[0]:
        raise RuntimeError("QWEN.md template drifted: banner not stripped")
    return s


# ---------- path / status (rules_file seam) ----------


def worker_rules_path(cwd, rules_file="QWEN.md"):
    """
    The rules file whose rules a run in `cwd` would actually load, or None.

    Walks up from cwd to the repo top looking for `rules_file`. Outside a git
    repo only `cwd` itself counts.
    """
    cur = os.path.realpath(cwd)
    rc, top = git(cwd, "rev-parse", "--show-toplevel")
    stop = os.path.realpath(top) if rc == 0 and top else cur
    while True:
        p = os.path.join(cur, rules_file)
        if os.path.isfile(p):
            return p
        parent = os.path.dirname(cur)
        if cur == stop or parent == cur:
            return None
        cur = parent


def worker_rules_status(cwd, rules_file="QWEN.md"):
    """
    ("ok"|"missing"|"placeholder", path_or_None) -- is this project configured?
    """
    p = worker_rules_path(cwd, rules_file)
    if not p:
        return ("missing", None)
    try:
        with open(p, errors="replace") as f:
            text = f.read()
    except Exception as e:
        _log(f"warning: could not read {p}: {e!r} -- treating as configured")
        return ("ok", p)
    if any(m in text for m in RULES_PLACEHOLDERS):
        return ("placeholder", p)
    return ("ok", p)


# ---------- bootstrap write ----------


def bootstrap_worker_rules(cwd, rules_file="QWEN.md"):
    """Create the rules file so a first delegation just works.

    Returns (test_cmd, path), or (None, None) on failure. Best-effort and never
    raises. A partial file is never left behind."""
    try:
        dest = os.path.join(os.path.realpath(cwd), rules_file)
        if os.path.exists(dest):
            try:
                shutil.copy(dest, dest + ".bak")
            except Exception:
                pass
        cmd = detect_test_cmd(cwd)
        text = render_worker_rules(cmd)
        tmp = dest + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, dest)
        register_project(os.path.realpath(cwd))
        return (cmd, dest)
    except Exception as e:
        _log(f"warning: could not bootstrap {rules_file} in {cwd}: {e!r}")
        return (None, None)


# ---------- messages ----------


def bootstrap_failed_refusal(cwd, reason, rules_file="QWEN.md"):
    """The rules file could not be written (IO error, template drift). Refuse rather than
    run unconfigured, and tell the caller how to fix it by hand."""
    return (
        "STATUS: error\n"
        f"Could not create {rules_file} in {cwd} automatically.\n\n"
        f"{reason} Without it the worker's rules are not loaded and it degrades silently, "
        f"so the run is refused. Create {rules_file} at the repo root by hand (any "
        f"content satisfies the check; copy templates/QWEN.md and set the test command), "
        f"then delegate again."
    )


def unconfigured_reason(cwd, state, path, rules_file="QWEN.md"):
    """One paragraph: what is wrong and why it matters. Shared by refusal and warning."""
    if state == "placeholder":
        return (
            f"{path} still contains an unreplaced placeholder "
            f"({RULES_PLACEHOLDERS[0]}... or {RULES_PLACEHOLDERS[1]}...). The worker reads "
            f"that line as an instruction -- it will run the placeholder as a command or "
            f"invent a test command of its own."
        )
    return (
        f"No {rules_file} governs {cwd}. That file is what makes the worker's "
        f"standing rules bind -- it is re-read every session, which is why delegations are "
        f"stateless. Without it the worker has no rule against editing a protected spec "
        f"file, expanding scope, or reporting work it did not do. Measured: it does all "
        f"three."
    )


def nongit_refusal(cwd, rules_file="QWEN.md"):
    """Missing rules AND not a git repo: cannot self-configure safely, so refuse."""
    return (
        "STATUS: error\n"
        f"{cwd} is not a git repository.\n\n"
        "Delegation needs one: git history is the only rollback (there is no sandbox and "
        "the worker runs at your full privilege), and the spec guard detects and reverts "
        "tampering through it.\n\n"
        "Fix it:\n"
        "    git init && git add -A && git commit -m 'baseline'\n\n"
        f"Then delegate again -- I will create {rules_file} automatically."
    )


def bootstrap_notice(test_cmd, path):
    """The SETUP line prepended to a verdict when the rules file was just auto-created.
    Tells the caller what happened, what still needs a human, and to commit it."""
    if test_cmd:
        cmd_line = (
            f"detected the test command as `{test_cmd}` and wrote it in -- tell me if that "
            f"is wrong."
        )
    else:
        cmd_line = (
            "could not detect a test command, so it instructs the worker not to guess one. "
            "If this project has tests, tell me how to run them and I will set it -- gates "
            "are weaker without it."
        )
    return (
        f"SETUP: first delegation here, so I created {path} (the worker's standing rules) "
        f"and {cmd_line} It is uncommitted -- commit it so the rules bind on every run. I "
        f"can also add a delegation policy block to this project's CLAUDE.md so I reach for "
        f"the worker automatically; say the word and I will."
    )


def unconfigured_notice(cwd, state, path, rules_file="QWEN.md"):
    """The read-only counterpart. A query cannot write, so refuse with context."""
    return (
        f"SETUP: {unconfigured_reason(cwd, state, path, rules_file)} Answers are read-only so nothing "
        f"can be damaged, but treat this one as an especially weak lead. A delegation here "
        f"will create {rules_file} automatically."
    )
