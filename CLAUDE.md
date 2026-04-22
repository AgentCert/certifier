# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest

# Run tests for a specific module
pytest fault_analyzer/tests/
pytest metrics_extractor/tests/
pytest aggregator/tests/
pytest cert_builder/tests/

# Run a single test file or test
pytest fault_analyzer/tests/test_fault_bucketing.py
pytest fault_analyzer/tests/test_fault_bucketing.py::TestEventClassification::test_minimal_creation

# Start the REST API server
uvicorn app:app --host 0.0.0.0 --port 8000

# Run Phase 0+1 pipeline (fault bucketing → metrics extraction)
python run_bucketing_and_extraction_pipeline.py \
    --trace-file <path/to/trace.json> \
    --output-dir <output_directory> \
    [--batch-size 10] [--store]

# Run Phase 2+3 pipeline (aggregation → certification report)
python run_aggregation_and_certification_pipeline.py \
    --metrics-dir <directory_with_metrics> \
    --output-dir <output_directory> \
    --agent-id <agent_id> \
    --agent-name <agent_name> \
    [--runs-per-fault 30] [--debug]
```

## Architecture

The certifier is a **four-phase analytical pipeline** that consumes raw Langfuse traces from AI agents operating on Kubernetes clusters under fault injection, and produces 12-section certification reports.

### Pipeline Overview

```
Raw Langfuse trace (JSON)
  → Phase 0: Fault Bucketing      (fault_analyzer/)
  → Phase 1: Metrics Extraction   (metrics_extractor/)
  → Phase 2: Aggregation          (aggregator/)
  → Phase 3: Certification        (cert_builder/)
  → CertificationReport (JSON, 12 sections)
```

### Modules

| Module | Phase | What it does |
|---|---|---|
| `fault_analyzer/` | 0 | LLM classifies interleaved trace events into per-fault lifecycle buckets |
| `metrics_extractor/` | 1 | Extracts quantitative (TTD, TTR, tokens) & qualitative metrics per fault |
| `aggregator/` | 2 | Aggregates metrics across N runs: pure-Python stats + LLM Council synthesis |
| `cert_builder/` | 3 | Builds certification report: deterministic builders + concurrent LLM narrative builders |
| `utils/` | All | Shared: config loader, AzureLLMClient, MongoDB client, logging, errors |
| `mock_trace_generator/` | Test | Generates synthetic traces for testing without Azure OpenAI calls |

### Key Design Patterns

**Deterministic numeric aggregation**: All statistics (mean, median, p95, success rates) are pure Python — no LLM arithmetic, fully reproducible.

**LLM Council for qualitative synthesis**: k independent judges + meta-judge consensus for Phase 2 narrative fields.

**Storage-agnostic query interface**: `DirectoryQueryService` (file-based) and `MetricsQueryService` (MongoDB) implement the same interface — the orchestrator doesn't know which backend it uses.

**Reasoning model handling**: `AzureLLMClient` (in `utils/azure_openai_util.py`) detects `model_type: "reasoning"` in config and automatically strips the `temperature` parameter for GPT-o-series deployments.

**Concurrent narrative generation**: Phase 3 runs 5 narrative builders concurrently via `asyncio.gather`; the recommendations builder runs sequentially after limitations (explicit dependency).

### Configuration

**Global config**: `configs/configs.json` — defines MongoDB collections, 3 model entries (`embedding_model`, `extraction_model`, `reasoning_model`), and Azure Blob Storage.

**Per-module configs**: Each module has a `config/` subdirectory with JSON or YAML files for batch sizes, model selection, temperatures, and collection names.

**Environment variable resolution**: Any config value prefixed with `ENV_` (e.g., `ENV_MONGODB_CONNECTION_STRING`) is resolved from the environment at load time via `ConfigLoader` in `utils/load_config.py`. See `.env.example` for the full list of required variables.

### Data Flow Detail

- **Phase 0** outputs per-fault bucket JSON files + manifest
- **Phase 1** outputs `*_metrics.json` per fault (or writes to MongoDB)
- **Phase 2** outputs a `CertificationScorecard` JSON
- **Phase 3** validates its final output against the `CertificationReport` Pydantic schema — if validation fails, the pipeline errors rather than emitting a malformed report

### REST API

Two endpoints under `POST /api/v1/`:
- `/bucketing-extraction` — runs Phase 0+1 on an uploaded trace file
- `/aggregation-certification` — runs Phase 2+3 on a metrics directory or MongoDB collection

Full API schema is in `api-spec.md`.
