#!/usr/bin/env python3
"""One live server per machine. Frozen by specs/lifecycle_spec.py.

A0d, second half. The first half shipped: `doctor` counts running servers, names
their pids and prints the exact `kill`. This is the part that stops the problem
happening rather than reporting it afterwards.

**The failure.** Claude Code respawns the MCP server on reload. The old process
does not always die, so two servers end up on one machine sharing the endpoint
semaphore, the repo locks and the run log -- and the locks are per-PROCESS, so
two servers do not queue behind each other. They double the endpoint's real
concurrency while each believes it is respecting `parallel_max`. Worse, an OLD
build keeps serving: the version a caller sees is whichever process won the
socket, which is how a fixed bug appears to come back.

**Why a file and not a socket.** The server is stdio -- there is no port to bind
and therefore no accidental mutual exclusion to lean on. A pid file is the
cheapest thing that survives the process, and staleness is answerable
(`os.kill(pid, 0)`), which is what makes it safe to act on.

**It clears the DEAD and reports the LIVE. It does not kill.** An earlier draft
SIGTERMed a live predecessor, and `specs/serialize_spec.py` caught it: two
servers on one machine is a SUPPORTED configuration, and that spec exists to
prove the repo lock and endpoint slot hold ACROSS processes. Killing one would
have broken a real guarantee to fix an accident.

So the scope is what A0d actually says -- retire a **stale** predecessor. A live
one is reported and left alone; `doctor` already prints the exact `kill` for a
human who wants it. That still removes most of the harm, because the recorded
version is what lets anyone see that an OLD BUILD is the one serving.
"""

import json
import os
import time


def record_path(base):
    return os.path.join(base, "server.json")


def read(base):
    """The recorded predecessor, or None. A corrupt record is treated as absent
    -- a half-written file must not be able to stop a server from starting."""
    try:
        with open(record_path(base)) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("pid") else None


def write(base, pid, version):
    """Claim the machine. Written AFTER the predecessor is dealt with, so a
    crash midway leaves the old record rather than a claim nobody honours."""
    os.makedirs(base, exist_ok=True)
    tmp = record_path(base) + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"pid": pid, "version": version, "started": time.time()}, f)
    os.replace(tmp, record_path(base))      # atomic: no torn record to read


def alive(pid):
    """Signal 0 asks the kernel without delivering anything.

    EPERM means the process EXISTS and belongs to somebody else -- alive, and
    emphatically not ours to kill.

    **Non-positive pids are rejected before they reach os.kill, and this is a
    safety rule rather than input validation.** `os.kill(0, sig)` signals the
    whole PROCESS GROUP -- which includes the client that spawned this server --
    and pid 1 is init. A corrupt record holding `0` would otherwise have this
    process SIGTERM everything around it while believing it was retiring one
    predecessor.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


def supersede(base, pid, version):
    """Record this process as the live server, and say what it replaced.

    Returns one of:
        "none"   no predecessor recorded
        "stale"  recorded but already dead -- the ordinary restart, and by far
                 the common case
        "self"   the record is this process; nothing to do
        "live"   another server IS running. Reported, never signalled.

    Nothing is killed. See the module docstring: two servers on one machine is
    a supported configuration that specs/serialize_spec.py exists to prove, so
    a process that assumed otherwise would break a real guarantee to tidy up an
    accident.
    """
    prev = read(base)
    outcome = "none"
    if prev:
        pid_prev = prev.get("pid")
        if pid_prev == pid:
            outcome = "self"
        elif alive(pid_prev):
            outcome = "live"
        else:
            outcome = "stale"
    write(base, pid, version)
    return outcome
