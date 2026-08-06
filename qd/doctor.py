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
    the LAST colon. Any registry that tags a model `family:variant` therefore
    loses the family: `qwen3.6:27b-agent-q8-maxctx` normalizes to
    `27b-agent-q8-maxctx`, matches nothing, and silently takes the 32k default
    output cap instead of the 64k its family would have got.
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

    Recorded in ~/.qwen-delegate/config.json by `--verified N` after reading the
    window the SERVER reports (vLLM: `--max-model-len`, or the `max_model_len`
    field on /v1/models). Nothing here can observe the endpoint, so this is the
    only way the declared number stops being a claim. Kept separate from the
    declaration on purpose: if either changes, they stop matching and the
    finding comes back.
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
                    f"less -- a server started with a smaller "
                    f"`--max-model-len`, or a window divided across concurrent "
                    f"slots -- a receipt reads 'safe, well under compaction' "
                    f"while the endpoint is truncating. Read the window the "
                    f"server reports (vLLM: `--max-model-len`, or `max_model_len` "
                    f"on /v1/models) and record it: "
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

    # The project half. Separate from the executor half because the two have
    # different owners: one is machine config, the other is this repo's.
    proj = project_check(os.getcwd())
    if proj:
        print("\n--- this project ---")
        print(report(proj))

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


# --- Project checks (v0.6) --------------------------------------------------
#
# `check()` above inspects the EXECUTOR settings. These inspect the PROJECT and
# the machine, and they exist because every one of them was a silent trap that
# cost real time in the field. All are static: nothing runs, nothing is guessed.

def _stale_contract_pins(cwd):
    """A8: test files pinned to a contract that has since moved or vanished.

    Link 1 writes `# contract: <path> @ <digest>` into the gate it commits, and
    a later link REFUSES if the digest no longer matches -- that is the
    cross-link protection (A2.3). This is the same question asked LATER, and it
    is the third way the design says a contract bites: *edited between the run
    and review, so the receipt you audit no longer describes the criteria that
    ran.*

    A gate refuses at the moment it matters. A doctor check finds the ones that
    already drifted and nobody re-ran -- a test still passing against criteria
    that changed underneath it, which reads exactly like a test that agrees with
    the contract.

    Never raises: doctor is what a confused caller reaches for.
    """
    out = []
    try:
        from qd.core import contract
        from qd.gittree import git, spec_files
    except Exception:
        return out
    try:
        rc, listing = git(cwd, "ls-files")
        paths = listing.splitlines() if rc == 0 else []
    except Exception:
        return out

    for rel in paths:
        if not rel.endswith((".py", ".js", ".ts", ".rb", ".go")):
            continue
        try:
            with open(os.path.join(cwd, rel)) as f:
                pinned_path, pinned = contract.parse_header(f.read())
        except (OSError, UnicodeDecodeError):
            continue
        if not pinned:
            continue
        full = os.path.join(cwd, pinned_path)
        if not os.path.exists(full):
            out.append(("warn", f"{rel} is pinned to {pinned_path}, which no "
                                f"longer exists -- the gate is graded against "
                                f"criteria nobody can read"))
            continue
        try:
            with open(full) as f:
                now = contract.digest(f.read())
        except (OSError, UnicodeDecodeError):
            continue
        if now != pinned:
            out.append(("warn",
                        f"{rel} is pinned to {pinned_path} @ {pinned}, but it "
                        f"is now @ {now} -- the contract changed after the gate "
                        f"was written, so the test agrees with a version that "
                        f"is gone. Re-run the step that wrote the gate."))
    return out


def project_check(cwd):
    """Findings about this project's delegation config. Same shape as check().

    Never raises: doctor is what a confused caller reaches for, so a fault in
    a check must not be the thing that stops them getting the other answers.
    """
    out = []
    try:
        from qd import gittree, profiles, runlog
    except Exception:
        return out

    try:
        cfg = gittree._project_config(cwd)
    except Exception:
        cfg = {}

    out += _stale_contract_pins(cwd)

    # A9: the gate the server synthesises for trust="self" comes from
    # `test_command`. The architect's own gates live in specs/ by the plugin's
    # own convention and are auto-reverted if the worker edits them -- but if
    # test_command does not REACH them, that protection guards a file the gate
    # never runs. A trust="self" call then grades against a suite that cannot
    # fail for the reason the delegation existed, and reports green.
    cmd = (cfg.get("test_command") or "").strip()
    if cmd:
        try:
            globs = gittree.spec_globs(cwd)
        except Exception:
            globs = []
        roots = {g.split("*")[0].strip("/") for g in globs if g.split("*")[0].strip("/")}
        if roots and not any(r in cmd for r in roots):
            out.append({
                "id": "gate-misses-specs", "severity": "high", "fixable": False,
                "text": ("`test_command` (%s) names none of the protected spec "
                         "locations (%s), so a trust=\"self\" gate cannot run "
                         "your own spec -- it would grade against a suite that "
                         "cannot fail for the reason you delegated."
                         % (cmd, ", ".join(sorted(roots)))),
            })

    # A0: a project whose suite is slower than the per-gate kill guarantees
    # that every trust="self" run refuses BEFORE the worker starts. Read from
    # this project's own history rather than by running the suite: doctor must
    # stay cheap enough to run on a whim.
    try:
        vt = int(cfg.get("verify_timeout_sec") or 300)
        worst = 0
        for rec in runlog.completed_runs(cwd):
            worst = max(worst, int(rec.get("gate_ms") or 0))
        if worst and worst > vt * 1000 * 0.7:
            out.append({
                "id": "gate-near-timeout", "severity": "high", "fixable": False,
                "text": ("A pre-flight gate here has taken %.0fs against a "
                         "verify_timeout_sec of %ds. Past that kill the run is "
                         "REFUSED before the worker starts, and the refusal "
                         "blames the gate. Raise verify_timeout_sec."
                         % (worst / 1000.0, vt)),
            })
    except Exception:
        pass

    # A4: capacity declared and unreachable, or fan-out that will silently
    # serialise. Either way a batch costs N x wall-clock for 1x throughput.
    try:
        prof = profiles.resolve(cwd, None)
        slots = prof["endpoint_cfg"]["parallel_max"]
        if slots <= 1:
            out.append({
                "id": "no-fanout", "severity": "info", "fixable": False,
                "text": ("Endpoint '%s' holds 1 slot, so `batch` runs its items "
                         "one at a time. Declare `parallel_max` in the endpoints "
                         "section of the machine executors file to fan out."
                         % prof["endpoint_cfg"]["name"]),
            })
    except Exception:
        pass

    # Orphaned containers. A lone run disposes of its worktree inside the run;
    # a CHAIN's container is held across every link and released only when the
    # chain ends, so a chain that dies mid-flight leaves one with no owner.
    # Reported, never removed: it holds work that passed its gate, and the
    # caller may not have read the receipt yet.
    try:
        from qd import worktrees
        orphans = worktrees.stale(cwd)
        if orphans:
            oldest = max(o["age_s"] for o in orphans) // 3600
            out.append({
                "id": "stale-worktrees", "severity": "info", "fixable": False,
                "text": ("%d delegation worktree(s) untouched for >6h (oldest "
                         "~%dh): %s. A chain that died mid-flight leaves its "
                         "container behind. Each holds committed work -- merge "
                         "or `git worktree remove` them."
                         % (len(orphans), oldest,
                            ", ".join(o["branch"] or o["path"]
                                      for o in orphans[:4]))),
            })
    except Exception:
        pass

    # A0d: several servers, possibly of different plugin versions, sharing one
    # .qwen-delegate/ directory and one machine-wide lock namespace. Eleven
    # were once alive on one box, the oldest three days stale.
    n, versions, pids = _server_count()
    if n > 1:
        out.append({
            "id": "stale-servers", "severity": "high", "fixable": False,
            # Names the PIDs. "Kill the stale ones" was true and unactionable:
            # a caller then had to re-derive the same pgrep this check already
            # ran, and get the pattern right -- a loose one matches the shell
            # doing the deriving.
            "text": ("%d token-saver servers are running (%s). They share this "
                     "project's .qwen-delegate/ state and the machine-wide "
                     "endpoint lock; an upgrade or /reload-plugins starts a new "
                     "one without stopping the old.%s"
                     % (n, ", ".join(versions) or "unknown versions",
                        _kill_hint(pids))),
        })

    order = {"high": 0, "info": 1}
    return sorted(out, key=lambda f: order.get(f["severity"], 1))


def _kill_hint(pids):
    """The exact command, or a bare instruction when the pids are unknown.

    A finding that names the problem and leaves the reader to reconstruct the
    query is a finding they postpone. Oldest first, and never including this
    process: the newest server is the one the caller is talking to.
    """
    others = [p for p in (pids or []) if p != os.getpid()]
    if not others:
        return " Kill the stale ones."
    return (" Kill the stale ones (oldest first): kill %s"
            % " ".join(str(p) for p in others[:-1] or others))


def _server_count():
    """(count, versions, pids) of running token-saver servers, oldest first.

    (0, [], []) if unknown."""
    import re as _re
    import subprocess as _sp
    # The command line must be an INTERPRETER running the server script, not
    # merely a line mentioning it. A loose `token-saver.*server.py` matched the
    # shell that was running the check itself, so the count depended on how it
    # was invoked -- a detector whose answer changes with the observer.
    real = _re.compile(r"\bpython[0-9.]*\s+\S*token-saver\S*/server\.py")
    try:
        p = _sp.run(["pgrep", "-af", "server.py"],
                    capture_output=True, text=True, timeout=5)
        lines = [l for l in (p.stdout or "").splitlines()
                 if l.strip() and real.search(l) and str(os.getpid()) not in l.split()[:1]]
        versions = sorted({m.group(1) for l in lines
                           for m in [_re.search(r"/(\d+\.\d+\.\d+)/", l)] if m})
        pids = []
        for l in lines:
            try:
                pids.append(int(l.split()[0]))
            except (ValueError, IndexError):
                continue
        # pgrep lists oldest first, which is the order to kill in: the newest
        # server is the one the caller is currently talking to.
        return len(lines), versions, pids
    except Exception:
        return 0, [], []

if __name__ == "__main__":
    sys.exit(main())
