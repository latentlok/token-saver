# Graph Report - .  (2026-07-23)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 801 nodes · 1444 edges · 49 communities (40 shown, 9 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 11 edges (avg confidence: 0.66)
- Token cost: 3,176 input · 8,103 output

## Graph Freshness
- Built from commit: `50a7b82a`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Core Engine Testing
- Git State Management
- Machine Profile Testing
- Profile Resolution Logic
- Batch Dispatch Testing
- Verdict Formatting Testing
- Git Status Testing
- Graph Index Management
- Command Invocation Testing
- Graph State Testing
- Project Bootstrap Testing
- Execution Log Testing
- Delegation Control Flow
- Execution Telemetry Processing
- Git Worktree Testing
- Worktree Management Logic
- Core Invocation Hooks
- Query Execution Testing
- Reference Tracking Testing
- Project Initialization Logic
- Verification Gate Testing
- Project Bootstrap Module
- Repository Spec Tracking
- Run Record Assembly
- Query Execution Engine
- System Architecture Documentation
- Delegation Policy Framework
- Delegation Entry Point
- Code Change Tracking
- Delegation Framework Overview
- Supported AI Models
- Reference File Management
- External Tool Integrations
- Core Development Tools
- Delegation Configuration
- Multi-Agent System Components
- Scoped Approval Hook
- Agent Delegation Interface
- Runtime Configuration Files
- Architect Agent Skills
- Compaction State Tracking
- Design Abstraction Levels
- Interface Contract Management
- Specification Quality Standards
- Self-Test Gate Script
- Archive Documentation
- Worker Configuration Rules

## God Nodes (most connected - your core abstractions)
1. `delegate()` - 28 edges
2. `_delegate_once()` - 23 edges
3. `render()` - 19 edges
4. `git()` - 18 edges
5. `render()` - 17 edges
6. `put()` - 17 edges
7. `Fixture` - 16 edges
8. `log()` - 12 edges
9. `git()` - 12 edges
10. `Fixture` - 12 edges

## Surprising Connections (you probably didn't know these)
- `run_gate()` --calls--> `_ensure_self_gate()`  [EXTRACTED]
  specs/trust_spec.py → qd/engine.py
- `qwen_query Tool` --conceptually_related_to--> `Graphify Integration`  [INFERRED]
  README.md → docs/HLD.md
- `Approval Modes` --conceptually_related_to--> `Spec Guard`  [INFERRED]
  context/SYSTEM.md → docs/FINDINGS.md
- `Verify Gate` --shares_data_with--> `Run Log`  [EXTRACTED]
  docs/PRINCIPLES.md → context/SYSTEM.md
- `Graphify Integration` --shares_data_with--> `Qwen (Executor)`  [EXTRACTED]
  docs/HLD.md → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Delegation Loop Core Components** — concept_qwen_delegate, concept_verify_gate, concept_spec_guard, concept_blast_radius, concept_run_log, concept_worktrees [EXTRACTED 1.00]
- **L5 Architect Pipeline** — skills_architect_SKILL_l5_architect_loop, skills_architect_SKILL_prd, skills_architect_SKILL_srs [EXTRACTED 1.00]
- **Benchmarked Coding Models** — docs_archive_RESEARCH_delegation_landscape_qwen2_5_coder_32b, docs_archive_RESEARCH_delegation_landscape_gpt_4o, docs_archive_RESEARCH_delegation_landscape_claude_3_5_sonnet [EXTRACTED 1.00]

## Communities (49 total, 9 thin omitted)

### Community 0 - "Core Engine Testing"
Cohesion: 0.09
Nodes (14): Accounting, Fixture, GraphWiring, Loop, MutationHardening, Prefilter, M4 seam: worktree='auto' runs the whole loop in an isolated container.     On su, M4 seam: per-task allowlist -- modify only named pre-existing files;     creatin (+6 more)

### Community 1 - "Git State Management"
Cohesion: 0.08
Nodes (41): blast_radius(), committed_during_run(), file_sha(), git(), head_sha(), new_public_symbols(), _project_config(), _publics_in_line() (+33 more)

### Community 2 - "Machine Profile Testing"
Cohesion: 0.08
Nodes (13): BuiltinPin, Cost, Endpoints, Fixture, Precedence, Case 2: the four-level chain, each level beating the next., Structured errors, never tracebacks-by-accident., Case 3: C7 -- the endpoint is the concurrency domain. (+5 more)

### Community 3 - "Profile Resolution Logic"
Cohesion: 0.08
Nodes (32): _apply_defaults(), _load(), _machine_path(), ProfileError, Exception, Resolve the executor profile to use for a delegation.      Precedence: call_exec, Raised for all profile resolution refusals., Load and parse a JSON file. Returns (data, error_message). (+24 more)

### Community 4 - "Batch Dispatch Testing"
Cohesion: 0.13
Nodes (9): Batch, Concurrency, Drain, Failure, Fixture, Protocol, M4/probe-5 seam: one MCP call carrying N delegation items, fanned     server-sid, One live dispatch subprocess; responses collected by a reader thread. (+1 more)

### Community 5 - "Verdict Formatting Testing"
Cohesion: 0.10
Nodes (9): C2Lines, CompactGreen, Fixture, Helpers, LogSeam, R2 (PLAN-v3-l5): a clean success renders COMPACT -- diagnostics appear     only, Non-success keeps the full diagnostics., sh() (+1 more)

### Community 6 - "Git Status Testing"
Cohesion: 0.11
Nodes (13): Basics, BlastRadius, commit_all(), Commits, Fixture, LinkedWorktree, make_repo(), Publics (+5 more)

### Community 7 - "Graph Index Management"
Cohesion: 0.11
Nodes (30): _do_refresh(), graph_line(), graphify_cmd(), Synchronously refresh the graph index.      Never raises — advisory infrastructu, Start a background thread running refresh_sync; return the thread.      Writes ", Return a single C2 GRAPH: status line., Path to .qwen-delegate/graph.json., Parse the sidecar JSON, or return None if absent/unparseable. (+22 more)

### Community 8 - "Command Invocation Testing"
Cohesion: 0.13
Nodes (5): Failures, Fixture, Invocation, PureFunctions, SettingsMerge

### Community 9 - "Graph State Testing"
Cohesion: 0.12
Nodes (7): Async, Fixture, GraphifyCmd, GraphLine, sh(), Sidecar, Staleness

### Community 10 - "Project Bootstrap Testing"
Cohesion: 0.14
Nodes (7): Bootstrap, DetectTable, mkrepo(), put(), Case 1: detection matches the crane AND the expected command., RenderRules, StatusAndNotices

### Community 11 - "Execution Log Testing"
Cohesion: 0.11
Nodes (6): ConcurrentAppends, Fixture, Invisibility, NeverRaises, RecordShape, Registry

### Community 12 - "Delegation Control Flow"
Cohesion: 0.14
Nodes (23): bootstrap_failed_refusal(), bootstrap_notice(), nongit_refusal(), The rules file could not be written (IO error, template drift). Refuse rather th, Missing rules AND not a git repo: cannot self-configure safely, so refuse., The SETUP line prepended to a verdict when the rules file was just auto-created., delegate(), _ensure_self_gate() (+15 more)

### Community 13 - "Execution Telemetry Processing"
Cohesion: 0.14
Nodes (21): accum_stats(), _cleanup(), compact_hooks(), cum_zero(), norm_tokens(), parse_qwen_json(), parse_stats(), peak_context() (+13 more)

### Community 14 - "Git Worktree Testing"
Cohesion: 0.13
Nodes (8): Acquire, Fixture, Isolation, MergeProtocol, Documented non-defect survivors of the adversarial mutation pass     (worktree r, Release, Residuals, sh()

### Community 15 - "Worktree Management Logic"
Cohesion: 0.15
Nodes (20): acquire(), _base_dir(), _branch_exists(), classify_merge(), _main_git_dir(), merge_lines(), Exception, worktree isolation, behavior frozen by specs/worktree_spec.py  Git worktree isol (+12 more)

### Community 16 - "Core Invocation Hooks"
Cohesion: 0.15
Nodes (20): accum_stats(), _cleanup(), compact_hooks(), compact_setup(), invoke_qwen(), norm_tokens(), parse_qwen_json(), parse_stats() (+12 more)

### Community 17 - "Query Execution Testing"
Cohesion: 0.19
Nodes (4): Basics, Errors, Fixture, LogSeam

### Community 18 - "Reference Tracking Testing"
Cohesion: 0.16
Nodes (4): Detection, Fixture, GitInvisibility, ReceiptLine

### Community 19 - "Project Initialization Logic"
Cohesion: 0.14
Nodes (17): ack_compaction(), bootstrap_worker_rules(), detect_test_cmd(), log(), The prompt for attempt N+1. Returns (prompt, action) where action is one of, Create <cwd>/.qwen-delegate/ holding a self-ignoring .gitignore.      The `*` pa, Add cwd to the global pointer index if absent. Paths only -- an aggregator reads, Append one run record. Best-effort by contract -- never raises. (+9 more)

### Community 20 - "Verification Gate Testing"
Cohesion: 0.21
Nodes (5): ReceiptTrustLine, run_gate(), SelfGateScript, TrustPrecondition, write_suite()

### Community 21 - "Project Bootstrap Module"
Cohesion: 0.17
Nodes (15): bootstrap_worker_rules(), detect_test_cmd(), _log(), The rules file whose rules a run in `cwd` would actually load, or None.      Wal, ("ok"|"missing"|"placeholder", path_or_None) -- is this project configured?, Create the rules file so a first delegation just works.      Returns (test_cmd,, One paragraph: what is wrong and why it matters. Shared by refusal and warning., The read-only counterpart. A query cannot write, so refuse with context. (+7 more)

### Community 22 - "Repository Spec Tracking"
Cohesion: 0.14
Nodes (15): committed_during_run(), git(), The QWEN.md whose rules a run in `cwd` would actually load, or None.      Qwen l, Protected-spec patterns for this project. Per-project config wins., Reset the working tree to a committed base so the next best-of-N candidate start, Tracked protected-spec paths, repo-relative. Language-agnostic., Tracked spec files that differ from `base` (default: HEAD, i.e. uncommitted edit, Restore spec files from `base` (default: HEAD).      Base matters for the same r (+7 more)

### Community 23 - "Run Record Assembly"
Cohesion: 0.14
Nodes (15): compaction_state(), cum_zero(), digest(), leverage_record(), now_iso(), parse_handoff(), Truncated head + full-text hash. Enough to identify and group runs without     p, Assemble the common half of a run record.      `verdict` is the exact string han (+7 more)

### Community 24 - "Query Execution Engine"
Cohesion: 0.14
Nodes (15): compaction_thresholds(), context_window(), main(), Back-compat alias: the codebase map is now qwen_query(format='map')., Configured context window for the active model, or None., Mirrors qwen's computeThresholds() (chunks/chunk-NJOFRXTM.js):       DEFAULT_PCT, ("ok"|"missing"|"placeholder", path_or_None) -- is this project configured to de, One paragraph: what is wrong and why it matters. Shared by refusal and warning. (+7 more)

### Community 25 - "System Architecture Documentation"
Cohesion: 0.14
Nodes (14): System Reference, Testing Brief, v1 Architecture, v2 Direction, v3 Architect Model, v3 L5 Plan, M0 Probes, Findings & Evidence (+6 more)

### Community 26 - "Delegation Policy Framework"
Cohesion: 0.15
Nodes (13): Verification Gate, Approval Mode, Best-of-N Workers, Delegation Loop, Reflexion Retries, Trust Self, Trust Verified, Verify Gate (+5 more)

### Community 27 - "Delegation Entry Point"
Cohesion: 0.19
Nodes (13): bootstrap_failed_refusal(), bootstrap_notice(), _delegate_once(), head_sha(), is_git_repo(), nongit_refusal(), Return (passed, combined_output)., Best-of-N entry point (#26). Runs up to `workers` independent candidates from th (+5 more)

### Community 28 - "Code Change Tracking"
Cohesion: 0.17
Nodes (12): blast_radius(), file_sha(), new_public_symbols(), _publics_in_line(), {path: porcelain status code} for the working tree., Content hash, or None if unreadable/absent., {path: (status_code, content_sha)} for every dirty path.      The sha matters: c, Public symbol names defined on this (de-plussed) diff line, or []. (+4 more)

### Community 29 - "Delegation Framework Overview"
Cohesion: 0.22
Nodes (11): Approval Modes, Blast Radius, Git Substrate, Leverage Ratio, qwen_delegate Tool, Run Log, Spec Guard, Trust Dial (+3 more)

### Community 30 - "Supported AI Models"
Cohesion: 0.20
Nodes (10): Cheap-Executor Delegation, Claude 3.5 Sonnet, Claude Haiku 4.5, DeepSeek V3, Gemini 2.0 Flash, GPT-4o, GPT-5-mini, MiniMax M2.5 (+2 more)

### Community 31 - "Reference File Management"
Cohesion: 0.29
Nodes (7): added(), refs pinning — the refs dir is git-ignored so git diff cannot see it; this modul, Return sorted list of filenames that are new or changed since *before*., Return C2 receipt line, or None for an empty list., Return {relative_filename: (size, mtime_ns)} for files in .qwen-delegate/refs/., refs_line(), snapshot()

### Community 32 - "External Tool Integrations"
Cohesion: 0.29
Nodes (7): Claude (Architect), Context Compaction, Firecrawl Web Access, Graphify Integration, Ollama Backend, Qwen (Executor), qwen_query Tool

### Community 33 - "Core Development Tools"
Cohesion: 0.29
Nodes (7): graphify Tool, L5 Architect Loop, Module Tree, Product Requirements Document, qwen_delegate Tool, qwen_query Tool, Software Requirements Specification

### Community 34 - "Delegation Configuration"
Cohesion: 0.33
Nodes (6): project_config(), Parsed <cwd>/.qwen-delegate.json (the per-project override file), or {}.      Re, Retry budget: attempts = 1 initial + (N-1) retries. Precedence:     per-call arg, Best-of-N breadth (#26): number of INDEPENDENT candidates to try for one task,, resolve_max_iter(), resolve_workers()

### Community 35 - "Multi-Agent System Components"
Cohesion: 0.50
Nodes (4): Anthropic Multi-Agent System, EcoAssistant, Local-Splitter, Mid-Tier Paid Executor

### Community 36 - "Scoped Approval Hook"
Cohesion: 0.83
Nodes (3): decide(), log_deny(), main()

### Community 37 - "Agent Delegation Interface"
Cohesion: 0.67
Nodes (3): Qwen Manager Agent, Delegate Command, Delegation Skill

## Knowledge Gaps
- **57 isolated node(s):** `python3`, `gate_selfsuite.sh script`, `Qwen Manager Agent`, `Architect Agent`, `Delegate Command` (+52 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `delegate()` connect `Delegation Control Flow` to `Git State Management`, `Execution Telemetry Processing`, `Worktree Management Logic`, `Project Bootstrap Module`, `Reference File Management`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Why does `git()` connect `Git State Management` to `Delegation Control Flow`, `Project Bootstrap Module`, `Graph Index Management`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **What connects `python3`, `gate_selfsuite.sh script`, `Qwen Manager Agent` to the rest of the system?**
  _57 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Core Engine Testing` be split into smaller, more focused modules?**
  _Cohesion score 0.09294199860237597 - nodes in this community are weakly interconnected._
- **Should `Git State Management` be split into smaller, more focused modules?**
  _Cohesion score 0.08194905869324474 - nodes in this community are weakly interconnected._
- **Should `Machine Profile Testing` be split into smaller, more focused modules?**
  _Cohesion score 0.08392603129445235 - nodes in this community are weakly interconnected._
- **Should `Profile Resolution Logic` be split into smaller, more focused modules?**
  _Cohesion score 0.08258258258258258 - nodes in this community are weakly interconnected._