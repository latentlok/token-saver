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
    "defaults": {"max_iterations": 3, "timeout": 900},
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
    "defaults": {"max_iterations": 3, "timeout": 900},
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

    A KILL SWITCH, not the concurrency knob. Capacity is declared per endpoint
    as `parallel_max`; this exists to clamp every endpoint to ONE in-flight
    request whatever they declare, for the case where a box is misbehaving and
    the fastest diagnosis is "stop everything overlapping".

    Unset is the normal state and is not "parallel": with no endpoints section
    every endpoint already holds one slot, so out of the box everything is
    serial anyway. Declaring `parallel_max` is the opt-in; setting this only
    matters where an endpoint has declared more than one slot to overrule. Any
    value other than "parallel" reads as "serial": a typo must not turn
    concurrency on.
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
    # `parallel_max` is the ONE concurrency declaration: how many requests
    # this endpoint serves at once. Every endpoint is an OpenAI-compatible
    # API -- self-hosted vLLM and a hosted key are the same thing to this
    # plugin, an argv template plus a base URL -- so there is no local/remote
    # distinction to model and no second knob to reconcile with this one.
    # Want an endpoint serial? Give it one slot.
    pm = (ep_section.get(ep) or {}).get("parallel_max", 1)
    profile["endpoint_cfg"] = {"name": ep, "parallel_max": max(pm, 1)}
    return profile


def resolve(cwd, call_executor=None):
    """Resolve the executor profile to use for a delegation.

    Precedence: call_executor arg > project .qwen-delegate.json 'executor'
    > machine file 'default' > builtin qwen-local.

    Concurrency comes from ONE place: the endpoint's `parallel_max`. Each
    endpoint holds that many slots, machine-wide, and items queue on the
    endpoint they actually target -- so a fleet of endpoints with different
    capacities needs no coordination beyond declaring each one.

    `dispatch: "serial"` (project or machine config) stays as a KILL SWITCH,
    not a policy layer: it clamps every endpoint to one slot for debugging a
    box that is misbehaving. It is deliberately blunt and deliberately global.
    Anything that is not exactly "parallel" reads as serial -- a typo must
    never turn concurrency on.

    `dispatch` carried on the returned profile is derived, never configured:
    it is just "did this call get more than one slot", which is what the
    receipt's DISPATCH line reports.
    """
    profile = _resolve_profile(cwd, call_executor)
    cfg = profile["endpoint_cfg"]
    if dispatch_mode(cwd) == "serial":
        cfg["parallel_max"] = 1
    profile["dispatch"] = (
        "serial" if cfg["parallel_max"] <= 1 else "parallel")
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

    # Level 4: builtin. `endpoints` is passed THROUGH: the minimal machine
    # file -- declare capacity for the builtin profile, define no custom
    # profile -- parsed fine, validated fine and did nothing, because this
    # level used to hardcode None. Capacity was silently ignored unless the
    # file also named `"default": "qwen-local"`, which nothing documents.
    return _resolve(QWEN_LOCAL["name"], {},
                    data.get("endpoints") if data else None)


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
