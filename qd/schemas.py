"""Tool schemas for the MCP surface -- the capability map ambient in every
session. Extracted from the v1 crane at cutover (M6); the qwen_delegate schema
gains the v2 input params (worktree, executor, trust, touch_scope, batch).

R1 (PLAN-v3-l5): descriptions are one-line descriptors -- a list of what each
tool/param does, nothing more. The measured guidance they used to carry lives in
skills/delegation/SKILL.md, loaded only when a session actually delegates. The
input SHAPES (names, params, enums, required) are frozen here; only description
text may change.

Additive evolution (C9): existing names, enums and required lists never change
-- a caller's working call must keep working. New OPTIONAL params may be added
by a C9 amendment, each landing with a spec proving that its ABSENCE leaves
behavior and receipt identical, so the freeze holds for everyone who never
passes it.
"""

import json

TOOL = json.loads(r'''
{
  "name": "qwen_delegate",
  "description": "Delegate a coding task to the local Qwen worker on free tokens. The call SUBMITS and answers in seconds with a run id, the file path its receipt will land at, a heartbeat file and a shell one-liner to wait on it -- do other work, then read that file (`wait: true` blocks and returns the receipt instead). The run builds in `cwd`; this server runs `verify` after each attempt and feeds failures back. The worker's self-report is never evidence -- the gate decides. Before first use, load the `delegation` skill: it carries the discipline (gates, approval modes, timeouts, fan-out, sessions).",
  "inputSchema": {
    "type": "object",
    "properties": {
      "task": {
        "type": "string",
        "description": "Concrete task: exact paths, symbols, expected end state. Vague tasks get invented scope -- route those through approval_mode 'plan'. A project's .qwen-delegate.json `task_suffix` is appended to every task server-side, so standing discipline does not have to be retyped here."
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
      "on_compaction": {
        "type": "string",
        "enum": [
          "refuse",
          "reinject",
          "discard"
        ],
        "description": "If the worker's session compacts mid-run: 'refuse' (default -- stop, trust nothing from the run, hand back so you can split the task), 'reinject' (continue on the summarised history, keeping the warm session) or 'discard' (cold restart). Compaction is the documented fabrication trigger; only 'refuse' declines to build on it."
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
        "description": "scoped mode only: extra allowed command regexes -- how you approve a command a prior run surfaced as SHELL APPROVAL NEEDED. Project default: .qwen-delegate.json `shell_allow`."
      },
      "shell_feedback": {
        "type": "string",
        "description": "scoped mode only: the reason for a denied command, shown to the worker up front so it stops retrying."
      },
      "mcp_allow": {
        "type": "array",
        "items": {
          "type": "string"
        },
        "description": "scoped mode only: allowed MCP tool-name regexes (names look like mcp__<server>__<tool>). Unlisted MCP tools are denied and surfaced by name -- approve by re-delegating with the pattern added. Project default: .qwen-delegate.json `mcp_allow`."
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
        "description": "'auto-edit' = write, no shell (default for code) | 'plan' = read-only (any vague task) | 'scoped' = auto-edit + allowlisted shell | 'yolo' = full shell (only when shell IS the work). 'default'/'auto' deny everything headless. Project default: .qwen-delegate.json `approval_mode`. Details: delegation skill."
      },
      "timeout_sec": {
        "type": "integer",
        "description": "Per-attempt kill, seconds (default 900, or project .qwen-delegate.json `timeout_sec`; max 7200). Size up for large tasks -- timing model in the delegation skill."
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
        "description": "'self': L5 full trust -- the delegate writes AND grades its own suite; `verify` optional (server generates a non-vacuous-suite gate). 'verified': your `verify` command is the gate -- pass it for correctness-critical, irreversible, or outward-facing work. Omit to use the configured default: project .qwen-delegate.json `trust` > machine ~/.qwen-delegate/config.json `trust` > built-in 'self'. If that default is 'auto', a bare call is refused so YOU pick 'self'/'verified' per task by criticality. Intermediate levels: parked."
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
        "description": "N independent delegations in ONE call (same fields per item), fanned across worktrees with per-item receipts -- the reliable fan-out. An item may itself carry `chain` for a batch OF pipelines: each item is an ordered chain sharing one worktree, and the items run concurrently. Nesting is one level (no `batch` inside an item)."
      },
      "chain": {
        "type": "array",
        "items": {
          "type": "object"
        },
        "description": "DEPENDENT steps in ONE call (same fields per item), run in order on the same tree; the first link that does not come back green halts the rest, which render as one-line SKIPPED receipts. Mutually exclusive with `batch` -- that one is for INDEPENDENT work."
      },
      "report_dont_fix": {
        "type": "boolean",
        "description": "Diagnose, do not repair: one attempt, one `verify` run, no retry loop, status 'reported'. The gate output is the deliverable (a red gate is the reproduction) plus a FINDINGS line from the worker."
      },
      "fixture_provenance": {
        "type": "boolean",
        "description": "Require every fixture file the run creates (under fixtures/testdata/golden/snapshots/cassettes, or project `fixture_globs`) to carry a `captured-from: <url or command> <date>` line in its first 10 lines -- a `<path>.src` sidecar for binaries. Violations are fed back by name; the last attempt ends 'fixture_unproven'. Imagined fixtures pass any gate written against them."
      },
      "verify_timeout_sec": {
        "type": "integer",
        "description": "Kill time for ONE `verify` run, seconds (default 300, or project .qwen-delegate.json `verify_timeout_sec`; clamped 10..3600). A pre-flight that times out refuses the run before any attempt is burned."
      },
      "preflight_expect": {
        "type": "string",
        "enum": [
          "red",
          "green",
          "any"
        ],
        "description": "What `verify` should say BEFORE the worker runs. 'red' (greenfield): a pre-flight that passes refuses the run -- the gate could not prove the work. 'green' (revision work on an already-passing suite): no success_but_preflight_passed demotion. 'any' (default): today's behavior. Project default: .qwen-delegate.json `preflight_expect`."
      },
      "result_schema": {
        "type": "object",
        "description": "The SHAPE you need back. The worker is told to end its reply with a fenced ```json block conforming to this (subset: type, required, properties, items, enum); the server validates it, feeds every violation back by path like a failed gate, and ends the run 'result_invalid' if the attempts run out. The receipt carries the block verbatim -- you parse a value instead of prose."
      },
      "brief_file": {
        "type": "string",
        "description": "Repo-relative markdown playbook to use as the brief: its body becomes the task (your `task` rides along as an addendum), `---` front matter supplies verify/touch_scope/approval_mode/timeouts where the call does not, and {{slot}}s fill from `vars`. Front matter `chain: true` compiles `## Step <n>` sections into a chain. The receipt pins `BRIEF: path @ digest`; the worker editing the document is reverted like a spec edit. Versioned by git -- send the name, not the text."
      },
      "vars": {
        "type": "object",
        "description": "Values for the {{slot}} placeholders in brief_file. An unfilled slot and a key matching no slot are both refused by name."
      },
      "amend_brief": {
        "type": "boolean",
        "description": "With retry_of: append retry_message to the playbook itself as a dated `## Amendments` line (git versions the correction) INSTEAD of a CORRECTION on the task text. The amendment lands before the run's snapshot, so it reads as pre-existing dirt, never worker change."
      },
      "retry_of": {
        "type": "string",
        "description": "session_id of an earlier delegation in this cwd: re-run its STORED brief (task, gate, scope, mode, trust) with `retry_message` appended, COLD -- no session resume, because a session that failed argues with the correction. Pass `task: \"\"` to reuse the stored task; any argument you do pass beats the stored one. Briefs live in .qwen-delegate/briefs/ (project key `store_briefs: false` opts out)."
      },
      "retry_message": {
        "type": "string",
        "description": "One line of correction for a `retry_of` run, appended to the stored task as CORRECTION. Say what the last attempt got wrong -- not the task again."
      },
      "wait": {
        "type": "boolean",
        "description": "Block until the run finishes and return the receipt in this response, instead of submitting. Default false: a call SUBMITS and answers at once with a run id, the receipt path it will land at, and a WATCH one-liner. Only pass this when you have nothing else to do with the wait."
      },
      "advisory_gates": {
        "type": "array",
        "items": {
          "type": "object"
        },
        "description": "Loose gates [{name, cmd}] run once after the final attempt in the run's tree. They NEVER affect status, never enter a retry prompt, never reach the worker -- red ones just glow in the receipt (architecture/conformance seams)."
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
      },
      "result_schema": {
        "type": "object",
        "description": "The SHAPE you need back: the worker is asked to end its answer with a fenced ```json block conforming to this (subset: type, required, properties, items, enum). A query has no retry loop, so the check is REPORTED -- one `RESULT:` line above the answer says valid, or names the first violation."
      }
    },
    "required": [
      "question",
      "cwd"
    ]
  }
}
''')
