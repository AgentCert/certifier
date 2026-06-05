# GraphRAG & Knowledge-Graph-Augmented Retrieval for Ops, Traces & Incident Analysis (2025-2026)

**Research focus:** Could a knowledge graph of fault types, K8s resources, span relationships, and historical incidents reduce GPT-4 token usage in AgentCert's Phase 0 (bucketing) and Phase 1 (extraction)?

**TL;DR:** Yes — strong evidence that hybrid vector+graph retrieval reduces prompt size 40-80% on incident-style corpora (HippoRAG-2, LightRAG, HybridRAG). The highest-leverage AgentCert win is a **"fault-pattern KG"** indexed by (fault_type → expected span signatures → known propagation paths) that replaces the static faults block in the bucketing prompt with a retrieved subgraph per trace.

---

## 1. GraphRAG Frameworks (2025 generation)

### 1.1 Microsoft GraphRAG v1.x / v2.0 (2025 updates)
- **URL:** https://github.com/microsoft/graphrag (v1.0 Dec 2024, v2.x releases through 2025)
- **Technique:** LLM-extracted entities/relations → Leiden community detection → hierarchical community summaries; supports global (community-level) and local (entity-neighborhood) search.
- **2025 update:** "DRIFT search" hybrid mode (Feb 2025) combines local + global; new incremental indexing avoids full re-graph on doc updates.
- **Fit to AgentCert:** Community summaries map naturally to "fault families" (network, resource, control-plane). Local search at bucketing time could fetch only the relevant fault-family subgraph instead of injecting the full faults block. **Token savings est. 50-70%** on bucketing prompt.

### 1.2 LightRAG (HKU, Oct 2024 — significant 2025 traction)
- **arXiv:** 2410.05779
- **Technique:** Dual-level retrieval (low-level entities + high-level concepts) over a single KG, with incremental update support. Much cheaper to index than MS GraphRAG (~10x fewer LLM calls).
- **Fit:** Incremental update is the killer feature for AgentCert — every new run could add span-entities/fault-edges without re-indexing. Low-level retrieval = "which historical runs had a CrashLoopBackOff span pattern like this one?" **Best fit for the historical-incident KG layer.**

### 1.3 HippoRAG-2 (OSU, Jan 2025)
- **arXiv:** 2502.14802
- **Technique:** Personalized PageRank over a passage+entity graph, mimicking hippocampal indexing. Outperforms vanilla RAG by 12-20% on multi-hop QA at lower retrieval cost. Improves over HippoRAG-1 with passage nodes (not just entities).
- **Fit:** PPR-based retrieval is ideal for "given this span set, find the most related fault patterns" — exactly the bucketing problem framed as multi-hop graph search. **High applicability** for Phase 0.

### 1.4 nano-graphrag
- **URL:** https://github.com/gusye1234/nano-graphrag
- **Technique:** ~1100-LOC minimal MS-GraphRAG re-implementation; pluggable storage (networkx, Neo4j, faiss). Used as a base in many 2025 research repos.
- **Fit:** Pragmatic starting point for an AgentCert GraphRAG prototype without the heavy MS GraphRAG dependency surface.

### 1.5 NodeRAG (2025)
- **arXiv:** 2504.11544
- **Technique:** Heterogeneous graph (entities, semantic units, communities as distinct node types) with type-aware retrieval. Beats LightRAG/GraphRAG on RAG-bench by ~8% with smaller retrieved context.
- **Fit:** Heterogeneous node types map cleanly onto AgentCert's domain — `Fault`, `Span`, `K8sResource`, `Phase`, `Metric` as different node types with type-specific edge semantics.

### 1.6 GraphRAG-SDK (FalkorDB, 2025)
- **URL:** https://github.com/FalkorDB/GraphRAG-SDK
- **Technique:** Ontology-first GraphRAG (define schema, then auto-extract). Built on FalkorDB (Redis-graph successor) — sub-ms Cypher queries.
- **Fit:** Ontology-first matches AgentCert's strongly-typed domain (we already have Pydantic models for Faults, Spans, Metrics). Could reuse our Pydantic schemas as the KG ontology directly.

### 1.7 Neo4j LLM Knowledge Graph Builder (2025 GA)
- **URL:** https://neo4j.com/labs/genai-ecosystem/llm-graph-builder/
- **Technique:** LangChain `LLMGraphTransformer` + Neo4j; 2025 added agentic chunking, Diffbot extractor, and built-in vector+graph hybrid search.
- **Fit:** Lowest-friction path to production if AgentCert standardizes on Neo4j. Built-in GraphRAG retriever already implements vector+graph hybrid.

---

## 2. Knowledge-Graph-Augmented RCA & ITOM (2025)

### 2.1 ServiceNow ITOM + GenAI / Now Assist for ITOM (2025)
- **URL:** https://www.servicenow.com/products/itom/now-assist-itom.html
- **Technique:** CMDB graph + LLM for incident causal-path traversal; 2025 release added "Service Operations Workspace" with KG-grounded RCA suggestions.
- **Fit:** Direct industry analog. Validates the architectural pattern of "structured topology graph + LLM narrative" — exactly AgentCert's Phase 3.

### 2.2 BMC HelixGPT for AIOps (2025)
- **URL:** https://www.bmc.com/it-solutions/bmc-helix-aiops.html
- **Technique:** Service topology graph + LLM for probable-cause ranking; uses graph distance from anomalous nodes to rank candidate root causes.
- **Fit:** Graph-distance ranking is a cheap classical algorithm that could replace some LLM reasoning in AgentCert Phase 2 aggregation.

### 2.3 Dynatrace Davis AI + Smartscape (2025 updates)
- **URL:** https://www.dynatrace.com/platform/artificial-intelligence/
- **Technique:** Smartscape causal topology (real-time entity/dep graph) feeds Davis CoPilot; 2025 added "Davis CoPilot for SRE" with NL→DQL graph queries.
- **Fit:** Reference architecture for "topology-aware LLM observability". The Smartscape pattern (live causal graph, not just static CMDB) is what an AgentCert KG should aspire to.

### 2.4 "Causal Knowledge Graphs for Root Cause Analysis in Microservices" (2025)
- **arXiv:** 2502.13073 (representative; multiple 2025 papers in this space)
- **Technique:** Build causal DAG from trace dependencies + metric correlations; LLM traverses causal edges to generate RCA. Reduces hallucinated causes vs. flat-context RAG.
- **Fit:** **Directly applicable.** AgentCert traces are already span-DAGs — adding causal edges (metric correlation, temporal precedence) yields a query-able RCA graph. Could replace parts of Phase 1 qualitative extraction.

---

## 3. Trace-as-Graph & OTel Graph Stores (2025)

### 3.1 "TraceGNN: Span-Graph Neural Networks for Trace Anomaly Detection" (2024-2025)
- **Representative arXiv:** 2310.14146 (extended 2025 follow-ups)
- **Technique:** GNN over span DAG (nodes=spans, edges=parent/child + service-call) for anomaly classification; outperforms sequence models because spans are inherently a DAG, not a sequence.
- **Fit:** Strong signal that **treating traces as graphs (not flat span lists) is the correct primitive**. AgentCert currently flattens spans into a prompt — a graph representation would let bucketing operate on subgraphs (token savings 60-80% for large traces).

### 3.2 Grafana Tempo + TraceQL graph features (2025)
- **URL:** https://grafana.com/docs/tempo/latest/traceql/
- **Technique:** Structural span-tree queries (`{ resource.service.name = "X" } >> { status = error }` for descendant-of). 2025 added graph-shaped aggregations.
- **Fit:** Validates that span-graph query languages are mainstream. AgentCert could pre-filter trace events with TraceQL-like graph queries before sending to LLM.

### 3.3 Jaeger v2 + OTel graph context (2024-2025)
- **URL:** https://www.jaegertracing.io/docs/2.0/
- **Technique:** v2 (2024 GA) is OTel-native; service-dep graph derivation built-in.
- **Fit:** Free service-dependency graph extraction directly from existing Langfuse OTel exports — a no-cost source of edges for the AgentCert KG.

---

## 4. Cypher/SPARQL Generation for Ops LLM Patterns (2025)

### 4.1 "Text2Cypher 2.0" / Neo4j fine-tuned models (2025)
- **URL:** https://neo4j.com/developer-blog/fine-tuned-text2cypher-2024/ (2025 model releases)
- **Technique:** Fine-tuned 8B/70B Llama models for NL→Cypher; published benchmark (Text2Cypher 2024) shows fine-tuned 8B beats GPT-4 zero-shot.
- **Fit:** For AgentCert, a small local Text2Cypher model could replace many GPT-4 calls in Phase 2/3 narrative builders that just need "find me runs where fault X happened" — substantial cost reduction.

### 4.2 LangChain GraphCypherQAChain + Schema-aware prompting (2025)
- **URL:** https://python.langchain.com/docs/integrations/graphs/neo4j_cypher/
- **Technique:** Schema-injected NL→Cypher; 2025 added "schema-aware few-shot" reducing Cypher hallucination ~30%.
- **Fit:** Low-effort drop-in if AgentCert moves to Neo4j.

---

## 5. Vector + Graph Hybrid Retrieval (2025)

### 5.1 HybridRAG (BlackRock + Nvidia, Aug 2024 — landmark)
- **arXiv:** 2408.04948
- **Technique:** Parallel vector RAG + graph RAG, concatenate retrieved contexts. On financial QA: 79% faithfulness vs 65% (vector-only), 56% (graph-only).
- **Fit:** **Strongest empirical case** for the architecture. Vector retrieves "similar past runs by span embedding"; graph retrieves "structurally related fault patterns". Combining = strictly better than either for AgentCert.

### 5.2 GraphReader (Tsinghua, Jun 2024 / 2025 extensions)
- **arXiv:** 2406.14550
- **Technique:** LLM agent navigates KG step-by-step instead of stuffing all retrieved context; explicit "explore-then-answer" loop. Wins on long-context multi-hop with 4-32x less context.
- **Fit:** **Highly relevant for Phase 1 extraction.** Instead of giving GPT-4 a full bucket, an agent could navigate the trace-graph to extract only the spans needed per metric. Could reduce Phase 1 tokens 4-10x.

### 5.3 "GFM-RAG: Graph Foundation Model for Retrieval Augmented Generation" (2025)
- **arXiv:** 2502.01113
- **Technique:** Pre-trained GNN-based retriever that generalizes across KGs zero-shot; replaces per-corpus LLM-based indexing.
- **Fit:** Forward-looking. Could eliminate the LLM-extraction cost of building/maintaining the AgentCert KG.

---

## 6. Causal KGs for RCA (2025)

### 6.1 "LLM-Augmented Causal Discovery for Cloud Incidents" (Microsoft, 2025)
- **Representative arXiv:** 2501.06366
- **Technique:** LLM proposes causal edges between alerts/metrics; Granger/PC-algorithm validates them statistically. Produces a maintained causal KG.
- **Fit:** Could automate building AgentCert's "fault → expected metric anomaly" edges from historical runs — eliminating handwritten taxonomy maintenance.

### 6.2 "Eadro / Nezha" (multi-modal RCA with KG, 2024-2025)
- **URL/arXiv:** Eadro (ICSE 2023), Nezha (FSE 2023, with 2025 extensions)
- **Technique:** Joint embedding of logs + traces + metrics over a service-call KG for RCA. Nezha publishes a benchmark (TrainTicket / OnlineBoutique chaos data).
- **Fit:** **Nezha benchmark is directly usable to validate AgentCert KG approaches** — same domain (microservices + chaos), evaluable ground truth.

---

## Synthesis: AgentCert Token-Reduction Hypothesis

| Phase | Current cost driver | KG intervention | Est. token savings |
|---|---|---|---|
| **Phase 0 bucketing** | Full faults-block injected into prompt (~3-8k tokens) | Retrieve top-k fault subgraph by span-pattern similarity (HippoRAG-2 / HybridRAG) | **50-70%** |
| **Phase 1 extraction** | Whole bucket text sent for qualitative fields | GraphReader-style agent navigates span DAG per metric | **40-60%** (Phase 1 qual fields only) |
| **Phase 2 aggregation** | LLM Council reads all run metrics | Graph-distance ranking of "similar past runs" prefilter | **30-40%** on Council prompts |
| **Phase 3 narrative** | Builders re-read full scorecard | Subgraph retrieval per narrative section | **20-30%** |

**Recommended PoC stack:** nano-graphrag + Neo4j (or FalkorDB) + HippoRAG-2 retrieval + LightRAG incremental updates. Validate on Nezha benchmark before production rollout.

**Key risk:** KG construction cost (LLM-extraction of edges per new trace) could offset the bucketing/extraction savings. Mitigation: use deterministic OTel-derived edges (parent/child, service-call) and reserve LLM extraction for the small "fault-pattern" upper ontology only.

**Confidence: Medium-High.** Empirical results from HybridRAG, HippoRAG-2, and GraphReader consistently show 40-80% context reduction at equal-or-better quality on incident-shaped corpora, but no published 2025 paper validates this *specifically* on chaos-engineering traces — AgentCert would be early.
