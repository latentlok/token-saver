"""Tool schemas for the MCP surface -- the capability map ambient in every
session. Extracted from the v1 crane at cutover (M6); the qwen_delegate schema
gains the v2 input params (worktree, executor, trust, touch_scope, batch).
M7 rewrites the description text as the capability map; the shapes are frozen here.
"""

import json

TOOL = json.loads(r'''
{
  "name": "qwen_delegate",
  "description": "Delegate a well-specified coding task to a local Qwen Code agent. Qwen runs with shell/edit/write enabled (and Firecrawl web access) in the given workspace and returns only its final result -- its tool-call noise stays out of your context.\n\nUse for mechanical, verifiable work (boilerplate, repetitive refactors, test writing, doc generation, web research), not tasks needing judgment. Qwen is a 27B local model: given a vague task it does NOT stop and ask -- it confidently invents scope and reports success. Specify exact files, symbols, and expected behavior.\n\nALWAYS pass `verify` -- a shell command exiting 0 only if the task truly succeeded. Qwen has fabricated 'all tests pass' for tests it never ran; its claim is not evidence, only the gate is. On failure the tool feeds the real error output back and retries in the same session (see max_iterations); worker tokens are free, so prefer letting it iterate over returning a failure to you.\n\nFiles matching *_spec.* / *.spec.* (any language) are YOUR protected spec: if Qwen edits one it is auto-reverted and the attempt fails. Write gate tests as <name>_spec.<ext>; let Qwen write its own as <name>_qwen.<ext>. Override per-project in .qwen-delegate.json {\"spec_globs\": [...]}.\n\nRe-read any file Qwen touched before editing it yourself -- your cached copy is stale. Parallel calls MUST use separate `cwd` worktrees.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "task": {
        "type": "string",
        "description": "The task, specified concretely enough to execute without judgment calls: name exact file paths, symbols, and the expected end state. Vague tasks produce confident invented scope, not questions."
      },
      "cwd": {
        "type": "string",
        "description": "Absolute path to the workspace. Qwen runs at full user privilege, so scope this to the project or worktree. For parallel fan-out, give each call its own git worktree."
      },
      "verify": {
        "type": "string",
        "description": "Shell command run in `cwd` after each attempt; exit 0 means real success (e.g. './gate.sh', 'npm test', 'cargo test', 'venv/bin/pytest -q'). Its output is fed back to Qwen on failure, so make failures legible. Strongly recommended -- omit only for read-only/research tasks."
      },
      "max_iterations": {
        "type": "integer",
        "description": "Attempts before giving up (1 = one shot, no retry; max 10). On each failure the verify output is fed back and Qwen retries warm -- the manager is NOT in that loop. Usually omit: the retry budget defaults to the project's .qwen-delegate.json `max_iterations`, then the built-in default (3); pass it only to deviate for one call. Each attempt is a full build, so it also bounds wall time -- keep it modest."
      },
      "workers": {
        "type": "integer",
        "description": "Best-of-N: run up to this many INDEPENDENT candidates for the task and accept the first whose gate passes (1 = single candidate, the default; max 8). Free worker tokens, zero manager cost -- but each candidate is a full build, so it multiplies wall time. Usually omit: defaults to the project's .qwen-delegate.json `workers`, then 1. Needs a `verify` gate to pick the winner and a committed base to reset between candidates."
      },
      "on_compaction": {
        "type": "string",
        "enum": [
          "reinject",
          "discard"
        ],
        "description": "YOUR call if Qwen's session gets compacted mid-run. Compaction summarises history away and is the documented trigger for fabrication -- after one, Qwen claimed to have read 13 files it never opened.\n\n'reinject' (default) -- keep the WARM session, restore the task into it. Cheap, keeps files already read, but the compaction summary REMAINS IN ITS HISTORY (good context beside the possibly-false context that fabricates).\n\n'discard' -- abandon the session, restart cold on the same tree. Costs a fresh ~21.6k preamble and all it had learned, but is the ONLY option that removes the corrupted summary; the fresh session re-reads QWEN.md, which makes the rules bind.\n\nChoose 'discard' when correctness matters more than latency: long multi-file work, anything where a false 'I already did that' is expensive, or when a compacted run reported something you could not verify. A cold restart costs latency only."
      },
      "session_id": {
        "type": "string",
        "description": "Resume a prior Qwen session (the SESSION value from an earlier call) to continue THAT task with warm context. Sessions are cwd-scoped: pass the same cwd or the id will not resolve.\n\nSTATEFUL when the follow-up builds directly on what Qwen just did ('now add X to the function you wrote', 'fix the edge case you missed').\n\nSTATELESS (omit this) otherwise -- the default, usually correct: a fresh session re-reads QWEN.md (which makes the rules bind) and keeps one task's reasoning from contaminating the next. A long-lived session drifts as its context fills and silently forgets the rules."
      },
      "shell_allow": {
        "type": "array",
        "items": {
          "type": "string"
        },
        "description": "Only with approval_mode='scoped'. Extra regex patterns of shell commands to allow, beyond the built-in read-only/test set and the exact `verify` command (always allowed). E.g. ['^make build$', '^tsc --noEmit$']. This is how you APPROVE a command a prior scoped run surfaced as SHELL APPROVAL NEEDED: judge the command alone (safe in this repo?), and if yes add its pattern here and re-delegate with the same session_id. Keep it tight -- these run at user privilege. Compound/redirect/network commands are rejected regardless."
      },
      "shell_feedback": {
        "type": "string",
        "description": "Only with approval_mode='scoped'. When you DENY a command a prior run requested, put the reason here (e.g. \"denied `rm -rf ~/data`: deletes outside the repo; clean only ./build\"). Shown to Qwen up front on re-delegation so it understands the constraint and stops retrying. A denial without a reason just makes Qwen guess -- always say why."
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
        "description": "Tool-approval policy. Pick the WEAKEST mode that can do the job.\n\nMeasured behaviour (probed; the bundle does not document this):\n  plan       write NO   shell NO   (also blocks agent/exit_plan_mode)\n  default    write NO   shell NO   (headless auto-denies -- useless)\n  auto-edit  write YES  shell NO   <- BEST DEFAULT for code tasks\n  auto       write NO   shell NO   (useless headless, same as default)\n  scoped     write cwd  shell ALLOWLIST  <- when Qwen should run tests\n  yolo       write YES  shell YES  (only when shell IS the work)\n\n'scoped' is auto-edit plus a safe shell: Qwen may run the exact `verify` command, a read-only/test allowlist (pytest, git status/diff/log, ls, grep, ...), and any `shell_allow` patterns you add, so it checks its own work before the gate. Writes confined to cwd; rm/curl/network/git push/compound-commands denied; anything blocked is surfaced as ELICITATION so you can re-delegate with it allowed. Enforced by a PreToolUse hook (yolo underneath so the hook fires); the boundary is validated -- an out-of-cwd write and an rm were both blocked.\n\nPREFER 'auto-edit' OVER 'yolo'. Qwen does not need shell to converge -- THIS server runs `verify` and feeds failures back, so the iterate loop is server-driven. Measured: in auto-edit, told to use a banned module, Qwen failed the gate on attempt 1, read the feedback, and passed on attempt 2 with all 3 of its shell attempts denied. Same convergence as yolo, but arbitrary command execution at user privilege is unreachable. Use yolo only when running something IS the task (build, migration, git ops).\n\nUSE 'plan' FOR ANY VAGUE TASK -- the only safe way to delegate underspecified work: Qwen physically cannot write, so it investigates and returns options instead of inventing scope. Two-phase workflow: (1) plan with approval_mode='plan' (no verify needed) -> options + a SESSION id; (2) let the user choose; (3) execute the chosen option with session_id=<from step 1>, approval_mode='auto-edit', verify=<a real gate> -> resumes warm.\n\nWhy (measured): given a vague task in yolo, Qwen did NOT stop and ask -- it invented a 4-feature plan, silently changed a public API, and all 909 upstream tests still passed because none asserted the changed behaviour. A vague task cannot be gated; a chosen plan item can. Never delegate a vague task straight to yolo."
      },
      "timeout_sec": {
        "type": "integer",
        "description": "Kill each attempt after this many seconds (default 900, max 7200). ESTIMATE THIS for anything beyond a single small file -- the default is sized for ~25k-context tasks and a large task WILL be killed mid-write, leaving a partial tree.\n\nFitted from 198 real calls on this box:\n    seconds = (turns * avg_context)/10882  +  output_tokens/70\n  prefill ~10,882 tok/s; decode ~70 tok/s (measured). avg_context ~= (22000 + peak_context)/2, since context grows from a ~22k baseline and every turn re-prefills the whole context.\n\nWorked example -- a task peaking at 120k over ~30 turns emitting ~15k tokens: avg_context=(22000+120000)/2=71000, prefill=30*71000/10882=196s, decode=15000/70=214s, ~410s -> set timeout_sec ~1200 (about 3x).\n\nUse 2-3x headroom: the estimate is a median and p90 calls ran 3x median. Over-setting costs nothing (the timeout only fires on a hang); under-setting destroys the run."
      },
      "worktree": {
        "type": "string",
        "enum": [
          "auto",
          "off"
        ],
        "description": "'auto' runs the delegation in an isolated git worktree (branched from HEAD) and, on success, commits the work to a qwen/<id> branch and reports whether it merges cleanly -- the receipt carries the exact MERGE command for you to run. Use for fan-out / parallel builds so each worker has its own tree. Default 'off' runs in-tree under a per-repo lock."
      },
      "executor": {
        "type": "string",
        "description": "Name of an executor profile from ~/.qwen-delegate/executors.json (model + endpoint + pricing). Omit to use the machine default, then the builtin 'qwen-local'."
      },
      "trust": {
        "type": "string",
        "description": "Trust level. Only 'verified' is accepted today (gate decides, worker self-report ignored); other levels are a parked design (see DIRECTION-v2)."
      },
      "touch_scope": {
        "type": "array",
        "items": {
          "type": "string"
        },
        "description": "Allowlist of repo-relative files the worker may MODIFY. Out-of-scope edits to pre-existing files are auto-reverted and the attempt fails; creating NEW files is always allowed. Omit for no restriction."
      },
      "batch": {
        "type": "array",
        "items": {
          "type": "object"
        },
        "description": "Fan out N independent delegations in ONE call, each an object with the same fields as a single delegate (task, verify, ...). Runs them in parallel across worktrees (capped by the endpoint), returns per-item receipts. This is the reliable way to parallelize from one session -- the client serializes separate tool calls."
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
  "description": "Ask the local Qwen worker an open-ended question ABOUT the code, READ-ONLY. Qwen reads (glob/grep/targeted reads) and answers; it cannot write or change anything (plan mode -- structurally safe, no gate needed). Its tokens are free, so you get an answer instead of spending your own context on the files.\n\nUse it to think with the codebase without touching it: 'how does auth flow to the token check?', 'is there already a duration parser?', 'what breaks if I change load()'s return type?'. It is a conversation -- pass the returned SESSION as `session_id` for a warm follow-up.\n\nformat='answer' (default) gives a direct prose answer with a VERIFY list; format='map' gives a structured MAP / KEY SYMBOLS / CONNECTIONS / ANSWER / VERIFY to orient in an unfamiliar repo (the old qwen_investigate).\n\nTreat the answer as a LEAD, not truth: Qwen's reading is broad but its conclusions are often wrong in plausible ways (it once mapped a library perfectly yet fabricated every line number). VERIFY says what to confirm against source before a decision rests on it.\n\nKeep each question BOUNDED -- a few files, not 'read the whole repo': a huge read pushes Qwen past compaction, after which it fabricates having read things. Several small queries, not one giant one; the response reports peak context so you can see it stayed safe.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "question": {
        "type": "string",
        "description": "The question to answer by reading the code. Open-ended is fine ('how does X work', 'why might Y fail', 'is there already a Z'). Scope it so a few files answer it, not the whole repo at once."
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
        "description": "'answer' (default): direct prose answer + VERIFY. 'map': structured codebase map (MAP/KEY SYMBOLS/CONNECTIONS/ANSWER/VERIFY) for orienting."
      },
      "session_id": {
        "type": "string",
        "description": "Resume a prior query conversation (the SESSION from an earlier qwen_query) to ask a warm follow-up -- Qwen still holds what it read. Same cwd required. Omit to start fresh."
      },
      "focus": {
        "type": "string",
        "description": "Optional: narrow the reading to a subdir ('src/parser'), glob ('**/*.ts'), or files. Keeps it bounded and cheap."
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
