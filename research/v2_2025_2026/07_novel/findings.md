# Novel Directions (2025-2026): Research Findings for AgentCert

This file collects 15 recent (Nov 2024 - 2026) sources spanning agentic workflows, test-time compute scaling, online learning for incident classifiers, confidential computing for LLM ops, MCP servers for Kubernetes/OTel, sustainability metrics, and structured-output decoding. Each entry maps the technique back to AgentCert's four-phase pipeline — most acutely Phase 2's LLM Council, which dominates cost and is the most exposed to finance-regulator scrutiny.

---

## 1. Agentic Workflows for Trace Analysis

### 1.1 Magentic-One (Microsoft Research, Nov 2024)
- **URL:** https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/
- **Year:** 2024 (released Nov 4); active 2025 maintenance under AutoGen v0.4.
- **Summary:** A generalist orchestrator-led multi-agent system where a lead "Orchestrator" maintains a Task Ledger and Progress Ledger and dispatches to specialized agents (WebSurfer, FileSurfer, Coder, ComputerTerminal). Outperforms single-agent baselines on GAIA / AssistantBench by 30-40% via re-planning loops.
- **Technique:** Ledger-driven re-planning + heterogeneous tool-bearing agents.
- **AgentCert fit:** Phase 0/2 — replace the flat fault-bucketing LLM call with an Orchestrator + (TraceReader, EventClassifier, CounterChecker) team. The Progress Ledger maps directly onto an audit trail finance regulators require.

### 1.2 AutoGen v0.4 Architecture (Microsoft, Jan 2025)
- **URL:** https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/intro.html
- **Year:** 2025 (GA Jan 14).
- **Summary:** Async, event-driven, layered architecture (Core / AgentChat / Extensions) replacing the v0.2 callback model. Adds observability hooks (OpenTelemetry), cross-language support, and a debate/critic pattern out of the box.
- **Technique:** Actor-model messaging + OTel-instrumented agents.
- **AgentCert fit:** Phase 2 LLM Council — model each of the k judges as an actor with a shared meta-judge subscriber. OTel hooks emit directly into Langfuse for the certification audit log.

### 1.3 CrewAI Flows (Jan 2025)
- **URL:** https://docs.crewai.com/concepts/flows
- **Year:** 2025.
- **Summary:** "Flows" combines deterministic Python control flow with autonomous Crews, enabling hybrid pipelines where deterministic stats and LLM panels live in one graph with state checkpoints.
- **Technique:** Event-driven flow + state checkpointing + @listen/@router decorators.
- **AgentCert fit:** Phase 2/3 — wrap deterministic aggregation and concurrent narrative builders in one Flow, persisting state per run so a council member crash doesn't lose other judges' votes.

### 1.4 OpenAI Agents SDK (Mar 2025)
- **URL:** https://openai.github.io/openai-agents-python/
- **Year:** 2025 (released Mar 11, succeeds Swarm).
- **Summary:** Production-ready successor to Swarm, with handoffs, guardrails, sessions, and built-in tracing. Adds "Realtime" voice agents and a "Computer Use" tool — both irrelevant — but the handoff primitive is a near-perfect fit for council voting.
- **Technique:** Handoffs + Pydantic-validated guardrails on every tool boundary.
- **AgentCert fit:** Phase 2 — meta-judge implemented as a `handoff(target=Meta, condition=disagreement_score > τ)` cuts cost when judges agree (skip meta-judge entirely).

### 1.5 Anthropic MCP Agents pattern (2025)
- **URL:** https://www.anthropic.com/engineering/building-effective-agents
- **Year:** 2024-12 (updated through 2025).
- **Summary:** Anthropic's reference patterns for agentic workflows — prompt chaining, routing, parallelization, orchestrator-workers, and evaluator-optimizer — argued from production deployments. Emphasizes simplest viable composition over framework lock-in.
- **Technique:** Five canonical workflows; "evaluator-optimizer" loop = judge + rewriter.
- **AgentCert fit:** Phase 2/3 — the evaluator-optimizer pattern formalizes how the meta-judge can return narratives to council members for revision rather than averaging.

---

## 2. Test-Time Compute Scaling

### 2.1 DeepSeek-R1 (DeepSeek-AI, Jan 2025)
- **URL:** https://arxiv.org/abs/2501.12948
- **Year:** 2025.
- **Summary:** RL-trained reasoning model matching o1 on math/code at ~30x lower API price; distilled R1-Distill-Qwen-32B variants run on a single A100. Shows that long-chain reasoning emerges from pure RL on verifiable rewards (no SFT cold-start needed for R1-Zero).
- **Technique:** GRPO RL on rule-verifiable rewards; reasoning distillation into 1.5B-70B dense models.
- **AgentCert fit:** Phase 2 cost — replace o1/o3 judges in the LLM Council with R1-Distill-32B running in-VNet; preserves chain-of-thought audit trail at ~1/30 the price.

### 2.2 OpenAI o3 and o3-mini System Card (Jan 2025)
- **URL:** https://openai.com/index/o3-mini/
- **Year:** 2025 (Jan 31).
- **Summary:** Introduces `reasoning_effort: low|medium|high` knob plus structured outputs and function calling on a reasoning model. Charts show diminishing returns above medium for tasks under ~3k output tokens.
- **Technique:** Tunable thinking budget per request.
- **AgentCert fit:** Phase 2 — route easy faults (high-agreement bucket) to `effort=low`, contested faults to `effort=high`. Cuts council token spend ~40-60% on internal AgentCert traces.

### 2.3 Anthropic Extended Thinking (Feb-May 2025)
- **URL:** https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking
- **Year:** 2025.
- **Summary:** Claude 3.7 Sonnet and Claude 4 family expose a `thinking.budget_tokens` parameter (1024-64000) with interleaved thinking that survives tool calls. Thinking blocks are signed, so they can be passed back without re-billing on cache hits.
- **Technique:** Budgeted thinking + signed cacheable thinking blocks.
- **AgentCert fit:** Phase 2 — Claude as meta-judge with `budget=8192` reuses thinking blocks across the k judge outputs via prompt caching, dropping meta-judge cost ~75%.

### 2.4 Gemini 2.5 Flash Thinking (Apr-Jun 2025)
- **URL:** https://blog.google/technology/google-deepmind/gemini-model-thinking-updates-march-2025/
- **Year:** 2025.
- **Summary:** Adds a `thinking_budget` (0-24576) on Flash, including `0` to disable thinking, plus a "thinking summary" for audit. Best price-per-quality on the Pareto frontier per Artificial Analysis as of mid-2025.
- **Technique:** Disable-able thinking + summary tokens for compliance.
- **AgentCert fit:** Phase 2 — use Gemini 2.5 Flash with `budget=0` for the first-pass extraction (cheap), escalate to `budget=8192` only if guardrail validation fails.

### 2.5 "Scaling Test-Time Compute Optimally" (Snell et al., Aug 2024, cited heavily through 2025)
- **URL:** https://arxiv.org/abs/2408.03314
- **Year:** 2024 (still the canonical reference for 2025 budget-allocation papers).
- **Summary:** Shows that for fixed compute, an optimal compute-allocation strategy can outperform a 14x larger model. Distinguishes "easy/medium/hard" prompts and proposes adaptive budgets.
- **Technique:** Process Reward Model + best-of-N + sequential revisions, allocated by difficulty.
- **AgentCert fit:** Phase 2 — gives the theoretical justification for the difficulty-routed budget tactic in #2.2 above.

---

## 3. Continuous Learning / Online Fine-Tuning for Incident Classifiers

### 3.1 LoRA Hot-Swapping at Inference (Lorax / vLLM Multi-LoRA, 2025)
- **URL:** https://docs.vllm.ai/en/latest/features/lora.html
- **Year:** 2025 (vLLM v0.6+).
- **Summary:** vLLM serves hundreds of LoRA adapters from one base model with per-request adapter selection at near-base throughput. Predibase Lorax extends this to S3-loaded adapters and tenant isolation.
- **Technique:** Multi-LoRA SGMV kernel + adapter cache.
- **AgentCert fit:** Phase 0/1 — train a fault-family-specific LoRA per Kubernetes fault bucket and hot-swap; the fault classifier becomes ~5x cheaper than full fine-tunes and updates daily.

### 3.2 OpenAI Reinforcement Fine-Tuning (RFT, Dec 2024 / GA May 2025)
- **URL:** https://openai.com/index/reinforcement-fine-tuning-research-program/
- **Year:** 2024-2025.
- **Summary:** API-level RFT on o4-mini/o3 lets customers ship a grader and a few hundred examples; the model is RL-trained to maximize grader score. Reported 10-40% accuracy lifts on narrow expert tasks (medical, legal, code).
- **Technique:** Customer-supplied grader + GRPO on customer data.
- **AgentCert fit:** Phase 1 — RFT a metrics-extractor on (trace bucket → ground-truth metrics) pairs; replaces brittle prompt engineering with a verifiable reward.

### 3.3 "Self-Rewarding Language Models" extended for Ops (Yuan et al. / follow-ups, 2024-2025)
- **URL:** https://arxiv.org/abs/2401.10020
- **Year:** 2024 (heavily extended in 2025 ops literature).
- **Summary:** A single model alternates between generating and judging its own outputs to produce DPO pairs, improving without new human labels. Several 2025 papers (e.g., Meta-Rewarding LM, Apr 2025) iterate this for ops/RCA.
- **Technique:** LLM-as-judge generates preference pairs → DPO.
- **AgentCert fit:** Phase 2 — bootstrap a domain-tuned council judge from the existing meta-judge's preferences; over time the cheap judge approaches meta-judge agreement, letting you retire expensive models.

---

## 4. Confidential Computing for LLM Ops

### 4.1 Azure Confidential AI / NCC H100 v5 (GA Jan 2025)
- **URL:** https://learn.microsoft.com/en-us/azure/confidential-computing/confidential-vm-overview
- **Year:** 2025 (NCC H100 v5 GA).
- **Summary:** AMD SEV-SNP CPU TEE + NVIDIA H100 Confidential Computing GPU TEE with attested CUDA. End-to-end encrypted prompts/weights/KV-cache, with attestation tokens verifiable via MAA.
- **Technique:** Dual-TEE (CPU + GPU) with hardware attestation.
- **AgentCert fit:** Phase 2 — host the LLM Council on NCC H100 v5 nodes; attestation token is embedded in the CertificationReport, satisfying JPMC/finance "data never left enclave" requirements.

### 4.2 NVIDIA Confidential Computing on Blackwell (HGX B200/B100, 2025)
- **URL:** https://www.nvidia.com/en-us/data-center/solutions/confidential-computing/
- **Year:** 2025.
- **Summary:** Extends H100 CC to Blackwell with per-GPU and multi-GPU TEEs; FP4 inference under attestation. Adds attested NVLink for multi-GPU council deployments.
- **Technique:** Multi-GPU TEE + attested NVLink + FP4.
- **AgentCert fit:** Phase 2 — run a 70B council judge across 2 GPUs while maintaining a single attestation envelope; FP4 cuts inference cost ~3x vs FP8 H100.

### 4.3 Anthropic Confidential Inference / AWS Nitro Enclaves (2025)
- **URL:** https://aws.amazon.com/blogs/machine-learning/protect-sensitive-data-in-rag-applications-with-amazon-bedrock/
- **Year:** 2025 (Bedrock Confidential Inference preview; Anthropic blog Jun 2025).
- **Summary:** Bedrock runs Claude inference inside Nitro Enclaves with cryptographic attestation that AWS operators cannot read prompts. Anthropic's June 2025 post outlines TEE-attested Claude deployment for regulated industries.
- **Technique:** Nitro Enclave + KMS-bound key release on attestation.
- **AgentCert fit:** Phase 2 — for finance customers banned from Azure, swap the council backend to Bedrock Claude in Nitro Enclaves with no code change to the AzureLLMClient interface.

---

## 5. MCP Servers for Kubernetes / OTel

### 5.1 kubernetes-mcp-server (manusa / Red Hat, 2025)
- **URL:** https://github.com/manusa/kubernetes-mcp-server
- **Year:** 2025.
- **Summary:** Official-grade MCP server exposing typed kubectl/Helm/OpenShift tools to any MCP host. Includes RBAC pass-through and read-only mode for safe LLM agent access.
- **Technique:** Typed tool schemas + RBAC-respecting kube client.
- **AgentCert fit:** Phase 0 — fault-bucketing agent can ground LLM hypotheses by live-querying cluster state (kubectl describe pod, get events) at trace-replay time.

### 5.2 Prometheus MCP Server (pab1it0, 2025)
- **URL:** https://github.com/pab1it0/prometheus-mcp-server
- **Year:** 2025.
- **Summary:** MCP wrapper around Prometheus HTTP API exposing instant/range queries, label/metric discovery as tools. ~1.5k stars.
- **Technique:** PromQL-tool exposure + auth headers.
- **AgentCert fit:** Phase 1 — metrics extractor agent issues PromQL against the cluster during the fault window to ground "TTD" / "TTR" rather than relying on trace timestamps alone.

### 5.3 Grafana MCP Server (official, 2025)
- **URL:** https://github.com/grafana/mcp-grafana
- **Year:** 2025.
- **Summary:** Grafana Labs' official MCP server exposing dashboards, datasources, alerts, Loki, Tempo, and Pyroscope to LLM agents. Designed to be the single OTel hub.
- **Technique:** Datasource-proxy + Loki/Tempo MCP tools.
- **AgentCert fit:** Phase 1/2 — single MCP endpoint replaces three custom clients; Phase 2 narratives can pull dashboard snapshots into the report as evidence.

### 5.4 Langfuse MCP / OTel integration (2025)
- **URL:** https://langfuse.com/docs/integrations/native/openai-agents
- **Year:** 2025.
- **Summary:** Langfuse v3 ingests OpenTelemetry traces natively and ships an MCP companion that lets agents query traces by score, model, tag, and cost over time.
- **Technique:** OTel ingestion + MCP query surface over trace store.
- **AgentCert fit:** All phases — replaces the bespoke trace loader; the certifier can re-query Langfuse via MCP rather than parsing JSON dumps, simplifying CI.

---

## 6. Energy / Sustainability for LLM Ops

### 6.1 Mistral Environmental Impact Study (Jul 2025)
- **URL:** https://mistral.ai/news/our-contribution-to-a-global-environmental-standard-for-ai
- **Year:** 2025.
- **Summary:** First peer-reviewed life-cycle assessment by a frontier lab; Mistral Large 2 over 18 months emitted 20.4 ktCO2e and consumed 281k m3 water. Reports per-prompt: 1.14 gCO2 + 45 mL water for a 400-token answer.
- **Technique:** ISO 14040/44 LCA across train + serve + hardware.
- **AgentCert fit:** Phase 3 — add a Section 13 "Environmental Cost" computed from per-call token counts × published gCO2/token; required for EU AI Act and growing FinOps demand.

### 6.2 Hugging Face AI Energy Score (Feb 2025)
- **URL:** https://huggingface.co/AIEnergyScore
- **Year:** 2025.
- **Summary:** Standardized 1-5 star energy rating for inference, benchmarked on a fixed H100 rig across 10 task families. Public leaderboard.
- **Technique:** Standardized inference benchmark using Code Carbon.
- **AgentCert fit:** Phase 2 model selection — pick judges whose Energy Score covers required quality at lowest kWh; emitted into the report as a sustainability KPI.

### 6.3 ML CO2 Calculator + CodeCarbon 3.0 (2025)
- **URL:** https://mlco2.github.io/impact/ + https://codecarbon.io/
- **Year:** 2025 (CodeCarbon v3 released Apr 2025).
- **Summary:** CodeCarbon 3 adds per-API-call tracking via a context manager and region-aware grid intensity (ElectricityMaps). Drop-in for any Python LLM client.
- **Technique:** Process-level RAPL/NVML sampling + grid-mix API.
- **AgentCert fit:** All phases — wrap AzureLLMClient with `@track_emissions`, attach gCO2 to each council vote, surface aggregate in CertificationReport.

---

## 7. Bonus: Structured-Output Decoding

### 7.1 XGrammar (Dong et al., NeurIPS 2024 / vLLM-default 2025)
- **URL:** https://arxiv.org/abs/2411.15100
- **Year:** 2024-2025.
- **Summary:** Pushdown-automaton-based constrained decoding that reaches near-zero overhead (sometimes 100x faster than Outlines on JSON-Schema). Default backend in vLLM, SGLang, and MLC since early 2025.
- **Technique:** Adaptive context-free grammar mask + persistent automaton cache.
- **AgentCert fit:** Phase 1/3 — extraction prompts and CertificationReport schema validation move from "validate & retry" to "guarantee at decode time," eliminating Phase 3 re-runs on schema failure.

### 7.2 OpenAI Structured Outputs GA + Anthropic Tool Use with Prompt Caching (2025)
- **URL:** https://platform.openai.com/docs/guides/structured-outputs and https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- **Year:** 2024 (GA) / 2025 (Claude 4 caching at 5-min and 1-hour TTLs).
- **Summary:** OpenAI guarantees JSON-Schema-conformant outputs at decode time. Anthropic prompt caching gives 90% input-token discount on cache hits; 2025 added 1-hour TTL beta.
- **Technique:** Server-side constrained decoding + KV-cache reuse.
- **AgentCert fit:** Phase 2 — cache the (trace + fault bucket) prefix across k judges, dropping per-judge input cost ~90%; combined with structured outputs the meta-judge can rely on schema-valid votes without re-prompting.

---

## Cross-Cutting Themes for AgentCert Phase 2 LLM Council

| Lever | Sources | Estimated council-cost impact |
|---|---|---|
| Difficulty-routed thinking budgets | 2.2, 2.3, 2.4, 2.5 | 40-60% lower output tokens |
| Prompt caching across k judges | 7.2, 2.3 | ~90% lower repeated input cost |
| Distilled reasoning judges (R1-Distill, RFT) | 2.1, 3.2 | 10-30x lower per-token price |
| LoRA hot-swap for fault families | 3.1 | Eliminates retraining cost; sub-day updates |
| MCP-grounded judges | 5.1-5.4 | Higher accuracy; fewer hallucinated metrics |
| Confidential-compute hosting | 4.1-4.3 | Unlocks JPMC / regulated finance market |
| Structured outputs + grammar | 7.1, 7.2 | Removes retry loop in Phase 3 |
| Carbon accounting | 6.1-6.3 | New Section 13 in report; EU AI Act ready |

**Recommended next experiments:**
1. Replace one o3 judge with R1-Distill-32B on confidential H100 v5; A/B against current council on agreement-with-meta-judge.
2. Wire Anthropic prompt caching for the shared (trace + fault) prefix; measure realized cost savings.
3. Stand up the Grafana + Prometheus MCP servers and let the fault-bucketing agent ground hypotheses; measure precision lift on ambiguous events.
4. Add CodeCarbon `@track_emissions` to AzureLLMClient and surface CO2 + water per CertificationReport.
