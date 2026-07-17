#!/usr/bin/env bash
# Bootstrap a project for qwen-delegate: drop in QWEN.md with the right test command.
#
#   ./init-project.sh /path/to/project
#
# Idempotent-ish: refuses to clobber an existing QWEN.md.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-}"

[ -n "$TARGET" ] || { echo "usage: $0 /path/to/project" >&2; exit 1; }
[ -d "$TARGET" ] || { echo "ERROR: not a directory: $TARGET" >&2; exit 1; }
TARGET="$(cd "$TARGET" && pwd)"

say() { printf '  %s\n' "$*"; }

echo "qwen-delegate: init $TARGET"
echo

# ---------- git is mandatory: it is the only rollback ----------
if ! git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    cat >&2 <<EOF
  ERROR: $TARGET is not a git repository.

  Git history is the rollback mechanism — there is no sandbox, and Qwen runs at
  your full user privilege. The spec guard also needs git to detect and revert
  edits to protected files. Run 'git init' first.
EOF
    exit 1
fi
say "git         ok"

# ---------- detect the test command ----------
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

TEST_CMD="$(detect_test_cmd)"
if [ -n "$TEST_CMD" ]; then
    say "test cmd    $TEST_CMD  (detected)"
else
    say "test cmd    NOT DETECTED — you must edit it in QWEN.md"
    TEST_CMD="<EDIT ME: your project's test command>"
fi

# ---------- write QWEN.md ----------
DEST="$TARGET/QWEN.md"
if [ -e "$DEST" ]; then
    say "QWEN.md     already exists — not overwriting"
else
    python3 - "$REPO/templates/QWEN.md" "$DEST" "$TEST_CMD" <<'PY'
import sys
src, dest, cmd = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(src).read()
s = s.replace(
    "- Run tests with: `venv/bin/pytest`      <-- EDIT: your project's real test command.\n"
    "  Never a bare `pytest` unless it is genuinely on PATH.",
    f"- Run tests with: `{cmd}`",
)
# strip the template banner
s = s.replace(
    "#\n# TEMPLATE — copy to <your-project>/QWEN.md and edit the two marked spots below.\n"
    "# Qwen auto-loads this file every session. That reload is what makes these rules bind,\n"
    "# which is why delegations are stateless by default.\n",
    "",
)
open(dest, "w").write(s)
PY
    say "QWEN.md     written"
fi

echo
echo "  next:"
echo "    1. read $DEST — check the test command and add any project-specific rules"
echo "    2. commit it:  git -C $TARGET add QWEN.md && git -C $TARGET commit -m 'Add QWEN.md'"
echo "    3. delegate:   ask Claude to hand a task to the qwen-manager subagent"
echo
echo "  spec files: name gate tests <name>_spec.<ext> — the guard protects *_spec.* and"
echo "  *.spec.* automatically. Override in $TARGET/.qwen-delegate.json if your project"
echo '  uses a different convention: {"spec_globs": ["tests/contract/*.ts"]}'
