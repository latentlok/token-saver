# Live test queue — next session with the Qwen endpoint

The P1–P8 probe round is DONE (all recorded — see `PROBES-P1-P8.md`).
**Sections A and B are DONE (2026-07-31) — all green, recorded in
`VLLM-ROUND.md`** (two runtime fixes fell out: `2808cbc` executor ledger
label, `90645c2` usage token-provenance). The cutover config is standing:
vllm-snowy machine default, parallel_max 2, dispatch parallel. Only §C
remains; none of it is vLLM-gated.

## A. vLLM cutover gates (run first — everything in B depends on them)

- **A1 — vLLM executor profile works at all.** Write the
  `~/.qwen-delegate/executors.json` profile (argv template + `settings_overlay`
  pinning temperature 0.6 / top_p 0.95 / top_k 20 via `generationConfig`), point
  an endpoint entry at vLLM, run one trivial gated delegation through it.
  Gates: the whole cutover.
- **A2 — context window re-verify.** Read the real served context off vLLM,
  `python3 -m qd.doctor --verified <N>`. Every CONTEXT/compaction line is
  computed from it; wrong means receipts read "safe" while the endpoint
  truncates. Gates: honest compaction warnings under the new endpoint.
- **A3 — priority passthrough exists.** Does qwen-code forward a per-request
  priority field to an OpenAI-compatible endpoint at all (vLLM needs
  `--scheduling-policy priority` server-side)? Five-minute check BEFORE any
  priority design. Gates: the derived-priority-by-tool-shape design; if the
  channel doesn't exist, the endpoint slots stay the only scheduler and the
  design is shelved without regret.
- **A4 — token accounting shape on vLLM.** Whether `usage`/cached fields arrive
  and in what names — this is where the carried P5 (`usage` fallback,
  `qd/invoke.py` stream path) finally gets its live exercise, and P4's dead
  cache clause (`BURN:` HEAVY binds on `prompt − cached`) either renders or
  stays inert. Gates: BurnLimit and every live limit on the new endpoint.

## B. The parallel round (needs A green + `parallel_max: 2` + `dispatch: "parallel"`)

- **B1 — two concurrent qwen builds, live.** `batch` of two independent items
  fanned across worktrees on real capacity. Watch for: mid-tool-call
  truncation (the Ollama failure mode that made serial the default — vLLM's
  continuous batching should not have it, but that is exactly the unproven
  claim), per-item receipts, MERGE lines both sane.
- **B2 — machine-wide repo lock, live.** Two Claude sessions, one repo, both
  in-tree, different endpoints/profiles. Must serialize (CI proves it
  hermetically in `specs/serialize_spec.py`; this is the field confirmation).
- **B3 — worktree config default, live.** A delegation in this repo with NO
  worktree arg — `.qwen-delegate.json` says `"auto"`, so the receipt must
  carry `WORKTREE:`/`MERGE:` unprompted. Then the co-work case: keep editing
  the main tree while it runs; nothing of yours reverts, merge probe honest.
- **B4 — heartbeat under parallel load.** P8 recorded per-record unthrottled
  writes on one stream; two concurrent streams write two sidecars — confirm
  the submit-cwd fix keeps them apart (C11) and cadence stays usable.

## C. Carried follow-ups (endpoint needed, not vLLM-specific)

- **C1 (was P2) — MCP-namespaced tool fencing.** Still spec-only: the worker
  declined to call an MCP tool in both live attempts. Needs a task that forces
  one; until then unknown tools are judged by input shape.
- **C2 (was P6) — worker delete-command phrasings.** Collect real ones before
  building `allow_delete`/stray auto-clean (designed, deliberately not built —
  a guessing delete parser is the one bug class with no rollback).
- **C3 (was P7 follow-up) — sidecar-for-text fixtures.** Worker complies on
  comment-friendly formats, thrashes on JSON (provenance sidecar is
  binary-only today). The fix would make `fixture_provenance` default-on safe.
- **C4 — streaming stats gap.** `tools` / `lines_added` / `lines_removed` read
  0 in stream mode with no way to tell that from a measured zero; any run with
  a limit streams, so on a metered endpoint the gap lands where cost
  attribution matters.
- **C5 — `workers` (best-of-N).** Advertised, unimplemented. Parallel capacity
  (post-B) is what finally makes it worth building — N candidates, first
  gate-pass wins — or delete the schema claim.

## Standing rules for the round

Same discipline as the probe round: record-only where possible, one variable
per run, findings land in this folder with a RESULT line each, runtime code
changes only where a probe's result demands one (each its own commit).
