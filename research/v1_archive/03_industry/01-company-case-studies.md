# Industry Case Studies — LLM Cost Optimization at Scale (2023–2026)

> 20 public engineering sources from Uber, LinkedIn, Pinterest, Airbnb, DoorDash, Meta, Anthropic/Honeycomb, GitHub/Microsoft, AWS, Google, Datadog, New Relic, JPMorgan, Bloomberg. Compiled for the AgentCert 4-phase pipeline.

---

## Uber

### 1. GenAI Gateway
- **URL:** https://www.uber.com/blog/genai-gateway/ (2024)
- **Pattern:** Single OpenAI-compatible gateway fronting OpenAI, Vertex (PaLM2), and self-hosted Llama-2-70B. Centralizes budgeting, PII redaction, per-team quotas.
- **Numbers:** 16M queries/month, peak 25 QPS, ~30 teams, 60+ apps.
- **Applies to AgentCert:** Replace direct `AzureLLMClient` calls with a routing layer. Phase-0 bucketing (cheap, structured) → Haiku-class; Phase-3 narrative → flagship. Aligns with `configs.json` `extraction_model` vs `reasoning_model` split.

### 2. QueryGPT (Text-to-SQL)
- **URL:** https://www.uber.com/blog/query-gpt/ (2024)
- **Pattern:** RAG schema-selection trims prompt; fine-tuned smaller model for the SQL hop. Two-stage retrieve → generate.
- **Applies:** Same two-hop shape as fault-bucket retrieval → metrics extraction.

### 3. Michelangelo LLM Platform
- **URL:** https://www.uber.com/blog/from-predictive-to-generative-ai/ (2024)
- **Pattern:** Explicit hybrid policy — "external models for general reasoning, fine-tuned in-house open-source for Uber-centric tasks at a fraction of cost and lower latency." DeepSpeed + Ray + Triton.
- **Applies:** Motivates routing deterministic Phase-1 metric extraction to a fine-tuned small model.

---

## LinkedIn

### 4. Musings on Building a GenAI Product (Hiring Assistant)
- **URL:** https://www.linkedin.com/blog/engineering/generative-ai/musings-on-building-a-generative-ai-product (Apr 2024)
- **Pattern:** Agent-based routing (sub-agents); Embedding-Based Retrieval as a prompt-cache substitute; async streaming pipelines; planned migration of "simpler tasks to in-house fine-tuned models."
- **Numbers:** Quality jumped 80% → 95% only after months. Most cost came from the last 15%.
- **Applies:** Mirrors AgentCert's 5 concurrent narrative builders. Embedding-cache directly applicable to fault-pattern lookups across runs.

---

## Pinterest

### 5. Text-to-SQL at Pinterest
- **URL:** https://medium.com/pinterest-engineering/how-we-built-text-to-sql-at-pinterest-30bad30dabff (2024)
- **Pattern:** Two-stage retrieval (OpenSearch vector index → LLM re-rank). Schema/column pruning shrinks prompt.
- **Numbers:** First-shot SQL acceptance 20% → 40%+; table-search hit rate 40% → 90%; 35% task-speed improvement.
- **Applies:** Schema/column pruning = event-field pruning before bucketing LLM.

---

## Airbnb

### 6. Automation Platform v2
- **URL:** https://medium.com/airbnb-engineering/automation-platform-v2-improving-conversational-ai-at-airbnb-d86c9386e0cb (2024)
- **Pattern:** CoT tool-using agent + parallel guardrail pipeline (not serial) to avoid double-latency.
- **Applies:** Validates Phase-3 concurrent narrative builders via `asyncio.gather`.

---

## DoorDash

### 7. LLM-Based Support Automation
- **URL:** https://careersatdoordash.com/blog/large-language-modules-based-doordash-support-automation/ (Feb 2024)
- **Pattern:** RAG knowledge-base retrieval, structured/JSON output to reduce retries, hierarchical intent classification (cheap classifier → expensive resolver).
- **Applies:** Two-tier classification matches "Phase 0 bucketing (cheap) → Phase 1 extraction (richer)" exactly.

---

## Meta

### 8. Llama 3 Inference Efficiency
- **URL:** https://ai.meta.com/blog/meta-llama-3/ (2024)
- **Pattern:** 128K-token vocab → **~15% fewer tokens vs Llama 2** on the same text; GQA on 8B and 70B.
- **Applies:** Tokenizer choice itself is a cost lever — measure tokens/event in Phase 1 per-model.

### 9. Self-Taught Evaluator (Meta FAIR, 2024)
- **URL:** https://arxiv.org/abs/2408.02666
- **Pattern:** Train a Llama-3-70B reward judge with synthetic preferences. Matches GPT-4 on RewardBench.
- **Applies:** Direct template for replacing GPT-4 LLM-Council judges in Phase 2 with a distilled in-house judge.

---

## Anthropic / Honeycomb

### 10. Honeycomb Query Assistant
- **URL:** https://www.honeycomb.io/blog/introducing-query-assistant (2023, updated)
- **Pattern:** Schema-aware prompt with only the columns observed in the user's last queries. Single-shot, no agentic loop.
- **Applies:** Minimal-context discipline AgentCert needs in Phase-0 prompts.

### 11. Anthropic Prompt Caching
- **URL:** https://www.anthropic.com/news/prompt-caching (Aug 2024)
- **Pattern:** Cache reusable prefixes (system prompts, few-shots, large docs). **Up to 90% cost reduction and 85% latency reduction.**
- **Applies:** Phase-0/1 prompts share a fixed system + schema header — perfect cache prefix.

---

## Microsoft / GitHub

### 12. Multi-Model Choice in Copilot
- **URL:** https://github.blog/news-insights/product-news/bringing-developer-choice-to-copilot-with-anthropics-claude-3-5-sonnet-googles-gemini-1-5-pro-and-openais-o1-preview/ (Oct 2024)
- **Pattern:** Per-request routing across Claude 3.5 Sonnet, Gemini 1.5 Pro, o1-preview.
- **Applies:** Production-validated `extraction_model` vs `reasoning_model` split.

### 13. AutoGen Multi-Agent Evaluation
- **URL:** https://www.microsoft.com/en-us/research/blog/autogen-enabling-next-generation-large-language-model-applications/ (2023)
- **Pattern:** Cheap "user-proxy" agent loops with a flagship "assistant" — minimizes flagship calls.
- **Applies:** Mirrors AgentCert's deterministic-stats + LLM-Council split.

---

## AWS

### 14. Bedrock Model Distillation (GA)
- **URL:** https://aws.amazon.com/blogs/aws/build-your-own-distilled-models-with-amazon-bedrock-model-distillation/ (Dec 2024)
- **Pattern:** Distill teacher (Llama 3.1 405B, Claude) into a student using your historical traffic. **Up to 500% faster, 75% cheaper** with <2% accuracy delta.
- **Applies:** Phase-1 outputs become training data for a distilled extractor — biggest long-term lever.

### 15. Amazon Q Developer & CloudWatch Logs Anomaly Detection
- **URL:** https://aws.amazon.com/blogs/aws/use-natural-language-to-query-amazon-cloudwatch-logs-and-metrics-preview/
- **Pattern:** LLM invoked only on anomaly-detected log windows, not full streams. Pre-filter classifier reduces tokens by orders of magnitude.
- **Applies:** Bucketing only on "interesting" event windows.

---

## Google

### 16. Gemini Flash Routing
- **URL:** https://cloud.google.com/blog/products/ai-machine-learning/gemini-1-5-flash-now-generally-available (2024)
- **Pattern:** Flash at ~1/10 cost of Pro; Flash first, escalate to Pro only on low-confidence or schema-violation.
- **Applies:** Cascade pattern for Phase-0 bucketing.

---

## Observability Vendors

### 17. Datadog Bits AI (Incident Management)
- **URL:** https://www.datadoghq.com/blog/datadog-bits-ai/ (2023, updated 2024)
- **Pattern:** RAG over customer's traces/logs/metrics; summarization only at incident-declaration, not continuously. Templated structured summaries.
- **Applies:** Event-triggered (per-fault) summarization model — exactly AgentCert Phase 3.

### 18. New Relic AI Monitoring
- **URL:** https://newrelic.com/blog/how-to-relic/ai-monitoring (2024)
- **Pattern:** Per-LLM-call telemetry capture (tokens, latency, cost). Surfaces model-routing recommendations from observed data.
- **Applies:** Extend Phase-1 metrics to per-call cost → feedback loop to tune routing.

---

## Finance

### 19. JPMorgan LLM Suite
- **URL:** https://www.ft.com/content/b14df3ec-3175-451f-86ee-c8d36b8ec48d (FT, Aug 2024)
- **Pattern:** Internal portal fronting OpenAI/Anthropic/Google behind compliance + budgeting. Centralizes prompt logging for evaluation.
- **Applies:** Reinforces gateway/router pattern as enterprise default.

### 20. BloombergGPT (50B finance LLM)
- **URL:** https://www.bloomberg.com/company/press/bloomberggpt-50-billion-parameter-llm-tuned-finance/ (2023)
- **Pattern:** Domain pre-trained 50B model — cheaper internal inference vs GPT-4 on finance-classification.
- **Applies:** Long-term option: a domain-pretrained "fault-trace" model would dominate cost-wise.

---

## Cross-Cutting Takeaways

1. **Gateway + routing is universal** (Uber, JPMorgan, LinkedIn, Copilot) — generalize `AzureLLMClient` to a routing layer.
2. **Cascade cheap → expensive** (Gemini Flash→Pro, AutoGen) — Phase 0 defaults to Haiku/Flash-tier; escalate only on low-confidence.
3. **Prompt caching of fixed prefixes** (Anthropic: 90% cost / 85% latency) — biggest immediate win.
4. **Trim the prompt with RAG/schema-pruning** (Pinterest, Honeycomb, QueryGPT) — don't dump the full trace; pre-filter to relevant events per bucket.
5. **Distill once you have data** (Bedrock 75% cheaper, Meta Self-Taught Evaluator) — existing Phase-1 outputs = training data for a small in-house extractor.
6. **Parallel guardrails, not serial** (Airbnb) — already used in Phase 3; keep it.
7. **Per-call cost telemetry** (New Relic, Michelangelo) — add cost to metrics-extractor output.
