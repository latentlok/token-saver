# Handover — v0.6 friction-ledger round

**State: tree is DIRTY and UNCOMMITTED. Suite is green.**
`bash ci/run-specs.sh` → exit 0, **875 tests**. Branch `v0.6`.

Nothing here has been committed, because committing was never asked for. Read
`git status`, then commit in whatever slices you prefer — a suggested split is at
the bottom.

The live queue is **[docs/PLAN-v06-ledger.md](docs/PLAN-v06-ledger.md)**. Start there.
This file only covers what a fresh session cannot reconstruct from it.

---

## What this round was

`docs/archive/plugin-improvement.md` — a friction ledger kept while building a real
project with the plugin. 23 findings against v0.5.1, **none previously fixed**. The
organising insight, which shaped every fix: *almost every finding is the plugin
knowing something and not acting on it.* The server can see the endpoint capacity,
the gate timing, the changed files, the mocked modules — and instead of acting, it
documented the fact in prose and asked the caller to remember it.

So the work is: **deterministic code that acts, replacing prose that instructs.**

## Landed (all specced, all green)

11 of 23 findings closed, plus 4 things found while fixing them.

| Area | What changed |
|---|---|
| **Teardown** (A0a/A0c/A11) | `start_new_session` + `killpg` on both spawn sites. Mutation-checked: pre-fix leaked 1 orphan, post-fix 0 |
| **Seam risk** (A18/A20/A22) | `UNCALLED:`, `MOCKED SEAM:`, `NEVER EXECUTED:` — three greps, nothing executes |
| **Concurrency** (A13 + scheduling) | shared pre-flight; per-item scheduling; `parallel_max` as the single knob |
| **Heartbeat** (A7b/A17) | run id stamped at submit, `state: "starting"` before the first token |
| **Denials** (A15) | `grep_search` allowlisted, trailing `2>&1` permitted, `DENIALS:` split by whether it could change the result |
| **Timeouts** (§6 #7) | the fitted regression is code now, not skill prose |
| **Doctor** | `project_check()` — four static config traps |
| **Config lies** (A0b/A0e/A12/A16) | endpoints honoured, sidecar stops claiming "fresh", batch `cwd` inherited, TEST DODGE stops crying wolf |
| **Deleted** | `_ref_impl.py` (2,293 lines), `workers`, the serial/parallel branch, ~330 resident tokens/session |

## The four bugs found while fixing, not from the ledger

Worth knowing because each was invisible and none had a symptom anyone had reported:

1. **`new_public_symbols` was blind to new directories.** It read `status_map()`, and
   `git status --porcelain` collapses a brand-new directory into one `engine/` entry.
   A run delivering a whole new module package reported **zero** new public surface —
   the bigger the unit, the more completely it was missed.
2. **`verdict_spec` computed a full v1 receipt 12 times and threw it away.** Crane
   equality had been formally retired in its own docstring; the scaffolding stayed.
3. **The killed-run timeout advice was self-contradictory.** A kill truncates the
   telemetry, so fitting it advised "set timeout_sec=900" for a run that had just
   died at 900s. The only exact fact is *more than the budget*.
4. **The stale-server check was observer-dependent.** A loose pgrep pattern matched
   the shell running the check, so the count changed with how it was invoked.

## What is NOT done, and why

**§2.1 — the self-gate default (A14). Deliberately left for you.**
This is the highest-value item remaining and I did not touch it, because it changes
what every `trust="self"` delegation gates on. The evidence is unambiguous — runs
1–9 under the default produced **zero mergeable units**; runs 10–21 under a
worker-written gate + `preflight_expect="red"` produced **11 of 11** one-shot
successes, same model, same repo, same day — but a default-gate change while you are
asleep is not my call to make. The ledger's preferred fix is #3 of four: gate the
self-graded run on the test files the run itself created, which the plugin already
tracks in `CHANGED`. Note a partial mitigation already exists (`_ensure_self_gate`'s
`min_override` ratchet); decide whether to extend or replace it.

**Also open:** §2.2 `STATUS: busy` (A11 transport), §2.3 `challenge_brief` (A23),
§2.4 server lifecycle (A0d — doctor reports it now, nothing kills the stale one),
§4 the delegation skill hot/cold split (326 → ~100 lines), and the test tiering
below.

**Test tiering — agreed, not built.** You asked whether separating test dirs is a
good idea. Half of it is: splitting by *run requirement* (fast-offline vs
needs-network/GPU/creds) is standard. Splitting by *author* to keep Qwen's tests out
of the suite is the `NEVER EXECUTED:` defect by construction, and your own
`ci/run-specs.sh` already argues against it. The shape I'd build:

```json
"tests": { "gate": "specs", "suite": "unit_tests", "live": "gate_tests" }
```

with one rule from A19: a live test that skips itself reads as a pass, so the `live`
tier must report SKIPPED distinctly and never fold into green.

## Things you should decide

- **Version.** `.claude-plugin/plugin.json` still says `0.5.1`. I did not bump it —
  that is a release step, and `docs/RELEASING.md` owns the sequence. The root
  `CHANGELOG.md` has no 0.6.0 entry yet either.
- **`reset_worktree()`** in `qd/gittree.py` is now called only by its own spec; it
  existed for best-of-N, which was removed. Keep or drop.
- **`qd/doctor.py`** still carries Ollama-shaped advice (`OLLAMA_NUM_PARALLEL`,
  reading CONTEXT off `ollama ps`). You said Ollama will never be used again.
- **Your machine config was edited**: `~/.qwen-delegate/executors.json` now has
  `snowy: parallel_max 4`. That is outside the repo and outside git.

## Docs

Archived to `docs/archive/`: the friction ledger, the v0.5 handoff (7 files), and
the dev-round changelog. `docs/PLAN-v06-ledger.md` is new and is the live tracker;
`docs/PENDING.md` now points at it.

`templates/CLAUDE-snippet.md` shrank 524 → 193 tokens. The doctrine that made it big
(U2.7: every capability surfaces in the block) was **restated, not quietly broken** —
the rule now reads "no capability lives only in long-form docs", satisfied by the tool
schema plus the submit response. `specs/setup_spec.py` pins it and caps the block size.

## Suggested commit split

1. `_ref_impl.py` deletion + spec rewrites (self-contained, big diff)
2. `workers` removal
3. teardown / A0b / A12 / A16 + `specs/teardown_spec.py`
4. concurrency: profiles + `run_batch` + shared pre-flight + `specs/fleet_spec.py`
5. seam detectors + `specs/seams_spec.py` (+ the `new_public_symbols` fix)
6. timeouts + wire format + `specs/wireformat_spec.py`
7. denials + heartbeat + doctor
8. docs: archive moves, plan, CLAUDE snippet, this file

Verify each with `bash ci/run-specs.sh` (exit 0, 875 tests).
