#!/usr/bin/env bash
# Install the qwen-delegate framework into Claude Code on this machine.
# Idempotent: safe to re-run after a git pull.
#
# This does NOT configure Qwen itself (model provider, API keys, Firecrawl).
# Those live in ~/.qwen/settings.json and are machine-specific secrets — see README.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
AGENTS_DIR="$CLAUDE_DIR/agents"
SETTINGS="$CLAUDE_DIR/settings.json"
CLAUDE_JSON="$HOME/.claude.json"

# Wall-clock cap per MCP tool call (2h). Long delegations are legitimate.
TIMEOUT_MS=7200000
# stdio servers idle out at 30 min by default; the server blocks silently in
# subprocess.run for the whole delegation, so it looks idle. Raise to 90 min.
IDLE_MS=5400000

say()  { printf '  %s\n' "$*"; }
fail() { printf '  ERROR: %s\n' "$*" >&2; exit 1; }

echo "qwen-delegate installer"
echo "  repo: $REPO"
echo

# ---------- prerequisites ----------
echo "checking prerequisites"
command -v python3 >/dev/null || fail "python3 not found"
command -v claude  >/dev/null || fail "claude CLI not found"
if command -v qwen >/dev/null; then
    say "qwen        $(qwen --version 2>/dev/null || echo '?')"
else
    say "WARNING: qwen not on PATH. Install Qwen Code, or set QWEN_BIN for the server."
fi
say "python3     $(python3 --version 2>&1 | cut -d' ' -f2)"
say "server.py   syntax $(python3 -c "import ast;ast.parse(open('$REPO/server.py').read());print('ok')")"
echo

# ---------- 1. register the MCP server ----------
echo "registering MCP server 'qwen-delegate' (user scope)"
claude mcp remove qwen-delegate -s user >/dev/null 2>&1 || true
claude mcp add qwen-delegate -s user -- python3 "$REPO/server.py" >/dev/null
say "-> python3 $REPO/server.py"

# ---------- 2. timeouts ----------
# claude mcp add has no --timeout flag, so patch the entry directly.
echo "setting timeouts"
python3 - "$CLAUDE_JSON" "$TIMEOUT_MS" <<'PY'
import json, sys
path, ms = sys.argv[1], int(sys.argv[2])
with open(path) as f:
    d = json.load(f)
srv = (d.get("mcpServers") or {}).get("qwen-delegate")
if srv is None:
    sys.exit("qwen-delegate not found in mcpServers - did `claude mcp add` succeed?")
srv["timeout"] = ms
with open(path, "w") as f:
    json.dump(d, f, indent=2)
print(f"  -> .claude.json  timeout={ms} ({ms//60000} min wall clock per call)")
PY

python3 - "$SETTINGS" "$IDLE_MS" <<'PY'
import json, os, sys
path, ms = sys.argv[1], sys.argv[2]
d = {}
if os.path.exists(path):
    with open(path) as f:
        d = json.load(f)
d.setdefault("env", {})["CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT"] = ms
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump(d, f, indent=2)
print(f"  -> settings.json CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT={ms} ({int(ms)//60000} min idle)")
PY

# ---------- 3. the subagent ----------
echo "installing subagent"
mkdir -p "$AGENTS_DIR"
ln -sfn "$REPO/agents/qwen-manager.md" "$AGENTS_DIR/qwen-manager.md"
say "-> $AGENTS_DIR/qwen-manager.md (symlink; git pull updates it in place)"

# ---------- 4. shared skills (preloaded into the manager) ----------
echo "installing skills"
SKILLS_DIR="$CLAUDE_DIR/skills"
mkdir -p "$SKILLS_DIR"
for skill in "$REPO"/skills/*/; do
    [ -d "$skill" ] || continue
    name="$(basename "$skill")"
    ln -sfn "${skill%/}" "$SKILLS_DIR/$name"
    say "-> $SKILLS_DIR/$name (symlink; preloaded via the manager's skills: frontmatter)"
done

# ---------- 5. commands (the front door) ----------
echo "installing commands"
COMMANDS_DIR="$CLAUDE_DIR/commands"
mkdir -p "$COMMANDS_DIR"
for cmd in "$REPO"/commands/*.md; do
    [ -f "$cmd" ] || continue
    ln -sfn "$cmd" "$COMMANDS_DIR/$(basename "$cmd")"
    say "-> $COMMANDS_DIR/$(basename "$cmd")  (invoke as /$(basename "$cmd" .md))"
done

echo
echo "installed. RESTART Claude Code to load the server, agent, skills, and commands."
echo
echo "Not done for you (machine-specific, and one of them is a secret):"
echo "  - Qwen's model provider + API key -> ~/.qwen/settings.json"
echo "  - Firecrawl, if you want web access -> see README"
echo
echo "NEXT: nothing required per project. The first delegation into a git repo"
echo "  self-configures -- it writes QWEN.md (the worker's standing rules) automatically."
echo "  A non-git repo is refused (git init first): there is no rollback without git."
echo
echo "  OPTIONAL, to set things up front instead of on first use:"
echo "    $REPO/init-project.sh /path/to/your/project [--test-cmd 'CMD'] [--claude-md]"
echo "  Use it to pin the test command, or to add the delegation policy block to the"
echo "  project's CLAUDE.md so mechanical work routes to the worker automatically."
