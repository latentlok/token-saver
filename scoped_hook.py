#!/usr/bin/env python3
"""
PreToolUse hook for scoped shell. Qwen Code runs this before every tool call and
honours the permissionDecision it prints. The server wires it in (via a temp
QWEN_CODE_SYSTEM_SETTINGS_PATH) only for approval_mode="scoped", and configures it
through environment variables -- it is never edited per-run.

Policy, gating EVERY effect-bearing tool (not just shell -- Qwen routes around a
single-tool block: a denied `touch` becomes a write_file to the same path):

  run_shell_command : allow the exact verify command (trusted, the manager wrote it),
                      plus a read-only/test allowlist. Reject compound/redirect/
                      substitution outright -- a pipeline can smuggle a denied command.
  write_file/edit   : allow only inside the project cwd. No escaping the repo.
  everything else   : allow (read_file, glob, grep, etc. are harmless).

Anything denied is appended to QGATE_DENYLOG so the server can surface it to the
manager as an elicitation ("Qwen wanted X; approve by re-delegating, or it was
correctly blocked").

Fail closed: any error → deny.
"""
import json
import os
import re
import sys

CWD = os.environ.get("QGATE_CWD", "")
VERIFY = os.environ.get("QGATE_VERIFY", "")          # exact command, always allowed
DENYLOG = os.environ.get("QGATE_DENYLOG", "")
try:
    EXTRA = json.loads(os.environ.get("QGATE_EXTRA", "[]"))  # extra regex patterns
except Exception:
    EXTRA = []

# read-only / test commands that are safe by default
DEFAULT_ALLOW = [
    r"^\./venv/bin/pytest\b", r"^\.?/?venv/bin/python3? -m pytest\b",
    r"^python3? -m pytest\b", r"^pytest\b",
    r"^git (status|diff|log|show|branch)\b",
    r"^ls\b", r"^cat\b", r"^head\b", r"^tail\b", r"^wc\b",
    r"^grep\b", r"^rg\b", r"^find\b\.?", r"^echo\b", r"^pwd\b",
    # graph READ queries only, all LLM-free -- update/watch/add/install (state-changing)
    # and extract/label/cluster-only (reach an LLM) stay denied
    r"^graphify (explain|path|diagnose|query|affected|god-nodes)\b",
]
# never allow these even if a pattern would match (state / network / escalation)
HARD_DENY = [r"\brm\b", r"\bmv\b", r"\bcurl\b", r"\bwget\b", r"\bsudo\b",
             r"\bchmod\b", r"\bchown\b", r"\bpip\b", r"\bnpm\b.*\b(install|i)\b",
             r"\bgit\b.*\b(push|commit|reset|checkout|clean|rebase)\b"]
# shell metacharacters that could chain/redirect around the allowlist
UNSAFE_META = [";", "&&", "||", "|", "`", "$(", ">", "<", "&", "\n"]


def log_deny(what):
    if DENYLOG:
        try:
            with open(DENYLOG, "a") as f:
                f.write(what + "\n")
        except Exception:
            pass


def decide(tool, ti):
    if tool == "run_shell_command":
        cmd = (ti.get("command") or "").strip()
        if VERIFY and cmd == VERIFY.strip():
            return True, "exact verify command"          # trusted; may contain &&/pipes
        if any(m in cmd for m in UNSAFE_META):
            return False, "compound/redirect/substitution not allowed"
        if any(re.search(p, cmd) for p in HARD_DENY):
            return False, "state-changing or network command"
        if any(re.search(p, cmd) for p in DEFAULT_ALLOW + EXTRA):
            return True, "on allowlist"
        return False, "not on the shell allowlist"
    if tool in ("write_file", "edit", "replace"):
        p = ti.get("file_path") or ti.get("path") or ""
        try:
            ap = os.path.realpath(p) if p else ""
        except Exception:
            return False, "unresolvable path"
        if CWD and (ap == CWD or ap.startswith(CWD + os.sep)):
            return True, "write inside project"
        return False, "write outside the project"
    return True, "read-only tool"


def main():
    try:
        d = json.load(sys.stdin)
        tool = d.get("tool_name", "")
        ti = d.get("tool_input") or {}
        ok, why = decide(tool, ti)
    except Exception as e:
        ok, why, tool, ti = False, f"hook error: {e!r}", "?", {}
    if not ok:
        target = (ti.get("command") or ti.get("file_path") or ti.get("path") or "?")
        log_deny(f"{tool}: {str(target)[:100]}  ({why})")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" if ok else "deny",
        "permissionDecisionReason": why,
    }}))


if __name__ == "__main__":
    main()
