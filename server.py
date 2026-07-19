#!/usr/bin/env python3
"""
qwen-delegate MCP server.

Exposes Qwen Code as a delegation tool for Claude. Claude plans and specifies;
Qwen executes in a scoped workspace; an objective verify command confirms the
result before Claude spends context on it.

Three structural protections, because Qwen's self-report is not evidence:

  1. verify gate   -- an objective command decides success, not Qwen's prose.
  2. spec guard    -- *_spec.* files are Claude-authored. If Qwen edits one, it is
                      auto-reverted and the attempt is failed. Qwen has, in practice,
                      rewritten spec tests to make them pass.
  3. iterate loop  -- on failure, the real verify output is fed back and Qwen retries
                      in the same session. Worker tokens are free, so iterating to
                      green costs latency only.

stdio JSON-RPC 2.0, no dependencies. stdout is the protocol channel -- all
diagnostics go to stderr.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

QWEN_BIN = os.environ.get("QWEN_BIN", "qwen")
RESULT_CAP = 3000
VERIFY_CAP = 2500
DEFAULT_TIMEOUT = 900
MAX_TIMEOUT = 7200
DEFAULT_MAX_ITER = 3
DEFAULT_WORKERS = 1

# ---------- run log ----------
# Per-project, because the plugin is used in real projects and the numbers belong with
# the code they describe. The global file is a pointer INDEX only (paths, no metrics) so
# an aggregator can find the per-project logs; it is never itself a metrics store.
# ---------- compaction markers ----------
# Written by compact_hook.py when the CLI compacts a session, read here before resuming
# it. Durable by necessity: the check happens on a LATER process than the compaction.
COMPACT_DIR = os.environ.get("QCOMPACT_DIR") or os.path.expanduser(
    "~/.qwen-delegate/compacted"
)
HOOK_COMPACT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "compact_hook.py")

RUNLOG_DIR = ".qwen-delegate"
RUNLOG_FILE = "runs.jsonl"
PROJECT_REGISTRY = os.environ.get("QWEN_DELEGATE_REGISTRY") or os.path.expanduser(
    "~/.qwen-delegate/projects.jsonl"
)
TASK_HEAD_CHARS = 200
# Prose estimate for the verdict returned to Claude. Raw char count is logged alongside,
# so this divisor can be re-derived later without losing data.
VERDICT_CHARS_PER_TOKEN = 4.0

# Files the guard protects: Claude-authored specs that define what correct means.
# Language-agnostic by convention -- any basename containing `_spec.` or `.spec.`:
#   roman_spec.py  foo.spec.ts  bar_spec.rb  baz.spec.js  qux_spec.go
# A project can override this in <cwd>/.qwen-delegate.json:
#   {"spec_globs": ["*.spec.ts", "tests/contract/*.ts"]}
DEFAULT_SPEC_GLOBS = ["*_spec.*", "*.spec.*"]
PROJECT_CONFIG = ".qwen-delegate.json"

# The file that makes the worker's standing rules bind. Qwen re-reads it every session --
# that reload is the whole reason delegations are stateless by default. Absent, the run
# does not fail, it silently degrades: measured, the worker edits protected spec files,
# games gates, and expands scope. Silence is the danger, so the entry path checks for it.
WORKER_RULES_FILE = "QWEN.md"
# Placeholders a human was supposed to replace. A QWEN.md still carrying one is worse than
# no QWEN.md: the worker reads the line as an instruction and runs (or invents) whatever
# it names. The server never writes these; older hand-made ones are still out there.
RULES_PLACEHOLDERS = ("<EDIT ME", "<-- EDIT")

PROTOCOL_VERSION = "2024-11-05"


def log(msg):
    print(f"[qwen-mcp] {msg}", file=sys.stderr, flush=True)


TOOL = {
    "name": "qwen_delegate",
    "description": (
        "Delegate a well-specified coding task to a local Qwen Code agent. Qwen runs with "
        "shell/edit/write enabled (and Firecrawl web access) in the given workspace and "
        "returns only its final result -- its tool-call noise stays out of your context.\n\n"
        "Use for mechanical, verifiable work (boilerplate, repetitive refactors, test "
        "writing, doc generation, web research), not tasks needing judgment. Qwen is a 27B "
        "local model: given a vague task it does NOT stop and ask -- it confidently invents "
        "scope and reports success. Specify exact files, symbols, and expected behavior.\n\n"
        "ALWAYS pass `verify` -- a shell command exiting 0 only if the task truly succeeded. "
        "Qwen has fabricated 'all tests pass' for tests it never ran; its claim is not "
        "evidence, only the gate is. On failure the tool feeds the real error output back and "
        "retries in the same session (see max_iterations); worker tokens are free, so prefer "
        "letting it iterate over returning a failure to you.\n\n"
        "Files matching *_spec.* / *.spec.* (any language) are YOUR protected spec: if Qwen "
        "edits one it is auto-reverted and the attempt fails. Write gate tests as "
        "<name>_spec.<ext>; let Qwen write its own as <name>_qwen.<ext>. Override per-project "
        "in .qwen-delegate.json {\"spec_globs\": [...]}.\n\n"
        "Re-read any file Qwen touched before editing it yourself -- your cached copy is "
        "stale. Parallel calls MUST use separate `cwd` worktrees."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "The task, specified concretely enough to execute without judgment "
                    "calls: name exact file paths, symbols, and the expected end state. "
                    "Vague tasks produce confident invented scope, not questions."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Absolute path to the workspace. Qwen runs at full user privilege, "
                    "so scope this to the project or worktree. For parallel fan-out, "
                    "give each call its own git worktree."
                ),
            },
            "verify": {
                "type": "string",
                "description": (
                    "Shell command run in `cwd` after each attempt; exit 0 means real "
                    "success (e.g. './gate.sh', 'npm test', 'cargo test', 'venv/bin/pytest -q'). Its "
                    "output is fed back to Qwen on failure, so make failures legible. "
                    "Strongly recommended -- omit only for read-only/research tasks."
                ),
            },
            "max_iterations": {
                "type": "integer",
                "description": (
                    f"Attempts before giving up (1 = one shot, no retry; max 10). On each "
                    "failure the verify output is fed back and Qwen retries warm -- the "
                    "manager is NOT in that loop. Usually omit: the retry budget defaults to "
                    "the project's .qwen-delegate.json `max_iterations`, then the built-in "
                    f"default ({DEFAULT_MAX_ITER}); pass it only to deviate for one call. Each "
                    "attempt is a full build, so it also bounds wall time -- keep it modest."
                ),
            },
            "workers": {
                "type": "integer",
                "description": (
                    "Best-of-N: run up to this many INDEPENDENT candidates for the task and "
                    "accept the first whose gate passes (1 = single candidate, the default; "
                    "max 8). Free worker tokens, zero manager cost -- but each candidate is a "
                    "full build, so it multiplies wall time. Usually omit: defaults to the "
                    f"project's .qwen-delegate.json `workers`, then {DEFAULT_WORKERS}. Needs a "
                    "`verify` gate to pick the winner and a committed base to reset between "
                    "candidates."
                ),
            },
            "on_compaction": {
                "type": "string",
                "enum": ["reinject", "discard"],
                "description": (
                    "YOUR call if Qwen's session gets compacted mid-run. Compaction summarises "
                    "history away and is the documented trigger for fabrication -- after one, "
                    "Qwen claimed to have read 13 files it never opened.\n\n"
                    "'reinject' (default) -- keep the WARM session, restore the task into it. "
                    "Cheap, keeps files already read, but the compaction summary REMAINS IN "
                    "ITS HISTORY (good context beside the possibly-false context that "
                    "fabricates).\n\n"
                    "'discard' -- abandon the session, restart cold on the same tree. Costs a "
                    "fresh ~21.6k preamble and all it had learned, but is the ONLY option that "
                    "removes the corrupted summary; the fresh session re-reads QWEN.md, which "
                    "makes the rules bind.\n\n"
                    "Choose 'discard' when correctness matters more than latency: long "
                    "multi-file work, anything where a false 'I already did that' is expensive, "
                    "or when a compacted run reported something you could not verify. A cold "
                    "restart costs latency only."
                ),
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Resume a prior Qwen session (the SESSION value from an earlier call) to "
                    "continue THAT task with warm context. Sessions are cwd-scoped: pass the "
                    "same cwd or the id will not resolve.\n\n"
                    "STATEFUL when the follow-up builds directly on what Qwen just did ('now "
                    "add X to the function you wrote', 'fix the edge case you missed').\n\n"
                    "STATELESS (omit this) otherwise -- the default, usually correct: a fresh "
                    "session re-reads QWEN.md (which makes the rules bind) and keeps one task's "
                    "reasoning from contaminating the next. A long-lived session drifts as its "
                    "context fills and silently forgets the rules."
                ),
            },
            "shell_allow": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Only with approval_mode='scoped'. Extra regex patterns of shell "
                    "commands to allow, beyond the built-in read-only/test set and the exact "
                    "`verify` command (always allowed). E.g. ['^make build$', "
                    "'^tsc --noEmit$']. This is how you APPROVE a command a prior scoped run "
                    "surfaced as SHELL APPROVAL NEEDED: judge the command alone (safe in this "
                    "repo?), and if yes add its pattern here and re-delegate with the same "
                    "session_id. Keep it tight -- these run at user privilege. "
                    "Compound/redirect/network commands are rejected regardless."
                ),
            },
            "shell_feedback": {
                "type": "string",
                "description": (
                    "Only with approval_mode='scoped'. When you DENY a command a prior run "
                    "requested, put the reason here (e.g. \"denied `rm -rf ~/data`: deletes "
                    "outside the repo; clean only ./build\"). Shown to Qwen up front on "
                    "re-delegation so it understands the constraint and stops retrying. A "
                    "denial without a reason just makes Qwen guess -- always say why."
                ),
            },
            "approval_mode": {
                "type": "string",
                "enum": ["plan", "default", "auto-edit", "auto", "yolo", "scoped"],
                "description": (
                    "Tool-approval policy. Pick the WEAKEST mode that can do the job.\n\n"
                    "Measured behaviour (probed; the bundle does not document this):\n"
                    "  plan       write NO   shell NO   (also blocks agent/exit_plan_mode)\n"
                    "  default    write NO   shell NO   (headless auto-denies -- useless)\n"
                    "  auto-edit  write YES  shell NO   <- BEST DEFAULT for code tasks\n"
                    "  auto       write NO   shell NO   (useless headless, same as default)\n"
                    "  scoped     write cwd  shell ALLOWLIST  <- when Qwen should run tests\n"
                    "  yolo       write YES  shell YES  (only when shell IS the work)\n\n"
                    "'scoped' is auto-edit plus a safe shell: Qwen may run the exact `verify` "
                    "command, a read-only/test allowlist (pytest, git status/diff/log, ls, "
                    "grep, ...), and any `shell_allow` patterns you add, so it checks its own "
                    "work before the gate. Writes confined to cwd; rm/curl/network/git "
                    "push/compound-commands denied; anything blocked is surfaced as "
                    "ELICITATION so you can re-delegate with it allowed. Enforced by a "
                    "PreToolUse hook (yolo underneath so the hook fires); the boundary is "
                    "validated -- an out-of-cwd write and an rm were both blocked.\n\n"
                    "PREFER 'auto-edit' OVER 'yolo'. Qwen does not need shell to converge -- "
                    "THIS server runs `verify` and feeds failures back, so the iterate loop "
                    "is server-driven. Measured: in auto-edit, told to use a banned module, "
                    "Qwen failed the gate on attempt 1, read the feedback, and passed on "
                    "attempt 2 with all 3 of its shell attempts denied. Same convergence as "
                    "yolo, but arbitrary command execution at user privilege is unreachable. "
                    "Use yolo only when running something IS the task (build, migration, git "
                    "ops).\n\n"
                    "USE 'plan' FOR ANY VAGUE TASK -- the only safe way to delegate "
                    "underspecified work: Qwen physically cannot write, so it investigates and "
                    "returns options instead of inventing scope. Two-phase workflow: (1) plan "
                    "with approval_mode='plan' (no verify needed) -> options + a SESSION id; "
                    "(2) let the user choose; (3) execute the chosen option with "
                    "session_id=<from step 1>, approval_mode='auto-edit', verify=<a real gate> "
                    "-> resumes warm.\n\n"
                    "Why (measured): given a vague task in yolo, Qwen did NOT stop and ask -- "
                    "it invented a 4-feature plan, silently changed a public API, and all 909 "
                    "upstream tests still passed because none asserted the changed behaviour. "
                    "A vague task cannot be gated; a chosen plan item can. Never delegate a "
                    "vague task straight to yolo."
                ),
            },
            "timeout_sec": {
                "type": "integer",
                "description": (
                    f"Kill each attempt after this many seconds (default {DEFAULT_TIMEOUT}, "
                    f"max {MAX_TIMEOUT}). ESTIMATE THIS for anything beyond a single small "
                    "file -- the default is sized for ~25k-context tasks and a large task "
                    "WILL be killed mid-write, leaving a partial tree.\n\n"
                    "Fitted from 198 real calls on this box:\n"
                    "    seconds = (turns * avg_context)/10882  +  output_tokens/70\n"
                    "  prefill ~10,882 tok/s; decode ~70 tok/s (measured). avg_context ~= "
                    "(22000 + peak_context)/2, since context grows from a ~22k baseline and "
                    "every turn re-prefills the whole context.\n\n"
                    "Worked example -- a task peaking at 120k over ~30 turns emitting ~15k "
                    "tokens: avg_context=(22000+120000)/2=71000, prefill=30*71000/10882=196s, "
                    "decode=15000/70=214s, ~410s -> set timeout_sec ~1200 (about 3x).\n\n"
                    "Use 2-3x headroom: the estimate is a median and p90 calls ran 3x median. "
                    "Over-setting costs nothing (the timeout only fires on a hang); "
                    "under-setting destroys the run."
                ),
            },
        },
        "required": ["task", "cwd"],
    },
}


# ---------- git-backed spec guard ----------


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


def worker_rules_path(cwd):
    """
    The QWEN.md whose rules a run in `cwd` would actually load, or None.

    Qwen loads context files hierarchically, so a subdirectory of a configured repo is
    configured -- delegating into `repo/src` must not be refused because the rules sit at
    `repo/`. The walk stops AT the repo top level: a QWEN.md above it belongs to some
    other project (or to $HOME) and is not this project's rules. Outside a git repo there
    is no top level to trust, so only `cwd` itself counts.
    """
    cur = os.path.realpath(cwd)
    rc, top = git(cwd, "rev-parse", "--show-toplevel")
    stop = os.path.realpath(top) if rc == 0 and top else cur
    while True:
        p = os.path.join(cur, WORKER_RULES_FILE)
        if os.path.isfile(p):
            return p
        parent = os.path.dirname(cur)
        if cur == stop or parent == cur:
            return None
        cur = parent


def worker_rules_status(cwd):
    """
    ("ok"|"missing"|"placeholder", path_or_None) -- is this project configured to delegate?
    """
    p = worker_rules_path(cwd)
    if not p:
        return ("missing", None)
    try:
        with open(p, errors="replace") as f:
            text = f.read()
    except Exception as e:
        # Our own IO problem, not the project's. Qwen is the reader that matters and it
        # reads the file itself; do not block a run on a stat we could not take.
        log(f"warning: could not read {p}: {e!r} -- treating as configured")
        return ("ok", p)
    if any(m in text for m in RULES_PLACEHOLDERS):
        return ("placeholder", p)
    return ("ok", p)


def bootstrap_failed_refusal(cwd, reason):
    """The rules file could not be written (IO error, template drift). Refuse rather than
    run unconfigured, and tell the caller how to fix it by hand -- there is no setup
    script to fall back to."""
    return (
        "STATUS: error\n"
        f"Could not create {WORKER_RULES_FILE} in {cwd} automatically.\n\n"
        f"{reason} Without it the worker's rules are not loaded and it degrades silently, "
        f"so the run is refused. Create {WORKER_RULES_FILE} at the repo root by hand (any "
        f"content satisfies the check; copy templates/QWEN.md and set the test command), "
        f"then delegate again."
    )


def unconfigured_reason(cwd, state, path):
    """One paragraph: what is wrong and why it matters. Shared by refusal and warning."""
    if state == "placeholder":
        return (
            f"{path} still contains an unreplaced placeholder "
            f"({RULES_PLACEHOLDERS[0]}... or {RULES_PLACEHOLDERS[1]}...). The worker reads "
            f"that line as an instruction -- it will run the placeholder as a command or "
            f"invent a test command of its own."
        )
    return (
        f"No {WORKER_RULES_FILE} governs {cwd}. That file is what makes the worker's "
        f"standing rules bind -- it is re-read every session, which is why delegations are "
        f"stateless. Without it the worker has no rule against editing a protected spec "
        f"file, expanding scope, or reporting work it did not do. Measured: it does all "
        f"three."
    )


def nongit_refusal(cwd):
    """Missing rules AND not a git repo: cannot self-configure safely, so refuse.

    Auto-writing QWEN.md needs a repo to write into safely -- and more fundamentally,
    delegation itself needs git: it is the only rollback, the spec guard reverts through
    it, and there is no sandbox. So the fix here is `git init`, not the rules file.
    """
    return (
        "STATUS: error\n"
        f"{cwd} is not a git repository.\n\n"
        "Delegation needs one: git history is the only rollback (there is no sandbox and "
        "the worker runs at your full privilege), and the spec guard detects and reverts "
        "tampering through it.\n\n"
        "Fix it:\n"
        "    git init && git add -A && git commit -m 'baseline'\n\n"
        f"Then delegate again -- I will create {WORKER_RULES_FILE} automatically."
    )


# ---------- self-configuration (auto-bootstrap) ----------
#
# A first delegation into a real repo should just work, not fail with "run this script".
# So when a git repo has no QWEN.md, the server writes one itself: it detects the test
# command, or -- when it cannot -- writes an instruction NOT to guess one. It never writes
# a placeholder, which is the failure the refusal was guarding against. The test command
# is the only project-specific part; everything else is the fixed worker contract, safe to
# write unattended. CLAUDE.md is deliberately NOT touched here: it is the user's file, so
# adding the policy block stays consent-gated (offered in the result, applied only if the
# user says yes).

# Ordered detectors, first match wins. Detection lives here in Python so there is one
# source of truth and no bash copy to drift out of sync.
def detect_test_cmd(cwd):
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
    if os.access(j("venv", "bin", "pytest"), os.X_OK):
        return "venv/bin/pytest -q"
    if os.access(j(".venv", "bin", "pytest"), os.X_OK):
        return ".venv/bin/pytest -q"
    if os.path.isfile(j("pyproject.toml")) or os.path.isfile(j("setup.py")):
        return "python -m pytest -q"
    return ""


# The line in templates/QWEN.md that the test command replaces. If the template drifts,
# rendering must fail loudly rather than emit a QWEN.md with a stale or empty testing rule.
TEMPLATE_TESTING_OLD = (
    "- Run tests with: `venv/bin/pytest`      <-- EDIT: your project's real test command.\n"
    "  Never a bare `pytest` unless it is genuinely on PATH."
)


def render_worker_rules(test_cmd):
    """Return the QWEN.md text for this project: the template with its testing block
    resolved and its human-facing banner stripped. `test_cmd=''` means the project
    declares it has no tests, which is written as an instruction, not a blank."""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "templates", WORKER_RULES_FILE)
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


def bootstrap_worker_rules(cwd):
    """Create QWEN.md so a first delegation just works. Returns (test_cmd, path), or
    (None, None) on failure so the caller can fall back to refusing. Best-effort and
    never raises. A pre-existing placeholder/legacy file is backed up, not silently lost.

    Written atomically via a temp file: a torn QWEN.md read mid-write by Qwen would be
    worse than none."""
    try:
        dest = os.path.join(os.path.realpath(cwd), WORKER_RULES_FILE)
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
        log(f"warning: could not bootstrap {WORKER_RULES_FILE} in {cwd}: {e!r}")
        return (None, None)


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


def unconfigured_notice(cwd, state, path):
    """
    The read-only counterpart. A query cannot write, so the rules that prevent damage are
    not in play and refusing would be gratuitous friction on the cheapest way to try this
    system out. The honesty and context-size rules still are, so say so and proceed.
    """
    return (
        f"SETUP: {unconfigured_reason(cwd, state, path)} Answers are read-only so nothing "
        f"can be damaged, but treat this one as an especially weak lead. A delegation here "
        f"will create {WORKER_RULES_FILE} automatically."
    )


def project_config(cwd):
    """Parsed <cwd>/.qwen-delegate.json (the per-project override file), or {}.

    Recognised keys: `spec_globs` (list) and `max_iterations` (int, the retry budget).
    A missing or corrupt file is treated as no overrides, never an error."""
    try:
        p = os.path.join(cwd, PROJECT_CONFIG)
        if os.path.isfile(p):
            with open(p) as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception as e:
        log(f"warning: could not read {PROJECT_CONFIG}: {e!r}; using defaults")
    return {}


def spec_globs(cwd):
    """Protected-spec patterns for this project. Per-project config wins."""
    globs = project_config(cwd).get("spec_globs")
    if isinstance(globs, list) and globs:
        return [str(g) for g in globs]
    return DEFAULT_SPEC_GLOBS


def resolve_max_iter(cwd, arg):
    """Retry budget: attempts = 1 initial + (N-1) retries. Precedence:
    per-call arg > project `.qwen-delegate.json` max_iterations > built-in default.
    Clamped to [1, 10]. Each attempt is a full worker build, so this also bounds wall
    time -- keep it modest."""
    cfg = project_config(cwd).get("max_iterations")
    return max(1, min(10, int(arg or cfg or DEFAULT_MAX_ITER)))


def resolve_workers(cwd, arg):
    """Best-of-N breadth (#26): number of INDEPENDENT candidates to try for one task,
    accepting the first whose gate passes. Precedence: per-call arg > project
    `.qwen-delegate.json` workers > built-in default (1). 1 = single candidate (current
    behaviour). Clamped to [1, 8]. Candidates cost free worker tokens + wall time only."""
    cfg = project_config(cwd).get("workers")
    return max(1, min(8, int(arg or cfg or DEFAULT_WORKERS)))


def reset_worktree(cwd, sha):
    """Reset the working tree to a committed base so the next best-of-N candidate starts
    clean and independent. clean -fd, NEVER -fdx (-fdx destroys a gitignored venv)."""
    git(cwd, "reset", "--hard", sha)
    git(cwd, "clean", "-fd")


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
    now = head_sha(cwd)
    if not now or now == pre_sha:
        return False, 0, []
    rc, out = git(cwd, "rev-list", "--count", f"{pre_sha}..HEAD")
    count = int(out) if rc == 0 and out.strip().isdigit() else 0
    rc, out = git(cwd, "diff", "--name-only", pre_sha, "HEAD")
    files = [p for p in (out or "").splitlines() if p.strip()] if rc == 0 else []
    return True, count, files


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


# ---------- handoff ----------

# Appended to every task. Gives Claude a compact, structured basis for deciding whether
# to continue this session or start fresh -- without reading Qwen's full prose. FILES is
# cross-checked against the filesystem in render(): a mismatch means Qwen misreported
# its own blast radius, which is exactly the class of error the gate exists to catch.
HANDOFF_SUFFIX = """

---
Finish your reply with exactly these three lines, after any prose:

HANDOFF: <one line: what state the work is in now>
FILES: <comma-separated paths you created or modified, or the word: none>
NEXT: <one line: what a follow-up would need to know, or the word: nothing>

Keep each line under 120 characters. This is a machine-read handoff, not prose.
"""


def parse_handoff(text):
    """Pull the HANDOFF/FILES/NEXT lines out of Qwen's reply."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip().lstrip("*# ").strip()
        for key in ("HANDOFF", "FILES", "NEXT"):
            prefix = f"{key}:"
            if line.upper().startswith(prefix):
                out[key] = line[len(prefix):].strip().strip("*`").strip()
    return out


def strip_handoff(text):
    """Remove the handoff lines from prose so they aren't shown twice."""
    keep = []
    for line in (text or "").splitlines():
        probe = line.strip().lstrip("*# ").strip().upper()
        if any(probe.startswith(f"{k}:") for k in ("HANDOFF", "FILES", "NEXT")):
            continue
        keep.append(line)
    return "\n".join(keep).strip()


# ---------- investigate: cheap, bounded, read-only context-building ----------

# Appended to an investigate task. Forces a structured, verifiable map rather than
# prose -- and, crucially, a VERIFY section. Qwen's investigation is broad and cheap but
# its conclusions are often wrong in plausible ways (measured: 3/5 options on one real
# plan rested on a misread of control flow). So the map is a lead, not a fact: it says
# WHERE to look, and flags which claims the caller must confirm against source itself.
INVESTIGATE_SUFFIX = """

---
You are in read-only investigation mode. Do NOT propose changes or write code. Use
glob/grep/targeted reads; do not read whole files "to be thorough" -- stay small.

Return ONLY this structure, nothing else:

MAP:
- <path> — <one line: what it is / what it exposes>
  (one bullet per relevant file; skip irrelevant files)

KEY SYMBOLS:
- <name> in <path> — <what it does>
  (the functions/classes/types that matter for the question; omit if not applicable)

CONNECTIONS:
- <how the relevant pieces call/depend on each other; the seams that matter>

ANSWER: <2-4 sentences directly answering the question you were asked>

VERIFY (load-bearing claims the caller must confirm against source before relying on
them — be honest about what you inferred vs. read directly):
- <claim> — <the symbol/path to grep for to check it>

Reference symbols by NAME and file (e.g. `dasherize in inflection/__init__.py`), never
by line number. You do not track line numbers reliably and a wrong number is worse than
none — a name can be grepped, a guessed line cannot. If you did not actually read
something, say so under VERIFY instead of asserting you confirmed it.
"""

# Freeform read-only answer -- the default query format. Grounded (cite file:symbol),
# with a VERIFY section, because Qwen's conclusions are often plausibly wrong.
ANSWER_SUFFIX = """

---
You are in read-only mode. Do NOT write, edit, or propose code changes -- only read
(glob/grep/targeted reads) and answer. Do not read whole files "to be thorough"; stay
small.

Answer the question directly and concretely. Cite evidence by NAME and file
(`validate_token in auth/tokens.py`), never by line number -- you do not track line
numbers reliably, and a name can be grepped while a guessed line cannot. If you are
inferring rather than confirming, say so. Finish with:

VERIFY: <the specific claims a decision should not rest on until checked against source,
each with the symbol/path to grep. If you did not actually read something, say so here
rather than asserting you confirmed it.>
"""

QUERY_TOOL = {
    "name": "qwen_query",
    "description": (
        "Ask the local Qwen worker an open-ended question ABOUT the code, READ-ONLY. Qwen "
        "reads (glob/grep/targeted reads) and answers; it cannot write or change anything "
        "(plan mode -- structurally safe, no gate needed). Its tokens are free, so you get an "
        "answer instead of spending your own context on the files.\n\n"
        "Use it to think with the codebase without touching it: 'how does auth flow to the "
        "token check?', 'is there already a duration parser?', 'what breaks if I change "
        "load()'s return type?'. It is a conversation -- pass the returned SESSION as "
        "`session_id` for a warm follow-up.\n\n"
        "format='answer' (default) gives a direct prose answer with a VERIFY list; "
        "format='map' gives a structured MAP / KEY SYMBOLS / CONNECTIONS / ANSWER / VERIFY to "
        "orient in an unfamiliar repo (the old qwen_investigate).\n\n"
        "Treat the answer as a LEAD, not truth: Qwen's reading is broad but its conclusions "
        "are often wrong in plausible ways (it once mapped a library perfectly yet fabricated "
        "every line number). VERIFY says what to confirm against source before a decision "
        "rests on it.\n\n"
        "Keep each question BOUNDED -- a few files, not 'read the whole repo': a huge read "
        "pushes Qwen past compaction, after which it fabricates having read things. Several "
        "small queries, not one giant one; the response reports peak context so you can see it "
        "stayed safe."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "The question to answer by reading the code. Open-ended is fine "
                    "('how does X work', 'why might Y fail', 'is there already a Z'). Scope "
                    "it so a few files answer it, not the whole repo at once."
                ),
            },
            "cwd": {"type": "string", "description": "Absolute path to the repository."},
            "format": {
                "type": "string",
                "enum": ["answer", "map"],
                "description": (
                    "'answer' (default): direct prose answer + VERIFY. 'map': structured "
                    "codebase map (MAP/KEY SYMBOLS/CONNECTIONS/ANSWER/VERIFY) for orienting."
                ),
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Resume a prior query conversation (the SESSION from an earlier "
                    "qwen_query) to ask a warm follow-up -- Qwen still holds what it read. "
                    "Same cwd required. Omit to start fresh."
                ),
            },
            "focus": {
                "type": "string",
                "description": (
                    "Optional: narrow the reading to a subdir ('src/parser'), glob "
                    "('**/*.ts'), or files. Keeps it bounded and cheap."
                ),
            },
            "timeout_sec": {
                "type": "integer",
                "description": f"Kill the query after this many seconds (default {DEFAULT_TIMEOUT}).",
            },
        },
        "required": ["question", "cwd"],
    },
}


def run_query(args):
    question = args["question"]
    cwd = args["cwd"]
    focus = args.get("focus")
    fmt = args.get("format") or "answer"
    session_id = args.get("session_id")
    timeout = max(30, min(MAX_TIMEOUT, int(args.get("timeout_sec") or DEFAULT_TIMEOUT)))

    if not os.path.isabs(cwd):
        return f"STATUS: error\ncwd must be an absolute path, got: {cwd}"
    if not os.path.isdir(cwd):
        return f"STATUS: error\ncwd does not exist or is not a directory: {cwd}"

    rules_state, rules_path = worker_rules_status(cwd)

    suffix = INVESTIGATE_SUFFIX if fmt == "map" else ANSWER_SUFFIX
    verb = "Map this codebase to answer" if fmt == "map" else "Answer this question about the code"
    prompt = f"{verb}.\n\nQUESTION: {question}"
    if focus:
        prompt += f"\n\nFOCUS your reading on: {focus}"

    log(f"query cwd={cwd} fmt={fmt} focus={focus or '-'} resume={session_id or '-'}")

    # plan mode: read-only by construction. No verify, no git snapshot -- nothing changes.
    text, denials, sid, err, meta = invoke_qwen(
        prompt, cwd, "plan", timeout, session_id, suffix=suffix
    )
    def _log_query(status, verdict):
        write_runlog(cwd, leverage_record(
            "qwen_query", cwd, status, verdict,
            meta.get("stats") or {}, meta.get("peak", 0),
            extra={
                "session": sid,
                "approval_mode": "plan",
                "format": fmt,
                "question": digest(question),
                "focus": focus or None,
                "resumed": bool(session_id),
            },
        ))

    # Errors are logged too: a timed-out or unparseable query still burned the tokens.
    if err:
        verdict = f"STATUS: error\n{err}"
        _log_query("error", verdict)
        return verdict

    lines = ["STATUS: ok", f"SESSION: {sid or 'unknown'}"]

    # Warn, do not refuse: see unconfigured_notice().
    if rules_state != "ok":
        lines.append(unconfigured_notice(cwd, rules_state, rules_path))

    # Compaction is the failure mode for a read that got too big: past it Qwen fabricates.
    peak = meta.get("peak", 0)
    win = context_window()
    if peak and win:
        _, auto_at = compaction_thresholds(win)
        pct = 100.0 * peak / win
        if peak >= auto_at:
            lines.append(
                f"CONTEXT: peak {peak:,}/{win:,} ({pct:.0f}%) -- COMPACTION LIKELY FIRED. "
                f"This read was too big; parts of the answer may be fabricated. Re-run with "
                f"a tighter `focus`, or split into smaller questions."
            )
        elif pct >= 60:
            lines.append(
                f"CONTEXT: peak {peak:,}/{win:,} ({pct:.0f}%) -- getting large; narrow "
                f"`focus` if you need more depth."
            )
        else:
            lines.append(f"CONTEXT: peak {peak:,}/{win:,} ({pct:.0f}%) -- safe, well under compaction")
    elif peak:
        lines.append(f"CONTEXT: peak {peak:,} tokens")

    st = meta.get("stats") or {}
    if st.get("tools"):
        lines.append(f"READS: {st['tools']} tool call(s), {st.get('ms',0)/1000:.0f}s")

    label = "map" if fmt == "map" else "answer"
    lines.append(f"--- {label} ---\n{truncate(text, RESULT_CAP + 1500)}")
    verdict = "\n".join(lines)
    _log_query("ok", verdict)
    return verdict


def run_investigate(args):
    """Back-compat alias: the codebase map is now qwen_query(format='map')."""
    a = dict(args)
    a["format"] = "map"
    return run_query(a)


# ---------- qwen invocation ----------

HOOK_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scoped_hook.py")


def scoped_setup(cwd, verify, shell_allow):
    """
    Wire the PreToolUse allowlist hook for approval_mode='scoped' WITHOUT touching the
    repo or ~/.qwen. Runs qwen in yolo (so the hook fires) but the hook enforces:
    shell allowlist + the exact verify command, writes confined to cwd, everything else
    denied and logged. Returns (env_overrides, denylog_path, tempdir_to_clean).
    """
    td = tempfile.mkdtemp(prefix="qgate-")
    denylog = os.path.join(td, "denied.log")
    sys_settings = os.path.join(td, "settings.json")
    with open(sys_settings, "w") as f:
        json.dump({"hooks": {"PreToolUse": [
            {"matcher": ".*", "hooks": [
                {"type": "command", "command": f"python3 {HOOK_SCRIPT}"}]}],
            **compact_hooks()}}, f)
    env = {
        "QWEN_CODE_SYSTEM_SETTINGS_PATH": sys_settings,
        "QGATE_CWD": os.path.realpath(cwd),
        "QGATE_VERIFY": verify or "",
        "QGATE_DENYLOG": denylog,
        "QGATE_EXTRA": json.dumps(shell_allow or []),
        "QCOMPACT_DIR": COMPACT_DIR,
    }
    return env, denylog, td


def compact_hooks():
    """The PreCompact/PostCompact hook block. Needed in EVERY mode -- any session can be
    compacted, and a missed marker means resuming a session whose task was summarised
    away."""
    entry = [{"hooks": [{"type": "command", "command": f"python3 {HOOK_COMPACT}"}]}]
    return {"PreCompact": entry, "PostCompact": entry}


def compact_setup():
    """Hook-only settings for non-scoped modes (scoped_setup covers scoped itself)."""
    td = tempfile.mkdtemp(prefix="qcompact-")
    sys_settings = os.path.join(td, "settings.json")
    with open(sys_settings, "w") as f:
        json.dump({"hooks": compact_hooks()}, f)
    return {"QWEN_CODE_SYSTEM_SETTINGS_PATH": sys_settings,
            "QCOMPACT_DIR": COMPACT_DIR}, td


def invoke_qwen(task, cwd, approval_mode, timeout, session_id,
                suffix=HANDOFF_SUFFIX, verify=None, shell_allow=None):
    real_mode = approval_mode
    scoped_env = {}
    denylog = None
    tempdir = None
    if approval_mode == "scoped":
        real_mode = "yolo"  # yolo so the hook is consulted; the hook does the gating
        scoped_env, denylog, tempdir = scoped_setup(cwd, verify, shell_allow)
    else:
        # Compaction hooks still have to be installed: any mode can be compacted.
        scoped_env, tempdir = compact_setup()

    cmd = [
        QWEN_BIN, "-p", task + suffix,
        "--approval-mode", real_mode, "-o", "json",
    ]
    if session_id:
        cmd += ["-r", session_id]

    env = dict(os.environ)
    env["QWEN_CODE_SUPPRESS_YOLO_WARNING"] = "1"
    env.update(scoped_env)

    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        _cleanup(tempdir)
        return None, [], None, f"timed out after {timeout}s", {}
    except FileNotFoundError:
        _cleanup(tempdir)
        return None, [], None, f"qwen binary not found (QWEN_BIN={QWEN_BIN})", {}

    blocked = _read_denylog(denylog)
    _cleanup(tempdir)

    text, denials, sid = parse_qwen_json(proc.stdout)
    meta = {"peak": peak_context(proc.stdout), "stats": parse_stats(proc.stdout),
            "blocked": blocked}
    if text is None:
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        return None, [], sid, f"unparseable output (exit {proc.returncode}): {tail}", meta
    return text, denials, sid, None, meta


def _read_denylog(path):
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path) as f:
            seen, out = set(), []
            for line in f:
                line = line.strip()
                if line and line not in seen:
                    seen.add(line)
                    out.append(line)
            return out
    except Exception:
        return []


def _cleanup(td):
    if td and os.path.isdir(td):
        try:
            for root, _, files in os.walk(td, topdown=False):
                for fn in files:
                    os.remove(os.path.join(root, fn))
            os.rmdir(td)
        except Exception:
            pass


def run_verify(verify, cwd):
    """Return (passed, combined_output)."""
    try:
        v = subprocess.run(
            verify, cwd=cwd, shell=True, capture_output=True, text=True, timeout=300
        )
        out = ((v.stdout or "") + (v.stderr or "")).strip()
        return v.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "verify command timed out after 300s"


def run_qwen(args):
    """Best-of-N entry point (#26). Runs up to `workers` independent candidates from the
    same committed base and accepts the first whose GATE passes -- the gate, not any
    candidate's self-report, selects the winner. workers=1 (default) is a transparent
    passthrough to the single-candidate path, byte-identical to before. Candidates run
    sequentially and reset the tree between them (correct on a single-worker box); true
    parallel execution across worktrees is the upgrade extra workers unlock. Zero Claude
    tokens either way -- the manager is never in this loop."""
    cwd = args.get("cwd", "")
    verify = args.get("verify")
    workers = resolve_workers(cwd, args.get("workers")) if os.path.isdir(cwd) else 1
    if workers <= 1 or not verify:
        return _delegate_once(args)
    base_sha = head_sha(cwd) if is_git_repo(cwd) else None
    if not base_sha:
        # No committed base to isolate candidates against -> fall back to one candidate.
        return _delegate_once(args)

    last = None
    for c in range(workers):
        if c > 0:
            reset_worktree(cwd, base_sha)  # independent candidate: clean base
        cand = dict(args)
        cand["session_id"] = None          # fresh session per candidate (re-reads QWEN.md)
        log(f"best-of-{workers}: candidate {c + 1}/{workers} from {base_sha[:8]}")
        last = _delegate_once(cand)
        passed, _ = run_verify(verify, cwd)
        if passed:
            log(f"best-of-{workers}: candidate {c + 1} passed the gate -- accepting")
            return last
    log(f"best-of-{workers}: no candidate passed the gate")
    return last


def _delegate_once(args):
    task = args["task"]
    cwd = args["cwd"]
    verify = args.get("verify")
    approval_mode = args.get("approval_mode", "yolo")
    timeout = max(30, min(MAX_TIMEOUT, int(args.get("timeout_sec") or DEFAULT_TIMEOUT)))
    max_iter = resolve_max_iter(cwd, args.get("max_iterations"))
    session_id = args.get("session_id")
    on_compaction = args.get("on_compaction") or "reinject"
    if on_compaction not in ("reinject", "discard"):
        on_compaction = "reinject"

    if not os.path.isabs(cwd):
        return f"STATUS: error\ncwd must be an absolute path, got: {cwd}"
    if not os.path.isdir(cwd):
        return f"STATUS: error\ncwd does not exist or is not a directory: {cwd}"

    guard_on = is_git_repo(cwd)
    if not guard_on:
        log(f"warning: {cwd} is not a git repo -- spec guard and rollback unavailable")

    # An unconfigured project must not run degraded -- without QWEN.md the worker has no
    # rule against editing a protected spec, expanding scope, or reporting work it never
    # did. But refusing outright made a first delegation fail with "go run a script".
    # Instead: in a git repo, self-configure -- write QWEN.md (detecting the test command,
    # or instructing the worker not to guess one; never a placeholder) and carry on. Only
    # the test command is project-specific and unguessable, and the honest "no command"
    # instruction is a safe answer to that. Non-git is different: it cannot roll back at
    # all, so it is refused, not bootstrapped.
    bootstrap_note = None
    rules_state, rules_path = worker_rules_status(cwd)
    if rules_state != "ok":
        if not guard_on:
            log(f"refusing: {rules_state} rules and non-git {cwd}")
            return nongit_refusal(cwd)
        cmd, path = bootstrap_worker_rules(cwd)
        if not path:
            return bootstrap_failed_refusal(
                cwd, unconfigured_reason(cwd, rules_state, rules_path))
        log(f"bootstrapped {WORKER_RULES_FILE} for {cwd} (test_cmd={cmd or 'none'})")
        bootstrap_note = bootstrap_notice(cmd, path)

    # The guard reverts any spec file that differs from HEAD after a run, and cannot
    # tell who edited it. If a spec is ALREADY dirty, that revert would silently
    # destroy Claude's uncommitted work and blame Qwen for it. Refuse instead --
    # this makes "commit before delegating" a precondition rather than a habit.
    if guard_on:
        pre_dirty = violated_specs(cwd)
        if pre_dirty:
            names = ", ".join(pre_dirty)
            return (
                f"STATUS: error\nUncommitted changes in protected spec file(s): {names}\n\n"
                f"The spec guard reverts post-run spec diffs to HEAD and cannot attribute "
                f"them, so running now would destroy this uncommitted work. Commit or stash "
                f"the spec changes first, then delegate."
            )

    # Snapshot pre-run state so the result can report what Qwen actually touched and
    # how to undo it, rather than what Qwen claims it touched.
    pre_status = snapshot(cwd) if guard_on else {}
    pre_sha = head_sha(cwd) if guard_on else None
    pre_clean = guard_on and not pre_status

    # Pre-flight the gate. If verify ALREADY passes, a post-run pass proves nothing --
    # it cannot distinguish "Qwen did the work" from "Qwen did nothing" from "the task's
    # premise was false". The false-premise test passed for exactly this reason.
    preflight = None
    preflight_out = ""
    if verify:
        preflight, preflight_out = run_verify(verify, cwd)
        log(f"preflight verify: {'pass' if preflight else 'fail'}")

    # The manager's approval decisions from a prior round. Prepended so Qwen sees, up
    # front, which of its earlier requests were allowed/denied and WHY -- so it does not
    # blindly retry a denied command and knows the constraint to work within.
    feedback = (args.get("shell_feedback") or "").strip()
    prompt = task
    if feedback:
        prompt = (
            "APPROVAL RESULT for shell commands you requested earlier "
            "(from the manager reviewing them):\n"
            f"{feedback}\n"
            "Respect these: do NOT retry a denied command; use the allowed ones or an "
            "alternative. Now continue the task below.\n\n---\n\n"
            + task
        )

    trail = []
    result_text = ""
    denials = []
    send_suffix = False  # set when a retry re-injects after compaction
    ctx = {
        "approval_mode": approval_mode,
        "timeout": timeout,
        "meta": {},
        "peak": 0,
        "preflight_out": preflight_out,
        "pre_status": pre_status,
        "pre_sha": pre_sha,
        "pre_clean": pre_clean,
        "preflight": preflight,
        "guard_on": guard_on,
        "cwd": cwd,
        # Run totals for the log. ctx["meta"] is last-attempt only; this is every attempt.
        "cum": cum_zero(),
        "task": task,
        "verify": verify,
        "on_compaction": on_compaction,
        "sessions": [session_id] if session_id else [],
        "bootstrap_note": bootstrap_note,
        "max_iter": max_iter,
        "session_hint": session_id,
    }

    prev_v_out = None  # previous attempt's verify output, to detect a repeated failure (#24)
    for attempt in range(1, max_iter + 1):
        log(f"attempt {attempt}/{max_iter} cwd={cwd} resume={session_id or '-'}")

        # The handoff instructions are already in the session from attempt 1, so
        # re-appending them on a retry just duplicates them. Send again only when
        # re-injecting after a compaction, which is exactly when they may have been
        # summarised away.
        suffix = HANDOFF_SUFFIX if (attempt == 1 or send_suffix) else ""
        send_suffix = False

        result_text, denials, sid, err, meta = invoke_qwen(
            prompt, cwd, approval_mode, timeout, session_id,
            suffix=suffix, verify=verify, shell_allow=args.get("shell_allow"),
        )
        ctx["meta"] = meta
        ctx["peak"] = max(ctx.get("peak", 0), meta.get("peak", 0))
        accum_stats(ctx["cum"], meta.get("stats"))
        if sid:
            session_id = sid  # resume this session on retry
        # Every session this run touched. A discard starts a NEW one, so counting
        # compactions off the final session_id alone would report the fresh session's
        # total and silently drop the discarded session's -- a run could show
        # "discards: 1, compactions: 0", which is self-contradictory.
        if sid and sid not in ctx["sessions"]:
            ctx["sessions"].append(sid)

        if err:
            trail.append(f"attempt {attempt}: {err}")
            return render(
                "error", session_id, trail, result_text or "", denials, max_iter, ctx
            )

        # spec guard: Qwen must never edit Claude's protected spec files
        # Diff against the sha the run STARTED from, not HEAD: if Qwen edited a spec and
        # committed it, HEAD moved with it and a plain diff sees nothing.
        cheated = violated_specs(cwd, base=pre_sha) if guard_on else []
        if cheated:
            revert_specs(cwd, cheated, base=pre_sha)
            names = ", ".join(cheated)
            trail.append(
                f"attempt {attempt}: SPEC VIOLATION -- edited {names} (auto-reverted)"
            )
            if attempt < max_iter:
                # Same delta-only rule as the verify retry: the session still holds the
                # task unless it was compacted.
                prompt = (
                    f"You edited a protected specification file ({names}). That file "
                    f"defines what correct means and has been reverted. Never modify a "
                    f"protected spec file. Fix the implementation code so it satisfies the "
                    f"spec as written. If you believe the spec is wrong, stop and say so "
                    f"instead of editing it."
                )
                if was_compacted_since_ack(session_id):
                    ack_compaction(session_id)
                    send_suffix = True
                    if on_compaction == "discard":
                        log(f"session {session_id} compacted -- discarding, restarting cold")
                        ctx["discards"] = ctx.get("discards", 0) + 1
                        session_id = None
                    else:
                        log(f"session {session_id} compacted -- re-injecting task context")
                        ctx["reinjects"] = ctx.get("reinjects", 0) + 1
                    prompt += (
                        f"\n\nYour conversation history was summarised (compacted), so "
                        f"you may have lost the original instructions and any summary of "
                        f"your earlier work may be inaccurate. Re-read the files; do not "
                        f"reconstruct it.\n\nOriginal task:\n{task}"
                    )
                continue
            return render(
                "spec_violation", session_id, trail, result_text, denials, max_iter, ctx
            )

        if not verify:
            trail.append(f"attempt {attempt}: no verify supplied")
            return render(
                "unverified", session_id, trail, result_text, denials, max_iter, ctx
            )

        passed, v_out = run_verify(verify, cwd)
        if passed:
            trail.append(f"attempt {attempt}: VERIFY PASS")
            return render("success", session_id, trail, result_text, denials, max_iter, ctx)

        trail.append(f"attempt {attempt}: verify failed")

        # If the gate produces byte-identical output before and after Qwen worked, then
        # nothing Qwen does moves it -- the gate is malformed, or tests something the
        # task never touches. Iterating is pure waste: a real case burned 3 attempts and
        # 64 session records thrashing against a verify command whose quoting was broken,
        # while the code on disk was correct the entire time. Bail with the diagnosis.
        if preflight is False and v_out.strip() == (preflight_out or "").strip():
            # Replace, don't append -- trail length is the attempt count.
            trail[-1] = (
                f"attempt {attempt}: verify failed -- output IDENTICAL to preflight"
            )
            return render(
                "gate_suspect",
                session_id,
                trail,
                result_text,
                denials,
                max_iter,
                ctx,
                last_verify=v_out,
            )

        # Same failure as last attempt -> plain error-feedback is looping; tell Qwen to
        # change approach rather than retry a variation (#24 Reflexion).
        repeated = prev_v_out is not None and v_out.strip() == prev_v_out.strip()
        prev_v_out = v_out

        if attempt < max_iter:
            prompt, action = retry_prompt(
                session_id, task, verify, v_out, on_compaction, repeated=repeated
            )
            if action != "none":
                ctx[action + "s"] = ctx.get(action + "s", 0) + 1
                send_suffix = True  # a restored/cold session needs the handoff format
                if action == "discard":
                    # Dropping the id is what actually discards it: the next invoke runs
                    # without -r, so nothing of the compacted history carries over.
                    session_id = None
            continue

        return render(
            "verify_failed",
            session_id,
            trail,
            result_text,
            denials,
            max_iter,
            ctx,
            last_verify=v_out,
        )

    return render("verify_failed", session_id, trail, result_text, denials, max_iter, ctx)


def retry_prompt(session_id, task, verify, v_out, on_compaction="reinject",
                 repeated=False):
    """
    The prompt for attempt N+1. Returns (prompt, action) where action is one of
    "none" | "reinject" | "discard".

    Normally the session is intact and already holds the task, the handoff instructions
    and (via QWEN.md) the rules -- so re-sending them makes Qwen hold two copies of
    everything and grows the context that caused the compaction. Send only the delta.

    If it was compacted since we last handled it, the caller's policy decides:

      reinject  keep the warm session and restore the task into it. Cheap, keeps the
                files it has already read -- but the compaction SUMMARY STAYS IN THE
                HISTORY. That summary is exactly what has been observed to fabricate
                ("claimed to have read files it never opened"), so this places correct
                context alongside possibly-false context and hopes the former wins.

      discard   abandon the session and start cold with the full task. Costs a fresh
                ~21.6k preamble and loses everything it learned -- but it is the only
                option that removes the corrupted summary, and it re-reads QWEN.md,
                which is what makes the rules bind.

    Either way `ack_compaction` fires once per compaction, so compacted-once-then-
    resumed-twice acts on the first resume only.
    """
    # Reflexion (#24): don't just re-send the error -- make Qwen diagnose before it edits.
    # Free tokens, zero Claude cost; the gate still decides, so a wrong self-diagnosis just
    # fails again and we move on. `repeated` (the same failure recurred) means plain
    # error-feedback is looping, so force a change of approach rather than another variation.
    reflect = (
        "Before editing, state in one or two sentences: (1) the ROOT CAUSE of this "
        "specific failure and (2) the fix you will make. Then apply it so the command "
        "passes."
    )
    if repeated:
        reflect = (
            "You have failed the SAME check again: your previous edit did not change this "
            "result, so that approach is wrong. Do not retry a variation of it. State in "
            "one or two sentences (1) why the previous approach cannot work and (2) a "
            "DIFFERENT approach to try, then apply it so the command passes."
        )
    failure = (
        f"The verification command failed. This is the real output:\n\n"
        f"```\n{truncate(v_out, VERIFY_CAP)}\n```\n\n"
        f"{reflect}"
    )
    if not was_compacted_since_ack(session_id):
        return failure, "none"

    ack_compaction(session_id)

    if on_compaction == "discard":
        log(f"session {session_id} was compacted -- discarding it, restarting cold")
        return (
            f"A previous attempt at this task was made in a session that has been "
            f"discarded, so you are starting fresh. Work already on disk may be partial "
            f"or wrong -- read the current state rather than assuming.\n\n"
            f"{failure}\n\nVerify command: {verify}\n\nTask:\n{task}"
        ), "discard"

    log(f"session {session_id} was compacted -- re-injecting task context")
    return (
        f"{failure}\n\n"
        f"Your conversation history was summarised (compacted), so you may have lost the "
        f"original instructions, and any summary of your earlier work may be inaccurate. "
        f"Do not reconstruct what you think you did -- re-read the files and work from "
        f"what follows.\n\n"
        f"Verify command: {verify}\n\nOriginal task:\n{task}"
    ), "reinject"


def render(status, session_id, trail, result_text, denials, max_iter, ctx, last_verify=None):
    cwd = ctx["cwd"]
    guard_on = ctx["guard_on"]

    # "verify passed" is only evidence if it was failing beforehand.
    if ctx["preflight"] and status == "success":
        status = "success_but_preflight_passed"

    lines = [f"STATUS: {status}", f"SESSION: {session_id or 'unknown'}"]
    lines.append(f"ATTEMPTS: {len(trail)}/{max_iter}")
    for t in trail:
        lines.append(f"  - {t}")

    # Prominent: the project was just self-configured. Relay it and act on the two open
    # questions (test command if undetected, CLAUDE.md policy block).
    if ctx.get("bootstrap_note"):
        lines.append(ctx["bootstrap_note"])

    if guard_on:
        lines.append(blast_radius(cwd, ctx["pre_status"]))
        # Deterministic (no model tokens): new public symbols Qwen introduced -- the
        # design choices that become contracts. The manager reviews this one line
        # instead of reading the whole diff. A passing gate does NOT catch an EXTRA
        # public symbol; this does.
        pubs = new_public_symbols(cwd)
        if pubs:
            flat = ", ".join(
                f"{n} ({f.split('/')[-1]})" for f, ns in pubs.items() for n in ns
            )
            lines.append(
                "NEW PUBLIC SURFACE (deterministic scan -- review the list, not the "
                f"diff): {flat}\n"
                "These are new names others can depend on. Keep any you intended; if "
                "one is unrequested scope, re-delegate a spec that forbids it. (Internal "
                "names and test symbols are not listed.)"
            )

    # Context used, so Claude can size the next delegation.
    peak = ctx.get("peak", 0)
    win = context_window()
    if peak and win:
        warn_at, auto_at = compaction_thresholds(win)
        pct = 100.0 * peak / win
        line = f"CONTEXT: peak {peak:,}/{win:,} ({pct:.0f}%)"
        if peak >= warn_at:
            line += (
                f" -- APPROACHING COMPACTION at {auto_at:,.0f}. Compaction is lossy and "
                f"can summarize QWEN.md's rules away mid-task. Split the work or start a "
                f"fresh session."
            )
        else:
            line += f", compaction at {auto_at:,.0f} ({100.0*auto_at/win:.0f}%) -- ample headroom"
        lines.append(line)
    elif peak:
        lines.append(f"CONTEXT: peak {peak:,} tokens (window unknown)")

    # Scoped-shell elicitation: commands the hook blocked. Surfacing them is the
    # "ask the manager" half -- Qwen wanted to run these; you decide if they're legit.
    blocked = ctx.get("meta", {}).get("blocked") or []
    if blocked:
        lines.append(
            "SHELL APPROVAL NEEDED: Qwen requested these; the scoped guard auto-blocked "
            "them (reason in parens). YOU are the judge -- decide each on the command "
            "alone (is it safe in this repo?), not on the task:\n"
            + "\n".join(f"  - {b}" for b in blocked[:12])
            + ("\n  ..." if len(blocked) > 12 else "")
            + "\nAPPROVE a command: add its pattern to `shell_allow` and re-delegate with "
            "the same session_id.\nDENY a command: put the reason in `shell_feedback` so "
            "Qwen learns the constraint instead of retrying.\n"
            "(If Qwen still reached success without them, no action needed -- it found "
            "another way.)"
        )

    st = ctx.get("meta", {}).get("stats") or {}
    if st.get("ms") and ctx.get("peak"):
        secs = st["ms"] / 1000.0
        budget = ctx.get("timeout", 0)
        if budget:
            used = 100.0 * secs / budget
            note = f"TIME: {secs:.0f}s of {budget}s budget ({used:.0f}%)"
            if used > 70:
                note += " -- close to timeout; raise timeout_sec for tasks like this"
            lines.append(note)
    if st.get("tools"):
        tl = f"TOOLS: {st['tools']} call(s)"
        if st.get("tool_names"):
            tl += f" ({', '.join(st['tool_names'][:6])})"
        if st.get("ms"):
            tl += f", {st['ms']/1000:.0f}s"
        lines.append(tl)
    if st.get("tool_fail"):
        # In restricted modes, denied shell/edit calls are the design, not a defect --
        # measured: auto-edit runs show 3/9 "failures" that are just blocked shell
        # attempts, while the gate passes. Only flag as suspect where tools were free.
        if ctx.get("approval_mode") in ("plan", "auto-edit", "default", "auto"):
            lines.append(
                f"TOOL FAILURES: {st['tool_fail']} of {st['tools']} tool call(s) were "
                f"blocked -- expected under approval_mode="
                f"'{ctx.get('approval_mode')}' (Qwen tried tools this mode denies). Not "
                f"a defect on its own; the gate is what decides."
            )
        else:
            lines.append(
                f"TOOL FAILURES: {st['tool_fail']} of {st['tools']} tool call(s) FAILED. "
                f"Qwen may have worked around this or reported success anyway -- treat the "
                f"result as suspect and check CHANGED."
            )
    if st.get("api_errors"):
        lines.append(f"API ERRORS: {st['api_errors']} request(s) errored during this run.")

    if ctx.get("reinjects") or ctx.get("discards"):
        if ctx.get("discards"):
            lines.append(
                f"COMPACTED: this session was compacted mid-run and DISCARDED "
                f"({ctx['discards']}x); work restarted cold against the same tree. The "
                f"corrupted summary is gone, but so is everything it had learned -- and "
                f"the files on disk are from the abandoned attempt. Check CHANGED."
            )
        else:
            lines.append(
                f"COMPACTED: this session was compacted mid-run ({ctx['reinjects']}x) "
                f"and the task was re-injected into the WARM session. The compaction "
                f"summary is still in its history and that summary is exactly what has "
                f"been observed to fabricate -- treat any claim about work done before "
                f"the compaction as unverified, and check CHANGED, not the narrative. "
                f"Re-delegate with on_compaction='discard' if you need a clean slate."
            )

    if status == "gate_suspect":
        lines.append(
            "GATE SUSPECT: the verify command produced identical output before and after "
            "Qwen ran, so nothing it did moves this gate. Almost always the gate itself is "
            "wrong -- malformed quoting, a bad path, or it tests something the task never "
            "touches. Check CHANGED above: Qwen may have done the work correctly while the "
            "gate was broken. Fix the gate before retrying; iterating cannot help."
        )

    if status == "success_but_preflight_passed":
        lines.append(
            "PREFLIGHT: the verify command ALREADY PASSED before Qwen ran, so this "
            "pass is not evidence the task was done. Either the task was a no-op, its "
            "premise was false, or the gate does not actually test the change. Check "
            "CHANGED above: if nothing changed, Qwen may have correctly declined -- read "
            "its result. Tighten the gate to test the specific new behavior."
        )

    # Qwen is told not to commit, but nothing enforces that in yolo -- and a commit hides
    # its work from `git status`, so CHANGED goes quiet while the tree really moved.
    moved, n_commits, committed_files = (
        committed_during_run(cwd, ctx["pre_sha"]) if guard_on else (False, 0, []))
    if moved:
        shown = ", ".join(committed_files[:10]) + (" ..." if len(committed_files) > 10 else "")
        lines.append(
            f"COMMITTED: Qwen moved HEAD during this run ({n_commits} commit(s), "
            f"{ctx['pre_sha']} -> {head_sha(cwd)}). It was told not to. Consequences you "
            f"must account for:\n"
            f"  - CHANGED above is INCOMPLETE: committed files no longer show in "
            f"git status. Actually changed: {shown or '(none)'}\n"
            f"  - the spec guard was still enforced (it diffs against the pre-run sha, "
            f"not HEAD), but review those files yourself\n"
            f"  - rollback needs a reset, not a checkout -- see ROLLBACK below"
        )

    if guard_on and ctx["pre_sha"]:
        if moved:
            # `git checkout .` cannot undo a commit; advising it here would leave the
            # commits in place and read as a successful rollback.
            safety = ("safe -- tree was clean" if ctx["pre_clean"]
                      else "CAUTION: tree was ALREADY dirty")
            lines.append(
                f"ROLLBACK: git reset --hard {ctx['pre_sha']} && git clean -fd   "
                f"({safety} at {ctx['pre_sha']} before this run. A plain "
                f"`git checkout .` will NOT undo the commit(s) above.)"
            )
        elif ctx["pre_clean"]:
            lines.append(
                f"ROLLBACK: git checkout . && git clean -fd   "
                f"(safe -- tree was clean at {ctx['pre_sha']} before this run)"
            )
        else:
            lines.append(
                f"ROLLBACK: unsafe to blanket-revert -- the tree was ALREADY dirty at "
                f"{ctx['pre_sha']} before this run, so Qwen's changes are mixed with "
                f"pre-existing ones. Review the diff and revert selectively."
            )

    # Structured handoff, extracted BEFORE truncation so a long reply can't bury it.
    handoff = parse_handoff(result_text)
    if handoff:
        if handoff.get("HANDOFF"):
            lines.append(f"HANDOFF: {handoff['HANDOFF']}")
        if handoff.get("NEXT"):
            lines.append(f"NEXT: {handoff['NEXT']}")

        # Qwen's own account of what it touched vs what the filesystem says. This is the
        # fib-fabrication failure mode in miniature -- trust the filesystem.
        claimed = handoff.get("FILES", "")
        if guard_on and claimed:
            post_snap = snapshot(cwd)
            actual = {p for p in post_snap if post_snap.get(p) != ctx["pre_status"].get(p)}
            said_none = claimed.strip().lower() in ("none", "no files", "-")
            if said_none and actual:
                lines.append(
                    f"MISREPORT: Qwen claims FILES: none but {len(actual)} file(s) "
                    f"changed on disk. Its account is unreliable -- trust CHANGED above."
                )
            elif not said_none and not actual:
                lines.append(
                    f"MISREPORT: Qwen claims it changed '{claimed}' but nothing changed "
                    f"on disk. It may have described intended work it never did."
                )

    if guard_on and status not in ("error",):
        lines.append(
            f"CONTINUE: to follow up on THIS task with warm context, pass "
            f"session_id=\"{session_id}\" and the same cwd (sessions are cwd-scoped). "
            f"It keeps what Qwen already read, so it need not re-derive it -- but it does "
            f"NOT reduce prompt tokens: resuming replays the history on top of the same "
            f"preamble, so context grows. Do NOT reuse it for an unrelated task -- a "
            f"fresh session re-reads QWEN.md, which is what makes the rules bind, and "
            f"keeps one task's reasoning from contaminating the next."
        )

    if denials:
        names = ", ".join(sorted({d.get("tool_name", "?") for d in denials}))
        lines.append(
            f"DENIALS: {len(denials)} blocked ({names}) -- Qwen may have worked around "
            f"this; treat the result as suspect."
        )

    if last_verify:
        lines.append(f"--- final verify output ---\n{truncate(last_verify, VERIFY_CAP)}")

    if status == "unverified":
        lines.append("NOTE: no verify command -- this is Qwen's unverified claim.")

    lines.append(f"--- qwen result ---\n{truncate(strip_handoff(result_text), RESULT_CAP)}")
    verdict = "\n".join(lines)

    # Logged LAST: every diff above is taken against the pre-run snapshot, so the log
    # file must not exist in the tree until they are done. (It is gitignored regardless
    # -- belt and braces, because getting this wrong would corrupt the CHANGED report.)
    cum = ctx.get("cum") or cum_zero()
    changed = 0
    if guard_on:
        post = snapshot(cwd)
        changed = len([p for p in post if post.get(p) != ctx["pre_status"].get(p)])
    write_runlog(cwd, leverage_record(
        "qwen_delegate", cwd, status, verdict, cum, ctx.get("peak", 0),
        extra={
            "session": session_id,
            "approval_mode": ctx.get("approval_mode"),
            "attempts": len(trail),
            "max_iterations": max_iter,
            "task": digest(ctx.get("task")),
            "gate": {
                "cmd": digest(ctx.get("verify")),
                "preflight_passed": ctx.get("preflight"),
            },
            "changed_files": changed,
            "head_moved": moved,
            "commits_by_worker": n_commits,
            "resumed": bool(ctx.get("session_hint")),
            "compactions": sum(compaction_state(s)[0] for s in ctx.get("sessions", [])),
            "sessions": len(ctx.get("sessions", [])),
            "reinjections": ctx.get("reinjects", 0),
            "discards": ctx.get("discards", 0),
            "on_compaction": ctx.get("on_compaction"),
            "blocked_shell": len(ctx.get("meta", {}).get("blocked") or []),
            "denials": len(denials or []),
        },
    ))
    return verdict


def context_window():
    """Configured context window for the active model, or None."""
    try:
        s = json.load(open(os.path.expanduser("~/.qwen/settings.json")))
        model = (s.get("model") or {}).get("name")
        for provs in (s.get("modelProviders") or {}).values():
            for p in provs if isinstance(provs, list) else []:
                if p.get("id") == model or p.get("name") == model:
                    cw = (p.get("generationConfig") or {}).get("contextWindowSize")
                    if cw:
                        return int(cw)
    except Exception:
        pass
    return None


def compaction_thresholds(window):
    """
    Mirrors qwen's computeThresholds() (chunks/chunk-NJOFRXTM.js):
      DEFAULT_PCT=0.85, SUMMARY_RESERVE=20000, AUTOCOMPACT_BUFFER=13000, WARN_BUFFER=20000
    Compaction is LOSSY -- it summarizes history away, which can drop QWEN.md rules
    mid-task. Statelessness normally keeps us near 11%, far below these.
    """
    effective = max(0, window - 20000)
    ceiling = effective - 13000
    auto = min(0.85 * window, ceiling) if ceiling > 0 else 0.85 * window
    return max(0, auto - 20000), auto


def peak_context(stdout):
    """
    Peak prompt tokens across assistant turns == context actually used.

    NOT result.usage.input_tokens: that SUMS every API call in the run, including
    Qwen's internal auto-memory-extractor sub-agent. Measured: result reported 31,317
    while true peak context was 20,285 -- a 50% overstatement.
    """
    best = 0
    try:
        parsed = json.loads((stdout or "").strip())
        msgs = parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        return 0
    for m in msgs:
        if not isinstance(m, dict) or m.get("type") != "assistant":
            continue
        u = (m.get("message") or {}).get("usage") or {}
        best = max(best, int(u.get("input_tokens") or 0))
    return best


def tok_zero():
    return {"prompt": 0, "completion": 0, "total": 0, "cached": 0, "thoughts": 0}


def tok_add(dst, src):
    for k in dst:
        dst[k] += int(src.get(k) or 0)
    return dst


def accum_stats(cum, st):
    """
    Sum one attempt's telemetry into the run total.

    ctx["meta"] holds only the LAST attempt, so a 3-attempt run costs roughly 3x what a
    last-attempt reading reports. Cost accounting has to see the whole run: the iterate
    loop is precisely where free tokens get spent.
    """
    st = st or {}
    for k in ("tokens", "tokens_main", "tokens_overhead"):
        tok_add(cum[k], st.get(k) or {})
    for k in ("ms", "turns", "tools", "tool_fail", "api_errors",
              "lines_added", "lines_removed"):
        cum[k] = (cum.get(k) or 0) + (st.get(k) or 0)
    for k in ("tool_names", "models"):
        cum[k] = sorted(set(cum.get(k) or []) | set(st.get(k) or []))
    # Worst case wins: one blended attempt makes the whole run's main/overhead split
    # unreliable, so the run must not claim a clean bySource provenance.
    seen = {cum.get("token_source", "none"), st.get("token_source", "none")}
    cum["token_source"] = ("blended" if "blended" in seen
                           else "bySource" if "bySource" in seen else "none")
    cum["attempts"] = (cum.get("attempts") or 0) + 1
    return cum


def cum_zero():
    return {"tokens": tok_zero(), "tokens_main": tok_zero(),
            "tokens_overhead": tok_zero(), "ms": 0, "turns": 0, "tools": 0,
            "tool_fail": 0, "api_errors": 0, "lines_added": 0, "lines_removed": 0,
            "tool_names": [], "models": [], "attempts": 0, "token_source": "none"}


def norm_tokens(t):
    """
    Normalise one `tokens` object to {prompt, completion, total, cached, thoughts}.

    `-o json` emits the INTERNAL camelCase shape, where the output count is named
    `candidates` (verified against a live run, not the bundled schema -- the snake_case
    `completion` spelling in the CLI source belongs to the statusLine hook, a different
    serializer). Both spellings are accepted so this survives an upstream rename.
    """
    t = t or {}
    return {
        "prompt": int(t.get("prompt") or 0),
        "completion": int(t.get("candidates") or t.get("completion") or 0),
        "total": int(t.get("total") or 0),
        "cached": int(t.get("cached") or 0),
        "thoughts": int(t.get("thoughts") or 0),
    }


def parse_stats(stdout):
    """
    Pull the run telemetry out of result.stats.

    tools.totalFail is the valuable one: a run where Qwen's tool calls failed currently
    reports identically to one where they all succeeded. Same class as permission_denials
    -- silent failure dressed as success.

    Tokens are split main vs overhead via stats.models[*].bySource. Qwen runs an internal
    `managed-auto-memory-extractor` sub-agent whose spend is real but is not task work --
    measured at 10,428 of 29,421 prompt tokens (35%) on a one-word prompt. Reporting a
    single blended total would overstate what the task itself cost.
    """
    out = {"tools": 0, "tool_fail": 0, "tool_names": [], "ms": 0, "turns": 0,
           "api_errors": 0, "lines_added": 0, "lines_removed": 0,
           "tokens": tok_zero(), "tokens_main": tok_zero(),
           "tokens_overhead": tok_zero(), "models": [], "token_source": "none"}
    try:
        parsed = json.loads((stdout or "").strip())
        msgs = parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        return out
    for m in reversed(msgs):
        if not isinstance(m, dict) or m.get("type") != "result":
            continue
        out["ms"] = m.get("duration_ms") or 0
        out["turns"] = m.get("num_turns") or 0
        st = m.get("stats") or {}
        t = st.get("tools") or {}
        out["tools"] = t.get("totalCalls") or 0
        out["tool_fail"] = t.get("totalFail") or 0
        out["tool_names"] = sorted((t.get("byName") or {}).keys())
        f = st.get("files") or {}
        out["lines_added"] = f.get("totalLinesAdded") or 0
        out["lines_removed"] = f.get("totalLinesRemoved") or 0
        for mid, mv in (st.get("models") or {}).items():
            out["api_errors"] += ((mv.get("api") or {}).get("totalErrors") or 0)
            out["models"].append(mid)
            tok_add(out["tokens"], norm_tokens(mv.get("tokens")))
            by = mv.get("bySource") or {}
            if by:
                out["token_source"] = "bySource"
                for src, sv in by.items():
                    bucket = "tokens_main" if src == "main" else "tokens_overhead"
                    tok_add(out[bucket], norm_tokens(sv.get("tokens")))
            else:
                # No per-source breakdown: attribute everything to the task rather than
                # silently dropping it. Overstates main, never invents tokens.
                #
                # Recorded, because otherwise this is indistinguishable from a real
                # zero-overhead run -- a metric reading 0 when it actually measured
                # nothing is the silent-failure class this system exists to catch.
                out["token_source"] = "blended"
                tok_add(out["tokens_main"], norm_tokens(mv.get("tokens")))
        break
    return out


# ---------- run log ----------
#
# Every delegation and query appends one JSON object to <cwd>/.qwen-delegate/runs.jsonl.
# The point is the leverage ratio: free tokens burned by Qwen vs tokens returned into
# Claude's context. That ratio was the product's headline claim on the strength of one
# hand-measured session; logging turns it into something continuously measured.
#
# Two rules this code must never break:
#   1. A logging failure must never fail a delegation. Everything here is best-effort.
#   2. Nothing is written into the working tree before the blast-radius diff is taken,
#      or the log would be counted as Qwen's own work.


def digest(text):
    """Truncated head + full-text hash. Enough to identify and group runs without
    parking whole prompts (which can embed real source) on disk."""
    text = text or ""
    return {
        "head": text[:TASK_HEAD_CHARS],
        "sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16],
        "chars": len(text),
    }


def runlog_dir(cwd):
    """
    Create <cwd>/.qwen-delegate/ holding a self-ignoring .gitignore.

    The `*` pattern makes git ignore every file in the directory INCLUDING the .gitignore
    itself, so `git status --porcelain` never reports it. That is load-bearing: snapshot()
    and blast_radius() diff the working tree to attribute changes to Qwen, and an
    un-ignored log file would show up as Qwen's work -- and would also trip the
    "refuses to run if the tree is dirty" precondition. Self-ignoring leaves the
    project's own .gitignore untouched.
    """
    d = os.path.join(cwd, RUNLOG_DIR)
    os.makedirs(d, exist_ok=True)
    gi = os.path.join(d, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w") as f:
            f.write("*\n")
    return d


def register_project(cwd):
    """Add cwd to the global pointer index if absent. Paths only -- an aggregator reads
    this to find the per-project logs. Deliberately not a metrics store: the numbers
    stay with the project that produced them."""
    try:
        os.makedirs(os.path.dirname(PROJECT_REGISTRY), exist_ok=True)
        known = set()
        if os.path.isfile(PROJECT_REGISTRY):
            with open(PROJECT_REGISTRY) as f:
                for line in f:
                    try:
                        known.add((json.loads(line) or {}).get("path"))
                    except Exception:
                        continue  # a corrupt line must not hide the rest
        if cwd in known:
            return
        with open(PROJECT_REGISTRY, "a") as f:
            f.write(json.dumps({"path": cwd, "first_seen": now_iso()}) + "\n")
    except Exception as e:
        log(f"warning: project registry update failed: {e!r}")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_runlog(cwd, record):
    """Append one run record. Best-effort by contract -- never raises."""
    try:
        path = os.path.join(runlog_dir(cwd), RUNLOG_FILE)
        with open(path, "a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
        register_project(cwd)
    except Exception as e:
        log(f"warning: run log write failed: {e!r}")


def leverage_record(tool, cwd, status, verdict, stats, peak, extra=None):
    """
    Assemble the common half of a run record.

    `verdict` is the exact string handed back to Claude, so verdict_chars measures the
    real context cost -- the denominator of the whole thesis.
    """
    tokens = stats.get("tokens") or tok_zero()
    v_chars = len(verdict or "")
    v_tokens = round(v_chars / VERDICT_CHARS_PER_TOKEN)
    rec = {
        "ts": now_iso(),
        "tool": tool,
        "status": status,
        "cwd": cwd,
        "tokens": tokens,
        "tokens_main": stats.get("tokens_main") or tok_zero(),
        "tokens_overhead": stats.get("tokens_overhead") or tok_zero(),
        # "bySource" = split is real; "blended" = everything attributed to main because
        # Qwen reported no per-source breakdown, so overhead=0 means "unmeasured".
        "token_source": stats.get("token_source") or "none",
        "peak_context": peak,
        "verdict_chars": v_chars,
        "verdict_tokens_est": v_tokens,
        # The number this whole system exists to make large.
        "leverage": round(tokens["total"] / v_tokens, 1) if v_tokens else None,
        "duration_ms": stats.get("ms") or 0,
        "turns": stats.get("turns") or 0,
        "tools": {
            "calls": stats.get("tools") or 0,
            "fail": stats.get("tool_fail") or 0,
            "names": stats.get("tool_names") or [],
        },
        "api_errors": stats.get("api_errors") or 0,
        "lines_added": stats.get("lines_added") or 0,
        "lines_removed": stats.get("lines_removed") or 0,
        "models": stats.get("models") or [],
    }
    rec.update(extra or {})
    return rec


# ---------- compaction state ----------
#
# The question at resume time is not "was this session ever compacted" but "was it
# compacted since I last dealt with it". A boolean would re-inject on every subsequent
# resume forever. So the hook appends events and the server keeps a watermark:
#
#   events > acked  ->  history was summarised since last time; re-inject, then ack
#   events == acked ->  intact as far as we are concerned; send only the delta
#
# Compacted once then resumed twice therefore re-injects exactly once. A later compaction
# pushes events ahead of acked and re-arms it. Events are kept rather than deleted so the
# history stays inspectable.


def compaction_state(session_id):
    """(events_seen, acked). (0, 0) when there is no marker -- never compacted."""
    if not session_id:
        return 0, 0
    try:
        with open(os.path.join(COMPACT_DIR, f"{session_id}.json")) as f:
            state = json.load(f)
        return len(state.get("events") or []), int(state.get("acked") or 0)
    except Exception:
        return 0, 0


def was_compacted_since_ack(session_id):
    seen, acked = compaction_state(session_id)
    return seen > acked


def ack_compaction(session_id):
    """Mark every compaction seen so far as handled. Idempotent."""
    if not session_id:
        return
    path = os.path.join(COMPACT_DIR, f"{session_id}.json")
    try:
        with open(path) as f:
            state = json.load(f)
        state["acked"] = len(state.get("events") or [])
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except Exception as e:
        log(f"warning: could not ack compaction for {session_id}: {e!r}")


def parse_qwen_json(stdout):
    """Return (result_text, denials, session_id) from qwen's -o json output."""
    stdout = (stdout or "").strip()
    if not stdout:
        return None, [], None

    try:
        parsed = json.loads(stdout)
        msgs = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        msgs = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("type") == "result":
            return (
                m.get("result") or "",
                m.get("permission_denials") or [],
                m.get("session_id"),
            )

    sid = next(
        (m.get("session_id") for m in msgs if isinstance(m, dict) and m.get("session_id")),
        None,
    )
    return None, [], sid


def truncate(s, cap):
    s = s or ""
    if len(s) <= cap:
        return s
    return s[:cap] + f"\n... [truncated {len(s) - cap} chars]"


def respond(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    log(f"starting (qwen={QWEN_BIN})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method")
        rid = req.get("id")

        if method == "initialize":
            respond(
                rid,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "qwen-delegate", "version": "0.2.0"},
                },
            )
        elif method == "notifications/initialized":
            continue
        elif method == "ping":
            respond(rid, {})
        elif method == "tools/list":
            respond(rid, {"tools": [TOOL, QUERY_TOOL]})
        elif method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            handler = {"qwen_delegate": run_qwen, "qwen_query": run_query,
                       "qwen_investigate": run_investigate}.get(name)
            if handler is None:
                respond(rid, error={"code": -32602, "message": f"unknown tool: {name}"})
                continue
            try:
                text = handler(args)
                respond(rid, {"content": [{"type": "text", "text": text}]})
            except Exception as e:
                log(f"error: {e!r}")
                respond(
                    rid,
                    {
                        "content": [{"type": "text", "text": f"STATUS: error\n{e!r}"}],
                        "isError": True,
                    },
                )
        elif rid is not None:
            respond(rid, error={"code": -32601, "message": f"unknown method: {method}"})


if __name__ == "__main__":
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
