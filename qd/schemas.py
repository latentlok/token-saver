"""Tool schemas for the MCP surface -- the capability map ambient in every
session. Extracted from the v1 crane at cutover (M6); the qwen_delegate schema
gains the v2 input params (worktree, executor, trust, touch_scope, batch).

R1 (PLAN-v3-l5): descriptions are one-line descriptors -- a list of what each
tool/param does, nothing more. The measured guidance they used to carry lives in
skills/delegation/SKILL.md, loaded only when a session actually delegates. The
input SHAPES (names, params, enums, required) are frozen here; only description
text may change.
"""

import json

TOOL = json.loads(r'''
{
  "name": "qwen_delegate",
  "description": "Delegate a coding task to the local Qwen worker on free tokens. It builds in `cwd`; this server runs `verify` after each attempt, feeds failures back, and returns a short receipt. The worker's self-report is never evidence -- the gate decides. Before first use, load the `delegation` skill: it carries the discipline (gates, approval modes, timeouts, fan-out, sessions).",
  "inputSchema": {
    "type": "object",
    "properties": {
      "task": {
        "type": "string",
        "description": "Concrete task: exact paths, symbols, expected end state. Vague tasks get invented scope -- route those through approval_mode 'plan'."
      },
      "cwd": {
        "type": "string",
        "description": "Absolute workspace path. The worker runs at user privilege -- scope it."
      },
      "verify": {
        "type": "string",
        "description": "Shell command run in `cwd`; exit 0 = real success, failures are fed back for retry. Strongly recommended -- omit only for read-only work."
      },
      "max_iterations": {
        "type": "integer",
        "description": "Attempts before giving up (default: project config, else 3; max 10)."
      },
      "workers": {
        "type": "integer",
        "description": "Best-of-N independent candidates; first gate-pass wins (default 1, max 8; needs `verify` and a committed base)."
      },
      "on_compaction": {
        "type": "string",
        "enum": [
          "reinject",
          "discard"
        ],
        "description": "If the worker's session compacts mid-run: 'reinject' (default; keep the warm session) or 'discard' (cold restart; safest -- compaction is the documented fabrication trigger)."
      },
      "session_id": {
        "type": "string",
        "description": "Resume a prior session for a tight follow-up on the SAME task (same cwd required). Omit otherwise -- fresh sessions re-read the rules file."
      },
      "shell_allow": {
        "type": "array",
        "items": {
          "type": "string"
        },
        "description": "scoped mode only: extra allowed command regexes -- how you approve a command a prior run surfaced as SHELL APPROVAL NEEDED."
      },
      "shell_feedback": {
        "type": "string",
        "description": "scoped mode only: the reason for a denied command, shown to the worker up front so it stops retrying."
      },
      "approval_mode": {
        "type": "string",
        "enum": [
          "plan",
          "default",
          "auto-edit",
          "auto",
          "yolo",
          "scoped"
        ],
        "description": "'auto-edit' = write, no shell (default for code) | 'plan' = read-only (any vague task) | 'scoped' = auto-edit + allowlisted shell | 'yolo' = full shell (only when shell IS the work). 'default'/'auto' deny everything headless. Details: delegation skill."
      },
      "timeout_sec": {
        "type": "integer",
        "description": "Per-attempt kill, seconds (default 900, max 7200). Size up for large tasks -- timing model in the delegation skill."
      },
      "worktree": {
        "type": "string",
        "enum": [
          "auto",
          "off"
        ],
        "description": "'auto': isolated git worktree, work lands on a qwen/<id> branch, receipt carries the MERGE command. 'off' (default): in-tree under the repo lock."
      },
      "executor": {
        "type": "string",
        "description": "Executor profile name from ~/.qwen-delegate/executors.json. Omit for the machine default, else builtin 'qwen-local'."
      },
      "trust": {
        "type": "string",
        "description": "Trust level. Only 'verified' is accepted today (gate decides); other levels are a parked design."
      },
      "touch_scope": {
        "type": "array",
        "items": {
          "type": "string"
        },
        "description": "Pre-existing files the worker may modify; out-of-scope edits auto-revert and fail the attempt. New files are always allowed."
      },
      "batch": {
        "type": "array",
        "items": {
          "type": "object"
        },
        "description": "N independent delegations in ONE call (same fields per item), fanned across worktrees with per-item receipts -- the reliable fan-out; separate tool calls serialize."
      }
    },
    "required": [
      "task",
      "cwd"
    ]
  }
}
''')

QUERY_TOOL = json.loads(r'''
{
  "name": "qwen_query",
  "description": "Ask the local Qwen worker a READ-ONLY question about a codebase (plan mode -- it cannot write; no gate needed). Free tokens instead of your context. Answers are LEADS to verify, not truth. Keep each question bounded to a few files. Discipline: `delegation` skill.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "question": {
        "type": "string",
        "description": "Question answerable by reading code ('how does X work', 'is there already a Z'). Scope it so a few files answer it."
      },
      "cwd": {
        "type": "string",
        "description": "Absolute path to the repository."
      },
      "format": {
        "type": "string",
        "enum": [
          "answer",
          "map"
        ],
        "description": "'answer' (default): prose answer + VERIFY list. 'map': structured repo map (MAP/KEY SYMBOLS/CONNECTIONS/ANSWER/VERIFY) for orienting."
      },
      "session_id": {
        "type": "string",
        "description": "Warm follow-up to a prior query (same cwd required). Omit to start fresh."
      },
      "focus": {
        "type": "string",
        "description": "Narrow the reading to a subdir, glob, or files."
      },
      "timeout_sec": {
        "type": "integer",
        "description": "Kill the query after this many seconds (default 900)."
      }
    },
    "required": [
      "question",
      "cwd"
    ]
  }
}
''')
