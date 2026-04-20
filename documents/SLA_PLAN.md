# SLA Plan for Ground Truth YAML Files

## Overview

This document defines the SLA (Service Level Agreement) section to be added to each `ground_truth.yaml` file. SLAs establish measurable performance thresholds against which the agent under test is evaluated. They complement the existing `ideal_course_of_action` and `ideal_tool_usage_trajectory` by adding **time bounds** and **efficiency limits** to the evaluation framework.

---

## SLA Schema

Each `ground_truth.yaml` will include a new top-level key `sla` under `ground_truth` with the following structure:

```yaml
ground_truth:
  sla:
    # --- Timeliness SLAs ---
    time_to_detect:
      description: "Max time from fault injection to first correct symptom identification"
      threshold: <seconds>
      unit: "seconds"

    time_to_mitigate:
      description: "Max time from fault injection to successful remediation"
      threshold: <seconds>
      unit: "seconds"

    # --- Efficiency SLAs ---
    max_tool_calls:
      description: "Max total tool invocations allowed (including retries)"
      threshold: <count>
      unit: "count"
```

> **Note:** `time_to_mitigate` is measured from the moment the fault is injected, not from detection. This reflects the real-world user experience where the clock starts when the disruption begins, not when the agent notices it.

---

## SLA Design Rationale

### Timeliness Thresholds

All time SLAs are measured from **fault injection time** (T=0). `time_to_mitigate` includes detection + diagnosis + remediation, reflecting the total disruption window experienced by end users.

Time SLAs are calibrated per-fault based on:

| Factor | Impact |
|--------|--------|
| **Fault severity** | Critical faults (node-restart) get tighter detection budgets but longer mitigation windows due to infrastructure recovery |
| **Symptom visibility** | Explicit K8s conditions (NodeNotReady, OOMKilled, DiskPressure) enable faster detection than ambiguous symptoms (packet corruption, throughput degradation) |
| **Prometheus scrape lag** | Metrics-dependent detection adds 15–30s baseline due to scrape intervals |
| **Diagnostic depth** | Network faults require multi-layer correlation (logs + exec + node stats + PromQL) adding 60–120s |
| **Infrastructure recovery** | Node reboots, DNS cache propagation, and tc rule cleanup are infrastructure-bound and outside agent control |
| **Agent tool overhead** | Each tool invocation adds ~2–5s round-trip; 8–12 diagnostic calls = 16–60s of API latency alone |

### Efficiency Thresholds

Efficiency SLAs are set relative to the ideal tool usage trajectory length:

- `max_tool_calls` = ideal tool steps × 1.5 (rounded up)

---

## Per-Fault SLA Definitions

### 1. pod-delete

**File:** `faults/kubernetes/pod-delete/ground_truth.yaml`

| SLA | Threshold | Rationale |
|-----|-----------|-----------|
| **Time to Detect** | 60s | Pod Terminating state and warning events are immediately visible in `Events: List` and `Pods: List`; 60s accounts for agent startup and initial tool calls |
| **Time to Mitigate** | 120s | Controller auto-recreates deleted pods; agent needs ~60s post-detection to verify replica count restoration and run health check |
| **Max Tool Calls** | 18 | 12 ideal × 1.5 |

---

### 2. pod-cpu-hog

**File:** `faults/kubernetes/pod-cpu-hog/ground_truth.yaml`

| SLA | Threshold | Rationale |
|-----|-----------|-----------|
| **Time to Detect** | 120s | CPU metrics depend on Prometheus scrape intervals (15–30s); `Pods: Top` may show stale data initially; agent needs exec into pod to confirm stress process, adding multiple tool round-trips |
| **Time to Mitigate** | 300s | Requires detection (120s) + process-level diagnosis via exec (30–60s) + kill stress process + delete degraded pods + verify deployment health; exec commands may fail if container is throttled |
| **Max Tool Calls** | 21 | 14 ideal × 1.5 |

---

### 3. pod-memory-hog

**File:** `faults/kubernetes/pod-memory-hog/ground_truth.yaml`

| SLA | Threshold | Rationale |
|-----|-----------|-----------|
| **Time to Detect** | 90s | OOMKilled pods show immediately in status, but gradual memory growth without OOM may only surface through `Pods: Top` or PromQL after scrape lag; 90s covers both fast-OOM and slow-growth scenarios |
| **Time to Mitigate** | 240s | Detection (90s) + exec to identify stress process (30s) + kill process + delete OOMKilled pods + verify recovery; OOMKilled pods may enter CrashLoopBackOff requiring multiple delete cycles |
| **Max Tool Calls** | 21 | 14 ideal × 1.5 |

---

### 4. pod-network-loss

**File:** `faults/kubernetes/pod-network-loss/ground_truth.yaml`

| SLA | Threshold | Rationale |
|-----|-----------|-----------|
| **Time to Detect** | 180s | Network packet loss manifests as generic timeouts and 5xx errors, indistinguishable from app bugs initially; agent must correlate pod logs + exec ping/curl + PromQL network error metrics across multiple tool calls to confirm network-layer cause |
| **Time to Mitigate** | 360s | Detection (180s) + delete affected pods + scale deployment + validate connectivity via exec + confirm recovery via PromQL; network state cleanup depends on chaos engine lifecycle |
| **Max Tool Calls** | 21 | 14 ideal × 1.5 |

---

### 5. pod-network-corruption

**File:** `faults/kubernetes/pod-network-corruption/ground_truth.yaml`

| SLA | Threshold | Rationale |
|-----|-----------|-----------|
| **Time to Detect** | 240s | Hardest network fault to detect — symptoms are garbled responses, CRC errors, and deserialization failures buried in application logs; these overlap heavily with application bugs and require deep log analysis + PromQL receive error correlation to isolate |
| **Time to Mitigate** | 420s | Detection (240s) + pod deletion + integrity validation; 5 remediation tools are unavailable in this fault’s trajectory, forcing the agent to work with limited tooling which adds exploratory overhead |
| **Max Tool Calls** | 21 | 14 ideal × 1.5 |

---

### 6. pod-network-rate-limit

**File:** `faults/kubernetes/pod-network-rate-limit/ground_truth.yaml`

| SLA | Threshold | Rationale |
|-----|-----------|-----------|
| **Time to Detect** | 180s | Throughput degradation is gradual, not binary; slow responses may initially appear as normal latency variance; requires PromQL histogram queries (p99 request duration) and throughput measurement via exec to confirm bandwidth throttling |
| **Time to Mitigate** | 360s | Detection (180s) + pod deletion + throughput validation; tc rule cleanup tool is unavailable, so mitigation relies on pod recreation and chaos engine auto-cleanup |
| **Max Tool Calls** | 26 | 17 ideal × 1.5, rounded up |

---

### 7. pod-dns-error

**File:** `faults/kubernetes/pod-dns-error/ground_truth.yaml`

| SLA | Threshold | Rationale |
|-----|-----------|-----------|
| **Time to Detect** | 60s | DNS failures produce clear NXDOMAIN/SERVFAIL errors that cascade immediately across all service-to-service calls; `Events: List` and pod logs show connection failures quickly, but agent must also check CoreDNS health (multi-namespace investigation) to rule out cluster-wide DNS issues |
| **Time to Mitigate** | 180s | Detection (60s) + CoreDNS log analysis + delete affected pods + optionally restart CoreDNS + validate DNS resolution via exec nslookup; DNS cache propagation may add 30–60s to recovery confirmation |
| **Max Tool Calls** | 21 | 14 ideal × 1.5 |

---

### 8. disk-fill

**File:** `faults/kubernetes/disk-fill/ground_truth.yaml`

| SLA | Threshold | Rationale |
|-----|-----------|-----------|
| **Time to Detect** | 60s | DiskPressure node condition and pod eviction events are explicit Kubernetes signals surfaced immediately in `Events: List`; 60s accounts for agent startup, event listing, and initial pod status checks |
| **Time to Mitigate** | 180s | Detection (60s) + exec `df -h` to confirm fill level + cleanup chaos-injected files + delete evicted pods + verify deployment health; file cleanup via exec adds latency as the agent identifies and removes the right files |
| **Max Tool Calls** | 20 | 13 ideal × 1.5, rounded up |

---

### 9. node-restart

**File:** `faults/kubernetes/node-restart/ground_truth.yaml`

| SLA | Threshold | Rationale |
|-----|-----------|-----------|
| **Time to Detect** | 30s | NodeNotReady is the highest-priority Kubernetes event, surfaced immediately by the API server; kubelet heartbeat timeout (~40s default) may delay the condition slightly, but events fire within seconds |
| **Time to Mitigate** | 300s | Detection is fast (30s), but mitigation is infrastructure-bound: node reboot takes 60–120s, kubelet restart and re-registration adds 30–60s, pod rescheduling to other nodes takes 30–60s, and deleting stuck Terminating pods + verifying deployment health adds another 30–60s; uncordon tool is unavailable |
| **Max Tool Calls** | 20 | 13 ideal × 1.5, rounded up |

---

### 10. pod-autoscaler

**File:** `faults/kubernetes/pod-autoscaler/ground_truth.yaml`

| SLA | Threshold | Rationale |
|-----|-----------|-----------|
| **Time to Detect** | 120s | FailedScheduling events accumulate gradually as pods enter Pending state; cluster autoscaler scale-up events take 30–60s to appear; agent must correlate pod states + node capacity + events to confirm the scaling pressure |
| **Time to Mitigate** | 180s | Remediation is a single deterministic `Resources: Scale` API call to restore original replica count; the short gap between TTD (120s) and TTM (180s) reflects that scale-back is fast once the agent identifies the issue |
| **Max Tool Calls** | 24 | 16 ideal × 1.5 |

---

## Summary Matrix

| Fault | TTD | TTM | Max Calls | TTM Breakdown |
|-------|-----|-----|-----------|---------------|
| pod-delete | 60s | 120s | 18 | 60s detect + 60s verify controller recovery |
| pod-cpu-hog | 120s | 300s | 21 | 120s detect + 60s diagnose process + 120s kill/delete/verify |
| pod-memory-hog | 90s | 240s | 21 | 90s detect + 60s diagnose + 90s kill/delete/verify |
| pod-network-loss | 180s | 360s | 21 | 180s detect + 90s delete pods + 90s validate connectivity |
| pod-network-corruption | 240s | 420s | 21 | 240s detect + 90s delete pods + 90s verify integrity |
| pod-network-rate-limit | 180s | 360s | 26 | 180s detect + 90s delete pods + 90s verify throughput |
| pod-dns-error | 60s | 180s | 21 | 60s detect + 60s CoreDNS analysis + 60s delete/validate |
| disk-fill | 60s | 180s | 20 | 60s detect + 60s cleanup files + 60s delete/verify |
| node-restart | 30s | 300s | 20 | 30s detect + 120–180s infra recovery + 60–90s pod cleanup/verify |
| pod-autoscaler | 120s | 180s | 24 | 120s detect + 60s scale-back (single API call) |

### Legend

| Abbreviation | Full Name |
|--------------|-----------|
| TTD | Time to Detect (from fault injection) |
| TTM | Time to Mitigate (from fault injection) |

---

## YAML Example

Below is an example of how the `sla` section would appear in `pod-delete/ground_truth.yaml`:

```yaml
ground_truth:
  # ... existing sections ...

  sla:
    time_to_detect:
      description: "Max time from fault injection to first correct symptom identification"
      threshold: 60
      unit: "seconds"

    time_to_mitigate:
      description: "Max time from fault injection to successful remediation"
      threshold: 120
      unit: "seconds"

    max_tool_calls:
      description: "Max total tool invocations allowed"
      threshold: 18
      unit: "count"
```

---

## Implementation Notes

1. **Placement**: The `sla` section should be added as a sibling to `fault_description_goal_remediation`, `ideal_course_of_action`, and `ideal_tool_usage_trajectory` under `ground_truth`.

2. **Metrics Pipeline Integration**: The SLA thresholds will be consumed by the metrics extraction pipeline alongside existing ground truth sections. SLA violations should be surfaced as evaluation warnings or failures.

3. **Threshold Tuning**: Initial thresholds in this document are baseline recommendations. They should be calibrated through empirical runs against real agents and adjusted based on:
   - P50/P90 agent performance across multiple runs
   - Infrastructure variability (cluster size, network latency)
   - Agent capability maturity

4. **Versioning**: SLA thresholds should be versioned alongside the ground truth. When fault definitions change (new steps, new tools), SLAs must be re-evaluated.

5. **Grading Tiers**: Consider introducing pass/warn/fail tiers per SLA:
   - **Pass**: Within threshold
   - **Warn**: Threshold exceeded by ≤ 25%
   - **Fail**: Threshold exceeded by > 25%
