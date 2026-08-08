# token-saver v0.5 — pending tasks (handoff; updated 2026-07-28)

Repo: `/home/<user>/projects/qwen-delegate`, branch `v0.5-field-report`,
tree CLEAN, suite `bash ci/run-specs.sh` = 775 tests, exit 0.
Commits so far: `26e750b` (phases 0–4), `2f4b729` (phase 5 async round),
`0f36f73` (docs/contracts), `e030857` (**Task 1 DONE** — phase 6 playbooks),
`c705f0b` (**Task 2 DONE** — README/USAGE rebuild). Remaining: Tasks 3–4.
Full build history: `## Build log` sections in
`/home/<user>/.claude/plans/wobbly-fluttering-ladybug.md` and at the end
of `ARCHITECTURE-playbooks.md` (this folder).

## Standing rules (apply to every task)

- Commit ONE commit per task, on `v0.5-field-report` — NEVER to master
  (merging to master publishes, per docs/RELEASING.md; that is the user's call).
- Suite must be exit 0 before each commit; verify independently after any agent.
- `specs/*_spec.py` are permanent gates — never remove, prune, or archive.
- Runtime code is Python stdlib only. House comment style: the REASON a guard
  exists (the measured failure that bought it), never what the next line does.
- Never touch `_ref_impl.py`. New params must be spec-proven inert when absent.
- Build with opus agents (user preference); verify their work yourself.
- CHANGELOG is human-only; agents learn from the CLAUDE.md managed block and
  receipt lines.

## Task 1 — Phase 6: playbooks (versioned delegation documents)

DESIGNED, NOT BUILT. The complete architecture — 9-item build order, five
decisions already taken, two implementation traps found on contact with the
code (content-based brief protection; chain compile at the server seam), trail
string pre-checked against all classifier substrings — is in
`ARCHITECTURE-playbooks.md` in this folder (canonical copy at the top of the
plan file). Build exactly that; do not re-derive. Also includes the C11
heartbeat-path fix (Progress writes to the SUBMIT cwd, not work_cwd). New
specs/playbook_spec.py + additions across engine/async/verdict/runlog specs.
Commit message theme: briefs live in the repo, versioned by git, sent by name.

## Task 2 — README + docs/USAGE rebuilt from scratch (AFTER playbooks)

Deliberately sequenced after playbooks: they change the recommended workflow
(brief-as-repo-file becomes the default; typed task params the fallback).
- Rewrite `README.md` and `docs/USAGE.md` from scratch against v0.5 behavior:
  async submit → WATCH → receipt file; co-work safety (attribution, never
  roll back caller edits); advisory gates; chains; retry_of; result_schema;
  recipe defaults + task_suffix; playbooks; heartbeat; resume-vs-cold
  heuristic; the routing decision table (direct edit vs delegation).
- README's "Step 6 MCP idle timeout" note is obsolete for delegations
  (submits return in seconds) — drop or scope it to wait:true.
- Move obsolete docs to `docs/archive/` (candidates: anything superseded by
  the rebuild; check docs/ against the current surface). NEVER specs.
- Polish `templates/CLAUDE-snippet.md` capability map within its ~350-token
  resident budget (it is the agent-facing surface, self-updating per U5.3).
- `docs/OVERVIEW.md`: refresh pointers/claims that the rebuild invalidates.
- Own commit.

## Task 3 — live probes P1–P8 (needs the Qwen endpoint FREE)

Recorded with what each gates in `docs/PENDING.md` (v0.5 section). Headlines:
P1 autoedit-as-yolo+hook parity → gates `autoedit_via_hook` default;
P2 PreToolUse deny for MCP-namespaced tools; P3 real edit/replace tool_input
field names; P4 cached-token counters (BURN cache note condition);
P5 usage-fallback + BurnLimit on a real stream; P6 worker delete phrasing →
gates allow_delete (designed, not built); P7 fixture-provenance compliance
loop → gates its default; P8 progress.json write cadence. ~1 hour of
supervised runs total. Also the first live exercise of async submit/receipts.

## Task 4 — release decision (user's call, not an agent task)

When 1–3 are done: version bump in .claude-plugin/plugin.json (+ marketplace
metadata check), final CHANGELOG polish, then the user decides on merging
`v0.5-field-report` → master (= publishing).

## Parked / future (recorded, no commitment)

- Detached-runner daemon (async runs surviving session death) — probe-gated v2.
- Judge/best-of-N verification for ungateable work; `workers` param still
  advertised-not-implemented (deliberate, in PENDING).
- Trust L1–L4 intermediate dial stops; capability slider outward (API-class
  executors — config recipe in PENDING's API-expansion readiness note).

## See also

- `BORROWINGS.md` (this folder) — what to adopt from existing architectures,
  prioritized (secret scrubbing, PR-as-receipt, matrix fan-out are the near-term
  smalls; resumable chains once chains see real use).
