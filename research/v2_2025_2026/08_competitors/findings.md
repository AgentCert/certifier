# AgentCert Competitor & Equivalent Landscape (2025-2026)

**Scope:** Commercial and OSS systems that overlap with AgentCert — a pipeline that runs AI agents on Kubernetes clusters under chaos-engineering fault injection (LitmusChaos, Chaos Mesh) and emits 12-section certification reports scoring reliability, recovery, cost, and safety.

**Headline finding (flagged for user):** No public commercial or open-source product currently offers "agent certification under chaos injection on Kubernetes" as a packaged, end-to-end deliverable. The closest conceptual neighbors split the problem in half — either (a) they certify/evaluate agents in static, non-chaotic environments (Patronus, Galileo, Braintrust, Inspect AI, TauBench), or (b) they inject chaos into clusters but do not understand or score AI-agent behavior (Steadybit, Gremlin, Chaos Mesh, Reliably). AgentCert sits squarely in the unoccupied intersection.

---

## 1. CI/CD & Continuous Verification Vendors

### Harness AI (2025)
- **Type:** Commercial. **URL:** harness.io/products/ai-code-assistant, harness.io/products/ai-test-automation
- **Latest:** Harness AI Code Assistant GA (Q2 2025); Harness AI Test Automation (announced HarnessCon 2025); Harness Continuous Verification with AI (2024, expanded 2025).
- **What they do:** Harness's "AI-Native Software Delivery Platform" wraps their existing pipeline, feature-flag, chaos (ChaosNative — see §5), and continuous-verification products in agentic copilots. AI Test Automation generates and prunes tests; Continuous Verification ingests Prometheus/Datadog/New Relic metrics and uses ML to decide whether a deploy regressed.
- **Overlap:** Continuous Verification has the same conceptual "did this deploy regress?" question AgentCert asks about an agent under fault. Harness owns ChaosNative — the closest commercial vendor to AgentCert's substrate.
- **AgentCert is unique because:** Harness verifies *deployed application* health, not *AI-agent behavior under fault*. No agent-specific metrics (TTD, TTR, hallucination rate, tool-call success).
- **AgentCert can borrow:** Harness's analysis-window / canary-comparison statistical primitives, and its "verification gate" pattern that blocks promotion on regression.

---

## 2. AI-Agent Evaluation Platforms (Commercial, 2025)

### Patronus AI
- **Type:** Commercial + OSS judge models. **URL:** patronus.ai
- **Latest:** Percival (agent eval, May 2025); Lynx (hallucination judge, OSS); Glider (small SLM judge, 3.8B, OSS).
- **What they do:** End-to-end LLM/agent evaluation platform. Percival specifically targets agentic systems — it ingests OpenTelemetry/LangGraph traces and outputs labeled failure modes (tool-misuse, plan-divergence, infinite-loop).
- **Overlap:** Percival's failure taxonomy is the closest analog to AgentCert's fault buckets. Lynx/Glider are direct alternatives for AgentCert's Phase-2 LLM Council judges.
- **AgentCert is unique because:** Patronus has zero substrate (no fault injection, no K8s) — it evaluates whatever traces you feed it.
- **AgentCert can borrow:** Percival's failure taxonomy schema; Glider as a cheaper local judge to replace one Azure-OpenAI council member.

### Galileo AI
- **Type:** Commercial. **URL:** galileo.ai
- **Latest:** Galileo Luna-2 small-language-model evaluators (2025); Galileo Protect runtime guardrail (2025); Galileo Agent Reliability Platform (announced mid-2025).
- **What they do:** Real-time and offline LLM observability. Luna SLMs replace GPT-4 judges. Protect is an inline gateway. The Agent Reliability Platform tracks agent-task completion across sessions.
- **Overlap:** "Agent reliability" naming overlaps strongly. Their span-level cost/latency dashboards mirror AgentCert's quantitative metrics.
- **AgentCert is unique because:** Galileo is a SaaS observability layer — no fault injection, no certification artifact, no K8s-native deployment.
- **Borrow:** Luna-2 as a cheap inline judge; their action-completion metric for AgentCert's qualitative scoring.

### Braintrust
- **Type:** Commercial. **URL:** braintrust.dev
- **Latest:** Braintrust Loop (agent eval, 2025); Brainstore (eval data warehouse, 2025).
- **What they do:** Eval-first developer platform. Strong on prompt iteration, dataset versioning, online scoring.
- **Overlap:** Dataset/eval-version management; would be a natural store for AgentCert's per-fault metrics.
- **Borrow:** Their experiment-comparison UI patterns for AgentCert's "run-to-run delta" reports.

### LangSmith Evaluations (LangChain)
- **Type:** Commercial. **URL:** smith.langchain.com
- **Latest:** Agent evaluation suite GA (2024); LangSmith for LangGraph agents (2025).
- **What they do:** Trace + eval product tightly bound to LangChain/LangGraph. Offers trajectory evaluators (did the agent take the right path?) and final-response evaluators.
- **Overlap:** Trajectory evaluation directly maps to AgentCert's recovery-trajectory analysis.
- **Borrow:** Their trajectory-similarity scoring; their hosted dataset format.

### HoneyHive, Confident AI / DeepEval Cloud, Future AGI, Athina AI, Maxim AI, Vellum, Comet Opik, Lunary, Langtrace, Helicone Evals
- All commercial (most have free tiers); launched or matured 2023-2025.
- URLs: honeyhive.ai, confident-ai.com, futureagi.com, athina.ai, getmaxim.ai, vellum.ai, comet.com/opik, lunary.ai, langtrace.ai, helicone.ai
- **Collective summary:** Each is a variation on LLM/agent observability + offline eval + (sometimes) prompt management. DeepEval (the OSS library behind Confident AI) is notable for its 40+ pre-built metric implementations. Opik (Comet, Apache-2.0) is fully OSS. Helicone is a proxy-based observability layer with bolt-on evals.
- **Overlap with AgentCert:** All share the "score an agent run" primitive. None do fault injection or K8s-native deployment.
- **Borrow:** DeepEval's metric library (G-Eval, faithfulness, answer-relevancy) as drop-in implementations; Opik as a self-hosted OSS trace store alternative to Langfuse.

---

## 3. OSS Evaluation & Safety Frameworks

### Inspect AI (UK AI Safety Institute → UK AI Security Institute)
- **Type:** OSS, MIT. **URL:** github.com/UKGovernmentBEIS/inspect_ai
- **Latest:** v0.3.x throughout 2025; agent eval toolkit and sandbox-based execution (2025).
- **What they do:** Government-grade eval framework with first-class support for agent solvers, tool use, and sandboxed (Docker, K8s) execution. Used by UK AISI for frontier-model pre-deployment safety evals.
- **Overlap:** **This is the closest OSS analog to AgentCert's intent** — Inspect AI has K8s sandbox executors and runs agents end-to-end. UK AISI uses it for cyber-uplift and autonomy evaluations.
- **AgentCert is unique because:** Inspect AI evaluates capability/safety in *static* sandboxes — it does not inject chaos into the sandbox during the run.
- **Borrow:** Inspect's solver/scorer abstraction; its K8s sandbox provider; reuse its log format so AgentCert reports can be ingested by AISI-compatible tooling.

### Promptfoo
- **Type:** OSS, MIT. **URL:** promptfoo.dev
- **Latest:** v0.100+ (2025); agent red-teaming module GA 2025.
- **What they do:** CLI/CI eval and red-team tool. The 2025 red-team module probes agents with adversarial prompts and policy-violation tests.
- **Overlap:** Red-team adversarial inputs are a form of "fault injection at the prompt layer" — analogous to AgentCert's chaos injection at the infra layer.
- **Borrow:** Their adversarial-prompt library as an additional fault axis ("prompt chaos") alongside infra chaos.

### Giskard
- **Type:** OSS Apache-2.0 + commercial Hub. **URL:** giskard.ai
- **Latest:** Giskard 2.x (2025) with LLM scan and RAG eval.
- **What they do:** Test framework that auto-generates test suites for LLM apps (robustness, bias, prompt injection, hallucination).
- **Borrow:** Their auto-generated test-suite pattern; could seed AgentCert's qualitative checks.

### Ragas
- **Type:** OSS, Apache-2.0. **URL:** github.com/explodinggradients/ragas
- **What they do:** RAG-specific metrics (faithfulness, context-precision, answer-relevancy).
- **Overlap:** Directly usable for any AgentCert qualitative metric where the agent does retrieval.

### TruLens
- **Type:** OSS, MIT (now Snowflake). **URL:** trulens.org
- **What they do:** Feedback-function framework for LLM apps; "RAG triad" metrics.
- **Borrow:** Feedback-function abstraction.

### Arize Phoenix
- **Type:** OSS Elastic-2.0. **URL:** phoenix.arize.com
- **What they do:** OSS LLM observability + eval, OpenTelemetry-native.
- **Overlap:** Alternative trace store to Langfuse.

### MLflow LLM Evaluate
- **Type:** OSS, Apache-2.0. **URL:** mlflow.org
- **What they do:** `mlflow.evaluate()` extended to LLM/GenAI metrics. Tracks runs/experiments.
- **Overlap:** Run-tracking model is analogous to AgentCert's per-fault run set.

### NVIDIA NeMo Evaluator
- **Type:** OSS + NVIDIA Enterprise commercial. **URL:** developer.nvidia.com/nemo-evaluator
- **Latest:** NeMo Microservices (2025) — includes Evaluator microservice for K8s.
- **Overlap:** K8s-native LLM eval microservice — same substrate as AgentCert.
- **Borrow:** Their Helm-deployable eval-job pattern.

### garak (NVIDIA)
- **Type:** OSS, Apache-2.0. **URL:** github.com/NVIDIA/garak
- **What they do:** LLM vulnerability scanner — probes for prompt injection, jailbreak, data leak, toxicity.
- **Overlap:** Security analog to AgentCert's chaos faults.
- **Borrow:** Their probe/detector/generator architecture maps cleanly onto AgentCert's fault/metric/judge architecture.

### PyRIT (Microsoft)
- **Type:** OSS, MIT. **URL:** github.com/Azure/PyRIT
- **What they do:** Python Risk Identification Tool for GenAI — automates red-team attacks against LLMs/agents.
- **Overlap:** Same intent as garak; Microsoft-maintained.
- **Borrow:** Attack-strategy library.

### HELM / HELM Lite, BIG-bench-Hard
- **Type:** OSS academic benchmarks (Stanford CRFM; Google).
- **What they do:** Static capability benchmarks.
- **Overlap:** Limited — benchmarks rather than runtime certification.

---

## 4. Agent Benchmark Suites (Conceptual Competitors)

| Benchmark | Maintainer | Focus | Relevance to AgentCert |
|---|---|---|---|
| **AgentBench** | Tsinghua / 2023, updated 2024-25 | Multi-environment agent eval (OS, DB, web) | Methodological precedent for multi-task agent scoring |
| **GAIA** | Meta/HF, 2023 | Real-world assistant tasks | Standard for "general assistant" scoring |
| **TauBench (τ-bench)** | Sierra, 2024; cited in Anthropic Claude 4 system cards 2025 | Tool-use in customer-service dialogues | Reference for tool-call success metric |
| **AppWorld** | Stony Brook / AI2, 2024 | App-control agents | Reference for action-correctness scoring |
| **SWE-bench Verified** | OpenAI + Princeton, 2024 | Real GitHub issue resolution | Reference for "graded patch" scoring |
| **MLE-bench** | OpenAI, 2024 | Kaggle-style ML tasks | Reference for end-to-end agent autonomy scoring |
| **ToolBench** | OpenBMB, 2023 | Tool-API agents | Tool-call test set |
| **OSWorld** | HKU, 2024 | Real OS GUI tasks in VMs | **Closest substrate analog** — runs agents inside real VMs |
| **WebArena / VisualWebArena** | CMU, 2023-24 | Web-browsing agents in self-hosted sites | Self-hosted environment pattern |
| **IFEval** | Google, 2023 | Instruction-following | Reference for compliance scoring |

**AgentCert's positioning vs. benchmarks:** Benchmarks test *capability on fixed tasks*. AgentCert tests *resilience to environmental perturbation*. None of the above injects chaos into the agent's substrate during the run — this is AgentCert's white space.

**Borrow:** OSWorld's VM-snapshot determinism; SWE-bench Verified's task-grading rubric; TauBench's pass^k metric for repeated-run stability (directly relevant to AgentCert's 30-runs-per-fault design).

---

## 5. Chaos Engineering Platforms (With AI Add-ons, 2025)

### Steadybit
- **Type:** Commercial + OSS extensions. **URL:** steadybit.com
- **Latest:** Steadybit AI Advisor (2025) — LLM that suggests next chaos experiments from observability data.
- **Overlap:** Same chaos substrate AgentCert sits on. The AI Advisor is *AI-for-chaos*, not *chaos-for-AI*.

### Gremlin
- **Type:** Commercial. **URL:** gremlin.com
- **Latest:** Gremlin Detected Risks + AI Reliability Recommendations (2024-25).
- **Overlap:** Reliability-scoring vocabulary mirrors AgentCert; targets infra, not agents.

### Harness ChaosNative (Litmus)
- **Type:** Commercial (acquired ChaosNative 2022; Litmus is CNCF). **URL:** harness.io/products/chaos-engineering
- **Overlap:** AgentCert *literally uses* Litmus. Harness is therefore the most plausible commercial acquirer/integrator if they extend their CV product to agents.

### Reliably (Adrian Hornsby)
- **Type:** Commercial + OSS Chaos Toolkit. **URL:** reliably.com
- **Overlap:** SLO-driven chaos. Their "objective-based" experiments map well to AgentCert's per-fault success criteria.

### LitmusChaos
- **Type:** CNCF Incubating, OSS Apache-2.0. **URL:** litmuschaos.io
- **Overlap:** Substrate.

### Chaos Mesh
- **Type:** CNCF Incubating, OSS Apache-2.0. **URL:** chaos-mesh.org
- **Latest:** 2.7.x (2025); experimental AI experiment-generation in community discussions, no GA AI feature.
- **Overlap:** Alternative substrate. AgentCert already supports both.

**Critical finding:** Of all chaos vendors surveyed, **none** ships a product that runs AI agents under chaos and produces a certification report. Steadybit and Gremlin use AI to *drive* chaos; nobody uses chaos to *test* AI.

---

## 6. AI Red-Team / Safety Platforms (2025)

| Vendor | Type | URL | One-liner |
|---|---|---|---|
| **Patronus Lynx + Glider** | OSS judges + commercial platform | patronus.ai | Hallucination + judge SLMs (see §2) |
| **Lakera Guard** | Commercial | lakera.ai | Inline prompt-injection / data-loss firewall; Gandalf threat intel |
| **Robust Intelligence** | Commercial (Cisco, acquired Aug 2024; integrated into Cisco AI Defense 2025) | cisco.com/ai-defense | Algorithmic red-teaming + runtime protection, now part of Cisco's secure-AI stack |
| **HiddenLayer** | Commercial | hiddenlayer.com | Model-scanner (malicious weights), AI Detection & Response |
| **Protect AI** | Commercial (acquired by Palo Alto Networks, 2025) | protectai.com | ModelScan, Guardian, Recon — now folded into PANW Prisma AIRS |
| **CalypsoAI** | Commercial | calypsoai.com | Inference Red-Team + Inference Defend |
| **Mindgard** | Commercial (UK) | mindgard.ai | Continuous automated red-teaming for LLMs/agents |

**Overlap:** All seven do *adversarial/security* certification of models. None does *operational-resilience* certification under infra chaos. Mindgard and CalypsoAI come closest in framing ("continuous certification of AI"), but their faults are prompt-layer, not cluster-layer.

**Borrow:** Their attack catalogs as additional AgentCert fault axes; their compliance-mapping (NIST AI RMF, EU AI Act, ISO/IEC 42001) as report sections.

---

## 7. Kubernetes "AI for Ops" Tools

| Tool | Type | URL | Relevance |
|---|---|---|---|
| **K8sGPT** | CNCF Sandbox, OSS | k8sgpt.ai | LLM-powered K8s diagnostics — the *thing AgentCert tests* (could itself be a certifiable agent) |
| **HolmesGPT (Robusta)** | OSS, MIT | github.com/robusta-dev/holmesgpt | SRE agent that investigates K8s alerts; same |
| **Komodor Klaudia** | Commercial | komodor.com | K8s-native AI troubleshooter; same |
| **Datadog Bits AI** | Commercial | datadoghq.com | Observability copilot |
| **Causely** | Commercial | causely.ai | Causal-AI for K8s SRE |
| **Dynatrace Davis CoPilot** | Commercial | dynatrace.com | Davis AI + GenAI for incident response |
| **ServiceNow Now Assist** | Commercial | servicenow.com | GenAI across ITSM/AIOps |

**Critical relationship:** This category is **AgentCert's primary customer base** — these are the agents that *need* to be certified. AgentCert does not compete with them; it certifies them.

---

## 8. Government / Institutional Eval Toolkits (2025)

- **Microsoft AICS / Counterfit / PyRIT** — Microsoft's AI Red Team stack. PyRIT (see §3) is GA and Apache-2.0. AICS is the internal Azure AI Content Safety eval harness; partially surfaced via Azure AI Foundry's "Evaluation" tab (GA 2025) which includes risk/safety, performance/quality, and agent-specific evaluators.
- **UK AISI Inspect AI** — see §3. The de-facto government standard.
- **US NIST AI RMF + ARIA program** — NIST's Assessing Risks and Impacts of AI program (2024-25) is building public eval methodology; not a product but a framing AgentCert should align to.
- **EU AI Act conformity assessment bodies** — emerging market for certified third-party evaluators.

**Borrow:** Adopt Inspect AI's log format and NIST AI RMF's risk taxonomy as report-section headers — this aligns AgentCert reports with what regulators will expect.

---

## Direct "Agent Certification Under Chaos" — Does it Exist Anywhere?

**No.** After surveying ~60 products, the explicit combination of (a) running an AI agent on a real K8s cluster, (b) injecting infra-level chaos (Litmus/Chaos Mesh) during the run, and (c) emitting a multi-section quantitative + qualitative certification artifact is **not offered by any commercial or OSS product reviewed**.

The nearest neighbors are:
1. **Inspect AI** (UK AISI) — has K8s sandboxes and agent solvers but no chaos injection.
2. **Harness ChaosNative + Continuous Verification** — has chaos and verification but no agent-awareness.
3. **OSWorld / WebArena** — run agents in real environments but use static, not chaotic, substrates.
4. **Patronus Percival + Galileo Agent Reliability** — score agent traces but are blind to the substrate that produced them.

**Strategic implication for AgentCert:** The white space is real and defensible. Highest competitive risk is Harness (already owns both ChaosNative and AI Test Automation; the two-product fusion would be a direct competitor) and Inspect AI (UK AISI could add a chaos extension in one quarter of work). Recommended moats: (i) ship the 12-section report schema as an open standard so it becomes the lingua franca; (ii) integrate Inspect AI's log format for upward compatibility with regulator tooling; (iii) publish reference fault libraries keyed to NIST AI RMF and EU AI Act articles to become the "compliance-grade" option.
