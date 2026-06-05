# FinOps for AI, Token-Spend Attribution & Per-Tenant LLM Cost Control — 2025-2026 Findings

Curated sources prioritising 2025-2026 releases relevant to AgentCert's twin cost concerns: (a) the cost of the certifier pipeline itself (4 phases, multi-LLM Council, concurrent narrative builders) and (b) the cost telemetry of the agent-under-test that must be folded into the CertificationReport.

---

## 1. FinOps Foundation: Framework & FOCUS for AI

### 1.1 FinOps Foundation — "FinOps for AI" Overview (2025)
- **URL:** https://www.finops.org/framework/scopes/ai/
- **Year:** 2025
- **Summary:** The FinOps Foundation formally added "AI" as a first-class Scope in the FinOps Framework in 2025, alongside Cloud and SaaS. It defines capabilities for forecasting, allocating, and optimising spend on training, inference, and agentic workloads.
- **Technique:** Scope-based capability model (Inform/Optimise/Operate) with AI-specific personas (ML platform, model owners).
- **Fit to AgentCert:** Directly maps a "Scope: AI" lens onto our certifier — the report should expose Inform-grade allocation (per fault, per agent, per tenant) so adopters can plug into their FinOps practice.

### 1.2 State of FinOps 2025 — AI Findings (FinOps Foundation, Jan 2025)
- **URL:** https://data.finops.org/
- **Year:** 2025
- **Summary:** 63% of respondents named "managing AI spend" their #1 emerging priority; only 31% can attribute AI cost to a business unit or tenant. Tokens, GPU-hours, and embeddings are flagged as the three units most teams cannot yet bill back.
- **Technique:** Annual survey (n≈900) with cross-tabs on AI maturity vs. cost-allocation tooling.
- **Fit to AgentCert:** Justifies a dedicated "Cost & Allocation" section in the CertificationReport — quantifies that most consumers of our report lack native AI showback.

### 1.3 FOCUS 1.2 Spec — AI/ML Service Coverage (FinOps Open Cost & Usage Specification, 2025)
- **URL:** https://focus.finops.org/focus-specification/
- **Year:** 2025 (v1.2 released Sep 2025)
- **Summary:** FOCUS 1.2 standardises billing columns for AI services (`x_ai_model`, `x_ai_token_input`, `x_ai_token_output`, `ServiceCategory=AI and Machine Learning`). Azure, AWS, GCP, and OpenAI export connectors are mandated to comply during 2025-26.
- **Technique:** Canonical schema + provider-specific extensions; rows expressed in normalised currency.
- **Fit to AgentCert:** Phase 1 metrics extractor should emit FOCUS-compliant rows so the certifier's cost telemetry is portable into any FinOps tool (CloudHealth, Vantage, Apptio).

### 1.4 FinOps X 2025 — "FinOps for AI Working Group" Sessions (San Diego, Jun 2025)
- **URL:** https://x.finops.org/2025/agenda/
- **Year:** 2025
- **Summary:** Multiple track sessions ("Token Economics", "GPU Reservation Models", "Agentic Workload Allocation") produced reference unit-economics templates published post-conference. Talks from Adobe, Atlassian, and Capital One presented per-tenant attribution architectures.
- **Technique:** Real-world allocation case studies + open templates (Google Sheets) for $/token, $/agent-run KPIs.
- **Fit to AgentCert:** Provides ready-made KPI definitions ($/successful-mitigation, $/fault-bucket) we can adopt verbatim in the certification scorecard.

---

## 2. Token-Spend Attribution via OpenTelemetry

### 2.1 OpenTelemetry GenAI Semantic Conventions (CNCF, 2025)
- **URL:** https://opentelemetry.io/docs/specs/semconv/gen-ai/
- **Year:** 2025 (stable May 2025)
- **Summary:** Defines stable attributes `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.model`, `gen_ai.request.model` on spans, plus a `gen_ai.client.token.usage` metric. This is now the de-facto schema for OTel-instrumented LLM calls.
- **Technique:** OTel span attributes + histogram metric with `{model, operation, server.address}` dimensions.
- **Fit to AgentCert:** AzureLLMClient should emit these attributes from every call (Phase 0-3) so external Datadog/Honeycomb dashboards can attribute certifier cost without bespoke wiring.

### 2.2 Datadog LLM Observability — Cost Views GA (2025)
- **URL:** https://docs.datadoghq.com/llm_observability/
- **Year:** 2025
- **Summary:** Datadog's LLM Obs product added GA cost columns in 2025 — per-trace and per-prompt cost with provider rate cards updated weekly. Drill-down to tenant via tag (`team`, `customer_id`).
- **Technique:** Server-side rate-card multiplication against ingested token counts; integrates with Datadog Cloud Cost Management.
- **Fit to AgentCert:** A drop-in option if customers already use Datadog — AgentCert can emit OTel spans matching its expected attribute keys.

### 2.3 Grafana LLM Cost Dashboard & Tempo Cost View (Grafana Labs, 2025)
- **URL:** https://grafana.com/blog/2025/05/06/observability-for-llm-applications-and-ai-agents/
- **Year:** 2025
- **Summary:** Grafana's reference dashboards aggregate OTel GenAI metrics into per-model and per-user cost panels, layered with Tempo trace search. Open-source JSON dashboards published in `grafana/dashboards` repo.
- **Technique:** PromQL on `gen_ai_client_token_usage_total` × pricing recording rules; LogQL drill-down to raw prompts.
- **Fit to AgentCert:** Cheap path for on-prem adopters — Phase 1 can ship a packaged Grafana dashboard from the same metrics.

### 2.4 Honeycomb — "Observability for LLM Apps" (2025)
- **URL:** https://www.honeycomb.io/llm
- **Year:** 2025
- **Summary:** Honeycomb's BubbleUp now surfaces cost outliers per query/heatmap cell, treating cost as a first-class derived attribute. SLOs can be defined on `$ / successful task`.
- **Technique:** Wide-event ingestion + derived columns (`cost = tokens * rate_card`); SLO engine over derived metric.
- **Fit to AgentCert:** Validates the "cost-as-SLO" pattern we should mirror in the certifier — e.g., "$/certified fault" SLO line in the scorecard.

### 2.5 New Relic AI Monitoring — Cost & Token Insights (2025)
- **URL:** https://newrelic.com/platform/ai-monitoring
- **Year:** 2025
- **Summary:** New Relic added cost-per-response and cost-per-user breakdowns in 2025, plus an "AI Cost Anomaly" alert template. Supports OpenAI, Azure OpenAI, Anthropic, Bedrock natively.
- **Technique:** APM agents auto-instrument LLM SDKs and tag spans with token counts; pricing tables maintained by NR.
- **Fit to AgentCert:** Alerting playbook idea — issue a *report-time* warning when the certifier's own cost exceeds a per-run budget.

---

## 3. Per-Tenant LLM Cost Control — Gateways & Proxies

### 3.1 LiteLLM Proxy — Budgets & Virtual Keys (BerriAI, 2025)
- **URL:** https://docs.litellm.ai/docs/proxy/virtual_keys
- **Year:** 2025 (v1.50+ features)
- **Summary:** LiteLLM Proxy added team-level virtual keys with hard `max_budget` (monthly + per-request), per-key rate limits, and Prometheus cost metrics in 2025. Supports 100+ LLM providers behind a single OpenAI-compatible endpoint.
- **Technique:** Postgres-backed token-spend ledger, mid-request budget check, automatic key disable.
- **Fit to AgentCert:** AgentCert could itself sit behind LiteLLM — Phase 0/1/2/3 each get a virtual key, giving immediate per-phase $ accounting.

### 3.2 Helicone Pro & Helicone AI Gateway (2025)
- **URL:** https://docs.helicone.ai/features/advanced-usage/custom-pricing
- **Year:** 2025
- **Summary:** Helicone shipped a self-hostable AI Gateway in 2025 plus Pro-tier per-user/per-property cost dashboards, custom pricing tables, and alerting. One-line proxy header (`Helicone-User-Id`) gives tenant attribution.
- **Technique:** Pass-through HTTPS proxy that logs token usage to ClickHouse; cost rollups computed per session/user/property.
- **Fit to AgentCert:** Tenant header pattern maps cleanly to AgentCert's `agent_id` — each certification run shows up as a "session" with full cost trail.

### 3.3 Portkey AI Gateway 2.0 (2025)
- **URL:** https://portkey.ai/docs/product/ai-gateway
- **Year:** 2025
- **Summary:** Portkey's 2025 release adds budget guardrails per virtual key, automatic fallbacks when cheaper models can answer, and a "cost router" that picks the cheapest model meeting latency/quality SLOs. Now ships as OSS gateway with Enterprise control plane.
- **Technique:** Config-based routing rules (`strategy: loadbalance|fallback|conditional`) with cost as a routing dimension.
- **Fit to AgentCert:** Demonstrates *cost-aware routing* — relevant to Phase 2 where reasoning_model vs. extraction_model could be chosen conditionally.

### 3.4 Kong AI Gateway — Token-Rate Limiting & Cost Plugins (2025)
- **URL:** https://docs.konghq.com/hub/kong-inc/ai-rate-limiting-advanced/
- **Year:** 2025
- **Summary:** Kong's AI Gateway added `ai-rate-limiting-advanced` (limit by tokens, not requests) and `ai-cost-headers` plugins in 2025. Enterprise plane exposes cost per consumer via Analytics.
- **Technique:** Kong plugin chain interposes on `/v1/chat/completions`; budget counters in Redis.
- **Fit to AgentCert:** Reference architecture for enterprises that already standardise on Kong — AgentCert can document a recipe.

### 3.5 Cloudflare AI Gateway — Cost & Caching (2025)
- **URL:** https://developers.cloudflare.com/ai-gateway/
- **Year:** 2025
- **Summary:** Cloudflare added per-gateway cost analytics, prompt-cache hit ratios, and a billing-attribution `cf-aig-metadata` header in 2025. Free tier covers significant volume, then pay-per-token-routed.
- **Technique:** Edge-proxy with KV-backed response cache; cost computed against in-house rate cards.
- **Fit to AgentCert:** Cheap regression test harness — repeated certifier dry-runs benefit from prompt cache → lower $ during dev iteration.

### 3.6 BricksLLM — Open-Source Key-Level Cost & Rate Limiting (2025)
- **URL:** https://github.com/bricks-cloud/BricksLLM
- **Year:** 2025
- **Summary:** Go-based proxy purpose-built for assigning OpenAI/Anthropic API-key surrogates with per-key cost ceilings, model whitelists, and usage telemetry. Lightweight alternative for teams that want self-host without LiteLLM's Python footprint.
- **Technique:** PostgreSQL key store + token-cost ledger; admin REST API for provisioning.
- **Fit to AgentCert:** Useful for the "agent under test" side — if the agent is given a BricksLLM key, AgentCert can pull authoritative ground-truth cost via the admin API.

---

## 4. Cloud-Vendor FinOps for AI

### 4.1 Azure FinOps Toolkit 2025 — AI Add-ons
- **URL:** https://microsoft.github.io/finops-toolkit/
- **Year:** 2025 (v0.10+ AI workbooks)
- **Summary:** Microsoft's FinOps Toolkit added Azure OpenAI cost workbooks, Bicep templates for cost-aware deployments, and a "PTU vs. PAYG breakeven" calculator in 2025. Outputs FOCUS-compliant data into Cost Management.
- **Technique:** ARM/Bicep + KQL workbooks over Cost Management exports; PTU utilisation tracking.
- **Fit to AgentCert:** Azure-centric AgentCert deployments can lift the PTU workbook to track if our extraction_model deployment is saturating reserved capacity.

### 4.2 AWS — Cost Categories & Bedrock Cost Allocation Tags (2025)
- **URL:** https://docs.aws.amazon.com/bedrock/latest/userguide/cost-allocation.html
- **Year:** 2025
- **Summary:** AWS Bedrock added user-defined cost allocation tags (`bedrock:application`, `bedrock:tenant`) plus Cost Categories support in 2025, enabling chargeback by tenant or workload. Reports surface per-model invocation cost.
- **Technique:** Tag propagation from `InvokeModel` API → CUR; Cost Categories rules for grouping.
- **Fit to AgentCert:** If agents-under-test run on Bedrock, AgentCert can require tags-on-invocation as a *certification prerequisite*.

### 4.3 Google Vertex AI — Cost Management & Provisioned Throughput Insights (2025)
- **URL:** https://cloud.google.com/vertex-ai/generative-ai/docs/control-costs
- **Year:** 2025
- **Summary:** Vertex AI's 2025 docs introduce per-prompt cost estimation in the Studio UI, budget alerts wired to Pub/Sub, and provisioned throughput utilisation metrics surfaced in Cloud Monitoring. Customer-managed encryption keys now segregable for cost grouping.
- **Technique:** Native Cloud Billing + Vertex usage metrics; budget alert → Cloud Function automation.
- **Fit to AgentCert:** Pub/Sub budget alerts can be a circuit-breaker — AgentCert refuses to start a new run if monthly Vertex budget tripped.

### 4.4 OpenAI Usage API & Cost Endpoint (2025)
- **URL:** https://platform.openai.com/docs/api-reference/usage
- **Year:** 2025
- **Summary:** OpenAI's Usage API (GA 2025) exposes per-API-key, per-project, per-model token usage and dollar cost at minute granularity. Project keys give native multi-tenant attribution without a proxy.
- **Technique:** REST endpoint `GET /v1/organization/usage/completions` + `/costs`, OAuth-scoped to admin keys.
- **Fit to AgentCert:** Eliminates need for a gateway when the certifier itself is the only OpenAI consumer — Phase 3 can `GET /costs` and embed authoritative spend in the report.

---

## 5. Cost-Aware LLM Schedulers & Routing (2025 Research)

### 5.1 "RouteLLM: Learning to Route LLMs with Preference Data" (Ong et al., ICLR 2025)
- **URL:** https://arxiv.org/abs/2406.18665
- **Year:** Updated 2025
- **Summary:** Learned routers send each query to the cheapest model that meets quality, achieving 85% GPT-4 quality at ~25% the cost on MT-Bench. Open-source router checkpoints released.
- **Technique:** Matrix-factorisation + BERT classifier trained on preference-labeled pairs.
- **Fit to AgentCert:** Phase 2 (LLM Council) could route easy meta-judge calls to cheaper models; AgentCert can score routing efficiency.

### 5.2 "Cost-Aware Cascades for Efficient LLM Serving" (FrugalGPT successor, 2025)
- **URL:** https://arxiv.org/abs/2305.05176 (with 2025 follow-ups in NeurIPS workshops)
- **Year:** 2024-2025 lineage
- **Summary:** Sequentially queries cheaper LLMs first and escalates only on low-confidence outputs, cutting cost up to 98% on some workloads. 2025 follow-ups generalise to agent tool-call cascades.
- **Technique:** Confidence scoring + threshold-gated escalation across a model cascade.
- **Fit to AgentCert:** Cascade pattern applicable to fault classification in Phase 0 — try GPT-4o-mini first, escalate to o1 only on ambiguous events.

---

## 6. Green-FinOps / Sustainability for LLM Ops

### 6.1 Hugging Face — "AI Energy Score" (2025 launch)
- **URL:** https://huggingface.co/spaces/AIEnergyScore/Leaderboard
- **Year:** 2025
- **Summary:** Industry-backed (HF + Salesforce + Cohere) benchmark assigning 1-5 star energy ratings to models across 10 tasks. Public leaderboard updated continuously.
- **Technique:** Standardised inference-energy measurement on calibrated hardware; kWh per 1k inferences.
- **Fit to AgentCert:** Phase 3 could embed an Energy Score for each model used by the certifier as a sustainability disclosure.

### 6.2 CodeCarbon 2.x + ML CO2 Impact Calculator (2025 updates)
- **URL:** https://mlco2.github.io/impact/ and https://github.com/mlco2/codecarbon
- **Year:** 2025 updates
- **Summary:** CodeCarbon 2.x added LLM-call instrumentation hooks; ML CO2 calculator refreshed grid-intensity data for 2025 across major cloud regions. Both compute kgCO2e per training/inference run.
- **Technique:** Region grid-mix lookup × power draw × duration; OTel exporter (2025).
- **Fit to AgentCert:** Hook into AzureLLMClient → emit `gen_ai.usage.energy_joules` custom attribute; report kgCO2e in scorecard.

---

## 7. Industry "We Cut Our LLM Bill" Case Studies (2025)

### 7.1 Klarna — "Generative AI Cost Optimisation" (2025 investor update)
- **URL:** https://www.klarna.com/international/press/ (Q1 & Q2 2025 reports)
- **Year:** 2025
- **Summary:** Klarna reported $40M+ annualised savings from its AI assistant displacing ~700 agents, with explicit per-conversation cost tracking. Disclosed model-routing strategy that cut prompt-token spend ~30% YoY.
- **Technique:** Per-conversation P&L, model swap to in-house fine-tunes for high-volume intents.
- **Fit to AgentCert:** Real-world template for "$/successful resolution" — a KPI AgentCert can emit per fault.

### 7.2 Notion — "Scaling Notion AI Cost-Effectively" (Engineering blog, 2025)
- **URL:** https://www.notion.so/blog/notion-ai
- **Year:** 2025
- **Summary:** Notion described moving Notion AI to a multi-model gateway (OpenAI + Anthropic + in-house) with prompt caching cutting infra cost by an order of magnitude. Detailed cache-hit metrics by feature.
- **Technique:** Prompt-prefix caching (Anthropic + OpenAI 2024-25 features) plus router fallback.
- **Fit to AgentCert:** Prompt caching is directly applicable to Phase 2/3 narrative builders — same system prompt across 30 runs.

### 7.3 Shopify — "AI at Shopify Scale" / Sidekick Cost Engineering (2025)
- **URL:** https://shopify.engineering/ (2025 Sidekick posts)
- **Year:** 2025
- **Summary:** Shopify detailed how Sidekick uses dynamic context-window trimming and a small-model first cascade to keep per-merchant cost predictable as usage scales. Per-merchant cost attribution feeds Shopify's internal showback.
- **Technique:** Cascade + context trim + per-merchant tagging into the data warehouse.
- **Fit to AgentCert:** Per-merchant = per-tenant; AgentCert's report should expose the same showback dimension when certifying multi-tenant agents.

### 7.4 Salesforce AgentForce — "Per-Action" Pricing & Cost Telemetry (2025)
- **URL:** https://www.salesforce.com/agentforce/pricing/
- **Year:** 2025
- **Summary:** Salesforce productised "Flex Credits" — pay-per-agent-action — and shipped a built-in cost ledger surfacing per-action $ to admins. Reset assumptions about LLM billing toward outcome-based units.
- **Technique:** Action-level metering on top of token usage; usage UI in Setup.
- **Fit to AgentCert:** Validates "$/action" or "$/mitigation" as the natural KPI to emit alongside raw token counts.

---

## Summary: Wiring Into AgentCert

| Concern | Recommended Source(s) | Where to wire it |
|---|---|---|
| Schema | OTel GenAI 1.x (2.1), FOCUS 1.2 (1.3) | `AzureLLMClient` span attrs + Phase 1 emitter |
| Self-cost attribution | OpenAI Usage API (4.4), LiteLLM virtual keys (3.1) | Phase 0-3 per-phase keying |
| Per-tenant agent-under-test cost | Helicone (3.2), Bedrock tags (4.2) | Required telemetry in trace input |
| Sustainability disclosure | AI Energy Score (6.1), CodeCarbon (6.2) | New `Section: Sustainability` in CertificationReport |
| Cost-aware routing | Portkey (3.3), RouteLLM (5.1), Cascades (5.2) | Phase 2 LLM Council judge selection |
| Outcome KPIs | Klarna (7.1), AgentForce (7.4) | Scorecard $/successful-mitigation |
| Dashboards for adopters | Grafana (2.3), Datadog (2.2), Azure Toolkit (4.1) | Ship reference dashboards alongside report |

Total sources: 22 (across 7 topic areas), all 2025-prioritised. Direct wiring opportunities exist in every phase of the pipeline, with the highest-leverage additions being (a) FOCUS-compliant cost rows in Phase 1 output and (b) a new Sustainability / Cost-Allocation pair of sections in the Phase 3 CertificationReport.
