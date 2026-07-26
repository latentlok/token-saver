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
