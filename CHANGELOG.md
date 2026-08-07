# Changelog

## 0.6.1 — 2026-08-07 (the dead-endpoint round)

One finding, from the first eval run after 0.6.0: the machine-default endpoint was
hard down (vLLM behind an auth proxy, answering HTTP 502 in ~8ms), and nothing
checked reachability before spawning workers. Two delegation runs burned ~380s and
11 API errors each, wrote nothing, and filed the outage as `gate_suspect` — the
worker's failure, not the infrastructure's. The 502 was provable in one
sub-second request.

**Refuses before burning**
- `qd/probe.py`: one `GET <baseUrl>/models` (3s timeout, stdlib urllib) before the
  first executor call of a run. A connection error, a timeout, or HTTP ≥ 500
  refuses immediately with `EXECUTOR UNREACHABLE: … nothing was run.` — the
  existing `refused` status, same slot as the `GATE UNUSABLE` precedent; no new
  status.
- The verdict is one-sided: DOWN is only what one request can prove. 2xx, 401,
  403 and 404 all read as alive (the field endpoint answered 401 to bare requests
  while 502ing authenticated ones, so reading 4xx as down would refuse a working
  configuration), an unrecognised exception reads as alive, and a profile with no
  discoverable base URL is never probed. The probe fails open on its own bugs, so
  it can never become the outage.
- A chain or batch probes each distinct endpoint once at its head; one dead
  endpoint refuses the whole call, so "nothing was run" stays true. Verdicts are
  never cached across calls: a just-restarted endpoint works on the very next
  call.
- `qwen_query` against a dead endpoint answers `STATUS: refused` in ~3s instead
  of hanging the synchronous call for the whole executor timeout.
- `qd.doctor` `project_check()` gains `endpoint-down` (high), silent when the
  default profile carries no base URL.

`specs/probe_spec.py` pins all of it — 27 checks against real local `http.server`
fixtures (502 / 200 / 401 / closed port / timeout), red phase witnessed in two
stages before the wiring existed.

## 0.6.0 — 2026-08-05 (the friction-ledger round)

The first round driven by a field ledger rather than a design doc: 23 findings from
building a real project with the plugin, **none of which had been fixed**. One
insight organises all of it — *almost every finding is the plugin knowing something
and not acting on it* — so the round replaces prose that instructs with code that
acts. Live tracker: `docs/archive/a92e876/PLAN-v06-ledger.md`; evidence: `docs/archive/plugin-improvement.md`.

**Stops losing work**
- Child processes now die with their parent (`start_new_session` + `killpg` on both
  spawn sites). Four confirmed leaks — an orphaned gate suite, a cancelled query's
  worker, two dropped-transport workers — were one missing teardown. The leaked
  suites also poisoned the timing used to set the timeout that produced them.
- A batch's pre-flight runs ONCE per (base commit, gate) instead of N times
  concurrently. Fan-out was starving its own gates into `GATE UNUSABLE`, so the wider
  the fan-out the likelier everything refused.

**Sees the seams**
- `UNCALLED:` — a new public symbol nothing outside its own file/tests references.
- `MOCKED SEAM:` — a delivered test mocks a module the run also changed.
- `NEVER EXECUTED:` — a delivered test file the gate does not run.
  Three greps, nothing executes. In the field these three cover six of ten defects
  that sixteen green receipts and 1,717 passing tests could not see.
- `new_public_symbols` no longer misses whole new directories (it read
  `git status --porcelain`, which collapses one to a single entry).

**Concurrency**
- `parallel_max` per endpoint is the single knob. Every executor is an
  OpenAI-compatible API, so there is no local/remote distinction to model.
- `run_batch` schedules per item instead of applying items[0]'s policy to the batch.
- `DISPATCH:` receipt line states what a fan-out actually got.

**Stops lying**
- The graph sidecar stores `"indexed"`, never `"fresh"` — freshness is computed live.
- `progress.json` stamps the run id at submit and opens `state: "starting"`.
- `TEST DODGE` matches the mark, not the substring: `skipif` (the repair) and string
  literals no longer fire.
- `DENIALS:` splits effect-shaped from read-only; a denied search no longer makes the
  receipt call its own verdict suspect.

**Fewer decisions left to the model**
- `timeout_sec` is fitted from the project's own history instead of being a
  regression formula the caller applied by hand. A `TIMEOUT:` line states the number.
- `qd.doctor` gained `project_check()`: gate cannot reach the specs, gate near its
  timeout, no fan-out capacity, stale servers sharing state.
- The CLAUDE.md managed block shrank 524 -> 193 tokens.

**Removed**
- `_ref_impl.py` (2,293 lines). The v1 differential oracle outlived the migration;
  crane equality had already been retired in `verdict_spec`'s own docstring while the
  scaffolding kept rendering a full v1 receipt 12 times per run and discarding it.
- `workers` (best-of-N) — advertised in the schema, never ported to `qd/`. Passing it
  silently got you one candidate. Deferred until wanted; git history has the v1 loop.

**Concurrency actually reaches the caller**
- Every spawned child now gets `stdin=subprocess.DEVNULL`. The server speaks
  JSON-RPC over stdio, so fd 0 IS the protocol stream, and a child inheriting it
  races the reader for the caller's next request — the qwen CLI provably consumes
  inherited stdin even when given `-p`. Concurrent delegations from one session
  were the headline capability and were unreachable client-side. Measured after:
  four submits, peak 4 concurrent workers, all four delivered.

**Chains are actually chains**
- A chain now holds ONE worktree across all its links and commits between them,
  so link 2 sees link 1's work. `run_chain` had always documented this; under
  `worktree: "auto"` it was false — every link acquired its own tree from HEAD.
  Committing between links is also what makes a delivered test *tracked*, so
  `spec_globs` can protect the gate the next link is graded by.
- Link N's `HANDOFF`/`FILES`/`NEXT` is forwarded to link N+1. The envelope was
  already requested of every worker and already parsed; nothing forwarded it, so
  the only route between links ran through the orchestrator's context.
- **Batch of chains**: a batch item may carry `chain`, so N pipelines run in one
  call, concurrently, each internally ordered and in its own worktree.
- Links after the first no longer share a cached pre-flight verdict — that cache
  assumes every item is cut from the same base, which a chain link is not.
- `worktrees.stale()` + a doctor check report orphaned containers. A chain's
  worktree outlives every link by design, which is what makes it orphanable.

**The brief can be wrong, and now something says so**
- `challenge_brief` (default ON): one read-only pass before any building asks
  whether the code contradicts the brief. A worker-written gate is the brief
  restated as an assertion, so a wrong requirement becomes a green test
  *defending* the defect — and `preflight_expect` is blind to it, because
  red-before/green-after is what a confident mistake looks like too.
- It blocks **only** on an objection citing a path that exists. A citation nobody
  can check is an opinion, and a run stopped by one teaches callers to switch the
  pass off — taking the real objections with it.
- Skipped for `report_dont_fix` (a diagnosis makes no claim to contradict) and run
  once at a chain's head, not per link.

**Telemetry per call, not per run**
- `ExecutorCall` / `CallLog`: every executor call is logged with its own kind,
  tokens, duration and session, plus a per-kind rollup priced from the profile.
  A run used to be one kind of call repeated; it is now a challenge plus N
  attempts, and one sum could not tell them apart. Lands in `runs.jsonl`,
  deliberately not in the receipt.
- It paid for itself immediately: `challenge_warm` was designed as "one prefill
  instead of two" and measured against a cold build. Default is cold; the A/B
  was only possible because the two calls are logged apart. *(The **+50% input,
  +16% wall** figure first recorded here was wrong — the counter it came from
  double-counts a resumed session. Corrected to +2% input and no wall-clock
  difference; see G5 in docs/archive/a92e876/FINDINGS.md.)*

**Stops counting non-evidence**
- The `trust="self"` vacuous-pass guard no longer counts SKIPPED tests. Five
  `@unittest.skip` tests satisfied `min_tests: 5` and exited 0; pytest's
  fully-skipped summary parsed as no count and passed anyway. unittest's total
  has its skips subtracted, pytest's `N passed` already excludes them, and a
  parse finding skips but no passing count now fails. A skip is still not a
  *failure* — just not evidence.
- `qd.doctor` no longer gives Ollama-shaped advice; it points at what the server
  reports (`--max-model-len`, `max_model_len` on `/v1/models`).

**Breaking**
- Endpoint `parallel_max` is now honoured from an `endpoints`-only machine file
  (previously ignored unless the file also named `"default": "qwen-local"`).
- `batch`/`chain` items inherit `cwd` and run-level fields from the call.
- `challenge_brief` defaults to ON: every delegation makes one extra read-only
  executor call before building. Pass `challenge_brief: false`, or set it in
  `.qwen-delegate.json`, to decline.

## 0.5.1 — 2026-08-01

The vLLM cutover release: everything that shipped dark in 0.5.0 exercised against a
live endpoint, plus the two fences and two accounting fixes the exercise demanded.
Runbook in `docs/archive/handoff-v05/VLLM-ROUND.md`.

### vLLM cutover round (A1–A4, B1–B4, C1)
- **Executor profiles proven live.** Endpoint/model/sampling pinned per profile via
  `settings_overlay` → provider `generationConfig.samplingParams`; machine-default
  resolution and overlay effectiveness verified against a real vLLM endpoint.
- **Parallel dispatch proven live.** `endpoints.<n>.parallel_max: 2` +
  `dispatch: "parallel"`: two gated builds fan out across worktrees and overlap on
  continuous batching with per-item receipts and sane MERGE lines; the machine-wide
  repo lock field-confirmed serializing two OS processes in-tree on one repo across
  different endpoints; worktree config-default + main-tree co-work confirmed in a
  real repo.
- **Scoped MCP fence (closes P2).** The worker does call MCP tools
  (`mcp__<server>__<tool>`), and input shape says nothing about what the server does —
  a `{url}` input fetched the network inside the fence. Scoped mode now denies
  `mcp__*` by default; allow per-tool with `mcp_allow` name regexes (call arg >
  project config, stored in briefs). Receipts render `MCP APPROVAL NEEDED` naming
  the right knob. Observed auto-edit keeps record-don't-gate, byte-identical.
- **Accounting fixes.** The run ledger records the RESOLVED executor profile (a
  machine default no longer mislabels every run `qwen-local`); the token-provenance
  ladder gains `usage` as coarsest source (closes P5 — streamed runs no longer read
  as unmeasured).
- Cached-token reporting on vLLM needs `--enable-prompt-tokens-details` server-side;
  absent it, `cached` reads 0 and the BURN cache clause stays inert (server note,
  not a client defect).

## 0.5.0 — 2026-07-29

The async-delegation release. The workflow is now submit-and-poll by default: a
delegation returns immediately with a run id, a receipt path, and a heartbeat file;
the caller does other work and reads the receipt on completion. A free local model
builds; a smart model orchestrates and verifies through an objective gate.

### Workflow
- **Async by default.** `qwen_delegate` submits and answers in seconds; `wait: true`
  blocks for the receipt. Result contracts (`result_schema`), `retry_of` (cold corrected
  re-run), and a self-updating capability map.
- **Playbooks (briefs).** Briefs live in the repo, versioned by git, sent by name
  (`brief_file` + `vars`); `chain: true` compiles `## Step <n>` sections into a chain.
- **Receipt diet.** Receipts carry only what a caller acts on; verbose internals moved
  to `runs.jsonl`.

### Safety & attribution
- **C10 write attribution.** A PreToolUse hook resolves and logs every worker write
  path; the engine reverts only what it can attribute, so a caller's concurrent edit is
  never rolled back.
- **`autoedit_via_hook` default ON** (probe P1). Attribution exists outside scoped mode
  by default; opt out per-project with `"autoedit_via_hook": false`. Behaviorally free
  vs plain auto-edit (~1s overhead, same outcome/gate).
- **Gate hygiene.** `preflight_expect` (red/green/any), `verify_timeout_sec`,
  `gate_slow` detection, `report_dont_fix` (diagnose, don't repair).
- **Scoped shell.** Path-confinement for writes, an exact-verify + read-only shell
  allowlist, deny-by-shape for unknown effect-bearing tools (`EFFECT_KEYS`).

### Features
- `fixture_provenance` (opt-in) — fixtures must carry a `captured-from:` source line;
  imagined fixtures were the field report's worst defect class.
- `findings` / `strays` / `result_schema` / `touch_scope` / `shell_allow`.
- `trust` dial: `self` (delegate's own suite is the gate) / `verified` / `auto`.

### Heartbeat (C11)
- `progress.json` sidecar updates **per streamed record** by default (the default
  10M-token burn budget wires it on every run). Push+poll recipe in the `delegation`
  skill: a background `until [ -f receipt ]` for completion, a `ScheduleWakeup`
  watchdog on `progress.json` for liveness. A stall shows as a frozen `updated` timestamp.

### Live-probe field report (P1–P8)
- P1 autoedit_via_hook: passed, default-on. P3 edit/replace field names: match, no change
  (executor's `edit` carries `file_path`; content fields immaterial — named tool, path
  attribution). P5 BurnLimit: fires correctly mid-stream. P8 progress.json: per-record,
  unthrottled.
- Recorded non-blocking open items: P5 `usage` fallback path still spec-only (this
  endpoint's stream kept `usage` intact); P7 `fixture_provenance` kept opt-in (the
  `.src` sidecar is honored for binaries only — comment-free text formats like JSON have
  no compliant route); P2 MCP-namespaced fencing spec-only (worker declined to call an
  MCP tool in test runs); streaming mode does not emit tool counts (`tools.calls` reads 0).
  P2 and P5 were closed in 0.5.1.

### Skills & docs
- `lld-principles` loaded on demand in the manager (was ~52% of weekly Claude usage;
  trimmed ~18%). README + `docs/archive/a92e876/USAGE.md` rebuilt against v0.5. `docs/archive/a92e876/HLD.md` contracts
  amended for the async flip.

---

## 0.4.1

- Receipt and runlog refinements; scoped-shell hardening.

## 0.4.0

- Scoped shell, BurnLimit, gate pre-flight, trust dial.
