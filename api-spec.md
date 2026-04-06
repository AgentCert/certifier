# AgentCert API Specification

Two REST API endpoints that expose the existing AgentCert pipelines.

---

## 1. POST `/api/v1/bucketing-extraction`

Run fault bucketing on a raw Langfuse trace, then extract per-fault metrics from each bucket.

### Request

**Content-Type:** `multipart/form-data`

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `trace_file` | file (JSON) | **Yes** | — | Raw Langfuse trace JSON file. |
| `batch_size` | integer | No | `10` | Number of events per LLM classification batch. |
| `store_to_mongodb` | boolean | No | `false` | Whether to persist extracted metrics to MongoDB. |

### Response

**Content-Type:** `application/json`

**Success — `200 OK`**

```jsonc
{
  "status": "success",
  "summary": {
    "trace_file": "trace_abc.json",
    "run_id": "string",
    "total_faults": 3,
    "faults_extracted": 3,
    "bucketing_tokens": {
      "input": 12000,
      "output": 3400,
      "total": 15400
    },
    "extraction_tokens": {
      "input": 8000,
      "output": 2200,
      "total": 10200
    }
  },
  "results": [
    {
      "fault_id": "disk-fill",
      "run_id": "uuid-string",
      "fault_name": "disk-fill",
      "quantitative": {
        "agent_name": "string | null",
        "agent_id": "string | null",
        "agent_version": "string | null",
        "experiment_id": "string | null",
        "run_id": "string | null",
        "fault_injection_time": "ISO-8601 | null",
        "agent_fault_detection_time": "ISO-8601 | null",
        "agent_fault_mitigation_time": "ISO-8601 | null",
        "time_to_detect": "float | null  (seconds)",
        "time_to_mitigate": "float | null  (seconds)",
        "fault_detected": "Yes | No | Unknown",
        "trajectory_steps": 5,
        "input_tokens": 4000,
        "output_tokens": 1200,
        "injected_fault_name": "string | null",
        "injected_fault_category": "string | null",
        "detected_fault_type": "string | null",
        "fault_target_service": "string | null",
        "fault_namespace": "string | null",
        "tool_calls": [
          {
            "tool_name": "kubectl_get",
            "arguments": {},
            "response_summary": "string | null",
            "was_successful": true,
            "timestamp": "ISO-8601 | null"
          }
        ],
        "pii_detection": "boolean | null",
        "number_of_pii_instances_detected": "integer | null",
        "malicious_prompts_detected": "integer | null",
        "tool_selection_accuracy": "float (0-1) | null"
      },
      "qualitative": {
        "rai_check_status": "Passed | Failed | Not Evaluated",
        "rai_check_notes": "string | null",
        "security_compliance_status": "Compliant | Non-Compliant | Partially Compliant | Not Evaluated",
        "security_compliance_notes": "string | null",
        "reasoning_quality_score": "float (0-1) | null",
        "reasoning_quality_notes": "string | null",
        "agent_summary": "string",
        "hallucination_score": "float (0-1) | null",
        "plan_adherence": "string | null",
        "collateral_damage": "string | null"
      },
      "token_usage": {
        "input_tokens": 4000,
        "output_tokens": 1200,
        "total_tokens": 5200
      },
      "mongodb_document_id": "string | null"
    }
  ]
}
```

**Error — `400 Bad Request`**

```json
{
  "status": "error",
  "message": "No trace file provided."
}
```

**Error — `422 Unprocessable Entity`**

```json
{
  "status": "error",
  "message": "Trace file is not valid JSON."
}
```

**Error — `500 Internal Server Error`**

```json
{
  "status": "error",
  "message": "Pipeline failed: <detail>"
}
```

---

## 2. POST `/api/v1/aggregation-certification`

Aggregate per-run metrics into a scorecard, then run the certification framework to produce the final report.

### Request

**Content-Type:** `multipart/form-data` or `application/json`

When using `multipart/form-data`, metric files are uploaded directly. When using `application/json`, a pre-existing directory path on the server is referenced.

#### Option A — File Upload (`multipart/form-data`)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `metrics_files` | file[] (JSON) | **Yes** | — | One or more per-run `*_metrics.json` files produced by the bucketing-extraction pipeline. |
| `agent_id` | string | **Yes** | — | Agent ID to aggregate metrics for. |
| `agent_name` | string | **Yes** | — | Agent name for the certification scorecard. |
| `runs_per_fault` | integer | No | `30` | Expected number of runs per fault. |
| `debug` | boolean | No | `false` | Persist intermediate outputs for debugging. |

#### Option B — Server-side Directory (`application/json`)

```json
{
  "metrics_dir": "/path/to/metrics",
  "agent_id": "agent-001",
  "agent_name": "SRE Agent",
  "certification_run_id": "",
  "runs_per_fault": 30,
  "debug": false
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `metrics_dir` | string | **Yes** | — | Server-side directory containing per-run `*_metrics.json` files. |
| `agent_id` | string | **Yes** | — | Agent ID to aggregate metrics for. |
| `agent_name` | string | **Yes** | — | Agent name for the certification scorecard. |
| `runs_per_fault` | integer | No | `30` | Expected number of runs per fault. |
| `debug` | boolean | No | `false` | Persist intermediate outputs for debugging. |

### Response

**Content-Type:** `application/json`

**Success — `200 OK`**

```jsonc
{
  "status": "success",
  "summary": {
    "agent_id": "agent-001",
    "agent_name": "SRE Agent",
    "certification_run_id": "run-42",
    "total_documents": 90,
    "total_fault_categories": 3,
    "fault_categories": ["network", "resource", "io"]
  },
  "aggregated_scorecard": {
    "agent_id": "agent-001",
    "agent_name": "SRE Agent",
    "certification_run_id": "run-42",
    "created_at": "2026-03-30T12:00:00Z",
    "total_runs": 90,
    "total_faults_tested": 9,
    "total_fault_categories": 3,
    "runs_per_fault": 30,
    "fault_category_scorecards": [
      {
        "fault_category": "network",
        "faults_tested": ["pod-network-loss", "pod-network-latency"],
        "total_runs": 60,
        "numeric_metrics": {
          "time_to_detect": {
            "mean": 12.5,
            "median": 11.0,
            "std_dev": 3.2,
            "p95": 18.0,
            "min": 5.0,
            "max": 22.0,
            "sum": 750.0,
            "unit": "seconds",
            "scale": "lower_is_better"
          }
          // ... other numeric metrics
        },
        "derived_metrics": {
          "fault_detection_success_rate": 0.93,
          "fault_mitigation_success_rate": 0.87,
          "false_negative_rate": 0.07,
          "false_positive_rate": 0.02,
          "rai_compliance_rate": 1.0,
          "security_compliance_rate": 0.98
        },
        "boolean_status_metrics": {
          "pii_detection": { "any_detected": false, "detection_rate": 0.0 },
          "hallucination_detection": { "any_detected": true, "detection_rate": 0.03 }
        },
        "textual_metrics": {
          "agent_summary": {
            "consensus_summary": "string",
            "severity_label": "string | null",
            "confidence": "string | null",
            "inter_judge_agreement": "float | null"
          },
          "known_limitations": { "ranked_items": [] },
          "recommendations": { "prioritized_items": [] }
        }
      }
    ]
  },
  "certification_report": {
    "meta": {
      "categories": [
        { "name": "network", "fault": "pod-network-loss", "runs": 30 }
      ]
    },
    "header": {
      "scorecard": [
        { "dimension": "Detection", "value": 0.93 },
        { "dimension": "Mitigation", "value": 0.87 }
      ],
      "findings": [
        { "severity": "good", "text": "High detection rate across all categories." },
        { "severity": "concern", "text": "Mitigation time exceeds SLA for IO faults." }
      ]
    },
    "sections": [
      {
        "id": "section-1",
        "number": 1,
        "part": "A",
        "title": "Detection Analysis",
        "intro": "Overview of fault detection performance.",
        "content": [
          { "type": "text", "body": "...", "style": "info" },
          { "type": "table", "title": "Detection Rates", "headers": [], "rows": [] },
          { "type": "chart", "chart_type": "radar", "title": "...", "dimensions": [] }
          // ... other ContentBlock types
        ]
      }
    ],
    "footer": "Report generated by AgentCert."
  }
}
```

**Error — `400 Bad Request`**

```json
{
  "status": "error",
  "message": "agent_id and agent_name are required."
}
```

**Error — `404 Not Found`**

```json
{
  "status": "error",
  "message": "No metric documents found for the given agent_id."
}
```

**Error — `500 Internal Server Error`**

```json
{
  "status": "error",
  "message": "Pipeline failed: <detail>"
}
```

---

## Common Details

### Authentication

TBD — endpoints should be protected behind an authentication mechanism (e.g. API key, OAuth token).

### Content Block Types (Certification Report)

The `content` array inside each certification report section uses a discriminated union on the `type` field:

| `type` | Description |
|---|---|
| `heading` | Section heading with optional detail text. |
| `text` | Prose paragraph. `style`: `info` or `warning`. |
| `table` | Data table with `headers` and `rows`. |
| `findings` | List of finding items with severity. |
| `assessment` | Qualitative assessment with rating and confidence. |
| `card` | Key-value card with label/value pairs. |
| `chart` | Visualization. `chart_type` one of: `radar`, `grouped_bar`, `stacked_bar`, `heatmap`. |

### Status Codes Summary

| Code | Meaning |
|---|---|
| `200` | Pipeline completed successfully. |
| `400` | Missing or invalid request parameters. |
| `404` | No data found for the given identifiers. |
| `422` | Request body/file is malformed. |
| `500` | Internal pipeline or server error. |
