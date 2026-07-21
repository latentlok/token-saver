# LLD — qwen-delegate v2

Module-level design. Boundaries and shared shapes come from [HLD.md](HLD.md) (contracts
C1–C9) — referenced here, never redefined. Each unit below is designed to
spec-readiness: its `specs/<name>_spec.py` can be written directly from its section, and
the spec **is** the design. Internal structure beyond the public surface is the
builder's to choose.

Ported modules (`gittree`, `invoke`, `verdict` core, `queries`, `bootstrap`, `engine`
core, `runlog` core) freeze current `server.py` behavior; their existing specs move to
`specs/` and act as regression gates. Only their *seam changes* are designed here.

---

## qd/profiles.py  (new — M1)

**Purpose:** resolve which executor runs, and how, from the C7 precedence chain.

**Public surface:**

    resolve(cwd: str, call_executor: str|None) -> dict          # a C1 Profile
    render_argv(profile, task: str, mode: str, resume: str|None) -> list[str]
    cost_usd(profile, tokens_in: int, tokens_out: int) -> float
    QWEN_LOCAL: dict                                            # the builtin (C7 pin)

**Behavior:** `resolve` walks call arg → project config `"executor"` → machine file
`default` → `QWEN_LOCAL`. Missing machine file is normal (builtin). `render_argv`
substitutes `{task}`, `{mode}`, `{resume}` into `argv`; a profile whose argv lacks
`{task}` is invalid. `cost_usd` = tokens × prices / 1e6, exact float math is fine
(record-keeping, not billing). Endpoint/model switching is ONLY via
`settings_overlay` (C1) — probed: `-m` and `OPENAI_*` env are silently ignored, and an
overlay with a bare `model.baseUrl` is validated away against the providers registry;
the overlay must carry a complete `modelProviders` entry + `model` + auth selection.
`QWEN_LOCAL.settings_overlay` is null (inherits the user's own config untouched).

**Edge cases:** malformed `executors.json` → structured refusal string naming the file
(never a traceback); unknown profile name → refusal listing known names; profile names
an unknown endpoint → refusal; no `endpoints` section → each profile gets an implicit
endpoint of its own with `parallel_max` 1; `parallel_max` < 1 → clamp to 1; missing
optional fields → C1 defaults applied (`rules_file` "QWEN.md", prices 0, altitude
"lld"); `{resume}` with no session → the placeholder and its flag argument are dropped,
not left empty.

**Spec asserts:** all four precedence levels win in order; `QWEN_LOCAL.render_argv`
equals the current server's invocation **literally** (copy the list into the spec);
cost math incl. zero-price → 0.0; malformed file and unknown name refusals; determinism
(`resolve` twice → equal). **Mutation:** swap precedence of project config and machine
default → red.

## qd/gittree.py  (port — M1)

**Purpose:** the trust machinery, verbatim: `git()`, `is_git_repo`, `head_sha`,
`snapshot`, `status_map`, `file_sha`, `spec_files`, `violated_specs`, `revert_specs`,
`committed_during_run`, `new_public_symbols`, `blast_radius`, `reset_worktree`.

**Design:** move, don't modify. All functions already take `cwd` — worktree-ready.
**Spec:** existing suites (`headguard_spec`, relevant parts of `bestofn_spec`,
`compaction_spec` where tree-related) moved to `specs/`, plus one new assert: every
public function operates correctly when `cwd` is a linked worktree (create one in the
spec fixture). **Mutation:** re-run one historical mutation per ported guard.

## qd/worktrees.py  (new — M4)

**Purpose:** isolation containers for parallel runs; the merge protocol lines.

**Public surface:**

    acquire(repo: str) -> {"path": str, "branch": str, "base_sha": str, "dirty": bool}
    release(repo: str, path: str, branch: str) -> None
    merge_lines(res: dict) -> list[str]        # the C2 WORKTREE:/MERGE: lines

**Behavior:** naming per C6; `git worktree add <path> -b <branch> HEAD`. A module-level
lock guards id allocation (unique under concurrency). `release` = `worktree remove
--force` + `branch -D`, idempotent (already-gone is success). `merge_lines` emits the
exact C2 strings; it never executes them — the server never merges.

**Edge cases:** unborn HEAD → refusal "make the first commit" (greenfield iteration
zero commits the HLD/specs first anyway); dirty main tree → `dirty: true` (verdict
warns: uncommitted work is not in the worktree); repo path is itself a linked worktree
→ resolve to the main repo's common dir before branching; stale leftover dir from a
crashed run at the target path → remove and re-create (run-ids are unique, so only a
crash leaves one).

**Spec asserts:** two concurrent `acquire`s → distinct paths/branches; a file written
in wt-A is invisible in wt-B and the main tree; `spec_files`/`violated_specs` fire
inside a worktree; `release` leaves `git worktree list` and branch list clean;
idempotent release; unborn-HEAD refusal; `merge_lines` strings match C2 verbatim.
**Mutation:** make id allocation non-locked with a forced collision → red.

## qd/invoke.py  (port + seam — M2)

**Purpose:** run the executor once; parse its stats. Ports `invoke_qwen`,
`parse_qwen_json`, `parse_stats`, `norm_tokens`, `accum_stats`, `peak_context`,
compaction sidecars, deny-log reading.

**Seam change:** signature becomes `run_executor(profile, task, cwd, mode, timeout,
session_id, settings_path)`; the command line comes from `profiles.render_argv`, and
the subprocess environment is `os.environ` merged with `profile["env"]` (C1) — one
process, one endpoint, chosen at launch. Timeout default from
`profile["defaults"]["timeout"]`.

**Spec:** ported stats/compaction suites + one new assert: with `QWEN_LOCAL`, the
subprocess argv equals the v1 hardcoded invocation (the crane and the new engine agree).

## qd/engine.py  (port + prefilter + fan-out — M2/M4)

**Purpose:** the delegation loop (HLD §4). Ports `_delegate_once`, `run_qwen`
(best-of-N), `retry_prompt` (reflexion), scoped-mode setup.

**Public surface:** `delegate(args: dict) -> ctx` where ctx satisfies contract C3.

**Seam changes:**
0. **Full-sha pre_sha:** capture the pre-run base as `git rev-parse HEAD` (full),
   not v1's short `head_sha()` — qd.gittree's guards pin full-sha comparisons
   (see gittree_spec's CONTRACT CHANGE note).
1. **Prefilter (C8):** after each invoke — detect changed files matching `*_qwen.*`
   (from `gittree.status_map` diff vs pre-run); if a test command is known (bootstrap
   detection) run it on those files, capture ≤2,000 chars. Wire per C8: gate red →
   append to feedback; gate green + prefilter red → `ctx["notes"] += "self-tests
   failing"`; never touches status. No test command known → skip silently (prefilter
   is opportunistic).
2. **Worktree path (M4):** `worktree:"auto"` (C9) → `worktrees.acquire`, run with
   `cwd=path`, populate `ctx["worktree"]`, attempt nothing on the main tree.
   Best-of-N with worktrees → candidates fan across `acquire`d trees, bounded by the
   server's semaphore; winner keeps its worktree (receipt carries MERGE lines), losers
   `release`d. `worktree:"off"` + `workers>1` → v1 sequential `reset_worktree` path,
   frozen.
3. **Notes assembly:** `ctx["notes"]` = builder's handoff BLOCKED/flag content +
   prefilter clause, truncated to 200 chars (C2).
4. **Trust stub (C9):** `args["trust"]` ≠ "verified" → structured refusal;
   `ctx["trust"]="verified"`.

5. **Batch (C9, from probe 5):** `batch: [items]` fans N independent delegations
   across worktrees on server-side threads (endpoint semaphore caps real concurrency;
   on the 1-worker endpoint items serialize whole-run, preserving KV cache). Returns
   per-item receipts, each with its own MERGE lines. `task` and `batch` are mutually
   exclusive.

**Edge cases:** prefilter test command itself crashes (rc≥2 vs test-failure rc=1) →
treat as "prefilter unavailable", never as builder failure; builder deletes its own
`*_qwen.*` tests mid-iteration → prefilter simply has nothing to run (allowed — they're
the builder's files); worktree acquire fails → refusal, no partial run; a batch item's
refusal (dirty spec, bad args) fails that item's receipt only, never the batch.

**Spec asserts:** stage order (pre-run gate → invoke → prefilter → gate) observable via
a scripted fake executor + fake gate; C8 truth table (4 combinations of gate×prefilter
→ status/feedback/notes); trust refusal; ctx satisfies C3 keys exactly; v1 regression
(ported `bestofn_spec`, `reflexion_spec`, `scoped_hook` specs green). **Mutation:** let
prefilter failure flip status → red.

## qd/verdict.py  (port + C2 — M2)

**Purpose:** render ctx → receipt. Ports `render`, `parse_handoff`, `strip_handoff`,
`truncate`.

**Seam change:** append C2 lines in C2 order, each only when applicable; total cap
3,000 chars **with new lines counted** — trailing sections are dropped whole (never
mid-line) in reverse-priority order: COST, REFS, GRAPH first; STATUS/CHANGED/ROLLBACK
never dropped.

**Spec asserts:** every C2 line byte-exact from a fixture ctx; conditional emission
(no NOTES line when notes empty, COST only when >0); cap enforcement drops whole lines
in pinned order; v1 receipt for a v1-shaped ctx is byte-identical to the crane's
(golden-file regression). **Mutation:** emit COST when cost==0 → red.

## qd/queries.py  (port — M2)

Ports `run_query`, `run_investigate` unchanged (session reuse, `format='map'`,
read-only warning for non-git). One seam: profile-driven argv via `run_executor`.
Spec: ported suite + argv-equality assert as in invoke.

## qd/bootstrap.py  (port + seam — M2)

Ports detection/render/refusal machinery (`detect_test_cmd`, `render_worker_rules`,
`bootstrap_worker_rules`, statuses, notices). **Seam changes:** rules filename from
`profile["rules_file"]` (C1); template gains the refs rule (C6: save fetched web refs
to `.qwen-delegate/refs/`, URL on line 1) and the self-test convention (builder's own
tests are `*_qwen.*`, encouraged, never graded as the gate). Spec: ported
`bootstrap_spec`/`setup_spec` + renders-with-refs-rule + no-placeholder invariant.

## qd/graph.py  (new — M5)

**Purpose:** graph freshness keyed to git (HLD F5). Queries are graphify's job, not ours.

**Public surface:**

    staleness(cwd: str) -> {"indexed_sha": str|None, "stale": [str], "status": str}
    refresh_async(cwd: str, files: list[str]) -> None
    graph_line(cwd: str) -> str                     # a C2 GRAPH: line
    graphify_cmd(cwd: str, files: list[str]) -> list[str]   # the M0-probe seam

**Behavior:** sidecar per C4, written atomically (temp + rename). `staleness` = `git
diff --name-only <indexed_sha> HEAD` — pure git facts. `refresh_async` spawns a daemon
thread: sidecar → "indexing", run `graphify_cmd`, → "fresh" (new HEAD sha) or "failed"
(+reason). Called by the server post-verdict only, with the blast-radius list — never
mid-run, by construction.

**Edge cases:** no sidecar → status "none" (GRAPH line invites a first index); sidecar
sha unknown to git (history rewritten) → treat as "none", don't crash; refresh already
"indexing" → skip (no queue; next verdict retriggers); graphify binary absent →
"failed: graphify not installed", delegation unaffected (graph is advisory
infrastructure, never a gate on delegation success).

**Spec asserts:** staleness math on a synthetic 3-commit repo; C4 transitions
fresh→indexing→fresh and →failed on nonzero exit (stub `graphify_cmd`); refresh receives
exactly the file list passed; every GRAPH line variant matches C2; atomic write (no
partial JSON visible mid-write). **Mutation:** compute staleness against `HEAD~1` → red.

## qd/refs.py  (new — M1)

**Purpose:** make fetched web references visible (HLD F6) — the refs dir is
git-ignored, so git can't see it; we listing-diff it.

**Public surface:**

    snapshot(cwd) -> dict                 # {relpath: (size, mtime_ns)}
    added(before: dict, cwd) -> [str]     # new or changed since `before`
    refs_line(names: [str]) -> str|None   # C2 REFS: line; None when empty

**Edge cases:** refs dir absent → empty snapshot (created lazily by the builder, not by
us); non-.md junk in the dir → listed anyway (visibility over tidiness); name with
newline/comma → sanitized in the line (grammar stays single-line).

**Spec asserts:** add/modify detected, untouched not; never appears in `CHANGED`
(fixture: run a fake delegation, assert attribution unpolluted); C2 line format;
`refs_line([]) is None`. **Mutation:** report modified-only as empty → red.

## qd/runlog.py  (port + v2 — M1)

Ports `runlog_dir`, `write_runlog`, `register_project`, `leverage_record`, `digest`,
`now_iso`. **Seam:** record gains C5 fields; `cost_usd` from `profiles.cost_usd`
(0.0 recorded, never omitted); a module lock serializes appends (S1 threads). Spec:
existing `runlog_spec` (with its mutation set — it caught two blind tests last time)
+ C5 fields present-and-typed + concurrent-append integrity (N threads → N valid JSONL
lines).

## server.py  (rewrite — M3)

**Purpose:** stdio entry + dispatch (HLD §6). The only file `.mcp.json` runs.

**Public surface (wire, not Python):** MCP `initialize`, `tools/list`, `tools/call` for
`qwen_delegate` (schema + C9 additions) and `qwen_query`. Tool descriptions = the
capability map (M7 rewrites the text; M3 keeps v1 text).

**Behavior:** reader thread: parse line, lifecycle requests answered inline,
`tools/call` → worker thread. `respond()` under a global lock, single `write` +
`flush` per response. Repo-lock table keyed on `os.path.realpath` (in-tree delegations
only; C6 worktree runs and queries skip). Per-endpoint semaphore table (C7), each
sized by that endpoint's `parallel_max`, held around the whole executor subprocess
(not around git bookkeeping) — one build's session turns are never interleaved with
another's on the same endpoint, so the KV cache survives; separate endpoints never
throttle each other.
Unknown method → JSON-RPC error, not crash; malformed line → logged to stderr, skipped.

**Edge cases:** two calls race one repo (lock, not corruption); exception in a worker →
JSON-RPC error response with the message, server stays up; stdin EOF → drain workers
(bounded 10s), exit 0.

**Spec asserts (`dispatch_spec` drives the server as a subprocess over pipes):** N=8
concurrent calls to a stub slow tool → 8 correct-id responses, every stdout line valid
JSON; same-repo in-tree delegates serialize (observable via stub timestamps) while
cross-repo overlap; per-endpoint caps honored independently (endpoint A at its cap
queues A-calls — across all profiles sharing A — while B-calls proceed); worker
exception → error response + liveness;
EOF drain. **Mutation:** remove the write lock under load → red (a torn line).

---

## Spec inventory (all in `specs/`)

    new:     profile_spec · worktree_spec · graphstate_spec · refs_spec · dispatch_spec
    seam:    engine_spec (prefilter truth table, trust stub, C3) · verdict_spec (C2 + golden v1)
    ported:  runlog_spec · bootstrap_spec · setup_spec · bestofn_spec · reflexion_spec
             · headguard_spec · compaction_spec · config_spec (+ scoped hook specs)

Every new/seam spec gets a mutation pass before its module is delegated (M-gates, HLD
§7). Contract drift check at M6: grep that no module redefines a C1–C9 shape — the HLD
is the single owner.
