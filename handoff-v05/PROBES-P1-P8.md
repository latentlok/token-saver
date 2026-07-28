# P1–P8 live probe runbook (v0.5)

**Goal:** exercise against a live Qwen endpoint everything that shipped dark in the v0.5
field-report round — built and spec-green against the hermetic harness, never run for real.
Also the first live exercise of async submit/receipts and playbooks. ~1 h supervised total.

**Branch:** `v0.5-field-report` (these are *probes*, not builds — most need no commit; any
that flip a default or build a feature get their own commit, suite green first, never to
master). **Prereq:** Qwen endpoint FREE and idle. Confirm with a throwaway
`qwen_query("ping", cwd=<repo>)` before starting.

**Per-probe shape:** what it gates → what to run → what to observe → decision (flip default /
build / record-only). Record findings inline; a probe is done when its decision is written.

**Cross-cutting:** every probe uses a real delegation (`qwen_delegate`) with a real `verify`
gate on a throwaway git repo (the engine refuses non-git). Read the receipt FILE, never the
worker's claim. Arm the heartbeat (`ScheduleWakeup` 900 s, stop on receipt) on anything long.

---

## P1 — auto-edit as yolo + observe-hook parity

**Gates:** flipping `autoedit_via_hook` ON by default — the only way attribution (C10)
exists outside `scoped` mode. Off today; opt-in via `.qwen-delegate.json`.

**Run:** two delegations, same trivial task + gate, same `approval_mode="auto-edit"`:
1. `autoedit_via_hook` absent (today's default) — flag OFF.
2. `autoedit_via_hook: true` in `.qwen-delegate.json` — flag ON.

**Observe:**
- Flag-OFF is spec-proven byte-identical argv/env to plain auto-edit (`invoke_spec`,
  `observe_hook=False`). Confirm the OFF run behaves as plain auto-edit: edits land, no
  hook installed, `attribution: "none"` in the receipt (`engine.py:1065-1175`).
- Flag-ON: `observe_hook=True` installs the PreToolUse hook *outside* scoped mode purely
  for attribution logs (`invoke.py:286-298`, `engine.py:1067`). Receipt should read
  `attribution: "hook"`, and `writes.log` should list the worker's resolved write paths
  (`scoped_hook.py` C10 allow-side).
- **The thing specs cannot prove:** that flag-ON behaves like plain auto-edit on a *real*
  worker — i.e. the hook's presence doesn't change what the worker can do or break the
  run. Compare the two runs: same files changed, same gate result, ON just adds the
  attribution log.

**Decision:** if ON matches OFF on outcome + adds attribution → flip `autoedit_via_hook`
to default-on (config recipe + a spec pinning the default). If the hook perturbs the run →
keep opt-in, record why.

**RESULT (2026-07-29, commit `c50aa64`): PASSED, default flipped ON.** Flag-ON matched
flag-OFF on outcome/gate/attempts (~1s overhead) and added attribution
(`writes_attributed` 0→1). Default now ON in `qd/engine.py` (`cfg.get(..., True)`), opt-out
per-project with `false`; spec pins it (`test_default_on` + `test_explicit_off`); harness
pinned to opt-out via `QWEN_DELEGATE_CONFIG`. Caveat recorded: one trivial task on a free
GPU — complex multi-file edits are a wider live-coverage gap.

---

## P2 — PreToolUse deny for MCP-namespaced tools

**Gates:** whether the scoped hook can fence the worker's *own* MCP tools at all. Today an
unknown tool is judged by input shape: `file_path`/`path`/`command`/`content` keys deny,
the rest allow + log `ungated:<tool>` (`scoped_hook.py:73-80`, `EFFECT_KEYS`).

**Run:** a `scoped` delegation whose task asks the worker to call an MCP-namespaced tool
the allowlist doesn't name (e.g. a `mcp__*` fetch/search). Give it a real gate.

**Observe:** does the hook deny it, allow it, or log `ungated:<tool>`? Read
`denied.log` / `allowlog` (`scoped_hook.py`). The question is whether an MCP tool the
manager didn't allowlist can slip through on shape alone.

**Decision:** record the real behavior. If MCP tools escape the fence → that's a scoped-mode
gap to fix (deny-by-default for `mcp__*`, or an explicit allowlist) — a build, not a flip.
If they're correctly fenced → no change.

---

## P3 — real `edit` / `replace` tool_input field names

**Gates:** completeness of the write log. A write-tool field name the hook doesn't read is
a worker write nobody attributes, and C10's policy only reverts what it can attribute
(`EFFECT_KEYS = ("file_path","path","command","content")`, `scoped_hook.py:73`).

**Run:** a delegation whose worker edits files via the real executor edit/replace tools.
Inspect what field names the live executor actually sends in `tool_input`.

**Observe:** do the real edit/replace calls carry `file_path`/`path`/`content`, or
different names (e.g. `old_string`/`new_string`, `edits`)? Cross-check against
`EFFECT_KEYS`. Any write field NOT in that tuple is an unattributed write.

**Decision:** if the live field names match `EFFECT_KEYS` → no change. If they diverge →
add the real names to `EFFECT_KEYS` + a spec (a write the hook can't see is the silent
attribution hole this release spent the day closing elsewhere).

---

## P4 — cached-token counters

**Gates:** the `BURN:` cache clause — `BurnLimit` binds on `prompt − cached`
(`limits.py:50`, `invoke.py:546` `cached`). On an endpoint that never reports `cached`,
that branch is dead code that has never rendered.

**Run:** any streaming delegation with a `burn`/token limit attached (a limit forces stream
mode). A repeat run back-to-back so a cache *could* warm counts double.

**Observe:** does the endpoint's `usage` ever report a non-zero `cached`? Read the receipt's
token line and `progress.json` `input_tokens`. The branch only matters if `cached > 0`
ever appears.

**Decision:** if `cached` is always 0 on this endpoint → the BURN clause is inert here;
record, no change (it's correct code, just never exercised — it'll matter on a caching
endpoint, see API-expansion in PENDING). If `cached` reports → confirm `BURN` binds on
`prompt − cached` correctly.

---

## P5 — `usage` fallback + BurnLimit on a real stream

**Gates (carried from 0.4.0):** trusting stream-mode token totals and every live limit
built on them. The `usage` fallback (`invoke.py:477-496`, `token_source: "usage"`) has
never run for real — spec-only.

**Run:** a streaming delegation with a `burn` budget set *below* what the run will
actually consume, so `BurnLimit` must fire and stop it mid-stream (`limits.py:50`).

**Observe:**
- Does the run actually stop when the budget is hit (not run to completion / not hang)?
- Does the receipt report `token_source: "usage"` and a believable total? `usage` SUMS
  every API call in the run (`invoke.py:477`) — confirm the total isn't double-counted or
  stuck at 0.
- Streaming loses `tools` and `lines_added`/`lines_removed` (open from 0.4.0) — confirm
  they read 0, and that you can tell that's "unmeasured" not "measured zero" (you can't,
  today — that's the known gap, just confirm it still holds).

**Decision:** if `BurnLimit` stops the run and the total is sane → `usage` fallback is
vindicated; record. If totals are wrong/the limit doesn't fire → that's a live-limit
correctness bug to fix before trusting any priced-endpoint cost figure.

---

## P6 — worker delete-command phrasing (DESIGNED, NOT BUILT)

**Gates:** `allow_delete` and stray auto-clean — designed but not built. Today `strays` is
a receipt line, a `RUN:` count, and a run-log integer (`verdict.py:662-670`,
`engine.py:212`); nothing parses or acts on delete commands.

**Run:** delegations whose tasks naturally ask the worker to delete files (`rm`, `git clean`,
"remove the old module"). Collect the *real phrasings* the worker uses across several runs.

**Observe:** what commands/forms does the worker actually emit for deletion? A delete parser
guessing at commands is "the one bug class with no rollback" (PENDING) — so the point here
is *data collection*, not testing existing code.

**Decision:** **build only once real phrasings are known.** If phrasings are consistent and
parseable → design `allow_delete` + a spec (refuse-by-default; the parser must be
deterministic, never best-effort). If they're wild/varied → keep `allow_delete` unbuilt and
leave `strays` as a receipt-only indicator. Either way, do NOT build a delete parser
blind — this probe exists to gather the inputs first.

---

## P7 — fixture-provenance compliance loop

**Gates:** flipping `fixture_provenance` to default-on (off today; opt-in,
`schemas.py:128`, `engine.py:848/1375`). The open question is *behavioral*: does a worker
told "capture it or name its source" comply, or thrash against the check until attempts run
out (last attempt ends `fixture_unproven`)?

**Run:** a delegation that creates fixture files (under `fixtures/`/`testdata/`/`golden`/
`snapshots`/`cassettes`, or project `fixture_globs`) with `fixture_provenance: true`. A
real gate. The task should *require* fixtures (e.g. "write a test with a sample input
fixture").

**Observe:**
- Does the worker add `captured-from: <url or command> <date>` to the first 10 lines
  (`engine.py:90` `_PROVENANCE_HEADER`), or a `<path>.src` sidecar for binaries?
- Or does it loop, each attempt rejected for missing provenance until attempts exhaust
  (`engine.py:781`, `fixture_unproven`)?
- Imagined/hand-authored fixtures pass any gate (`schemas.py:130`) — confirm that holds
  (the header is the only requirement, not a real capture).

**Decision:** if the worker complies within the attempt budget → safe to flip
`fixture_provenance` to default-on + a spec. If it thrashes → keep opt-in; the compliance
friction isn't worth default-on, record the failure mode.

---

## P8 — `progress.json` write cadence

**Gates:** advertising the heartbeat as a liveness check with a *stated interval*. The
sidecar's shape is spec'd (C11, `limits.py:122-243`); how often a real stream actually
writes it is unmeasured. (Note: the heartbeat *loop* is already live-tested on a non-Claude
model via a synthetic fixture, and the recipe is now push+poll — a background `until` on the
receipt for completion + a `ScheduleWakeup` watchdog on `progress.json` for liveness. This
probe measures the *write* side, which both primitives assume.)

**Run:** any streaming delegation (a limit forces stream → `Progress` is wired as
`on_line`, `limits.py:158-170`). Watch `progress.json` `updated` timestamps as it runs.

**Observe:**
- How often does `_write()` fire — per streamed record (`Progress.__call__` runs on every
  record, `limits.py:158`), or is it throttled?
- Does `attempt` advance correctly across retries, and `state` flip to terminal on
  `finish()` (`limits.py:177`)?
- Is the cadence frequent enough to be a useful liveness signal (the heartbeat pings every
  900 s; the sidecar should update well more often than that)?

**Decision:** measure the real cadence. If writes are per-record (frequent) → advertise the
heartbeat with a stated "updates per streamed record" interval in the skill/docs. If writes
are sparse/stalled → that bounds what the heartbeat can honestly claim, and the stated
interval must reflect it. Record the measured number.

---

## After the probes

- **P1, P7** may flip defaults → each its own commit (config + spec pin), suite green.
- **P3, P6** may build/extend → own commit each (P6 only if P6 phrasings warrant it).
- **P2, P4, P5, P8** are record-and-decide; changes only if a real defect surfaces.
- Any commit on `v0.5-field-report` only. Suite `bash ci/run-specs.sh` exit 0 before each.
- Then **Task 4 — release** (version bump in `.claude-plugin/plugin.json` + marketplace
  metadata, CHANGELOG human-only, merge `v0.5-field-report` → master = **publish** — the
  user's call).

## Standing rules (unchanged)

- One commit per task, on `v0.5-field-report`, never to master.
- Suite exit 0 before any commit. `specs/*_spec.py` are permanent gates — never remove.
- Runtime code stdlib-only. Comments say WHY (the measured failure), never what.
- Read the receipt FILE; the worker's self-report is never evidence — the gate decides.
- No sharp Qwen-skepticism in agent-facing surfaces; mechanism statements only.
