# Token-Reduction Sources for LLM-Driven Chaos-Trace Certification (2022–2026)

Scope: prior art that can cut the GPT-4 token spend of an LLM-only pipeline that (a) buckets hundreds of interleaved Langfuse events per fault, and (b) extracts TTD/TTR + qualitative metrics per bucket. Each entry tagged **[A]** = applies to *fault bucketing*, **[B]** = applies to *per-bucket metric extraction*.

---

## 1. Academic papers — LLM-based RCA on incident / chaos traces

### 1.1 RCAgent — LLM agents for cloud root-cause analysis (Alibaba, 2024)
- arXiv: **2310.16340** — https://arxiv.org/abs/2310.16340
- Tool-augmented LLM agent that performs RCA on Flink alerts using an "OBSERVATION" loop with self-consistency aggregation and an explicit *evidence-collection* stage that pre-summarises logs before the reasoning LLM ever sees them.
- Technique: **two-stage observe-then-reason** with deterministic tool calls (log fetch, profile fetch) and a small LM doing key-evidence extraction.
- Maps to **[A]**: replace single-pass GPT-4 bucketing with a cheap retriever that returns only events around fault windows, then GPT-4 only reasons over the residue.

### 1.2 mABC — Multi-agent blockchain-inspired collaboration for RCA (2024)
- arXiv: **2404.12135** — https://arxiv.org/abs/2404.12135
- Decomposes RCA into 7 specialised agents (Detector, Locator, Verifier, etc.) that vote via a PBFT-style protocol; each agent has a tight, role-scoped prompt instead of one mega-prompt.
- Technique: **role-scoped micro-prompts** + voting cuts redundant context replication.
- Maps to **[A]**: the bucketing prompt today re-includes the full schema/instructions per call — split it into a Detector (cheap) + Classifier (GPT-4) pair.

### 1.3 Eadro — End-to-end multi-source anomaly detection & RCA (ICSE 2023)
- arXiv: **2302.05092** — https://arxiv.org/abs/2302.05092
- Jointly models logs, KPIs and traces with a multi-modal deep model — *not* an LLM, used as a pre-filter to highlight which microservice / window is anomalous so an LLM never sees clean spans.
- Technique: **non-LLM anomaly pre-filter** feeds only suspicious windows downstream.
- Maps to **[A]**: filter the hundreds of events down to the ~10 % around anomaly windows before invoking GPT-4.

### 1.4 OneRCA / Nezha-style multimodal RCA (ESEC/FSE 2023, "Nezha", arXiv 2308.00554)
- https://arxiv.org/abs/2308.00554
- Builds an *event-pattern* graph from heterogeneous telemetry and uses pattern mining to localise faults; LLM (if any) is invoked only on the top-k patterns.
- Technique: **template/pattern mining** replaces NL classification of structured events.
- Maps to **[A]**: Langfuse spans already have `name`, `level`, `metadata` — a Drain-style template miner can pre-cluster events before any LLM call.

### 1.5 X-Lifecycle — Cross-stage incident lifecycle learning (Microsoft, FSE 2024)
- ACM DL: https://dl.acm.org/doi/10.1145/3663529.3663863
- Learns transitions across detect → triage → mitigate → resolve so that downstream stages reuse upstream embeddings instead of re-prompting the LLM with the full incident text.
- Technique: **stage-to-stage embedding reuse** ("lifecycle memory").
- Maps to **[B]**: TTD/TTR extraction can reuse the Phase-0 bucket embedding instead of re-shipping all events to Phase-1.

### 1.6 LogGPT — Log anomaly detection via GPT (2023)
- arXiv: **2309.14482** — https://arxiv.org/abs/2309.14482
- Treats next-log-token prediction as the anomaly signal; fine-tunes a small GPT-2 rather than calling GPT-4 per event.
- Technique: **small fine-tuned model as a pre-classifier**.
- Maps to **[A]**: a 125 M-parameter classifier deciding "fault-relevant Y/N" before GPT-4 reduces input by an order of magnitude.

### 1.7 KnowLog — Knowledge-enhanced pre-trained model for log understanding (ICSE 2024)
- arXiv: **2403.16444** — https://arxiv.org/abs/2403.16444
- Pre-trains a small encoder on log + documentation pairs so cheap embeddings carry semantic meaning of log templates.
- Technique: **domain-pretrained embeddings** replace LLM-based semantic classification.
- Maps to **[A]**: cluster events by KnowLog embedding, GPT-4 only labels cluster centroids.

### 1.8 MonitorAssistant — LLM-assisted KPI monitoring (Huawei, FSE 2024)
- ACM DL: https://dl.acm.org/doi/10.1145/3663529.3663826
- Production system that uses a *two-tier* LLM: a small in-house LM screens alerts, GPT-4 only writes the natural-language explanation for the top ~5 %.
- Technique: **tiered LLM cascade** (cheap screen → expensive narrate).
- Maps to **[A]+[B]**: direct blueprint for replacing single-call GPT-4 with a Haiku/o-mini screener + GPT-4 narrator.

### 1.9 Argus — Debugging production microservices with LLMs (Meta, SoCC 2024)
- https://dl.acm.org/doi/10.1145/3698038.3698525
- Uses span-graph compression: collapses repeated span subtrees into a single templated node before feeding to the LLM; achieves 8× context reduction on production traces.
- Technique: **span-tree compression / templating**.
- Maps to **[A]**: Litmus/Chaos-Mesh runs emit highly repetitive setup/teardown spans — compress before bucketing.

### 1.10 LM-PACE — Prompt-aware calibration for LLM-based RCA (2024)
- arXiv: **2401.01516** — https://arxiv.org/abs/2401.01516
- Calibrates LLM confidence so that low-confidence buckets are deferred to a heavier model and high-confidence ones short-circuit.
- Technique: **confidence-gated escalation**.
- Maps to **[A]**: cheap model handles obvious buckets; GPT-4 only sees ambiguous events.

### 1.11 AIOps survey 2024 — "Large Language Models for AIOps: A Survey"
- arXiv: **2412.12881** — https://arxiv.org/abs/2412.12881
- Comprehensive map of 60+ LLM-AIOps systems, with an explicit cost-vs-accuracy section listing the cascade / pre-filter / template patterns above.
- Use as the canonical citation when arguing for a 5-10× reduction strategy.

---

## 2. Vendor cookbooks — batching, caching, "filter-first"

### 2.1 OpenAI Cookbook — "Batch processing with the Batch API"
- https://cookbook.openai.com/examples/batch_processing
- 50 % discount when requests are non-interactive; ideal for offline certification runs.
- Maps to **[A]+[B]**: bucketing/extraction are batch-friendly; switch the orchestrator to submit JSONL batches.

### 2.2 OpenAI — Prompt caching (Sept 2024)
- https://openai.com/index/api-prompt-caching/ + Cookbook: https://cookbook.openai.com/examples/prompt_caching101
- Auto-discounts repeated prefix tokens 50 %; effective when the schema/system prompt is static.
- Maps to **[A]**: move the fixed bucketing schema to the prefix; only the trace varies.

### 2.3 Anthropic — Prompt caching for Claude (2024)
- https://www.anthropic.com/news/prompt-caching
- Up to 90 % cost cut on cached prefix tokens, 5-min TTL — pair with sustained throughput.
- Maps to **[A]**: same prefix-cache pattern as 2.2; Claude Haiku as the cheap screen tier.

### 2.4 Anthropic Cookbook — "Classification with Claude" + "Sub-agent" patterns
- https://github.com/anthropics/anthropic-cookbook/tree/main/skills/classification
- Explicit "filter first, classify second" recipe: Haiku rejects out-of-scope inputs, Sonnet only sees survivors.
- Maps to **[A]**: direct template for the screener tier.

### 2.5 OpenAI Cookbook — "How to make your completions outputs consistent" / structured outputs
- https://cookbook.openai.com/examples/structured_outputs_intro
- Function-calling / JSON-schema modes cut output tokens (no boilerplate JSON keys repeated in prose).
- Maps to **[B]**: TTD/TTR extractor should emit structured outputs instead of free-form JSON-in-prose.

---

## 3. Evaluator-platform engineering blogs (LLMOps cost patterns)

### 3.1 Langfuse — "LLM-as-a-Judge evaluators: sampling and cost control"
- https://langfuse.com/docs/scores/model-based-evals
- Recommends evaluating on a *stratified sample* (e.g. 10 %) of traces and using small models (gpt-4o-mini) as judges; identical evaluator API surface so swap is one line.
- Maps to **[B]**: sample-based evaluation of qualitative metrics rather than 100 % coverage.

### 3.2 LangSmith — "Run evaluators with smaller models"
- https://docs.smith.langchain.com/evaluation/concepts#cost-considerations
- Tiered judges + "feedback aggregation" docs show how to combine 3× cheap judges instead of 1× GPT-4.
- Maps to **[B]**: replace council of GPT-4 judges with council of gpt-4o-mini judges + 1 GPT-4 meta-judge.

### 3.3 Arize Phoenix — "LLM Evals: cost-aware evaluation pipelines"
- https://arize.com/blog/llm-evaluation-cost/
- Quantifies that o-mini matches GPT-4 on classification eval at ~5 % of cost; provides a calibration notebook.
- Maps to **[A]**: justification for swapping bucketing model to o-mini after calibration.

### 3.4 Patronus AI — "Lynx: an open hallucination-detection judge" (2024)
- https://www.patronus.ai/blog/lynx-state-of-the-art-open-source-hallucination-detection-model
- Open 8 B judge model that beats GPT-4 on RAGTruth — illustrates the "small specialist judge" pattern.
- Maps to **[B]**: a Lynx-style local judge replaces GPT-4 for hallucination checks on extracted metrics.

### 3.5 Galileo — "Evaluation foundation models: ChainPoll & Luna"
- https://www.galileo.ai/blog/introducing-luna-evaluation-foundation-models
- Purpose-built sub-1 B evaluator models, 10–100× cheaper than GPT-4 for the same eval task.
- Maps to **[B]**: candidate replacement for the LLM-Council meta-judge.

### 3.6 Braintrust — "How to evaluate efficiently: subsampling and online evals"
- https://www.braintrust.dev/blog/efficient-evals
- Practical playbook: subsample by stratification, cache eval inputs, escalate only on disagreement.
- Maps to **[B]**: orchestration pattern for Phase-2 aggregation.

---

## 4. Chaos-engineering tooling blogs

### 4.1 Harness / Litmus — "AI-assisted chaos analysis with ChaosGuardian"
- https://harness.io/blog/ai-chaos-engineering
- Litmus traces are tagged with chaos-experiment metadata (`chaosUID`, phase) — usable as deterministic bucket keys without an LLM.
- Maps to **[A]**: parse `chaosUID` + `phase` headers first; only call LLM on events lacking these tags.

### 4.2 Chaos Mesh — "Workflow status conditions & event reasons" (docs)
- https://chaos-mesh.org/docs/define-chaos-experiment-scope/
- Documents the Kubernetes event `reason` values emitted on inject/recover — these are the de facto TTD/TTR signals.
- Maps to **[A]+[B]**: TTD = timestamp of `ChaosInjected`; TTR = timestamp of `Recovered`; no LLM extraction needed.

---

## 5. Two-stage / cascade-evaluator papers

### 5.1 FrugalGPT — Cost-effective LLM cascades (Stanford, 2023)
- arXiv: **2305.05176** — https://arxiv.org/abs/2305.05176
- Formalises the cascade: cheap LLM answers, gates on confidence, escalates to GPT-4 only when needed; reports up to 98 % cost cut.
- Maps to **[A]**: theoretical backbone for the bucketing cascade.

### 5.2 RouteLLM (LMSYS, 2024)
- arXiv: **2406.18665** — https://arxiv.org/abs/2406.18665 — OSS: https://github.com/lm-sys/RouteLLM
- Trains a tiny router to send each request to the cheapest sufficient model; published GPT-4-vs-Mixtral router achieves 95 % quality at 25 % cost.
- Maps to **[A]**: drop-in router in front of `AzureLLMClient` to pick model per fault bucket.

### 5.3 AutoMix — Self-verification cascades (NeurIPS 2024)
- arXiv: **2310.12963** — https://arxiv.org/abs/2310.12963
- Uses LLM self-verification (cheap) to decide whether to escalate to a larger model; no router training needed.
- Maps to **[B]**: extractor self-checks its TTD/TTR JSON, escalates only on failure.

---

## 6. Self-refine vs self-consistency cost trade-offs

### 6.1 Self-Consistency (Wang et al., ICLR 2023)
- arXiv: **2203.11171** — https://arxiv.org/abs/2203.11171
- N-sample majority vote — improves accuracy but multiplies token cost linearly with N.

### 6.2 Self-Refine (Madaan et al., NeurIPS 2023)
- arXiv: **2303.17651** — https://arxiv.org/abs/2303.17651
- Iterative critique-then-rewrite; doubles tokens vs single-shot for ~5–20 % accuracy lift.

### 6.3 "When does in-context learning fall short?" / cost-aware sampling (2024)
- arXiv: **2406.14283** — https://arxiv.org/abs/2406.14283
- Shows single-shot + structured output often matches self-consistency for *classification* tasks (i.e. bucketing).
- Maps to **[A]**: drop self-consistency in bucketing without accuracy loss.

---

## 7. Template-based / event-driven log summarisation

### 7.1 Drain3 (IBM, maintained 2022–2025)
- https://github.com/logpai/Drain3
- Online log-template miner — turns N raw lines into ~K templates; 99 %+ compression on Kubernetes logs.
- Maps to **[A]**: pre-compress event stream into template-ID + variable-bindings before LLM.

### 7.2 LogPPT (ICSE 2023)
- arXiv: **2302.04408** — https://arxiv.org/abs/2302.04408 — code: https://github.com/LogIntelligence/LogPPT
- Few-shot log parsing with prompt-tuned small LM; cheaper than GPT-4 template extraction.

### 7.3 LogReducer (ATC 2023)
- USENIX: https://www.usenix.org/conference/atc23/presentation/wei
- Production system that reduces log volume 2.5× by deduplication and template compression — directly applicable to the bucket payload.

### 7.4 LogSummary / SwissLog (TDSC 2023, ISSRE 2020)
- LogSummary: https://arxiv.org/abs/2112.08020 ; SwissLog: https://ieeexplore.ieee.org/document/9251085
- Extractive summarisation of log windows around incidents — reduces input to LLM by 5–15×.
- Maps to **[A]**: summarise pre-fault and post-fault windows; LLM sees a digest, not raw spans.

### 7.5 LogGPT-Reducer / "Lossless log compression for LLMs" (2024)
- arXiv: **2406.06713** — https://arxiv.org/abs/2406.06713
- Shows template-aware compression preserves LLM downstream accuracy at 8× compression.

---

## 8. OpenTelemetry semantic conventions — replace LLM extraction

### 8.1 OTel Incident / Exception semantic conventions
- https://opentelemetry.io/docs/specs/semconv/exceptions/exceptions-spans/
- Standard attributes: `exception.type`, `exception.message`, `exception.stacktrace`, `event.name`, timestamps — *machine-extractable*, no LLM needed.
- Maps to **[B]**: TTD/TTR/error-IDs come from these attributes via deterministic parsing, eliminating Phase-1 LLM calls for these fields.

### 8.2 OTel Kubernetes & "events" semconv (stable 2025)
- https://opentelemetry.io/docs/specs/semconv/resource/k8s/ + https://opentelemetry.io/docs/specs/semconv/general/events/
- Standardises `k8s.event.reason`, `k8s.event.action`, `k8s.pod.phase` — these *are* the lifecycle bucket labels.
- Maps to **[A]**: bucket by `k8s.event.reason` deterministically; LLM only on residual untagged events.

### 8.3 Langfuse — OTel ingestion & span attributes (2024 GA)
- https://langfuse.com/docs/opentelemetry
- Confirms Langfuse stores OTel attributes verbatim — so the pipeline can query attributes directly instead of asking GPT-4 to parse the span text.
- Maps to **[A]+[B]**: foundation for the deterministic pre-filter recommended throughout this report.

---

## Summary mapping back to the AgentCert pipeline

| Reduction lever                        | Sources              | Expected token cut |
|----------------------------------------|----------------------|--------------------|
| OTel/Litmus attribute pre-extraction   | 8.1–8.3, 4.1–4.2     | 30–50 %            |
| Template/Drain compression of buckets  | 7.1, 7.3–7.5, 1.9    | 2–8×               |
| Cheap-judge cascade (o-mini → GPT-4)   | 1.8, 1.10, 2.4, 5.1–5.3, 3.1–3.6 | 3–10×  |
| Prompt caching + Batch API             | 2.1–2.3              | additional 40–60 % |
| Drop self-consistency in bucketing     | 6.1, 6.3             | linear N×          |
| Stratified evaluator sampling          | 3.1, 3.6             | 5–10× on Phase-2   |

Combining attribute extraction + template compression + cascade routing + caching is the well-supported 5–10× envelope the project is targeting.
