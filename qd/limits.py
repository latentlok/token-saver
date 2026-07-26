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


class Progress:
    """Write a live progress snapshot to disk as the executor streams.

    Usable as the ``on_line`` callback of :func:`qd.invoke.run_executor`.
    Unlike :class:`BurnLimit` it always returns ``None`` — it observes,
    never stops a run.

    The snapshot path is ``<cwd>/.qwen-delegate/progress.json``.

    Parameters
    ----------
    cwd : str | Path
        Working directory that contains (or will contain) the
        ``.qwen-delegate`` folder.
    session_id : str | None
        Optional session identifier stored in the snapshot.
    """

    def __init__(self, cwd, session_id=None):
        self._cwd = cwd
        self._session_id = session_id
        self._records = 0
        self._input_tokens = 0

    def __call__(self, record):
        try:
            self._records += 1

            self._input_tokens += record_input_tokens(record)

            try:
                last_type = record.get("type") if isinstance(record, dict) else None
            except Exception:
                last_type = None

            try:
                from datetime import datetime, timezone

                updated = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            except Exception:
                updated = ""

            snapshot = {
                "session": self._session_id,
                "records": self._records,
                "input_tokens": self._input_tokens,
                "last_type": last_type,
                "updated": updated,
            }

            dir_path = os.path.join(self._cwd, ".qwen-delegate")
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

        except Exception:
            pass

        return None


def read_progress(cwd):
    """Return the latest progress snapshot, or ``None``.

    Never raises.

    Parameters
    ----------
    cwd : str | Path
        Working directory containing ``.qwen-delegate/progress.json``.

    Returns
    -------
    dict | None
        Parsed JSON object or ``None`` when the file is absent or
        unreadable.
    """
    try:
        import json

        path = os.path.join(cwd, ".qwen-delegate", "progress.json")
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None
