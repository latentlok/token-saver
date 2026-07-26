#!/usr/bin/env python3
"""
Executor profiles for qwen-delegate.

Resolves which Qwen Code invocation to use for delegation, based on a
four-level precedence chain: call arg > project config > machine file
default > builtin qwen-local. Each profile carries argv templates,
endpoint concurrency hints, and optional pricing metadata.
"""

import copy
import json
import os

QWEN_BIN = os.environ.get("QWEN_BIN", "qwen")


class ProfileError(Exception):
    """Raised for all profile resolution refusals."""


_defaults = {
    "env": {},
    "settings_overlay": None,
    "price_in_per_mtok": 0,
    "price_out_per_mtok": 0,
    "rules_file": "QWEN.md",
    "altitude": "lld",
    "defaults": {"workers": 1, "max_iterations": 3, "timeout": 900},
}

QWEN_LOCAL = {
    "name": "qwen-local",
    "argv": [
        QWEN_BIN,
        "-p",
        "{task}",
        "--approval-mode",
        "{mode}",
        "-o",
        "json",
        "-r",
        "{resume}",
    ],
    "env": {},
    "settings_overlay": None,
    "price_in_per_mtok": 0,
    "price_out_per_mtok": 0,
    "endpoint": "local",
    "rules_file": "QWEN.md",
    "altitude": "lld",
    "defaults": {"workers": 1, "max_iterations": 3, "timeout": 900},
}


def _machine_path():
    return os.environ.get(
        "QWEN_DELEGATE_EXECUTORS",
        os.path.expanduser("~/.qwen-delegate/executors.json"),
    )


def _machine_config_path():
    return os.environ.get(
        "QWEN_DELEGATE_CONFIG",
        os.path.expanduser("~/.qwen-delegate/config.json"),
    )


def dispatch_mode(cwd):
    """The configured delegation dispatch policy, or None if nobody set one.

    Precedence: project .qwen-delegate.json `dispatch` > machine
    ~/.qwen-delegate/config.json `dispatch`.

    `"serial"` pins every endpoint to ONE in-flight request whatever its
    parallel_max says -- one local endpoint is one GPU, and concurrent requests
    do not get a private context each (on Ollama the loaded context is split
    across parallel slots), so fan-out buys wall-clock at the price of a
    shorter effective context per request, which is how a turn gets truncated
    mid tool-call.

    Unset is not "parallel": with no endpoints section every endpoint already
    holds one slot, so the out-of-the-box behaviour is serial. Setting this
    only matters where an endpoint declares parallel_max > 1 -- there, an
    explicit `"serial"` overrules it and `"parallel"` leaves it alone. Any
    other value reads as "serial": a typo must not turn concurrency on.
    """
    for path in (os.path.join(cwd or ".", ".qwen-delegate.json"),
                 _machine_config_path()):
        data, _ = _load(path)
        mode = (data or {}).get("dispatch")
        if mode:
            return "parallel" if str(mode).strip().lower() == "parallel" else "serial"
    return None


def _load(config_path):
    """Load and parse a JSON file. Returns (data, error_message)."""
    if not os.path.isfile(config_path):
        return None, None
    try:
        with open(config_path) as f:
            return json.load(f), None
    except (json.JSONDecodeError, ValueError):
        return None, config_path


def _apply_defaults(profile):
    """Fill in missing optional fields."""
    for key, default in _defaults.items():
        if key not in profile:
            profile[key] = copy.deepcopy(default)


def _resolve(name, profiles, endpoints):
    """Resolve a profile name to a profile dict with defaults and endpoint cfg."""
    if name in profiles:
        raw = profiles[name]
        _apply_defaults(raw)
        if "name" not in raw:
            raw["name"] = name
        if "endpoint" not in raw:
            raw["endpoint"] = raw["name"]
        argv = raw.get("argv", [])
        if not any("{task}" in e for e in argv):
            raise ProfileError(
                f"Profile '{name}' argv is missing '{{task}}' placeholder"
            )
        profile = copy.deepcopy(raw)
    elif name == QWEN_LOCAL["name"]:
        profile = copy.deepcopy(QWEN_LOCAL)
    else:
        known = list(profiles.keys()) + [QWEN_LOCAL["name"]]
        raise ProfileError(
            f"Unknown executor '{name}'; known: {', '.join(known)}"
        )

    # Endpoint concurrency config
    ep = profile["endpoint"]
    ep_section = endpoints or {}
    if ep_section and ep not in ep_section:
        raise ProfileError(
            f"Endpoint '{ep}' referenced by profile '{profile['name']}' "
            "does not exist in endpoints section"
        )
    if ep in ep_section:
        pm = ep_section[ep].get("parallel_max", 1)
        profile["endpoint_cfg"] = {
            "name": ep,
            "parallel_max": max(pm, 1),
        }
    else:
        profile["endpoint_cfg"] = {"name": ep, "parallel_max": 1}
    return profile


def resolve(cwd, call_executor=None):
    """Resolve the executor profile to use for a delegation.

    Precedence: call_executor arg > project .qwen-delegate.json 'executor'
    > machine file 'default' > builtin qwen-local.

    The resolved profile carries the EFFECTIVE dispatch policy (see
    dispatch_mode): a configured "serial" pins the endpoint to one slot
    whatever its parallel_max says, enforced here -- the one place every
    caller already reads -- so no call site can route around the policy.
    """
    profile = _resolve_profile(cwd, call_executor)
    if dispatch_mode(cwd) == "serial":
        profile["endpoint_cfg"]["parallel_max"] = 1
    profile["dispatch"] = (
        "serial" if profile["endpoint_cfg"]["parallel_max"] <= 1 else "parallel")
    return profile


def _resolve_profile(cwd, call_executor=None):
    mp_path = _machine_path()

    # Level 1: explicit call argument
    if call_executor:
        data, err = _load(mp_path)
        if err:
            raise ProfileError(f"Malformed machine file: {err}")
        return _resolve(
            call_executor,
            (data or {}).get("profiles", {}),
            data.get("endpoints") if data else None,
        )

    # Level 2: project config
    proj_path = os.path.join(cwd, ".qwen-delegate.json")
    proj, _ = _load(proj_path)
    if proj and "executor" in proj:
        data, err = _load(mp_path)
        if err:
            raise ProfileError(f"Malformed machine file: {err}")
        return _resolve(
            proj["executor"],
            (data or {}).get("profiles", {}),
            data.get("endpoints") if data else None,
        )

    # Level 3: machine file default
    data, err = _load(mp_path)
    if err:
        raise ProfileError(f"Malformed machine file: {err}")
    if data and "default" in data:
        return _resolve(
            data["default"],
            data.get("profiles", {}),
            data.get("endpoints"),
        )

    # Level 4: builtin
    return _resolve(QWEN_LOCAL["name"], {}, None)


def render_argv(profile, task, mode, resume):
    """Substitute task/mode/resume placeholders into profile argv template."""
    argv = list(profile["argv"])
    for i in range(len(argv)):
        if isinstance(argv[i], str):
            argv[i] = argv[i].replace("{task}", task).replace("{mode}", mode)
    if resume:
        for i in range(len(argv)):
            if isinstance(argv[i], str):
                argv[i] = argv[i].replace("{resume}", resume)
    else:
        # Drop {resume} and the flag element before it (e.g. "-r").
        for i in range(len(argv)):
            if isinstance(argv[i], str) and argv[i] == "{resume}":
                del argv[i]
                del argv[i - 1]
                break
    return argv


def cost_usd(profile, tokens_in, tokens_out):
    """Compute USD cost from token counts and profile prices."""
    return (
        tokens_in * profile["price_in_per_mtok"] / 1e6
        + tokens_out * profile["price_out_per_mtok"] / 1e6
    )
