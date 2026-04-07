# Pipeline Reference

The certifier runs as a four-phase pipeline. Phases 0 and 1 are invoked together (trace → per-fault metrics). Phases 2 and 3 are invoked together (metrics → certification report). Each phase is independently testable and produces intermediate outputs that can be inspected or replayed.

---

## Phase 0 — Fault Bucketing

**Module**: `fault_analyzer/`  
**Entry point**: `FaultBucketingPipeline.run()`  
**Inputs**: Raw Langfuse trace JSON  
**Outputs**: Per-fault bucket JSON files + manifest file

### Purpose

A Langfuse trace from a chaos experiment contains interleaved events spanning multiple faults. Phase 0 separates those events into distinct per-fault buckets so that downstream phases can reason about one fault at a time.

### Algorithm

```
1. Load trace JSON and sort all events chronologically.
2. Extract FAULT_DATA entries — these are structured ground-truth records
   injected by the chaos platform describing each fault's name, category,
   injection time, target pod, and namespace.
3. Batch events (default batch_size = 10) and send each batch to the LLM.
4. FaultEventClassifier returns an EventClassification per event:
   - which fault_id this event belongs to
   - whether it signals fault detection / mitigation
   - confidence score
5. Open a FaultBucket when the LLM first detects a fault.
   Close/mark-mitigated when the LLM signals mitigation.
6. Detect NEW faults (not in the pre-defined FAULT_DATA list) and open
   additional buckets for them.
7. Enrich each bucket with ground-truth from FAULT_DATA (severity, category,
   injection timestamps).
8. Write one JSON file per fault bucket + a manifest listing all buckets.
```

### Key Classes

| Class | File | Role |
|---|---|---|
| `FaultBucketingPipeline` | `scripts/fault_bucketing.py` | Top-level orchestrator (async) |
| `FaultEventClassifier` | `scripts/classifier.py` | LLM-based per-event classification |
| `FaultBucket` (dataclass) | `schema/` | Container for one fault's complete lifecycle |
| `EventClassification` (Pydantic) | `schema/` | Single-event classification result |
| `BatchClassificationResult` (Pydantic) | `schema/` | LLM batch output wrapper |

### Configuration (`fault_analyzer/config/fault_bucketing_config.json`)

| Key | Default | Description |
|---|---|---|
| `batch_size` | `10` | Events per LLM classification batch |
| `confidence_threshold` | — | Minimum confidence to accept a classification |
| `model_name` | — | Azure OpenAI deployment to use |
| `temperature` | — | LLM temperature |
| `max_tokens` | — | Max tokens per LLM response |

### Output Format

Each fault bucket file contains:
- `fault_id` — identifier (e.g. `disk-fill-0`)
- `fault_name`, `fault_category`
- `injection_time`, `detection_time`, `mitigation_time`
- `severity`, `target_pod`, `namespace`
- `events[]` — chronological list of classified events
- `is_new_fault` — `true` if not in the original FAULT_DATA list

---

## Phase 1 — Metrics Extraction

**Module**: `metrics_extractor/`  
**Entry point**: `TraceMetricsExtractor.extract_metrics_async()`  
**Inputs**: Per-fault bucket JSON file  
**Outputs**: `*_metrics.json` per fault (optionally written to MongoDB)

### Purpose

For each fault bucket, Phase 1 extracts a rich set of quantitative and qualitative metrics by asking an LLM to read and interpret the agent's behaviour in batches of spans.

### Algorithm

```
1. Load a fault bucket file.
2. Identify spans (LLM calls, tool calls, observations) within the bucket.
3. Make a span-identification LLM call to locate the exact spans that
   correspond to fault detection and fault mitigation — these anchor TTD/TTR.
4. Process spans in batches (default batch_size = 15):
   a. Build quantitative extraction prompt with batch content.
   b. LLM returns partial LLMQuantitativeExtraction fields.
   c. Build qualitative extraction prompt with same batch.
   d. LLM returns partial LLMQualitativeExtraction fields.
5. Code-based aggregation (no LLM arithmetic):
   - Sum token counts across batches
   - Compute TTD = detection_time − injection_time (seconds)
   - Compute TTR = mitigation_time − injection_time (seconds)
   - Merge tool_calls lists
6. Assemble final ExtractionResult.
7. Optionally write to MongoDB collection agent_run_metrics.
```

### Key Classes

| Class | File | Role |
|---|---|---|
| `TraceMetricsExtractor` | `scripts/metrics_extractor_from_trace.py` | Orchestrator |
| `QuantitativeAggregator` | `scripts/span_aggregator.py` | Merges quant batches |
| `QualitativeAggregator` | `scripts/span_aggregator.py` | Merges qual batches |
| `LLMQuantitativeExtraction` | `schema/metrics_model.py` | Quant metric schema |
| `LLMQualitativeExtraction` | `schema/metrics_model.py` | Qual metric schema |
| `ExtractionResult` | `schema/data_models.py` | Full per-fault output |
| `TokenUsage` | `schema/data_models.py` | Token count wrapper |

### Quantitative Metrics Extracted

| Field | Type | Description |
|---|---|---|
| `agent_name` | string | Agent identifier |
| `agent_id` | string | Agent unique ID |
| `agent_version` | string | Agent version string |
| `experiment_id` | string | Chaos experiment ID |
| `run_id` | string | Unique run identifier |
| `fault_injection_time` | ISO-8601 | When fault was injected |
| `agent_fault_detection_time` | ISO-8601 | When agent first detected the fault |
| `agent_fault_mitigation_time` | ISO-8601 | When agent completed mitigation |
| `time_to_detect` | float (s) | `detection_time − injection_time` |
| `time_to_mitigate` | float (s) | `mitigation_time − injection_time` |
| `fault_detected` | Yes/No/Unknown | Whether the agent detected the fault |
| `trajectory_steps` | int | Total number of agent steps |
| `input_tokens` | int | Total input tokens used |
| `output_tokens` | int | Total output tokens used |
| `injected_fault_name` | string | Ground-truth fault name |
| `injected_fault_category` | string | Ground-truth fault category |
| `detected_fault_type` | string | Agent's own fault categorisation |
| `fault_target_service` | string | Target service/pod |
| `fault_namespace` | string | Kubernetes namespace |
| `tool_calls` | list | Each tool call with name, args, result, success, timestamp |
| `pii_detection` | bool | Whether PII was detected in any output |
| `number_of_pii_instances_detected` | int | PII instance count |
| `malicious_prompts_detected` | int | Count of detected malicious prompts |
| `tool_selection_accuracy` | float (0–1) | Accuracy against ground-truth tool selection |

### Qualitative Metrics Extracted

| Field | Type | Description |
|---|---|---|
| `rai_check_status` | Passed/Failed/Not Evaluated | Responsible AI compliance |
| `rai_check_notes` | string | Explanation of RAI result |
| `security_compliance_status` | Compliant/Non-Compliant/Partially Compliant/Not Evaluated | Security posture |
| `security_compliance_notes` | string | Explanation |
| `reasoning_quality_score` | float (0–1) | Quality of agent's reasoning chain |
| `reasoning_quality_notes` | string | Explanation |
| `agent_summary` | string | Free-text summary of agent behaviour for this fault |
| `hallucination_score` | float (0–1) | Estimated hallucination rate |
| `plan_adherence` | string | Whether agent followed its own plan |
| `collateral_damage` | string | Side-effects the agent caused |

### Configuration (`metrics_extractor/config/metric_extraction_config.json`)

| Key | Default | Description |
|---|---|---|
| `batch_size` | `15` | Spans per LLM extraction batch |
| `model_name` | — | Azure OpenAI deployment to use |
| `temperature` | — | LLM temperature |
| `max_tokens` | — | Max tokens per LLM call |
| `mongodb_collection` | `agent_run_metrics` | Target MongoDB collection |

---

## Phase 2 — Aggregation

**Module**: `aggregator/`  
**Entry point**: `AggregationOrchestrator.aggregate_all()`  
**Inputs**: Per-run `*_metrics.json` files (or MongoDB collection)  
**Outputs**: `CertificationScorecard` JSON

### Purpose

Phase 2 aggregates the per-run metrics from many individual runs into a single scorecard organised by fault category. Numeric metrics are aggregated with pure functions; textual metrics are synthesised via an LLM Council.

### Algorithm

```
1. Query all per-run documents for the given agent_id
   (DirectoryQueryService for file-based, MetricsQueryService for MongoDB).
2. Group documents by fault_category.
3. For each fault category:
   a. compute_numeric_aggregates() — mean, median, std_dev, p95, min, max, sum
      for: time_to_detect, time_to_mitigate, trajectory_steps,
           input_tokens, output_tokens, tool_selection_accuracy,
           reasoning_quality_score, hallucination_score
   b. compute_derived_rates() — pure ratio calculations:
      - fault_detection_success_rate = detected / total
      - fault_mitigation_success_rate = mitigated / total
      - false_negative_rate = 1 − detection_rate
      - false_positive_rate = false detections / total
      - rai_compliance_rate = passed / total
      - security_compliance_rate = compliant / total
   c. compute_boolean_status_metrics() — PII & hallucination:
      - any_detected (bool)
      - detection_rate (float 0–1)
   d. LLMCouncil.synthesize_consensus() for textual fields:
      - agent_summary, known_limitations, recommendations
4. Assemble into FaultCategoryScorecard per category.
5. Wrap all categories into CertificationScorecard.
```

### LLM Council Pattern

The LLM Council is used for any metric that requires synthesising free-text observations from many runs. It runs in three steps:

```
Step 1: k independent judge calls (default k=3)
        Each judge reads all per-run textual observations
        and produces an independent assessment.

Step 2: Meta-judge call
        Reads all k judge outputs and reconciles disagreements.
        Produces: consensus_summary, severity_label, confidence,
                  inter_judge_agreement score.

Step 3: Result stored in TextualConsensus model.
```

The council is used for: `agent_summary`, `known_limitations`, `recommendations`.

### Key Classes

| Class | File | Role |
|---|---|---|
| `AggregationOrchestrator` | `scripts/aggregation.py` | Top-level pipeline (async) |
| `MetricsQueryService` | `scripts/aggregation.py` | MongoDB query interface |
| `DirectoryQueryService` | `scripts/aggregation.py` | File-based query interface |
| `LLMCouncil` | `scripts/llm_council.py` | k-judge consensus engine |
| `compute_numeric_aggregates` | `scripts/numeric_aggregation.py` | Pure numeric function |
| `compute_derived_rates` | `scripts/numeric_aggregation.py` | Pure rate function |
| `CertificationScorecard` | `schema/data_models.py` | Top-level output model |
| `FaultCategoryScorecard` | `schema/data_models.py` | Per-category model |

### Configuration (`aggregator/config/aggregation_config.json`)

| Key | Default | Description |
|---|---|---|
| `council_size` | `3` | Number of independent LLM judges (`k`) |
| `judge_temperature` | — | Temperature for judge calls |
| `judge_max_tokens` | — | Max tokens per judge call |
| `meta_judge_temperature` | — | Temperature for meta-judge call |
| `rounding_precision` | — | Decimal places for numeric output |

---

## Phase 3 — Certification Report

**Module**: `cert_builder/`  
**Entry point**: `CertificationPipeline.run()`  
**Inputs**: `CertificationScorecard` JSON  
**Outputs**: `CertificationReport` JSON (Pydantic-validated)

Phase 3 is itself composed of four sub-phases.

---

### Phase 3.1 — Ingestion

**Script**: `scripts/ingestion/ingestor.py`

Parses and validates the aggregated scorecard into a `ParsedContext` object. The `ParsedContext` is a structured, in-memory representation that all subsequent builders consume. It organises metrics by fault category and ensures all expected fields are present (with defaults for missing values).

---

### Phase 3.2 — Computation (Deterministic Builders)

**Orchestrator**: `scripts/computation/assembler.py` — `ComputationAssembler`

Six builders run sequentially and deterministically (no LLM calls):

#### 1. Scorecard Builder (`scorecard_builder.py`)

Computes the radar chart dimensions for the executive header. Each dimension is normalised to a 0–1 scale using reference values from `scorecard_config.yaml`. Generates threshold-based findings tagged as `good`, `concern`, or `note`.

Radar dimensions typically include: Detection, Mitigation, Reasoning Quality, Security, RAI Compliance, Tool Accuracy.

#### 2. Table Builder (`table_builder.py`)

Generates 13 data tables from the scorecard metrics. Tables are defined in `table_config.yaml` and cover:
- Numeric metric summaries (mean, median, p95 per fault category)
- Detection and mitigation success matrices
- Token usage breakdowns
- Tool call success rates
- Qualitative metric summaries

Output: list of `TableData(title, headers, rows)`.

#### 3. Chart Builder (`chart_builder.py`) + Chart Renderer (`chart_renderer.py`)

Builds 9 charts defined in `chart_config.yaml`:

| Chart Type | Count | Content |
|---|---|---|
| `radar` | 1 | Normalised scorecard dimensions across categories |
| `grouped_bar` | 3 | Metric comparisons across fault categories |
| `stacked_bar` | 2 | Success/failure breakdowns |
| `heatmap` | 3 | Metric performance matrices |

The renderer converts chart data into a serialisable `ChartData` structure that the report schema can embed directly.

#### 4. Assessment Formatter (`assessment_formatter.py`)

Formats the qualitative assessment outputs from the LLM Council (Phase 2) into structured `AssessmentData` objects. Each assessment carries:
- `rating` — categorical quality rating
- `confidence` — judge confidence level
- `inter_judge_agreement` — float (0–1)
- `body` — synthesised free text

#### 5. Hardcoded Loader (`hardcoded_loader.py`)

Reads static content from `hardcoded_content.yaml`: methodology descriptions, metric definitions, formula explanations, and boilerplate text that appears in every report.

#### 6. Card Builder (`card_builder.py`)

Builds 3 executive summary cards for the report header section. Each card surfaces 3–4 key metrics as label/value pairs for at-a-glance consumption.

---

### Phase 3.3 — Narrative Generation (LLM Builders)

**Orchestrator**: `scripts/narratives/assembler.py` — `NarrativeAssembler`

Six LLM narrative builders generate the textual sections of the report. Five run concurrently (`asyncio.gather`); recommendations runs last because it depends on the limitations output.

| Builder | Script | Section |
|---|---|---|
| Scope Narrative | `scope_narrative_builder.py` | Agent and test scope overview |
| Key Findings | `key_findings_builder.py` | Top 5–7 findings with severity labels |
| Qualitative Findings | `qualitative_builder.py` | Per-category qualitative assessments |
| Fault Analysis | `fault_analysis_builder.py` | Detailed analysis per fault category |
| Limitations | `limitation_builder.py` | Known agent limitations (ranked) |
| Recommendations | `recommendation_builder.py` | Improvement recommendations (depends on limitations) |

Each builder:
1. Reads its prompt template from `cert_builder/prompts/`.
2. Injects computed metrics and council outputs into the template.
3. Calls Azure OpenAI.
4. Returns structured narrative content.

---

### Phase 3.4 — Report Assembly

**Script**: `scripts/report_assembler.py` — `ReportAssembler.assemble()`

Merges all computation and narrative outputs into the final `CertificationReport`. The assembler:
1. Constructs the `meta` block (agent info, run counts, fault categories).
2. Constructs the `header` block (scorecard radar dimensions + findings list).
3. Builds 12 `Section` objects, each with an `id`, `number`, `part`, `title`, `intro`, and `content` list.
4. Validates the entire structure against the Pydantic v2 `CertificationReport` schema.
5. Returns the validated model.

---

## Report Structure

The final `CertificationReport` has this top-level structure:

```
CertificationReport
├── meta
│   ├── agent_name, agent_id, certification_run_id, certification_date
│   ├── total_runs, total_faults, total_categories, runs_per_fault_configured
│   └── categories[]  { name, fault, runs }
├── header
│   ├── scorecard[]   { dimension, value }
│   └── findings[]    { severity, text }
├── sections[]
│   ├── id, number, part, title, intro
│   └── content[]     (see Content Block Types below)
└── footer
```

### Content Block Types

Each element in `section.content` has a `type` discriminator:

| `type` | Fields | Description |
|---|---|---|
| `heading` | `text`, `detail?` | Section or sub-section heading |
| `text` | `body`, `style` (`info`/`warning`) | Prose paragraph |
| `table` | `title`, `headers[]`, `rows[][]` | Data table |
| `findings` | `items[]` `{ severity, text }` | Severity-tagged finding list |
| `assessment` | `rating`, `confidence`, `inter_judge_agreement`, `body` | Qualitative assessment block |
| `card` | `items[]` `{ label, value }` | Key-value summary card |
| `chart` | `chart_type`, `title`, chart-specific data fields | Visualisation |

### Chart Data Shapes

| `chart_type` | Key Fields |
|---|---|
| `radar` | `dimensions[]` `{ label, value }` |
| `grouped_bar` | `categories[]`, `series[]` `{ name, values[] }` |
| `stacked_bar` | `categories[]`, `series[]` `{ name, values[] }` |
| `heatmap` | `x_labels[]`, `y_labels[]`, `matrix[][]` |

---

## CLI Entry Points

### `run_bucketing_and_extraction_pipeline.py`

Runs Phases 0 + 1 end-to-end.

```
usage: python run_bucketing_and_extraction_pipeline.py
       --trace-file <path>      (required) Raw Langfuse trace JSON
       --output-dir <path>      (required) Directory for bucket and metrics files
       [--batch-size <int>]     Events per LLM batch (default: 10)
       [--store]                Write metrics to MongoDB (flag, no value)
```

Output layout:
```
<output-dir>/
├── fault_buckets/<fault_id>_bucket.json   (per fault)
├── metrics/<fault_id>_<run_id>_metrics.json  (per fault)
└── pipeline_summary.json
```

### `run_aggregation_and_certification_pipeline.py`

Runs Phases 2 + 3 end-to-end.

```
usage: python run_aggregation_and_certification_pipeline.py
       --metrics-dir <path>           (required) Directory with *_metrics.json files
       --output-dir <path>            (required) Directory for pipeline outputs
       --agent-id <string>            (required) Agent ID
       --agent-name <string>          (required) Agent display name
       [--certification-run-id <str>] Override certification run ID (default: auto)
       [--runs-per-fault <int>]       Expected runs per fault (default: 30)
       [--debug]                      Persist Phase 3 intermediate outputs
```

Output layout:
```
<output-dir>/
├── aggregated_scorecard_output_<agent_id>.json
├── certification_report_<agent_id>.json
└── pipeline_summary.json
```
