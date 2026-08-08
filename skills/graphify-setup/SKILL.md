---
name: graphify-setup
description: Set up the graphify code graph on a project so the worker locates code instead of reading it. Use when asked to install / set up / index / enable graphify (or "the code graph") on a codebase. Encodes the safe sequence and the two traps — force `--no-cluster` for the structural index, and NEVER run a bare LLM/semantic graphify command: with no `--backend` graphify auto-selects from the environment (AWS Bedrock if `AWS_PROFILE` is set), billing a real cloud account and egressing the code.
---

# Setting up graphify

graphify is the optional code graph the **worker** (Qwen) locates through instead of
reading files — where the −69% existing-codebase saving comes from. This is the recipe to
stand it up safely. It is optional: absent, delegations still run (Qwen greps). The
**structural** graph is all delegation needs; **semantic** is a separate, deliberate step.

## The one rule that must not bend

**The only graphify subcommands that reach an LLM are `extract`, `label`, and
`cluster-only` — never run one without an explicit `--backend`.** Bare, graphify picks a
backend from the environment — **AWS Bedrock if `AWS_PROFILE` is exported** — which **bills
a real cloud account** and ships the corpus off-box. Everything else is local and LLM-free:
`update` (structural build/refresh) and the read queries `explain`/`path`/`affected`/
`query`/`diagnose`/`god-nodes`. So the setup below is entirely LLM-free; only touch a
semantic step (§3) if the user explicitly asks, and only with `--backend ollama` (local).

## 1. Install (once per machine)

Check first — `command -v graphify`; if present, skip.

    uv tool install "graphifyy[ollama]"     # package `graphifyy`; CLI `graphify`; pip works too

## 2. Index — structural, the default

    graphify update . --no-cluster          # ~2s, deterministic, no LLM, no egress

Writes `graphify-out/graph.json`. From here the supervised-delegation server keeps it fresh (also
`--no-cluster`, so it never touches an LLM) after every delegation — you do not re-run it.

## 3. Semantic naming — only if asked, and only explicitly

LLM-named clusters help orient in a large *unfamiliar* codebase; they are NOT needed for the
worker's lookups. It is a separate step *after* the structural build — `cluster-only` /
`label` / `extract` (exact flags vary by version: `graphify --help`). Run it only when the
user asks, as a deliberate one-off, and NEVER bare — always name the local backend:

    OLLAMA_BASE_URL=http://<endpoint>/v1 OLLAMA_MODEL=<model> OLLAMA_API_KEY=<key> \
      GRAPHIFY_MAX_WORKERS=1 graphify cluster-only . --backend ollama

It ships the code to that endpoint, so expect — and surface to the user — an approval
prompt. Confirm the endpoint is the user's own (localhost / LAN / their Tailscale box)
before proceeding; a third-party endpoint means shipping their code to a vendor.

## 4. Confirm it's live

After the structural index (or the next delegation), the receipt's `GRAPH:` line should
read `GRAPH: fresh @ <sha>`. `GRAPH: failed: graphify not installed` → step 1;
`GRAPH: none` → step 2.

## 5. How the worker uses it — nothing more for you to wire

Delegate in `approval_mode="scoped"`: that gives Qwen the shell for the allowlisted reads
(`graphify explain/affected/path/query/diagnose/god-nodes`), and the server-injected
`QWEN.md` graph-before-grep rule
does the rest. In `auto-edit` (no shell) the worker greps instead — still correct.
**Claude never queries graphify itself** — locate via `query` (measured +64% when
Claude uses the graph directly). Keep LLDs behavior-only.

Point at a non-default binary with `DELEGATION_GRAPHIFY=/path/to/graphify`.
