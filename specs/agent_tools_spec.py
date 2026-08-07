#!/usr/bin/env python3
"""
Spec for agents/*.md tool grants -- the names that decide whether a delegation
container is born holding its delegation tools.

Claude-authored gate (never delegate this file -- it defines what correct means).

Field evidence, 2026-08-07: both game-eval ARM B runs dispatched
token-saver:qwen-manager and the container came up with built-ins only. The
frontmatter granted `mcp__qwen-delegate__qwen_delegate`, but Claude Code exposes
a plugin's MCP tools as `mcp__plugin_<plugin>_<server>__<tool>` -- verified live
in the seeded repo: `mcp__plugin_token-saver_qwen-delegate__qwen_delegate`. A
granted name that matches no tool is DROPPED SILENTLY, so every subagent ran
toolless and nothing said so. One run still "delegated" -- the Opus manager
hand-rolled a JSON-RPC stdio client from Bash -- which is exactly why this class
of defect survives: the output looks like delegation and is not.

Pins two things: every agent grants both prefixed tool names, and the prefix is
DERIVED from the two manifests that define it (`.claude-plugin/plugin.json`
name + `.mcp.json` server key), so renaming either goes red here instead of
silently orphaning every grant again.

Run:  python3 specs/agent_tools_spec.py
"""

import glob
import json
import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

TOOL_BASENAMES = ("qwen_delegate", "qwen_query")
LEGACY_PREFIX = "mcp__qwen-delegate__"


def prefixed_names():
    """The tool names Claude Code actually exposes, derived from the manifests.

    mcp__plugin_<plugin.json name>_<.mcp.json server key>__<tool>. Derivation is
    the point: a hardcoded string here would go stale the same way the agent
    files did.
    """
    with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as f:
        plugin = json.load(f)["name"]
    with open(os.path.join(ROOT, ".mcp.json")) as f:
        servers = json.load(f)["mcpServers"]
    (server,) = servers.keys()
    base = "mcp__plugin_%s_%s__" % (plugin, server)
    return tuple(base + t for t in TOOL_BASENAMES)


def agent_grants():
    """{agent path: [granted tool names]} from each frontmatter `tools:` line."""
    grants = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "agents", "*.md"))):
        with open(path) as f:
            text = f.read()
        m = re.search(r"^tools:\s*(.+)$", text, re.MULTILINE)
        if m:
            grants[path] = [t.strip() for t in m.group(1).split(",")]
    return grants


class DelegationContainersHoldTheirTools(unittest.TestCase):

    def setUp(self):
        self.expected = prefixed_names()
        self.grants = agent_grants()

    def test_the_agents_this_spec_guards_actually_exist(self):
        # A glob matching nothing would pass everything below vacuously --
        # the same trap as a deleted spec file making the suite green.
        self.assertGreaterEqual(len(self.grants), 2,
                                "expected at least architect and qwen-manager")

    def test_every_agent_grants_both_prefixed_tool_names(self):
        for path, tools in self.grants.items():
            for name in self.expected:
                self.assertIn(name, tools,
                              "%s does not grant %s" % (path, name))

    def test_the_bare_legacy_names_never_stand_alone(self):
        # The legacy form may ride along for older CLIs that exposed plugin
        # tools unprefixed; alone, it is the defect this spec exists to stop.
        for path, tools in self.grants.items():
            if any(t.startswith(LEGACY_PREFIX) for t in tools):
                for name in self.expected:
                    self.assertIn(name, tools,
                                  "%s grants only the legacy name" % path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
