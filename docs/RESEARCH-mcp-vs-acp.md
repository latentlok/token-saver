# Parked: MCP vs ACP for the referee↔worker wire

Side-note research, 2026-07-21. **Not part of v2 scope** — saved for a future session.
Nothing here is probed yet; treat every capability claim as a lead.

## Context

Two protocols named "ACP" exist; they'd slot into different edges of this stack.
MCP's edge (Claude ↔ referee) is not in question — it is Claude Code's native
extension point, and the design *uses* MCP-specific machinery: tool schemas ambient in
every session (the capability-map layer; ACP has no equivalent), the 2h per-server
timeout, and >120s auto-backgrounding (what makes fire-and-keep-talking fan-out work).
graphify and firecrawl are MCP servers too.

The open question is the **referee ↔ Qwen** edge, today deliberately primitive:
spawn `qwen -p`, wait, parse JSON, exit.

## ACP #1 — Agent Client Protocol (Zed): the relevant one

JSON-RPC/stdio for a client driving an agent session: streaming turns, session
management, cancellation, and a **native permission-request flow** (agent asks the
client to approve each tool call). Qwen Code plausibly inherits support from its
Gemini CLI lineage (`--experimental-acp`) — unverified.

Would buy:
- **Scoped mode without the contraption.** Today: yolo + PreToolUse hook injected via
  temp settings (`QWEN_CODE_SYSTEM_SETTINGS_PATH`), denials batched as ELICITATION.
  With ACP the server is the client in a native approve/deny loop — same deterministic
  allowlist, no settings injection, mid-run denial reasons.
- Streaming stats; real cancellation (today: SIGKILL and a lost run-log record).

Why parked (not in v2):
- The trust model deliberately ignores what a richer protocol provides. ACP's offering
  is visibility into the worker's *process*; verdicts here come from end-state (tree,
  diff, gate). More stream tempts the manager to read narration it must not trust.
- One-shot statelessness is load-bearing (fresh session re-reads QWEN.md — measured);
  persistent ACP sessions invite the statefulness the design avoids.
- The headless surface is *measured* (approval modes, stats schema, compaction); ACP
  mode resets that evidence base on an experimental flag.
- ACP does NOT fix the known limitation people expect it to: the Claude-side manager
  still cannot answer mid-MCP-call (FINDINGS: live model-approval is impossible).
  ACP only upgrades *server-side* adjudication, which the hook already achieves.

## ACP #2 — Agent Communication Protocol (IBM/BeeAI; kin of Google A2A)

Agent↔agent federation over REST: discovery, agent cards, async tasks between
organizations. Wrong layer here — local orchestrator, local worker, git as substrate.
Adds HTTP surface + auth while providing none of what the referee does (gates,
snapshots, attribution). Relevant only if an executor ever becomes a *hosted agent
service*; the API-class future is "same CLI, different endpoint" and the profile/env
design covers it more simply.

## Verdict

| edge | today | verdict |
|---|---|---|
| Claude ↔ referee | MCP | Keep. Native; ambient schemas + backgrounding are architecturally used. |
| referee ↔ Qwen | headless CLI | Keep for v2. ACP (Zed) is the credible upgrade later — cleaner scoped mode, streaming, cancel — at the cost of trading a measured surface for an experimental one. |
| agent ↔ agent | — | Wrong layer; revisit only for hosted-agent executors. |

## If/when revisited: the probe

"Does qwen-code expose a working ACP mode, and does its permission flow reach a stdio
client?" — cheap to answer alongside the M0 probes. If yes, the candidate change is
exactly one module (`qd/invoke.py` grows an ACP transport behind the same
`run_executor` contract) plus retiring the scoped hook; nothing else in the design
moves.
