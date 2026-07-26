"""Threaded MCP dispatch (M3). Crane-shaped wire protocol; concurrency added.

One reader thread parses stdin and answers lifecycle requests inline;
each tools/call runs on its own worker thread. Three locks make that safe:

  - a global write lock: one complete JSON line per response, never torn;
  - per-repo locks: in-tree delegations into the same repo serialize
    ("one actor per tree", enforced structurally, worktree runs skip it);
  - per-endpoint semaphores: every executor-invoking call (delegations AND
    queries) holds its endpoint's slot for the whole handler, so on a
    single-slot local endpoint two sessions' turns never interleave and the
    KV cache survives by construction.

Locks wrap the whole handler, not just the subprocess -- a deliberate
simplification (bookkeeping is milliseconds against minutes of inference)
pinned in specs/dispatch_spec.py, which freezes all behavior here.

Hand-written by the manager, not delegated: races are where green gates
under-prove. At M6 cutover the root server.py becomes a two-line shim
importing main() from here; .mcp.json never changes.
"""

import json
import os
import sys
import threading
import time

from qd import profiles

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "qwen-delegate", "version": "0.4.1"}
DRAIN_SECONDS = 10.0
SLOT_POLL_SECONDS = 0.5

_write_lock = threading.Lock()
_repo_locks = {}
_repo_locks_guard = threading.Lock()
_endpoint_sems = {}
_endpoint_guard = threading.Lock()


def lock_dir():
    return os.environ.get("QWEN_DELEGATE_LOCKS") or os.path.expanduser(
        "~/.qwen-delegate/locks")


class FileSlots:
    """`slots` flock files under lock_dir(), held for the length of a call.

    The endpoint semaphore is per PROCESS, and every Claude session runs its
    own MCP server process -- so two sessions pointed at one local endpoint
    both saw a free slot and fired concurrently, which is exactly the
    parallelism the serial policy exists to prevent. These files are the
    machine-wide half of the same slot count.

    Degrades to a no-op if flock or the directory is unavailable (Windows, a
    read-only home): the in-process semaphore still holds, and a lock that
    cannot be taken must never fail a delegation.
    """

    def __init__(self, name, slots):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))
        self.paths = [os.path.join(lock_dir(), f"{safe}.{i}.lock")
                      for i in range(max(1, slots))]
        self._local = threading.local()

    def _stack(self):
        if not hasattr(self._local, "fds"):
            self._local.fds = []
        return self._local.fds

    def _try(self, path):
        import fcntl
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        return fd

    def acquire(self):
        try:
            os.makedirs(lock_dir(), exist_ok=True)
            while True:
                for path in self.paths:
                    fd = self._try(path)
                    if fd is not None:
                        self._stack().append(fd)
                        return
                # Every slot busy. Inference runs in minutes; polling at this
                # rate is free next to it, and avoids blocking on one slot
                # while a different one frees up.
                time.sleep(SLOT_POLL_SECONDS)
        except Exception as e:
            log(f"cross-process lock unavailable ({e!r}); in-process only")
            self._stack().append(None)

    def release(self):
        stack = self._stack()
        fd = stack.pop() if stack else None
        if fd is not None:
            try:
                os.close(fd)          # closing drops the flock
            except Exception:
                pass


class EndpointGuard:
    """Endpoint slot: in-process semaphore first, then the cross-process file
    slot. Both or neither -- a failure to take the second releases the first."""

    def __init__(self, name, slots):
        self.sem = threading.BoundedSemaphore(slots)
        self.files = FileSlots(name, slots)

    def acquire(self):
        self.sem.acquire()
        try:
            self.files.acquire()
        except BaseException:
            self.sem.release()
            raise

    def release(self):
        try:
            self.files.release()
        finally:
            self.sem.release()


def log(msg):
    sys.stderr.write(f"[qwen-delegate] {msg}\n")
    sys.stderr.flush()


def respond(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    line = json.dumps(msg) + "\n"
    with _write_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def _repo_lock(cwd):
    key = os.path.realpath(cwd or "")
    with _repo_locks_guard:
        return _repo_locks.setdefault(key, threading.Lock())


def _endpoint_sem(cwd, executor):
    """Resolve the call's profile and return its endpoint's semaphore.
    Raises ProfileError for unknown names -- the caller turns that into an
    error receipt, never a crash."""
    prof = profiles.resolve(cwd or ".", executor)
    cfg = prof["endpoint_cfg"]
    with _endpoint_guard:
        sem = _endpoint_sems.get(cfg["name"])
        if sem is None:
            sem = EndpointGuard(cfg["name"], cfg["parallel_max"])
            _endpoint_sems[cfg["name"]] = sem
    return sem


def _guards_for(name, args):
    """The locks a call must hold, in acquisition order: endpoint first
    (the scarce hardware), then the repo (cheap, uncontended across repos)."""
    guards = []
    if name in ("qwen_delegate", "qwen_query", "qwen_investigate"):
        guards.append(_endpoint_sem(args.get("cwd"), args.get("executor")))
    if name == "qwen_delegate" and (args.get("worktree") or "off") == "off":
        guards.append(_repo_lock(args.get("cwd")))
    return guards


def _run_call(rid, name, args, handler):
    """Worker-thread body: acquire guards, run, respond, release."""
    guards = []
    try:
        guards = _guards_for(name, args)
    except profiles.ProfileError as e:
        respond(rid, {"content": [{"type": "text",
                                   "text": f"STATUS: error\n{e}"}],
                      "isError": True})
        return
    except Exception as e:
        respond(rid, {"content": [{"type": "text",
                                   "text": f"STATUS: error\n{e!r}"}],
                      "isError": True})
        return
    acquired = []
    try:
        for g in guards:
            g.acquire()
            acquired.append(g)
        try:
            text = handler(args)
            respond(rid, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            log(f"error in {name}: {e!r}")
            respond(rid, {"content": [{"type": "text",
                                       "text": f"STATUS: error\n{e!r}"}],
                          "isError": True})
    finally:
        for g in reversed(acquired):
            g.release()


def run_batch(items, handler):
    """Fan N delegation items across worker threads IN ONE CALL (probe 5:
    the client serializes multi-call dispatch, so this is the primary fan-out
    path). Each item holds its own endpoint slot + (for in-tree) repo lock via
    the same _guards_for machinery, so real concurrency is capped by the
    endpoint, not by the batch size. Returns the per-item receipts joined,
    order preserved. A worktree='auto' item isolates itself; an item that
    raises becomes an error receipt in its slot, never sinking the batch."""
    results = [None] * len(items)

    def one(idx, args):
        acquired = []
        try:
            for g in _guards_for("qwen_delegate", args):
                g.acquire()
                acquired.append(g)
            try:
                results[idx] = handler(args)
            except Exception as e:
                results[idx] = f"STATUS: error\n{e!r}"
        except profiles.ProfileError as e:
            results[idx] = f"STATUS: error\n{e}"
        finally:
            for g in reversed(acquired):
                g.release()

    # Under the serial policy the items would queue on a one-slot endpoint
    # anyway; running them in order instead of racing for that slot keeps N
    # worktrees from being cut for work that executes one at a time, and makes
    # the receipt order the submitted order.
    serial = True
    try:
        if items:
            first = items[0] or {}
            serial = profiles.resolve(
                first.get("cwd") or ".",
                first.get("executor"))["dispatch"] == "serial"
    except Exception:
        serial = True

    if serial:
        for i, a in enumerate(items):
            one(i, a)
    else:
        threads = [threading.Thread(target=one, args=(i, a), daemon=True)
                   for i, a in enumerate(items)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    return "\n\n=== batch item ===\n".join(
        r if r is not None else "STATUS: error\n(no result)" for r in results)


def run_delegate_batch(args):
    """The qwen_delegate entry, batch-aware: args['batch'] fans out, else one."""
    from qd import engine
    if args.get("batch"):
        return run_batch(args["batch"], engine.run)
    return engine.run(args)


def _default_tools():
    from qd import queries
    return {"qwen_delegate": run_delegate_batch,
            "qwen_query": queries.run_query,
            "qwen_investigate": queries.run_investigate}


def _default_schemas():
    from qd import schemas
    return [schemas.TOOL, schemas.QUERY_TOOL]


def main(tools=None, schemas=None):
    tools = tools or _default_tools()
    schemas = schemas if schemas is not None else _default_schemas()
    workers = []
    log("dispatch starting (threaded)")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method")
        rid = req.get("id")

        if method == "initialize":
            respond(rid, {"protocolVersion": PROTOCOL_VERSION,
                          "capabilities": {"tools": {}},
                          "serverInfo": dict(SERVER_INFO)})
        elif method == "notifications/initialized":
            continue
        elif method == "ping":
            respond(rid, {})
        elif method == "tools/list":
            respond(rid, {"tools": list(schemas)})
        elif method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            handler = tools.get(name)
            if handler is None:
                respond(rid, error={"code": -32602,
                                    "message": f"unknown tool: {name}"})
                continue
            t = threading.Thread(target=_run_call,
                                 args=(rid, name, args, handler), daemon=True)
            t.start()
            workers.append(t)
        elif rid is not None:
            respond(rid, error={"code": -32601,
                                "message": f"unknown method: {method}"})

    # EOF: drain in-flight calls, bounded -- a hung executor must not make
    # the server unkillable, but normal work gets to finish and respond.
    import time
    deadline = time.monotonic() + DRAIN_SECONDS
    for t in workers:
        t.join(timeout=max(0.0, deadline - time.monotonic()))
    log("dispatch exiting")


if __name__ == "__main__":
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
