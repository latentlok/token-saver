# Research: The Expensive-Planner / Cheap-Executor Delegation Landscape

*Grounded survey for the qwen-delegate architecture (expensive Claude orchestrator writes an objective shell gate, delegates coding to a free local Qwen executor, the gate — not re-reading output — decides success).*

**Status of our own numbers (context, not researched here):** delegation currently costs MORE of Claude's own tokens than solo (+69% small tasks, +34% large), because the per-delegation preamble (system prompt + ~6k-token agent definition + ~4k-token tool schemas) is cached but re-read every turn. Everything below is read against that problem.

**How to read the citations:** every claim is tied to a URL. Where a number comes from a vendor blog, a community README, or a source I could not fully extract, it is explicitly flagged **[UNVERIFIED]** or **[VENDOR CLAIM]**. Two future-dated model names ("Fable 5", "Sonnet 5", "GPT-5-mini", "Haiku 4.5", "DeepSeek V4") appear in 2026-dated sources returned by search; I treat the *pattern* they illustrate as solid and the *exact figures* as claims, not facts.

---

## 1. Landscape — who splits an expensive planner from a cheaper executor, and how

The field splits into two boundary styles: **role split** (different *jobs*, often same model) and **cost/capability split** (different *models* by price). Only some are true expensive-plan / cheap-execute. The table maps each; detail follows.

| System | Boundary drawn by | Expensive model does | Cheap model does | True cost-tier split? |
|---|---|---|---|---|
| Aider architect/editor | Reasoning vs edit-formatting | Describe the solution | Turn description into file edits | **Yes** (canonical) |
| Roo Code Orchestrator (Boomerang) | Mode (per-mode model) | Plan / decompose | Execute subtask in Code mode | **Yes**, configurable |
| Claude Code subagents / pilotfish | Subagent model alias | Plan, review, verify | Discovery, mechanical edits | **Yes** |
| OpenHands CodeAct | Controller vs agent | (single LLM) | (single LLM) | No — controller is code, not an LLM |
| SWE-agent | Agent-Computer Interface | (single LM) | — | No |
| MetaGPT / GPT-Pilot | SOP role | (usually one base model) | (usually one base model) | Mostly no (role split) |
| LangGraph supervisor / CrewAI hierarchical / AutoGen | Supervisor vs worker | Route / delegate / review | Do the task; execute code | Optional — you assign models |

### Aider architect/editor — the cleanest expensive-plan/cheap-execute
Aider's `--architect` mode sends each request to two models: the **architect** model is asked to *describe how to solve the problem*, then a separate **editor** model (`--editor-model`) is *given the architect's solution and asked to produce specific code-editing instructions to apply to source files*. The expensive model reasons; the cheap model does the mechanical diff-formatting, which is where weaker models actually fail (edit-format compliance), not where they fail on reasoning.
Benchmark numbers from the release (aider's own code-editing benchmark):
- o1-preview (architect) + deepseek (editor): **85.0%**; o1-preview + claude-3.5-sonnet: **82.7%**
- Claude 3.5 Sonnet paired with *itself*: **77.4% → 80.5%**; GPT-4o self-pair: **71.4% → 75.2%**
- o1-mini + deepseek: **71.4%**, vs o1-mini solo **61.1%** — a weak-cheap editor lifted a strong architect materially.
- Sources: https://aider.chat/2024/09/26/architect.html , https://aider.chat/docs/usage/modes.html
Note the last point cuts *for* us: the editor's job is narrow enough that even a weak model helps, and the paired cost is dominated by the architect. This is exactly the "expensive plans, cheap executes" shape — but Aider makes it cheap because the editor turn is *short* (a diff), not because it delegates a whole task with a heavy preamble.

### Roo Code "Orchestrator / Boomerang Tasks" — per-mode model, summary-only return
Roo Code (a Cline fork) has a dedicated 🪃 **Orchestrator** mode: it decomposes a task, spawns each subtask in a specialized mode (💻 Code, 🏗️ Architect, 🪲 Debug), **the parent pauses and later resumes with only the subtask's summary — not its full transcript.** Crucially, you can assign a *different model per mode* (e.g. a strong model for Architect, a cheaper one for Code) and it swaps automatically. This is the closest production analogue to our design, and the "return only a summary" detail is the mechanism that keeps the orchestrator's context from ballooning.
- Sources: https://docs.roocode.com/features/boomerang-tasks , https://docs.roocode.com/basic-usage/using-modes , https://docs.roocode.com/features/custom-modes
- Multi-model setup walkthrough (Gemini for Architect, Claude for Code): https://xebia.com/blog/multi-agent-workflow-with-roo-code-2/

### Claude Code subagents + pilotfish — role-based model routing
Claude Code lets each subagent pick a model (`sonnet`/`opus`/`haiku`/full IDs); the built-in **Explore** agent is read-only and runs on Haiku for cheap discovery. https://code.claude.com/docs/en/sub-agents
**pilotfish** (community project) is essentially our architecture productized: frontier model plans/decides/reviews in the main session; eight cheaper specialized roles execute — `scout`/Explore (cheap, read-only), `mech-executor` (mid, mechanical refactors), `executor` (implementation), `plan-verifier` and `verifier` (fresh-context review, read-and-run tools, never fixes). Verification is layered: pre-approval plan review + post-hoc fresh-context refutation. https://github.com/Nanako0129/pilotfish
- pilotfish cites **"Fable 5 orchestrator + Sonnet 5 workers at 96% of all-Fable performance for 46% of the cost"** and a community "58% savings" — **[VENDOR/COMMUNITY CLAIM, model names unverifiable at my cutoff]**. The independently verifiable Anthropic figure of the same shape is in §5.

### OpenHands CodeAct & SWE-agent — *not* cost-tier splits (important correction)
Both are frequently miscited as planner/executor splits. They are not model splits:
- **OpenHands CodeAct** uses a controller-agent-runtime structure where the `AgentController` is a *supervisor of the loop* (enforces iteration/budget limits, lifecycle) — it is **code, not a second LLM**. One LLM generates `CmdRun`/`FileEdit`/`AgentFinish` actions against a sandbox. SDK reports **72% on SWE-bench Verified** (Claude Sonnet 4.5 + extended thinking). https://www.openhands.dev/blog/openhands-codeact-21-an-open-state-of-the-art-software-development-agent , SDK paper https://arxiv.org/html/2511.03690v1
- **SWE-agent** (Princeton, NeurIPS 2024) contributes the **Agent-Computer Interface** — a small, guarded action set for viewing/searching/editing — with a single LM. Original SWE-bench pass@1 **12.5%**. https://arxiv.org/abs/2405.15793 , https://github.com/SWE-agent/SWE-agent
Takeaway for us: their "supervisor" is a *deterministic harness*, which is philosophically aligned with our gate — the cheap thing is a script, not a model.

### MetaGPT & GPT-Pilot — role split (SOP), mostly single base model
- **MetaGPT** (ICLR 2024) encodes a software company as an SOP: Product Manager → Architect → Project Manager → Engineer → QA. "Code = SOP(Team)." Roles verify each other's intermediate artifacts to reduce error propagation, but the split is by *responsibility*, not by *model price* — typically one strong base model plays all roles. https://arxiv.org/abs/2308.00352 , https://github.com/FoundationAgents/MetaGPT
- **GPT-Pilot / Pythagora** has the most executor-like decomposition: a **Developer** agent describes a task, a **Code Monkey** agent writes the actual file changes, a **Reviewer** agent checks each step and bounces bad work back to Code Monkey. It also filters context so each LLM call sees only relevant files (a token-control tactic we care about). https://github.com/Pythagora-io/gpt-pilot , https://blog.pythagora.ai/gpt-pilot-coding-workflow-part-2-3/
  - **[UNVERIFIED]** A search summary claims a credential-stealer was found in `core/telemetry/` (Aug 2025–Jun 2026). Treat as rumor until confirmed against the repo's security advisories; irrelevant to the architecture either way.

### Supervisor + worker frameworks — the model split is *opt-in*
- **LangGraph supervisor**: a central supervisor decides which worker runs next over shared state with an explicit termination condition; supports multi-level hierarchies (supervisor of supervisors). Models are per-node, so cost-tiering is your choice. https://github.com/langchain-ai/langgraph-supervisor-py
- **AutoGen**: the **UserProxyAgent executes code and returns auto-feedback (success/failure + output) to the AssistantAgent, which debugs and resends** — an execution-grounded loop with no human and no re-reading of prose. `SocietyOfMindAgent` hides an inner group chat behind a single agent surface. https://microsoft.github.io/autogen/0.2/docs/notebooks/agentchat_auto_feedback_from_code_execution/
- **CrewAI hierarchical process**: a `manager_llm` (or custom manager agent with `allow_delegation=True`) delegates to workers, reviews outputs, re-delegates. https://docs.crewai.com/en/learn/hierarchical-process
  - **Directly relevant caveat:** critiques report the manager adds **30–50% extra token usage** vs sequential, and delegation logic is fragile (all tasks can run sequentially, outputs overwritten). https://towardsdatascience.com/why-crewais-manager-worker-architecture-fails-and-how-to-fix-it/ — this mirrors *our own* finding that orchestration overhead can swamp the savings.

---

## 2. Trust & verification without re-reading the executor's output

Ranked by how well each lets the orchestrator **avoid re-reading the full implementation**:

**A. Execution-based validation (tests/gates) — best fit; this is our design.**
The orchestrator trusts a *program's* exit code, not the model's prose. Real instances:
- AutoGen's UserProxyAgent auto-feedback loop (code runs; pass/fail is the signal). https://microsoft.github.io/autogen/0.2/docs/notebooks/agentchat_auto_feedback_from_code_execution/
- SWE-bench itself grades by **hidden test suites**, never by reading the patch. https://arxiv.org/abs/2405.15793
- **EcoAssistant** wires the assistant to an automatic code executor and iterates on execution results. https://arxiv.org/abs/2310.03046
This is the only class that fully removes "read and trust the diff." Its weakness is gate quality: a gate that under-specifies success is gameable — which is why our gate discipline (non-gameable specs) is load-bearing.

**B. Diff-review-only (read the change, not the whole file).**
Aider's architect never re-reads full source; it reasons over a plan, and the editor emits a diff. GPT-Pilot's Reviewer inspects *per-step changes*. This is cheaper than re-reading but still costs orchestrator tokens proportional to diff size, and still requires the expensive model to judge correctness.

**C. LLM-as-judge / verifier model.**
A separate model scores the output. Widely used, but **reliability is contested** — use with skepticism:
- Survey: https://arxiv.org/abs/2411.15594
- "No judge is uniformly reliable across benchmarks"; agreement/consistency/bias problems: https://arxiv.org/pdf/2606.19544
- "One Token to Fool LLM-as-a-Judge" — trivial tokens flip judgments: https://arxiv.org/pdf/2507.08794
Implication: an LLM judge is *not* a safe substitute for an executable gate; at best a cheap pre-filter before the gate.

**D. Self-consistency / majority voting.**
Sample N solutions, take the most frequent; "the most frequently generated code is more likely correct." Recovers 5–15 accuracy points on reasoning benchmarks; **Adaptive-Consistency** stops early once a majority is clear, cutting samples. The orchestrator reads *nothing* — it only votes/compares — but you pay N× executor tokens (free for us) and need a canonicalizer to compare outputs. https://arxiv.org/pdf/2305.11860 , https://zeroentropy.dev/concepts/self-consistency/

**E. Reflexion / self-critique (executor fixes itself).**
Reflexion converts test/environment feedback into a verbal reflection stored in episodic memory and retried — SOTA on code-gen benchmarks *without* weight updates. This keeps the fix loop inside the *cheap* model, so the orchestrator only sees the final gated result. https://arxiv.org/abs/2303.11366

**Verdict for us:** our gate (class A) is the strongest known way to avoid re-reading. Layer D/E *inside the free executor* (self-consistency + reflexion against the gate) to raise first-pass gate-pass rate at zero orchestrator-token cost. Avoid leaning on C.

---

## 3. Routing & cascade economics (the theory behind the "trust slider")

**FrugalGPT** (Chen, Zaharia, Zou; TMLR) — the foundational cascade: query cheap models first, a learned scorer decides if the answer is good enough, escalate only if not. Three lever families: prompt adaptation, LLM approximation, LLM cascade. Headline: **match GPT-4 with up to ~98% cost reduction, or +4% accuracy at equal cost.** https://arxiv.org/abs/2305.05176
> Trust-slider mapping: FrugalGPT's *scorer threshold* IS a trust slider. Raise the acceptance threshold → escalate more → higher trust demanded of the cheap model.

**RouteLLM** (Berkeley/LMSYS, ICLR 2025) — trains a router on preference data to predict P(strong model wins) for a query, then routes by a cost threshold; **>2× cost reduction with no quality loss**, generalizes to model pairs unseen in training. https://arxiv.org/abs/2406.18665 , https://github.com/lm-sys/RouteLLM
> Trust-slider mapping: routing is *predictive* (decide before running) vs cascade's *reactive* (run cheap, escalate on low confidence). For coding we can afford reactive because the gate gives a ground-truth "did it work," which is a far better escalation signal than a router's guess.

**Mixture-of-Agents** (arXiv 2406.04692) — layered *proposers* (diverse, can be cheap/open models) + an *aggregator* that synthesizes. Open-source-only MoA hit **65.1% AlpacaEval 2.0 vs GPT-4o's 57.5%**. "MoA-Lite" uses fewer layers + a cheaper aggregator for cost. Relevant as a *partial-delegation* pattern (cheap drafts, one model refines) more than a router.

**Confidence-based escalation, formalized.** "**Is Escalation Worth It? A Decision-Theoretic Characterization of LLM Cascades**" (2026) frames cascade escalation as constrained optimization and characterizes when escalating is +EV vs not. https://arxiv.org/abs/2605.06350 . General cascade practice reports **50–80% cost reductions** when the request distribution is tail-heavy (most tasks easy). https://tianpan.co/blog/2025-11-03-llm-routing-model-cascades

**EcoAssistant** (arXiv 2310.03046) — the coding-specific cascade: cheap assistant first, back off to expensive only on failure, plus retrieved solution demonstrations. **Beat GPT-4 by ~10 points success rate at <50% of GPT-4 cost**; the hierarchy alone gave 30–50% savings. This is the closest published result to what we're trying to do.

**Core theory for WHEN to route cheap vs expensive:** route cheap when (a) expected difficulty is low, or (b) you have a *cheap, reliable success signal* to catch failures. Our situation is unusually favorable for (b): the gate is a near-perfect success signal. That argues for **reactive cascade keyed on gate outcome**, not a learned predictive router.

---

## 4. Partial delegation — cheap does part, expensive finishes/fixes

**Expensive-plans / cheap-executes** — Aider architect/editor is the proven instance (§1); the editor turn is short and cheap because it's a diff. **Measurably helps** (self-pairing gains +3 pts; weak editor still lifts strong architect). https://aider.chat/2024/09/26/architect.html

**Cheap-drafts / expensive-reviews-or-patches** — "instead of generating from scratch, a local model drafts, the cloud model reviews or patches; cloud output tokens drop because it edits rather than authors." Because output tokens dominate cost, a 90%-correct local draft turns the cloud into a 10%-correction.
- Measurement study: "**Local-Splitter: Seven Tactics for Reducing Cloud LLM Token Usage on Coding-Agent Workloads**." https://arxiv.org/pdf/2604.12301 — **[PARTIALLY VERIFIED]** confirms local-draft-then-cloud-refine and prompt caching as effective tactics and explicitly warns some tactics backfire (local compute overhead, quality loss); I could **not** extract the exact per-tactic percentages from the PDF — treat magnitudes as unquantified.
- **Explicit risk (well-documented):** "if the local draft is poor, the cloud spends more tokens correcting it than writing from scratch." This is the failure mode we already feel.

**Skeleton-then-fill / edit-oriented drafting (code-specific research):**
- **EfficientEdit** — edit-oriented speculative decoding for code editing (draft the edited region, verify). https://arxiv.org/pdf/2506.02780
- **Self-Edit** — fault-aware code editor: generate, run tests, edit using the error as signal. https://arxiv.org/pdf/2305.04087

**Speculative decoding — a precise analogy, with a warning.** Token-level: a small draft model proposes k tokens, the big target verifies them in one parallel pass and accepts/rejects **so the output is provably identical to the target's.** https://developer.nvidia.com/blog/an-introduction-to-speculative-decoding-for-reducing-latency-in-ai-inference/
> The analogy to our system is seductive but leaky: SD's acceptance test is a *cheap, exact* distribution check, guaranteeing target-quality output. Our "verifier" is a *gate*, which guarantees only "passes the gate," not "is what Claude would have written." So the analogy justifies *cheap-drafts-verified* as a shape, but the quality guarantee is only as strong as the gate.

**MoA aggregator** = a real cheap-proposers / one-refiner instance (§3).

**Do these measurably help?** Aider: yes (benchmarked). MoA: yes (benchmarked). Local-draft-cloud-refine: directionally yes but I lack clean numbers — **prototype-and-measure, don't assume.**

---

## 5. Where cheap-executor delegation SHINES vs FAILS

**Shines — mechanical, well-specified, verifiable work.** Cheap/open coders reach near-frontier on *structured, single-file, edit-format* tasks:
- **Qwen2.5-Coder-32B** on Aider's whole-edit benchmark **~73.7 / 74%**, ≈ GPT-4o's 71% (Claude 3.5 Sonnet 84%). https://qwenlm.github.io/blog/qwen2.5-coder-family/ , https://openrouter.ai/qwen/qwen-2.5-coder-32b-instruct
  - **[SKEPTICAL]** A "69.6% on SWE-bench Verified, matching Claude 3.5 Sonnet" figure circulates for Qwen2.5-Coder-32B; I do **not** trust it — it conflicts with agent-scaffold SWE-bench results and is likely benchmark/scaffold conflation. The Aider edit-benchmark numbers are the trustworthy ones.
- These are exactly the tasks our `/delegate` skill targets: renames, signature changes across files, codemods, boilerplate, tests for existing code, lint/type fixes — a command can prove them.

**Fails — open-ended, underspecified, specification-heavy, long-horizon reasoning.**
- "All LLMs perform poorly on specification-heavy tasks, especially open-source models." https://arxiv.org/pdf/2311.08993
- Underspecificity in SWE (agents must ask/clarify): **Ambig-SWE**. https://arxiv.org/pdf/2502.13069
- "Cheaper models often fail partway through complex tasks, wasting all tokens used up to that point." https://www.codeant.ai/blogs/cheap-llm-models

**Benchmark cost/quality evidence (SWE-bench Verified, cheap models + light scaffolds):**
- **MiniMax M2.5** on mini-SWE-agent: **75.8% at ~$0.07/issue** — mid-tier model, thin scaffold, near-frontier resolve rate at cents.
- **GPT-5-mini** on mini-SWE-agent: **~56–60% at $0.04–0.05/issue**.
- **Claude Haiku 4.5 + Moatless Tools: 35.9%**, but "**pass-rate-per-dollar beats most larger setups**."
- **Gemini 2.0 Flash: 24%** on the 50-task mini set — the floor; weakest budget model struggles on agentic coding.
- **Sonar Foundation Agent: $1.26/issue** at top-of-leaderboard resolve rate (for contrast — quality costs more).
- Sources: https://hal.cs.princeton.edu/swebench_verified_mini , https://www.sonarsource.com/company/press-releases/sonar-claims-top-spot-on-swe-bench-leaderboard/
**Overriding rule:** *quality-adjusted cost, not sticker price* — a cheap model that fails and forces retries/review can cost more than a mid-tier one-shot. https://subquery.ai/blog/2026-01-21-budget-model-showdown

**Anthropic's own orchestrator-worker economics (the number to internalize):** multi-agent (Opus 4 lead + Sonnet 4 subagents) beat single-agent Opus 4 by **90.2%** on their research eval — *but used ~15× the tokens of a plain chat.* "Token usage explains ~80% of performance variance." Multi-agent wins only when "the answer is worth a lot of tokens." https://www.anthropic.com/engineering/multi-agent-research-system
> This is the mirror of our finding: orchestration is a *token amplifier*. It pays only when the task is big and parallel, or when the amplified tokens are *free* (our executor) AND the orchestrator's own overhead is kept small.

---

## 6. The mid-tier-paid-executor question

**Models people actually use as mid-tier coding executors** (smarter than a small local model, far cheaper than a frontier orchestrator):
- **DeepSeek V3** — ~**$0.27 / $0.40 per 1M in/out**, ~82.6% HumanEval, strong on coding benchmarks. Cited as editor in Aider's top architect/editor pairs. https://openrouter.ai/deepseek/deepseek-v3.2 , https://aider.chat/2024/09/26/architect.html . (Vendor blogs claiming it "beats Claude 3.5 Sonnet on SWE-bench" are **[VENDOR CLAIM]** — discount.)
- **Claude Haiku 4.5** — **73.3% SWE-bench Verified [VENDOR-REPORTED]**, best-in-class instruction-following/agentic reliability among budget models; best pass-per-dollar in Moatless test. https://evolink.ai/blog/gemini-3-5-flash-vs-claude-haiku-4-5
- **GPT-4o-mini / GPT-5-mini** — cheapest input ($0.15/M for 4o-mini); GPT-5-mini resolves SWE-bench mini issues at ~$0.04–0.05.
- **Gemini Flash / Flash-Lite** — cheapest, but weakest on multi-step agentic coding (Flash 2.0 = 24% on mini-SWE).
- **MiniMax M2.5** — standout cost/quality on mini-SWE ($0.07/issue, 75.8%).

**Is there evidence a mid-tier paid executor + light verification beats BOTH (free-local + heavy verification) AND (expensive solo)?**
- **Indirect but consistent yes.** The SWE-bench cost/quality data shows **mid-tier models resolving real issues at cents each with thin scaffolds** — i.e., they sit on the cost/quality frontier where a heavy-verification harness would only add overhead. EcoAssistant demonstrates the general shape holds for code (beat GPT-4 by ~10 pts at <50% cost via cheap-first cascade). https://arxiv.org/abs/2310.03046
- **BUT the specific three-way comparison you want does not exist in a clean, cited form.** I found **no** benchmark that directly pits *(free-local + heavy gate)* vs *(mid-tier paid + light gate)* vs *(frontier solo)* on the same coding tasks with total-cost accounting. Local-Splitter is the nearest (it measures token-reduction tactics on coding-agent workloads) but I could not extract its per-tactic numbers. **This is the biggest evidence gap — and it's exactly the experiment your setup is instrumented to run.**
- **The economic logic that *does* hold up:** verification has a cost (extra orchestrator turns, re-runs, back-and-forth). A free executor is only free in *executor* tokens; every failed gate cycle spends *orchestrator* tokens (our actual bottleneck). A mid-tier paid executor that passes the gate on the first or second try can be cheaper *in the currency that binds us (Claude tokens)* even though its own tokens aren't free — because it collapses the expensive verification/rework loop. Quality-adjusted cost, again. https://subquery.ai/blog/2026-01-21-budget-model-showdown

---

## Synthesis for our architecture

1. **Our gate is the strongest verification primitive in the field (§2A).** Execution-based validation is the only class that fully removes "re-read and trust the diff"; LLM-as-judge is demonstrably unreliable and gameable ("One Token to Fool", https://arxiv.org/pdf/2507.08794). Keep the gate central; do **not** add an LLM-judge as a load-bearing check — at most a cheap pre-filter.

2. **The real disease is orchestration overhead, and everyone has it.** Anthropic's own multi-agent system costs ~15× tokens (https://www.anthropic.com/engineering/multi-agent-research-system); CrewAI's manager adds 30–50% (https://towardsdatascience.com/why-crewais-manager-worker-architecture-fails-and-how-to-fix-it/). Our +34–69% is *in-family*, not pathological. **Attack the preamble before anything else:** the ~6k agent-definition + ~4k tool schemas re-read every turn is the tax. Trim/prune the agent definition, expose the minimum tool surface, and lean on prompt caching — Local-Splitter confirms caching is a real win (https://arxiv.org/pdf/2604.12301). A cheaper executor does nothing about this; only shrinking the orchestrator's per-turn context does.

3. **Adopt Roo Code's "return only a summary" discipline (§1).** The orchestrator should never re-ingest the executor's transcript — only a short structured result + the gate outcome. This is the single highest-leverage context saver and it's already proven in Boomerang Tasks.

4. **Trust slider = cascade acceptance threshold, keyed on the GATE, not a learned router (§3).** FrugalGPT/EcoAssistant show cascades save 50–98% *when there's a cheap success signal*. We have a near-perfect one (the gate), so go **reactive** (delegate, gate, escalate on fail) rather than build a RouteLLM-style predictive router. The slider's real knobs: (a) how many executor retry/reflexion loops before escalating; (b) whether to require N self-consistent passes or just one gate-pass; (c) whether a *class* of task (mechanical vs open-ended) is delegated at all.

5. **Make the slider dial *verification depth*, not *whether to verify*.** As measured trust in the executor rises for a task class, reduce N (self-consistency samples) and reduce retry budget toward 1 — but keep the gate. "Turning down rigor" should mean fewer *samples/retries*, never "skip the gate," because the gate is what makes not-reading-output safe.

6. **Push self-consistency + Reflexion INTO the free executor (§2 D/E).** Sampling N solutions and self-reflecting against the gate raises first-pass gate-pass rate at **zero orchestrator-token cost** (executor tokens are free). This directly reduces the expensive escalation loops that dominate our bill. This is the cheapest quality lever we have and we're under-using it.

7. **A mid-tier paid executor is worth it precisely when it collapses the verification loop (§6).** The binding currency is Claude's tokens, not the executor's. If a paid mid-tier model (DeepSeek V3, Haiku 4.5, GPT-5-mini, MiniMax M2.5) passes the gate first-try where Qwen needs 3 rounds, the paid model is cheaper *in Claude tokens* despite non-free executor tokens. Worth it for **open-ended / specification-heavy / multi-file-reasoning** tasks where Qwen fails partway (§5). **Not** worth it for the mechanical work Qwen already nails at ~74% on Aider edit benchmarks (§5) — keep those on the free local model.

8. **Route by task type, and let the gate outcome auto-escalate the model tier.** Cheap-local for mechanical/well-specified (renames, codemods, boilerplate, tests-for-existing-code, lint fixes — Qwen's sweet spot). Escalate to a mid-tier *paid* executor automatically after k failed local gate cycles, before ever escalating to Claude-does-it-itself. This is a 3-tier cascade (free-local → mid-paid → frontier-solo) — the natural generalization of EcoAssistant's hierarchy (§3).

9. **Partial-delegation pattern worth prototyping first: cheap-drafts / expensive-patches (§4).** Have Qwen produce a full draft; Claude edits the diff rather than authoring from scratch — output tokens (the costly kind) drop. Proven directionally by Local-Splitter and by Aider's editor economics. **But instrument the backfire case** ("if the draft is poor, patching costs more than authoring") — gate the draft *before* Claude ever looks at it, so Claude only patches drafts that already pass or near-pass.

10. **Second pattern worth prototyping: architect/editor split within delegation (§1, §4).** For a genuinely hard task, let Claude write the *plan/spec* (cheap: short output), Qwen *implement* against it, the gate decide. This is Aider's proven shape and keeps Claude's turns short. It's arguably what we already do — the optimization is making Claude's plan-turn as short as the gate allows.

11. **Speculative decoding is an inspiring analogy but NOT a quality guarantee (§4).** Token-level SD gives *provably identical* output; our gate gives only *passes-the-gate*. So "cheap drafts, verify cheaply" is sound as a *shape*, but the burden falls entirely on gate quality — a weak/gameable gate turns the whole scheme into rubber-stamping. Invest in non-gameable gates (already our principle) precisely because the speculative analogy's guarantee doesn't come for free.

12. **Biggest open question — run the experiment nobody has published (§6).** No cited source cleanly compares *(free-local + heavy verify)* vs *(mid-tier paid + light verify)* vs *(frontier solo)* on the same coding tasks in total-cost terms. Our harness is built to measure exactly this. Recommendation: pick ~20 tasks spanning mechanical→open-ended, run all three arms, and measure **Claude-token cost + wall-clock + gate-pass rate**. That data would settle the mid-tier question for our workload better than any external benchmark, all of which measure the executor's own cost, not the orchestrator-token cost that actually binds us.

---

### Flagged / unverified claims (skeptic's ledger)
- pilotfish "Fable 5 + Sonnet 5 = 96% perf at 46% cost" and "58% savings" — **community/vendor claim**, future model names unverifiable at my Jan-2026 cutoff. Verifiable analogue: Anthropic's 90.2% uplift + 15× tokens.
- Qwen2.5-Coder-32B "69.6% SWE-bench Verified" — **distrusted**; use the Aider edit-benchmark ~74% instead.
- DeepSeek V3 "beats Claude 3.5 Sonnet on SWE-bench" — **vendor blog**, discount.
- Haiku 4.5 "73.3% SWE-bench Verified" and other 2026-dated model scores — **vendor-reported**, directionally useful, not independently confirmed here.
- Local-Splitter per-tactic percentages — **could not extract from PDF**; tactics confirmed, magnitudes not.
- GPT-Pilot credential-stealer report — **unverified rumor** from a search summary.
