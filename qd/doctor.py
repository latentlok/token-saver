#!/usr/bin/env python3
"""
Executor health checks, behavior frozen by specs/doctor_spec.py.

Everything else in this plugin travels with the repo. This module exists for the
part that does NOT: `~/.qwen/settings.json` and the inference endpoint behind it
are per-machine, and the settings that decide whether a delegation comes back
whole live there. A plugin installed on a second box inherits none of it, which
is why the same task succeeds on one machine and returns a truncated fragment on
another.

The checks are pure functions over a parsed settings dict so they can be gated
without a qwen install; `main()` is the thin CLI (`python3 -m qd.doctor [--fix]`).

Run:  python3 -m qd.doctor          # report
      python3 -m qd.doctor --fix    # write the safe fixes, backup first
"""

import json
import os
import re
import shutil
import sys

SETTINGS_PATH = os.environ.get("QWEN_SETTINGS") or os.path.expanduser(
    "~/.qwen/settings.json")

# qwen-code's own defaults (packages/core/src/core/tokenLimits.ts, read out of
# the installed bundle). Mirrored, not imported -- there is no stable way to ask
# the CLI for them, and a silent upstream change should surface as a wrong
# recommendation here, not as a truncated run nobody can explain.
DEFAULT_OUTPUT_TOKEN_LIMIT = 32_000
ESCALATED_MAX_TOKENS = 64_000

# The output-limit table, qwen-family rows only -- the rest of the table cannot
# match a name that reached this plugin.
_OUTPUT_PATTERNS = [
    (re.compile(r"^qwen3\.\d"), 65_536),
    (re.compile(r"^coder-model$"), 65_536),
    (re.compile(r"^qwen"), 32_768),
]

# What --fix writes when no number is given. 65,536 is the output limit a
# RECOGNISED qwen3.x gets, so the fix can never leave a model worse off than
# correct naming would have. Deliberately not the 32,000 generic default: on a
# thinking model the reasoning stream spends this same budget, and a cap that the
# thinking alone can exhaust produces a truncated turn with no answer in it.
RECOMMENDED_MAX_TOKENS = 65_536

# Mirrors qd.invoke.COMPACTION_RESERVE; duplicated rather than imported so the
# doctor stays runnable as a bare script on a machine mid-install.
COMPACTION_RESERVE = 33_000


def normalize_model(name):
    """qwen-code's normalize(), the part that decides a model's token limits.

    The load-bearing line is `s.split(":").pop()` -- it keeps only what follows
    the LAST colon. An Ollama tag is `family:variant`, so `qwen3.6:27b-agent-q8-maxctx`
    normalizes to `27b-agent-q8-maxctx`, matches nothing, and silently takes the 32k
    default output cap instead of the 64k its family would have got.
    """
    s = (name or "").lower().strip()
    s = re.sub(r"^.*/", "", s)
    s = s.split("|")[-1]
    s = s.split(":")[-1]
    s = re.sub(r"\s+", "-", s)
    return s.replace("-preview", "")


def output_limit(name):
    """(limit, recognised) — the output cap qwen-code will apply to this name."""
    norm = normalize_model(name)
    for rx, limit in _OUTPUT_PATTERNS:
        if rx.search(norm):
            return min(limit, ESCALATED_MAX_TOKENS), True
    return DEFAULT_OUTPUT_TOKEN_LIMIT, False


def verified_window():
    """The context window someone confirmed the endpoint actually serves, or None.

    Recorded in ~/.qwen-delegate/config.json by `--verified N` after reading it off
    the server (`ollama ps` reports CONTEXT for the loaded model). Nothing here can
    observe the endpoint, so this is the only way the declared number stops being a
    claim. Kept separate from the declaration on purpose: if either changes, they
    stop matching and the finding comes back.
    """
    try:
        path = os.environ.get("QWEN_DELEGATE_CONFIG") or os.path.expanduser(
            "~/.qwen-delegate/config.json")
        with open(path) as f:
            return int(json.load(f).get("verified_context_window") or 0) or None
    except Exception:
        return None


def record_verified(window):
    """Write verified_context_window into the machine config. Returns the path."""
    path = os.environ.get("QWEN_DELEGATE_CONFIG") or os.path.expanduser(
        "~/.qwen-delegate/config.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}
    cfg["verified_context_window"] = int(window)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)
    return path


def active_provider(settings):
    """(provider_entry, model_name) for the selected model, or (None, name)."""
    model = ((settings or {}).get("model") or {}).get("name")
    for entries in ((settings or {}).get("modelProviders") or {}).values():
        for p in entries if isinstance(entries, list) else []:
            if p.get("id") == model or p.get("name") == model:
                return p, model
    return None, model


def check(settings):
    """Return a list of findings, worst first.

    Each is {"id", "severity", "text", "fixable"}. Severity "high" means a run
    can come back truncated or a receipt can state a safety margin it has not
    measured; "info" is context for reading the other two.
    """
    out = []
    provider, model = active_provider(settings)

    if not model:
        return [{"id": "no-model", "severity": "high", "fixable": False,
                 "text": "No model selected in settings (`model.name` is unset)."}]

    gen = (provider or {}).get("generationConfig") or {}
    limit, recognised = output_limit(model)
    explicit = gen.get("maxTokens")

    if not explicit:
        if not recognised:
            out.append({
                "id": "unrecognised-model-name", "severity": "high",
                "fixable": True,
                "text": (
                    f"Model '{model}' normalizes to '{normalize_model(model)}', "
                    f"which matches no known-limits pattern, so qwen-code caps "
                    f"output at its {DEFAULT_OUTPUT_TOKEN_LIMIT:,}-token default "
                    f"and sends max_tokens itself. NOTHING on the inference "
                    f"server shows this cap. A turn cut at the cap that was "
                    f"mid tool-call has that call rejected outright."),
            })
        else:
            out.append({
                "id": "no-explicit-max-tokens", "severity": "info",
                "fixable": True,
                "text": (
                    f"No `generationConfig.maxTokens`: qwen-code applies "
                    f"{limit:,} for this model and, on hitting it, retries once "
                    f"at {ESCALATED_MAX_TOKENS:,} plus up to 3 continuations. "
                    f"Setting it explicitly caps the churn."),
            })

    if gen.get("extra_body", {}).get("enable_thinking") and not explicit:
        out.append({
            "id": "thinking-against-output-cap", "severity": "high",
            "fixable": False,
            "text": (
                f"`enable_thinking` is on and no maxTokens is set. Reasoning "
                f"tokens count against the same {limit:,} output cap, so a long "
                f"think can exhaust it before the answer starts."),
        })

    win = gen.get("contextWindowSize")
    if win:
        ceiling = max(0, int(win) - COMPACTION_RESERVE)
        out.append({
            "id": "compaction-ceiling", "severity": "info", "fixable": False,
            "text": (
                f"Earliest-possible compaction on a {int(win):,} window: "
                f"{ceiling:,} tokens ({100.0 * ceiling / int(win):.1f}%). qwen "
                f"subtracts a hardcoded {COMPACTION_RESERVE:,} (20,000 to write the "
                f"summary + 13,000 buffer) before applying any threshold, so NO "
                f"setting moves the trigger past this. To hold N tokens before "
                f"compaction you need a window of N+{COMPACTION_RESERVE:,} actually "
                f"served — e.g. 194,000 needs 227,000."),
        })
        seen = verified_window()
        if seen == int(win):
            out.append({
                "id": "context-window-verified", "severity": "info",
                "fixable": False,
                "text": (
                    f"`contextWindowSize` {int(win):,} was confirmed against the "
                    f"endpoint. Compaction and CONTEXT lines are computed from a "
                    f"real number."),
            })
        else:
            out.append({
                "id": "context-window-unverified", "severity": "high",
                "fixable": False,
                "text": (
                    f"`contextWindowSize` is declared as {int(win):,}"
                    + (f" but {seen:,} was recorded as verified -- they no longer "
                       f"agree. " if seen else ". ")
                    + f"This plugin computes every CONTEXT / compaction line from "
                    f"it and cannot verify it -- if the endpoint actually serves "
                    f"less (Ollama's num_ctx, or a context split across "
                    f"OLLAMA_NUM_PARALLEL slots), a receipt reads 'safe, well "
                    f"under compaction' while the endpoint is truncating. Read "
                    f"CONTEXT off `ollama ps` on the box, then record it: "
                    f"`python3 -m qd.doctor --verified <N>`."),
            })
    else:
        out.append({
            "id": "no-context-window", "severity": "info", "fixable": False,
            "text": ("No `contextWindowSize` set: compaction warnings in the "
                     "receipt are disabled entirely (peak is reported raw)."),
        })

    order = {"high": 0, "info": 1}
    return sorted(out, key=lambda f: order.get(f["severity"], 1))


def apply_fix(settings, max_tokens=RECOMMENDED_MAX_TOKENS):
    """Set generationConfig.maxTokens on the active provider. Returns (settings, changed).

    Only this one fix is written. The others need a fact this process cannot
    observe (what the endpoint really serves), and guessing at those would
    replace an unverified number with a differently unverified number.
    """
    provider, _ = active_provider(settings)
    if provider is None:
        return settings, False
    gen = provider.setdefault("generationConfig", {})
    if gen.get("maxTokens") == max_tokens:
        return settings, False
    gen["maxTokens"] = max_tokens
    return settings, True


def load(path=None):
    path = path or SETTINGS_PATH
    with open(path) as f:
        return json.load(f)


def report(findings):
    """Human-readable report; returns the text."""
    if not findings:
        return "qwen executor settings: no findings."
    lines = ["qwen executor settings -- %d finding(s):" % len(findings), ""]
    for f in findings:
        lines.append(f"[{f['severity'].upper()}] {f['id']}")
        lines.append(f"  {f['text']}")
        lines.append("")
    if any(f["fixable"] for f in findings):
        lines.append("Re-run with --fix to write the settings-side fixes "
                     "(a .bak copy is made first).")
    return "\n".join(lines).rstrip()


def summary_line(path=None):
    """One line for a receipt, or None when there is nothing to say."""
    try:
        findings = check(load(path))
    except Exception:
        return None
    high = [f for f in findings if f["severity"] == "high"]
    if not high:
        return None
    return ("EXECUTOR: %d setting(s) can truncate or mis-report this run (%s). "
            "Run `python3 -m qd.doctor` -- this is machine config, not a repo bug."
            % (len(high), ", ".join(f["id"] for f in high)))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    path = SETTINGS_PATH
    if "--settings" in argv:
        path = argv[argv.index("--settings") + 1]
    try:
        settings = load(path)
    except FileNotFoundError:
        print(f"No qwen settings at {path} -- is Qwen Code configured on this "
              f"machine? (Each machine needs its own.)")
        return 2
    except ValueError as e:
        print(f"Malformed settings at {path}: {e}")
        return 2

    if "--verified" in argv:
        try:
            window = int(argv[argv.index("--verified") + 1])
        except (IndexError, ValueError):
            print("--verified needs the context size, e.g. --verified 196608")
            return 2
        print(f"Recorded verified context window {window:,} in "
              f"{record_verified(window)}.\n")
        settings = load(path)

    findings = check(settings)
    print(report(findings))

    if "--fix" in argv:
        want = RECOMMENDED_MAX_TOKENS
        tail = argv[argv.index("--fix") + 1:]
        if tail and tail[0].isdigit():
            want = int(tail[0])
        settings, changed = apply_fix(settings, want)
        if not changed:
            print("\nNothing to write.")
            return 0
        shutil.copyfile(path, path + ".bak")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp, path)
        print(f"\nWrote maxTokens={want:,} to {path} (backup: {path}.bak).")
    return 1 if any(f["severity"] == "high" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
