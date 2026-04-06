# Metric Extraction from Trace Module

## Overview

The **Metric Extraction from Trace** module extracts quantitative and qualitative metrics from Langfuse trace files produced by autonomous IT-Ops agents. It uses LLM-based analysis combined with code-based numeric aggregation to ensure computational accuracy.

## Architecture

The module follows a layered design:

```
config/          → Module-specific configuration (batch size, model params)
docs/            → This documentation
prompt/          → LLM system prompt templates (YAML)
schema/          → Data models (dataclasses) for extraction results
scripts/
  metrics_extractor_from_trace.py → Main extractor class and CLI
  span_aggregator.py              → Code-based numeric aggregation (no LLM math)
tests/           → Unit tests
```

### Data Flow

```
Langfuse Trace JSON
    │
    ▼
┌──────────────────────────────┐
│   TraceMetricsExtractor      │
│   1. Load & sort spans       │
│   2. Split into batches      │
│      (BATCH_SIZE from config)│
│   3. Per-batch LLM extraction│
│      (quantitative +         │
│       qualitative)           │
│   4. LLM span identification │
│      (detection/mitigation)  │
│   5. Code-based numeric      │
│      aggregation             │
│      (QuantitativeAggregator │
│       QualitativeAggregator) │
│   6. LLM text consolidation  │
│   7. Override numerics with   │
│      code-computed values    │
│   8. Optional MongoDB store  │
└──────────────────────────────┘
    │
    ▼
ExtractionResult
  ├── LLMQuantitativeExtraction
  ├── LLMQualitativeExtraction
  └── TokenUsage
```

## Configuration

Module-specific settings are in `config/metric_extraction_config.json`:

```json
{
    "extractor": {
        "model_name": "extraction_model",
        "batch_size": 15,
        "temperature": 0.1,
        "max_tokens": 16000
    },
    "mongodb": {
        "database": "agentcert",
        "quantitative_collection": "llm_quantitative_extractions",
        "qualitative_collection": "llm_qualitative_extractions"
    }
}
```

Global configuration (Azure OpenAI endpoints, keys) is loaded via `ConfigLoader` from `utils/load_config.py`.

## Usage

### CLI

```bash
# Single trace file
python -m notebooks.metric_extraction_from_trace.scripts.metrics_extractor_from_trace \
    --trace-file-name path/to/trace.json \
    --fault-config-path path/to/fault_configuration.json \
    --store

# Directory of traces
python -m notebooks.metric_extraction_from_trace.scripts.metrics_extractor_from_trace \
    --trace-directory path/to/traces/ \
    --fault-config-path path/to/fault_configuration.json
```

### Programmatic (Async)

```python
import asyncio
from notebooks.metric_extraction_from_trace import (
    TraceMetricsExtractor,
    extract_metrics_from_trace_async,
)

# Using convenience function
result = asyncio.run(extract_metrics_from_trace_async(
    "path/to/trace.json",
    fault_config_path="path/to/fault_config.json",
    store_to_mongodb=True,
))

# Using class directly
extractor = TraceMetricsExtractor(fault_config_path="path/to/fault_config.json")
result = asyncio.run(extractor.extract_metrics_async("path/to/trace.json"))
```

### Programmatic (Sync)

```python
from notebooks.metric_extraction_from_trace import extract_metrics_from_trace

result = extract_metrics_from_trace(
    "path/to/trace.json",
    fault_config_path="path/to/fault_config.json",
)

print(result.quantitative.model_dump_json(indent=2))
print(result.qualitative.model_dump_json(indent=2))
print(result.token_usage.to_dict())
```

## Output

The `ExtractionResult` contains:

- **quantitative** (`LLMQuantitativeExtraction`): Fault info, timestamps, TTD/TTR, token counts, tool calls, security metrics, ground-truth comparison
- **qualitative** (`LLMQualitativeExtraction`): RAI checks, security compliance, reasoning quality, hallucination score, plan adherence, agent summary
- **token_usage** (`TokenUsage`): Total input/output/total tokens used across all LLM calls
- **mongodb_document_id** (`str | None`): Document ID if stored to MongoDB

## Key Design Decisions

1. **Batch Processing**: Large traces are split into batches (default 15 spans) to avoid LLM token limits
2. **Two-Phase Aggregation**: LLM extracts per-batch observations, code aggregates all numerics (sums, averages, ratios)
3. **Span Identification**: A dedicated LLM call identifies the first detection and final mitigation spans for accurate TTD/TTR timestamps
4. **Fault Config Integration**: When `fault_configuration.json` is provided, ground truth context is injected into LLM prompts and deterministic fields override LLM-extracted values
5. **No LLM Math**: All mathematical operations happen in `span_aggregator.py` for accuracy

## Dependencies

- `utils.azure_openai_util.AzureLLMClient` — LLM API calls
- `utils.load_config.ConfigLoader` — Global configuration
- `utils.mongodb_util.MongoDBClient` — MongoDB persistence
- `utils.setup_logging.logger` — Centralized logging
- `data_models.metrics_model` — Pydantic models for structured LLM output
