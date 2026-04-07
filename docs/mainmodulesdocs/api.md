# REST API Reference

The certifier exposes two REST endpoints. Both are `POST` routes that mirror the two CLI pipeline invocations.

Base URL: `http://<host>/api/v1`

---

## Authentication

**Status: TBD.**  
Endpoints will be protected behind an authentication mechanism (API key or OAuth token). Currently unauthenticated in development.

---

## Endpoints

### POST `/api/v1/bucketing-extraction`

Run Phase 0 (fault bucketing) and Phase 1 (metrics extraction) on a raw Langfuse trace.

#### Request

**Content-Type:** `multipart/form-data`

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `trace_file` | file (JSON) | **Yes** | — | Raw Langfuse trace JSON. |
| `batch_size` | integer | No | `10` | Events per LLM classification batch. |
| `store_to_mongodb` | boolean | No | `false` | Whether to persist extracted metrics to MongoDB. |

#### Success Response — `200 OK`

```jsonc
{
  "status": "success",
  "summary": {
    "trace_file": "trace_abc.json",
    "run_id": "550e8400-e29b-41d4-a716-446655440000",
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
      "run_id": "550e8400-e29b-41d4-a716-446655440000",
      "fault_name": "disk-fill",
      "quantitative": {
        "agent_name": "string | null",
        "agent_id": "string | null",
        "agent_version": "string | null",
        "experiment_id": "string | null",
        "run_id": "string | null",
        "fault_injection_time": "2026-03-30T10:00:00Z | null",
        "agent_fault_detection_time": "2026-03-30T10:00:12Z | null",
        "agent_fault_mitigation_time": "2026-03-30T10:01:05Z | null",
        "time_to_detect": 12.0,
        "time_to_mitigate": 65.0,
        "fault_detected": "Yes",
        "trajectory_steps": 5,
        "input_tokens": 4000,
        "output_tokens": 1200,
        "injected_fault_name": "disk-fill",
        "injected_fault_category": "io",
        "detected_fault_type": "disk saturation",
        "fault_target_service": "cart-service",
        "fault_namespace": "sock-shop",
        "tool_calls": [
          {
            "tool_name": "kubectl_get",
            "arguments": { "resource": "pods", "namespace": "sock-shop" },
            "response_summary": "Listed 8 running pods.",
            "was_successful": true,
            "timestamp": "2026-03-30T10:00:13Z"
          }
        ],
        "pii_detection": false,
        "number_of_pii_instances_detected": 0,
        "malicious_prompts_detected": 0,
        "tool_selection_accuracy": 0.9
      },
      "qualitative": {
        "rai_check_status": "Passed",
        "rai_check_notes": "No content policy violations detected.",
        "security_compliance_status": "Compliant",
        "security_compliance_notes": "No privilege escalation or unauthorised access observed.",
        "reasoning_quality_score": 0.85,
        "reasoning_quality_notes": "Agent correctly narrowed root cause in three steps.",
        "agent_summary": "The agent detected a disk-fill fault within 12 seconds...",
        "hallucination_score": 0.02,
        "plan_adherence": "Agent followed its stated remediation plan.",
        "collateral_damage": "None observed."
      },
      "token_usage": {
        "input_tokens": 4000,
        "output_tokens": 1200,
        "total_tokens": 5200
      },
      "mongodb_document_id": "507f1f77bcf86cd799439011 | null"
    }
  ]
}
```

#### Error Responses

| Code | Condition | Body |
|---|---|---|
| `400` | No trace file provided | `{"status":"error","message":"No trace file provided."}` |
| `422` | File is not valid JSON | `{"status":"error","message":"Trace file is not valid JSON."}` |
| `500` | Pipeline failure | `{"status":"error","message":"Pipeline failed: <detail>"}` |

---

### POST `/api/v1/aggregation-certification`

Run Phase 2 (aggregation) and Phase 3 (certification report generation) on pre-extracted metrics.

Two request modes are supported, selected by `Content-Type`.

#### Option A — File Upload (`multipart/form-data`)

Upload one or more per-run metrics files produced by `/api/v1/bucketing-extraction`.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `metrics_files` | file[] (JSON) | **Yes** | — | Per-run `*_metrics.json` files. |
| `agent_id` | string | **Yes** | — | Agent ID to aggregate for. |
| `agent_name` | string | **Yes** | — | Agent display name. |
| `runs_per_fault` | integer | No | `30` | Expected runs per fault (used for coverage reporting). |
| `debug` | boolean | No | `false` | Persist intermediate outputs to disk. |

#### Option B — Server-side Directory (`application/json`)

Reference a directory of metrics files already present on the server.

```json
{
  "metrics_dir": "/path/to/metrics",
  "agent_id": "agent-001",
  "agent_name": "SRE Agent",
  "certification_run_id": "optional-override",
  "runs_per_fault": 30,
  "debug": false
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `metrics_dir` | string | **Yes** | — | Absolute server-side path to `*_metrics.json` files. |
| `agent_id` | string | **Yes** | — | Agent ID. |
| `agent_name` | string | **Yes** | — | Agent display name. |
| `certification_run_id` | string | No | auto-generated | Override the certification run identifier. |
| `runs_per_fault` | integer | No | `30` | Expected runs per fault. |
| `debug` | boolean | No | `false` | Persist intermediate outputs. |

#### Success Response — `200 OK`

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
            "consensus_summary": "The agent performed well on network faults...",
            "severity_label": "low",
            "confidence": "high",
            "inter_judge_agreement": 0.91
          },
          "known_limitations": {
            "ranked_items": [
              { "rank": 1, "description": "Slow to detect packet loss under 5%." }
            ]
          },
          "recommendations": {
            "prioritized_items": [
              { "priority": 1, "description": "Add a dedicated network health check tool." }
            ]
          }
        }
      }
    ]
  },
  "certification_report": {
    "meta": {
      "agent_name": "SRE Agent",
      "agent_id": "agent-001",
      "certification_run_id": "run-42",
      "certification_date": "2026-03-30T12:00:00Z",
      "total_runs": 90,
      "total_faults": 9,
      "total_categories": 3,
      "runs_per_fault_configured": 30,
      "categories": [
        { "name": "network", "fault": "pod-network-loss", "runs": 30 }
      ]
    },
    "header": {
      "scorecard": [
        { "dimension": "Detection", "value": 0.93 },
        { "dimension": "Mitigation", "value": 0.87 },
        { "dimension": "Reasoning", "value": 0.81 }
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
        "intro": "Overview of fault detection performance across all categories.",
        "content": [
          { "type": "text", "body": "The agent detected...", "style": "info" },
          {
            "type": "table",
            "title": "Detection Rates by Category",
            "headers": ["Category", "Success Rate", "P95 TTD (s)"],
            "rows": [["network", "93%", "18.0"]]
          },
          {
            "type": "chart",
            "chart_type": "radar",
            "title": "Agent Scorecard",
            "dimensions": [
              { "label": "Detection", "value": 0.93 }
            ]
          }
        ]
      }
    ],
    "footer": "Report generated by AgentCert."
  }
}
```

#### Error Responses

| Code | Condition | Body |
|---|---|---|
| `400` | Missing `agent_id` or `agent_name` | `{"status":"error","message":"agent_id and agent_name are required."}` |
| `404` | No metrics found for the given agent | `{"status":"error","message":"No metric documents found for the given agent_id."}` |
| `500` | Pipeline failure | `{"status":"error","message":"Pipeline failed: <detail>"}` |

---

## Status Codes Summary

| Code | Meaning |
|---|---|
| `200` | Pipeline completed successfully. |
| `400` | Missing or invalid request parameters. |
| `404` | No data found for the given identifiers. |
| `422` | Request body or uploaded file is malformed. |
| `500` | Internal pipeline or server error. |

---

## Content Block Types Reference

Every element in a report section's `content` array carries a `type` discriminator:

| `type` | Required Fields | Optional Fields |
|---|---|---|
| `heading` | `text` | `detail` |
| `text` | `body`, `style` (`info`/`warning`) | — |
| `table` | `headers[]`, `rows[][]` | `title` |
| `findings` | `items[]` (`severity`, `text`) | — |
| `assessment` | `rating`, `confidence`, `inter_judge_agreement`, `body` | — |
| `card` | `items[]` (`label`, `value`) | — |
| `chart` | `chart_type`, `title`, chart-specific fields | — |

### Chart-specific fields by `chart_type`

**`radar`**
```json
{
  "type": "chart",
  "chart_type": "radar",
  "title": "Agent Scorecard",
  "dimensions": [ { "label": "Detection", "value": 0.93 } ]
}
```

**`grouped_bar`**
```json
{
  "type": "chart",
  "chart_type": "grouped_bar",
  "title": "TTD by Fault Category",
  "categories": ["network", "resource", "io"],
  "series": [
    { "name": "mean", "values": [12.5, 9.1, 18.3] },
    { "name": "p95",  "values": [18.0, 14.2, 30.1] }
  ]
}
```

**`stacked_bar`**
```json
{
  "type": "chart",
  "chart_type": "stacked_bar",
  "title": "Detection Outcomes",
  "categories": ["network", "resource", "io"],
  "series": [
    { "name": "detected",     "values": [56, 28, 25] },
    { "name": "not_detected", "values": [4,  2,  5]  }
  ]
}
```

**`heatmap`**
```json
{
  "type": "chart",
  "chart_type": "heatmap",
  "title": "Metric Performance Matrix",
  "x_labels": ["network", "resource", "io"],
  "y_labels": ["TTD", "TTR", "Reasoning"],
  "matrix": [
    [0.93, 0.87, 0.72],
    [0.88, 0.91, 0.69],
    [0.75, 0.80, 0.65]
  ]
}
```
