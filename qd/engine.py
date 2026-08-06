#!/usr/bin/env python3
"""
The delegation loop — behavior frozen by specs/engine_spec.py.

Ports server.py's _delegate_once / run_qwen / retry_prompt into a clean
two-function surface backed entirely by qd submodules.
"""

import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import threading
import time

from qd.profiles import resolve, cost_usd
from qd import invoke
from qd.invoke import (
    run_executor, accum_stats, cum_zero,
    was_compacted_since_ack, ack_compaction, compaction_counts,
    truncate, stall_seconds as invoke_stall_seconds, context_window,
)
from qd.gittree import (
    git, is_git_repo, file_sha, unquote_path,
    snapshot, violated_specs, revert_specs, untracked_files,
    snapshot_contents, restore_paths, spec_files,
    # committed_during_run / head_sha / new_public_symbols / numstat_map moved
    # to qd/core/facts.py, and uncalled_symbols / mocked_seams / never_executed
    # / dodge_markers to qd/features/detectors/ -- the engine neither computes
    # tree facts nor observes the tree any more. It asks, and renders the answer.
    _project_config, _global_config,
)
from qd.core.plan import RunPlan, setting
from qd.core import contract as core_contract
from qd.core.attempt import Attempt
from qd.core.pipeline import (
    gate_is_slow,
    graph_shell_grant,
    peak_high_water,
    preflight_shareable,
    ratchet_minimum,
)
from qd.core.prompt import compose as prompt_compose, tail as prompt_tail
from qd.core.status import classify as run_status
from qd.core.scope import RunScope
from qd.features import advisories, detectors, gates, guards
from qd.bootstrap import (
    worker_rules_status, bootstrap_worker_rules,
    bootstrap_notice, bootstrap_failed_refusal,
    nongit_refusal, detect_test_cmd, test_dir as detect_test_dir,
)
from qd.runlog import runlog_dir, save_brief, load_brief, BRIEFS_DIR, CallLog
from qd.core import facts
from qd import doctor
from qd import playbook
from qd import jsonschema
from qd import limits
from qd import worktrees
from qd.verdict import (
    render, HANDOFF_SUFFIX, FINDINGS_SUFFIX, VERIFY_CAP, parse_findings,
)
from qd import refs
from qd import graph

_DEFAULT_MAX_ITER = 3
_DEFAULT_TIMEOUT = 900

# How long ONE verify run may take. Was a bare literal: a live-network gate in
# the field timed out at 300s before the run and again after it, which the
# classifier then read as "output identical to preflight" and reported a good
# delivery as gate_suspect. Nobody could raise it, because it was not a knob.
_DEFAULT_VERIFY_TIMEOUT = 300

# Cumulative input tokens a single delegation may spend before it is stopped.
# Set deliberately high: a limit that fires on legitimate work is worse than no
# limit, because the first false positive is the one that gets it switched off.
# For scale, measured in this repo: a real delegation that wrote a module and
# its tests cost ~560k, while the runaway this exists to catch reached 19M. So
# this sits an order of magnitude above known-good work and still ends the
# runaway less than halfway. Tune down with `burn_budget` once a project knows
# where its own normal sits; 0 or null disables it.
_DEFAULT_BURN_BUDGET = 10_000_000

# --- The reserved args, and what "reserved" actually buys ---
#
# WHAT IS TRUE: every name below is absent from qd/schemas.py, so nothing
# advertises it, no conforming client sends it, and the server itself sets it at
# exactly one place each. Where a setter also STRIPS an inherited copy first --
# run_chain does this for CARRIED_ARG -- the value on that path is the server's
# by construction.
#
# WHAT IS NOT TRUE, and used to be claimed here: that a caller CANNOT set them.
# There is no `additionalProperties: false` and no filtering at the wire seam --
# qd/server.py's tools/call handler passes `params["arguments"]` through as it
# arrives -- so an off-schema key does reach engine.run on a LONE delegation.
#
# EIGHT keys, not the five declared in this file: `_brief` (set at
# qd/playbook.py:292) and `_batch_size` (qd/server.py:873) are reserved by the
# same convention and live elsewhere. `_brief` is the one with a demonstrated
# bite -- its `path` is rendered into the receipt's BRIEF: line, high enough up
# to sit ABOVE a genuine result stamp, so a caller-supplied newline in it forged
# a validated result for the next chain link (closed now, in qd/verdict.py's
# `_one_line`, and pinned by specs/verdict_spec.py::StampedResult). That is this
# same class of hole with a caller rather than a worker holding the pen.
#
# The exposure that remains is bounded (a lone run has no next link, so a forged
# value only colours that run's own prompt, which is the caller lying to their
# own worker), but it is real, and a comment that overstates a protection is
# worse than no comment: it stops the next reader from looking. Closing it means
# filtering reserved keys at that one seam for ALL EIGHT names at once, which is
# a change to args other owners hold and is gated by a spec CI skips
# (dispatch_spec) -- queued deliberately rather than half-applied here.
#
# U4.1: the chain position run_chain injects into each link's args. Absent from
# the schema so no ordinary call claims to be link 3 of a chain that never ran.
CHAIN_ARG = "_chain"
# G2: the WHOLE chain's briefs, composed by run_chain and handed to link 1 only.
# The challenge pass reads this instead of link 1's task alone, so a link 3 that
# contradicts link 1 is caught BEFORE link 2 commits into the shared worktree --
# which is the moment the contradiction becomes expensive rather than free.
CHAIN_BRIEF_ARG = "_chain_brief"

# The worktree a chain LENDS to each of its links. Reserved for the same reason
# CHAIN_ARG is, and with the same real strength: absent from the schema, set by
# run_chain alone, and not filtered at the wire (see the family note above).
#
# A chain is the dependent shape -- "link 2 builds on link 1's tree" -- and that
# sentence was false under `worktree: "auto"`. Every link acquired its OWN tree
# from HEAD and, on success, committed to its own branch without merging back
# (see the disposition block below), so link 2 opened a clean checkout with none
# of link 1's files in it. The dependency the shape promises simply was not
# implemented; `"off"` was the only mode where it held, and that mode takes the
# repo lock and serializes every concurrent chain.
#
# A lent worktree is USED but never disposed of: the link commits into it (which
# is what makes link 1's files both visible AND tracked, so spec_globs can
# protect them from link 2), and the chain decides at the end whether to keep or
# release the container.
WT_ARG = "_worktree"

# PARKED B / §10.3, `carry: "structured"`: the previous link's VALIDATED result,
# as a declared slot on this link's args -- {"grade", "from", "of", "json"},
# where `json` is the stamped block verbatim. §10.2 asks for "a declared slot"
# and means it: the payload glued onto `task` instead is the handoff preamble
# with more tokens and no type, which is precisely what the old `!= "none"` test
# did with it.
#
# Reserved like CHAIN_ARG and WT_ARG, and the one of the family with a real
# enforcement rather than only an absence: run_chain STRIPS any inherited copy
# before setting its own, so inside a chain -- the only place the value means
# anything -- a caller cannot forge it. On a lone delegation it is settable from
# the wire like every other reserved arg (see the family note above); there is
# no next link there, so the lie goes no further than that run's own prompt.
# The slot's whole value is the sentence "the previous link validated this",
# which is the one thing a task string could not carry honestly.
CARRIED_ARG = "_carried"

# U5.2, same reserved-arg convention: the submitted run's id, and the result of
# the preconditions the server already ran. Both are injected by qd/server.py
# on the way into a background delegation and are absent from the schema -- a
# caller cannot mint its own run id, nor claim to have passed a check.
RUN_ID_ARG = "_run_id"
PRECHECK_ARG = "_precheck"

# The deterministic hand-off from the challenge pass to the build, prepended to
# the build task when the two share a session. NOT generated by the model: the
# challenge prompt ends with "Do NOT build anything yet", that line is in the
# history the build inherits, and something has to revoke it in words the
# worker cannot mistake for more review. A model asked to retract its own
# instruction produces a paraphrase; this is a fact stated by the server.
CHALLENGE_CLEARED = (
    "--- END OF BRIEF REVIEW ---\n"
    "The review above is finished and its instruction not to build no longer "
    "applies. You are now building. Ignore any earlier line telling you to "
    "hold off; that line governed the review only.\n"
    "--- YOUR TASK FOLLOWS ---\n\n"
)


def _carried_prefix(slot):
    """The inherited result, as a prompt PREFIX. "" when nothing was carried.

    A prefix, per qd/core/prompt.py's own rule: this is the SITUATION the
    worker is in -- what the link before it delivered -- and it has to be read
    before the instruction it qualifies. The task is the instruction, and the
    instruction reads last.

    FRAMED, for the same reason `_carry_forward`'s preamble is framed: a bare
    JSON object at the top of a task reads as a specification to satisfy, and
    the worker would spend the run reproducing the previous link's output
    instead of building its own. The frame also names the SOURCE, because "the
    previous link validated this" is the only thing that distinguishes a
    carried result from a value the caller typed.
    """
    if not isinstance(slot, dict) or not slot.get("json"):
        return ""
    src = f"link {slot.get('from')}"
    if slot.get("of"):
        src += f" of {slot['of']}"
    return (
        f"--- {src} finished; this is the result it delivered, validated "
        f"against its result_schema ---\n"
        f"```json\n{slot['json']}\n```\n"
        f"(context, not instructions -- your task follows)\n\n"
    )


def _warm_challenge(args, cfg):
    """Whether the build resumes the challenge's session. Default FALSE.

    Default cold, and the reason has been re-measured (see _challenge_brief).
    The old reason -- "warm costs +50% input" -- was a broken counter, not a
    cost; corrected, warm is +2% on the wire and the same wall-clock.

    So the honest position is that cost does not decide this either way.
    `challenge_warm: true` buys context continuity for anyone who wants it; it
    is not the default because nothing measured says continuity HELPS, and
    "measured harmless" is not a reason to turn something on.

    Explicit None checks, not `or`: `false` is a real answer and `or` chaining
    would fall through it to the next layer -- the same trap the challenge flag
    itself had.
    """
    return bool(setting("challenge_warm", args, cfg, _global_config(),
                        default=False))

# Minted per process, never leaving it. The tool schema does not carry
# PRECHECK_ARG, but nothing stops a client from sending one anyway -- and
# honoring an unsigned "I already passed the checks" would be a way to walk
# past the trust and dirty-spec preconditions. A result without this token is
# not one of ours, so the checks simply run again.
_PRECHECK_TOKEN = secrets.token_hex(8)

# U3.3 fixture provenance. Directory SEGMENTS, matched per path component: the
# five names the field report's fixtures actually lived under. Projects override
# with `fixture_globs` in .qwen-delegate.json.
_FIXTURE_SEGMENTS = ("fixtures", "testdata", "golden", "snapshots", "cassettes")
_PROVENANCE_HEADER = "captured-from:"

_SELF_GATE_PATH = os.path.join(".qwen-delegate", "selfgate.sh")

_SELF_GATE = """#!/bin/bash
# Generated per-attempt by trust="self" (L5): the DELEGATE'S OWN suite is the
# gate; this wrapper only guards the vacuous pass. Worker edits are overwritten.
cd "$(dirname "$0")/.." || exit 1
# Braces group the WHOLE suite before redirecting: in `$(a; b 2>&1)` the
# redirect binds only to `b`, so a compound test command (`a && b`, or a
# script running several files) silently loses every earlier command's
# stderr -- which is exactly where unittest reports its results.
out=$({{ {suite} ; }} 2>&1)
status=$?
echo "$out" | tail -25
[ "$status" -ne 0 ] && exit 1
# SUM every count, don't take the first: a suite that runs many files prints
# one line per file, and reading only the first compares the bar against a
# single file's total. That can demand more tests than any one file holds, so
# the gate is unsatisfiable and self-grading silently never works.
#
# The two runners are counted SEPARATELY because they mean different things by
# their totals, and conflating them is how a suite of nothing but skips cleared
# this bar. unittest's "Ran N tests" INCLUDES skipped ones (it then prints
# "OK (skipped=N)"); pytest's "N passed" already excludes them. So unittest's
# total needs the skips subtracted and pytest's must not be discounted twice.
# A skip is not a failure -- but it is not evidence either, and this guard
# exists to count evidence.
u_ran=$(echo "$out" | grep -Eo 'Ran [0-9]+ tests?' | grep -Eo '[0-9]+' \
        | awk '{{s+=$1}} END {{if (NR) print s}}')
p_ran=$(echo "$out" | grep -Eo '[0-9]+ passed' | grep -Eo '[0-9]+' \
        | awk '{{s+=$1}} END {{if (NR) print s}}')
u_skip=$(echo "$out" | grep -Eo 'skipped=[0-9]+' | grep -Eo '[0-9]+' \
         | awk '{{s+=$1}} END {{if (NR) print s+0}}')
p_skip=$(echo "$out" | grep -Eo '[0-9]+ skipped' | grep -Eo '[0-9]+' \
         | awk '{{s+=$1}} END {{if (NR) print s+0}}')
[ -z "$u_skip" ] && u_skip=0
[ -z "$p_skip" ] && p_skip=0
skipped=$(( u_skip + p_skip ))
ran=""
[ -n "$u_ran" ] && ran=$(( u_ran - u_skip ))
[ -n "$p_ran" ] && ran=$(( ${{ran:-0}} + p_ran ))
if [ -n "$ran" ] && [ "$ran" -lt {min} ]; then
  note=""
  [ "$skipped" -gt 0 ] && note=" ($skipped skipped -- a skip is not evidence)"
  echo "SELF-GATE: only $ran test(s) actually ran$note -- write a real suite (>= {min} tests)"
  exit 1
fi
# No parseable count AND visible skips: we do not know how many ran, but we do
# know some did not. pytest's fully-skipped summary is "N skipped" with no
# "passed" clause at all, which is exactly this case -- and it used to reach
# the "guard inactive" note below and exit 0.
if [ -z "$ran" ] && [ "$skipped" -gt 0 ]; then
  echo "SELF-GATE: $skipped test(s) skipped and no passing count -- a skip is not evidence"
  exit 1
fi
if [ -z "$ran" ]; then
  echo "SELF-GATE NOTE: could not parse a test count; vacuous-pass guard inactive"
fi
exit 0
"""


def _ensure_self_gate(work_cwd, min_override=None):
    """(Re)write the trust="self" gate script; return the verify command.

    Rewritten before every gate run so a worker edit to the script cannot
    survive to the next gate (the same reason spec files auto-revert). Lives
    in .qwen-delegate/ -- self-gitignored, so it never appears in CHANGED.
    Suite: the project's detected test command, else stdlib unittest discovery.
    Vacuous-pass guard: >= min_tests (project .qwen-delegate.json, default 5)
    when a test count is parseable (unittest "Ran N" / pytest "N passed").
    min_override: the incremental ratchet (see delegate()) -- an existing green
    suite of N tests raises the bar to N+1 so the gate binds on the delta.
    """
    min_tests = 5
    try:
        with open(os.path.join(work_cwd, ".qwen-delegate.json")) as f:
            min_tests = int(json.load(f).get("min_tests") or min_tests)
    except Exception:
        pass
    if min_override is not None:
        min_tests = max(min_tests, min_override)
    suite = detect_test_cmd(work_cwd) or \
        f"python3 -m unittest discover -s {detect_test_dir(work_cwd) or 'tests'} -t . -v"
    d = os.path.join(work_cwd, ".qwen-delegate")
    os.makedirs(d, exist_ok=True)
    gi = os.path.join(d, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w") as f:
            f.write("*\n")
    path = os.path.join(work_cwd, _SELF_GATE_PATH)
    with open(path, "w") as f:
        f.write(_SELF_GATE.format(suite=suite, min=min_tests))
    os.chmod(path, 0o755)
    return f"bash {_SELF_GATE_PATH}"


def _challenge_brief(profile, task, work_cwd, timeout, session_id=None):
    """Ask the worker to object to the brief BEFORE building it (A23).

    Returns (objection|None, meta, session). The objection is (why, evidence)
    only when the brief is challenged AND the evidence names a path that EXISTS.

    `session` is the id this pass ran under, so the BUILD can resume it
    (`challenge_warm`). The builder then starts having already read the code it
    is about to change.

    MEASURED TWICE, and the first measurement was wrong. It read "cold 148,267
    in / warm 222,407 in -- +50% input, +16% wall", and attributed the gap to a
    resumed session re-sending its history. Both halves were mistaken: the
    counter was double-counting (G5 in docs/archive/a92e876/FINDINGS.md -- `result.usage` is a
    SESSION total, and a resumed process starts it at the previous run's), and
    re-sending history is what EVERY turn of every session does, warm or cold.

    Re-measured through a logging proxy, n=3 interleaved, same brief and repo:

        cold  97,049 on the wire   20.3s
        warm  98,954 on the wire   19.9s

    +2% input, wall-clock a wash. Warm is neither the saving the design
    predicted nor the penalty the first measurement claimed -- on cost it is
    very nearly free, and it buys context continuity. It stays OFF by default
    because n=3 on one task shape says nothing about whether continuity helps,
    and a default should turn on something measured to be better, not something
    measured to be harmless.

    `meta` comes back so the caller can fold this pass into the run's totals.
    It is a real executor call: discarding its telemetry would put its tokens
    in nobody's BURN and nobody's COST, which is the same defect class as a
    metric that reads 0 without measuring -- and it would land on EVERY
    delegation now that the pass is on by default.

    Read-only: plan mode, no gate, nothing written.

    The finding: a worker-written gate is the brief restated as an assertion,
    so a wrong requirement becomes a green test defending the defect. Measured
    at one requirement error = 2 runs + ~35 min of GPU, with the worker able to
    see the contradicting evidence the whole time and never asked.

    `preflight_expect` cannot cover this. It proves the gate was red before and
    green after -- which is equally true of correct work and of a defect built
    exactly as specified. The only party positioned to catch it is the one that
    has read the code, and this is the one moment it is cheap to ask.

    Evidence is VERIFIED, not just requested. A path that does not exist is an
    opinion dressed as a citation, and a run stopped by one would teach callers
    to pass `challenge_brief=False` -- which is how a detector that cries wolf
    ends up switched off. Unverifiable objections are reported, never blocking.
    """
    from qd import verdict          # local: verdict imports gittree/invoke too
    # The try covers the EXECUTOR call and nothing else. Wrapping the whole
    # body was how the first version of this shipped, and it swallowed a plain
    # NameError -- every challenge silently returned "no objection" and the
    # feature looked like it worked. A question that cannot fail a run must
    # still be allowed to fail loudly when it is broken.
    try:
        text, _, sid, err, meta = invoke.run_executor(
            profile, f"Review this brief.\n\nBRIEF:\n{task}", work_cwd,
            "plan", timeout, session_id, suffix=verdict.CHALLENGE_SUFFIX)
    except Exception:
        return None, {}, None        # endpoint down, timeout, profile fault
    meta = meta or {}
    if err or not text:
        return None, meta, sid
    parsed = verdict.parse_handoff(text)
    raw = (parsed.get("CHALLENGE") or "").strip()
    if not raw or raw.lower().rstrip(".") in ("none", "no", "n/a"):
        return None, meta, sid
    ev = (parsed.get("EVIDENCE") or "").strip()
    for token in re.split(r"[\s,;]+", ev):
        path = token.strip("`'\"").split(":")[0]
        if path and os.path.exists(os.path.join(work_cwd, path)):
            return (raw, ev), meta, sid
    # Objected but could not point at it. Recorded, never blocking -- see the
    # docstring: a run stopped by an unverifiable citation teaches callers to
    # switch the pass off, and it takes the real objections with it.
    meta["challenge_unverified"] = raw
    return None, meta, sid


def _repo_relative(paths, work_cwd):
    """Hook-logged absolute writes as repo-relative paths (C10).

    Everything the guards compare against -- touch_scope, `git status`, the spec
    list -- is repo-relative, while the hook can only log what it resolved, so
    an un-translated write log matches nothing and attributes nothing. Writes
    outside the tree are dropped: they cannot be a violation IN it, and both
    sides are realpath'd because a /tmp symlink alone would break every match.
    """
    try:
        root = os.path.realpath(work_cwd)
    except Exception:
        return []
    out = []
    for p in paths or []:
        try:
            ap = os.path.realpath(p)
        except Exception:
            continue
        if ap != root and not ap.startswith(root + os.sep):
            continue
        rel = os.path.relpath(ap, root)
        if rel not in out:
            out.append(rel)
    return out


def _created(cwd, changed, pre_status, pre_tracked, writes, hooked):
    """Paths this run CREATED, worker-attributed when a channel exists.

    Shared by the stray line and the fixture check, because both would
    otherwise accuse a caller of files it created itself on the same tree
    while the run was live (C10). Pre-existing means dirty at T0 OR tracked at
    T0: `pre_status` alone is the DIRTY snapshot, so against a clean tree every
    edited file would read as brand new. A new DIRECTORY arrives from `git
    status` as one `dir/` entry and is expanded here -- unexpanded, both rules
    would report a directory name and neither could read the files inside it.
    """
    out = []
    every = None
    for p in changed:
        if p in pre_status or p in (pre_tracked or ()):
            continue
        if p.endswith("/"):
            if every is None:
                every = untracked_files(cwd)
            out += [q for q in every if q.startswith(p)]
        else:
            out.append(p)
    if hooked:
        out = [p for p in out if p in (writes or [])]
    return sorted(out)


def _fixture_files(paths, segments):
    """Created paths living under a fixture-ish directory SEGMENT.

    Matched per path component rather than by substring: `golden_ratio.py` and
    `snapshots.md` are ordinary source, and demanding provenance headers in
    them would teach callers to switch the whole check off.
    """
    segs = set(segments or ())
    out = []
    for p in paths:
        parts = p.replace(os.sep, "/").split("/")[:-1]   # directories only
        if any(part in segs for part in parts):
            out.append(p)
    return out


def _unproven_fixtures(cwd, paths):
    """Fixture files carrying no `captured-from:` provenance (U3.3).

    Imagined fixtures were the field report's worst defect class: a golden file
    the worker INVENTED satisfies any gate written against it, and nothing in
    the diff distinguishes captured bytes from generated ones. Text files carry
    the header themselves (first 10 lines); binary files cannot, so a
    `<path>.src` sidecar carries it for them (C6) -- and that sidecar is a text
    file whose first line IS the header, so it clears the check on its own.
    An unreadable file is not evidence of a missing header and is never
    accused.
    """
    bad = []
    for p in paths:
        full = os.path.join(cwd, p)
        try:
            with open(full, "rb") as f:
                head = f.read(4096)
        except Exception:
            continue
        if b"\0" in head:
            proven = False
            try:
                with open(full + ".src", "rb") as f:
                    first = f.readline().decode("utf-8", "replace").strip()
                proven = first.lower().startswith(_PROVENANCE_HEADER)
            except Exception:
                proven = False
            if not proven:
                bad.append(p)
            continue
        # Re-read as text rather than slicing the 4 KB sniff: ten long lines
        # can outrun it, and a header ruled missing by the buffer size would be
        # a failure the worker cannot fix.
        try:
            with open(full, errors="replace") as f:
                lines = [line for _, line in zip(range(10), f)]
        except Exception:
            continue
        if not any(_PROVENANCE_HEADER in line.lower() for line in lines):
            bad.append(p)
    return bad


def _run_verify_timed(cmd, cwd, timeout):
    """Run a gate command; return (passed, output, ms, timed_out).

    A timeout is reported as its own fact rather than inferred from the output
    text: it decides whether the whole run is refused before the worker starts,
    and a gate that merely PRINTS the timeout sentence must not be able to
    trigger that. `ms` is what a retry pays to run this gate again.
    """
    t0 = time.monotonic()
    # Own session/group + killpg on timeout. `subprocess.run(timeout=)` kills
    # ONLY the direct child -- here the shell -- so a gate like
    # `uv run pytest` left `uv` and `pytest` running under init after the
    # server had already written the refusal and closed the run. Nothing
    # recorded them, they compounded (one per refused run), and they competed
    # for CPU with the very timing used to set this timeout.
    proc = subprocess.Popen(
        cmd, cwd=cwd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        # A11 transport half, and this site is the worse of the two: `shell=True`
        # means ANY command in the project's gate can read fd 0, which under the
        # MCP server is the JSON-RPC input stream. One stray read eats the
        # caller's next request and the transport dies DRAIN_SECONDS later.
        # A gate has no legitimate use for the server's stdin.
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        out = ((stdout or "") + (stderr or "")).strip()
        return (proc.returncode == 0, out,
                int((time.monotonic() - t0) * 1000), False)
    except subprocess.TimeoutExpired:
        invoke._terminate(proc)
        try:                       # drain the pipes the killed tree left open
            proc.communicate(timeout=5)
        except Exception:
            pass
        return (False, f"verify command timed out after {timeout}s",
                int((time.monotonic() - t0) * 1000), True)


# --- Pre-flight sharing (A13) ---------------------------------------------
#
# The pre-flight asks one question: "does `verify` pass BEFORE any work?" For
# a batch, every item is cut from the SAME base commit into its own clean
# worktree, so that answer is identical for every item by construction -- and
# the plugin used to run it N times, concurrently, on one machine. A two-item
# batch put two full suites on the box at once; each ran ~N x slower through
# CPU and DB contention while `verify_timeout_sec` stayed a fixed per-gate
# constant, so both timed out and BOTH refused with "fix the gate or raise
# verify_timeout_sec" -- blaming a gate that was provably fine serially.
#
# That is a scaling failure, not a tuning problem: the wider the fan-out, the
# likelier every item refuses. Fan-out is the feature and fan-out is what
# broke it.
#
# Cached ONLY for worktree runs. A worktree branches from HEAD and is clean,
# so (base sha, gate command) fully determines the answer. An in-tree run sees
# a working tree that anything may have touched between two runs, so it always
# executes its own gate -- a shared verdict there would be a guess.
_PREFLIGHT_LOCK = threading.Lock()
_PREFLIGHT = {}            # (sha, cwd_root, cmd) -> (passed, out, ms, timed_out)


def _preflight_once(cmd, work_cwd, timeout, base_sha, isolated, served=None):
    """`_run_verify_timed`, executed once per (base sha, gate) across a batch.

    Items that share a base and a gate share the verdict. The FIRST caller
    runs it while the others wait on the lock rather than racing it -- waiting
    is what stops them starving each other, so the lock is held across the run
    deliberately, not just around the dict.

    `served`, when a list is passed, gets "ran" or "cached" appended: which of
    the two this caller got. Out-of-band rather than in the returned tuple
    because the tuple IS the verdict, and specs/fleet_spec.py:219 pins that two
    sharers receive the same one -- correctly, since the verdict is identical
    by construction. Whether THIS caller paid for it is a fact about the caller,
    not about the verdict, and the telemetry that needs it is the only reader.
    """
    if not isolated or not base_sha:
        if served is not None:
            served.append("ran")
        return _run_verify_timed(cmd, work_cwd, timeout)

    key = (base_sha, os.path.realpath(os.path.dirname(work_cwd)), cmd)
    with _PREFLIGHT_LOCK:
        if key in _PREFLIGHT:
            if served is not None:
                served.append("cached")
            return _PREFLIGHT[key]
        if served is not None:
            served.append("ran")
        result = _run_verify_timed(cmd, work_cwd, timeout)
        # A timeout is NOT cached: it says the box was busy, not that the gate
        # is red, and caching it would spread one item's bad luck to the whole
        # batch. Every other verdict is a real answer about this commit.
        if not result[3]:
            _PREFLIGHT[key] = result
        return result


def _preflight_forget(base_sha=None):
    """Drop cached verdicts. Called when a batch finishes: the tree can move
    between calls, and a verdict outliving its commit is exactly the stale
    state this project keeps removing."""
    with _PREFLIGHT_LOCK:
        if base_sha is None:
            _PREFLIGHT.clear()
        else:
            for k in [k for k in _PREFLIGHT if k[0] == base_sha]:
                del _PREFLIGHT[k]


def _run_advisory(gates, cwd, timeout):
    """Run the advisory gates once each; return ([{name, ok, ms, head}], skipped).

    Malformed items are counted and skipped, never raised on: these are loose
    indicators a caller bolts onto a delegation, and a typo in one of them must
    not cost a finished run its receipt. `head` is the first output line,
    because an advisory that pastes a full test log is a gate in disguise.
    """
    results, skipped = [], 0
    for g in gates or []:
        name = g.get("name") if isinstance(g, dict) else None
        cmd = g.get("cmd") if isinstance(g, dict) else None
        if (not isinstance(name, str) or not isinstance(cmd, str)
                or not name.strip() or not cmd.strip()):
            skipped += 1
            continue
        ok, out, ms, _ = _run_verify_timed(cmd.strip(), cwd, timeout)
        head = ""
        for line in (out or "").splitlines():
            if line.strip():
                head = line.strip()[:120]
                break
        results.append({"name": name.strip(), "ok": ok, "ms": ms, "head": head})
    return results, skipped


def _retry_prompt(session_id, task, verify, v_out, on_compaction, repeated=False):
    """Build the retry prompt for attempt N+1.

    Returns (prompt_text, action) where action is "none" | "reinject" | "discard".
    """
    # Reflexion: force Qwen to diagnose before editing.
    if repeated:
        reflect = (
            "You have failed the SAME check again: your previous edit did not change this "
            "result, so that approach is wrong. Do not retry a variation of it. State in "
            "one or two sentences (1) why the previous approach cannot work and (2) a "
            "DIFFERENT approach to try, then apply it so the command passes."
        )
    else:
        reflect = (
            "Before editing, state in one or two sentences: (1) the ROOT CAUSE of this "
            "specific failure and (2) the fix you will make. Then apply it so the command "
            "passes."
        )

    failure = (
        f"The verification command failed. This is the real output:\n\n"
        f"```\n{truncate(v_out, VERIFY_CAP)}\n```\n\n"
        f"{reflect}"
    )

    if not was_compacted_since_ack(session_id):
        return failure, "none"

    ack_compaction(session_id)

    if on_compaction == "discard":
        return (
            f"A previous attempt at this task was made in a session that has been "
            f"discarded, so you are starting fresh. Work already on disk may be partial "
            f"or wrong -- read the current state rather than assuming.\n\n"
            f"{failure}\n\nVerify command: {verify}\n\nTask:\n{task}"
        ), "discard"

    return (
        f"{failure}\n\n"
        f"Your conversation history was summarised (compacted), so you may have lost the "
        f"original instructions, and any summary of your earlier work may be inaccurate. "
        f"Do not reconstruct what you think you did -- re-read the files and work from "
        f"what follows.\n\n"
        f"Verify command: {verify}\n\nOriginal task:\n{task}"
    ), "reinject"


# U5.5: what a stored brief remembers -- the half of a call that describes the
# WORK, so a retry replays it without the caller retyping any of it. Reserved
# args, the session and the retry fields themselves are deliberately absent: a
# retry is a new, cold run of the same brief, not a replay of the old run's
# identity.
BRIEF_KEYS = (
    "task", "verify", "touch_scope", "approval_mode", "shell_allow",
    "mcp_allow",
    "shell_feedback", "trust", "max_iterations", "timeout_sec",
    "verify_timeout_sec", "preflight_expect", "worktree", "executor",
    "report_dont_fix", "fixture_provenance", "advisory_gates", "challenge_brief",
    "review_brief", "contract",
    "challenge_warm",
    "result_schema", "on_compaction",
    # U6: a retry replays the same DOCUMENT, not a frozen copy of its text.
    # `amend_brief` is deliberately NOT here: stored, it would re-amend the
    # document on every later retry of that session.
    "brief_file", "vars",
)

_CORRECTION = ("\n\nCORRECTION (from the caller, after reviewing your "
               "previous attempt):\n")


def _resolve_retry(args):
    """Rebuild a call from the brief stored for `retry_of` (U5.5).

    Returns (args, refusal|None). PURE -- one file read and a dict merge -- so
    the server can run it to answer a bad `retry_of` in the submit response
    and the engine can run it again without the run coming out differently.

    Merge rule: the stored brief supplies the defaults, an explicit call arg
    wins. `task: ""` counts as "not passed", which is how a caller re-runs a
    brief it does not want to retype (the schema requires the field).

    The retry runs COLD: no session is resumed, even if the caller passed one.
    The field finding this exists for is that a session which failed carries
    its confusion forward -- resuming into it is how a corrected brief gets
    argued with rather than followed.
    """
    sid = args.get("retry_of")
    if not sid:
        return args, None
    cwd = args.get("cwd") or "."
    brief = load_brief(cwd, sid)
    stored = (brief or {}).get("args")
    if not isinstance(stored, dict) or not stored:
        return args, (
            f"retry_of=\"{sid}\": no stored brief for that session. Briefs are "
            f"written to {os.path.join(cwd, '.qwen-delegate', BRIEFS_DIR)}/ "
            f"when a delegation comes back with a session id -- a project can "
            f"switch that off with \"store_briefs\": false. Send the task "
            f"again, or check that directory for the session you meant."
        )
    merged = dict(stored)
    for key, value in args.items():
        if value is None:
            continue
        if key == "task" and not str(value).strip():
            continue
        merged[key] = value
    merged.pop("session_id", None)
    message = (args.get("retry_message") or "").strip()
    # U6: `amend_brief` REPLACES the correction append -- the amendment IS the
    # correction channel, and appending here too would send the same sentence
    # twice, once as prose and once as a line of the document.
    if message and not args.get("amend_brief"):
        merged["task"] = (merged.get("task") or "") + _CORRECTION + message
    return merged, None


def resolve_call(args, amend=False):
    """Resolve a call to its runnable form (U5.5 + U6): the retry brief first
    (it is what carries `brief_file` on a retry), then the amendment, then the
    playbook. Returns (args, refusal|None).

    `precheck` runs this with amend=False and `_delegate` with amend=True, so
    the amendment is written exactly once per run -- and `_delegate` runs its
    preconditions BEFORE the amend pass, so a refused run never edits the
    caller's document.
    """
    args, refusal = _resolve_retry(args)
    if refusal:
        return args, refusal
    amended = False
    if args.get("amend_brief"):
        # Shape refusals fire on BOTH passes (so the submit answers them
        # synchronously); only the amend=True pass writes.
        if not args.get("retry_of"):
            return args, (
                "amend_brief without retry_of: the amendment is the "
                "correction channel for a STORED brief -- pass "
                "retry_of=<session> with a retry_message, or drop amend_brief.")
        if not args.get("brief_file"):
            return args, (
                "amend_brief: the stored brief for that session has no "
                "brief_file -- there is no document to amend. Use "
                "retry_message alone.")
        message = (args.get("retry_message") or "").strip()
        if not message:
            return args, (
                "amend_brief with no retry_message: an amendment is a dated "
                "correction line, and there is nothing to write.")
        if amend:
            refusal = playbook.amend(args.get("cwd") or ".",
                                     args["brief_file"], message,
                                     time.strftime("%Y-%m-%d"))
            if refusal:
                return args, refusal
            amended = True
    return playbook.resolve(args, amended=amended)


def expand_playbook(args):
    """The server seam (U6): compile a `chain: true` playbook into `chain`
    items BEFORE the submit routes the call. Returns (args, refusal|None).

    Args come back UNCHANGED unless the playbook compiled to a chain (then
    `chain` is set and `brief_file`/`vars` are consumed) -- the single path
    resolves its own brief inside the run, which is what keeps the amendment
    on exactly one code path.
    """
    if not args.get("brief_file"):
        return args, None
    if args.get("chain") or args.get("batch"):
        return args, (
            "`brief_file` describes ONE delegation (or compiles to its own "
            "chain) -- it cannot ride beside `chain`/`batch`. Put brief_file "
            "on the items instead.")
    resolved, refusal = playbook.resolve(dict(args))
    if refusal:
        return args, refusal
    if resolved.get("chain"):
        out = dict(resolved)
        out.pop("brief_file", None)
        out.pop("vars", None)
        return out, None
    return args, None


def worktree_mode(args, cwd=None):
    """The effective worktree mode for a call: arg > project config > "off".

    The ONE resolver for every reader -- the engine's acquisition, run()'s
    in_tree, and the server's repo-lock decision all ask this, because a
    config default the lock path did not consult would take the repo lock
    for a run that then isolates itself (harmless), or worse, skip it for
    one that stays in-tree. "auto" and "off" are the only recognised values;
    anything else reads as "off" -- a typo must not silently isolate work
    the caller expected to land in the tree, nor vice versa: "off" is the
    long-standing default, so unrecognised input degrades to v1 behavior.

    Config, not front matter: .qwen-delegate.json is the CALLER's standing
    file, so a repo where co-work is the norm states "worktree": "auto"
    once. A brief document stays unable to choose where it runs (U6).
    """
    mode = args.get("worktree")
    if not mode:
        mode = _project_config(cwd or args.get("cwd") or ".").get("worktree")
    return "auto" if mode == "auto" else "off"


def _refusal(text, max_iter=_DEFAULT_MAX_ITER):
    """A refusal in delegate()'s return shape. ctx is EMPTY by contract: run()
    routes refusals around the renderer, which needs a populated one."""
    return {
        "status": "refused",
        "session_id": None,
        "trail": [],
        "result_text": text,
        "denials": [],
        "max_iter": max_iter,
        "last_verify": None,
        "ctx": {},
    }


def refusal_receipt(text):
    """A refusal as the caller reads it -- run()'s formatting, extracted so the
    server's synchronous submit path (U5.2) answers with the same bytes."""
    text = text or "refused"
    return text if text.startswith("STATUS:") else f"STATUS: refused\n\n{text}"


def precheck(args):
    """The refusals cheap enough to answer BEFORE a run is spawned (U5.2).

    Returns {"refusal": text|None, "bootstrap_note": str|None, "token": ...}
    -- the token is what lets _delegate tell a result this process produced
    from one a caller made up.
    """
    result = _preconditions(args)
    result["token"] = _PRECHECK_TOKEN
    return result


def _preconditions(args):
    """The precheck body.

    Returns {"refusal": text|None, "bootstrap_note": str|None}.

    Everything here is config reads, one `git rev-parse` and one `git diff`:
    milliseconds, so the async submit can answer them in the tool response,
    where the caller is still looking, instead of filing them as a receipt
    nobody is waiting on yet. What is deliberately NOT here is the PRE-FLIGHT
    gate run: `GATE UNUSABLE` / `GATE VACUOUS` cost up to the whole verify
    budget (an hour at the clamp), which is a run rather than a precondition --
    they land in the receipt file with every other outcome.

    The bootstrap step WRITES the worker rules file, so its notice is returned
    here and threaded into the run through PRECHECK_ARG rather than recomputed:
    a second pass would find the file already in place and the receipt would
    silently lose the line saying it had just been made.
    """
    # U6: resolved BEFORE the trust/gate-expectation checks below, because the
    # front matter can supply `verify` -- and verify's presence is what decides
    # the preflight_expect="green" + trust="self" contradiction refusal.
    args, refusal = resolve_call(args, amend=False)
    if refusal:
        return {"refusal": refusal, "bootstrap_note": None}

    # U5.1 accept-time check, AFTER resolve_call above: result_schema is not
    # read for real until line ~1097, well past every precondition here, but
    # BRIEF_KEYS (:644) includes "result_schema" so a retry_of restores a
    # STORED schema through resolve_call -- checking the raw incoming args
    # would miss a retry that reintroduces the exact schema this refuses.
    # jsonschema.schema_refusal() is the one function both accept points
    # call (see run_query in qd/queries.py), so the keyword list cannot
    # drift between a surface that enters this module and one that never does.
    schema_text = jsonschema.schema_refusal(args.get("result_schema"))
    if schema_text:
        return {"refusal": schema_text, "bootstrap_note": None}

    # U6 size guard: a huge brief costs the caller a filename, but the worker
    # pays it as peak context -- and under the refuse policy that converts to
    # compaction_refused deaths. Refused at submit, where the fix is cheap.
    brief_meta = args.get("_brief")
    if isinstance(brief_meta, dict):
        win = context_window()
        est = int(brief_meta.get("chars") or 0) // 4
        if win and est > win * 0.25:
            return {"refusal": (
                f"BRIEF TOO BIG: {brief_meta.get('path')} composes to "
                f"~{est:,} tokens -- over a quarter of the worker's "
                f"{win:,}-token window before it reads a single file. Split "
                f"the document into `## Step` sections with `chain: true`, "
                f"or consolidate its amendments into the body."
            ), "bootstrap_note": None}

    cwd = args["cwd"]
    verify = args.get("verify")
    # Config-aware for the same reason the engine is (U5.6): a project that
    # declares its gate expectation in .qwen-delegate.json must hit the same
    # contradiction check as one that passes it per call.
    preflight_expect = setting("preflight_expect", args, _project_config(cwd),
                               _global_config(), default="any")

    # --- Precondition: trust (R3: the slider) ---
    # Position resolves like `executor`: call arg > project .qwen-delegate.json
    # 'trust' > machine ~/.qwen-delegate/config.json 'trust' > builtin ("self"/L5).
    # The resolved value is validated below, so a bad config value is refused by
    # name exactly like a bad call arg.
    trust = setting("trust", args, _project_config(cwd), _global_config(),
                    default="self")
    if trust == "auto":
        # "auto" has no gate of its own -- the server cannot judge criticality.
        # Refuse the bare call so the orchestrator classifies THIS task and passes
        # a concrete level. A concrete call arg overrides an "auto" default above,
        # so this only fires when nobody chose.
        return {"refusal": (
            "Trust is \"auto\" — pick per task by criticality and pass it "
            "explicitly. Use trust=\"verified\" for correctness-critical, "
            "irreversible, outward-facing, or security / data-loss / money / "
            "auth work; trust=\"self\" (L5) for low-stakes mechanical or "
            "greenfield work. \"auto\" has no gate of its own (the server "
            "cannot judge criticality), so the orchestrator decides."
        ), "bootstrap_note": None}
    if trust not in ("verified", "self"):
        return {"refusal": (
            f"Trust dial \"{trust}\" is unknown — run refused. Accepted: "
            "\"verified\" (your verify command is the gate) or \"self\" "
            "(L5 full trust — the delegate's own suite is the gate; "
            "verify optional). Intermediate levels are a parked design."
        ), "bootstrap_note": None}

    # --- Precondition: the gate expectation must be satisfiable ---
    # The self-gate ratchet exists to force the preflight RED (an already-green
    # suite proves nothing about the delta), so declaring "green" against a gate
    # generated for that purpose asks for a contradiction -- and would land as a
    # pass that proves nothing, with the one flag that says so switched off.
    if preflight_expect == "green" and trust == "self" and not verify:
        return {"refusal": (
            "preflight_expect=\"green\" contradicts trust=\"self\" with no "
            "verify command: the server-generated gate ratchets its test "
            "count precisely so the preflight comes back RED. Pass your own "
            "`verify` for revision work, or drop preflight_expect."
        ), "bootstrap_note": None}

    # --- Precondition: git repo ---
    if not is_git_repo(cwd):
        return {"refusal": nongit_refusal(cwd), "bootstrap_note": None}

    # --- Bootstrap rules file ---
    bootstrap_note = None
    rules_state, rules_path = worker_rules_status(cwd)
    if rules_state != "ok":
        cmd, path = bootstrap_worker_rules(cwd)
        if not path:
            return {"refusal": bootstrap_failed_refusal(cwd, "IO error"),
                    "bootstrap_note": None}
        bootstrap_note = bootstrap_notice(cmd, path) + " " + graph.bootstrap_line()
        # First delegation in a repo is also the first on a NEW MACHINE, and the
        # settings that decide whether a run comes back whole do not travel with
        # this plugin. Say so once, here, rather than let it surface later as an
        # unexplained truncation someone debugs the repo over.
        try:
            executor_note = doctor.summary_line()
            if executor_note:
                bootstrap_note += " " + executor_note
        except Exception:
            pass

    # --- Precondition: no dirty protected spec ---
    pre_dirty = violated_specs(cwd)
    if pre_dirty:
        return {"refusal": (
            f"STATUS: error\nUncommitted changes in protected spec file(s): "
            f"{', '.join(pre_dirty)}\n\nCommit or stash the spec changes first, "
            f"then delegate."
        ), "bootstrap_note": bootstrap_note}

    return {"refusal": None, "bootstrap_note": bootstrap_note}


def delegate(args):
    """Single-candidate delegation loop.

    Returns dict with keys:
        status, session_id, trail, result_text, denials,
        max_iter, last_verify, ctx
    """
    # The T0 byte-snapshot lives in a per-run tempdir so a revert can restore
    # what the tree actually held at T0 (see snapshot_contents).
    t0_dir = tempfile.mkdtemp(prefix="qd-t0-")
    try:
        return _delegate(args, t0_dir)
    finally:
        shutil.rmtree(t0_dir, ignore_errors=True)


def _delegate(args, t0_dir):
    # --- End-to-end wall clock ---
    # The only duration the run log carried was `duration_ms`, which reads as a
    # run total and is not one: it is ctx["cum"]["ms"], fed by the two
    # accum_stats sites below (:1422 challenge, :1631 attempt), both of them the
    # executor's own self-reported time. Nothing measured the run. So the gate
    # runs, the prefilter and the review pass were not merely unattributed --
    # there was no whole for them to be missing from.
    #
    # Here rather than in delegate(): this is the function that owns the ctx the
    # figure is written onto, and by the time the wrapper's `finally` runs the
    # ctx has already left. That teardown -- rmtree of the T0 snapshot -- is
    # also not the run; it happens after the run's answer is fixed.
    _t0_wall = time.monotonic()

    # --- Preconditions (U5.2: also runnable pre-spawn, see precheck) ---
    # Run FIRST when the server has not already run them: resolve_call's
    # amend=True pass below WRITES the amendment, and a run any precondition
    # refuses must not have edited the caller's document. When the server did
    # precheck, the result rides in on PRECHECK_ARG and is NOT recomputed --
    # the bootstrap step writes a file, and running it twice would drop the
    # notice that says so.
    pre = args.get(PRECHECK_ARG)
    if not isinstance(pre, dict) or pre.get("token") != _PRECHECK_TOKEN:
        pre = precheck(args)
    if pre.get("refusal"):
        return _refusal(pre["refusal"])
    bootstrap_note = pre.get("bootstrap_note")

    # U5.5 + U6: the stored brief, then the amendment (written exactly once,
    # here), then the playbook -- resolved before anything reads an argument.
    # Idempotent with the server's precheck pass, which resolves its own copy.
    args, refusal = resolve_call(args, amend=True)
    if refusal:
        return _refusal(refusal)
    if args.get("chain") and args.get("_brief"):
        # A single-run path cannot execute links. Reachable only when a stored
        # or directly-passed brief compiles to a chain outside the server seam
        # (e.g. retry_of a document later edited to chain: true).
        return _refusal(
            "this brief compiles to a chain (`chain: true`) -- submit it as "
            "its own qwen_delegate call so the server runs the links; "
            "retry_of replays one link, never a document.")
    task = args["task"]
    cwd = args["cwd"]
    verify = args.get("verify")

    # --- Config (project > machine) ---
    # Read FIRST: U5.6 makes it the source of a project's standing defaults,
    # so every resolution below can consult it. It still lands before the
    # pre-flight, which is what the verify budget needs.
    cfg = dict(_global_config())
    cfg.update(_project_config(cwd))

    # U5.6 recipe defaults: a project states its standing preferences once in
    # .qwen-delegate.json instead of every call repeating them. Call args
    # always win -- the config is what a call FALLS BACK to, never what
    # overrides it.
    approval_mode = args.get("approval_mode") or cfg.get("approval_mode") \
        or "auto-edit"
    # Resolved through core/plan.py, NOT with `or`: for a permission list the
    # empty list is the most deliberate answer a caller can give -- no extra
    # capability at all -- and `or` fell through it to whatever the project
    # declared, silently widening a boundary the call had just narrowed.
    shell_allow = setting("shell_allow", args, cfg)
    mcp_allow = setting("mcp_allow", args, cfg)
    # Grant the graph's READ-ONLY subcommands when this run can actually use
    # them. Every condition -- `scoped` only, a graph only, never twice, never
    # `update` -- is in core/pipeline.py with its reason, because the boundary
    # used to be guarded by a GREP for the `if` line that stood here and a
    # widening which kept that line left the whole suite green.
    #
    # The mode is tested HERE as well, and the duplication is deliberate:
    # `graph.read_state` is NOT a pure read and must not be reached under a mode
    # that cannot use the answer. It calls `sidecar_path` -> `runlog_dir`
    # (qd/runlog.py:43), which CREATES `.qwen-delegate/` and WRITES a .gitignore
    # into it -- and that call sits OUTSIDE `read_state`'s own try
    # (qd/graph.py:28), so on an unwritable tree a PermissionError escapes into
    # this call site, which has no guard. Measured: read_state() on a chmod
    # 0500 tree raises PermissionError rather than returning None. Under
    # `auto-edit` -- the DEFAULT mode -- that path never ran before, and every
    # other route to `runlog_dir` in a run is either guarded (`if burn` below)
    # or contractually non-raising (`write_runlog`).
    #
    # The RULE still belongs to graph_shell_grant, which re-tests the mode; what
    # stays here is only the cheap guard in front of the expensive, side-
    # effecting half. The cost is that the widening mutation can no longer reach
    # QGATE_EXTRA, so specs/pipeline_wiring_spec.py stops killing it -- it still
    # dies in specs/pipeline_spec.py and specs/graph_allow_spec.py, which assert
    # on the decision itself rather than on the source text.
    #
    # TRUTHINESS, not `is not None`: the line this replaced was
    # `if approval_mode == "scoped" and graph.read_state(cwd):`, so a sidecar
    # that parses to something falsy -- `{}` is the reachable one -- granted
    # nothing, and this is a migration.
    shell_allow = graph_shell_grant(
        approval_mode,
        approval_mode == "scoped" and bool(graph.read_state(cwd)),
        shell_allow)
    # Default: project config, else 3; clamped 1..10 -- the schema has promised
    # both since v1, and the engine port had silently dropped them.
    max_iter = (args.get("max_iterations")
                # NOT migrated to setting() on purpose: `or` here treats 0 as
                # unspecified, and making 0 a real answer would mean a call
                # could ask for zero attempts and get a run that never invokes
                # the worker. That is a behaviour change, not a consolidation.
                or _project_config(cwd).get("max_iterations")
                or _DEFAULT_MAX_ITER)
    try:
        max_iter = max(1, min(10, int(max_iter)))
    except (TypeError, ValueError):
        max_iter = _DEFAULT_MAX_ITER
    # U4.2: a report is ONE look at the tree. A second attempt would be the
    # worker trying to turn the gate green, which is the exact behavior this
    # param exists to switch off -- so the budget is clamped here rather than
    # asked of the caller, and no retry-prompt branch below can be reached.
    report = bool(args.get("report_dont_fix"))
    if report:
        max_iter = 1
    # When nobody states a timeout, FIT one from this project's own finished
    # runs instead of using a static 900. The static default killed large
    # tasks mid-write, and the documented remedy was a regression formula the
    # caller applied by hand -- with no access to the telemetry it needs. The
    # server has that telemetry. A first run in a fresh project has no history
    # and falls back to the old default, which is the one case where a guess
    # is unavoidable.
    timeout = args.get("timeout_sec") or cfg.get("timeout_sec")
    if timeout:
        timeout = max(30, min(7200, int(timeout)))
    else:
        try:
            timeout = limits.suggested_timeout(cwd, _DEFAULT_TIMEOUT)
        except Exception:
            timeout = _DEFAULT_TIMEOUT
    session_id = args.get("session_id")
    # "refuse" is the default: compaction is the documented fabrication trigger, so a
    # run that reaches it has already exceeded what one delegation can hold honestly.
    # Continuing on a summarised history -- reinject or discard -- buys a result whose
    # provenance nobody can vouch for. Stopping hands the call back to the orchestrator,
    # which can split the task; that is the only fix that addresses the cause.
    on_compaction = args.get("on_compaction") or "refuse"
    if on_compaction not in ("refuse", "reinject", "discard"):
        on_compaction = "refuse"
    wt_mode = worktree_mode(args, cwd)
    touch_scope = args.get("touch_scope")
    # U3.2: what the gate is expected to say BEFORE the worker runs. "red"
    # (greenfield) refuses a gate that already passes; "green" (revision work)
    # stops the preflight demotion crying wolf on every task whose suite is
    # green by definition. An unrecognised value falls back to today's behavior
    # rather than refusing -- same policy as on_compaction, and the schema enum
    # is the front line.
    # The SAME call as in _preconditions, deliberately: this value is resolved
    # in both places and a drifting second copy would be silent. Layers are
    # passed separately rather than as the merged `cfg` because a merge cannot
    # tell "the project said nothing" from "the project said null".
    preflight_expect = setting("preflight_expect", args, _project_config(cwd),
                               _global_config(), default="any")
    if preflight_expect not in ("red", "green", "any"):
        preflight_expect = "any"
    # U5.1: the shape the caller needs OUT of the run. A non-object is treated
    # as absent rather than refused -- the schema belongs to the caller, and a
    # malformed one must not cost a delegation that would otherwise be fine.
    result_schema = args.get("result_schema")
    if not isinstance(result_schema, dict):
        result_schema = None

    # Past the precheck (top of this function) this is a fact, not a
    # question: a non-repo cwd was refused there.
    guard_on = True
    trust = setting("trust", args, _project_config(cwd), _global_config(),
                    default="self")

    # --- Resolve executor profile ---
    profile = resolve(cwd, args.get("executor"))

    # --- Worktree acquisition (M4 seam 1) ---
    work_cwd = cwd
    wt = None
    wt_owned = True          # whether THIS run may dispose of the container
    lent = args.get(WT_ARG)
    if isinstance(lent, dict) and lent.get("path"):
        # A chain link running in the chain's tree (see WT_ARG). Used, never
        # disposed of -- the chain outlives this link and owns the container.
        wt = lent
        wt_owned = False
    elif wt_mode == "auto":
        wt = worktrees.acquire(cwd)
    # Step 5: ONE owner for the container. The three disposal sites below
    # (refusal, green, red) each used to re-derive "may I dispose of this?"
    # from the flag above; a rule spread over three sites is one edit from
    # disagreeing with itself, and here that deletes work rather than leaking
    # a directory. See qd/core/scope.py.
    scope = RunScope(cwd, container=wt, owned=wt_owned)
    work_cwd = scope.work_cwd

    def refuse(text):
        """Refuse from PAST the worktree acquisition.

        The preconditions above run before any container exists; the gate
        refusals below do not, and returning straight out of one would leave
        the worktree and its branch behind for every refused run.

        A LENT tree is exempt: releasing it here would delete the tree the
        chain's earlier links already committed into, on a refusal that only
        concerns this link.
        """
        scope.abandon()
        return _refusal(text, max_iter)

    # U3.1: arg > config > 300, clamped 10..3600. The floor is what keeps a
    # mistyped budget from turning every gate into a timeout refusal; the
    # ceiling is an hour, past which the timeout_sec kill lands first anyway.
    verify_timeout = (args.get("verify_timeout_sec")
                      or cfg.get("verify_timeout_sec")
                      or _DEFAULT_VERIFY_TIMEOUT)
    try:
        verify_timeout = max(10, min(3600, int(verify_timeout)))
    except (TypeError, ValueError):
        verify_timeout = _DEFAULT_VERIFY_TIMEOUT

    # U3.3, opt-in per call (default off until probe P7). The segment list is a
    # project decision -- what a repo calls its fixture directory varies -- so
    # the config value replaces the defaults rather than extending them.
    fixture_provenance = bool(args.get("fixture_provenance"))
    # Through core/plan.py, not `or`: `fixture_globs: []` is a project saying
    # no path segment marks a fixture here, and `or` replaced that answer with
    # the builtin list. Milder than the permission case (the fall-through makes
    # the check stricter, not laxer) but the same bug.
    fixture_segments = setting("fixture_globs", cfg, default=_FIXTURE_SEGMENTS)
    if not isinstance(fixture_segments, (list, tuple)):
        fixture_segments = _FIXTURE_SEGMENTS

    # --- trust="self" (R3): server-generated gate over the delegate's own suite ---
    self_gate = trust == "self" and not verify
    if self_gate:
        verify = _ensure_self_gate(work_cwd)

    # --- Pre-run snapshot ---
    _, pre_sha_full = git(work_cwd, "rev-parse", "HEAD")
    pre_status = snapshot(work_cwd)
    # T0 belongs to the run's scope: it describes the tree this run holds, at
    # the moment it took it. Recorded here rather than at construction because
    # it must be read FROM the container, which did not exist until above.
    scope.mark_start(pre_status, pre_sha_full)
    pre_clean = not pre_status
    # T0 byte contents of every dirty path: a revert must restore what the tree
    # HELD at T0, not what HEAD holds -- checkout-from-sha destroyed pre-run
    # caller edits (and staged the destruction).
    t0_saved = snapshot_contents(work_cwd, pre_status, t0_dir)
    # Tracked at T0, for the touch-scope classifier. `ls-files` answers
    # "tracked NOW": a worker that creates a file and `git add`s it would turn
    # its own new file into a pre-existing-file violation whose revert-from-sha
    # then silently no-ops.
    #
    # DECODED, because this set is compared for MEMBERSHIP against paths that
    # arrive from `snapshot()` -- decoded at the porcelain seam since f75572a.
    # `ls-tree` C-quotes exactly as porcelain does, so the two sides stopped
    # agreeing the moment only one of them was decoded: `pre_tracked` held
    # `"caf\303\251.py"` while `changed` held `café.py`, and touch_scope read
    # the mismatch as "not pre-existing" -- its ONE silent branch, since new
    # files are always allowed. A file the caller declared off-limits was
    # edited, kept, and the run passed with an empty trail. Measured on git
    # 2.53 over 16 name classes x both core.quotePath settings: 11 of 32 pairs
    # disagreed, including plain `café.py` under the stock default.
    # `_created` reads the same set for the opposite question, and got the
    # opposite error: an EDITED pre-existing file read as one this run created.
    rc_t, out_t = git(work_cwd, "ls-tree", "-r", "--name-only",
                      pre_sha_full or "HEAD")
    pre_tracked = {unquote_path(p) for p in out_t.splitlines()} \
        if rc_t == 0 and out_t else set()

    # U6: the brief document is protected by CONTENT, not by the spec guard's
    # base diff -- the amendment dirties it before the run, and a diff against
    # the pre-run sha would convict the amendment as a worker edit on every
    # attempt. Captured right after the T0 snapshot so a revert restores the
    # AMENDED bytes (dirty path) or the HEAD content (clean worktree copy).
    brief_meta = args.get("_brief") if isinstance(args.get("_brief"), dict) \
        else None
    brief_rel = (brief_meta or {}).get("path")
    brief_sha0 = file_sha(work_cwd, brief_rel) if brief_rel else None

    # --- Pre-flight verify ---
    preflight = None
    preflight_out = ""
    gate_ms = 0
    gate_timed_out = False
    self_min = None
    # (kind, ms) per pre-flight, held until the CallLog exists: both gate runs
    # below happen before the ctx that owns it is built. A list rather than
    # `gate_ms` alone because the self-gate ratchet runs the gate a SECOND time
    # and overwrites the first figure -- recorded off `gate_ms` a self-gate run
    # would report one gate run where the caller waited for two.
    preflight_calls = []
    if verify:
        # Whether this run may be served a verdict another item paid for. The
        # rule, and the `_preflight_once` invariant a chain link after the first
        # violates by design, are in core/pipeline.py.
        #
        # The chain position goes in RAW: `preflight_shareable` normalises
        # absence to link 1 itself, so resolving it with `or 1` here as well
        # would be the same decision written in two places, one of which nobody
        # would think to test.
        shareable = preflight_shareable(
            wt is not None, (args.get(CHAIN_ARG) or {}).get("pos"))
        _served = []
        preflight, preflight_out, gate_ms, gate_timed_out = _preflight_once(
            verify, work_cwd, verify_timeout, pre_sha_full, shareable,
            served=_served)
        # A BORROWED verdict is a different fact from a gate run, and billing it
        # like one is a live arithmetic error: `gate_ms` on a cache hit is the
        # FIRST item's duration, and a late hit -- an item that started long
        # after the gate it inherits -- puts more time on the record than the
        # item's own wall clock contains, driving `unaccounted_ms` negative.
        # That signal means double-counting, and here it would be right.
        #
        # 0 ms under its OWN kind, not 0 ms under `gate_preflight`: this file
        # already refuses to write a zero-ms call for a skipped advisory gate
        # because it would be indistinguishable from a gate that ran and was
        # measured at nothing, and the same objection applies here. Nor is it
        # dropped -- a run whose record showed no pre-flight at all, while
        # `gate.preflight_passed` says otherwise, is the silence this whole
        # change removes. A kind is the mechanism ExecutorCall names for
        # precisely this ("a log that rejects a call it does not recognise
        # records less than one that accepts it and labels it honestly");
        # `ExecutorCall.cached` was not reused because it is a TOKEN count that
        # `fresh_prompt` subtracts, not a flag.
        if _served and _served[0] == "cached":
            preflight_calls.append(("gate_preflight_cached", 0))
        else:
            preflight_calls.append(("gate_preflight", gate_ms))
        if self_gate and preflight:
            # Incremental ratchet: an existing suite is already green, so this
            # gate proves nothing -- and every later feature would read as
            # success_but_preflight_passed. Require MORE tests than preflight
            # found; the gate now binds on the delta, and preflight re-runs red.
            self_min = ratchet_minimum(preflight_out)
            verify = _ensure_self_gate(work_cwd, min_override=self_min)
            preflight, preflight_out, gate_ms, gate_timed_out = \
                _run_verify_timed(verify, work_cwd, verify_timeout)
            # Never cached: the ratchet re-run goes straight to
            # `_run_verify_timed`, so this run paid for it.
            preflight_calls.append(("gate_preflight", gate_ms))

    # U3.1: fail fast. The same command runs after every attempt, so a gate that
    # cannot finish inside its budget has already decided the run -- left alone
    # it burns max_iter attempts of free tokens and then reports its own timeout
    # as the worker's failure (measured: a good delivery filed as gate_suspect).
    if gate_timed_out:
        return refuse(
            f"GATE UNUSABLE: the verify command timed out after "
            f"{verify_timeout}s BEFORE the worker ran -- fix the gate or raise "
            f"verify_timeout_sec; every retry would pay this."
        )

    # U3.2: a gate declared red that comes back green cannot prove anything the
    # run does. Refusing here costs nothing; discovering it afterwards costs a
    # whole delegation and hands back a pass nobody can read.
    if preflight and preflight_expect == "red":
        return refuse(
            "GATE VACUOUS: preflight already passes and preflight_expect="
            "\"red\" -- the gate cannot prove the work happened. Tighten the "
            "gate or drop preflight_expect."
        )

    # --- Refs snapshot (pre-run) ---
    refs_before = refs.snapshot(cwd)

    # U5.6: the project's standing instruction, appended to the task itself so
    # it rides every path the task rides -- the first prompt, and the
    # compaction re-injections that re-send the task verbatim. A briefing
    # discipline every task in the repo has to repeat is a discipline the
    # caller pays for on every call; this is where it belongs. Kept out of the
    # STORED brief (U5.5), which would otherwise stack a copy per retry.
    base_task = task
    task_suffix = cfg.get("task_suffix")
    if isinstance(task_suffix, str) and task_suffix.strip():
        task = f"{task}\n\n---\n{task_suffix.strip()}"

    # --- Prefix layers (qd/core/prompt.py) ---
    # Composed, not concatenated. That module landed for SUFFIXES only and the
    # prefix half of its own docstring stayed aspirational, so the prefixes went
    # on being assembled by `+` at three sites across two files -- which is the
    # exact scatter it exists to end: five `if`s deciding what a worker was told
    # and nowhere that could answer "what is this worker about to be sent?".
    # Adding the carried-result layer by concatenation would have made it six.
    #
    # ORDER is the argument, not the ergonomics: the inherited result is the
    # older fact and the shell verdict is about what happened since, and the
    # feedback layer's own last sentence is "continue the task below" -- so
    # nothing may sit between it and the task.
    feedback = (args.get("shell_feedback") or "").strip()
    feedback_prefix = ""
    if feedback:
        feedback_prefix = (
            "APPROVAL RESULT for shell commands you requested earlier "
            "(from the manager reviewing them):\n"
            f"{feedback}\n"
            "Respect these: do NOT retry a denied command; use the allowed ones or an "
            "alternative. Now continue the task below.\n\n---\n\n"
        )
    prompt = prompt_compose(
        task,
        prefixes=(_carried_prefix(args.get(CARRIED_ARG)), feedback_prefix),
    )

    # --- Initial session tracking ---
    sessions = [session_id] if session_id else []
    send_suffix = False

    # --- ctx (C3 shape) ---
    ctx = {
        "cwd": cwd,
        "guard_on": guard_on,
        "preflight": preflight,
        "preflight_out": preflight_out,
        "pre_status": pre_status,
        "pre_sha": pre_sha_full,
        "pre_clean": pre_clean,
        "peak": 0,
        "meta": {},
        # What the fan-out actually got. Resolved here because this is where
        # the profile is resolved; rendered only on a batch (see verdict.py).
        "dispatch": profile.get("dispatch"),
        "endpoint": profile.get("endpoint_cfg"),
        "batch_size": args.get("_batch_size", 1),
        "timeout": timeout,
        "approval_mode": approval_mode,
        "task": task,
        "verify": verify,
        "cum": cum_zero(),
        "sessions": sessions,
        "reinjects": 0,
        "discards": 0,
        "on_compaction": on_compaction,
        "session_hint": session_id,
        "bootstrap_note": bootstrap_note,
        "notes": "",
        "worktree": None,
        "merge": None,
        "challenge": None,
        "calls": CallLog(),
        # The executor profile, so the run log can price each call KIND.
        # Not rendered anywhere -- carried for the log, which is the only
        # reader that needs to turn tokens into money per call.
        "profile": profile,
        "graph_line": None,
        "refs_added": [],
        "cost_usd": 0.0,
        # The RESOLVED name, not the call arg: with a machine-file default the
        # arg is None, and the ledger labeled every default-routed run
        # "qwen-local" whatever profile actually served it (seen at vLLM
        # cutover, 2026-07-31).
        "executor": profile["name"],
        "trust": trust,
        "unrestorable": [],
        # C10 attribution. Empty + "none" is the honest reading of a run with
        # no evidence channel, and every consumer renders nothing for it.
        "writes": [],
        "attribution": "none",
        "scope_unattributed": [],
        "spec_unattributed": [],
        # C3 gate hygiene: what the gate cost, what budget it had, and what the
        # caller said it should say beforehand.
        "gate_ms": gate_ms,
        "verify_timeout_sec": verify_timeout,
        "preflight_expect": preflight_expect,
        # Past HALF the budget -- a pre-flight there is paid AGAIN by every
        # attempt, so at max_iter 3 the gate alone can outlast the work it is
        # grading. core/pipeline.py carries the arithmetic and why the constant
        # is not x100 or x1000. `bool(verify)` stays HERE on purpose: whether
        # there is a gate at all is this loop's question, not the threshold's.
        "gate_slow": bool(verify) and gate_is_slow(gate_ms, verify_timeout),
        # U5.5: this run is a corrected re-run of another session, started cold.
        "retry_of": args.get("retry_of"),
        # U6: which document briefed this run -- the receipt names it and the
        # ledger groups by it. None whenever no brief_file was involved.
        "brief": brief_meta,
        # C3 features (U4.1/U4.2/U4.3): every one of these renders nothing when
        # its param was never passed.
        "report": report,
        # The worker's OWN reported findings, parsed out of its reply. Not to be
        # confused with `detections` below, which is what the detectors observed
        # about the tree -- one is the worker's account, the other is evidence
        # taken against it.
        "findings": None,
        # Findings from qd/features/detectors/, and the KINDs of any detector
        # that raised. Empty means every detector ran and had nothing to say;
        # a KIND in `detections_failed` means nothing is known either way.
        "detections": [],
        "detections_failed": [],
        "fixtures_unproven": [],
        # U5.1: the conforming result block (verbatim) and, when it did not
        # conform, what was wrong with it.
        "result_json": None,
        "result_errors": [],
    }
    # The pre-flight ran above, before this CallLog existed, so it is recorded
    # at the first moment it can be. `gate_ms` already drove the GATE SLOW
    # receipt line (qd/verdict.py:515) and stopped there; the same measurement
    # now reaches the record, so the receipt and the log are one account of one
    # gate run rather than two.
    #
    # A verdict served from the batch cache is recorded here too, under
    # `gate_preflight_cached` at 0 ms -- named, so the borrower's record does
    # not read as a run with no pre-flight, and priced at nothing, because the
    # borrower spent nothing on it. See the pre-flight block above.
    for _kind, _ms in preflight_calls:
        ctx["calls"].record(_kind, {"stats": {"ms": _ms}})

    chain = args.get(CHAIN_ARG)
    if isinstance(chain, dict):
        ctx["chain"] = {"pos": chain.get("pos"), "of": chain.get("of")}
    # U5.2: the submitted run's id, so the completion record can be paired with
    # the `running` record the submit wrote. Absent for a direct engine call --
    # then nothing was ever left open to pair with.
    if args.get(RUN_ID_ARG):
        ctx["run_id"] = args[RUN_ID_ARG]

    # --- Challenge the brief (A23) ---
    # Placed AFTER the gate refusals and BEFORE the first attempt: a gate that
    # cannot run is a worse problem than a brief that is wrong, and there is no
    # point asking about a brief for a run that GATE UNUSABLE will refuse
    # anyway. This is the last moment before any tokens are spent building.
    # DEFAULT ON. A23 is the failure a caller cannot see from the receipt: a
    # wrong requirement becomes a green test defending the defect, and every
    # signal downstream reads as success. Opt-in safety does not get opted into
    # -- the same measurement A14 rests on -- so the flag is inverted here and
    # `challenge_brief: false` is how a caller declines it.
    #
    # Resolved with explicit None checks rather than `or`: `false` is a real
    # answer, and `or` chaining would fall through it to the next layer and
    # silently re-enable what the caller just switched off.
    challenge = setting("challenge_brief", args, cfg, _global_config(),
                        default=True)
    # G2: at a chain's head the subject is the CHAIN, not the link. Every
    # existing rule still applies -- it runs once, it refuses only on evidence
    # naming a path that exists, and a diagnosis run is exempt -- because it is
    # the same pass with a wider brief, not a second mechanism.
    challenge_subject = args.get(CHAIN_BRIEF_ARG) or task
    # A diagnosis is exempt. `report_dont_fix` asks "why does this fail?" -- a
    # brief that makes no claim about the code, so there is nothing for the
    # code to contradict. One attempt is the whole shape of a report run, and
    # doubling its executor calls to ask an unanswerable question is pure cost.
    objection = None
    if challenge and not report:
        objection, ch_meta, ch_session = _challenge_brief(
            profile, challenge_subject, work_cwd, timeout, session_id)
        # Folded into the run's totals BEFORE the refusal branch, so a caller
        # who does get a receipt sees what the pass cost. accum_stats does not
        # touch `attempts`, so the attempt count still means attempts.
        accum_stats(ctx["cum"], (ch_meta or {}).get("stats"), attempt=False)
        ctx["calls"].record("challenge", ch_meta, session=ch_session)
        ctx["challenge"] = {"ran": True, "warm": False,
                            "unverified": (ch_meta or {}).get("challenge_unverified")}
        # --- Carry the challenge session into the build (opt-in) ---
        # OFF by default, but NOT on cost: re-measured at +2% input and the same
        # wall-clock against a cold build (see _challenge_brief; the old "+50%"
        # was a double-counted telemetry number). `challenge_warm: true` for
        # callers who want the builder to inherit the reading the review did.
        #
        # The hand-off line is DETERMINISTIC, written here, never asked of the
        # model. The challenge prompt ends with "Do NOT build anything yet",
        # and that instruction is now in the history the build inherits -- so
        # something has to retract it, and a retraction the model composes for
        # itself is not a retraction, it is a hope.
        if ch_session and _warm_challenge(args, cfg):
            session_id = ch_session
            task = CHALLENGE_CLEARED + task
            ctx["challenge"]["warm"] = True
            ctx["session_hint"] = session_id

    # Step 6: WHAT WAS ASKED FOR, resolved once and frozen. Built here because
    # this is where every layer is in hand; read by the features below, which no
    # longer take loose arguments about the caller's intent.
    plan = RunPlan.build(args, _project_config(cwd), _global_config(),
                         fixture_default=_FIXTURE_SEGMENTS,
                         brief_path=brief_rel)

    # --- The gates (step 4) ---
    # ONE call, and deliberately outside the challenge branch above: it used to
    # sit inside it, so `challenge_brief: false` would have silently switched
    # off every other gate as well -- a caller declining one opinion losing all
    # the refusals with it. A1's red gate registers alongside `challenge` and
    # this call site does not change to accept it.
    #
    # Placed here, after the preflight, because that is where the evidence
    # exists: the red gate judges the gate's own output, and the challenge's
    # objection was gathered above.
    _decision = gates.run_all(gates.GATES, gates.GateRun(
        objection=objection,
        gate_output=preflight_out,
        expect=preflight_expect,
        contract_path=plan.contract_path,
        contract_tests=spec_files(work_cwd),
        work_cwd=work_cwd))
    if not _decision.ok:
        return refuse(_decision.reason)

    # --- Live limits (config: project > machine > builtin) ---
    # Both are ceilings on how wrong a run may go before we stop paying for it,
    # and both are off the gate's critical path: neither can turn a failing run
    # green, only end one early.
    budget = cfg.get("burn_budget", _DEFAULT_BURN_BUDGET)
    try:
        budget = int(budget or 0)
    except (TypeError, ValueError):
        budget = _DEFAULT_BURN_BUDGET
    # One meter for the whole delegation, not one per attempt: three attempts
    # under a per-attempt budget could spend three times the ceiling, and what
    # a caller means by "this delegation cost X" is the total.
    burn = limits.BurnLimit(budget) if budget else None
    stall_after = invoke_stall_seconds(cwd, cfg)
    ctx["burn_budget"] = budget
    ctx["stall_after"] = stall_after

    # U4.4/C11 heartbeat. Wired ONLY alongside the burn limit, never on its own:
    # run_executor switches to stream-json for any on_line, and the streaming
    # adapter emits no `stats` -- so a heartbeat attached to a burn_budget=0 run
    # would silently cost it the tool counts and the bySource token split that
    # batch mode is kept for. The sidecar lands in the SUBMIT cwd, not the
    # work tree: the poller was handed <cwd>/.qwen-delegate/progress.json at
    # submit time, and a worktree run's pulse written inside its container is
    # a heartbeat nobody is watching. runlog_dir's self-ignoring .gitignore
    # keeps it out of the guards' view of the caller's tree.
    progress = None
    if burn:
        runlog_dir(cwd)
        progress = limits.Progress(cwd, session_id=session_id,
                                   run_id=args.get(RUN_ID_ARG))
        # Truncate the sidecar NOW, at submit, so the gap between "submitted"
        # and the first token shows this run starting rather than a previous
        # run's final state sitting at the path the submit just advertised.
        progress._write()
    on_line = limits.compose(burn, progress) if burn else None

    # U1.4: run auto-edit as yolo + the PreToolUse hook so attribution exists
    # outside scoped mode. Default ON (probe P1, 2026-07-29: confirmed behaviorally
    # free -- same outcome/gate as plain auto-edit, only adds the C10 attribution
    # log; ~1s overhead). Opt out per-project with "autoedit_via_hook": false.
    # Off, run_executor is called exactly as before -- argv and env byte-identical.
    observe_hook = (cfg.get("autoedit_via_hook", True)
                    and approval_mode == "auto-edit")
    # Whether ANY channel can say "the worker wrote this". Without one, a
    # changed file is just a changed file -- the guards below must not accuse.
    hooked = approval_mode == "scoped" or observe_hook

    trail = []
    result_text = ""
    denials = []
    denials_all = []
    blocked_all = []
    writes_all = []
    allowed_all = []
    last_verify = None
    no_progress = False
    prev_v_out = None

    def schema_gate(attempt, note):
        """U5.1: check the result contract on an attempt that would otherwise
        END the loop, and record `note` as that attempt's trail line.

        Only on an ending attempt, never beside a red gate: the gate is the
        stronger signal, and replacing its output with a complaint about a
        JSON block would spend the retry on the formatting instead of the bug.
        Returns "ok" | "retry" | "stop".
        """
        nonlocal prompt
        if result_schema is None:
            trail.append(f"attempt {attempt}: {note}")
            return "ok"
        value, raw, err = jsonschema.last_json_block(result_text)
        errors = [err] if err else jsonschema.validate(value, result_schema)
        ctx["result_errors"] = errors
        ctx["result_json"] = None if errors else raw
        if not errors:
            trail.append(f"attempt {attempt}: {note}")
            return "ok"
        trail.append(f"attempt {attempt}: {note}; RESULT SCHEMA invalid -- "
                     + "; ".join(errors[:3]))
        if attempt >= max_iter:
            return "stop"
        # Fed back like a red gate, and for the same reason: the worker can
        # only fix what it is told by name. Every violation is listed with its
        # path, and the schema is repeated so the reply does not depend on the
        # worker remembering a suffix from several turns ago.
        prompt = (
            "Your reply is missing a usable result block. Each of these must "
            "be fixed:\n\n"
            + "\n".join(f"- {e}" for e in errors)
            + "\n\nThe work itself is not in question here -- do not change "
              "the code. Re-send your reply ending with a fenced ```json "
              "block that conforms to this schema:\n\n"
            + json.dumps(result_schema, indent=2)
        )
        return "retry"

    for attempt in range(1, max_iter + 1):
        # The machine-read tail, composed in one place (qd/core/prompt.py).
        # WANTED on attempt 1 and after a compaction, and otherwise not: those
        # are exactly the moments the worker has no other way to learn what
        # shape its answer must take. The riders go with it or not at all --
        # a machine-read instruction with nowhere to be read is worse than
        # absent, because it spends tokens AND looks like the contract was
        # stated.
        suffix = prompt_tail(
            HANDOFF_SUFFIX,
            wanted=(attempt == 1 or send_suffix),
            findings=FINDINGS_SUFFIX if report else None,
            schema=(jsonschema.schema_suffix(result_schema)
                    if result_schema is not None else None))
        send_suffix = False
        if progress is not None:
            # Records alone cannot tell a poller attempt 3 of 3 from an attempt
            # 1 that wedged.
            progress.attempt = attempt

        # --- Invoke executor ---
        compaction_before = compaction_counts(session_id)
        text, denials, sid, err, meta = run_executor(
            profile, prompt, work_cwd, approval_mode,
            timeout=timeout, session_id=session_id,
            verify=verify,
            shell_allow=shell_allow,
            mcp_allow=mcp_allow,
            suffix=suffix,
            compaction_policy=on_compaction,
            on_line=on_line,
            stall_after=stall_after,
            observe_hook=observe_hook,
        )

        meta = meta or {}
        # One line per executor call, beside the running sum. The sum answers
        # "what did this run cost"; only the per-call log answers "what did the
        # CHALLENGE cost", which is the question that decides whether a
        # default-on pre-pass is worth keeping.
        ctx["calls"].record("attempt", meta, session=sid, err=err)
        # Accumulate across attempts: every QGATE log is fresh per attempt, so
        # binding only the last one silently drops earlier attempts' evidence
        # -- and for the write log that means un-attributing real worker edits.
        for b in meta.get("blocked") or []:
            if b not in blocked_all:
                blocked_all.append(b)
        for w in meta.get("writes") or []:
            if w not in writes_all:
                writes_all.append(w)
        for a in meta.get("allowed") or []:
            if a not in allowed_all:
                allowed_all.append(a)
        for d in denials or []:
            if d not in denials_all:
                denials_all.append(d)
        meta["blocked"] = list(blocked_all)
        meta["writes"] = list(writes_all)
        meta["allowed"] = list(allowed_all)
        ctx["meta"] = meta
        ctx["writes"] = _repo_relative(writes_all, work_cwd)
        ctx["attribution"] = "hook" if hooked else "none"
        # A HIGH-WATER mark across attempts, not the latest attempt's figure --
        # core/pipeline.py names the three readers that lose the spike if this
        # ever becomes an assignment.
        ctx["peak"] = peak_high_water(ctx.get("peak"), meta.get("peak"))
        accum_stats(ctx["cum"], meta.get("stats"))

        if sid:
            session_id = sid
            if progress is not None:
                # A cold run only learns its session here, and a sidecar whose
                # session stays null is a heartbeat nobody can match to a run.
                progress.session_id = sid
            if sid not in ctx["sessions"]:
                ctx["sessions"].append(sid)

        # --- Executor error ---
        if err:
            trail.append(f"attempt {attempt}: {err}")
            # The kill is the one failure whose remedy is a NUMBER, and the
            # receipt can compute it (limits.timeout_line) instead of telling
            # the caller to derive one.
            if "timed out after" in err:
                ctx["timed_out"] = True
            if "run stopped:" in err:
                # A stopped run's output is exactly what was not graded; on
                # attempt 2+ result_text still holds the PREVIOUS attempt's
                # prose, which must not present under STATUS: stopped (mirror
                # of the compaction clear below).
                result_text = ""
            break

        # --- Compaction, under the refuse policy: stop, do not retry ---
        # Checked before anything reads `text`: past a compaction the worker's report
        # is exactly what cannot be trusted, so it must not reach a gate, a spec
        # check, or the receipt as if it were ordinary output.
        if on_compaction == "refuse":
            done_after, tried_after = compaction_counts(sid or session_id)
            if (done_after > compaction_before[0]
                    or tried_after > compaction_before[1]):
                blocked = done_after == compaction_before[0]
                trail.append(
                    f"attempt {attempt}: COMPACTION "
                    + ("blocked -- run stopped before the summary"
                       if blocked else
                       "fired -- history was summarised; result not trusted"))
                ctx["compaction_blocked"] = blocked
                result_text = ""
                break

        result_text = text or ""

        # --- Post snapshot (shared by touch scope + C8 prefilter) ---
        post_snap = snapshot(work_cwd)
        changed = [
            p for p in set(list(post_snap.keys()) + list(pre_status.keys()))
            if post_snap.get(p) != pre_status.get(p)
        ]

        # --- Guards: the things that fail an ATTEMPT (features/guards/) ---
        # A guard detects, reverts if it must, and RETURNS a violation. The
        # loop owns the control flow -- a guard cannot `continue` a loop it does
        # not own, and that is what makes the retry-or-give-up rule exist once
        # here instead of once per guard.
        scope.mark_attribution(pre_tracked, hooked)
        scope.mark_brief(brief_sha0)
        scope.mark_t0_bytes(t0_saved)
        scope.mark_created(_created(work_cwd, changed, pre_status, pre_tracked,
                                    ctx.get("writes"), hooked))
        _v = guards.first(scope, plan,
                          Attempt(n=attempt, of=max_iter, changed=changed,
                                  writes=ctx.get("writes") or []))
        # Attribution findings are the scope's; the receipt reads them here.
        ctx["scope_unattributed"] = list(scope.scope_unattributed)
        ctx["unrestorable"] = list(scope.unrestorable)
        ctx["spec_unattributed"] = list(scope.spec_unattributed)
        if _v is not None:
            # Notes are recorded whether or not anything failed: an
            # unattributed spec change is the caller's own edit, which the
            # receipt owes them and must not punish the worker for.
            trail.extend(_v.notes)
        if _v is not None and _v.trail is not None:
            if _v.kind == "fixture_provenance":
                ctx["fixtures_unproven"] = scope.unproven_fixtures(
                    plan.fixture_segments)
            trail.append(_v.trail)
            if _v.prompt and attempt < max_iter:
                prompt = _v.prompt
                # The rider mutates session state, so it stays with the loop: a
                # guard says "this correction is useless to a worker that has
                # forgotten the task", and the loop decides what that costs.
                if _v.rider and was_compacted_since_ack(session_id):
                    ack_compaction(session_id)
                    send_suffix = True
                    if on_compaction == "discard":
                        ctx["discards"] += 1
                        session_id = None
                    else:
                        ctx["reinjects"] += 1
                    prompt += (
                        f"\n\nYour conversation history was summarised (compacted), so "
                        f"you may have lost the original instructions and any summary of "
                        f"your earlier work may be inaccurate. Re-read the files; do not "
                        f"reconstruct it.\n\nOriginal task:\n{task}"
                    )
                continue
            break

        # --- No verify: unverified success ---
        if not verify:
            action = schema_gate(attempt, "no verify supplied")
            if action == "retry":
                continue
            break

        # --- C8 prefilter (advisory) — after executor, before gate ---
        qwen_files = [p for p in changed if "_qwen." in p]
        prefilter_out = None
        prefilter_failed = False
        if qwen_files:
            test_cmd = detect_test_cmd(cwd)
            if test_cmd:
                # The detected command runs VERBATIM. It used to be prefixed
                # with `./` (`f"./{test_cmd}"`, unless it already began with a
                # dot), which was in the very first landing of this file
                # (93bf235) with no rationale recorded. The `startswith(".")`
                # guard says what it was aimed at: the ONE detector branch that
                # answers with a repo-relative script path, `venv/bin/pytest`
                # / `.venv/bin/pytest`. It was wrong for every other answer
                # bootstrap.detect_test_cmd could already give at that same
                # commit -- `npm test`, `cargo test`, `go test ./...`,
                # `bundle exec rspec`, `python -m pytest` -- and for the
                # stdlib-discovery branch and the `test_command` config branch
                # (`make check`, an absolute path) added since.
                #
                # Reproduced 2026-08-07 on a plain stdlib-layout fixture (a
                # tests/ folder, no venv), driving this loop:
                #
                #   detected: python3 -m unittest discover -s tests -p "*.py" -v
                #   ran:      ./python3 -m unittest discover ... calc_qwen.py
                #   shell:    /bin/sh: 1: ./python3: not found     (exit 127)
                #
                # 127 is non-zero, so `prefilter_failed` was UNCONDITIONALLY
                # true on every such project -- the prefilter never ran at all
                # -- and the branch below put `NOTES: self-tests failing` on
                # the receipt of a run whose gate passed on attempt 1. A green
                # run reporting a failure that never happened, plus a
                # correction telling the worker to root-cause a command that
                # does not exist.
                #
                # Nothing needs the prefix, including the branch it was aimed
                # at: a word containing `/` is resolved by the shell as a path
                # relative to cwd without help (measured -- `sh -c
                # 'venv/bin/pytest -q calc_qwen.py'` with cwd set runs the
                # script). And the rest of this project already agrees: with
                # the SAME string from the SAME function, `_ensure_self_gate`
                # interpolates it into the gate script bare, and
                # bootstrap.render_worker_rules prints it to the worker bare
                # under "Use exactly that command" -- so the prefix also made
                # the engine run something other than what it told the worker
                # to run. Pinned by Prefilter's enable_prefilter_on_path tests,
                # which reach the stub as a command NAME; the pre-existing ones
                # install `venv/bin/pytest` and so only ever exercised the one
                # branch where the prefix was harmless.
                tc = test_cmd
                # Nothing timed this before -- no t0 wrapped it at all. Its
                # budget is 60s, and the except branch below is reached BY that
                # timeout, so the unrecorded case was the expensive one: a full
                # minute of a run's wall-clock belonging to nobody.
                _t0_pf = time.monotonic()
                # Each path quoted SEPARATELY, because every one of them is a
                # filename the WORKER chose and this line runs with shell=True.
                # That made it worker-to-server arbitrary command execution on
                # the default path -- not the hostile-config story `test_dir`
                # was (qd/bootstrap.py, a70c83a), which needed the attacker to
                # write a config file first. Creating files IS the job we hire
                # the worker for. Reproduced 2026-08-06 against this loop:
                #
                #   worker writes  x$(touch${IFS}PWNED)_qwen.py
                #     -> ./venv/bin/pytest -q -o "..." x$(touch${IFS}PWNED)_qwen.py
                #     -> PWNED created; pytest was handed the argument `x_qwen.py`
                #
                # git's own path quoting was never a defence, and this is the
                # only defence there is. Measured, git 2.53: porcelain C-quoted
                # a path only when it held a space, a DOUBLE quote, a backslash
                # or a control byte (plus non-ASCII under the default
                # core.quotePath=true -- that flag changes the non-ASCII case
                # and nothing else). `;` `$` backtick `|` `&` `*` `>` and the
                # SINGLE quote all arrived bare; and a path git did quote landed
                # inside real double quotes, where `$(...)` and backticks still
                # expand. Both halves executed.
                #
                # As of the gittree fix those quotes are decoded at the parse
                # seam, so every path now arrives here as a REAL filename --
                # which removes the accident that made a space-bearing name
                # survive an unquoted join, and leaves shlex.quote as the whole
                # of the protection. That is the right shape (git's escaping was
                # never a security boundary), but it means this line must never
                # go back to interpolating a path raw.
                #
                # shlex.quote and not a blanket f'"{p}"': it adds quotes only
                # where the value needs them, so ordinary `calc_qwen.py` reaches
                # pytest as the identical argument it always did -- and a
                # double-quote wrapper would not even be a fix here, since
                # command substitution survives it.
                #
                # `tc` is deliberately NOT quoted: it is a whole command line
                # with its own flags (`venv/bin/pytest -q -o "..."`), the
                # project declaring how to run its own tests, which is the same
                # by-design verdict a70c83a reached for `test_command`.
                try:
                    pv = subprocess.run(
                        f"{tc} {' '.join(shlex.quote(p) for p in qwen_files)}",
                        cwd=work_cwd, shell=True,
                        capture_output=True, text=True, timeout=60,
                        env=os.environ, stdin=subprocess.DEVNULL,
                    )
                    prefilter_out = (
                        ((pv.stdout or "") + (pv.stderr or "")).strip()
                    )[:2000]
                    prefilter_failed = pv.returncode != 0
                except Exception:
                    prefilter_out = "prefilter timed out or errored"
                    prefilter_failed = True
                ctx["calls"].record(
                    "prefilter",
                    {"stats": {"ms": int((time.monotonic() - _t0_pf) * 1000)}})

        # --- Run verify ---
        if self_gate:
            # overwrite any worker edit to the gate, keeping the ratcheted bar
            _ensure_self_gate(work_cwd, min_override=self_min)
        passed, v_out, verify_ms, _ = _run_verify_timed(
            verify, work_cwd, verify_timeout)
        # This `ms` was discarded into `_`. It is paid once per attempt, so at
        # max_iter 3 a slow suite is three full gate runs -- routinely more of
        # the run than the work being graded -- and none of it was worth
        # anything in the log. `err` carries the color, because a red gate and a
        # green one that cost the same second are not the same fact.
        ctx["calls"].record("gate_verify", {"stats": {"ms": verify_ms}},
                            err=None if passed else "gate failed")

        if report:
            # On a report run the gate output IS the deliverable: red is the
            # reproduction the caller asked for, green is the evidence that the
            # reported problem does not reproduce here. Kept on BOTH colors,
            # because the ordinary path saves it only on failure and would
            # otherwise hand back a report with its findings missing.
            last_verify = v_out
            ctx["report_gate_green"] = passed

        if passed:
            if prefilter_failed:
                ctx["notes"] = "self-tests failing"
            action = schema_gate(attempt, "VERIFY PASS")
            if action == "retry":
                continue
            break

        trail.append(f"attempt {attempt}: verify failed")
        last_verify = v_out

        if prefilter_failed and qwen_files:
            cmd_line = f"{tc} {' '.join(qwen_files)}"
            out_display = prefilter_out or "(no output)"
            last_verify = (
                f"{v_out}\n\n"
                f"Also: your own self-tests failed ({cmd_line}):\n"
                f"{out_display}"
            )

        # --- Gate suspect: identical to preflight output ---
        if preflight is False and v_out.strip() == (preflight_out or "").strip():
            trail[-1] = (
                f"attempt {attempt}: verify failed -- output IDENTICAL to preflight"
            )
            break

        # --- Build retry prompt ---
        repeated = (
            prev_v_out is not None
            and v_out.strip() == prev_v_out.strip()
        )
        # G3: kept past the loop. The worker is already told (Reflexion, in
        # _retry_prompt); until now the CALLER was not, so a run that produced
        # the same bytes three times was indistinguishable from one that failed
        # once and was worth another go. `= repeated`, not `or repeated`: what
        # matters is the state the run ENDED in, not whether it ever stalled.
        no_progress = repeated
        prev_v_out = v_out

        if attempt < max_iter:
            prompt, action = _retry_prompt(
                session_id, task, verify,
                last_verify, on_compaction,
                repeated=repeated,
            )
            if action != "none":
                ctx[action + "s"] += 1
                send_suffix = True
                if action == "discard":
                    session_id = None
            continue

    if progress is not None:
        # A run that ended leaves its last "running" snapshot on disk forever
        # otherwise, and a poller cannot tell that from a run still going.
        progress.finish()

    # --- Determine status ---
    # The whole cascade lives in qd/core/status.py: a pure function of the
    # trail and three flags, where ORDER IS PRECEDENCE and every branch is a
    # more specific diagnosis than the one below it. It was an elif chain here,
    # testable only by driving a full delegation, so its rules were asserted
    # incidentally by tests about other things.
    status = run_status(trail, no_progress=no_progress, report=report,
                        preflight=preflight, preflight_expect=preflight_expect)

    # --- Tree facts (C3) ---
    # Captured from the tree the run ACTUALLY used, and BEFORE the worktree
    # commit/release below -- after it, the work is either committed (which
    # reads as a false COMMITTED alarm) or deleted (nothing left to report).
    # The renderer prefers these facts and only re-reads a tree when they are
    # absent (the v1-ctx fallback).
    try:
        # Computed ONCE, here, from the tree the run actually used -- see
        # qd/core/facts.py for why when-it-runs matters as much as what it
        # returns. The detectors below still write their results back into this
        # record, which is the facts/findings confusion the design names; they
        # move out in step 2.
        ctx["tree_facts"] = facts.collect(work_cwd, pre_status, pre_sha_full)
        final_changed = ctx["tree_facts"]["changed"]
    except Exception:
        ctx["tree_facts"] = None
    # The detectors READ the facts and RETURN findings; nothing writes back into
    # the record (§4). They observe the same tree at the same moment as the facts
    # above -- after a worktree run's commit/release there is nothing left to
    # scan. Adding or removing one is a file in qd/features/detectors/; this call
    # site does not change.
    if ctx["tree_facts"] is not None:
        scope.mark_created(_created(work_cwd, final_changed, pre_status,
                                    pre_tracked, ctx.get("writes"), hooked))
        ctx["detections"], ctx["detections_failed"] = detectors.run_all(
            ctx["tree_facts"], scope, plan)
    if plan.contract_path:
        # A2.2: pinned in the receipt. A reviewer weeks later must be able to
        # tell whether the document they are reading is the one that ran.
        try:
            with open(os.path.join(work_cwd, plan.contract_path)) as _cf:
                _doc = _cf.read()
            ctx["contract"] = {"path": plan.contract_path,
                               "digest": core_contract.digest(_doc),
                               "clauses": core_contract.clauses(_doc)}
        except OSError:
            ctx["contract"] = None
    ctx["work_cwd"] = work_cwd
    # Extracted here rather than at render time so the one line a report run
    # exists to produce survives the receipt's result-text truncation.
    ctx["findings"] = parse_findings(result_text)

    # U1.3: WHO moved HEAD. Scoped hard-denies `git commit` in the hook, so a
    # moved HEAD there cannot be the worker's -- the receipt accused it anyway,
    # once per co-working caller. Anywhere else nobody knows, and saying so
    # beats guessing. "worker" is reserved for positive evidence we cannot yet
    # produce (no channel reports a commit as the worker's).
    ctx["head_moved_attribution"] = ("caller" if approval_mode == "scoped"
                                     else "unknown")

    # --- Advisory gates (U3.4) ---
    # Placed here on purpose: after the tree facts (the gates may read the tree
    # the run used) and BEFORE the worktree block, which either commits that
    # tree or deletes it. Indicators only -- nothing below reads the results,
    # the retry loop is already over, and the worker never sees a line of them.
    # G4: did the run build what the brief asked for? The one question the gate
    # structurally cannot answer -- a confidently-built misunderstanding passes
    # its own tests perfectly. ADVISORY and staying that way: this is a WITNESS,
    # and PRINCIPLES §I says the verdict is a command's exit code, never
    # anybody's account of the work. OFF by default; it costs a whole executor
    # pass on a run that has already finished.
    if setting("review_brief", args, cfg, _global_config(), default=False):
        def _review_ask(text):
            # Was `...run_executor(...)[0]`, and that `[0]` threw away the meta
            # of a WHOLE executor pass -- the one executor call in the system
            # whose tokens were recorded nowhere at all.
            #
            # This puts them in the LOG, under their own kind, from the
            # executor's own meta -- the same source and convention "challenge"
            # and "attempt" already use. It does NOT put them in the receipt:
            # BURN and COST are rendered from ctx["cum"], fed by accum_stats,
            # and nothing here touches it. So the asymmetry with "challenge",
            # which IS folded into cum at :1422, survives this change on
            # purpose. Whether a pass that runs after the verdict is settled
            # belongs in the run's headline cost is a decision about the
            # receipt, and making it here would be an unasked-for second change.
            r_text, _denials, r_sid, r_err, r_meta = run_executor(
                profile, text, work_cwd, "plan", verify_timeout, None)
            ctx["calls"].record("review_brief", r_meta, session=r_sid,
                                err=r_err)
            return r_text

        _rev = advisories.review(_review_ask, task, ctx.get("tree_facts"))
        if _rev:
            ctx["advisory"] = (ctx.get("advisory") or []) + [_rev]

    if args.get("advisory_gates"):
        _gates, ctx["advisory_skipped"] = _run_advisory(
            args["advisory_gates"], work_cwd, verify_timeout)
        ctx["advisory"] = _gates
        # Off `_gates`, never back out of ctx["advisory"]. TODAY that reads the
        # same: the assignment on the line above discards whatever the review
        # pass appended, so ctx["advisory"] holds exactly `_gates`. It is a
        # hazard guarded in advance, not one being closed -- the discarding is
        # itself a known defect with its own task, and when it is fixed the
        # review pass's entry (same name/ok/ms/head shape) will be sitting in
        # that list. A loop over ctx["advisory"] would then log one executor
        # pass twice: once as itself, once as a gate that never ran. Binding the
        # source keeps this code correct across that fix instead of quietly
        # depending on the bug.
        #
        # `_run_advisory` returns only the gates it executed, and a malformed
        # item it skipped is not a call: it ran no command, and a 0 ms entry for
        # it would be indistinguishable from a gate that ran and was measured at
        # nothing.
        for _g in _gates:
            ctx["calls"].record("gate_advisory", {"stats": {"ms": _g["ms"]}})

    # --- Worktree commit or release (M4 seam 1) ---
    # The whole decision -- green keeps and commits, red releases, a borrower
    # commits but never releases or classifies -- is one call now. Every rule
    # it encodes is spelled out in qd/core/scope.py and frozen by
    # specs/scope_spec.py.
    _disposal = scope.dispose(status)
    if _disposal["worktree"] is not None:
        ctx["worktree"] = _disposal["worktree"]
    if _disposal["merge"] is not None:
        ctx["merge"] = _disposal["merge"]

    # --- Compute cost_usd ---
    try:
        t_main = ctx["cum"].get("tokens_main", {})
        tokens_in = t_main.get("prompt", 0) if isinstance(t_main, dict) else 0
        tokens_out = t_main.get("completion", 0) if isinstance(t_main, dict) else 0
        ctx["cost_usd"] = float(cost_usd(profile, tokens_in, tokens_out))
    except Exception:
        ctx["cost_usd"] = 0.0

    # --- Refs added ---
    ctx["refs_added"] = refs.added(refs_before, work_cwd)

    # --- Stored brief (U5.5) ---
    # Written post-run and only when a session exists, because the session id
    # is the handle the caller is given back: `retry_of=<that id>` is how a
    # corrected re-run finds this call again. The task stored is the one the
    # caller sent (corrections included, the project's task_suffix not), so
    # retries accumulate the corrections and never a stack of suffixes.
    if session_id and cfg.get("store_briefs", True) is not False:
        skip = {"task"}
        used_doc = bool(args.get("brief_file")) and brief_meta is not None
        if used_doc:
            # U6: front-matter-supplied values are NOT frozen into the stored
            # brief -- the document is the source of truth and a retry re-reads
            # it; a stored copy would beat an edited document on every retry.
            skip.update(brief_meta.get("filled") or ())
        brief = {k: args[k] for k in BRIEF_KEYS
                 if args.get(k) is not None and k not in skip}
        # U6 (the quiet trap): with a brief_file the composed task IS the
        # document -- storing it would make a retry inline the document twice
        # (once re-read, once as the stored addendum). Store the caller's
        # ORIGINAL task instead. A compiled chain link has no brief_file, so
        # it stores its composed link task and retries as a plain run.
        brief["task"] = brief_meta.get("addendum", "") if used_doc else base_task
        brief["trust"] = trust
        save_brief(cwd, session_id, brief)

    # Closed last, so the span holds every phase above it -- including the ones
    # nothing records. That is what makes the remainder in the run log a
    # measurement of what is missing rather than a restatement of what is not.
    ctx["wall_ms"] = int((time.monotonic() - _t0_wall) * 1000)

    return {
        "status": status,
        "session_id": session_id,
        "trail": trail,
        "result_text": result_text,
        "denials": denials_all,
        "max_iter": max_iter,
        "last_verify": last_verify,
        "ctx": ctx,
    }


def run(args):
    """Delegate then render the verdict receipt.

    Returns the rendered verdict string.
    """
    d = delegate(args)
    cwd = args["cwd"]
    in_tree = worktree_mode(args, cwd) != "auto"

    # Refusals carry their explanation in result_text and an EMPTY ctx; the
    # renderer needs a populated ctx, so routing them through it replaced every
    # carefully-written refusal with "STATUS: error KeyError('cwd')" -- the
    # caller never learned why the run was refused.
    if d["status"] == "refused":
        text = d["result_text"] or "refused"
        if text.startswith("STATUS:"):
            return text
        return f"STATUS: refused\n\n{text}"

    # A preflight-passed run is demoted by the engine now (U3.2); it still moved
    # the tree, so it still earns a graph refresh -- keying on "success" alone
    # would have quietly stopped refreshing for a whole class of runs.
    green = d["status"] in ("success", "success_but_preflight_passed")
    will_refresh = green and in_tree
    try:
        d["ctx"]["graph_line"] = graph.graph_line(cwd, will_refresh=will_refresh)
    except Exception:
        pass
    receipt = render(
        d["status"], d["session_id"], d["trail"],
        d["result_text"], d["denials"],
        d["max_iter"], d["ctx"],
        last_verify=d["last_verify"],
    )
    if green and in_tree:
        try:
            post = snapshot(cwd)
            changed = [
                p for p in set(list(post.keys()) + list(d["ctx"]["pre_status"].keys()))
                if post.get(p) != d["ctx"]["pre_status"].get(p)
            ]
            graph.refresh_async(cwd, changed)
        except Exception:
            pass
    return receipt
