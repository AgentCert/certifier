# Configuration Reference

The certifier uses a two-level configuration system:

1. **Global config** (`configs/configs.json`) — Azure OpenAI endpoints, MongoDB connection, Azure Storage. Shared by all modules.
2. **Module configs** — per-phase JSON or YAML files with module-specific parameters.

---

## Environment Variable Resolution

Any string value in any config file that starts with `ENV_` is replaced at load-time with the corresponding environment variable (the `ENV_` prefix is stripped and the remainder is used as the env var name).

**Example:**
```json
"api_key": "ENV_AZURE_OPENAI_API_KEY"
```
→ resolves to the value of `$AZURE_OPENAI_API_KEY` at runtime.

This is handled by `ConfigLoader._resolve_env_values()` in `utils/load_config.py`. If an `ENV_` variable is not set, the value resolves to `None` (non-compulsory by default). The `ConfigLoader.load_config()` method raises `FileNotFoundError` if `configs/configs.json` is missing.

---

## Global Config — `configs/configs.json`

Full contents as they exist in the file:

```json
{
    "mongodb": {
        "connection_string_env": "ENV_MONGODB_CONNECTION_STRING",
        "database": "agentcert",
        "collections": {
            "metrics":      "agent_run_metrics",
            "quantitative": "llm_quantitative_extractions",
            "qualitative":  "llm_qualitative_extractions"
        },
        "vector_search": {
            "index_name":      "metrics_vector_index",
            "embedding_field": "embedding",
            "dimensions":      1536,
            "similarity":      "cosine",
            "num_candidates":  100,
            "limit":           10
        }
    },
    "storage_connections": {
        "isActive":       true,
        "account_name":   "",
        "connection_str": "ENV_AZURE_STORAGE_CONNECTION_STRING"
    },
    "models": {
        "embedding_model": {
            "endpoint":         "ENV_AZURE_EMBEDDING_ENDPOINT",
            "api_key":          "ENV_AZURE_EMBEDDING_API_KEY",
            "api_version":      "ENV_AZURE_EMBEDDING_API_VERSION",
            "deployment_name":  "ENV_AZURE_EMBEDDING_MODEL"
        },
        "extraction_model": {
            "endpoint":         "ENV_AZURE_OPENAI_ENDPOINT",
            "api_key":          "ENV_AZURE_OPENAI_API_KEY",
            "api_version":      "ENV_AZURE_OPENAI_API_VERSION",
            "deployment_name":  "ENV_AZURE_OPENAI_CHAT_DEPLOYMENT_NAME",
            "model_type":       "standard"
        },
        "reasoning_model": {
            "endpoint":         "ENV_AZURE_OPENAI_GPT5_ENDPOINT",
            "api_key":          "ENV_AZURE_OPENAI_GPT5_API_KEY",
            "api_version":      "ENV_AZURE_OPENAI_GPT5_API_VERSION",
            "deployment_name":  "ENV_AZURE_OPENAI_GPT5_CHAT_DEPLOYMENT_NAME",
            "model_type":       "reasoning"
        }
    }
}
```

### `models` block

Three named model entries are defined:

| Alias | Role | `model_type` |
|---|---|---|
| `extraction_model` | Primary LLM for classification, extraction, council | `"standard"` |
| `reasoning_model` | Secondary LLM (GPT-5 series) for complex reasoning | `"reasoning"` |
| `embedding_model` | Embeddings for vector search | — |

Each model entry contains:

| Key | Description |
|---|---|
| `endpoint` | Azure OpenAI resource endpoint URL (ENV reference) |
| `api_key` | Azure OpenAI API key (ENV reference) |
| `api_version` | API version string (ENV reference) |
| `deployment_name` | Deployment name (ENV reference) |
| `model_type` | `"standard"` or `"reasoning"`. Reasoning models do **not** receive `temperature` parameters. |

### `mongodb` block

| Key | Description |
|---|---|
| `connection_string_env` | ENV reference for Atlas connection string |
| `database` | Database name: `agentcert` |
| `collections.metrics` | Per-run metrics collection: `agent_run_metrics` |
| `collections.quantitative` | Quantitative extractions collection: `llm_quantitative_extractions` |
| `collections.qualitative` | Qualitative extractions collection: `llm_qualitative_extractions` |
| `vector_search.index_name` | `metrics_vector_index` |
| `vector_search.embedding_field` | `embedding` |
| `vector_search.dimensions` | `1536` |
| `vector_search.similarity` | `cosine` |
| `vector_search.num_candidates` | `100` |
| `vector_search.limit` | `10` |

### `storage_connections` block

| Key | Description |
|---|---|
| `isActive` | Whether Azure Blob Storage is enabled |
| `account_name` | Azure Storage account name |
| `connection_str` | ENV reference for the storage connection string |

---

## Required Environment Variables

| Variable | Used by | Description |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | `extraction_model` | Azure OpenAI resource URL |
| `AZURE_OPENAI_API_KEY` | `extraction_model` | API key |
| `AZURE_OPENAI_API_VERSION` | `extraction_model` | API version |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | `extraction_model` | Deployment name |
| `AZURE_OPENAI_GPT5_ENDPOINT` | `reasoning_model` | GPT-5 resource URL |
| `AZURE_OPENAI_GPT5_API_KEY` | `reasoning_model` | GPT-5 API key |
| `AZURE_OPENAI_GPT5_API_VERSION` | `reasoning_model` | GPT-5 API version |
| `AZURE_OPENAI_GPT5_CHAT_DEPLOYMENT_NAME` | `reasoning_model` | GPT-5 deployment name |
| `AZURE_EMBEDDING_ENDPOINT` | `embedding_model` | Embeddings endpoint |
| `AZURE_EMBEDDING_API_KEY` | `embedding_model` | Embeddings API key |
| `AZURE_EMBEDDING_API_VERSION` | `embedding_model` | Embeddings API version |
| `AZURE_EMBEDDING_MODEL` | `embedding_model` | Embeddings deployment name |
| `MONGODB_CONNECTION_STRING` | `MongoDBClient` | MongoDB Atlas connection string |
| `AZURE_STORAGE_CONNECTION_STRING` | `storage_connections` | Azure Blob Storage connection string |

---

## Module Config — Phase 0 (`fault_analyzer/config/fault_bucketing_config.json`)

Nested structure with two sections:

```json
{
    "classifier": {
        "model_name":          "extraction_model",
        "temperature":         0.1,
        "max_tokens":          4000,
        "fallback_confidence": 0.3
    },
    "pipeline": {
        "default_batch_size":        10,
        "max_filename_stem_length":  80
    }
}
```

| Path | Default | Description |
|---|---|---|
| `classifier.model_name` | `"extraction_model"` | Model alias from global config |
| `classifier.temperature` | `0.1` | LLM temperature for classification |
| `classifier.max_tokens` | `4000` | Max tokens per LLM response |
| `classifier.fallback_confidence` | `0.3` | Confidence score assigned when LLM fails |
| `pipeline.default_batch_size` | `10` | Events per LLM classification batch |
| `pipeline.max_filename_stem_length` | `80` | Max characters for generated file names |

---

## Module Config — Phase 1 (`metrics_extractor/config/metric_extraction_config.json`)

```json
{
    "extractor": {
        "model_name":  "extraction_model",
        "batch_size":  15,
        "temperature": 0.1,
        "max_tokens":  16000
    },
    "mongodb": {
        "database":                 "agentcert",
        "quantitative_collection":  "llm_quantitative_extractions",
        "qualitative_collection":   "llm_qualitative_extractions"
    }
}
```

| Path | Default | Description |
|---|---|---|
| `extractor.model_name` | `"extraction_model"` | Model alias |
| `extractor.batch_size` | `15` | Spans per LLM extraction batch |
| `extractor.temperature` | `0.1` | LLM temperature |
| `extractor.max_tokens` | `16000` | Max tokens per LLM call |
| `mongodb.database` | `"agentcert"` | MongoDB database name |
| `mongodb.quantitative_collection` | `"llm_quantitative_extractions"` | Target collection for quantitative metrics |
| `mongodb.qualitative_collection` | `"llm_qualitative_extractions"` | Target collection for qualitative metrics |

---

## Module Config — Phase 2 (`aggregator/config/aggregation_config.json`)

```json
{
    "llm_council": {
        "council_size":                      3,
        "council_members":                   ["extraction_model", "extraction_model", "extraction_model"],
        "meta_judge_model":                  "extraction_model",
        "judge_temperature":                 0.3,
        "judge_max_tokens":                  1500,
        "meta_judge_temperature":            0.1,
        "meta_judge_max_tokens":             2000,
        "scorecard_synthesis_temperature":   0.2,
        "scorecard_synthesis_max_tokens":    2000
    },
    "pipeline": {
        "aggregated_scorecards_collection":  "aggregated_scorecards",
        "rounding_precision":                4
    }
}
```

| Path | Default | Description |
|---|---|---|
| `llm_council.council_size` | `3` | Number of independent judges (`k`) |
| `llm_council.council_members` | `["extraction_model" × 3]` | Model alias for each judge |
| `llm_council.meta_judge_model` | `"extraction_model"` | Model alias for the meta-judge |
| `llm_council.judge_temperature` | `0.3` | Temperature for individual judge calls |
| `llm_council.judge_max_tokens` | `1500` | Max tokens per judge call |
| `llm_council.meta_judge_temperature` | `0.1` | Temperature for meta-judge call |
| `llm_council.meta_judge_max_tokens` | `2000` | Max tokens for meta-judge call |
| `llm_council.scorecard_synthesis_temperature` | `0.2` | Temperature for scorecard synthesis |
| `llm_council.scorecard_synthesis_max_tokens` | `2000` | Max tokens for scorecard synthesis |
| `pipeline.aggregated_scorecards_collection` | `"aggregated_scorecards"` | MongoDB collection for scorecard storage |
| `pipeline.rounding_precision` | `4` | Decimal places for numeric output |

---

## Module Config — Phase 3 (`cert_builder/config/`)

### `scorecard_config.yaml`

Controls radar normalisation and finding thresholds.

```yaml
normalization:
  speed_ref:   1800   # seconds — ceiling for TTD/TTM (0s = 1.0, 1800s = 0.0)
  score_scale: 10     # reasoning/hallucination are on 0-10 scale

findings:
  concern:
    detection_rate_below:   0.5   # fault_detection_success_rate < this
    false_negative_above:   0.5   # false_negative_rate > this
    ttd_median_above:       300   # median TTD seconds > this
    ttm_median_above:       600   # median TTM seconds > this
    hallucination_max_above: 3.0  # hallucination_score.max > this

  good:
    all_rai_perfect:          true  # all categories rai_compliance_rate == 1.0
    all_security_perfect:     true  # all categories security_compliance_rate == 1.0
    all_hallucination_zero:   true  # all categories hallucination_score.mean == 0.0
```

### `table_config.yaml`

Provides static data for the table builder — does not define table schemas, those are hard-coded in the builder scripts.

```yaml
judge_models:
  headers: ["Judge", "Model", "Provider", "Role"]
  rows:
    - ["Judge 1", "gpt-4.1", "Azure OpenAI", "Primary evaluator"]
    - ...

formatting:
  time_suffix: "s"
  time_decimals: 1
  rate_suffix: "%"
  rate_decimals: 0
  score_decimals: 2

severity_order: ["High", "Medium", "Low"]
priority_order: ["Critical", "High", "Medium", "Low"]
```

### `chart_config.yaml`

Provides reference lines and scale parameters for the chart builder — does not define chart schemas.

```yaml
reference_lines:
  ttd_concern: { value: 300, label: "Concern Threshold" }
  ttm_concern: { value: 600, label: "Concern Threshold" }
  rates_minimum: { value: 0.5, label: "Minimum Acceptable" }

heatmap_scale: [0.0, 0.25, 0.5, 0.75, 1.0]
score_scale: 10
```

### `hardcoded_content.yaml`

Static text content loaded verbatim into the report: metric definitions, methodology descriptions, section intro text, and statistical formula explanations.

---

## LLM Prompt Files

| File | Used by |
|---|---|
| `cert_builder/prompts/scope_narrative_prompt.yaml` | Scope Narrative Builder |
| `cert_builder/prompts/key_findings_prompt.yaml` | Key Findings Builder |
| `cert_builder/prompts/qualitative_prompt.yaml` | Qualitative Findings Builder |
| `cert_builder/prompts/fault_analysis_prompt.yaml` | Fault Analysis Builder |
| `cert_builder/prompts/limitation_prompt.yaml` | Limitations Builder |
| `cert_builder/prompts/recommendation_prompt.yaml` | Recommendations Builder |
| `fault_analyzer/prompt/prompt.yml` | Fault Event Classifier |
| `metrics_extractor/prompt/prompts.yml` | Metrics Extractor |
| `aggregator/prompt/prompt.yml` | LLM Council judges and meta-judge |

---

## Configuration Loading

```
utils/load_config.py
  ConfigLoader.load_config()
    1. Reads certifier/configs/configs.json
    2. Recursively replaces ENV_* values with environment variables
    3. Returns resolved dict
    
  Used by:
    AzureLLMClient(config=config)  → reads config["models"]
    MongoDBClient                  → reads config["mongodb"]
    Each pipeline phase            → passes full config down
```

Module configs are loaded independently by each pipeline module and are not merged with the global config automatically.
