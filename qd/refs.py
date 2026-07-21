"""refs pinning — the refs dir is git-ignored so git diff cannot see it; this module detects fetched references by listing/diffing; behavior frozen by specs/refs_spec.py."""

import os


def snapshot(cwd):
    """Return {relative_filename: (size, mtime_ns)} for files in .qwen-delegate/refs/."""
    ref_dir = os.path.join(cwd, ".qwen-delegate", "refs")
    if not os.path.isdir(ref_dir):
        return {}
    result = {}
    for entry in os.listdir(ref_dir):
        full = os.path.join(ref_dir, entry)
        if os.path.isfile(full):
            st = os.stat(full)
            result[entry] = (st.st_size, st.st_mtime_ns)
    return result


def added(before, cwd):
    """Return sorted list of filenames that are new or changed since *before*."""
    now = snapshot(cwd)
    added = []
    for name, fp in now.items():
        if name not in before or before[name] != fp:
            added.append(name)
    return sorted(added)


def refs_line(names):
    """Return C2 receipt line, or None for an empty list."""
    if not names:
        return None
    sanitized = []
    for n in names:
        sanitized.append(n.replace("\n", "_").replace("\r", "_").replace(",", "_"))
    return "REFS: {} saved ({})".format(len(names), ", ".join(sanitized))
