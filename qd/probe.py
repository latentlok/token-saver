#!/usr/bin/env python3
"""
Executor endpoint health probe -- one GET before the first executor call.

Field evidence, 2026-08-07: the machine-default executor endpoint (vLLM
behind an auth proxy) was hard down, answering HTTP 502 in ~8ms. Nothing
checked reachability before spawning workers, so two delegation runs burned
~380s and 11 api_errors each, wrote nothing, and both filed `gate_suspect`
-- the gate was byte-identical before and after a worker that never ran.
One sub-second GET turns that into an immediate, labelled refusal.

The verdict is deliberately one-sided. DOWN is only what is PROVABLE from a
single request: a connection error, a timeout, or HTTP >= 500. Anything a
server answers -- 2xx, 401, 403, 404 -- means it is up; whether the WORKER's
credentials satisfy it is the worker's business (the field endpoint answered
401 to bare requests while 502ing authenticated ones, so a probe that read
4xx as down would refuse a working configuration). An exception this module
does not recognise also reads as alive: the probe is a guard whose absence
was the status quo, and a guard that fails closed on its own bugs turns
every one of them into an outage.

No base URL discoverable on the profile means NO probe: configurations this
module cannot see (the builtin qwen-local, a cloud-default executor with no
settings_overlay) keep exactly their old behavior.

Verdicts are never cached: every call that probes pays its own GET, so a
just-restarted endpoint works on the very next call. Once-per-CALL dedup is
the caller's job (qd/server.py stamps chain links and batch items with
qd/engine.PROBED_ARG after probing each distinct endpoint once).
"""

import os
import time
import urllib.error
import urllib.request

# Worst-case delay a probe may add to a run. Small against the cost it
# guards (a dead endpoint burned ~90s per attempt in the field) and large
# against a healthy answer (the field 502 arrived in ~8ms).
PROBE_TIMEOUT = 3.0


def base_url(profile):
    """(base URL, auth env key) from the profile's settings overlay.

    (None, None) when no URL is discoverable -- the caller must then skip
    the probe entirely. The walk mirrors how qwen-code consumes the overlay
    (every provider list under modelProviders; see qd/invoke.py's readers of
    ~/.qwen/settings.json): the first entry carrying a baseUrl wins, and its
    own `envKey` names the bearer-token variable (the machine-file shape the
    vLLM cutover round pinned: baseUrl -> endpoint, `envKey: VLLM_TOKEN`).
    """
    overlay = (profile or {}).get("settings_overlay") or {}
    providers = overlay.get("modelProviders") or {}
    if not isinstance(providers, dict):
        return None, None
    for entries in providers.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("baseUrl"):
                return str(entry["baseUrl"]), entry.get("envKey")
    return None, None


def down(profile, timeout=PROBE_TIMEOUT):
    """None when the endpoint is reachable or invisible to the probe; else
    {"endpoint", "url", "signal", "ms"} describing the provable failure.

    GET <baseUrl>/models: the OpenAI-compatible liveness route every target
    here serves (self-hosted vLLM and a hosted key are the same thing to
    this plugin -- an argv template plus a base URL), and the one the vLLM
    cutover round already used by hand to verify the endpoint.
    """
    url, env_key = base_url(profile)
    if not url:
        return None
    probe_url = url.rstrip("/") + "/models"
    headers = {}
    token = None
    if env_key:
        # profile["env"] first: the worker runs under os.environ updated
        # with it (qd/invoke.py), so the profile value is what the worker
        # would actually send.
        token = (profile.get("env") or {}).get(env_key) \
            or os.environ.get(env_key)
    if token:
        headers["Authorization"] = "Bearer %s" % token
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(probe_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        return None                          # 2xx: alive
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        if code < 500:
            return None                      # it answered: alive
        signal = "HTTP %d" % code
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, TimeoutError):
            signal = "nothing within %dms (timed out)" % int(timeout * 1000)
        else:
            signal = "no connection (%s)" % reason
    except OSError:
        # A read that stalls after connect raises socket.timeout (an OSError
        # alias of TimeoutError since 3.10) rather than URLError.
        signal = "nothing within %dms (timed out)" % int(timeout * 1000)
    except Exception:
        return None                          # see module docstring: fail open
    ms = int((time.monotonic() - t0) * 1000)
    return {"endpoint": profile.get("endpoint") or profile.get("name")
            or "executor", "url": probe_url, "signal": signal, "ms": ms}


def refusal(profile, timeout=PROBE_TIMEOUT):
    """The EXECUTOR UNREACHABLE refusal text, or None to proceed.

    Same machinery as every other precondition refusal (status `refused`,
    the GATE UNUSABLE precedent): a named marker, what was observed, and
    what to do -- never a new status.
    """
    d = down(profile, timeout)
    if not d:
        return None
    return (
        "EXECUTOR UNREACHABLE: %s -- GET %s answered %s in %dms; nothing "
        "was run. A dead endpoint burns every attempt's whole timeout as "
        "API errors and files the outage as the worker's failure (measured "
        "2026-08-07: two runs, ~380s, 0 tokens, both gate_suspect). Bring "
        "the endpoint up, or name a live one with `executor`, then resubmit."
        % (d["endpoint"], d["url"], d["signal"], d["ms"])
    )
