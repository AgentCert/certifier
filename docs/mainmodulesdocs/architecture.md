# Architecture

## Overview

The certifier is a **four-phase analytical pipeline** built in Python. Each phase is an independent module with its own configuration, Pydantic schemas, and scripts. The phases communicate through JSON files (or optionally MongoDB). A shared `utils/` layer provides LLM access, MongoDB connectivity, configuration loading, and logging.

---

## Component Map

```
certifier/
│
├── utils/                          Shared infrastructure layer
│   ├── load_config.py              ConfigLoader — loads configs.json, resolves ENV_* vars
│   ├── azure_openai_util.py        AzureLLMClient — per-model Azure OpenAI clients (class-level cache)
│   ├── mongodb_util.py             MongoDBClient — Atlas connection, vector search
│   ├── embedding.py                Embedding helper (for vector search)
│   ├── rai_util.py                 Responsible AI content checks
│   ├── custom_errors.py            Custom exception classes (base: MyCustomError)
│   ├── setup_logging.py            Centralized logger factory
│   └── file_storage.py             JSON file persistence helpers
│
├── configs/
│   └── configs.json                Global: three model entries + MongoDB + Azure Storage
│
├── fault_analyzer/                 ── Phase 0 ──
│   ├── config/                     fault_bucketing_config.json
│   ├── schema/                     EventClassification, FaultBucket models
│   ├── prompt/                     prompt.yml — classifier prompts
│   └── scripts/
│       ├── fault_bucketing.py      FaultBucketingPipeline (orchestrator)
│       └── classifier.py           FaultEventClassifier (LLM)
│
├── metrics_extractor/              ── Phase 1 ──
│   ├── config/                     metric_extraction_config.json
│   ├── schema/                     LLMQuantitativeExtraction, LLMQualitativeExtraction
│   ├── prompt/                     prompts.yml — extraction prompts
│   └── scripts/
│       ├── metrics_extractor_from_trace.py   TraceMetricsExtractor (orchestrator)
│       └── span_aggregator.py                numeric aggregation helpers
│
├── aggregator/                     ── Phase 2 ──
│   ├── config/                     aggregation_config.json
│   ├── schema/                     CertificationScorecard, FaultCategoryScorecard
│   ├── prompt/                     prompt.yml — LLM Council judge prompts
│   └── scripts/
│       ├── aggregation.py          AggregationOrchestrator, DirectoryQueryService
│       ├── llm_council.py          LLMCouncil — k-judge consensus
│       └── numeric_aggregation.py  Pure numeric/rate functions
│
└── cert_builder/                   ── Phase 3 ──
    ├── config/                     scorecard_config.yaml, table_config.yaml,
    │                               chart_config.yaml, hardcoded_content.yaml
    ├── schema/
    │   ├── certification_schema.py  CertificationReport (Pydantic v2)
    │   └── intermediate.py          Phase-output models (ComputedContent, etc.)
    ├── prompts/                     6 narrative LLM prompt templates (YAML)
    └── scripts/
        ├── certification_pipeline.py          CertificationPipeline (top-level, 4 sub-phases)
        ├── ingestion/ingestor.py              Sub-phase 1 — scorecard → ParsedContext
        ├── computation/                       Sub-phase 2 — deterministic builders
        │   ├── assembler.py                   ComputationAssembler
        │   ├── scorecard_builder.py
        │   ├── table_builder.py
        │   ├── chart_builder.py               Charts depend on scorecard dimensions
        │   ├── chart_renderer.py
        │   ├── assessment_formatter.py
        │   ├── hardcoded_loader.py
        │   └── card_builder.py
        ├── narratives/                        Sub-phase 3 — LLM narrative builders
        │   ├── assembler.py                   NarrativeAssembler (5 concurrent + 1 sequential)
        │   ├── llm_client.py
        │   ├── scope_narrative_builder.py
        │   ├── key_findings_builder.py
        │   ├── qualitative_builder.py
        │   ├── fault_analysis_builder.py
        │   ├── limitation_builder.py
        │   └── recommendation_builder.py
        └── report_assembler.py                Sub-phase 4 — final assembly + Pydantic validation
```

---

## Data Flow

```
[Langfuse trace JSON]
        │
        │  multipart/form-data  OR  file path
        ▼
POST /api/v1/bucketing-extraction
        │
        ├──► FaultBucketingPipeline          (Phase 0)
        │       ├─ load & sort events
        │       ├─ extract FAULT_DATA ground truth
        │       ├─ FaultEventClassifier (LLM, batched)
        │       └─ emit per-fault bucket JSON + manifest
        │           (output_dir/fault_buckets/)
        │
        └──► TraceMetricsExtractor           (Phase 1, per fault bucket)
                ├─ batch spans → LLM extraction (quant + qual)
                ├─ code-based numeric aggregation
                └─ emit *_metrics.json  [optional: write to MongoDB]
                    (output_dir/metrics/)
        │
        │  per-run metrics files  OR  MongoDB
        ▼
POST /api/v1/aggregation-certification
        │
        ├──► AggregationOrchestrator         (Phase 2)
        │       ├─ query documents (DirectoryQueryService / MetricsQueryService)
        │       ├─ compute_numeric_aggregates()   (pure functions)
        │       ├─ compute_derived_rates()        (pure functions)
        │       └─ LLMCouncil.synthesize_consensus()  (k judges + meta-judge)
        │           └─ emit CertificationScorecard JSON
        │
        └──► CertificationPipeline           (Phase 3, 4 sub-phases)
                ├─ Sub-phase 1: ingest_from_file() → ParsedContext
                ├─ Sub-phase 2: ComputationAssembler → ComputedContent
                │       scorecard (→ dims passed to charts), tables, charts,
                │       assessments, hardcoded content, cards
                ├─ Sub-phase 3: NarrativeAssembler → narratives dict
                │       scope, key findings, qualitative findings,
                │       fault analysis, limitations (concurrent ×5)
                │       recommendations (sequential, depends on limitations)
                └─ Sub-phase 4: ReportAssembler → CertificationReport (validated)
```

---

## Technology Stack

| Layer | Technology / Package |
|---|---|
| Language | Python 3.x |
| Data validation | `pydantic==2.12.5` |
| REST framework | `fastapi==0.127.0` + `uvicorn==0.40.0` |
| LLM calls | `agent-framework==1.0.0b251223` (`ChatAgent`, `AzureOpenAIChatClient`) |
| Azure OpenAI SDK | `openai==2.14.0`, `azure-ai-agents==1.2.0b5` |
| Database | `pymongo[srv]==4.16.0` + `motor==3.7.1` (async) |
| Observability input | Langfuse traces (raw JSON) |
| Async | `asyncio` throughout; narrative builders use `asyncio.to_thread` |
| Config format | JSON (global + per-module) + YAML (cert_builder configs + all prompts) |
| Azure Storage | `azure-storage-blob==12.27.1` |

---

## Shared Infrastructure (`utils/`)

### ConfigLoader (`load_config.py`)

Loads `configs/configs.json`. Any value prefixed with `ENV_` is replaced at load-time with the corresponding environment variable (prefix stripped). Returns `None` for unset variables (non-compulsory). Raises `FileNotFoundError` if `configs.json` is absent.

### AzureLLMClient (`azure_openai_util.py`)

Not a true singleton but uses class-level `_shared_clients: Dict[str, AzureOpenAIChatClient]` to cache one client per named model. On init, iterates over `config["models"]` and calls `AzureOpenAIChatClient(endpoint, api_key, deployment_name, api_version)` for each entry. Raises `LLMError` on failure.

Key method: `call_llm(model_name, messages, temperature, max_tokens, ...)` — **reasoning models** (`model_type: "reasoning"`) do not receive the `temperature` parameter (it is silently dropped). This supports GPT-5 / o-series deployments.

### MongoDBClient (`mongodb_util.py`)

Manages the Atlas connection. Exposes document insertion and retrieval. Vector search uses the `metrics_vector_index` (cosine, 1536 dims, 100 candidates, limit 10).

### Error Classes (`custom_errors.py`)

All custom errors inherit from `MyCustomError(Exception)`:

| Class | Purpose |
|---|---|
| `LLMError` | Azure OpenAI call failures |
| `AzureOpenAIClientError` | Client initialisation failures |
| `OrchestratorError` | Pipeline orchestration failures |
| `ResponsibleAIUtilError` | RAI check failures |
| `AsyncFileStorageError` | File storage failures |
| `OpenAIEmbeddingError` | Embedding call failures |

> Note: there is no `MongoDBError` or `ConfigError` class. MongoDB errors and config errors surface as standard Python exceptions.

---

## Key Design Decisions

**Deterministic numeric aggregation** — All statistical computations in Phase 2 (mean, median, p95, rates) are pure Python functions with no LLM involvement. This ensures reproducibility.

**LLM Council for text** — Qualitative fields are processed by `k` independent judges (default 3, all using `extraction_model`) followed by a meta-judge. The `scorecard_synthesis` path has its own temperature and token settings.

**Reasoning model support** — The `AzureLLMClient` detects `model_type: "reasoning"` and strips the `temperature` parameter from those calls, enabling GPT-5 / o-series deployments alongside standard models in the same config.

**Batch processing** — Phase 0 defaults to 10 events/batch; Phase 1 to 15 spans/batch. Both are configurable.

**File-or-DB flexibility** — `DirectoryQueryService` and `MetricsQueryService` implement the same query interface so the orchestrator is storage-agnostic.

**Concurrent narrative generation** — Phase 3 sub-phase 3 runs five builders concurrently via `asyncio.to_thread` + `asyncio.gather`. Recommendations runs sequentially after limitations because it consumes the limitations output.

**Chart dependency** — In Phase 3 sub-phase 2, chart building depends on the scorecard dimensions computed first. This ordering is enforced by `ComputationAssembler`.
