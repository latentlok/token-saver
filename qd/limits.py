#!/usr/bin/env python3
"""
Live burn-budget guard for streamed executor runs.

A burn budget measures the SUM of per-call input tokens across the run, not the
maximum. Each ``assistant`` record's ``usage.input_tokens`` is the context
window sent for that single API call. Summing them gives total prefill work --
the figure behind "that run burned 19M tokens", and the number that grows
quadratically in agentic loops (every turn re-sends full history).

Reading max instead of sum conflates *total prefill* with *peak context*. The
latter is what ``qd.invoke.peak_context`` already reports. Picking max here
would let a 218-call run averaging 87k context slip under a 3M budget entirely,
leaving BURN looking like a live guard while it is silent.

Public surface
--------------
- :func:`record_input_tokens` -- extract input-token count from one record.
- :class:`BurnLimit` -- callable used as ``on_line`` for
  :func:`qd.invoke.run_executor`.
- :class:`Progress` / :func:`read_progress` -- the C11 heartbeat sidecar.
- :func:`compose` -- one ``on_line`` over several observers.
"""

import os


def record_input_tokens(record):
    """Return the input tokens a single streamed record represents.

    For an ``assistant`` record this is the per-call context size. For every
    other type (including ``result``) it is ``0`` -- a result record carries
    run-total stats that would double-count the assistant records it
    summarises.

    Any missing, malformed, or non-numeric data returns ``0``. Never raises.
    """
    try:
        if (record is None
                or not isinstance(record, dict)
                or record.get("type") != "assistant"):
            return 0

        tokens = record["message"]["usage"]["input_tokens"]
        return int(tokens)
    except Exception:
        return 0


class BurnLimit:
    """Cumulative input-token budget, callable as ``on_line``.

    Each call adds the record's input tokens to :attr:`total`. When
    :attr:`total` reaches (or exceeds) the budget, every subsequent call
    returns a reason string containing the running total and the budget, both
    formatted with thousands separators.

    A budget of ``None`` or ``0`` disables the limit (always returns ``None``),
    but :attr:`total` still accumulates.

    Parameters
    ----------
    budget: int | None
        Cumulative input-token cap. Pass ``None`` or ``0`` for "no limit".
    """

    def __init__(self, budget):
        self._budget = budget if budget else None
        self.total = 0

    def __call__(self, record):
        self.total += record_input_tokens(record)

        if self._budget is None:
            return None

        if self.total >= self._budget:
            return (
                f"Burn limit exceeded: "
                f"{self.total:,} input tokens against {self._budget:,} budget"
            )


def compose(*callbacks):
    """Fan one streamed record out to several ``on_line`` observers.

    :func:`qd.invoke.run_executor` accepts exactly ONE ``on_line``, and the two
    observers that exist want different things from the same stream: the burn
    limit must be able to stop the run, the heartbeat only watches. Every
    observer sees every record and the FIRST non-``None`` reason is returned,
    so a heartbeat can never swallow a stop and a stop can never cost the
    heartbeat its final snapshot.

    ``None`` callbacks are skipped. When nothing is left to watch this returns
    ``None`` rather than an inert callable: ``run_executor`` switches the
    executor to stream-json for ANY on_line, and the streaming adapter emits no
    ``stats``, so an empty composition would silently cost a run its tool
    counts and its bySource token split for the sake of observing nothing.
    """
    watchers = [c for c in callbacks if c is not None]
    if not watchers:
        return None

    def on_line(record):
        reason = None
        for w in watchers:
            try:
                r = w(record)
            except Exception:
                # The stream pump catches per-on_line, not per-observer: one
                # raising observer would take every observer behind it down
                # with it -- including the burn limit, which would then read as
                # a guard that found nothing rather than one that never ran.
                r = None
            if r and reason is None:
                reason = r
        return reason

    return on_line


class Progress:
    """Write a live progress snapshot to disk as the executor streams.

    Usable as the ``on_line`` callback of :func:`qd.invoke.run_executor`.
    Unlike :class:`BurnLimit` it always returns ``None`` — it observes,
    never stops a run.

    The snapshot path is ``<cwd>/.delegation/progress.json``.

    :attr:`attempt` and :attr:`state` are set by the caller (C11): a poller
    asking "is it hung?" cannot answer from a record count alone -- attempt 3
    of 3 and a wedged attempt 1 look identical without them, and a run that
    ended leaves its last running snapshot on disk forever unless something
    marks it done.

    Parameters
    ----------
    cwd : str | Path
        Working directory that contains (or will contain) the
        ``.delegation`` folder.
    session_id : str | None
        Optional session identifier stored in the snapshot. Public and
        settable: a cold run only learns its session from the first reply, and
        a sidecar whose ``session`` is null forever cannot be correlated with
        anything.
    """

    def __init__(self, cwd, session_id=None, run_id=None):
        self._cwd = cwd
        self.session_id = session_id
        # The run id, stamped at construction -- BEFORE the first token.
        # `session` is not an identity for the window that matters: a cold run
        # only learns its session from the first reply, so the heartbeat read
        # `"session": null` for the entire live period and carried no
        # identifier of ANY kind during the only window it is the sole signal.
        # Worse, the documented "is it hung?" check then read a PREVIOUS run's
        # `"state": "done"` and reported success for work that had not started.
        # A consumer must be able to assert progress["run"] == <my run id>
        # before believing a single field.
        self.run_id = run_id
        self._records = 0
        self._input_tokens = 0
        self._last_type = None
        self.attempt = 0
        self.state = "starting"      # not "running": nothing has run yet

    def __call__(self, record):
        try:
            # First token: the run is no longer merely submitted. The caller
            # owns `state` for every OTHER transition (attempt/done), but this
            # one is only observable from the stream.
            if self.state == "starting":
                self.state = "running"
            self._records += 1

            self._input_tokens += record_input_tokens(record)

            try:
                self._last_type = (record.get("type")
                                   if isinstance(record, dict) else None)
            except Exception:
                self._last_type = None

            self._write()

        except Exception:
            pass

        return None

    def finish(self, state="done"):
        """Write the terminal snapshot, so a poller can stop polling.

        Never raises: this runs on the way out of a delegation that has already
        produced its result, and a failed heartbeat write must not be able to
        take that result with it.
        """
        try:
            self.state = state
            self._write()
        except Exception:
            pass

    def _write(self):
        try:
            from datetime import datetime, timezone

            updated = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        except Exception:
            updated = ""

        snapshot = {
            "run": self.run_id,
            "session": self.session_id,
            "records": self._records,
            "input_tokens": self._input_tokens,
            "last_type": self._last_type,
            "updated": updated,
            "attempt": self.attempt,
            "state": self.state,
        }

        dir_path = os.path.join(self._cwd, ".delegation")
        os.makedirs(dir_path, exist_ok=True)

        import json
        import tempfile

        fd = None
        tmp_path = None
        try:
            fd = tempfile.NamedTemporaryFile(
                mode="w",
                dir=dir_path,
                suffix=".tmp",
                delete=False,
            )
            tmp_path = fd.name
            json.dump(snapshot, fd)
            fd.close()
            fd = None
            target = os.path.join(dir_path, "progress.json")
            os.replace(tmp_path, target)
            tmp_path = None
        except Exception:
            if fd is not None:
                try:
                    fd.close()
                except Exception:
                    pass
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass


def read_progress(cwd):
    """Return the latest progress snapshot, or ``None``.

    Never raises.

    Parameters
    ----------
    cwd : str | Path
        Working directory containing ``.delegation/progress.json``.

    Returns
    -------
    dict | None
        Parsed JSON object or ``None`` when the file is absent or
        unreadable.
    """
    try:
        import json

        path = os.path.join(cwd, ".delegation", "progress.json")
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# --- Timeout fitting (v0.6) -------------------------------------------------
#
# This replaces a REGRESSION FORMULA THE SKILL ASKED CLAUDE TO APPLY BY HAND:
#
#     seconds ~ turns*avg_context/10,882 + output_tokens/70,
#     avg_context ~ (22k + peak)/2 ... then set ~3x
#
# Four lines of arithmetic, resident in every session, re-derived per call by a
# model that cannot see the telemetry it needs. The server has all three inputs
# on every finished run and writes them to the runlog already, so it can do the
# arithmetic once and say the answer. A number the machine can compute is not a
# judgement call.

FIT_CTX_PER_SEC = 10_882.0     # fitted over 198 measured calls
FIT_OUT_PER_SEC = 70.0
FIT_BASE_CTX = 22_000          # cold-start context, before the task grows it
TIMEOUT_HEADROOM = 3.0         # p90 ran ~3x median; over-setting costs nothing
TIMEOUT_FLOOR = 900            # never advise BELOW the historical default
TIMEOUT_CEILING = 7200         # the engine's own clamp


def fitted_seconds(turns, peak_context, out_tokens):
    """Seconds this shape of run needs, from its own telemetry. None if the
    run reported nothing measurable (an error before the worker spoke)."""
    try:
        turns = int(turns or 0)
        peak = int(peak_context or 0)
        out = int(out_tokens or 0)
    except Exception:
        return None
    if turns <= 0 and out <= 0:
        return None
    avg_ctx = (FIT_BASE_CTX + peak) / 2.0
    return int(turns * avg_ctx / FIT_CTX_PER_SEC + out / FIT_OUT_PER_SEC)


def suggested_timeout(cwd, default=TIMEOUT_FLOOR):
    """A `timeout_sec` for THIS project, fitted from its own finished runs.

    Takes the worst observed requirement rather than the mean: the cost of
    over-setting is nothing (the kill is a ceiling, not a schedule) while the
    cost of under-setting is a large task killed mid-write, which is the exact
    failure the 900s default produced. Falls back to `default` for a project
    with no history -- a first run cannot be fitted and must not be guessed at.
    """
    from qd import runlog
    worst = 0
    for rec in runlog.completed_runs(cwd):
        tokens = rec.get("tokens") or {}
        need = fitted_seconds(rec.get("turns"),
                              rec.get("peak_context"),
                              tokens.get("completion"))
        if need:
            worst = max(worst, need)
    if not worst:
        return default
    return max(default, min(TIMEOUT_CEILING, int(worst * TIMEOUT_HEADROOM)))


def timeout_line(ctx):
    """A `TIMEOUT:` receipt line, or None when the budget was comfortable.

    Speaks only when it has something actionable: the run was killed, or it
    used enough of its budget that the next slightly larger task will be. A
    line on every receipt would be noise, and a noisy line stops being read.
    """
    budget = int(ctx.get("timeout") or 0)
    cum = ctx.get("cum") or {}
    tokens = cum.get("tokens") or {}
    used = int((cum.get("ms") or 0) / 1000)
    need = fitted_seconds(cum.get("turns"), ctx.get("peak"),
                          tokens.get("completion"))
    if not budget or not need:
        return None
    advise = max(TIMEOUT_FLOOR, min(TIMEOUT_CEILING,
                                    int(need * TIMEOUT_HEADROOM)))
    if ctx.get("timed_out"):
        # A killed run reports only what it streamed BEFORE the kill, so its
        # telemetry is truncated and `need` is a floor, never the requirement.
        # The one thing known exactly is that it needed more than the budget --
        # so the advice is anchored on the budget, and the fitted figure is
        # offered as evidence rather than as the answer.
        advise = max(TIMEOUT_FLOOR,
                     min(TIMEOUT_CEILING,
                         max(budget * 2, int(need * TIMEOUT_HEADROOM))))
        return (f"TIMEOUT: killed at {budget}s -- it needed more. "
                f"(It had done ~{need}s of measurable work when killed; that "
                f"is a floor, not the total, because a kill truncates the "
                f"telemetry.) Set timeout_sec={advise} and re-run. A kill "
                f"lands mid-write, so nothing here is a finished state.")
    if used and used >= 0.7 * budget:
        return (f"TIMEOUT: used {used}s of {budget}s. This shape fits ~{need}s; "
                f"a larger task will not. Set timeout_sec={advise}.")
    return None
