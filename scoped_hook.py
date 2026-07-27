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
  known read tools  : allow (read_file, glob, grep, list_directory).
  everything else   : allow ONLY if its input carries no effect-shaped key --
                      see decide().

Anything denied is appended to QGATE_DENYLOG so the server can surface it to the
manager as an elicitation ("Qwen wanted X; approve by re-delegating, or it was
correctly blocked").

C10, the allow side: every allowed write appends its RESOLVED path to
QGATE_WRITELOG and every allowed shell command (plus every ungated tool) appends
a line to QGATE_ALLOWLOG. That is the attribution channel -- without it the
engine's guards cannot tell a worker edit from a caller's concurrent one, and
they revert whatever moved.

QGATE_MODE="autoedit" runs the same policy for writes with NO shell at all: that
mode exists to attribute writes under auto-edit, and no manager-approved
allowlist reaches it.

Fail closed: any error → deny.
"""
import json
import os
import re
import sys

CWD = os.environ.get("QGATE_CWD", "")
VERIFY = os.environ.get("QGATE_VERIFY", "")          # exact command, always allowed
DENYLOG = os.environ.get("QGATE_DENYLOG", "")
WRITELOG = os.environ.get("QGATE_WRITELOG", "")      # C10: one resolved path per write
ALLOWLOG = os.environ.get("QGATE_ALLOWLOG", "")      # C10: allowed shell + ungated tools
MODE = os.environ.get("QGATE_MODE", "scoped")        # "scoped" | "autoedit"
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

# Inputs that mean "this call has an effect somewhere". An UNKNOWN tool carrying
# one is denied: the old default-allow tail passed anything not named above as
# "read-only", which is the same hole a denied `touch` walks through as a
# write_file -- one unrecognised tool name and the gate is off.
EFFECT_KEYS = ("file_path", "path", "command", "content")

# ...except that the executor's own READ tools take `path` as a SEARCH ROOT
# (glob, search_file_content/grep, list_directory). Judging them by shape alone
# would deny file search outright, and a gate that blocks reading is the kind
# that gets switched off entirely -- so the read tools are named.
READ_ONLY = ("read_file", "read_many_files", "glob", "grep",
             "search_file_content", "list_directory", "ls")


def _append(path, what):
    """Best-effort log line. A log that raises would fail the tool call it is
    only supposed to observe."""
    if not path:
        return
    try:
        with open(path, "a") as f:
            f.write(what + "\n")
    except Exception:
        pass


def log_deny(what):
    _append(DENYLOG, what)


def log_write(path):
    _append(WRITELOG, path)


def log_allow(what):
    _append(ALLOWLOG, what)


def decide(tool, ti):
    """(allow, reason). Allow-side logging happens HERE, not in main(): the
    write log has to carry the RESOLVED path, and this is the only place that
    resolves it (C10 -- the engine attributes reverts to that exact string)."""
    if tool == "run_shell_command":
        cmd = (ti.get("command") or "").strip()
        if MODE == "autoedit":
            # Observed auto-edit installs this hook for attribution only; no
            # manager-approved allowlist reaches it, so an allowed command here
            # would be running ungated under yolo.
            return False, ("shell is denied under observed auto-edit -- use "
                           "write/edit tools or re-delegate with "
                           "approval_mode=scoped + shell_allow")
        if VERIFY and cmd == VERIFY.strip():
            log_allow(f"run_shell_command: {cmd}")
            return True, "exact verify command"          # trusted; may contain &&/pipes
        if any(m in cmd for m in UNSAFE_META):
            return False, "compound/redirect/substitution not allowed"
        if any(re.search(p, cmd) for p in HARD_DENY):
            return False, "state-changing or network command"
        if any(re.search(p, cmd) for p in DEFAULT_ALLOW + EXTRA):
            log_allow(f"run_shell_command: {cmd}")
            return True, "on allowlist"
        return False, "not on the shell allowlist"
    if tool in ("write_file", "edit", "replace"):
        p = ti.get("file_path") or ti.get("path") or ""
        try:
            ap = os.path.realpath(p) if p else ""
        except Exception:
            return False, "unresolvable path"
        if CWD and (ap == CWD or ap.startswith(CWD + os.sep)):
            log_write(ap)
            return True, "write inside project"
        return False, "write outside the project"
    if tool in READ_ONLY:
        return True, "read-only tool"
    if any(k in ti for k in EFFECT_KEYS):
        return False, "unknown tool carrying an effect-shaped input"
    # Default-allow survives (denying every unknown tool would break the
    # firecrawl/web research path), but it is now RECORDED: a tool nobody gated
    # ran during this run, and the receipt gets to say so.
    log_allow(f"ungated:{tool}")
    return True, "not an effect-bearing tool we gate (default-allow)"


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
