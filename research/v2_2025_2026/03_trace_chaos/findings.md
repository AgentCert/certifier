# Distributed-Trace Analysis with LLMs, OTel GenAI Conventions, and Chaos+AI — 2025/2026 Sources

Scope: 2025-2026 SOTA grounding for AgentCert's Phase 0 (fault bucketing of Langfuse traces) and Phase 1 (per-fault metric extraction). The repo today uses GPT-4 to classify interleaved trace events into per-fault buckets and then extract TTD/TTR/tokens/qualitative observations. Sources below cover what changed since 2024 in (a) trace-aware LLM analysis, (b) OpenTelemetry GenAI semantic conventions, and (c) chaos engineering driven or assisted by LLMs.

---

## A. OpenTelemetry GenAI semantic conventions (2025-2026)

### 1. OpenTelemetry semantic-conventions-genai — dedicated repo split
- **Year:** 2025 (active 2026)
- **URL:** https://github.com/open-telemetry/semantic-conventions-genai
- **Summary:** The GenAI conventions were extracted out of the main `semantic-conventions` repo into a dedicated repo, with schema URL `https://opentelemetry.io/schemas/gen-ai/1.42.0`. It defines three attribute namespaces (`gen_ai.*`, `mcp.*`, `openai.*`) and now ships separate documents for spans, metrics, events, exceptions, agent spans, and MCP — a structure that didn't exist in 2024.
- **Technique:** Attribute-based span/event/metric model with provider sub-conventions (Anthropic, OpenAI, AWS Bedrock, Azure AI Inference).
- **Fit to AgentCert:** Phase 0 should normalize Langfuse spans into these `gen_ai.*` keys so bucketing prompts and Phase 1 extractors are vendor-agnostic. Adopt the schema URL constant in `utils/` so downstream metrics extractors key off `gen_ai.operation.name` rather than Langfuse-specific JSON shapes.

### 2. GenAI agent spans + AI Sandboxes specs
- **Year:** 2025
- **URL:** https://github.com/open-telemetry/semantic-conventions-genai/tree/main/docs
- **Summary:** New `gen-ai-agent-spans.md` standardizes spans for *agentic* operations (task, memory, action, tool execution) and a sibling "AI Sandboxes" spec covers ephemeral code-execution environments. Both are net-new in 2025 — the 2024 spec only covered single LLM client calls.
- **Technique:** Hierarchical agent spans with tool-call children and dedicated memory-access events.
- **Fit to AgentCert:** Phase 0 bucketing currently relies on text classification of free-form events. With agent-span conventions, bucketing can switch to a hybrid: deterministic grouping by `gen_ai.agent.id` + `gen_ai.tool.name`, then LLM only for ambiguous events. Reduces GPT-4 calls in `fault_analyzer/` and improves reproducibility.

### 3. "OpenTelemetry: Unpacking 2025, Charting 2026" — KubeCon NA 2025 keynote
- **Year:** 2025 (Nov 11, Atlanta)
- **URL:** https://kccncna2025.sched.com/event/27Fad (Sharma/McLean/Suereth/Parker)
- **Summary:** Project-leadership talk covering the GenAI conventions promotion path, Profiling signal GA, and roadmap for agent telemetry. Confirms `gen_ai.*` events have moved out of "Experimental" for the core client-inference subset while agent spans remain Development.
- **Technique:** Spec governance + multi-signal correlation (traces ↔ profiles ↔ events).
- **Fit to AgentCert:** Use the stable client-inference subset (`gen_ai.request.*`, `gen_ai.response.*`, `gen_ai.usage.*`) immediately for Phase 1 token extraction; gate agent-span ingestion behind a feature flag until those stabilize.

### 4. "How Jaeger is evolving to trace AI agents with OpenTelemetry" — CNCF blog
- **Year:** 2026 (recent; reports 2025 work)
- **URL:** https://www.cncf.io/blog/2026/05/26/how-jaeger-is-evolving-to-trace-ai-agents-with-opentelemetry/
- **Summary:** Describes Jaeger v2's OTel-native rebuild and the project's adoption of MCP + Agent Client Protocol (ACP) + AG-UI to expose traces to LLM consumers. Surfaces embedding latency, tool calls, and token usage as first-class metrics.
- **Technique:** OTLP-native ingest + MCP server exposing trace queries to LLMs.
- **Fit to AgentCert:** A Jaeger-MCP-style read layer over Langfuse would let `cert_builder/` narrative builders pull span context on demand instead of stuffing it all into the Phase 2 scorecard prompt — a 2025-only pattern.

### 5. "Deep Dive To AI Agent Observability" — KubeCon EU 2025
- **Year:** 2025 (Apr 2, London)
- **URL:** https://kccnceu2025.sched.com/event/1twfX (Guangya Liu, IBM; Karthik Kalyanaraman, Langtrace)
- **Summary:** Maps `gen_ai.*` attributes to agent workflows (planner, tool, memory) and demonstrates Langtrace's OTel exporter for multi-agent traces. Provides the canonical mapping from "agent step" → OTel span that AgentCert currently re-invents in `fault_analyzer/`.
- **Technique:** OTel SDK auto-instrumentation for LangChain/LlamaIndex/CrewAI.
- **Fit to AgentCert:** Adopt Langtrace's exporter naming so Phase 0 can ingest both Langfuse-native and Langtrace-OTel traces with the same bucketing logic — broadens AgentCert's input surface beyond Langfuse.

---

## B. Trace summarization, RCA, and causal analysis with LLMs (2025)

### 6. ErrorPrism — error-propagation path reconstruction (Sept 2025)
- **URL:** https://arxiv.org/abs/2509.xxxxx (search "ErrorPrism microservice")
- **Summary:** Combines static analysis of service code repos with an LLM agent doing iterative backward search over spans to rebuild error-propagation paths; reports 97% accuracy on real-world microservice failures. Treats traces as a graph the LLM walks rather than a flat prompt.
- **Technique:** Static call-graph + ReAct-style LLM traversal over span DAG.
- **Fit to AgentCert:** Phase 1 currently extracts qualitative observations per fault bucket independently. ErrorPrism's backward-walk pattern can be added to `metrics_extractor/` to produce a "first-cause span" field — useful for the Phase 3 narrative builders.

### 7. MicroRCA-Agent — multimodal log+trace compression (Sept 2025)
- **URL:** arXiv 2509 listing, "MicroRCA-Agent"
- **Summary:** Compresses logs into "fault features" via a parsing algorithm + multi-level filtering, then feeds compressed features into a multimodal LLM for RCA. Specifically attacks the long-context problem that AgentCert hits when a fault bucket has thousands of span/log lines.
- **Technique:** Drain-style log compression + multimodal prompt.
- **Fit to AgentCert:** Phase 0 already buckets events; adding MicroRCA's log-feature compression *inside each bucket* before sending to the extraction model would cut Phase 1 token cost — directly relevant given `configs/configs.json` separates `extraction_model` from `reasoning_model`.

### 8. RCLAgent — multi-agent recursion-of-thought RCA (Aug 2025)
- **URL:** arXiv 2508 listing, "RCLAgent"
- **Summary:** Multi-agent system mimicking SRE workflows, jointly reasoning over traces and metrics with a recursion-of-thought pattern. Provides a published template for "judge agents + meta-judge" patterns identical to AgentCert's LLM Council in Phase 2.
- **Technique:** Role-specialized agents (trace-reader, metric-reader, synthesizer) with bounded recursion.
- **Fit to AgentCert:** Validates the LLM Council design in `aggregator/`; the recursion-of-thought termination criterion can be borrowed to bound meta-judge iterations.

### 9. GALA — causal inference + LLM iterative reasoning (Aug 2025)
- **URL:** arXiv 2508 listing, "GALA causal LLM RCA"
- **Summary:** Combines statistical causal inference (PC-style algorithms on metric DAGs) with LLM iterative reasoning, reporting 42.22% improvement over prior LLM-only RCA. The first paper to make causal "what-if" reasoning over span-derived variables a first-class step.
- **Technique:** Causal graph discovery → LLM counterfactual interrogation.
- **Fit to AgentCert:** AgentCert ingests N runs per fault — a built-in setup for causal estimation. Phase 2's `aggregator/` could compute a do-calculus-style "would TTR drop if hallucinations were 0" estimate per fault, then have the LLM Council narrate it. Net-new capability vs 2024.

### 10. Flow-of-Action — SOP-anchored multi-agent RCA (WWW '25)
- **URL:** https://arxiv.org/abs/2502.08224
- **Summary:** Anchors LLM RCA decisions to retrieved Standard Operating Procedures, raising accuracy from ReAct's 35.5% to 64.0%. Directly addresses hallucinated diagnostic actions.
- **Technique:** SOP retrieval + tool-grounded action graph.
- **Fit to AgentCert:** Phase 1 qualitative extraction could be SOP-grounded: maintain a YAML of "expected agent behaviors under fault type X" in `metrics_extractor/config/` and have the extractor cite the SOP rule it violated. Drops hallucination rate on the qualitative metric.

### 11. KnowledgeMind — MCTS over knowledge base for AIOps (July 2025)
- **URL:** arXiv 2507 listing, "KnowledgeMind AIOps"
- **Summary:** Uses Monte Carlo Tree Search with knowledge-base-derived rewards, cutting LLM context window needs by 90% while improving RCA accuracy 49-128%. Important for AgentCert's cost target.
- **Technique:** MCTS planner + retrieval-augmented evaluator.
- **Fit to AgentCert:** A KnowledgeMind-style policy could replace the Phase 2 "judge call → meta-judge call" fan-out with a tree search that early-stops once a confident conclusion is reached, reducing GPT-4 calls per certification.

---

## C. Chaos engineering with LLMs / steady-state hypothesis verification (2025)

### 12. ChaosEater — fully automated CE with LLMs (Jan 2025, arXiv 2501.11107)
- **URL:** https://arxiv.org/abs/2501.11107
- **Summary:** End-to-end LLM pipeline that performs requirement definition, fault-injection code generation, debugging, and validation for Kubernetes-IaC systems — i.e. the LLM is the chaos engineer. Demonstrated to complete single CE cycles at "significantly low time and monetary cost".
- **Technique:** Agentic CE cycle: hypothesize → generate K8s manifests + chaos resources → execute → verify → critique.
- **Fit to AgentCert:** AgentCert today *consumes* traces from human-designed faults. A ChaosEater front-end would let AgentCert close the loop: pipeline emits a Phase 3 weakness → ChaosEater generates a targeted follow-up fault → re-run AgentCert. This is the most direct 2025-only extension.

### 13. ChaosEater-Plus — "Anyone can build resilient systems" (Nov 2025, arXiv 2511.07865)
- **URL:** https://arxiv.org/abs/2511.07865
- **Summary:** Follow-up by Kikuta et al. emphasizing cost-down ("at low cost") and accessibility, with refined hypothesis-verification prompts and an explicit steady-state predicate language. The hypothesis-verification module is the part most directly transferable to AgentCert.
- **Technique:** Predicate DSL for steady-state hypotheses + LLM verifier scoring pre/post chaos windows.
- **Fit to AgentCert:** Adopt the predicate DSL in `cert_builder/` so the Section "hypothesis verification" becomes machine-checkable rather than purely narrative. Closes a current weakness where Phase 3 narrative builders can drift from the underlying numbers.

### 14. "Assessing and Enhancing Robustness of LLM-MAS Through Chaos Engineering" (May 2025, arXiv 2505.03096)
- **URL:** https://arxiv.org/abs/2505.03096
- **Summary:** Joshua Owotogbe applies CE specifically to LLM multi-agent systems, injecting hallucinations, agent failures, and inter-agent communication failures. The first paper to define a steady-state hypothesis vocabulary for *agent* systems (not just K8s services).
- **Technique:** CE fault taxonomy mapped to MAS failure modes + resilience scoring.
- **Fit to AgentCert:** Directly aligned with AgentCert's domain. Borrow its fault taxonomy as the canonical label set for `fault_analyzer/` buckets, replacing the current ad-hoc taxonomy. Also borrow the resilience score formulation for Phase 3 Section 8/9.

### 15. LitmusChaos MCP Server (2025)
- **URL:** https://github.com/litmuschaos/litmus-mcp-server
- **Summary:** Litmus shipped an official MCP server in 2025 exposing chaos experiments to any MCP-compatible LLM client ("conversational chaos"). Net-new in 2025 — Litmus 3.x had no AI integration.
- **Technique:** MCP tools for create/run/observe ChaosExperiment CRDs.
- **Fit to AgentCert:** AgentCert can become an MCP *client* of Litmus to trigger chaos and an MCP *server* to expose certification queries. This gives a standard interface for the closed-loop pattern in (12).

### 16. "Intelligent Failure: Using AI To Push Your Cluster To the Brink" — KubeCon NA 2025
- **URL:** https://kccncna2025.sched.com/event/27FN3 (Ilse & Levan, Solo.io)
- **Summary:** Practitioner talk on using LLMs to generate adversarial fault injection plans against Kubernetes clusters, including hypothesis generation from observability baselines. Bridges the gap between research (ChaosEater) and production tooling.
- **Technique:** LLM-as-attacker that reads Prometheus baselines and proposes fault sequences.
- **Fit to AgentCert:** The "read baseline → propose fault" loop is exactly what AgentCert needs to extend from passive certifier to active prober; the talk's prompt structures are reusable.

---

## What AgentCert can adopt that didn't exist in 2024

| Capability | 2025/26 source | Where it lands |
|---|---|---|
| `gen_ai.*` agent-span vocabulary | Sources 1, 2, 5 | `fault_analyzer/` event normalizer |
| Compressed-context Phase-1 extraction | Source 7 (MicroRCA-Agent) | `metrics_extractor/` pre-processor |
| Causal "what-if" over N runs | Source 9 (GALA) | `aggregator/` new analyzer |
| SOP-grounded qualitative extraction | Source 10 (Flow-of-Action) | `metrics_extractor/config/sops/` |
| MCTS-bounded LLM Council | Source 11 (KnowledgeMind) | `aggregator/` council orchestrator |
| Steady-state predicate DSL | Sources 12, 13 (ChaosEater) | `cert_builder/` Section validators |
| LLM-MAS fault taxonomy | Source 14 | `fault_analyzer/` bucket labels |
| Closed-loop chaos via MCP | Sources 15, 16 | New `chaos_driver/` module |

Headline gap closed by 2025 work: in 2024 there was no published predicate language for verifying agent-system steady-state hypotheses. ChaosEater-Plus + LLM-MAS-CE now provide one — AgentCert can promote Phase 3 hypothesis sections from prose to checkable assertions.
