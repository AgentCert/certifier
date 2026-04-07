# Data Models

All models are defined with Pydantic v2 unless noted as `dataclass`. Models are grouped by the pipeline phase that owns them. All field names, types, and default values are taken directly from the source files.

---

## Phase 0 — Fault Analyzer (`fault_analyzer/schema/data_models.py`)

### `EventClassification`

LLM classifier output for a single trace event.

| Field | Type | Default | Description |
|---|---|---|---|
| `event_id` | `str` | required | Unique identifier of the classified event |
| `related_faults` | `List[str]` | `[]` | Fault IDs this event relates to (a single event can apply to multiple faults) |
| `fault_detected` | `Optional[str]` | `None` | If this event is the first detection of a fault, the fault name; `None` otherwise |
| `detected_fault_severity` | `Optional[str]` | `None` | Severity if `fault_detected` is set (e.g. `"critical"`, `"high"`) |
| `detected_fault_target_pod` | `Optional[str]` | `None` | Target pod/resource if `fault_detected` is set |
| `detected_fault_namespace` | `Optional[str]` | `None` | Kubernetes namespace if `fault_detected` is set |
| `detected_fault_signals` | `List[str]` | `[]` | Symptoms that led to detection (e.g. `"CrashLoopBackOff"`) |
| `fault_mitigated` | `Optional[str]` | `None` | Fault name/ID if this event confirms successful remediation; `None` otherwise |
| `has_quantitative_value` | `bool` | `False` | Whether the event contains a measurable numeric value |
| `has_qualitative_value` | `bool` | `False` | Whether the event contains a subjective/descriptive assessment |
| `has_cost_token_details` | `bool` | `False` | Whether the event contains LLM cost or token usage information |
| `confidence` | `float` | `0.0` | Confidence score (0–1) for the classification |

### `BatchClassificationResult`

Wrapper for a batch of LLM-classified events.

| Field | Type | Description |
|---|---|---|
| `classifications` | `List[EventClassification]` | One entry per event in the batch |

### `FaultBucket` (dataclass)

Container representing the complete lifecycle of one fault as seen by the agent.

| Field | Type | Default | Description |
|---|---|---|---|
| `fault_id` | `str` | required | Unique identifier (e.g. `"disk-fill-0"`) |
| `fault_name` | `str` | required | Human-readable fault name |
| `severity` | `Optional[str]` | `None` | Ground-truth severity label |
| `target_pod` | `Optional[str]` | `None` | Kubernetes pod targeted by the fault |
| `namespace` | `Optional[str]` | `None` | Kubernetes namespace |
| `detection_signals` | `List[str]` | `[]` | Symptoms that triggered detection |
| `events` | `List[Dict]` | `[]` | Chronological list of classified events in this bucket |
| `status` | `str` | `"active"` | `"active"` or `"closed"` |
| `detected_at` | `Optional[str]` | `None` | ISO-8601 string when agent first detected the fault |
| `mitigated_at` | `Optional[str]` | `None` | ISO-8601 string when agent completed mitigation |
| `injection_timestamp` | `Optional[str]` | `None` | ISO-8601 string when fault was injected (from FAULT_DATA) |
| `ground_truth` | `Optional[Dict]` | `None` | Ground-truth dict from FAULT_DATA (includes ideal course of action) |
| `ideal_course_of_action` | `Optional[List]` | `None` | Ideal remediation steps |
| `ideal_tool_usage_trajectory` | `Optional[List]` | `None` | Expected tool call sequence |
| `agent_id` | `Optional[str]` | `None` | Agent identifier |
| `agent_name` | `Optional[str]` | `None` | Agent display name |
| `agent_version` | `Optional[str]` | `None` | Agent version |
| `experiment_id` | `Optional[str]` | `None` | Chaos experiment identifier |
| `run_id` | `Optional[str]` | `None` | Unique run UUID |

---

## Phase 1 — Metrics Extractor (`metrics_extractor/schema/`)

### `ToolCall` (Pydantic, `metrics_model.py`)

Represents a single tool call made by the agent.

| Field | Type | Default | Description |
|---|---|---|---|
| `tool_name` | `str` | required | Name of the tool called |
| `arguments` | `Optional[Dict[str, Any]]` | `None` | Arguments passed to the tool |
| `response_summary` | `Optional[str]` | `None` | Summary of the tool response |
| `was_successful` | `bool` | `True` | Whether the tool call succeeded |
| `timestamp` | `Optional[str]` | `None` | ISO-8601 timestamp of the call |

### `LLMQuantitativeExtraction` (Pydantic, `metrics_model.py`)

All numeric and structured metrics extracted by the LLM for one fault run. Note: `tool_calls` is stored as `List[Dict[str, Any]]` in the extracted output, not as `List[ToolCall]`.

| Field | Type | Default | Description |
|---|---|---|---|
| `agent_name` | `Optional[str]` | `None` | — |
| `agent_id` | `Optional[str]` | `None` | — |
| `agent_version` | `Optional[str]` | `None` | — |
| `experiment_id` | `Optional[str]` | `None` | Chaos experiment identifier |
| `run_id` | `Optional[str]` | `None` | Unique run UUID |
| `fault_injection_time` | `Optional[str]` | `None` | ISO-8601 when fault was injected |
| `agent_fault_detection_time` | `Optional[str]` | `None` | ISO-8601 when agent first detected the fault |
| `agent_fault_mitigation_time` | `Optional[str]` | `None` | ISO-8601 when agent completed mitigation |
| `time_to_detect` | `Optional[float]` | `None` | Seconds from injection to detection |
| `time_to_mitigate` | `Optional[float]` | `None` | Seconds from injection to mitigation |
| `fault_detected` | `str` | `"Unknown"` | `"Yes"` / `"No"` / `"Unknown"` |
| `trajectory_steps` | `int` | `0` | Total agent steps |
| `input_tokens` | `int` | `0` | Total input tokens |
| `output_tokens` | `int` | `0` | Total output tokens |
| `injected_fault_name` | `Optional[str]` | `None` | Ground-truth fault name |
| `injected_fault_category` | `Optional[str]` | `None` | Ground-truth category |
| `detected_fault_type` | `Optional[str]` | `None` | Agent's own fault categorisation |
| `fault_target_service` | `Optional[str]` | `None` | Target Kubernetes service |
| `fault_namespace` | `Optional[str]` | `None` | Kubernetes namespace |
| `tool_calls` | `List[Dict[str, Any]]` | `[]` | List of tool call records |
| `pii_detection` | `Optional[bool]` | `None` | Whether PII was found in any output |
| `number_of_pii_instances_detected` | `Optional[int]` | `None` | PII instance count |
| `malicious_prompts_detected` | `Optional[int]` | `None` | Count of detected prompt injections |
| `tool_selection_accuracy` | `Optional[float]` | `None` | Correct tools / total tools (0–1) |

### `LLMQualitativeExtraction` (Pydantic, `metrics_model.py`)

| Field | Type | Default | Description |
|---|---|---|---|
| `rai_check_status` | `str` | `"Not Evaluated"` | `"Passed"` / `"Failed"` / `"Not Evaluated"` |
| `rai_check_notes` | `Optional[str]` | `None` | Explanation |
| `security_compliance_status` | `str` | `"Not Evaluated"` | `"Compliant"` / `"Non-Compliant"` / `"Partially Compliant"` / `"Not Evaluated"` |
| `security_compliance_notes` | `Optional[str]` | `None` | Explanation |
| `reasoning_quality_score` | `Optional[float]` | `None` | **0–10 scale** — reasoning depth, logical coherence, explanation quality, diagnostic soundness |
| `reasoning_quality_notes` | `Optional[str]` | `None` | Narrative assessment |
| `agent_summary` | `str` | `""` | Concise summary of agent actions, findings, and remediation |
| `hallucination_score` | `Optional[float]` | `None` | 0–1; lower = fewer hallucinations |
| `plan_adherence` | `Optional[str]` | `None` | Whether agent followed a systematic troubleshooting approach |
| `collateral_damage` | `Optional[str]` | `None` | Unintended side-effects of agent actions |

### `TokenUsage` (dataclass, `data_models.py`)

| Field | Type | Default |
|---|---|---|
| `input_tokens` | `int` | `0` |
| `output_tokens` | `int` | `0` |
| `total_tokens` | `int` | `0` |

### `ExtractionResult` (dataclass, `data_models.py`)

Result of metrics extraction for one fault run. Does **not** include `fault_id` / `run_id` / `fault_name` — those fields are added by the pipeline orchestrator when it writes the per-fault metrics JSON file.

| Field | Type | Default | Description |
|---|---|---|---|
| `quantitative` | `LLMQuantitativeExtraction` | required | Numeric metrics |
| `qualitative` | `LLMQualitativeExtraction` | required | Qualitative metrics |
| `token_usage` | `TokenUsage` | `TokenUsage()` | Token counts |
| `mongodb_document_id` | `Optional[str]` | `None` | MongoDB `_id` if stored |

> **Note**: The per-fault JSON files written to disk contain additional top-level keys (`fault_id`, `run_id`, `fault_name`) added by the pipeline script. These are not part of `ExtractionResult` itself.

---

## Phase 2 — Aggregator (`aggregator/schema/data_models.py`)

### `StatsSummary`

Statistical summary for one numeric metric.

| Field | Type | Description |
|---|---|---|
| `mean` | `Optional[float]` | Arithmetic mean |
| `median` | `Optional[float]` | 50th percentile |
| `std_dev` | `Optional[float]` | Standard deviation |
| `p95` | `Optional[float]` | 95th percentile |
| `min` | `Optional[float]` | Minimum |
| `max` | `Optional[float]` | Maximum |
| `sum` | `Optional[float]` | Sum |
| `mode` | `Optional[float]` | Mode |
| `unit` | `Optional[str]` | Human-readable unit (e.g. `"seconds"`) |
| `scale` | `Optional[str]` | `"lower_is_better"` or `"higher_is_better"` |

### `DetectionStatus`

Boolean detection aggregate (used for PII and hallucination).

| Field | Type | Description |
|---|---|---|
| `any_detected` | `Optional[bool]` | True if at least one run triggered detection |
| `detection_rate` | `Optional[float]` | Fraction of runs that triggered detection |

### `BooleanAggregates`

| Field | Type | Description |
|---|---|---|
| `pii_detection` | `DetectionStatus` | PII detection summary |
| `hallucination_detection` | `DetectionStatus` | Hallucination detection summary |

### `DerivedRates`

| Field | Type | Description |
|---|---|---|
| `fault_detection_success_rate` | `Optional[float]` | Fraction of runs where fault was detected |
| `fault_mitigation_success_rate` | `Optional[float]` | Fraction of runs where fault was mitigated |
| `false_negative_rate` | `Optional[float]` | `1 − fault_detection_success_rate` |
| `false_positive_rate` | `Optional[float]` | Fraction of runs with false detection |
| `rai_compliance_rate` | `Optional[float]` | Fraction of runs passing RAI check |
| `security_compliance_rate` | `Optional[float]` | Fraction of runs that are security compliant |

### `TextualConsensus`

LLM Council consensus output for a textual metric.

| Field | Type | Default | Description |
|---|---|---|---|
| `consensus_summary` | `str` | `""` | Reconciled summary from meta-judge |
| `severity_label` | `Optional[str]` | `None` | Categorical severity |
| `confidence` | `Optional[str]` | `None` | Judge confidence level |
| `inter_judge_agreement` | `Optional[float]` | `None` | Agreement score (0–1) |

### `RankedLimitation`

| Field | Type | Default | Description |
|---|---|---|---|
| `limitation` | `str` | required | Limitation description |
| `frequency` | `int` | `0` | How often this limitation appeared across runs |
| `severity` | `str` | `"Medium"` | `"High"` / `"Medium"` / `"Low"` |

### `PrioritizedRecommendation`

| Field | Type | Default | Description |
|---|---|---|---|
| `recommendation` | `str` | required | Recommendation description |
| `priority` | `str` | `"Medium"` | `"Critical"` / `"High"` / `"Medium"` / `"Low"` |
| `frequency` | `int` | `0` | How often this recommendation surfaced |

### `KnownLimitations`

| Field | Type | Description |
|---|---|---|
| `ranked_items` | `List[RankedLimitation]` | Limitations list |

### `Recommendations`

| Field | Type | Description |
|---|---|---|
| `prioritized_items` | `List[PrioritizedRecommendation]` | Recommendations list |

### `FaultCategoryScorecard`

Aggregated scorecard for one fault category. The metric sub-fields are stored as plain dicts (not typed models) to accommodate flexible schema evolution.

| Field | Type | Description |
|---|---|---|
| `fault_category` | `str` | Category name |
| `faults_tested` | `List[str]` | Individual faults tested in this category |
| `total_runs` | `int` | Total runs in this category |
| `numeric_metrics` | `Dict[str, Dict[str, Any]]` | One `StatsSummary`-shaped dict per numeric metric |
| `derived_metrics` | `Dict[str, Optional[float]]` | `DerivedRates`-shaped dict |
| `boolean_status_metrics` | `Dict[str, Any]` | `BooleanAggregates`-shaped dict |
| `textual_metrics` | `Dict[str, Any]` | Council-synthesised textual metrics |

### `CertificationScorecard`

Top-level Phase 2 output.

| Field | Type | Default | Description |
|---|---|---|---|
| `agent_id` | `str` | `""` | Agent identifier |
| `agent_name` | `str` | `""` | Agent display name |
| `certification_run_id` | `str` | `""` | Unique certification run ID |
| `created_at` | `str` | UTC ISO-8601 now | Creation timestamp (string) |
| `total_runs` | `int` | `0` | Total runs across all categories |
| `total_faults_tested` | `int` | `0` | Total distinct faults tested |
| `total_fault_categories` | `int` | `0` | Number of fault categories |
| `runs_per_fault` | `int` | `30` | Expected runs per fault |
| `fault_category_scorecards` | `List[FaultCategoryScorecard]` | `[]` | One per fault category |

---

## Phase 3 — Cert Builder (`cert_builder/schema/`)

### `CertificationReport` (Pydantic v2, `certification_schema.py`)

Root model. `extra="forbid"` — no unexpected fields allowed.

| Field | Type | Description |
|---|---|---|
| `meta` | `Meta` | Agent and run metadata |
| `header` | `Header` | Executive scorecard and findings |
| `sections` | `list[Section]` | Report sections (min 1) |
| `footer` | `str` | Footer text (min length 1) |

### `Meta`

| Field | Type | Description |
|---|---|---|
| `agent_name` | `str` | — |
| `agent_id` | `str` | — |
| `certification_run_id` | `str` | — |
| `certification_date` | `str` | Date string (min length 1) |
| `subtitle` | `str` | Report subtitle (min length 1) |
| `total_runs` | `int` | — |
| `total_faults` | `int` | — |
| `total_categories` | `int` | — |
| `runs_per_fault_configured` | `int` | — |
| `categories` | `list[CategoryMeta]` | Per-category summary (min 1) |

### `CategoryMeta`

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Category display name |
| `fault` | `str` | Representative fault name |
| `runs` | `int` | Number of runs (≥ 0) |

### `Header`

| Field | Type | Description |
|---|---|---|
| `scorecard` | `list[ScorecardDimension]` | Radar chart dimensions (min 1) |
| `findings` | `list[FindingItem]` | Top-level findings (min 1) |

### `ScorecardDimension`

| Field | Type | Constraint |
|---|---|---|
| `dimension` | `str` | min_length=1 |
| `value` | `float` | 0.0 ≤ value ≤ 1.0 |

### `FindingItem`

| Field | Type | Values |
|---|---|---|
| `severity` | `FindingSeverity` (enum) | `"concern"` / `"good"` / `"note"` |
| `text` | `str` | min_length=1 |

### `Section`

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Section identifier (min_length=1) |
| `number` | `int` | Section number (≥ 1) |
| `part` | `SectionPart \| None` | `"Agent Capability Assessment"` / `"Fault Injection Analysis"` / `None` |
| `title` | `str` | Section title |
| `intro` | `str` | Introductory paragraph |
| `content` | `list[ContentBlock]` | Discriminated-union content items (min 1) |

### Content Block Types (Discriminated Union on `type`)

#### `HeadingBlock`
```
type: "heading"    (Literal)
title: str         (min_length=1)
detail: str | None
```

#### `TextBlock`
```
type: "text"       (Literal)
body: str          (min_length=1)
style: "info" | "warning" | None
```

#### `TableBlock`
```
type: "table"      (Literal, default)
title: str | None
headers: list[str] (min_length=1)
rows: list[list[Any]] (min_length=1; each row must match len(headers))
```

#### `FindingsBlock`
```
type: "findings"   (Literal)
items: list[FindingItem]  (min_length=1)
```

#### `AssessmentBlock`
```
type: "assessment" (Literal, default)
title: str         (min_length=1)
rating: Rating | None   ("Strong"|"Clean"|"Moderate"|"Minor"|"Significant"|None)
confidence: Confidence  ("High"|"Medium"|"Low")
agreement: float | str  (0.0–1.0 or display string)
body: str          (min_length=1)
```

#### `CardBlock`
```
type: "card"       (Literal, default)
title: str | None
items: list[CardItem]  (min_length=1)
  CardItem.label: str
  CardItem.value: str | int
```

#### Chart Blocks (all have `type: "chart"`)

Resolution uses a two-level discriminator: `type` + `chart_type`.

**`RadarChartBlock`** (`chart_type: "radar"`):
```
type: "chart"
chart_type: "radar"
title: str
dimensions: list[ScorecardDimension]  (min_length=1)
```

**`GroupedBarChartBlock`** (`chart_type: "grouped_bar"`):
```
type: "chart"
chart_type: "grouped_bar"
title: str
categories: list[str]    (min_length=1)
series: list[BarSeries]  (min_length=1)
y_axis: str              (required — axis label)
reference_lines: list[ReferenceLine] | None
```

**`StackedBarChartBlock`** (`chart_type: "stacked_bar"`):
```
type: "chart"
chart_type: "stacked_bar"
title: str
categories: list[str]
series: list[BarSeries]
y_axis: str   (required)
```

**`HeatmapChartBlock`** (`chart_type: "heatmap"`):
```
type: "chart"
chart_type: "heatmap"
title: str
x_labels: list[str]
y_labels: list[str]
values: list[list[float | None]]   (note: "values" not "matrix")
scale: list[float]                 (colour scale breakpoints, min_length=1)
```

---

## Intermediate Phase 3 Models (`cert_builder/schema/intermediate.py`)

Internal models that validate builder outputs before final assembly. Not part of the certified report schema.

| Model | Produced by | Key fields |
|---|---|---|
| `Scorecard` | — | `dimensions: list[ScorecardDimension]`, `normalized_per_category: list[dict]` |
| `ScorecardResult` | Scorecard Builder | `scorecard: Scorecard`, `findings: list[FindingItem]` |
| `TablesResult` | Table Builder | `tables: dict[str, TableData]` (13 tables) |
| `HeatmapChart` | Chart Builder | Extends `HeatmapChartData` with `display_values: list[list[Any]] \| None` |
| `ChartsResult` | Chart Builder | `charts: dict[str, ChartModel]` (9 charts) |
| `AssessmentsResult` | Assessment Formatter | `assessments: dict[str, list[AssessmentData]]` |
| `HardcodedContent` | Hardcoded Loader | `definitions`, `normalization`, `statistics`, `section_intros`, `methodology_bullets` |
| `HardcodedResult` | Hardcoded Loader | `hardcoded: HardcodedContent` |
| `CardsResult` | Card Builder | `cards: dict[str, CardData]` (3 cards) |
| `ComputedContent` | ComputationAssembler | All 6 builder outputs merged: `scorecard`, `findings`, `tables`, `charts`, `assessments`, `hardcoded`, `cards` |

### `ParsedContext` (dataclass, `ingestion/ingestor.py`)

Output of Phase 3.1 ingestion. Passed as input to all Phase 3.2 builders.

| Field | Type | Description |
|---|---|---|
| `meta` | `dict[str, Any]` | Agent metadata and run counts |
| `categories` | `list[dict[str, Any]]` | Per-category data (numeric, derived, boolean, textual metrics) |
| `warnings` | `list[str]` | Non-fatal issues encountered during ingestion |
