# Deployment & Setup Guide

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | Pydantic v2 requires Python 3.10 |
| Azure OpenAI access | Requires at least one GPT-4 family deployment (`extraction_model`) |
| MongoDB Atlas | Optional — only needed when `--store` flag is used |
| pip | For dependency installation |

---

## 1. Clone & Install Dependencies

```bash
cd certifier/
pip install -r requirements.txt
```

Key packages installed:

| Package | Version | Purpose |
|---|---|---|
| `pydantic` | `2.12.5` | Data validation |
| `fastapi` | `0.127.0` | REST API server |
| `uvicorn` | `0.40.0` | ASGI server |
| `agent-framework` | `1.0.0b251223` | LLM ChatAgent + AzureOpenAIChatClient |
| `openai` | `2.14.0` | Azure OpenAI underlying SDK |
| `pymongo[srv]` | `4.16.0` | MongoDB Atlas client |
| `motor` | `3.7.1` | Async MongoDB driver |
| `azure-storage-blob` | `12.27.1` | Azure Blob Storage (optional) |

---

## 2. Environment Variables

Set these before running the pipelines or the API server:

```bash
# Required — extraction model (used for all LLM calls)
export AZURE_OPENAI_ENDPOINT="https://<your-resource>.openai.azure.com/"
export AZURE_OPENAI_API_KEY="<your-api-key>"
export AZURE_OPENAI_API_VERSION="2024-02-01"
export AZURE_OPENAI_CHAT_DEPLOYMENT_NAME="<your-deployment-name>"

# Optional — reasoning model (GPT-5 / o-series deployments)
export AZURE_OPENAI_GPT5_ENDPOINT="https://<gpt5-resource>.openai.azure.com/"
export AZURE_OPENAI_GPT5_API_KEY="<gpt5-api-key>"
export AZURE_OPENAI_GPT5_API_VERSION="2024-02-01"
export AZURE_OPENAI_GPT5_CHAT_DEPLOYMENT_NAME="<gpt5-deployment-name>"

# Optional — embedding model (for vector search)
export AZURE_EMBEDDING_ENDPOINT="https://<embedding-resource>.openai.azure.com/"
export AZURE_EMBEDDING_API_KEY="<embedding-api-key>"
export AZURE_EMBEDDING_API_VERSION="2024-02-01"
export AZURE_EMBEDDING_MODEL="text-embedding-ada-002"

# Optional — MongoDB Atlas
export MONGODB_CONNECTION_STRING="mongodb+srv://<user>:<pass>@<cluster>.mongodb.net/"

# Optional — Azure Blob Storage
export AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;..."
```

Environment variable names must match the `ENV_*` references in `configs/configs.json`. See [configuration.md](configuration.md) for the complete mapping.

---

## 3. Running Phases 0 + 1 (Trace → Metrics)

```bash
python run_bucketing_and_extraction_pipeline.py \
    --trace-file  /path/to/trace.json \
    --output-dir  ./output/run-001
```

**All CLI flags** (note: kebab-case):

| Flag | Required | Default | Description |
|---|---|---|---|
| `--trace-file` | **Yes** | — | Path to the raw Langfuse trace JSON file |
| `--output-dir` | **Yes** | — | Directory for all pipeline outputs |
| `--batch-size` | No | `10` | Events per LLM batch in Phase 0 |
| `--store` | No | off | Persist extracted metrics to MongoDB (flag, no value) |

**Output directory structure created:**

```
./output/run-001/
├── fault_buckets/
│   ├── <fault_id>_bucket.json     (one per fault)
│   └── ...
├── metrics/
│   ├── <fault_id>_<run_id>_trace.json        (temporary trace per fault)
│   ├── <fault_id>_<run_id>_fault_config.json (temporary fault config per fault)
│   └── <fault_id>_<run_id>_metrics.json      (per-fault extracted metrics)
└── pipeline_summary.json          (summary of the full run)
```

---

## 4. Running Phases 2 + 3 (Metrics → Report)

```bash
python run_aggregation_and_certification_pipeline.py \
    --metrics-dir  ./output/run-001/metrics \
    --output-dir   ./output/report-001 \
    --agent-id     agent-001 \
    --agent-name   "My SRE Agent"
```

**All CLI flags** (note: kebab-case):

| Flag | Required | Default | Description |
|---|---|---|---|
| `--metrics-dir` | **Yes** | — | Directory containing `*_metrics.json` files |
| `--output-dir` | **Yes** | — | Directory for all pipeline outputs |
| `--agent-id` | **Yes** | — | Agent ID to aggregate metrics for |
| `--agent-name` | **Yes** | — | Agent display name |
| `--certification-run-id` | No | `""` (auto) | Override the certification run identifier |
| `--runs-per-fault` | No | `30` | Expected runs per fault for coverage reporting |
| `--debug` | No | off | Persist intermediate Phase 3 outputs to disk (flag) |

**Output files created:**

```
./output/report-001/
├── aggregated_scorecard_output_<agent_id>.json  (Phase 2 output)
├── certification_report_<agent_id>.json         (Phase 3 final report)
└── pipeline_summary.json                        (run summary)
```

When `--debug` is set, Phase 3 intermediate files are also written to `data/intermediate/` relative to the scorecard input path.

---

## 5. Running the REST API Server

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

The API exposes two endpoints:

```
POST http://localhost:8000/api/v1/bucketing-extraction
POST http://localhost:8000/api/v1/aggregation-certification
```

See [api.md](api.md) for full request/response documentation.

---

## 6. MongoDB Setup (Optional)

Only required when using `--store` (CLI) or `"store_to_mongodb": true` (API).

1. Create a MongoDB Atlas cluster.
2. Create database `agentcert` with collections:
   - `agent_run_metrics`
   - `llm_quantitative_extractions`
   - `llm_qualitative_extractions`
   - `aggregated_scorecards`
3. Create an Atlas Vector Search index named `metrics_vector_index` on `agent_run_metrics`:

```json
{
  "mappings": {
    "dynamic": true,
    "fields": {
      "embedding": {
        "type": "knnVector",
        "dimensions": 1536,
        "similarity": "cosine"
      }
    }
  }
}
```

4. Set `MONGODB_CONNECTION_STRING` in your environment.

---

## 7. Running Tests

Unit and integration tests live in `*/tests/` within each module:

```bash
# Run all tests
pytest

# Run tests for a specific module
pytest fault_analyzer/tests/
pytest metrics_extractor/tests/
pytest aggregator/tests/
pytest cert_builder/tests/
```

The `mock_trace_generator/` module generates synthetic traces and metrics for testing without calling Azure OpenAI.

---

## 8. Common Failure Modes

| Symptom | Likely Cause | Fix |
|---|---|---|
| `FileNotFoundError: Configuration file not found` | `configs/configs.json` missing | Ensure you are running from the `certifier/` directory |
| `AzureOpenAIClientError` on startup | `AZURE_OPENAI_*` env vars not set | Export required environment variables |
| `LLMError: rate limit exceeded` | Too many concurrent LLM calls | Reduce `--batch-size` or add retry backoff |
| `No per-run metric documents found for agent_id` | Wrong `agent_id` or empty metrics dir | Verify `agent_id` matches the value in the `*_metrics.json` files |
| Empty `results[]` in Phase 0+1 response | LLM could not classify any events | Check trace format; reduce `--batch-size` |
| Pydantic `ValidationError` in Phase 3 | Upstream scorecard has unexpected nulls | Check Phase 2 output; run with `--debug` for intermediate files |
| `certifier_run_id` empty in report | `--certification-run-id` not specified | Either pass `--certification-run-id` or accept the auto-generated ID |

---

## 9. Production Considerations

- **Authentication**: The API endpoints have no authentication currently. Add an API key middleware or OAuth integration before exposing to the network. See `api-spec.md` ("TBD" section).
- **Reasoning models**: If you configure a `reasoning_model` (GPT-5/o-series), it will not receive `temperature` parameters. This is handled automatically by `AzureLLMClient.is_reasoning_model()`.
- **Token cost**: Token usage is tracked per phase and returned in every API response. Monitor `bucketing_tokens` and `extraction_tokens` in Phase 0+1 responses.
- **Scaling**: LLM calls are the throughput bottleneck. Phase 3 runs 5 narrative builders concurrently; other phases are sequential per request.
- **Logging**: Centralized logging is configured via `utils/setup_logging.py`. Redirect to Azure Monitor or another observability stack by adding a log handler there.
- **Debug outputs**: Use `--debug` during development to inspect `parsed_context.json`, `computed_content.json`, and `narratives.json` from Phase 3 sub-phases.
