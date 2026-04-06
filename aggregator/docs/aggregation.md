# Aggregation Module

Fault-category and overall agent-level metrics aggregation for AgentCert.

## Overview

Aggregates per-run metrics (stored in MongoDB by the metric extraction pipeline) into:
1. **Fault-category level scorecards** — one per fault category (e.g., "pod-kill", "network-loss")
2. **Certification scorecard** — a single top-level scorecard combining all fault categories

Numeric metrics are aggregated deterministically in code; textual/narrative metrics
are synthesized via an **LLM Council** (k independent judges + meta-reconciliation).

## Architecture

```
aggregation/
├── config/
│   └── aggregation_config.json   # LLM council & pipeline settings
├── docs/
│   └── aggregation.md            # This file
├── prompt/
│   └── prompt.yml                # LLM judge & meta-judge prompt templates
├── schema/
│   ├── __init__.py
│   └── data_models.py            # Pydantic models (scorecards, stats, consensus)
├── scripts/
│   ├── __init__.py
│   ├── aggregation.py            # Main orchestrator + CLI entry point
│   ├── llm_council.py            # LLM Council (k-judge + meta-reconciliation)
│   └── numeric_aggregation.py    # Pure numeric/rate/boolean aggregation
├── tests/
│   ├── __init__.py
│   └── test_aggregation.py       # Unit tests (pytest)
└── __init__.py                   # Public API exports
```

## Data Flow

```
Per-run metrics in MongoDB (agent_run_metrics collection)
    │
    ▼
┌─────────────────────────────────────────┐
│  AggregationOrchestrator.aggregate_all()│
│                                         │
│  1. Query per-run docs (MetricsQuery)   │
│  2. Numeric aggregates (pure functions) │
│  3. Derived rates (pure functions)      │
│  4. Boolean aggregates (pure functions) │
│  5. Textual synthesis (LLM Council)     │
│  5b. Limitations & recommendations      │
│  6. Assemble category scorecard         │
└─────────────────────────────────────────┘
    │
    ▼
Aggregated scorecard in MongoDB (aggregated_scorecards collection)
    + JSON file output
```

## Configuration

All module-specific settings are in `config/aggregation_config.json`:

| Key | Default | Description |
|-----|---------|-------------|
| `llm_council.council_size` | `3` | Number of independent LLM judges (k) |
| `llm_council.model_name` | `"extraction_model"` | Model key in `configs.json` |
| `llm_council.judge_temperature` | `0.3` | Temperature for individual judges |
| `llm_council.judge_max_tokens` | `1500` | Max tokens per judge response |
| `llm_council.meta_judge_temperature` | `0.1` | Temperature for meta-reconciliation judge |
| `llm_council.meta_judge_max_tokens` | `2000` | Max tokens for meta-judge response |
| `llm_council.scorecard_synthesis_temperature` | `0.2` | Temperature for scorecard synthesis |
| `llm_council.scorecard_synthesis_max_tokens` | `2000` | Max tokens for synthesis response |
| `pipeline.aggregated_scorecards_collection` | `"aggregated_scorecards"` | MongoDB collection name |
| `pipeline.rounding_precision` | `4` | Decimal places for rounding stats |

Global Azure OpenAI and MongoDB settings are loaded via `ConfigLoader` from `configs/configs.json`.

## Usage

### CLI

```bash
cd agentcert
# Load environment variables
python -m aggregation.scripts.aggregation \
    --agent-id "agent-001" \
    --agent-name "MyAgent" \
    --certification-run-id "run-001" \
    --runs-per-fault 30
```

### Programmatic

```python
import asyncio
from aggregation import AggregationOrchestrator
from utils.azure_openai_util import AzureLLMClient
from utils.load_config import ConfigLoader
from utils.mongodb_util import MongoDBClient, MongoDBConfig

config = ConfigLoader.load_config()
db_client = MongoDBClient(MongoDBConfig(config))
llm_client = AzureLLMClient(config=config)

orchestrator = AggregationOrchestrator(db_client, llm_client)
scorecard = asyncio.run(orchestrator.aggregate_all(
    agent_id="agent-001",
    agent_name="MyAgent",
))
```

## Key Classes

- **`AggregationOrchestrator`** — Main entry point, orchestrates the full pipeline
- **`MetricsQueryService`** — MongoDB query helper for per-run metrics
- **`ScorecardAssembler`** — Assembles category and certification scorecards
- **`ScorecardStorage`** — Persists scorecards to MongoDB
- **`LLMCouncil`** — k-judge + meta-reconciliation pattern for textual metrics
- **`compute_stats()`** — Core statistics computation (mean, median, std_dev, p95, etc.)
- **`compute_numeric_aggregates()`** — Aggregates all numeric metrics across runs
- **`compute_derived_rates()`** — Computes detection/mitigation/compliance rates
- **`compute_boolean_aggregates()`** — Aggregates PII and hallucination detection flags
