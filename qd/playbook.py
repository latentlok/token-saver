"""Playbooks -- versioned delegation documents (Phase 6).

A playbook is a markdown file IN THE REPO that carries a delegation: the body
is the task, optional front matter supplies call params the caller did not,
`{{slot}}`s fill from `vars`, and `## Step <n>` sections compile to a chain.
This module is the whole parsing and composition surface -- pure functions
plus one confined file read -- so the engine and the server can both resolve
a brief without either owning it. Behavior frozen by specs/playbook_spec.py.
"""

import hashlib
import json
import os
import re

# The front-matter allowlist. Unrecognised keys are refused BY NAME rather
# than skipped: a typo'd gate key silently ignored is a gate that never runs.
# trust / executor / worktree are deliberately absent -- who is trusted and
# where the run happens are the CALLER's decisions, never the document's.
#
# Be honest about what that line does NOT cover: `verify` is on this list, and
# `verify` is a COMMAND this machine runs -- the pre-flight runs it at
# qd/engine.py before the worker starts at all, so no approval mode,
# touch_scope or trust level stands between the document and it. Same for
# `advisory_gates[].cmd`. By the standard the paragraph above sets, that is a
# larger grant than the three keys it excludes, and a reader deserves to see
# the two statements next to each other rather than infer the gap. Documented
# for callers in docs/archive/a92e876/USAGE.md ("`verify` IS a front-matter key"). Whether the
# allowlist should keep admitting it is a design decision for the humans who
# own this contract, not something to quietly change under a bugfix.
FRONT_KEYS = ("verify", "touch_scope", "shell_allow", "approval_mode",
              "timeout_sec", "verify_timeout_sec", "preflight_expect",
              "advisory_gates", "max_iterations", "chain")

# Values land in the same engine paths as call args, and a front-matter value
# of the wrong shape binds SILENTLY wrong there (a bare-string touch_scope
# turns the membership check into substring matching) -- so the shape is
# refused here by name, where the author can fix the document.
_LIST_KEYS = ("touch_scope", "shell_allow", "advisory_gates")
_INT_KEYS = ("timeout_sec", "verify_timeout_sec", "max_iterations")
_STR_KEYS = ("verify", "approval_mode", "preflight_expect")

_SLOT = re.compile(r"\{\{(\w+)\}\}")
# Case-insensitive on "step" only -- the digit and the optional ": title" are
# structural, and `## Step 2` / `## step 2: wire it` both read naturally.
_STEP = re.compile(r"^##\s+step\s+\d+\s*(?::\s*.*)?$", re.I | re.M)
_AMENDMENTS = re.compile(r"(?m)^## Amendments\s*$")
_OVERRIDE = re.compile(r"^(verify|touch_scope):\s*(.*)$")


def read(cwd, rel):
    """Read a brief file; returns (text, refusal). Realpath-confined inside
    `cwd` -- same rule and reasons as engine._repo_relative: briefs live
    beside the code they change, and a path (or symlink) that resolves
    outside the tree must not be readable through this seam."""
    try:
        root = os.path.realpath(cwd)
        full = os.path.realpath(os.path.join(cwd, rel))
    except Exception:
        return None, f"brief_file \"{rel}\": path could not be resolved."
    if full != root and not full.startswith(root + os.sep):
        return None, (f"brief_file \"{rel}\": resolves outside the workspace "
                      f"-- briefs live in the repo they run in, sent by "
                      f"repo-relative name.")
    try:
        with open(full) as f:
            return f.read(), None
    except OSError:
        return None, (f"brief_file \"{rel}\": no readable file at that path "
                      f"under {cwd}. Briefs are repo files sent by name -- "
                      f"check the path, or create the document first.")


def substitute(text, vars):
    """Fill `{{name}}` slots; returns (text, missing, unused).

    Applied to the WHOLE file before the front matter is parsed -- a slot in
    `verify:` is the useful case. Both directions refuse upstream: an
    unfilled slot would send literal braces to the worker, and a `vars` key
    matching no slot is almost always a typo'd slot name. `{{` is reserved;
    there is no escape hatch (the docs say so).
    """
    vars = vars if isinstance(vars, dict) else {}
    names = set(_SLOT.findall(text or ""))
    missing = sorted(n for n in names if n not in vars)
    unused = sorted(k for k in vars if k not in names)
    out = _SLOT.sub(lambda m: str(vars[m.group(1)])
                    if m.group(1) in vars else m.group(0), text or "")
    return out, missing, unused


def _value(raw):
    """A front-matter value: JSON when it parses, else the bare string."""
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _typed(key, value):
    """Refusal text when `value` is the wrong shape for `key`, else None."""
    if key in _LIST_KEYS and not isinstance(value, list):
        return f"`{key}` must be a JSON list (e.g. [\"a.py\"])"
    if key in _INT_KEYS and (isinstance(value, bool)
                             or not isinstance(value, int)):
        return f"`{key}` must be an integer"
    if key == "chain" and not isinstance(value, bool):
        return "`chain` must be true or false"
    if key in _STR_KEYS and not isinstance(value, str):
        return f"`{key}` must be a string"
    return None


def front_matter(text):
    """Parse the optional front matter; returns (meta, body, refusal).

    Opens only when the FIRST line is exactly `---`; `key: value` lines until
    the closing `---`. Blank lines inside the fence are allowed; anything
    else that is not `key: value` is refused by name -- so is an unclosed
    fence, an unrecognised key, and a wrong-shaped value.
    """
    lines = (text or "").splitlines()
    if not lines or lines[0] != "---":
        return {}, text or "", None
    meta = {}
    for i, line in enumerate(lines[1:], start=2):
        if line.strip() == "---":
            return meta, "\n".join(lines[i:]).lstrip("\n"), None
        if not line.strip():
            continue
        if ":" not in line:
            return {}, "", (f"front matter line {i} is not `key: value`: "
                            f"\"{line.strip()}\"")
        key, _, raw = line.partition(":")
        key = key.strip()
        if key not in FRONT_KEYS:
            return {}, "", (f"front matter key \"{key}\" is not recognised "
                            f"(a typo'd gate key silently ignored is a gate "
                            f"that never runs). Recognised: "
                            f"{', '.join(FRONT_KEYS)}.")
        value = _value(raw.strip())
        bad = _typed(key, value)
        if bad:
            return {}, "", f"front matter: {bad} (got: {raw.strip()!r})"
        meta[key] = value
    return {}, "", "front matter opens with --- but never closes its fence."


def _step_overrides(section):
    """Split a step section into (overrides, task_text, refusal).

    Overrides are LEADING `verify:` / `touch_scope:` lines only, and the
    first non-blank line that is neither ends the block -- prose routinely
    opens with `Goal:` / `Note:`, so the front matter's refuse-on-unknown-key
    rule must NOT be reused here. The override lines are routing metadata and
    are stripped from the task the worker sees.
    """
    lines = section.splitlines()
    over = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _OVERRIDE.match(line)
        if not m:
            break
        key, raw = m.group(1), m.group(2).strip()
        value = _value(raw)
        bad = _typed(key, value)
        if bad:
            return None, "", bad + f" (got: {raw!r})"
        over[key] = value
        i += 1
    return over, "\n".join(lines[i:]).strip(), None


# What a compiled link must NOT inherit: the document keys it was compiled
# FROM (re-reading would re-compose), the shape keys that route a whole
# submission, and the retry fields (retry_of replays one link, never a
# document -- the stored brief per link is what makes that work).
_LINK_DROP = ("brief_file", "vars", "chain", "batch", "task", "wait",
              "amend_brief", "retry_of", "retry_message")


def compile_steps(body, base_args):
    """Compile `## Step <n>` sections into chain links; returns (links, refusal).

    Each link's task is the preamble (everything before the first Step) plus
    that step's own section -- per-link context load stays flat as the
    document grows, which is the pressure valve for big briefs.
    """
    matches = list(_STEP.finditer(body or ""))
    if not matches:
        return None, ("chain: true but the body has no `## Step <n>` "
                      "sections -- add them, or drop `chain` from the front "
                      "matter.")
    preamble = body[:matches[0].start()].strip()
    links = []
    for j, m in enumerate(matches):
        end = matches[j + 1].start() if j + 1 < len(matches) else len(body)
        over, text, refusal = _step_overrides(body[m.end():end])
        if refusal:
            return None, f"{m.group(0).strip()}: {refusal}"
        task = m.group(0).strip() + ("\n\n" + text if text else "")
        if preamble:
            task = preamble + "\n\n" + task
        link = {k: v for k, v in base_args.items() if k not in _LINK_DROP}
        link.update(over)
        link["task"] = task
        link["_brief"] = dict(base_args.get("_brief") or {}, chars=len(task),
                              addendum="")
        links.append(link)
    return links, None


def amendment_count(text):
    """How many `- ` entries the `## Amendments` section holds."""
    m = _AMENDMENTS.search(text or "")
    if not m:
        return 0
    count = 0
    for line in text[m.end():].splitlines():
        if line.startswith("## "):
            break
        if line.lstrip().startswith("- "):
            count += 1
    return count


def amend(cwd, rel, message, date):
    """Append `- <date> <message>` under `## Amendments`; returns refusal|None.

    The section is created at the END of the file when absent, and the line
    always lands at the end -- the documented convention is that Amendments
    is the last section. Git is what versions the document, so this write is
    the whole persistence story.
    """
    text, refusal = read(cwd, rel)
    if refusal:
        return refusal
    out = text.rstrip("\n")
    if not _AMENDMENTS.search(out):
        out += "\n\n## Amendments\n"
    else:
        out += "\n"
    out += f"- {date} {message}\n"
    try:
        with open(os.path.realpath(os.path.join(cwd, rel)), "w") as f:
            f.write(out)
    except OSError:
        return f"brief_file \"{rel}\": the amendment could not be written."
    return None


def resolve(args, amended=False):
    """Resolve `brief_file` into a runnable call; returns (args, refusal).

    read -> substitute -> front matter -> front-matter values fill in ONLY
    where the call arg is absent (that alone gives args > front matter >
    project > machine, because every consumer already falls back to config)
    -> task = body plus the caller's non-empty `task` as an addendum ->
    `_brief` reserved arg set. `chain: true` also compiles the Step sections
    into `args["chain"]`.

    No `brief_file` returns args untouched, so this is idempotent and safe
    on every path -- a compiled link carries `_brief`, never `brief_file`,
    and is never re-read or re-amended.
    """
    rel = args.get("brief_file")
    if not rel:
        return args, None
    cwd = args.get("cwd") or "."
    raw, refusal = read(cwd, rel)
    if refusal:
        return args, refusal
    text, missing, unused = substitute(raw, args.get("vars"))
    if missing:
        slots = ", ".join("{{%s}}" % n for n in missing)
        return args, (f"brief_file \"{rel}\": unfilled slot(s) {slots} -- "
                      f"pass them in `vars`.")
    if unused:
        return args, (f"vars key(s) {', '.join(unused)} match no "
                      f"{{{{slot}}}} in \"{rel}\" -- a typo on one side or "
                      f"the wrong document.")
    meta, body, refusal = front_matter(text)
    if refusal:
        return args, f"brief_file \"{rel}\": {refusal}"
    out = dict(args)
    filled = []
    for key, value in meta.items():
        if key == "chain":
            continue
        if out.get(key) is None:
            out[key] = value
            filled.append(key)
    addendum = (args.get("task") or "").strip()
    task = body.strip()
    if addendum:
        task = task + "\n\n" + addendum
    out["task"] = task
    out["_brief"] = {
        "path": rel,
        # The digest names the DOCUMENT version (post-amendment, before
        # slots): two runs off the same bytes group in the ledger even when
        # their vars differ.
        "sha256": hashlib.sha256(
            raw.encode("utf-8", "replace")).hexdigest()[:16],
        "amended": bool(amended),
        "chars": len(task),
        "amendments": amendment_count(raw),
        "addendum": addendum,
        "filled": filled,
    }
    if meta.get("chain"):
        links, refusal = compile_steps(body, out)
        if refusal:
            return args, f"brief_file \"{rel}\": {refusal}"
        out["chain"] = links
    return out, None
