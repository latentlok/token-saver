#!/usr/bin/env bash
# Bootstrap a project for qwen-delegate: worker rules (QWEN.md), the delegation policy
# block in CLAUDE.md, and registration in the global project index.
#
#   ./init-project.sh /path/to/project [options]
#
# Idempotent: safe to re-run. Never clobbers or duplicates existing content.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET=""
TEST_CMD=""
TEST_CMD_SET=0
CLAUDE_MD="ask"          # ask | yes | no
FORCE=0

usage() {
    cat <<EOF
usage: $0 /path/to/project [options]

  --test-cmd CMD    the project's test command (skips detection and the prompt).
                    Pass '' to declare that there is no test command.
  --claude-md       append the delegation policy block to the project's CLAUDE.md
  --no-claude-md    do not touch CLAUDE.md
                    (neither flag: ask, or skip if not running on a terminal)
  --force           rewrite an existing QWEN.md, backing it up to QWEN.md.bak
  -h, --help        this text

Run once per project. The project must be a git repo -- git history is the only
rollback, and the spec guard needs it to detect and revert edits to protected files.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)      usage; exit 0 ;;
        --test-cmd)     [ $# -ge 2 ] || { echo "ERROR: --test-cmd needs a value" >&2; exit 1; }
                        TEST_CMD="$2"; TEST_CMD_SET=1; shift 2 ;;
        --claude-md)    CLAUDE_MD="yes"; shift ;;
        --no-claude-md) CLAUDE_MD="no";  shift ;;
        --force)        FORCE=1; shift ;;
        -*)             echo "ERROR: unknown option: $1" >&2; usage >&2; exit 1 ;;
        *)              [ -z "$TARGET" ] || { echo "ERROR: unexpected argument: $1" >&2; exit 1; }
                        TARGET="$1"; shift ;;
    esac
done

[ -n "$TARGET" ] || { usage >&2; exit 1; }
[ -d "$TARGET" ] || { echo "ERROR: not a directory: $TARGET" >&2; exit 1; }
TARGET="$(cd "$TARGET" && pwd)"

say() { printf '  %s\n' "$*"; }
interactive() { [ -t 0 ] && [ -t 1 ]; }

echo "qwen-delegate: init $TARGET"
echo

# ---------- git is mandatory: it is the only rollback ----------
if ! git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    cat >&2 <<EOF
  ERROR: $TARGET is not a git repository.

  Git history is the rollback mechanism -- there is no sandbox, and Qwen runs at
  your full user privilege. The spec guard also needs git to detect and revert
  edits to protected files. Run 'git init' first.
EOF
    exit 1
fi
say "git         ok"

# ---------- the test command ----------
# There is deliberately no placeholder path here. A QWEN.md carrying
# "<EDIT ME: your test command>" is worse than one with no testing section at all: the
# worker reads it as an instruction and will run it, or invent a command of its own. So
# the command is either real or explicitly declared absent -- never a blank to fill in.
detect_test_cmd() {
    if [ -f "$TARGET/package.json" ] && grep -q '"test"' "$TARGET/package.json" 2>/dev/null; then
        echo "npm test"; return
    fi
    if [ -f "$TARGET/Cargo.toml" ];  then echo "cargo test"; return; fi
    if [ -f "$TARGET/go.mod" ];      then echo "go test ./..."; return; fi
    if [ -f "$TARGET/Gemfile" ];     then echo "bundle exec rspec"; return; fi
    if [ -x "$TARGET/venv/bin/pytest" ];  then echo "venv/bin/pytest -q"; return; fi
    if [ -x "$TARGET/.venv/bin/pytest" ]; then echo ".venv/bin/pytest -q"; return; fi
    if [ -f "$TARGET/pyproject.toml" ] || [ -f "$TARGET/setup.py" ]; then
        echo "python -m pytest -q"; return
    fi
    echo ""
}

if [ "$TEST_CMD_SET" -eq 1 ]; then
    if [ -n "$TEST_CMD" ]; then say "test cmd    $TEST_CMD  (given)"
    else                        say "test cmd    none  (declared absent)"; fi
else
    TEST_CMD="$(detect_test_cmd)"
    if [ -n "$TEST_CMD" ]; then
        say "test cmd    $TEST_CMD  (detected)"
    elif interactive; then
        echo
        echo "  No test command detected. Enter the exact command that runs this"
        echo "  project's tests, or press Enter if it has none."
        printf '  test command: '
        read -r TEST_CMD || TEST_CMD=""
        if [ -n "$TEST_CMD" ]; then say "test cmd    $TEST_CMD  (entered)"
        else                        say "test cmd    none -- QWEN.md will tell the worker not to guess one"; fi
    else
        say "test cmd    NOT DETECTED -- QWEN.md will tell the worker not to guess one."
        say "            Re-run with --test-cmd 'CMD' once you know it."
    fi
fi

# ---------- write QWEN.md ----------
DEST="$TARGET/QWEN.md"
WROTE_QWEN=0
if [ -e "$DEST" ] && [ "$FORCE" -eq 0 ]; then
    say "QWEN.md     already exists -- not overwriting"
    if grep -qE '<EDIT ME|<-- EDIT' "$DEST" 2>/dev/null; then
        say "QWEN.md     WARNING: it still contains an unreplaced placeholder."
        say "            The server refuses to delegate until that is fixed. Edit it, or"
        say "            re-run with --force --test-cmd 'CMD' to regenerate it."
    fi
else
    [ -e "$DEST" ] && { cp "$DEST" "$DEST.bak"; say "QWEN.md     existing copy backed up to QWEN.md.bak"; }
    python3 - "$REPO/templates/QWEN.md" "$DEST" "$TEST_CMD" <<'PY'
import re, sys
src, dest, cmd = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(src).read()

if cmd:
    testing = (
        f"- Run tests with: `{cmd}`\n"
        "  Use exactly that command. Do not substitute a bare `pytest`/`npm test` you\n"
        "  assume is on PATH."
    )
else:
    # Not a blank to fill in -- an instruction. The worker must not invent a runner.
    testing = (
        "- No test command is configured for this project. Do NOT guess one and do NOT\n"
        "  invent a test runner. If the task requires running tests, say so plainly in\n"
        "  your report and stop."
    )

old = (
    "- Run tests with: `venv/bin/pytest`      <-- EDIT: your project's real test command.\n"
    "  Never a bare `pytest` unless it is genuinely on PATH."
)
if old not in s:
    sys.exit(f"ERROR: could not find the testing block in {src}; template drifted.")
s = s.replace(old, testing)

# strip the template banner
s = re.sub(r"^#\n# TEMPLATE .*?stateless by default\.\n", "", s, flags=re.S | re.M)
if "TEMPLATE" in s.split("\n\n")[0]:
    sys.exit(f"ERROR: could not strip the template banner from {src}; template drifted.")

open(dest, "w").write(s)
PY
    say "QWEN.md     written"
    WROTE_QWEN=1
fi

# ---------- offer the CLAUDE.md policy block ----------
# Without this, delegation depends on Claude happening to remember the plugin exists.
# Append-only and marker-guarded: an existing CLAUDE.md is never rewritten, and a second
# run is a no-op.
CLAUDE_DEST="$TARGET/CLAUDE.md"
if [ "$CLAUDE_MD" = "ask" ]; then
    if interactive; then
        echo
        if [ -e "$CLAUDE_DEST" ]; then
            echo "  Append the delegation policy block to $CLAUDE_DEST?"
            echo "  (appends only -- your existing content is untouched)"
        else
            echo "  Create $CLAUDE_DEST with the delegation policy block?"
        fi
        echo "  It tells Claude to hand mechanical work to the worker instead of doing it"
        echo "  inline. Without it, delegation only happens when Claude remembers to."
        printf '  add it? [Y/n] '
        read -r reply || reply="n"
        case "$reply" in [nN]*) CLAUDE_MD="no" ;; *) CLAUDE_MD="yes" ;; esac
    else
        CLAUDE_MD="no"
    fi
fi

if [ "$CLAUDE_MD" = "yes" ]; then
    python3 - "$REPO/templates/CLAUDE-snippet.md" "$CLAUDE_DEST" <<'PY'
import os, re, sys
src, dest = sys.argv[1], sys.argv[2]

BEGIN = "<!-- qwen-delegate:begin (managed by init-project.sh; delete this block to remove) -->"
END = "<!-- qwen-delegate:end -->"

existing = ""
if os.path.exists(dest):
    with open(dest, errors="replace") as f:
        existing = f.read()

# Two ways it can already be there. Neither is rewritten: the user may have edited the
# block, and clobbering their CLAUDE.md is the one thing this must never do.
if "qwen-delegate:begin" in existing:
    print("  CLAUDE.md   policy block already present -- unchanged")
    sys.exit(0)
if "## Delegating mechanical work" in existing:
    print("  CLAUDE.md   policy block already present (hand-pasted) -- unchanged")
    sys.exit(0)

body = open(src).read()
# Drop the leading HTML comment: it is instructions for a human pasting the snippet,
# not policy for Claude to read every session.
body = re.sub(r"\A\s*<!--.*?-->\s*", "", body, flags=re.S).strip()
if not body.startswith("## "):
    sys.exit(f"ERROR: {src} did not start with a heading after stripping its comment.")

block = f"{BEGIN}\n\n{body}\n\n{END}\n"

if existing:
    sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    with open(dest, "a") as f:
        f.write(sep + block)
    print("  CLAUDE.md   policy block appended")
else:
    with open(dest, "w") as f:
        f.write(f"# {os.path.basename(os.path.dirname(os.path.abspath(dest)))}\n\n{block}")
    print("  CLAUDE.md   created with the policy block")
PY
else
    say "CLAUDE.md   skipped -- add it later with: $0 $TARGET --claude-md"
fi

# ---------- register in the global project index ----------
# Pointer index only (paths, no metrics) so an aggregator can find each project's
# .qwen-delegate/runs.jsonl. The server registers on first write too, so projects set up
# before this existed still get picked up; this just registers them earlier.
python3 - "$TARGET" <<'PY'
import json, os, sys, time
target = sys.argv[1]
# Same override the server honours, so a test run never touches the real index.
reg = os.environ.get("QWEN_DELEGATE_REGISTRY") or os.path.expanduser(
    "~/.qwen-delegate/projects.jsonl")
try:
    os.makedirs(os.path.dirname(reg), exist_ok=True)
    known = set()
    if os.path.isfile(reg):
        with open(reg) as f:
            for line in f:
                try:
                    known.add((json.loads(line) or {}).get("path"))
                except Exception:
                    continue
    if target in known:
        print("  registry    already registered")
    else:
        with open(reg, "a") as f:
            f.write(json.dumps({
                "path": target,
                "first_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }) + "\n")
        print(f"  registry    added to {reg}")
except Exception as e:
    print(f"  registry    WARNING: not registered ({e!r})")
PY

echo
echo "  next:"
n=1
if [ "$WROTE_QWEN" -eq 1 ]; then
    echo "    $n. read $DEST -- check the test command and add any project-specific rules"
    n=$((n + 1))
fi
echo "    $n. commit it:  git -C $TARGET add QWEN.md CLAUDE.md && git -C $TARGET commit -m 'Add qwen-delegate config'"
n=$((n + 1))
echo "    $n. delegate:   /delegate <task>, or hand a task to the qwen-manager subagent"
echo
echo "  spec files: name gate tests <name>_spec.<ext> -- the guard protects *_spec.* and"
echo "  *.spec.* automatically. Override in $TARGET/.qwen-delegate.json if your project"
echo '  uses a different convention: {"spec_globs": ["tests/contract/*.ts"]}'
