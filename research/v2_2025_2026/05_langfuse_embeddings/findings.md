# 2025-2026 Sources: Langfuse v3, OSS LLM Observability, Judge Bias, and Embedding Models

Scope: 15 sources prioritized for **2025-2026** with explicit fit to AgentCert's four-phase pipeline (Phase 0 bucketing, Phase 1 extraction, Phase 2 aggregation, Phase 3 certification). All `[2025+]` items are tagged for immediate adoption review.

---

## A. Langfuse v3 and Tracing Platform Evolution

### 1. Langfuse v3 Release & Self-Hosted Architecture `[2025+]`
- **Year:** 2025 (v3.0 GA Q1 2025; minor releases through 2026)
- **URL:** https://langfuse.com/changelog and https://langfuse.com/self-hosting
- **Summary:** v3 splits the monolith into Web, Worker, ClickHouse (analytics), Redis (queue), and S3-compatible blob layers, enabling horizontal scaling for high-trace workloads. The OSS self-hosted edition now supports SSO, RBAC, and ClickHouse-backed analytics that previously were Cloud-only.
- **Technique:** Event-driven async ingestion (Web → Redis → Worker → ClickHouse) with idempotent upserts.
- **AgentCert fit:** **Phase 0 ingestion.** Replaces the current single-Postgres backend; ClickHouse query performance directly improves bucketing read-throughput for large chaos runs (>10k spans per fault).

### 2. Langfuse OpenTelemetry Ingestion (OTel `/api/public/otel`) `[2025+]`
- **Year:** 2025
- **URL:** https://langfuse.com/docs/opentelemetry/get-started
- **Summary:** Langfuse added a native OTLP/HTTP endpoint that ingests OpenTelemetry GenAI semantic-convention spans directly, so SDK-less Python or any OTel exporter (Traceloop, OpenLLMetry, OpenLIT) writes natively. Eliminates vendor-locked SDK wrapping for LangChain/LlamaIndex/CrewAI agents.
- **Technique:** OTLP HTTP/Protobuf receiver mapped to Langfuse trace/observation schema; respects `gen_ai.*` semconv attributes.
- **AgentCert fit:** **Phase 0.** Lets the Kubernetes chaos agent emit OTel spans without the Langfuse SDK, decoupling instrumentation from the certifier.

### 3. Langfuse Datasets v2 + Prompt Experiments `[2025+]`
- **Year:** 2025
- **URL:** https://langfuse.com/docs/datasets/overview and https://langfuse.com/docs/prompts/experiments
- **Summary:** Datasets v2 introduces versioned dataset items, run-comparison views, and dataset-linked LLM-as-Judge evaluators that fire on each experiment run. Prompt Experiments let teams A/B prompt versions over a dataset with metric diffs (cost, latency, score) inline.
- **Technique:** Dataset-run join + judge templates with structured-output rubrics.
- **AgentCert fit:** **Phase 1 & Phase 3 regression.** Turn each canonical fault scenario into a Dataset item; nightly run-comparison catches prompt-version regressions in the extractor and narrative builders.

### 4. Langfuse Managed LLM-as-Judge Templates + Custom Evaluators `[2025+]`
- **Year:** 2025
- **URL:** https://langfuse.com/docs/scores/model-based-evals
- **Summary:** Langfuse ships shareable judge templates (helpfulness, correctness, hallucination, toxicity) with variable interpolation, structured `score + reasoning` output, and per-trace or per-dataset-run triggering. Custom evaluators support Python sandbox execution in v3.5+.
- **Technique:** Templated judge prompt + Pydantic-validated JSON score.
- **AgentCert fit:** **Phase 2 LLM Council.** Reuse the existing judge templates as the per-judge prompts of the Council to standardize rubric vocabulary across runs.

### 5. Langfuse-MCP Server (Community + Official) `[2025+]`
- **Year:** 2025
- **URL:** https://github.com/langfuse/langfuse-mcp (and `mcp-server-langfuse`)
- **Summary:** An MCP server exposes Langfuse traces, prompts, datasets, and scores as MCP resources/tools so IDE agents (Claude Code, Cursor) can query and create them. Enables "ask Claude why fault-42 failed" directly against trace data.
- **Technique:** MCP stdio/SSE wrapping the Langfuse SDK; resource URIs scoped per project.
- **AgentCert fit:** **Phase 0/3 dev-loop.** Lets engineers query bucket lifecycle from inside the IDE without dumping JSON; also supports a future Phase-3 "self-debug" agent.

---

## B. Open-Source LLM Observability & Eval Platforms (2025)

### 6. Arize Phoenix v5 + OpenInference 2.0 `[2025+]`
- **Year:** 2025
- **URL:** https://arize.com/docs/phoenix/release-notes
- **Summary:** Phoenix v5 unifies tracing + eval + datasets, ships an LLM-as-Judge library (`phoenix.evals`) with consistency-checking judges, and adds **Sessions** for multi-turn agent debugging. OpenInference 2.0 standardizes agent-span semantic conventions (tool calls, retriever, reranker, guardrail) that overlap with OTel GenAI.
- **Technique:** OpenInference spans + DuckDB-backed eval analytics.
- **AgentCert fit:** **Phase 0/1.** Sessions map naturally onto per-fault buckets; OpenInference 2.0 attrs give Phase 1 a richer schema for tool-call extraction than raw Langfuse generations.

### 7. Comet Opik (OSS Eval Platform, MIT) `[2025+]`
- **Year:** Launched late 2024, major 2025 releases
- **URL:** https://github.com/comet-ml/opik
- **Summary:** Opik is a self-hostable OSS observability+eval system with built-in heuristic and LLM-judge metrics (hallucination, moderation, G-Eval, AnswerRelevance) plus a Pytest-style `opik-evaluate` runner. It scales to millions of traces and integrates with LangChain, LiteLLM, OpenAI Agents, ADK.
- **Technique:** ClickHouse traces + Python eval SDK with online + offline scoring.
- **AgentCert fit:** **Phase 2 alternative aggregator.** Opik's `G-Eval` and `Moderation` metrics can replace or audit the LLM Council judges; also a Langfuse-substitute when self-hosting friction matters.

### 8. Weights & Biases Weave 2.0 `[2025+]`
- **Year:** 2025
- **URL:** https://weave-docs.wandb.ai
- **Summary:** Weave 2.0 added Scorers (callable evaluators that can run online), Leaderboards for comparing prompt/model versions across datasets, and OTel ingestion. Strong UI for nested agent traces with cost/latency rollups.
- **Technique:** `weave.Scorer` decorator + `weave.Evaluation.evaluate()` async parallel runner.
- **AgentCert fit:** **Phase 2/3 benchmarking.** Use Weave Leaderboards to compare GPT-4 vs GPT-4o vs o1 as the extraction/reasoning model across the same fault corpus.

### 9. Traceloop OpenLLMetry 2025 + OTel GenAI Conventions `[2025+]`
- **Year:** 2025
- **URL:** https://github.com/traceloop/openllmetry and https://opentelemetry.io/docs/specs/semconv/gen-ai/
- **Summary:** OpenLLMetry's 2025 releases align fully with the OTel GenAI semantic conventions ratified mid-2025 (`gen_ai.request.*`, `gen_ai.usage.input_tokens`, `gen_ai.tool.call.*`). Provides one-line instrumentation for 30+ LLM/vector/agent libs, exportable to Langfuse, Phoenix, Grafana, Datadog.
- **Technique:** Monkey-patch instrumentations producing OTel spans with GenAI semconv.
- **AgentCert fit:** **Phase 0.** The chaos agent can ship to Langfuse via OTel without code changes; Phase 1 extraction can rely on standardized attribute names instead of Langfuse-specific fields.

### 10. OpenLIT, Helicone, Lunary, Laminar, Langtrace (Comparative Brief) `[2025+]`
- **Year:** 2025 releases of each
- **URLs:** https://openlit.io, https://helicone.ai, https://lunary.ai, https://lmnr.ai, https://langtrace.ai
- **Summary:** All five are 2025-active OSS observability projects standardizing on OTel GenAI: **OpenLIT** adds GPU and vector-DB metrics; **Helicone** offers proxy-based caching and PII redaction; **Lunary** focuses on prompt-mgmt + safety; **Laminar** ships Rust-based high-throughput ingest + browser-agent traces; **Langtrace** is OpenTelemetry-native with semantic conv compliance certifications.
- **Technique:** OTel exporters + per-platform analytics backends (ClickHouse / Postgres / DuckDB).
- **AgentCert fit:** **Phase 0 vendor-neutral.** Helicone's PII redaction is interesting for Phase 3 reports; Laminar's browser traces are relevant if AgentCert ever certifies UI-agents.

---

## C. LLM-as-Judge Bias Research (2025)

### 11. JudgeBench (ICLR 2025) `[2025+]`
- **Year:** 2025
- **URL:** https://arxiv.org/abs/2410.12784 and https://github.com/ScalerLab/JudgeBench
- **Summary:** A 350-pair benchmark of objectively-verifiable responses (math, code, reasoning, knowledge) where the ground-truth winner is known, exposing that strong judges like GPT-4o achieve only ~57% accuracy on hard pairs while o1-preview reaches ~75%. Provides per-domain breakdown of judge failure modes.
- **Technique:** Pairwise judging on verified-truth pairs; separates judge ability from annotator preference.
- **AgentCert fit:** **Phase 2 Council calibration.** Run candidate judge models against JudgeBench before promoting them into the Council; gate model upgrades on JudgeBench delta.

### 12. RewardBench 2 (Allen AI / Lambert et al., 2025) `[2025+]`
- **Year:** 2025
- **URL:** https://huggingface.co/spaces/allenai/reward-bench and https://arxiv.org/abs/2506.01937
- **Summary:** Second-generation reward-model and generative-judge benchmark, with multi-skill domains (factuality, precise IF, safety, math, ties) and a hard "best-of-4" format that prevents overfitting to v1. Demonstrates persistent **format bias** and **verbosity bias** even in 2025 frontier judges.
- **Technique:** Multi-domain pairwise preference + best-of-4 selection; explicit tie handling.
- **AgentCert fit:** **Phase 2.** Use RewardBench-2 categories to weight Council judges per dimension (e.g., prefer factuality-strong judges for TTD/TTR claims, safety-strong for chaos remediation narratives).

### 13. Panel of LLM Judges (PoLL) and Position-Bias Mitigation `[2025+]`
- **Year:** 2024 paper, 2025 production patterns
- **URL:** https://arxiv.org/abs/2404.18796 (Verga et al., "Replacing Judges with Juries")
- **Summary:** PoLL replaces a single large judge with a panel of smaller diverse judges, achieving higher correlation to human preference at lower cost and with less intra-family bias. 2025 follow-ups add **swap-order debiasing**, **score-then-aggregate** (vs. vote), and **calibration-aware aggregation** against verbosity and self-preference.
- **Technique:** k-judge ensemble across model families + order-swap voting + meta-aggregator.
- **AgentCert fit:** **Phase 2 LLM Council.** Directly validates AgentCert's existing Council design; adopt explicit order-swap and family-diversity (e.g., GPT-4 + Claude + Gemini) to reduce self-preference.

### 14. 2025 Verbosity, Self-Preference, and Position-Bias Studies (Anthropic / Galileo / Allen AI)
- **Year:** 2025
- **URLs:**
  - Anthropic — "Evaluating LLM judges" (2025 alignment-science post)
  - Galileo — "LLM-as-a-Judge bias taxonomy" (2025 blog + whitepaper) https://www.galileo.ai/blog/llm-as-a-judge
  - Wataoka et al. — "Self-Preference Bias in LLM-as-a-Judge" (2025) https://arxiv.org/abs/2410.21819
- **Summary:** Converging 2025 findings: (a) judges prefer their own family's outputs even after blinding cues; (b) verbosity correlates ~0.3-0.5 with judge win-rate independent of content quality; (c) position bias persists at 5-15% even with swap-debiasing in long-context judging. Galileo's taxonomy adds **chain-of-thought bias** and **rubric-leakage bias**.
- **Technique:** Controlled length/order/family ablations + mitigation via constrained rubric + structured-output JSON.
- **AgentCert fit:** **Phase 2 + Phase 3.** Enforce structured-output judges, swap-order voting, cross-family panel, and **length-normalized scoring** before aggregating into the certification scorecard.

---

## D. 2025 Embedding Models for Trace/Log Retrieval

### 15. 2025 Embedding Model Landscape (Voyage-3, Cohere Embed v4, Snowflake Arctic-Embed-L-v2, NV-Embed-v2, Stella-1.5B-v5, BGE-Gemma2, GritLM, Mistral-Embed) `[2025+]`
- **Year:** 2024 Q4 - 2025
- **URLs:**
  - Voyage-3 / voyage-3-large — https://blog.voyageai.com/2024/09/18/voyage-3/ and 2025 large update
  - Cohere Embed v4 — https://cohere.com/blog/embed-4 (Apr 2025), multimodal + 128k context
  - Snowflake Arctic-Embed-L-v2.0 — https://huggingface.co/Snowflake/snowflake-arctic-embed-l-v2.0 (Dec 2024)
  - NV-Embed-v2 — https://huggingface.co/nvidia/NV-Embed-v2
  - Stella-1.5B-v5 — https://huggingface.co/dunzhang/stella_en_1.5B_v5
  - BGE-Multilingual-Gemma2 — https://huggingface.co/BAAI/bge-multilingual-gemma2
  - GritLM — https://arxiv.org/abs/2402.09906 (generative + embedding unified)
  - Mistral-Embed — https://docs.mistral.ai/capabilities/embeddings/
- **Summary:** 2025 leaders on MTEB v2 are dominated by Matryoshka-trained, LLM-distilled encoders. **Cohere Embed v4** adds 128k-context multimodal (text+image+PDF) with int8/binary outputs; **Voyage-3-large** leads code/finance verticals; **Arctic-Embed-L-v2** offers small-footprint multilingual; **NV-Embed-v2** and **Stella-1.5B** top open-weights leaderboards; **GritLM** unifies generation + embedding in one model.
- **Technique:** Decoder-LLM embedding with Matryoshka loss + instruction tuning + hard-negative mining.
- **AgentCert fit:** **Phase 0 retrieval & Phase 2 dedup.** Use Cohere Embed v4 or Voyage-3-large for long-trace chunk retrieval (128k context fits whole fault buckets); use Arctic-Embed-L-v2 or Stella-1.5B locally for similarity search across runs when classifying near-duplicate failures.

### 16. ColBERT-v2/PLAID + ColPali (Late-Interaction Retrieval for Visual Logs) `[2025+]`
- **Year:** ColBERT-v2 2022; **ColPali 2024-2025**, PLAID-X 2025
- **URLs:**
  - ColPali — https://arxiv.org/abs/2407.01449 and HF `vidore/colpali-v1.3`
  - PLAID-X 2025 — https://github.com/stanford-futuredata/ColBERT
- **Summary:** **ColPali** extends late-interaction retrieval to document **images** by embedding PDF/screenshot patches with a vision-LM (PaliGemma) and scoring with MaxSim, dramatically outperforming OCR→text→embedding pipelines on charts, tables, and dashboards. 2025 releases (ColPali-v1.3, ColQwen2) cut latency and add multilingual visual retrieval.
- **Technique:** Multi-vector patch embeddings + MaxSim late-interaction scoring; PLAID index for sub-second retrieval.
- **AgentCert fit:** **Phase 0/1 visual-log ingestion.** Kubernetes Grafana/Prometheus screenshots and chaos dashboards captured during runs become retrievable without OCR; enables Phase 3 to cite "the CPU spike at t=12s" with image grounding.

### 17. Matryoshka Representation Learning + Adaptive/Cascade Retrieval (2025 Production Patterns) `[2025+]`
- **Year:** MRL 2022; **2025 production playbooks**
- **URLs:**
  - MRL paper — https://arxiv.org/abs/2205.13147
  - Hugging Face MTEB-v2 2025 (Matryoshka adoption) — https://huggingface.co/blog/matryoshka
  - Cohere v4 / OpenAI text-embedding-3 / Nomic-v2 all ship MRL dims
- **Summary:** Matryoshka training packs coarse-to-fine information into prefix dimensions, enabling a single embedding to serve a **cascade**: cheap binary/64-dim ANN over the full corpus, then re-rank with 512-dim, then top-k with full 1536/3072-dim. 2025 production guides (Cohere, Pinecone, Qdrant, Weaviate) standardize this 3-stage cascade, cutting query cost 10-100x at <1% recall loss.
- **Technique:** Nested-loss training + int8/binary quantization + multi-stage rerank.
- **AgentCert fit:** **Phase 0 similarity search & Phase 2 cross-run dedup.** Run binary-quantized 64-dim search over the full trace store, re-rank with 256-dim, full-precision rerank only top-50. Critical when bucketing 100k+ events per certification run.

---

## Adoption Priority for AgentCert

| Priority | Source | Phase | Action |
|---|---|---|---|
| P0 | Langfuse v3 + OTel ingest (1, 2) | Phase 0 | Migrate self-hosted to v3; switch agent to OTel exporter |
| P0 | PoLL + 2025 bias mitigations (13, 14) | Phase 2 | Add swap-order, cross-family Council, length-normalized scoring |
| P1 | Langfuse Datasets v2 + judge templates (3, 4) | Phase 1, 3 | Canonicalize fault scenarios as Datasets; share judge templates with Council |
| P1 | Cohere Embed v4 / Voyage-3-large + MRL cascade (15, 17) | Phase 0 retrieval | Replace ada-002 with MRL-enabled embedder; binary→full cascade |
| P2 | JudgeBench / RewardBench-2 (11, 12) | Phase 2 | Gate Council judge upgrades on these benchmarks |
| P2 | Phoenix v5 / Opik (6, 7) | Phase 2 alt | Evaluate as Langfuse complement or fallback |
| P3 | ColPali (16) | Phase 0/1 visual | Pilot when dashboard screenshots enter the pipeline |

---

## Key 2025-2026 Themes

1. **OTel GenAI semconv won.** Every serious 2025 platform speaks it; vendor SDK lock-in is over.
2. **Self-hosted parity with cloud.** Langfuse v3, Phoenix v5, Opik, Weave all ship full-feature OSS.
3. **Bias is measurable and partially fixable.** JudgeBench + RewardBench-2 + swap-debias + family-diverse panels are now table stakes for any production Council.
4. **Embeddings are LLMs now.** Top-MTEB models are decoder-LLMs (NV-Embed, GritLM, Stella) with Matryoshka heads, making cascade retrieval cheap and tunable.
5. **Late-interaction on images.** ColPali removes OCR from observability pipelines that include dashboards/screenshots — directly relevant to chaos-engineering visual evidence.
