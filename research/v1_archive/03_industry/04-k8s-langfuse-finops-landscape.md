# How Production Systems Parse K8s/OTel/Langfuse Data With LLMs

A landscape review for AgentCert's pipeline design. AgentCert ingests Langfuse
traces from AI agents acting on Kubernetes clusters under Litmus/Chaos-Mesh
fault injection and uses GPT-4 to (Phase 0) bucket interleaved events per
fault, (Phase 1) extract per-fault metrics, (Phase 2) aggregate over N runs,
and (Phase 3) emit a 12-section certification. This document maps how others
solve the same primitives.

---

## 1. ITOps / K8s Log + Trace Parsing with LLMs

### 1.1 K8sGPT (CNCF Sandbox, OSS)
- URL: https://github.com/k8sgpt-ai/k8sgpt
- Docs: https://docs.k8sgpt.ai/
- Year: 2023 onwards
- What it does: Built-in "analyzers" (Pod, Deployment, Service, PVC, HPA,
  NetworkPolicy, etc.) scan cluster state and emit structured `Result` objects
  with `Kind`, `Name`, `Error[]`. With `--explain` the failures are
  anonymized, joined with the analyzer label, and sent to an LLM backend
  (OpenAI/Azure/Bedrock/Gemini/Ollama) with a fixed prompt that asks for
  Markdown "Error" + "Solution" sections.
- Maps to AgentCert: **Phase 0 (event-to-fault classification)** and the
  output-schema discipline mirrors Phase 1 metric extractors. The
  `gpt.go::default_prompt` ("Simplify the following Kubernetes error message
  ... return a concise explanation and a step-by-step solution") is the
  canonical "minimal classifier prompt" pattern.

### 1.2 HolmesGPT (Robusta)
- URL: https://github.com/robusta-dev/holmesgpt
- Year: 2024
- What it does: Agentic ReAct loop. Exposes ~30 tools (`kubectl_describe`,
  `kubectl_logs`, `kubectl_previous_logs`, `kubectl_events`, Prometheus query,
  Grafana, Loki, Tempo, OpsGenie, runbook fetch). Enforces server-side
  filtering, per-tool memory limits, streaming-to-disk, and "automatic output
  budgeting" so a single investigation cannot exhaust the LLM context. Uses
  OpenAI tool-calling format.
- Maps to AgentCert: **Phase 0** if AgentCert ever moves from "feed entire
  trace" to "let LLM pull only the slices it needs", and **Phase 3** for the
  recommendation/runbook narrative. Output budgeting is directly applicable
  to Phase 1 prompt sizing.

### 1.3 Komodor Klaudia / Komodor AI
- URL: https://komodor.com/klaudia/
- Year: 2024
- What it does: Commercial agentic RCA over k8s timelines. Klaudia correlates
  deploys, config changes, k8s events, and pod logs into an "incident
  timeline" with an LLM-generated root-cause hypothesis and "next best
  action." Their public blog stresses *deterministic timeline construction
  before LLM summarisation*—exactly AgentCert's split between Phase 0/1
  (deterministic) and Phase 3 (LLM narrative).
- Maps to AgentCert: **Phase 0 bucket + Phase 2 narrative aggregation**.

### 1.4 KubePilot / KubeBuddy / KubeAI
- URLs: https://github.com/cloud-pi/kubepilot, https://kubebuddy.io,
  https://github.com/substratusai/kubeai
- Year: 2024
- What they do: Smaller OSS attempts at "LLM front-ends to kubectl". Same
  pattern as K8sGPT but with chat UX. KubeAI focuses on serving models in
  cluster (closer to KAITO than to analysis).
- Maps to AgentCert: limited — useful only as a reference for Phase 0 input
  shaping (kubectl-output → LLM message).

### 1.5 KAITO (Microsoft / CNCF Sandbox)
- URL: https://github.com/kaito-project/kaito
- Year: 2024
- What it does: Kubernetes operator for *serving* LLMs (vLLM CRDs, GPU
  autoscale, RAG engine). Not an analyzer, but relevant because AgentCert's
  Azure OpenAI calls could be replaced with a KAITO-served model for the
  Phase 1 extraction model when running fully in-cluster.
- Maps to AgentCert: infrastructure substrate, not a pipeline pattern.

### 1.6 Datadog Bits AI for Incident Management
- URL: https://www.datadoghq.com/blog/bits-ai-incident-management/
- Year: 2024
- What it does: Summarises a Datadog incident from its trace, log, and metric
  artifacts; auto-drafts the post-mortem; suggests responders. The
  summarisation prompt explicitly receives a *pre-filtered* set of spans
  (high-error-rate services + top-N error logs) — Datadog does Phase 0
  outside the LLM.
- Maps to AgentCert: **Phase 3** narrative builders (limitations, recs).

### 1.7 Dynatrace Davis CoPilot
- URL: https://www.dynatrace.com/platform/artificial-intelligence/davis-copilot/
- Year: 2024
- What it does: Layers an LLM on top of Davis (causal AI). Davis still does
  the deterministic anomaly-detection and topology-aware RCA; the LLM only
  generates DQL queries and natural-language explanations. This *causal-first,
  LLM-second* split is a strong validation of AgentCert's architecture.
- Maps to AgentCert: **Phase 2 aggregation** (deterministic) → **Phase 3**
  (LLM narrative).

### 1.8 New Relic AI / Grok
- URL: https://newrelic.com/platform/new-relic-ai
- Year: 2024
- What it does: NRQL generation from natural language plus log/trace
  summarisation. Notable for using log *patterns* (already
  clustered/templated) as the LLM input, not raw lines — drastically reducing
  token spend.
- Maps to AgentCert: directly inspires a Phase 0 optimisation (cluster
  similar events into templates before LLM sees them).

### 1.9 Sysdig Sage
- URL: https://sysdig.com/products/sage/
- Year: 2024
- What it does: Conversational agent over runtime security + k8s findings.
  Sage exposes "investigation" actions that pre-fetch only the relevant
  process tree / syscall slice for the LLM.
- Maps to AgentCert: **Phase 0** scope-narrowing.

### 1.10 Elastic AI Assistant for Observability
- URL: https://www.elastic.co/observability/ai-assistant
- Year: 2023 onwards
- What it does: ESQL/KQL generation plus RAG over runbooks. Uses Elastic's
  log clustering (Categorisation ML job) as a pre-LLM compression step.
- Maps to AgentCert: **Phase 0/1** — analogous to bucketing.

### 1.11 Grafana Loki + Sift + Asserts
- URLs: https://grafana.com/docs/grafana-cloud/alerting-and-irm/sift/,
  https://grafana.com/products/cloud/asserts/
- Year: 2024
- What it does: Sift auto-investigates an alert by running a fixed set of
  "checks" (noisy-neighbour, OOM, deploy-correlation) — the LLM only writes
  the *summary* once the deterministic checks have collected evidence. This
  is essentially the AgentCert split.
- Maps to AgentCert: **Phase 2 → 3** boundary.

### 1.12 Splunk AI Assistant for SPL
- URL: https://www.splunk.com/en_us/products/splunk-ai-assistant.html
- Year: 2024
- What it does: NL → SPL with citation back to Splunk docs. Adds an
  "incident summary" agent that pre-aggregates by `eventtype` before LLM.
- Maps to AgentCert: NL-query generation is out of scope, but the
  pre-aggregation pattern is reusable in Phase 2.

### 1.13 Vector.dev with LLM remap functions
- URL: https://vector.dev/docs/reference/configuration/transforms/remap/
- Year: 2024
- What it does: VRL transforms can call out to an LLM in-flight to classify
  or redact log lines before they reach storage. Pattern: keep LLM at the
  *edge of the data plane* not inside the analytical query.
- Maps to AgentCert: could front-load Phase 0 by pre-tagging events at
  Langfuse ingestion time.

---

## 2. Langfuse / OTel Trace Parsing for LLM Apps

### 2.1 Langfuse LLM-as-a-Judge
- URL: https://langfuse.com/docs/scores/model-based-evals
- Year: 2024
- What it does: User authors a Jinja-style prompt with
  `{{input}} {{output}} {{ground_truth}}` placeholders; Langfuse maps trace
  fields (with JSONPath, e.g. `$.choices[0].message.content`) into the
  variables, runs the judge model, and writes a `Score` back to the trace.
  Supports k-judge majority via "Evaluator Library".
- Maps to AgentCert: **Phase 2 Council** is conceptually the same flow but
  with multiple judges + meta-judge instead of a single score.

### 2.2 Langfuse Datasets + Experiments
- URL: https://langfuse.com/docs/datasets
- Year: 2024
- What it does: Promote real traces into a versioned dataset, then re-run
  experiments and diff scores. AgentCert's "30 runs per fault" set is
  morally a Langfuse Dataset; the certification report is the experiment
  artifact.
- Maps to AgentCert: **Phase 2 aggregation** sample design.

### 2.3 OpenTelemetry GenAI Semantic Conventions
- URL: https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/
- Year: 2024-2026 (Experimental → Stable migration in 2026)
- The attributes AgentCert should be reading **directly off the span** instead
  of asking GPT-4 to re-extract:

  | Attribute | Use in AgentCert |
  |---|---|
  | `gen_ai.system` | model family (e.g. `azure.ai.openai`) → Phase 1 metadata |
  | `gen_ai.request.model` | requested deployment → Phase 1 |
  | `gen_ai.response.model` | actual model used → Phase 1 |
  | `gen_ai.response.id` | dedup / replay key |
  | `gen_ai.response.finish_reasons[]` | "stop", "tool_calls", "length" → fault correlation |
  | `gen_ai.usage.input_tokens` | Phase 1 token metric (no LLM extraction) |
  | `gen_ai.usage.output_tokens` | Phase 1 token metric |
  | `gen_ai.usage.reasoning.output_tokens` | Phase 1 reasoning-token metric for o-series |
  | `gen_ai.usage.cache_read.input_tokens` | Phase 1 FinOps signal |
  | `gen_ai.usage.cache_creation.input_tokens` | Phase 1 FinOps signal |
  | `gen_ai.response.time_to_first_chunk` | Phase 1 TTFB metric |
  | `gen_ai.operation.name` | `chat`, `embedding`, `text_completion` |

  Action item: replace any LLM-prompt step that today re-derives these from
  the message body with a deterministic span-attribute reader.

### 2.4 OTel Collector pre-filter pipelines
- URL: https://opentelemetry.io/docs/collector/configuration/
- Year: 2024
- What it does: `tail_sampling`, `filter`, `transform` processors strip
  noisy spans (health checks, debug spans) before export. Production LLM
  pipelines (Honeycomb, Grafana Tempo) recommend dropping >80 % of spans
  before any AI step.
- Maps to AgentCert: pre-Phase-0 cost reduction.

### 2.5 LangSmith trace + dataset replay
- URL: https://docs.smith.langchain.com/evaluation
- Year: 2024
- What it does: Re-runs a prompt against historical traces, scores with
  configurable evaluators (including custom LLM-judges). The "trajectory
  evaluator" matches AgentCert's notion of judging a *sequence* of agent
  actions per fault.
- Maps to AgentCert: **Phase 2 Council** pattern reference.

### 2.6 Arize Phoenix
- URL: https://arize.com/docs/phoenix/tracing/llm-traces
- Year: 2024
- What it does: OTLP-native LLM trace store with built-in evaluators
  (Hallucination, QA-Correctness, Toxicity) that expect spans tagged with
  the OTel GenAI conventions.
- Maps to AgentCert: validates the convention-first approach; their eval
  prompts (in `phoenix.evals`) are good templates for Phase 2 judges.

### 2.7 Sentry AI Monitoring
- URL: https://docs.sentry.io/product/insights/ai/
- Year: 2024
- What it does: Surfaces token counts, model, latency, and error rates per
  LLM span. Adds "AI Agent Monitoring" that auto-detects tool calls and
  retries — directly equivalent to AgentCert's fault timeline.
- Maps to AgentCert: **Phase 0 + Phase 1** reference.

### 2.8 W&B Weave
- URL: https://wandb.ai/site/weave
- Year: 2024
- What it does: Trace ingestion with "Scorers" (programmatic + LLM-judge)
  attached to a project. Scorers run async on every trace.
- Maps to AgentCert: **Phase 2** scoring loop.

### 2.9 PostHog LLM observability
- URL: https://posthog.com/docs/ai-engineering
- Year: 2024
- What it does: Captures LLM events as PostHog events (`$ai_generation`)
  with `$ai_input_tokens`, `$ai_output_tokens`, `$ai_total_cost_usd`.
- Maps to AgentCert: alternate schema reference for Phase 1 metric names.

### 2.10 Traceloop OpenLLMetry
- URL: https://github.com/traceloop/openllmetry
- Year: 2023 onwards
- What it does: OTel SDK extensions that auto-instrument OpenAI, Anthropic,
  LangChain, LlamaIndex, vector DBs, and emit OTel GenAI-compliant spans.
  Upstreamed many of the `gen_ai.*` conventions.
- Maps to AgentCert: the *correct* way to source Langfuse spans — if the
  agents under test use OpenLLMetry, Phase 1 collapses to pure attribute
  reads.

### 2.11 Honeycomb on parsing OTel traces with LLMs
- URL: https://www.honeycomb.io/blog/introducing-query-assistant
- Year: 2023
- What it does: Query Assistant turns NL into Honeycomb queries. Their
  engineering blog emphasises "give the LLM the schema, not the data" — a
  pattern AgentCert already uses in Phase 1.
- Maps to AgentCert: prompt-design reference.

---

## 3. FinOps / Token-Spend Attribution

### 3.1 OpenCost LLM allocation
- URL: https://www.opencost.io/docs/integrations/llm
- Year: 2024
- What it does: Tags LLM API spend back to k8s namespace/workload via
  HTTP-proxy or LiteLLM middleware that writes Prometheus counters
  (`llm_input_tokens_total`, `llm_output_tokens_total`) labelled with
  `namespace`, `pod`, `model`.
- Maps to AgentCert: **Phase 2 aggregation** of token/cost across runs.

### 3.2 Helicone
- URL: https://docs.helicone.ai
- Year: 2023
- What it does: Drop-in proxy logging every OpenAI/Anthropic request with
  cost, latency, cache hit; dashboards by user/key/feature. Cost is
  computed from a maintained `model-pricing.json` table — exactly the
  determinism AgentCert wants for Phase 1.
- Maps to AgentCert: **Phase 1** cost-computation reference (don't ask the
  LLM how much it cost; multiply tokens by the table).

### 3.3 LangSmith / LangChain cost callbacks
- URL: https://python.langchain.com/docs/how_to/llm_token_usage_tracking/
- Year: 2024
- What it does: `get_openai_callback()` and `UsageMetadataCallbackHandler`
  surface `prompt_tokens / completion_tokens / total_cost_usd` per
  invocation.
- Maps to AgentCert: same as 3.2.

### 3.4 Datadog LLM Observability cost views
- URL: https://docs.datadoghq.com/llm_observability/
- Year: 2024
- What it does: Out-of-the-box cost-per-app / cost-per-feature dashboards
  driven by token counts and a maintained price book.
- Maps to AgentCert: dashboard inspiration for the certification report's
  cost section.

### 3.5 Vantage AI Cost Insights
- URL: https://www.vantage.sh/blog/ai-cost-management
- Year: 2024
- What it does: Cross-cloud LLM spend (Bedrock, Azure OpenAI, Vertex,
  OpenAI direct) normalised to a single ledger.
- Maps to AgentCert: model-pricing normalisation pattern.

### 3.6 AWS Bedrock + Cost Explorer
- URL: https://docs.aws.amazon.com/bedrock/latest/userguide/monitoring-cw.html
- Year: 2024
- What it does: `InputTokenCount` / `OutputTokenCount` CloudWatch metrics
  per model + Cost Allocation Tags.
- Maps to AgentCert: when AgentCert reports on Bedrock-backed agents,
  these metrics are the source of truth, not the trace body.

### 3.7 Azure FinOps Toolkit + OpenAI metrics
- URL: https://microsoft.github.io/finops-toolkit/
- Year: 2024
- What it does: Azure OpenAI emits `ProcessedPromptTokens`,
  `ProcessedCompletionTokens`, `GeneratedTokens`, plus FinOps Toolkit
  KQL queries to allocate to cost-centre tags.
- Maps to AgentCert: Phase 1 deterministic source for Azure OpenAI
  spend; KQL templates reusable for the certification appendix.

### 3.8 FinOps Foundation: FinOps for AI Working Group
- URL: https://www.finops.org/projects/finops-for-ai/
- Year: 2024
- What it does: Maturity model (Crawl/Walk/Run) for tagging, showback,
  budget guardrails on GenAI spend. Defines KPIs: cost-per-1k-tokens,
  cost-per-successful-request, cache-hit-savings.
- Maps to AgentCert: vocabulary for the certification report's cost
  section + reusable KPI definitions for Phase 2.

### 3.9 Anthropic token-usage best practices
- URL: https://docs.anthropic.com/en/docs/build-with-claude/token-counting
  and https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- Year: 2024-2025
- What they emphasise: count tokens client-side before sending;
  cache_creation vs cache_read tokens are billed differently — exactly the
  reason AgentCert must read OTel `gen_ai.usage.cache_*` attributes
  separately.
- Maps to AgentCert: Phase 1 token-metric granularity.

### 3.10 DoorDash / Shopify engineering blogs on LLM cost guardrails
- URLs: https://careersatdoordash.com/blog/large-language-modules-based-doordash-support-automation/,
  https://shopify.engineering/topics/data-science-engineering
- Year: 2024
- What they do: Per-request budget caps, model-routing (cheap-first,
  expensive-fallback), and *per-tenant* cost attribution headers.
- Maps to AgentCert: motivates a Phase 3 recommendation builder that
  surfaces "agents that exceeded $X per fault".

---

## 4. Reusable Prompt Templates

### 4.1 K8sGPT default analyzer prompt (paraphrased from source)
```
Simplify the following Kubernetes error message delimited by triple dashes
written in --- {{ .Language }} ---.
Provide the most possible solution in a step-by-step style in no more than
{{ .MaxLength }} characters.
Write the output in the following format:
Error: {Explain in simple terms}
Solution: {Step-by-step solution}
---
{{ .Input }}
---
```
AgentCert use: Phase 0 single-event classification fallback prompt.

### 4.2 HolmesGPT investigator system prompt (paraphrased)
```
You are a senior SRE. Investigate the alert below.
You have these tools: {tool_schemas}.
Rules:
- Always call a tool before asserting a cause.
- Stop when you can name the offending resource and the trigger.
- Cite the tool output line that supports each claim.
Alert: {alert_json}
```
AgentCert use: Phase 0 if/when AgentCert evolves to tool-calling slicing
of the trace.

### 4.3 Langfuse LLM-as-Judge template
```
You are an expert evaluator. Given the user input, the model output, and the
reference answer, score correctness from 1-5 and explain.

Input: {{input}}
Output: {{output}}
Reference: {{ground_truth}}

Return JSON: {"score": int, "reasoning": str}
```
AgentCert use: Phase 2 Council judge prompt skeleton.

### 4.4 Arize Phoenix Q&A correctness eval
```
You are given a question, an answer, and a reference text. Decide if the
answer is correct.
[QUESTION]: {input}
[REFERENCE]: {reference}
[ANSWER]: {output}
Respond with "correct" or "incorrect" and a one-sentence justification.
```
AgentCert use: meta-judge tie-breaker prompt.

### 4.5 OTel-aware extraction prompt (recommended AgentCert pattern)
```
Below are span attributes already parsed from OTel. DO NOT recompute them.
You are only to summarise the qualitative outcome of this fault.
Attributes: {gen_ai.usage.input_tokens, gen_ai.usage.output_tokens,
gen_ai.response.finish_reasons, ...}
Bucket events: {events_jsonl}
Return JSON conforming to FaultMetricsSchema.
```
AgentCert use: Phase 1 — sharply reduces tokens by forbidding the LLM from
re-extracting numerics.

---

## 5. Concrete Recommendations for AgentCert

1. **Read OTel `gen_ai.*` attributes directly in Phase 1.** Every numeric
   currently derived by GPT-4 (tokens, model, finish reason, TTFB) is
   already on the Langfuse span if the agent under test uses Traceloop /
   OpenLLMetry / Langfuse SDK ≥ 2.30. Replace those extraction prompts
   with a deterministic reader; reserve the LLM for qualitative
   narrative.
2. **Adopt OpenCost / Helicone-style price-table cost calculation** in
   Phase 1 instead of any LLM-side estimation.
3. **Add a pre-Phase-0 filter step** modelled on OTel Collector
   `tail_sampling` + Grafana Sift's "checks-first" pattern, so the LLM
   only sees spans that crossed an error / latency threshold.
4. **Move the Phase 2 Council prompt into the Langfuse Evaluator Library
   format** (`{{input}}`/`{{output}}` placeholders) — buys you replay,
   versioning, and a free UI for prompt iteration.
5. **Cite FinOps Foundation "FinOps for AI" KPIs** in the Phase 3
   certification cost section: cost-per-successful-resolution,
   cache-hit-savings %, and cost-per-fault-class.
