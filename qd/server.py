"""Threaded MCP dispatch (M3). Crane-shaped wire protocol; concurrency added.

One reader thread parses stdin and answers lifecycle requests inline;
each tools/call runs on its own worker thread. Three locks make that safe:

  - a global write lock: one complete JSON line per response, never torn;
  - per-repo locks: in-tree delegations into the same repo serialize
    ("one actor per tree", enforced structurally, worktree runs skip it).
    Machine-wide, same two-layer shape as the endpoint slot: the in-process
    half alone held only within one MCP server process, which was masked
    while every session queued on the single-slot local endpoint and opens
    the moment two sessions reach one repo through different endpoints;
  - per-endpoint semaphores: every executor-invoking call (delegations AND
    queries) holds its endpoint's slot for the whole handler, so on a
    single-slot local endpoint two sessions' turns never interleave and the
    KV cache survives by construction.

Locks wrap the whole handler, not just the subprocess -- a deliberate
simplification (bookkeeping is milliseconds against minutes of inference)
pinned in specs/dispatch_spec.py, which freezes all behavior here.

U5.2 makes qwen_delegate ASYNCHRONOUS: the tool call SUBMITS a run and answers
in milliseconds with a run id and the path its receipt will land at; the
delegation itself continues on a daemon thread and files the receipt when it
finishes. Two consequences are load-bearing here:

  - a submit takes no locks, so it never blocks on a busy GPU. The endpoint
    and repo guards are acquired INSIDE the background thread, which makes a
    submit an ENQUEUE: the queue is real, it just no longer runs down the
    caller's clock. Handlers that guard themselves this way are marked with
    @self_guarded so _run_call knows not to guard them twice.
  - daemon threads die with the process, so an MCP server whose session ends
    takes its in-flight runs with it. That is the staleness rule behind
    qd.runlog.runs_in_flight: a `running` record whose pid is gone is a run
    that died, not one still going.

Hand-written by the manager, not delegated: races are where green gates
under-prove. At M6 cutover the root server.py becomes a two-line shim
importing main() from here; .mcp.json never changes.
"""

import hashlib
import json
import os
import sys
import threading
import time

from qd import profiles
from qd import runlog

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "qwen-delegate", "version": "0.5.1"}
DRAIN_SECONDS = 10.0
SLOT_POLL_SECONDS = 0.5

# Where a submitted run's receipt lands, under runlog_dir()'s self-ignoring
# .gitignore -- a receipt file is exactly the kind of new file the delegation
# guards would otherwise attribute to the worker.
RECEIPTS_DIR = "receipts"
BATCH_SEP = "\n\n=== batch item ===\n"
CHAIN_SEP = "\n\n"

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
    """One actor per tree, machine-wide: a one-slot EndpointGuard keyed on
    the repo's realpath.

    A plain threading.Lock held only within this process -- which was masked
    while every session queued on the single-slot local endpoint, and stops
    holding the moment two sessions reach one repo through different
    endpoints (a second executor profile, or parallel dispatch). The name
    hashes the realpath because FileSlots flattens path separators, and
    "/a/b-c" colliding with "/a/b_c" would serialize strangers.
    """
    key = os.path.realpath(cwd or "")
    with _repo_locks_guard:
        guard = _repo_locks.get(key)
        if guard is None:
            name = (f"repo-{os.path.basename(key) or 'root'}-"
                    f"{hashlib.sha256(key.encode()).hexdigest()[:8]}")
            guard = EndpointGuard(name, 1)
            _repo_locks[key] = guard
        return guard


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
    (the scarce hardware), then the repo (cheap, uncontended across repos).

    The worktree decision goes through engine.worktree_mode -- the one
    resolver -- because a project config default the lock path did not
    consult would skip the repo lock for a run the engine then keeps
    in-tree."""
    from qd import engine
    guards = []
    if name in ("qwen_delegate", "qwen_query", "qwen_investigate"):
        guards.append(_endpoint_sem(args.get("cwd"), args.get("executor")))
    if name == "qwen_delegate" and engine.worktree_mode(args) == "off":
        guards.append(_repo_lock(args.get("cwd")))
    return guards


def self_guarded(fn):
    """Mark a handler as taking its own locks.

    A property of the HANDLER, not of the tool name: qwen_delegate submits and
    returns in milliseconds (U5.2), so guarding the submit would queue the
    caller behind a busy GPU for a call that runs nothing. Anything else
    registered under that name -- a test double, a future synchronous tool --
    keeps the guards _run_call has always taken for it.
    """
    fn.self_guarded = True
    return fn


def _run_call(rid, name, args, handler):
    """Worker-thread body: acquire guards, run, respond, release."""
    guards = []
    try:
        if not getattr(handler, "self_guarded", False):
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


def _announce(on_partial, text):
    """Hand the work-so-far to a progress sink, best-effort.

    Always guarded: the sink is a file write for a caller who is not there
    yet, and a full disk must not be able to fail a delegation that already
    produced its result.
    """
    if on_partial is None:
        return
    try:
        on_partial(text)
    except Exception as e:
        log(f"progress write failed: {e!r}")


def _joined(results, sep):
    """The receipts that have landed so far, in submission order."""
    return sep.join(r for r in results if r is not None)


def run_batch(items, handler, on_partial=None):
    """Fan N delegation items across worker threads IN ONE CALL (probe 5:
    the client serializes multi-call dispatch, so this is the primary fan-out
    path). Each item holds its own endpoint slot + (for in-tree) repo lock via
    the same _guards_for machinery, so real concurrency is capped by the
    endpoint, not by the batch size. Returns the per-item receipts joined,
    order preserved. A worktree='auto' item isolates itself; an item that
    raises becomes an error receipt in its slot, never sinking the batch.
    `on_partial` (U5.2) receives the receipts landed so far each time one
    lands, so a submitted batch can be read while it is still running."""
    results = [None] * len(items)
    progress_lock = threading.Lock()

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
        # Under the parallel branch two items can finish at once and each
        # rewrites the WHOLE partial text -- serialized, or one write lands
        # inside the other.
        with progress_lock:
            _announce(on_partial, _joined(results, BATCH_SEP))

    # Every item runs on its own thread and blocks on ITS OWN endpoint's
    # guard. There is deliberately no serial/parallel branch here any more.
    #
    # There used to be one, and it read the dispatch policy of items[0] and
    # applied it to the whole batch. When every item shares an endpoint that
    # was merely redundant -- the guards already enforce capacity, so a
    # one-slot endpoint serializes its items whether or not the loop does.
    # When items target DIFFERENT endpoints it was wrong: an item on a
    # one-slot endpoint at the head of the batch pinned every item behind it,
    # and a batch that happened to start on a wide endpoint let a narrow one
    # run over capacity. Capacity belongs to an endpoint, so only the endpoint
    # may decide it.
    #
    # The two things the serial branch was protecting are both still true:
    #   - Worktrees are NOT cut early. A worktree is acquired inside
    #     engine.run, which a thread only reaches after its guard is taken, so
    #     a one-slot endpoint still cuts one tree at a time.
    #   - Receipt order is submission order regardless: results are written by
    #     index and joined in order, not in completion order.
    # Guards are acquired endpoint-then-repo by every thread, so the fixed
    # ordering keeps a mixed batch from deadlocking on them.
    threads = [threading.Thread(target=one, args=(i, a), daemon=True)
               for i, a in enumerate(items)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        # The shared pre-flight verdict lives exactly as long as the batch
        # that shares it. The tree can move between calls, and a verdict that
        # outlives its commit is the same class of stale state as the sidecar
        # that claimed "fresh" -- so it is dropped on every exit path.
        from qd import engine
        engine._preflight_forget()
    return BATCH_SEP.join(
        r if r is not None else "STATUS: error\n(no result)" for r in results)


def _receipt_status(text):
    """The status a receipt reports. Receipts open with `STATUS: <status>`
    (every producer here does, refusals included); anything else is a receipt
    this server did not write, which a chain must treat as a failure rather
    than read past."""
    first = (text or "").splitlines()[0] if text else ""
    if first.startswith("STATUS:"):
        return first[len("STATUS:"):].strip() or "unknown"
    return "unknown"


def run_chain(items, handler, on_partial=None):
    """Run delegation items in submission order, halting on the first red one.

    A chain is the dependent counterpart of `batch`: link 2 builds on link 1's
    tree, so it is ALWAYS serial (never the dispatch-policy branch batch takes)
    and the first link that does not come back green stops the rest. Continuing
    past a failure is what makes an eight-step chain expensive -- seven more
    delegations against a tree whose premise already broke, each producing a
    receipt the caller still has to read.

    Guards are taken per link through the same _guards_for machinery batch
    uses, and released before the next link starts: a chain holds one endpoint
    slot at a time, not one for its whole length.

    `on_partial` (U5.2) receives the links finished so far each time one
    finishes: a submitted chain is the longest thing this server runs, and
    waiting for link 8 to read link 1 is what made it feel like a black box.
    """
    from qd.engine import CHAIN_ARG
    n = len(items)
    out = []
    halted_at = halted_status = None
    for k, item in enumerate(items, 1):
        if halted_at is not None:
            out.append(f"=== chain link {k}/{n}: skipped ===\n"
                       f"SKIPPED (chain halted at link {halted_at}: "
                       f"{halted_status})")
            _announce(on_partial, CHAIN_SEP.join(out))
            continue
        args = dict(item or {})
        args[CHAIN_ARG] = {"pos": k, "of": n}
        acquired = []
        try:
            for g in _guards_for("qwen_delegate", args):
                g.acquire()
                acquired.append(g)
            try:
                text = handler(args)
            except Exception as e:
                text = f"STATUS: error\n{e!r}"
        except profiles.ProfileError as e:
            text = f"STATUS: error\n{e}"
        finally:
            for g in reversed(acquired):
                g.release()
        status = _receipt_status(text)
        out.append(f"=== chain link {k}/{n}: {status} ===\n{text}")
        _announce(on_partial, CHAIN_SEP.join(out))
        # A preflight-passed pass still moved the tree the next link builds on,
        # so it is green here for the same reason the worktree commit treats it
        # as green (U3.2, decision 4).
        if status not in ("success", "success_but_preflight_passed"):
            halted_at, halted_status = k, status
    return CHAIN_SEP.join(out)


def _shape_refusal(args):
    """Argument-shape refusals: answerable from the call alone, so they are
    answered in the response and nothing is ever spawned."""
    if args.get("chain") and args.get("batch"):
        # Refused by name before anything runs: the two mean opposite things
        # about ordering and failure, and picking one for the caller would run
        # N delegations under a policy they did not ask for.
        return ("STATUS: error\n`chain` and `batch` are mutually exclusive -- "
                "`chain` runs items in order and stops at the first failure, "
                "`batch` runs independent items and reports each. Nothing was "
                "run. Send one of them.")
    return None


def run_delegate_batch(args, on_partial=None):
    """Run a delegation to completion: args['chain'] runs dependent,
    args['batch'] fans out, else one delegation. BLOCKING -- this is the body
    submit_delegate hands to its background thread, and what `wait: true`
    calls directly."""
    from qd import engine
    refusal = _shape_refusal(args)
    if refusal:
        return refusal
    # U6: a `chain: true` playbook becomes `chain` items here; anything else
    # comes back untouched (the single path resolves its own brief inside the
    # run -- that keeps the amendment on exactly one code path). Idempotent,
    # so the submit path having already expanded costs nothing.
    args, refusal = engine.expand_playbook(args)
    if refusal:
        return engine.refusal_receipt(refusal)
    if args.get("chain"):
        return run_chain(_inherit(args, args["chain"]), engine.run,
                         on_partial=on_partial)
    if args.get("batch"):
        return run_batch(_inherit(args, args["batch"]), engine.run,
                         on_partial=on_partial)
    return engine.run(args)


# Call-level fields an item inherits when it does not state its own. `cwd` is
# the one that mattered: it is a REQUIRED top-level parameter, so every caller
# passes it at the call, and the skill's own example says items carry "the same
# fields per item" -- which reads as "per-item fields are the ones that VARY".
# Items without it reached engine.run() and came back as a bare
# `KeyError('cwd')` for the whole receipt, with no field name and no hint that
# the batch items were at fault. The rest are here for the same reason they are
# call-level at all: they describe the RUN, not the item.
_INHERITED = ("cwd", "executor", "worktree", "trust", "approval_mode",
              "timeout_sec", "verify_timeout_sec", "max_iterations",
              "on_compaction", "shell_allow", "mcp_allow")


def _inherit(args, items):
    """Items with call-level defaults filled in. An item's own value always
    wins; only keys the item is SILENT about are inherited.

    `_batch_size` rides along so each item's receipt can state what the
    fan-out actually did (the DISPATCH line) -- the engine resolves the
    capacity but only the caller knows how many items were sent.
    """
    out = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        merged = dict(item)
        for k in _INHERITED:
            if merged.get(k) is None and args.get(k) is not None:
                merged[k] = args[k]
        merged["_batch_size"] = len(items)
        out.append(merged)
    return out


def _held(guards, fn):
    """Run fn holding `guards`, releasing in reverse order whatever it got."""
    acquired = []
    try:
        for g in guards:
            g.acquire()
            acquired.append(g)
        return fn()
    finally:
        for g in reversed(acquired):
            g.release()


def _submit_cwd(args):
    """The tree a submitted run reports into.

    The call's own cwd, else the first item's: chain and batch carry theirs per
    item, and the receipt has to land SOMEWHERE a caller can name from the
    submit response.
    """
    cwd = args.get("cwd")
    if not cwd:
        for item in (args.get("chain") or args.get("batch") or []):
            if isinstance(item, dict) and item.get("cwd"):
                cwd = item["cwd"]
                break
    return cwd or os.getcwd()


def receipt_paths(cwd, run_id):
    """(final, partial) receipt paths for a run; the directory is created.

    Both live under runlog_dir()'s self-ignoring .gitignore -- a receipt file
    written into the tree the next delegation runs in would otherwise read as
    the worker's own new file, and would trip the dirty-tree preconditions.
    """
    d = os.path.join(runlog.runlog_dir(cwd), RECEIPTS_DIR)
    os.makedirs(d, exist_ok=True)
    return (os.path.join(d, f"{run_id}.md"),
            os.path.join(d, f"{run_id}.partial.md"))


def _write_atomic(path, text):
    """Write via temp + rename.

    The WATCH loop the submit hands back keys on the receipt EXISTING, so the
    file must not exist until it is complete: a plain open() publishes an empty
    file the instant it starts, and the caller's `cat` answers nothing.
    """
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def _submit_text(run_id, receipt, partial, heartbeat):
    """What a submit answers with. Four fixed lines -- what ran, where the
    receipt lands, where the pulse is, and the exact shell to wait on it --
    plus the partial path when the run produces per-link receipts."""
    lines = [
        "STATUS: submitted",
        f"RUN: {run_id}",
        f"RECEIPT: {receipt} — lands on completion",
    ]
    if partial:
        lines.append(f"PARTIAL: {partial} — each link's receipt as it lands")
    lines.append(f"HEARTBEAT: {heartbeat}")
    lines.append(f"WATCH: until [ -f {receipt} ]; do sleep 5; done; "
                 f"cat {receipt}")
    return "\n".join(lines)


def _deliver(run_id, receipt, partial, work):
    """Background body of a submitted delegation (U5.2).

    EVERY path ends in a receipt file, including the ones that raise: the
    caller was handed a WATCH loop that polls for it, so a run that dies
    without filing anything polls forever.
    """
    try:
        text = work()
    except profiles.ProfileError as e:
        text = f"STATUS: error\n{e}"
    except Exception as e:
        log(f"error in submitted run {run_id}: {e!r}")
        text = f"STATUS: error\n{e!r}"
    try:
        _write_atomic(receipt, text or "STATUS: error\n(no result)")
    except Exception as e:
        log(f"receipt for {run_id} could not be written: {e!r}")
        return
    # The partial holds the same text mid-flight; once the receipt exists it
    # can only mislead a reader into `cat`ing the older copy.
    try:
        os.remove(partial)
    except OSError:
        pass


@self_guarded
def submit_delegate(args):
    """The qwen_delegate entry: SUBMIT the run, answer now (U5.2).

    A delegation runs for minutes. Blocking the tool call for them bought the
    caller nothing it could not get from a file, and cost it everything it
    could have done meanwhile -- so the response is a run id and a path, and
    the receipt lands on disk when the work is done.

    Answered synchronously (never as a file): the argument-shape refusals and
    engine.precheck's -- nobody is polling for a run that was never spawned,
    and a refusal read minutes later has cost exactly the wait it exists to
    save. Everything from the pre-flight gate onward is part of the RUN and
    lands in the receipt, `GATE UNUSABLE` included: that one can cost the whole
    verify budget, which is not a precondition, it is work.

    `wait: true` restores the old blocking call, byte-for-byte.
    """
    from qd import engine
    refusal = _shape_refusal(args)
    if refusal:
        return refusal

    # U6: expand a `chain: true` playbook BEFORE routing -- the submission
    # must know it is a chain (per-link guards, partial receipts, no single
    # precheck). Refusals here are argument-shaped: answered now, nothing
    # spawned, no receipt file for a run that never existed.
    args, refusal = engine.expand_playbook(args)
    if refusal:
        return engine.refusal_receipt(refusal)

    items = args.get("chain") or args.get("batch")
    single = not items

    if args.get("wait"):
        # The pre-U5.2 call, kept for callers who genuinely have nothing else
        # to do: the response IS the receipt. Chain and batch take their guards
        # per link, so only the lone delegation needs them here.
        if single:
            return _held(_guards_for("qwen_delegate", args),
                         lambda: run_delegate_batch(args))
        return run_delegate_batch(args)

    # Resolved here, acquired in the thread: resolving names a profile (an
    # argument error worth answering now), acquiring waits for a GPU.
    guards = _guards_for("qwen_delegate", args) if single else []

    run_args = dict(args)
    if single:
        pre = engine.precheck(args)
        if pre.get("refusal"):
            return engine.refusal_receipt(pre["refusal"])
        run_args[engine.PRECHECK_ARG] = pre

    cwd = _submit_cwd(args)
    run_id = runlog.new_run_id()
    receipt, partial = receipt_paths(cwd, run_id)
    heartbeat = os.path.join(cwd, runlog.RUNLOG_DIR, "progress.json")

    if single:
        run_args[engine.RUN_ID_ARG] = run_id

        def work():
            return _held(guards, lambda: engine.run(run_args))
    else:
        # One run id for the whole chain/batch: it is ONE submission, and every
        # link's completion record carries the id so the pair closes.
        key = "chain" if args.get("chain") else "batch"
        run_args[key] = [dict(i or {}, **{engine.RUN_ID_ARG: run_id})
                         for i in items]

        def work():
            return run_delegate_batch(
                run_args, on_partial=lambda text: _write_atomic(partial, text))

    # The open half of the pair (C5): status "running" plus the pid that owns
    # it. Nothing rewrites this line -- the completion record closes it
    # logically, and a pid that is gone says the run died with its session.
    # It lives in the SUBMIT's cwd; a batch whose items span repos logs its
    # completions elsewhere and so leaves this marker open until its process
    # ends. Accepted: the marker answers "did my run die with the session",
    # and for a run that outlived its session the answer is still correct.
    runlog.write_runlog(cwd, {"tool": "qwen_delegate", "status": "running",
                              "run_id": run_id, "pid": os.getpid(),
                              "cwd": cwd, "ts": runlog.now_iso()})
    threading.Thread(target=_deliver,
                     args=(run_id, receipt, partial, work),
                     daemon=True).start()
    return _submit_text(run_id, receipt, partial if items else None, heartbeat)


def _default_tools():
    from qd import queries
    return {"qwen_delegate": submit_delegate,
            # Queries stay SYNCHRONOUS: the answer is the deliverable and it
            # arrives in a minute or two, so a file to poll would be pure
            # ceremony between a question and its answer.
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
