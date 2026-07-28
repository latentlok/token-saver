## ⏸ PAUSED — Phase 6 playbooks: DESIGNED, NOT BUILT (2026-07-27, user-requested)

**State: zero code written. The working tree is exactly as commit 0f36f73 left it
(clean); suite 706 tests, exit 0.** An agent read the codebase, worked the design out
against it and was stopped before the first edit. Everything below is the resume brief —
the design section further down ("QUEUED — Phase 6") is the *ask*; this is the *plan*,
with the traps that only showed up on contact with the code.

### Pending task list (Phase 6, in build order)

1. **`qd/playbook.py` (new module, ~200 lines, pure + one file read).** The whole
   feature's parsing and composition surface, so the engine and the server can both
   resolve a brief without either owning it.
   - `read(cwd, rel)` — realpath-confine inside `cwd` (same rule and reasons as
     `engine._repo_relative`), read text; missing/unreadable/outside ⇒ named refusal.
   - `substitute(text, vars)` → `(text, missing, unused)`. Slot form `{{name}}`, one
     regex, applied to the WHOLE file before the front matter is parsed (a slot in
     `verify:` is the useful case). Unfilled slots ⇒ refusal naming them; `vars` keys
     matching no slot ⇒ refusal naming them (typo detection). `{{` is reserved; no
     escape hatch, say so in the docs.
   - `front_matter(text)` → `(meta, body, refusal)`. Opens only when the first line is
     exactly `---`; `key: value` until the closing `---`; value = `json.loads` else the
     bare string. Unclosed fence, a malformed line, or an unrecognised key ⇒ refusal by
     name (a typo'd gate key silently ignored is a gate that never runs). Recognised:
     `verify, touch_scope, shell_allow, approval_mode, timeout_sec, verify_timeout_sec,
     preflight_expect, advisory_gates, max_iterations, chain`.
   - `compile_steps(body, base_args)` — `## Step <n>[: title]` sections (case-insensitive
     on "Step"); link task = preamble (everything before the first Step) + that section.
     Per-step overrides = leading `verify:` / `touch_scope:` lines only, and the first
     line that is not one of those two ends the block — prose routinely opens with
     `Goal:` / `Note:`, so the front matter's refuse-on-unknown-key rule must NOT be
     reused here. `chain: true` with no Step sections ⇒ refusal by name.
   - `amend(path, message, date)` — create `## Amendments` if absent, append
     `- <YYYY-MM-DD> <retry_message>`.
   - `resolve(args)` → `(args, refusal)`: read → substitute → front matter → slot
     front-matter values in ONLY where the call arg is absent (that alone gives
     args > front matter > project > machine, because every consumer already reads
     `args.get(k) or cfg.get(k)`) → set `task` = body + the explicit non-empty `task`
     as a one-line addendum → consume `brief_file`/`vars` → set the reserved
     `_brief = {path, sha256, amended}`. When front matter says `chain: true`, also set
     `args["chain"]` to the compiled links. No `brief_file` ⇒ returns args untouched, so
     it is idempotent and safe to run on every path.

2. **`qd/engine.py`.**
   - `resolve_call(args, amend=False)` — public, replaces the two bare `_resolve_retry`
     calls: retry brief first (it is what carries `brief_file` on a retry), then the
     amendment, then `playbook.resolve`. `precheck` calls it with `amend=False`,
     `_delegate` with `amend=True`, so the file is written exactly once per run and
     never for a run that was refused.
   - **Trap (found on contact): the amendment fights the spec-guard machinery.** The
     amendment dirties a tracked file before the run, so the brief CANNOT be protected
     by `violated_specs(..., base=pre_sha)` — that diff would report the amendment as a
     worker edit on every attempt. Protect it by CONTENT instead: capture
     `file_sha(work_cwd, brief_rel)` right after the pre-run snapshot and compare per
     attempt; revert through `restore_paths(..., base=pre_sha, t0=t0_saved)`, which
     already restores T0 bytes for dirty paths and HEAD content for clean ones — so the
     amendment survives the revert, which is the point. Do NOT add the brief to
     `_preconditions`' dirty-spec check, or an amended brief refuses its own run.
   - Trail string `PLAYBOOK EDITED -- ...` (checked against every classifier substring:
     VERIFY PASS / RESULT SCHEMA invalid / run stopped: / COMPACTION / SPEC VIOLATION /
     TOUCH SCOPE VIOLATION / IDENTICAL to preflight / FIXTURE PROVENANCE / no verify
     supplied — no collision), classified to the EXISTING `spec_violation` status so C3
     gains no new status. Same C10 attribution split as the spec guard.
   - `BRIEF_KEYS += ("brief_file", "vars")` so a retry replays the same document.
   - `amend_brief` without `retry_of` ⇒ refusal by name.
   - `ctx["brief"]`; `expand_playbook(args)` as the server seam (below).
   - **Heartbeat fix (C11 caveat):** `runlog_dir(cwd)` + `limits.Progress(cwd, ...)`
     instead of `work_cwd`. Caller-visible regardless of work tree.
3. **`qd/server.py`** — `engine.expand_playbook(args)` in `submit_delegate` and
   `run_delegate_batch`, after `_shape_refusal`. It returns args UNCHANGED unless the
   playbook compiled to a chain (then `chain` is set and `brief_file`/`vars` consumed),
   so the single-delegation path still resolves its own brief inside the run — which is
   what keeps the amendment on exactly one code path. A compiled link carries `_brief`,
   never `brief_file`, so no link re-reads or re-amends the document. Refusals answered
   synchronously with `engine.refusal_receipt`.
4. **`qd/gittree.py`** — `violated_specs(cwd, base=None, extra=None)` only if the
   content-sha approach above is dropped; otherwise gittree is untouched this phase.
5. **`qd/verdict.py`** — `BRIEF: <path> @ <sha16>` (+ ` (amended)`) as a BODY line near
   the top; run-log extra `brief: {path, sha256}`; LEDGER gains
   ` · this brief: <ok> ok / <red> red` only when prior runs recorded the same path.
6. **`qd/runlog.py`** — `brief_summary(cwd, path)` beside `ledger_summary` (a second
   helper reads better than a filter argument on a function whose return shape is
   already fixed). Same skip-`running` rule; ok = the two green statuses, red = every
   other completed one.
7. **`qd/schemas.py`** — `brief_file` (string), `vars` (object), `amend_brief` (boolean).
   Additive-optional; `required` untouched.
8. **Specs** — new `specs/playbook_spec.py` (front-matter parse incl. json-vs-string
   values and unknown-key refusal; both slot-refusal directions; precedence
   args > front > project; BRIEF line + digest; amendment shape AND its pre-snapshot
   timing; steps→chain incl. per-step verify; `chain: false` inertness; protection
   revert; path confinement; missing file ⇒ sync refusal with NO receipt file) plus
   additions to `engine_spec` (per-playbook ledger, protection, amendment timing),
   `async_spec` + `engine_spec.Heartbeat` (the sidecar now lands in the SUBMIT cwd —
   `test_a_worktree_run_beats_inside_its_container` inverts), `verdict_spec`,
   `runlog_spec`. Every new param needs its absence-is-inert pin.
9. **Docs this phase owns** — `docs/HLD.md` dated C9 clause (brief_file/vars/
   amend_brief), C2 line for `BRIEF:`, and the C11 rewrite (drop the caveat, state the
   rule); `skills/delegation/SKILL.md` one compact "Playbooks" paragraph + one line in
   "Ask for these when they fit"; `templates/CLAUDE-snippet.md` one capability-map line.
   README/USAGE/OVERVIEW are OFF LIMITS — task #3 rebuilds them.

### Decisions already taken (do not re-litigate)

- **`amend_brief` replaces the CORRECTION append, it does not stack with it.** The
  amendment IS the correction channel; letting `_resolve_retry` also append
  `CORRECTION:` to the task would send the same sentence twice — once as prose, once as
  a line of the document.
- **The amendment lands in the submit `cwd`, not `work_cwd`.** The playbook is the
  repo's file and git is what versions it; a worktree run's copy stays at HEAD, so the
  task text and the protected copy diverge by exactly the amendment. Note it, don't fix
  it.
- **Amending before the pre-run snapshot makes the tree dirty at T0**, which is the
  stated point (it reads as pre-existing dirt, never worker change) but also flips
  `pre_clean` and therefore the ROLLBACK wording. Honest; accept.
- **`_brief` is a reserved arg** in the CHAIN_ARG/RUN_ID_ARG tradition, but unlike
  `PRECHECK_ARG` it needs no token: forging it buys a receipt line and an EXTRA
  protected path, never a way past a precondition.
- **A `chain: true` playbook is never amended** — a compiled link has no `brief_file`,
  and `retry_of` replays one link, not a document. Say so rather than build for it.

### Late traps (found while reading; design them out before writing code)

- **The stored brief would double-inline the document.** `_delegate` stores
  `brief["task"] = base_task`, which with `brief_file` is the COMPOSED text — so a
  `retry_of` merges a stored task holding the whole document, then `playbook.resolve`
  re-reads the file and appends that stored text again as the addendum. Store the
  caller's ORIGINAL `task` (the addendum) when a brief file was used, never the
  composed text. This is the one trap that produces a plausible-looking wrong prompt
  rather than a loud failure.
- **`amend_brief` must stay OUT of `BRIEF_KEYS`.** Stored, it would re-amend the
  document on every later retry of that session. The allowlist excludes it by
  construction; keep it excluded deliberately, with a spec, not by accident.
- **The `BRIEF:` body line is non-droppable weight against the N1 cap.**
  `verdict_spec` pins the length of the R2-compact green receipt — the same pin `RUN:`
  had to be measured against in Phase 2. Read that pin before adding the line and
  expect to re-pin it.
- **Content-based protection covers all three tracking states, but only because an
  untracked file is dirty at T0**: tracked-clean restores from `git show base:path`,
  tracked-dirty and untracked both restore from the T0 byte snapshot. There is no
  fourth case — one spec each.
- **`qd/limits_qwen.py` is worker-written but CI-RUN** (`ci/run-specs.sh` globs
  `qd/*_qwen.py` beside `specs/*_spec.py`). The never-weaken rule covers it. The
  heartbeat fix should not reach it — it constructs `Progress(tmpdir)` directly — but
  confirm rather than assume.
- **`specs/dispatch_spec.py` is CI-excluded and permanent.** `expand_playbook` lands in
  `submit_delegate`, which dispatch_spec drives with injected doubles; it must stay true
  as written (the reason `@self_guarded` is a property of the handler rather than a
  tool-name special case).
- **Build the steps→chain spec on `chain_spec.py`'s fake handler**, not engine_spec's
  stub: chain_spec exists precisely because it is CI-safe with no wall-clock.
- Ordering note: `resolve_call` must run before the trust/gate-expectation checks in
  `_preconditions`, because front matter can supply `verify` — and `verify`'s presence
  is what decides the `preflight_expect="green"` + `trust="self"` contradiction refusal.

### Resume checklist

`git status` must be clean at 0f36f73 before starting. Build in the order above,
`bash ci/run-specs.sh` to exit 0, do NOT commit, and append
`## Build log — playbooks` to the END of this file when it is green. Then task #3
(README + USAGE rebuilt from scratch), which is still the last item.


### Size discipline (user question, 2026-07-27 — build these into the phase)

A huge brief costs nothing at the caller (filename) — the risks are worker
window pressure (an inlined bloated brief raises peak ctx and converts to
compaction_refused deaths under the refuse policy) and amendment sprawl (an
append-only list that contradicts itself is session-confusion in document
form). Four layers, first two are cheap units of THIS phase:
1. Visibility: BRIEF: line carries a size estimate; when amendments exceed ~5,
   append "(N amendments — consolidate)".
2. Precheck guard: brief text over a fraction of the worker window (~25% of
   context_window(), when known) ⇒ named refusal at submit, same family as
   GATE UNUSABLE ("split into steps + chain: true, or consolidate").
3. Steps→chain is the pressure valve: links get preamble + own step only, so
   per-link load stays flat as the document grows. Recommend in docs.
4. Distillation is a delegable task: "fold this playbook's amendments into its
   body" is mechanical, gateable (document still parses: front matter + slots
   + steps), and git preserves the archaeology. Guidance in USAGE (task #3).
Discipline rule for USAGE: the playbook carries the DELEGATION (task, gate,
scope), never the design doc — background stays in stable repo docs the worker
reads on demand.

## Build log — playbooks

2026-07-28, built inline (no agent, user-directed). Suite 706 → **775 tests,
exit 0**. All nine build-order items landed as designed; the traps above held
on contact. Deliberate deviations, each spec-pinned:

- `_delegate` runs its PRECONDITIONS before the amend pass (precheck first,
  then `resolve_call(amend=True)`), so "never amend a refused run" holds for
  direct engine calls too, not just the server path. Refusals therefore carry
  the default max_iter — nothing pinned the old value.
- `playbook.amend(cwd, rel, message, date)` — cwd-first signature so path
  confinement lives entirely in qd/playbook.read; the design's `amend(path,…)`
  had the engine doing its own confinement.
- Front matter validates value SHAPES by name (list/int/bool/str per key) on
  top of the json-else-string rule: a bare-string `touch_scope` would have
  bound as substring matching — silently wrong in the engine. Consequence:
  `verify: true` (the shell no-op) must be quoted `"true"`.
- Stored briefs exclude front-matter-supplied values (`_brief.filled`): the
  document is the source of truth, so a value edited between run and retry
  binds on the retry. Stored task = the caller's ADDENDUM when a document was
  used (the double-inline trap, closed); a compiled link stores its composed
  link task and retries as a plain run.
- `amend_brief` with no `retry_message`, `brief_file` beside `chain`/`batch`,
  and a `chain: true` document reaching the single-run path (retry_of of a
  later-chained document) are all refused by name.
- Unattributed brief changes reuse `ctx["spec_unattributed"]` + a
  `PLAYBOOK CHANGED (unattributed)` trail line (C10 split, no new ctx key).
- Size discipline layers 1–2 shipped: BRIEF: line carries `~N tokens` always
  and `(N amendments — consolidate)` past 5; precheck refuses `BRIEF TOO BIG`
  over 25% of `context_window()` (per LINK for compiled chains, since each
  link carries its own `_brief.chars`).
- `runlog.brief_summary(cwd, path)` → `{n, ok, red}`; red includes stopped
  (the document's credibility was spent either way).

Docs: HLD C9 dated amendment (brief_file/vars/amend_brief), C2 `BRIEF:` line,
C11 rewritten to the submit-cwd rule (engine_spec heartbeat worktree test
inverted accordingly). SKILL.md gained the Playbooks section + ask-list line;
CLAUDE-snippet gained one capability line. README/USAGE untouched (task #2).
New: qd/playbook.py, specs/playbook_spec.py (39 tests); amended: engine,
server, verdict, runlog, schemas + engine/async/verdict/runlog specs.
