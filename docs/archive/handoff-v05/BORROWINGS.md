# What token-saver can borrow from existing architectures

2026-07-27. Method: borrow only what serves the three house theses — the gate
decides (never the worker's word), the receipt is a prompt (not a log), and the
repo is the truth (versioned files over conversation). Each section: what the
ecosystem does, what we take, what we deliberately refuse. A prioritized
adoption table closes the document. Items marked ✅ were already absorbed in
the v0.5 round.

## 1. Agent harnesses (Claude Code subagents, agent SDKs)

They do: context isolation with a compressed report back; schema-forced
structured outputs with validate-and-retry; background agents with completion
notification; per-task model routing; worktree isolation; permission hooks.

Take:
- ✅ Schema-validated results (`result_schema`, v0.5).
- ✅ Async submit + durable receipts (v0.5; the file IS the notification).
- **Judge / best-of-N with diverse lenses** — the one still missing. `workers`
  is advertised and unimplemented; when built, prefer N judges with DIFFERENT
  lenses (correctness / does-it-reproduce / scope) over N identical candidates
  — diversity catches what redundancy cannot. For gateable code the gate
  substitutes; this matters for report_dont_fix/audit work where no gate can
  bind.
- **Fork/checkpoint** (continue a session from a chosen point, not just its
  end) — cheap to approximate: briefs + session ids already stored; a
  `retry_of` variant that resumes WARM from attempt N's state is the lite
  version. Low priority; cold retry_of covers most of it.

Refuse: trusting the subagent's report (their receipts are prose; ours are
evidence), and transparent mid-task compaction (ours refuses — measured
fabrication trigger for this worker class).

## 2. Durable workflow engines (Temporal; also Airflow, GitHub Actions)

They do: journaled, replayable execution that survives process death; DAG
dependencies; retries as policy; matrix builds; concurrency groups.

Take:
- **Resumable chains** (Temporal's core idea, lite): journal each link's
  receipt as it lands (the partial receipt file already exists);
  `resume_chain: <run_id>` re-submits from the first unfinished link, guarded
  by the playbook digest (same document or refuse by name). Turns an 8-link
  overnight chain from all-or-nothing into checkpointed. Medium effort, high
  value once chains are used in anger.
- **Detached runner** (durability past session death) — already parked as the
  async v2; Temporal is the argument it is worth building eventually.
- **Matrix fan-out**: playbook slots × an inventory of var-sets compile to a
  `batch` (GH Actions `matrix` ≈ Ansible inventory). Near-zero server code on
  top of `vars` + `batch`; the codemod-over-40-targets case.
- **DAG steps** (`needs:` per step, parallel branches via worktrees under one
  chain) — defer until linear chains prove out; complexity is real and the
  endpoint is serial today anyway.
- **Content-keyed step skipping** (Make/Bazel via Actions cache): skip a link
  whose step text + input tree digest is unchanged since a green run. Clever,
  complex, easy to get wrong — parked as an idea, not a plan.

Refuse: a DSL. Playbooks stay prose-first markdown; the moment they become a
YAML programming language we have rebuilt Ansible without its tooling.

## 3. Spec-driven development (GitHub Spec Kit, Amazon Kiro)

They do: spec → plan → tasks decomposition as tooling; constitution files;
acceptance criteria per task.

Take:
- **Spec→playbook compilation as a delegable authoring aid** — "turn
  docs/design.md §4 into a playbook with steps and per-step gates" is itself a
  mechanical, gateable delegation (the output must parse: front matter, slots,
  steps). Zero server code; a skill recipe. The free worker authors the
  documents that drive it.
- Their existence validates per-step acceptance criteria (= per-step `verify`,
  already in the design) and standing constitutions (= QWEN.md + task_suffix,
  already built).

Refuse: spec-as-source-of-truth-for-CODE (their model regenerates code from
spec). Here the gate is the truth about code; the playbook is the truth about
the DELEGATION only.

## 4. Issue-driven agents (Devin, OpenHands, SWE-agent, Copilot coding agent)

They do: the issue is the durable brief; comments are amendments; the PR is
the deliverable; humans review in the tracker.

Take:
- **PR-as-receipt** (highest-value borrow in this document): a worktree
  success already produces a committed branch — optionally push it and open a
  PR whose body IS the receipt (gh CLI, config-gated, off by default; needs a
  remote). The reviewable diff + receipt-as-PR-body composes perfectly with
  the existing MERGE line, and the human collaboration layer comes free from
  GitHub instead of being built.
- **`brief_source` generalization** (later): an issue URL as the brief origin
  when the collaboration layer matters; the `brief_file` seam extends
  naturally. Not now — it drags auth + network into a stdlib server.

Refuse: tracker-as-primary-home. Briefs live beside the code they change;
the tracker can be a source, never the system of record.

## 5. Infrastructure-as-code (Ansible — the namesake)

They do: versioned parameterized step documents, inventories, check mode,
tags, secret vaults, a decade of operational proof.

Take:
- **Secret scrubbing** (their vault, our lite): a config list of regex/env
  patterns redacted from briefs before storage, from receipts before render,
  and from run-log records. Briefs park prompts on disk and receipts travel to
  the caller — neither should ever carry a leaked key. Small, genuinely
  important, spec-able. Should ride the playbooks phase or immediately after.
- **Tags/limit** (`steps: [2,4]` to run a subset of a playbook's steps) —
  trivially cheap once steps→chain exists; the re-run-one-link case.
- **Check mode** exists already as `approval_mode: "plan"` — a docs task, not
  a build task: name it as the dry-run in USAGE.

Refuse: Jinja-class templating. `{{var}}` with refuse-on-unknown stays; a
template language inside briefs is a debugging surface nobody asked for.

## 6. CI systems (required checks, quarantine, retention)

Take:
- **Gate flakiness tracking**: the run log already records the gate command's
  digest; a per-gate tally (n runs, gate_suspect count) surfaces "this gate
  has cried wolf 3 times" in the receipt — the gate_suspect classifier gets a
  memory. Low effort, extends the ledger machinery.
- **Receipts retention**: `.qwen-delegate/receipts/` grows one file per run;
  a keep-last-N (default generous, config-able) sweep at submit time. Trivial;
  fold into async v2 or playbooks phase.
- Required-vs-optional checks already exist as verify vs advisory_gates —
  validation, not a borrow.

## Priority table (effort × fit)

| Borrow | From | Effort | When |
|---|---|---|---|
| Secret scrubbing | Ansible vault / CI masking | S | playbooks phase or next |
| PR-as-receipt (config-gated) | issue-driven agents | S–M | after playbooks; needs remote + live probe |
| Matrix fan-out (vars × inventory) | GH Actions / Ansible | S | with or right after playbooks |
| Steps subset (`steps: [..]`) | Ansible tags | S | rides steps→chain |
| Gate flakiness tally | CI quarantine | S | any receipt-touching phase |
| Receipts retention | CI artifacts | S | async v2 |
| Resumable chains | Temporal | M | once chains see real use |
| Spec→playbook authoring recipe | Spec Kit / Kiro | S (skill only) | docs task #3 |
| Judge / diverse-lens best-of-N | harness patterns | M–L | with `workers`, needs endpoint capacity |
| Detached runner | Temporal | L | parked v2, probe-gated |
| DAG steps / content-keyed skipping | Actions / Bazel | L | parked ideas |
| brief_source = issue URL | issue-driven agents | M | only if collaboration layer is wanted |

Standing test for any future borrow: does it strengthen gate-decides,
receipt-as-prompt, or repo-as-truth? If it needs the worker to be trusted, a
DSL to be learned, or a service to be depended on, it does not come in.
