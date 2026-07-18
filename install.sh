#!/usr/bin/env bash
# DEPRECATED. token-saver is now a native Claude Code plugin — this script no
# longer installs anything, and running it changes no global state.
#
# The plugin manifest (.claude-plugin/plugin.json) and the bundled MCP config
# (.mcp.json) now do everything install.sh used to do by hand: they register the
# qwen-delegate MCP server (python3 ${CLAUDE_PLUGIN_ROOT}/server.py) with a 2h
# per-call timeout, and surface the qwen-manager agent, the lld-principles skill,
# and the /delegate command via plugin auto-discovery. No `claude mcp add`, no
# symlinks into ~/.claude, no edits to ~/.claude.json or ~/.claude/settings.json.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cat <<EOF
token-saver is a native Claude Code plugin now. install.sh does nothing.

Load it one of two ways:

  Local / dev (this repo, live-editable):
      claude --plugin-dir "$REPO"
    In-session, after editing a component:  /reload-plugins

  Distribution (installed copy):
      claude plugin install token-saver@<marketplace>

Verify the components loaded (non-interactive):
      claude --plugin-dir "$REPO" plugin details token-saver

Still manual, once per machine (holds a secret, so never in this repo):
  - Qwen's model provider + API key -> ~/.qwen/settings.json
  - Firecrawl, if you want web access -> see README

Per project: nothing. The first delegation into a git repo self-configures
(writes QWEN.md). A non-git repo is refused (git init first).
EOF
