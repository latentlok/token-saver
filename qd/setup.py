#!/usr/bin/env python3
"""
First-run setup, behavior frozen by specs/setup_spec.py.

Claude Code has no post-install hook, so this runs from a SessionStart hook and
makes itself idempotent instead: it stamps the plugin version under
~/.qwen-delegate/setup/ and does nothing on every later session. A version bump
un-stamps it, which is what makes this fire on INSTALL and on UPDATE and at no
other time.

Two hard rules, because a SessionStart hook runs before the user's first word:

  1. It must not fail. A hook that errors on a fresh machine makes the plugin
     look broken when the thing it is reporting is a config nit.
  2. It must be near-silent. Anything printed to stdout is injected into the
     session's context -- which is the exact budget this plugin exists to
     protect. Nothing to say, say nothing.

Run:  python3 qd/setup.py           # from a hook, or by hand
      python3 qd/setup.py --force   # re-run even if this version is stamped
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STAMP_DIR = os.environ.get("QWEN_DELEGATE_SETUP") or os.path.expanduser(
    "~/.qwen-delegate/setup")

# U5.3: the managed block in a project's CLAUDE.md. Everything between these
# two markers belongs to the plugin; everything outside them belongs to the
# user and is never touched.
BEGIN_MARK = "qwen-delegate:begin"
END_MARK = "qwen-delegate:end"


def plugin_version(root=None):
    """Version from .claude-plugin/plugin.json, or "unknown"."""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, ".claude-plugin", "plugin.json")) as f:
            return str(json.load(f).get("version") or "unknown")
    except Exception:
        return "unknown"


def stamp_path(version):
    return os.path.join(STAMP_DIR, f"{version}.json")


def already_ran(version):
    return os.path.isfile(stamp_path(version))


def write_stamp(version, findings):
    """Record that this version ran. Failure to write is not failure to set up."""
    try:
        os.makedirs(STAMP_DIR, exist_ok=True)
        tmp = stamp_path(version) + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"version": version, "findings": findings}, f)
        os.replace(tmp, stamp_path(version))
        return True
    except Exception:
        return False


def template_block(version, root=None):
    """The managed block from templates/CLAUDE-snippet.md, version-stamped.

    Returns the marker line, the block, and the closing marker as one string
    with no trailing newline -- exactly the span it replaces. None if the
    template has lost its markers, because writing a block that cannot be
    found again would make the next update impossible.
    """
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, "templates", "CLAUDE-snippet.md")) as f:
            lines = f.read().splitlines()
    except Exception:
        return None
    start = stop = None
    for i, line in enumerate(lines):
        if start is None and BEGIN_MARK in line:
            start = i
        elif start is not None and END_MARK in line:
            stop = i
            break
    if start is None or stop is None:
        return None
    return "\n".join(lines[start:stop + 1]).replace("{version}", version)


def update_managed_block(cwd=None, version=None, root=None):
    """Refresh the plugin's block inside <cwd>/CLAUDE.md, if it is installed.

    This block is the only place an AGENT reads about what the plugin can do:
    a capability that never reaches it goes undiscovered, and the changelog is
    not an agent surface. So it is MANAGED -- rewritten from the template once
    per plugin version, in place.

    The contract is what makes that safe to do to somebody's file:
      - no CLAUDE.md, or no markers in it, is a NO-OP. Not installing the
        block is a choice, and installing one uninvited is not this hook's to
        make;
      - only the span between the markers (inclusive) is rewritten -- every
        byte outside is preserved exactly, including where the file ends;
      - identical content is not written at all, so an unchanged version does
        not touch the file's mtime or a git diff.

    Returns "updated" | "current" | "absent". Never raises.
    """
    try:
        version = version or plugin_version(root)
        path = os.path.join(cwd or os.getcwd(), "CLAUDE.md")
        block = template_block(version, root)
        if block is None or not os.path.isfile(path):
            return "absent"
        with open(path) as f:
            text = f.read()
        i = text.find(BEGIN_MARK)
        j = text.find(END_MARK, i + 1) if i >= 0 else -1
        if i < 0 or j < 0:
            return "absent"
        start = text.rfind("\n", 0, i) + 1        # the begin marker's own line
        stop = text.find("\n", j)
        stop = len(text) if stop < 0 else stop
        if text[start:stop] == block:
            return "current"
        tmp = path + ".qd-tmp"
        with open(tmp, "w") as f:
            f.write(text[:start] + block + text[stop:])
        os.replace(tmp, path)
        return "updated"
    except Exception:
        return "absent"


def build_notice(version, findings):
    """The message for the session, or None when there is nothing worth saying.

    Only HIGH findings earn context. Everything else is available on demand from
    the doctor command, and a setup notice that cries wolf gets ignored on the
    install where it mattered.
    """
    high = [f for f in findings if f.get("severity") == "high"]
    if not high:
        return None
    lines = [
        f"token-saver {version} — first run on this machine. The executor "
        f"settings live in ~/.qwen/settings.json and do NOT ship with the plugin; "
        f"{len(high)} of them will affect delegations here:",
    ]
    for f in high:
        # Split on ". ", never ".", or a version number in a model name ends the
        # sentence early -- "Model 'qwen3." was the whole finding.
        lines.append(f"  - {f['id']}: {f['text'].split('. ')[0].strip()}.")
    lines.append("Run `/token-saver:doctor` for the detail, or "
                 "`python3 -m qd.doctor --fix` to write the safe fixes. "
                 "These are machine settings, not repo bugs.")
    return "\n".join(lines)


def run(force=False):
    """(notice_or_None, ran_bool). Never raises."""
    version = plugin_version()
    if already_ran(version) and not force:
        return None, False
    # U5.3: once per version, in the project this session opened. Deliberately
    # before the doctor check below, which returns early on a machine where
    # Qwen is not configured yet -- the capability map is worth refreshing
    # either way, and it is silent, so it costs the session nothing.
    update_managed_block()
    try:
        from qd import doctor
        findings = doctor.check(doctor.load())
    except FileNotFoundError:
        # Qwen Code isn't configured on this machine yet. That is a legitimate
        # state mid-install, not an error to shout about -- and stamping it would
        # mean never checking again, so leave the stamp off.
        return None, False
    except Exception:
        findings = []
    write_stamp(version, [f["id"] for f in findings])
    return build_notice(version, findings), True


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        notice, _ = run(force="--force" in argv)
    except Exception:
        return 0                      # rule 1: a hook must not fail
    if notice:
        print(notice)
    return 0


if __name__ == "__main__":
    sys.exit(main())
