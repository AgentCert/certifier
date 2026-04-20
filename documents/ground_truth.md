## Ground Truth

The `ground_truth` section is the most critical part of the schema. It defines the **ideal agent behaviour** against which the agent under test is evaluated. The ground truth is never exposed to the agent — it is used solely by the metrics extraction pipeline to compute accuracy, correctness, and adherence metrics.

### Fault Description, Goal & Remediation

The `fault_description_goal_remediation` sub-section contains:

| Field | Type | Description |
| --- | --- | --- |
| `symptoms` | `List[string]` | Observable symptoms the agent should detect (e.g., pod entering Terminating state, HTTP 5xx errors) |
| `goal` | `string` | The objective of the fault injection — what disruption it introduces and what recovery it triggers |
| `remediation` | `string` | The expected remediation steps the agent should recommend or perform |

### Ideal Course of Action

The `ideal_course_of_action` is an ordered list of steps representing the optimal diagnostic and remediation workflow:

| Field | Type | Description |
| --- | --- | --- |
| `step` | `int` | Step number in the ideal sequence |
| `action` | `string` | Short description of the action |
| `detail` | `string` | Detailed explanation of what the step involves |

This sequence is used to evaluate the agent's **plan adherence**, **action correctness**, and **trajectory efficiency** — comparing the agent's actual actions against this ideal path.

### Ideal Tool Usage Trajectory

The `ideal_tool_usage_trajectory` defines the optimal sequence of tool invocations:

| Field | Type | Description |
| --- | --- | --- |
| `step` | `int` | Step number in the ideal tool sequence |
| `tool` | `string` | Name of the tool/command |
| `command` | `string` | The exact command with placeholder arguments |
| `purpose` | `string` | Why this tool call is expected at this step |
| `tool_available` | `bool` | Whether this tool is available in the available tool list |

This trajectory is used to compute **tool selection accuracy**, **argument accuracy**, **action efficiency**, and **optimal tool-call deviations** during metrics extraction.

### SLA (Service Level Agreement)

The `sla` sub-section defines measurable performance thresholds that complement the ideal course of action and tool usage trajectory by adding **time bounds** and **efficiency limits** to the evaluation framework.

| Field | Type | Description |
| --- | --- | --- |
| `time_to_detect.threshold` | `int` | Max seconds from fault injection to first correct symptom identification |
| `time_to_detect.unit` | `string` | Unit of measurement (always `"seconds"`) |
| `time_to_mitigate.threshold` | `int` | Max seconds from fault injection to successful remediation |
| `time_to_mitigate.unit` | `string` | Unit of measurement (always `"seconds"`) |
| `max_tool_calls.threshold` | `int` | Max total tool invocations allowed (including retries) |
| `max_tool_calls.unit` | `string` | Unit of measurement (always `"count"`) |

> **Note:** `time_to_mitigate` is measured from the moment the fault is injected, not from detection. This reflects the real-world user experience where the clock starts when the disruption begins, not when the agent notices it.

**Schema:**

```yaml
ground_truth:
  sla:
    time_to_detect:
      description: "Max time from fault injection to first correct symptom identification"
      threshold: <seconds>
      unit: "seconds"

    time_to_mitigate:
      description: "Max time from fault injection to successful remediation"
      threshold: <seconds>
      unit: "seconds"

    max_tool_calls:
      description: "Max total tool invocations allowed (including retries)"
      threshold: <count>
      unit: "count"
```

These thresholds are used to evaluate **timeliness** (did the agent detect and remediate within acceptable windows?) and **efficiency** (did the agent solve the problem without excessive tool invocations?). SLA violations are surfaced as evaluation warnings or failures by the metrics extraction pipeline.