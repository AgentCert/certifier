# Chaoscenter Trace Schema — Certifier Consumer Reference

| | |
|---|---|
| **Status** | Stable |
| **Owner** | AgentCert producer team |
| **Audience** | Certifier / data science consumers |
| **Last updated** | 2026-05-07 |
| **Reference fixture** | `traceId 43bf1cdf-b23e-4940-b2f1-084a85308114` (`argowf-chaos-sock-shop-parallel`, 3-fault parallel workflow) |

## Purpose

This document specifies the schema of chaoscenter experiment-run traces
as observed in Langfuse. It enumerates every span the certifier will
encounter, the keys those spans carry, and the semantics of each value.

It is the contract between the AgentCert producer and the certifier
consumer. Build extractors and scoring rules directly against this
specification.

## Consumption model

The certifier consumes the **final, fully-populated trace** after the
experiment run has completed. The producer assembles the trace
incrementally during the run (creating spans, then updating them in place
as data becomes available); the certifier never observes intermediate
states. By the time a trace is read:

- Every span listed in §1 is present.
- Every `fault: <name>` span carries every field whose source data was
  non-empty at any point during the run.
- A field absent from the final trace was never populated — for example,
  `fault.injection_end_timestamp` will be absent only if the fault never
  reached completion.

Read each observation once. There is no need to poll, page, or merge
multiple records per fault.

## Contents

1. [Trace structure](#1-trace-structure)
2. [Identifiers](#2-identifiers)
3. [Fault span schema](#3-fault-span-schema)
4. [Embedded ground truth](#4-embedded-ground-truth)
5. [Auxiliary span schemas](#5-auxiliary-span-schemas)
6. [Reference example](#6-reference-example)
7. [Per-fault scoring inputs](#7-per-fault-scoring-inputs)

---

## 1. Trace structure

Each chaoscenter experiment run produces exactly one Langfuse trace
containing six observation types:

```
Langfuse trace
│
├── experiment-triggered     SPAN          1 per run
├── experiment_context       SPAN          1 per run
├── workflow-step: <step>    SPAN          N per run (one per workflow node update)
├── fault: <faultName>       SPAN          1 per fault, upserted
├── litellm-acompletion      GENERATION    M per run (one per agent LLM call)
└── completion: <expName>    EVENT         1 per run
```

All observations share the same `traceId` and attach directly to the
trace root: `parentObservationId == null`. There is no nesting between
observation types.

**Reference example.** The fixture run carries 44 observations:
1 `experiment-triggered`, 1 `experiment_context`, 13 `workflow-step:`,
3 `fault:`, 25 `litellm-acompletion`, and 1 `completion:`.

---

## 2. Identifiers

| Identifier | Location | Definition |
|---|---|---|
| `traceId` | top-level on every observation | Join key for all observations within one run. Equals the agent-emitted `notifyID` (UUID with dashes). Falls back to `experimentRunID` when `notifyID` is absent. |
| `notifyID` | identical to `traceId` | The canonical run identifier, propagated by the agent. |
| `experimentRunID` | `experiment.run_id` on `workflow-step:` spans | The workflow run's Kubernetes UID. Surfaced in the chaoscenter UI. |
| `experimentID` | `experiment.id` on `experiment-triggered` and `workflow-step:` spans | The chaoscenter experiment template (the definition, not the run). |
| Trace name | top-level `name` on the trace | `"<experimentName>:<experimentRunID>"`. |
| Fault span ID | `id` on each `fault:` observation | `"<traceID>-fault-<faultName>"`. Deterministic and stable across upserts. |

To enumerate every fault span in a run:

```
filter: traceId == <run trace id>  AND  name STARTS WITH "fault: "
```

---

## 3. Fault span schema

### 3.1 Top-level Langfuse fields

| Field | Value |
|---|---|
| `name` | `"fault: <faultName>"`. The fault name is the canonical fault identifier and is never aliased. |
| `id` | `"<traceID>-fault-<faultName>"`. Deterministic; stable across upserts. |
| `type` | `"SPAN"` |
| `traceId` | The run trace ID. |
| `parentObservationId` | `null` |
| `startTime` | ISO-8601 UTC. Equal to `fault.injection_timestamp`. |
| `endTime` | ISO-8601 UTC. Equal to `startTime` while the fault is still active; equal to `fault.injection_end_timestamp` once the fault completes. |
| `level` | `"DEFAULT"` |

### 3.2 Body

```json
"input":  { "fault_name": "<name>", "ground_truth": { /* §4 */ } }
"output": { "status": "injected" }
```

`output.status` is a constant marker indicating the span anchors the
fault bucket. It does **not** convey the fault outcome — see
`fault.injection.verdict` in §3.3.

### 3.3 `metadata.attributes`

The certifier consumes this dictionary. **All keys are omit-empty:**
when the underlying source value is empty, the key is absent rather than
present-with-empty-string. Branch on absence (`key not in attrs`), not on
sentinels (`attrs[key] == ""` or `0`).

#### Identity

Always present.

| Key | Type | Description |
|---|---|---|
| `fault.name` | string | Canonical fault name; the key for ground-truth lookup. |
| `fault.engine_name` | string | Internal fault-instance identifier. Often suffixed with a randomised string for uniqueness (e.g. `pod-cpu-hogpw59s`). Useful for narrative / debugging only — use `fault.name` for keying. |
| `fault.namespace` | string | The infrastructure namespace where the fault is orchestrated (typically `litmus`). Distinct from the target application namespace. |
| `fault.status` | string | Constant `"injected"`. Bucket-anchor marker. |
| `fault.injection_timestamp` | ISO-8601 UTC | The chaos window start. Equal to the span's `startTime`. |

#### Target

`fault.target_namespace` is always present. `fault.target_label` and
`fault.target_kind` are present in normal runs and absent in the
degraded case described below.

| Key | Type | Description |
|---|---|---|
| `fault.target_namespace` | string | The application namespace under chaos (e.g. `sock-shop`). |
| `fault.target_label` | string | Label selector in `<key>=<value>` form (e.g. `name=carts`). |
| `fault.target_kind` | string | Lowercase Kubernetes kind: `deployment`, `statefulset`, `daemonset`, etc. |

> **Degraded case.** If `fault.target_namespace == fault.namespace`
> and `fault.target_label` is absent, the target metadata could not be
> resolved for this fault. Use `fault.target.workload_ref` or
> `fault.engine_name` for attribution.

#### Window

| Key | Type | Domain | Description |
|---|---|---|---|
| `fault.injection_end_timestamp` | ISO-8601 UTC | — | Observed end of the fault. Absent only if the fault never completed during the run. |
| `fault.timing.total_chaos_duration_sec` | int (>0) | seconds | Configured chaos duration (sourced from the `TOTAL_CHAOS_DURATION` / `CHAOS_DURATION` env on the fault definition). |
| `fault.timing.ramp_time_sec` | int (>0) | seconds | `RAMP_TIME` env — pre-chaos and post-chaos buffer applied around the active window. |
| `fault.timing.chaos_interval_sec` | int (>0) | seconds | `CHAOS_INTERVAL` env. Applies to iterative faults (e.g. `pod-delete`). |
| `fault.timing.sequence` | string | `parallel` \| `serial` | `SEQUENCE` env, lowercased. Per-fault iteration shape for multi-target faults. Distinct from the workflow-level `fault.workflow.sequence_mode`. |
| `fault.injection.verdict` | string | `Pass` \| `Fail` \| `Stopped` \| `Awaited` \| `Error` | Final outcome of the fault injection. Absent only if no result was produced during the run. |
| `fault.injection.phase` | string | `Running` \| `Completed` \| `Stopped` \| `Error` \| `Completed_With_Probe_Failure` \| `Completed_With_Error` | Final lifecycle phase. An older spaced variant (e.g. `Completed With Probe Failure`) may appear from legacy runs and should be normalised to the underscored form before matching. |
| `fault.injection.probe_success_pct` | string | `"0"` to `"100"` | Probe success percentage. Encoded as a string; cast to int for arithmetic. |
| `fault.injection.fail_step` | string | free-form | Identifies the failing step when `verdict ≠ Pass` (e.g. `"PreChaosCheck"`). |

#### Scope

| Key | Type | Description |
|---|---|---|
| `fault.target.workload_ref` | string | Workload-level anchor in `<TitleCaseKind>/<Name>` form (e.g. `Deployment/carts`, `Statefulset/user-db`). |
| `fault.target.containers` | array of string | Comma-split `TARGET_CONTAINER` / `TARGET_CONTAINERS` env. Absent ⇔ all containers in the targeted pod are in scope. |

#### Probes

Independent observation of the fault window via configured probes.

| Key | Type | Description |
|---|---|---|
| `fault.probes.results` | array of object | Probe outcomes. Absent (not `[]`) when no probes ran or no results were captured for the run. Today the array contains one entry per probe carrying its terminal verdict — not a per-attempt timeline. |

Per-element schema:

```json
{
  "name":        "<probe name>",
  "type":        "httpProbe" | "k8sProbe" | "cmdProbe",
  "mode":        "SOT" | "EOT" | "Edge" | "OnChaos" | "Continuous",
  "verdict":     "Passed" | "Failed" | "N/A" | "Awaited",
  "description": "<probe-emitted message>"
}
```

#### Workflow cohort

| Key | Type | Domain | Description |
|---|---|---|---|
| `fault.workflow.sequence_mode` | string | `single` \| `sequential` \| `parallel` | Run-level workflow shape. `parallel` ⇔ at least two faults in the run have overlapping `[start, end]` intervals. |
| `fault.workflow.cohort_faults` | array of string | sorted, deduplicated fault names | The set of sibling faults whose interval overlaps this fault's. Symmetric across siblings. Absent when this fault has no overlapping siblings. |

### 3.4 SLA targets — location

The SLA bars (max time-to-detect, max time-to-mitigate, max per-tool-call
latency) are not on the `fault: <name>` span. They are run-wide and are
read from the `experiment-triggered` span (or, equivalently, from
`experiment_context.attributes`):

```
experiment.sla.detect_sec      max seconds to detect a fault
experiment.sla.mitigate_sec    max seconds to mitigate a fault
experiment.sla.tool_call_sec   max seconds per agent tool call
```

Defaults are `60 / 300 / 30` seconds; producer-side environment overrides
apply. Every fault in a given run scores against the same SLA contract.

---

## 4. Embedded ground truth

Each `fault: <name>` span carries the full decoded `ground_truth.yaml`
for that fault, mirrored at two paths for consumer convenience:

```
fault: <name>
├── input.ground_truth     ◀── full ground-truth object
└── metadata.ground_truth  ◀── identical object, mirrored
```

### Schema

```yaml
fault_description_goal_remediation:
  goal:        <string>           # what this fault tests
  remediation: <string>           # what good remediation looks like
  symptoms:    [<string>, ...]    # observable symptoms agents should detect

ideal_course_of_action:
  - { step: <int>, action: <string>, detail: <string> }
  - ...                           # ordered diagnostic + mitigation steps

ideal_tool_usage_trajectory:
  - step:           <int>         # 1-based; aligns with ideal_course_of_action
    tool:           <string>      # MCP tool name
    command:        <string>      # exact tool-call form, with placeholders
    purpose:        <string>
    tool_available: <bool>        # if false, this tool is not exposed to
                                  # the agent — exclude from the scorable
                                  # denominator
  - ...
```

### Recommended use

| Block | Recommended consumer use |
|---|---|
| `fault_description_goal_remediation.symptoms` | Seed corpus for detection-event classification (keyword / embedding match). |
| `fault_description_goal_remediation.goal` / `remediation` | Free-form context for the cert narrative. |
| `ideal_course_of_action[]` | Ordered scoring rubric for diagnostic / mitigation completeness. |
| `ideal_tool_usage_trajectory[]` | Per-step expected tool calls. Filter by `tool_available == true` before scoring trajectory adherence. |

---

## 5. Auxiliary span schemas

The following observation types are present on every trace. The
certifier rarely needs them for per-fault scoring; their primary value
is run-level identity, expected-fault enumeration, and run-end
detection.

### 5.1 `experiment-triggered` (SPAN, 1 per run)

Run-wide identity and SLA contract. Verbatim `metadata.attributes` from
the reference fixture:

```json
{
  "agent.id":                     "9a623bab-bf80-4c07-a342-81028997b7e7",
  "agent.name":                   "vaya",
  "agent.platform_name":          "Kubernetes",
  "experiment.fault_name":        "chaos-workflow",
  "experiment.id":                "a8731c79-96b4-4339-be41-76dd135a4328",
  "experiment.name":              "argowf-chaos-sock-shop-parallel",
  "experiment.phase":             "injection",
  "experiment.priority":          "high",
  "experiment.run_key":           "43bf1cdf-b23e-4940-b2f1-084a85308114",
  "experiment.session_id":        "43bf1cdf-b23e-4940-b2f1-084a85308114",
  "experiment.sla.detect_sec":    60,
  "experiment.sla.mitigate_sec":  300,
  "experiment.sla.tool_call_sec": 30,
  "experiment.type":              "experiment",
  "infra.id":                     "9a623bab-bf80-4c07-a342-81028997b7e7",
  "infra.name":                   "vaya",
  "infra.namespace":              "litmus",
  "infra.platform_name":          "Kubernetes",
  "infra.service_account":        "litmus",
  "project.id":                   "af3007fd-6bd8-4789-b302-8c748a49ad65"
}
```

Notes:

- `experiment.sla.*` is the run-wide SLA contract (see §3.4). Values
  are emitted as floats; whole-number defaults (`60`, `300`, `30`) render
  without a trailing `.0` in JSON.
- `experiment.run_key` and `experiment.session_id` are both equal to the
  `notifyID` (which is the trace ID).
- `experiment.fault_name == "chaos-workflow"` is a placeholder used for
  multi-fault workflows. The authoritative per-fault names are
  `experiment_context.fault_names[]` and the names of the `fault: <name>`
  spans themselves.

### 5.2 `experiment_context` (SPAN, 1 per run)

Run-wide identity plus the canonical list of expected faults. Verbatim
`metadata`:

```json
{
  "agent_id":        "9a623bab-bf80-4c07-a342-81028997b7e7",
  "agent_name":      "vaya",
  "agent_platform":  "Kubernetes",
  "agent_version":   "3.0.0",
  "experiment_id":   "a8731c79-96b4-4339-be41-76dd135a4328",
  "experiment_name": "argowf-chaos-sock-shop-parallel",
  "namespace":       "litmus",
  "fault_names":     ["pod-cpu-hog", "pod-network-loss", "pod-memory-hog"],
  "attributes": {
    "experiment.sla.detect_sec":    60,
    "experiment.sla.mitigate_sec":  300,
    "experiment.sla.tool_call_sec": 30
  }
}
```

`fault_names[]` is the authoritative list of faults expected to fire.
A discrepancy between this list and the count of `fault: <name>` spans
indicates a missing or extra injection.

`namespace` here is the infrastructure namespace (`litmus`), not the
application target namespace.

### 5.3 `workflow-step: <step>` (SPAN, N per run)

One observation per workflow node update event. `metadata.attributes`:

```json
{
  "experiment.id":             "<experiment template id>",
  "experiment.name":           "<workflow name>",
  "experiment.run_id":         "<workflow run k8s UID>",
  "experiment.type":           "events",
  "workflow.event_type":       "UPDATE",
  "workflow.name":             "argowf-chaos-sock-shop-parallel-1778176239450",
  "workflow.namespace":        "litmus",
  "workflow.node.children":    1,
  "workflow.node.finished_at": "1778177445",
  "workflow.node.id":          "argowf-chaos-sock-shop-parallel-1778176239450",
  "workflow.node.message":     "",
  "workflow.node.name":        "argowf-chaos-sock-shop-parallel-1778176239450",
  "workflow.node.phase":       "Succeeded",
  "workflow.node.started_at":  "1778176239",
  "workflow.node.type":        "Steps",
  "workflow.notify_id":        "<notify id = trace id>",
  "workflow.phase":            "Completed"
}
```

Notes:

- `workflow.node.started_at` and `workflow.node.finished_at` are Unix
  epoch seconds encoded as strings. Convert before arithmetic.
- `workflow.node.phase` enum: `Pending` \| `Running` \| `Succeeded` \|
  `Skipped` \| `Failed` \| `Error` \| `Omitted`.
- `workflow.phase` enum (whole-workflow state): `Pending` \| `Running`
  \| `Completed` \| `Failed` \| `Error`.
- **Run-end detection.** The run is finished when any `workflow-step:`
  observation carries `workflow.phase ∈ {Completed, Failed, Error}`.
  The `metadata.terminal: true` flag is set on every `workflow-step:`
  span and is *not* a run-end marker.

### 5.4 `litellm-acompletion` (GENERATION, M per run)

Agent LLM generations emitted by LiteLLM. They share the same `traceId`
and are consumed by the existing certifier LLM extractors. This document
does not redocument their schema.

### 5.5 `completion: <experimentName>` (EVENT, 1 per run)

```json
"output":   { "status": "PASS" | "FAIL", "result": "...", "error": "" },
"metadata": { "completionPhase": "post-execution" }
```

This event marks **trace-creation completion** (workflow successfully
submitted to the infrastructure), not workflow-execution completion.
For workflow-execution completion, use `workflow.phase` on
`workflow-step:` spans or `fault.injection.phase` on `fault:` spans.

---

## 6. Reference example

A single `fault: <name>` observation from the reference fixture, verbatim:

```json
{
  "id":                  "43bf1cdf-b23e-4940-b2f1-084a85308114-fault-pod-cpu-hog",
  "name":                "fault: pod-cpu-hog",
  "type":                "SPAN",
  "traceId":             "43bf1cdf-b23e-4940-b2f1-084a85308114",
  "startTime":           "2026-05-07T17:57:27.000Z",
  "endTime":             "2026-05-07T18:09:30.000Z",
  "level":               "DEFAULT",
  "parentObservationId": null,

  "input":  { "fault_name": "pod-cpu-hog", "ground_truth": { /* full GT */ } },
  "output": { "status": "injected" },

  "metadata": {
    "action":          "fault_injection",
    "fault_name":      "pod-cpu-hog",
    "ground_truth":    { /* full GT, mirrored */ },
    "llm_used":        false,
    "tokens_consumed": 0,
    "attributes": {
      "fault.engine_name":                     "pod-cpu-hogpw59s",
      "fault.injection.phase":                 "Completed",
      "fault.injection.probe_success_pct":     "100",
      "fault.injection.verdict":               "Pass",
      "fault.injection_end_timestamp":         "2026-05-07T18:09:30.000Z",
      "fault.injection_timestamp":             "2026-05-07T17:57:27.000Z",
      "fault.name":                            "pod-cpu-hog",
      "fault.namespace":                       "litmus",
      "fault.probes.results": [
        {
          "description": "The URL http://front-end.sock-shop.svc.cluster.local:80 did respond with correct status code. Actual code: '200'. Expected code: '200'",
          "mode":        "Edge",
          "name":        "check-frontend-access-url-dqVxmKhLTpuF_2nEI1g1qQ",
          "type":        "httpProbe",
          "verdict":     "Passed"
        }
      ],
      "fault.status":                          "injected",
      "fault.target.workload_ref":             "Deployment/carts",
      "fault.target_kind":                     "deployment",
      "fault.target_label":                    "name=carts",
      "fault.target_namespace":                "sock-shop",
      "fault.timing.ramp_time_sec":            30,
      "fault.timing.total_chaos_duration_sec": 600,
      "fault.workflow.cohort_faults":          ["pod-memory-hog", "pod-network-loss"],
      "fault.workflow.sequence_mode":          "parallel"
    }
  }
}
```

### Worked interpretation

| Question | Answer derived from this span |
|---|---|
| Which fault? | `pod-cpu-hog` (`fault.name`). |
| Target? | `Deployment/carts` in namespace `sock-shop` (`fault.target.workload_ref`, `fault.target_namespace`). |
| Chaos window? | `2026-05-07T17:57:27Z` → `2026-05-07T18:09:30Z` (12 min 03 s wall-clock). |
| Outcome? | Pass — `verdict=Pass`, `phase=Completed`, `probe_success_pct=100`. |
| Configured vs observed window? | Configured `total_chaos_duration_sec=600` + `2 × ramp_time_sec=60` = 660 s. Observed 723 s. The ~63 s delta is normal engine setup, runner-pod scheduling, and post-chaos cleanup overhead. |
| Concurrent faults? | Yes — `sequence_mode=parallel` with cohort `[pod-memory-hog, pod-network-loss]`. |
| Independent observation? | One `Edge`-mode `httpProbe` returned `Passed` against `front-end:80`. The front-end remained reachable throughout the chaos window. |

The other two fault spans in the same run target `Statefulset/user-db`
(pod-network-loss) and `Deployment/orders` (pod-memory-hog), with the
same shape and symmetric cohort relationships.

---

## 7. Per-fault scoring inputs

The complete set of signals the certifier needs to score a single fault,
all readable from the same `fault: <name>` observation (with the run-wide
SLA on `experiment-triggered`):

```
                          PER-FAULT SCORING INPUTS

   ┌──────────────────────────┐  ┌──────────────────────────┐
   │  IDENTITY & TARGET       │  │  WINDOW & VERDICT        │
   │                          │  │                          │
   │  fault.name              │  │  fault.injection_        │
   │  fault.target_namespace  │  │      timestamp           │
   │  fault.target_label      │  │  fault.injection_end_    │
   │  fault.target_kind       │  │      timestamp           │
   │  fault.target.           │  │  fault.timing.           │
   │      workload_ref        │  │      total_chaos_        │
   │  fault.target.containers │  │      duration_sec        │
   │                          │  │  fault.timing.           │
   │                          │  │      ramp_time_sec       │
   │                          │  │  fault.timing.           │
   │                          │  │      chaos_interval_sec  │
   │                          │  │  fault.timing.sequence   │
   │                          │  │  fault.injection.verdict │
   │                          │  │  fault.injection.phase   │
   │                          │  │  fault.injection.        │
   │                          │  │      probe_success_pct   │
   │                          │  │  fault.injection.        │
   │                          │  │      fail_step           │
   └──────────────────────────┘  └──────────────────────────┘

   ┌──────────────────────────┐  ┌──────────────────────────┐
   │  WORKFLOW COHORT         │  │  INDEPENDENT OBSERVATION │
   │                          │  │                          │
   │  fault.workflow.         │  │  fault.probes.results[]  │
   │      sequence_mode       │  │      • name              │
   │  fault.workflow.         │  │      • type              │
   │      cohort_faults[]     │  │      • mode              │
   │                          │  │      • verdict           │
   │                          │  │      • description       │
   └──────────────────────────┘  └──────────────────────────┘

   ┌──────────────────────────────────────────────────────────┐
   │  GROUND TRUTH                                            │
   │                                                          │
   │  input.ground_truth  /  metadata.ground_truth            │
   │      • fault_description_goal_remediation                │
   │      • ideal_course_of_action[]                          │
   │      • ideal_tool_usage_trajectory[]                     │
   └──────────────────────────────────────────────────────────┘


                       RUN-WIDE SLA TARGETS
                                ▲
                                │
                                │  (read once per run)
                                │
   ┌──────────────────────────────────────────────────────────┐
   │  experiment-triggered  /  experiment_context             │
   │                                                          │
   │      experiment.sla.detect_sec                           │
   │      experiment.sla.mitigate_sec                         │
   │      experiment.sla.tool_call_sec                        │
   └──────────────────────────────────────────────────────────┘
```

All of the per-fault inputs above land on a single Langfuse observation.
A single fetch — `name = "fault: <faultName>"`, filtered by `traceId` —
returns the complete record. The only required cross-span lookup is the
run-wide SLA on `experiment-triggered`, which is read once per run.
