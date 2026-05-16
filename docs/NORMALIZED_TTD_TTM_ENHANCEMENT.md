# Normalized TTD/TTM Enhancement & SLA Integration Guide

## Part 1: Current Implementation Analysis

### Location & Architecture

| Component | File | Responsibility |
|-----------|------|-----------------|
| **Metrics Extraction** | `aggregator/scripts/numeric_aggregation.py:compute_numeric_aggregates()` | Extracts per-run TTD/TTM → `{mean, median, std_dev, p95, min, max}` |
| **Normalization** | `cert_builder/scripts/computation/scorecard_builder.py:normalize_speed()` | Converts raw seconds → 0-1 score |
| **Config** | `cert_builder/config/scorecard_config.yaml` | Fixed `speed_ref: 1800` ceiling |
| **SLA Data** | `documents/SLA_PLAN.md` + trace metadata | Per-category thresholds in seconds |

### Current Formula & Logic

```python
def normalize_speed(mean_seconds):
    """Lower time = better. 0s (no data) -> 0.0, >=1800s -> 0.0"""
    if mean_seconds is None or mean_seconds == 0:
        return 0.0
    return _clamp(1 - mean_seconds / SPEED_REF)  # SPEED_REF = 1800
```

**Example (fixed 1800s ceiling):**
```
Application:  mean_ttd = 366.3s  → 1 - 366.3/1800 = 0.797
Network:      mean_ttd = 536.6s  → 1 - 536.6/1800 = 0.702
Resource:     mean_ttd = 1364.5s → 1 - 1364.5/1800 = 0.242
Final = avg(0.797, 0.702, 0.242) = 0.58
```

---

## Part 2: Best Logic Enhancements (Without SLA)

### Enhancement 1: Fault-Severity-Based Scaling

**Rationale:** Different fault types warrant different speed expectations:
- **Critical** (node-restart, pod-delete): Need ~30-60s detection
- **Severe** (network-loss, cpu-hog): Need ~60-120s detection  
- **Moderate** (disk-fill, rate-limit): Need ~120-300s detection

**Implementation:**

```python
# In scorecard_config.yaml or new severity_config.yaml
fault_severity:
  critical:
    ttd_threshold: 60          # detect within 60s
    ttm_threshold: 300         # mitigate within 300s
  severe:
    ttd_threshold: 120
    ttm_threshold: 600
  moderate:
    ttd_threshold: 300
    ttm_threshold: 900

# In scorecard_builder.py
def normalize_speed_by_severity(mean_seconds, severity):
    """Use category-specific ceiling based on fault severity."""
    severity_config = CONFIG["fault_severity"]
    threshold = severity_config[severity]["ttd_threshold"]
    
    if mean_seconds is None or mean_seconds == 0:
        return 0.0
    return _clamp(1 - mean_seconds / threshold)
```

---

### Enhancement 2: Percentile-Based Scoring for Reliability

**Rationale:** Mean alone doesn't capture tail behavior. P95/P99 matters for production SLAs.

**Implementation:**

```python
def normalize_speed_with_percentile(metrics_dict):
    """
    Score based on multiple percentiles:
    - mean: 50% weight (typical case)
    - p95: 30% weight (strong tail)
    - p99/max: 20% weight (worst case)
    """
    mean_score = _clamp(1 - metrics_dict["mean"] / SPEED_REF)
    p95_score = _clamp(1 - metrics_dict.get("p95", SPEED_REF) / SPEED_REF)
    max_score = _clamp(1 - metrics_dict.get("max", SPEED_REF) / SPEED_REF)
    
    # Weighted combination favoring percentiles
    blended = (
        0.40 * mean_score +
        0.35 * p95_score +
        0.25 * max_score
    )
    return _clamp(blended)
```

**Example:**
```
Category: Network
  mean=536.6s → 0.702
  p95=800s   → 0.556
  max=1200s  → 0.333
  Blended = 0.40*0.702 + 0.35*0.556 + 0.25*0.333 = 0.547
  (vs. 0.702 if using mean only)
```

---

### Enhancement 3: Success-Rate Weighted Scoring

**Rationale:** Fast detection is meaningless if detection rate is low. Couple TTD with detection success.

**Implementation:**

```python
def normalize_speed_weighted_by_detection_rate(mean_seconds, detection_rate):
    """
    Penalize fast times when detection rate is low.
    Formula: base_speed_score * detection_rate
    """
    base_score = normalize_speed(mean_seconds)
    
    # If detection_rate < 50%, apply severe penalty
    if detection_rate < 0.5:
        penalty = 0.5 + 0.5 * detection_rate  # range [0.25, 0.75]
        return base_score * penalty
    
    # Otherwise, boost slightly with high detection
    return base_score * (0.9 + 0.1 * detection_rate)
```

**Rationale:**
- 100% detection + 536.6s → 0.702 * 1.0 = 0.702 ✓
- 50% detection + 536.6s → 0.702 * 0.75 = 0.527 (penalized)
- 25% detection + 536.6s → 0.702 * 0.625 = 0.439 (heavily penalized)

---

### Enhancement 4: Degradation-Mode Scoring

**Rationale:** Some runs may have incomplete TTD/TTM data (N/A). Score them proportionally.

**Implementation:**

```python
def normalize_with_data_completeness(
    ttd_metrics,
    ttm_metrics, 
    detection_runs,
    mitigation_runs,
    total_runs
):
    """
    Score takes data availability into account.
    - If TTD available in only 30/100 runs → apply confidence penalty
    """
    ttd_data_completeness = detection_runs / total_runs  # e.g., 0.7
    ttm_data_completeness = mitigation_runs / total_runs  # e.g., 0.3
    
    ttd_score = normalize_speed(ttd_metrics["mean"])
    ttm_score = normalize_speed(ttm_metrics["mean"])
    
    # Penalize low data completeness
    confidence_factor = 0.5 + 0.5 * ttd_data_completeness
    
    return ttd_score * confidence_factor, ttm_score * confidence_factor
```

---

### Enhancement 5: Multi-Run Stability Scoring

**Rationale:** Consistent performance is better than variable performance, even with same mean.

**Implementation:**

```python
def normalize_speed_with_stability(mean_seconds, std_dev):
    """
    Lower std_dev = more reliable.
    Combine speed + stability using coefficient of variation.
    """
    base_score = normalize_speed(mean_seconds)
    
    if mean_seconds <= 0 or std_dev is None:
        return base_score
    
    # Coefficient of variation: lower is more stable
    cv = std_dev / mean_seconds
    
    # Stability penalty: high CV (>0.5) degrades score by 10-20%
    stability_factor = 1.0 - (0.2 * min(cv, 2.5) / 2.5)
    
    return base_score * stability_factor
```

**Example:**
```
Scenario A: mean=100s, std_dev=10s  → cv=0.1 → stability_factor=0.98 → final ~ 0.98 * base
Scenario B: mean=100s, std_dev=50s  → cv=0.5 → stability_factor=0.96 → final ~ 0.96 * base
Scenario C: mean=100s, std_dev=100s → cv=1.0 → stability_factor=0.92 → final ~ 0.92 * base
```

---

## Part 3: SLA Integration (Recommended Approach)

### Design Overview

**Goal:** Replace fixed 1800s ceiling with **category-level SLA thresholds** extracted from:
1. Trace metadata: `experiment.sla.detect_sec`, `experiment.sla.mitigate_sec`
2. Ground truth file: `ground_truth/{category}/sla.yaml`

### Step 1: Extract SLA from Ground Truth

**File Structure:**
```
data/input/12-05-26-aarya/Sequential/fault-bucketing/.../ground_truth/
├── application_fault/
│   ├── ideal_course_of_action.md
│   ├── ideal_tool_usage_trajectory.md
│   └── sla.yaml          ← NEW
├── network_fault/
│   └── sla.yaml
└── resource_fault/
    └── sla.yaml
```

**sla.yaml Format:**
```yaml
fault_category: "network_fault"
sla_metrics:
  time_to_detect:
    description: "Max time from fault injection to first correct symptom ID"
    threshold_seconds: 60
    unit: "seconds"
    
  time_to_mitigate:
    description: "Max time from injection to successful remediation"
    threshold_seconds: 300
    unit: "seconds"
    
  tool_call_efficiency:
    description: "Max tool invocations allowed"
    threshold_count: 30
    unit: "count"

# Optional: define percentile-based SLAs
percentile_slas:
  p95:
    time_to_detect: 80    # p95 must be ≤ 80s
    time_to_mitigate: 350
```

### Step 2: Load SLA in Aggregation Phase

**In `aggregator/scripts/aggregation.py`:**

```python
def load_category_slas(ground_truth_dir: Path) -> Dict[str, Dict[str, float]]:
    """
    Load SLA thresholds from ground_truth/{category}/sla.yaml
    Returns: {
        "application_fault": {"ttd_threshold": 60, "ttm_threshold": 300},
        "network_fault": {"ttd_threshold": 60, "ttm_threshold": 300},
        "resource_fault": {"ttd_threshold": 120, "ttm_threshold": 600},
    }
    """
    sla_map = {}
    for cat_dir in ground_truth_dir.glob("*/"):
        sla_file = cat_dir / "sla.yaml"
        if sla_file.exists():
            import yaml
            with open(sla_file, 'r') as f:
                sla_data = yaml.safe_load(f)
            sla_map[sla_data["fault_category"]] = {
                "ttd_threshold": sla_data["sla_metrics"]["time_to_detect"]["threshold_seconds"],
                "ttm_threshold": sla_data["sla_metrics"]["time_to_mitigate"]["threshold_seconds"],
            }
    return sla_map
```

### Step 3: Pass SLA to Scorecard Builder

**In `aggregation.py` (before Phase 2B):**

```python
# Load SLAs
sla_thresholds = load_category_slas(ground_truth_dir) if ground_truth_dir else {}

# Pass to Phase 2B (scorecard builder)
scorecard_result = build_scorecard_and_findings_with_sla(
    categories=categories,
    sla_thresholds=sla_thresholds,  ← NEW parameter
)
```

### Step 4: Update Scorecard Builder to Use SLA

**In `cert_builder/scripts/computation/scorecard_builder.py`:**

```python
def build_scorecard_with_sla(categories, sla_thresholds=None):
    """
    Enhanced scorecard builder that:
    1. Uses category-specific SLA thresholds if available
    2. Computes SLA compliance rate
    3. Adds "Normalized TTD (SLA-Aware)" dimension
    """
    det_speeds = []
    mit_speeds = []
    sla_compliance_rates = []
    sla_breach_counts = {"ttd": 0, "ttm": 0}
    
    for cat in categories:
        cat_label = cat["label"]  # e.g., "Network"
        n = cat["numeric"]
        
        # Map category label to fault category key
        fault_cat = label_to_fault_category(cat_label)
        
        # Get SLA for this category
        sla = sla_thresholds.get(fault_cat, {}) if sla_thresholds else {}
        ttd_threshold = sla.get("ttd_threshold", 1800)  # fallback to default
        ttm_threshold = sla.get("ttm_threshold", 1800)
        
        # Extract metrics
        ttd_mean = _safe_get(n, "time_to_detect", "mean")
        ttm_mean = _safe_get(n, "time_to_mitigate", "mean")
        
        # --- NEW: SLA-aware normalization ---
        det_sla = normalize_speed_sla_aware(
            mean_seconds=ttd_mean,
            sla_threshold=ttd_threshold,
            percentile_value=_safe_get(n, "time_to_detect", "p95")
        )
        mit_sla = normalize_speed_sla_aware(
            mean_seconds=ttm_mean,
            sla_threshold=ttm_threshold,
            percentile_value=_safe_get(n, "time_to_mitigate", "p95")
        )
        
        # Track compliance
        if ttd_mean > ttd_threshold:
            sla_breach_counts["ttd"] += 1
        if ttm_mean > ttm_threshold:
            sla_breach_counts["ttm"] += 1
            
        det_speeds.append(det_sla)
        mit_speeds.append(mit_sla)
    
    # Compute breach rates
    total_cats = len(categories)
    ttd_breach_rate = sla_breach_counts["ttd"] / total_cats if total_cats else 0
    ttm_breach_rate = sla_breach_counts["ttm"] / total_cats if total_cats else 0
    
    # Final scorecard dimensions
    dimensions = [
        {"dimension": "Normalized TTD",              "value": round(_mean(det_speeds), 2)},
        {"dimension": "Normalized TTM",              "value": round(_mean(mit_speeds), 2)},
        # NEW: SLA-aware dimensions
        {"dimension": "TTD SLA Compliance Rate",     "value": round(1 - ttd_breach_rate, 2)},
        {"dimension": "TTM SLA Compliance Rate",     "value": round(1 - ttm_breach_rate, 2)},
        {"dimension": "Overall SLA Compliance",      "value": round(1 - (ttd_breach_rate + ttm_breach_rate)/2, 2)},
        # ... other dimensions ...
    ]
    
    return {"dimensions": dimensions, "sla_thresholds": sla_thresholds}
```

### Step 5: New Normalization Function with SLA

```python
def normalize_speed_sla_aware(mean_seconds, sla_threshold, percentile_value=None):
    """
    SLA-aware normalization:
    1. Use SLA threshold as the reference ceiling (not fixed 1800)
    2. Bonus if under threshold, penalty if over
    3. Account for percentile (p95) to catch tail risk
    
    Formula:
    - If mean ≤ SLA threshold: score = 1 - (mean / threshold) * 0.85
                              → can reach 0.15 when mean=threshold
                              → reaches 1.0 only when mean=0
    - If mean > SLA threshold: score = max(0, 1 - 2 * (mean / threshold))
                              → penalized for breach
                              → reaches 0 when mean = 1.5x threshold
    """
    if mean_seconds is None or mean_seconds == 0:
        return 0.0
    
    # Use percentile for stricter evaluation if available
    eval_time = percentile_value if percentile_value else mean_seconds
    
    if eval_time <= sla_threshold:
        # Within SLA: score scales from 0.15 (at threshold) to ~1.0 (at 0)
        score = 1 - (eval_time / sla_threshold) * 0.85
    else:
        # SLA breach: penalized score
        overage_ratio = eval_time / sla_threshold
        score = max(0, 1.5 - overage_ratio)  # reaches 0 at 1.5x threshold
    
    return _clamp(score, 0, 1)
```

**Example with SLA:**
```
Network Category:
  SLA threshold = 60s
  mean_ttd = 45s  → within SLA → score = 1 - (45/60)*0.85 = 0.363 (good, room to improve)
  mean_ttd = 60s  → at SLA     → score = 1 - (60/60)*0.85 = 0.150 (barely meets)
  mean_ttd = 90s  → 1.5x SLA   → score = 1.5 - (90/60) = 0 (breach)
  mean_ttd = 75s  → 1.25x SLA  → score = 1.5 - (75/60) = 0.25 (penalty)
```

---

## Part 4: Implementation Roadmap

### Phase A: Enhancement (No SLA)
Priority: **Quick wins**

1. **Add severity-based thresholds** to `scorecard_config.yaml`
2. **Update `normalize_speed()`** to accept severity parameter
3. **Add p95 weighting** for more robust scoring
4. **Add detection-rate coupling** (don't reward fast detection if rate is low)

### Phase B: SLA Foundation
Priority: **Medium**

1. Create `sla.yaml` template in each ground_truth category folder
2. Implement `load_category_slas()` in aggregator
3. Thread SLA thresholds through to Phase 2B
4. Update scorecard builder to accept `sla_thresholds` parameter

### Phase C: SLA-Aware Scoring
Priority: **High-impact**

1. Implement `normalize_speed_sla_aware()` with breach penalties
2. Add "SLA Compliance Rate" dimensions to scorecard
3. Update findings builder to flag SLA breaches
4. Add SLA compliance to hypothesis testing (H-06, H-07)

### Phase D: Advanced (Optional)
Priority: **Nice-to-have**

1. Per-fault-instance SLA tracking (not just category average)
2. Percentile-based SLA targets (p95 < 80s, p99 < 150s)
3. SLA budgeting across categories with weight allocation
4. Time-series trending of SLA compliance across releases

---

## Part 5: Code Example (Complete Integration)

**File: `cert_builder/scripts/computation/scorecard_builder_sla.py`**

```python
"""
Enhanced scorecard builder with SLA-aware normalization.
"""

from cert_builder.scripts.computation.scorecard_builder import (
    _clamp, _safe_get, _mean, CONFIG, build_findings
)

def normalize_speed_sla_aware(mean_seconds, sla_threshold, percentile_value=None):
    """SLA-aware normalization with breach penalties."""
    if mean_seconds is None or mean_seconds == 0:
        return 0.0
    
    eval_time = percentile_value if percentile_value else mean_seconds
    
    if eval_time <= sla_threshold:
        score = 1 - (eval_time / sla_threshold) * 0.85
    else:
        overage_ratio = eval_time / sla_threshold
        score = max(0, 1.5 - overage_ratio)
    
    return _clamp(score, 0, 1)


def build_scorecard_with_sla(categories, sla_thresholds=None):
    """
    Build scorecard with optional SLA-aware dimensions.
    
    Args:
        categories: List of category dicts with numeric/derived metrics
        sla_thresholds: Dict mapping fault_category → {"ttd_threshold": X, "ttm_threshold": Y}
    
    Returns:
        Dict with "dimensions" (list) and "sla_details" (dict)
    """
    det_speeds = []
    mit_speeds = []
    ttd_breaches = 0
    ttm_breaches = 0
    per_category = []
    
    # Map display names to fault category keys
    label_to_fault_cat = {
        "Application": "application_fault",
        "Network": "network_fault",
        "Resource": "resource_fault",
    }
    
    for cat in categories:
        n = cat["numeric"]
        label = cat["label"]
        fault_cat = label_to_fault_cat.get(label, label.lower())
        
        # Get SLA if available
        sla = (sla_thresholds or {}).get(fault_cat, {})
        ttd_threshold = sla.get("ttd_threshold", 1800)
        ttm_threshold = sla.get("ttm_threshold", 1800)
        
        # Extract metrics
        ttd_mean = _safe_get(n, "time_to_detect", "mean")
        ttd_p95 = _safe_get(n, "time_to_detect", "p95")
        ttm_mean = _safe_get(n, "time_to_mitigate", "mean")
        ttm_p95 = _safe_get(n, "time_to_mitigate", "p95")
        
        # Compute SLA-aware scores
        if sla_thresholds:
            det = normalize_speed_sla_aware(ttd_mean, ttd_threshold, ttd_p95)
            mit = normalize_speed_sla_aware(ttm_mean, ttm_threshold, ttm_p95)
            
            if ttd_mean > ttd_threshold:
                ttd_breaches += 1
            if ttm_mean > ttm_threshold:
                ttm_breaches += 1
        else:
            # Fallback to original logic if no SLA
            det = _clamp(1 - ttd_mean / 1800) if ttd_mean else 0
            mit = _clamp(1 - ttm_mean / 1800) if ttm_mean else 0
        
        det_speeds.append(det)
        mit_speeds.append(mit)
        
        per_category.append({
            "category": label,
            "normalized_ttd": round(det, 3),
            "normalized_ttm": round(mit, 3),
            "ttd_sla_threshold": ttd_threshold,
            "ttm_sla_threshold": ttm_threshold,
            "ttd_breached": ttd_mean > ttd_threshold if ttd_mean else None,
            "ttm_breached": ttm_mean > ttm_threshold if ttm_mean else None,
        })
    
    # Compute final dimensions
    total_cats = len(categories)
    dimensions = [
        {"dimension": "Normalized TTD",           "value": round(_mean(det_speeds), 2)},
        {"dimension": "Normalized TTM",           "value": round(_mean(mit_speeds), 2)},
    ]
    
    if sla_thresholds:
        ttd_compliance = 1 - (ttd_breaches / total_cats if total_cats else 0)
        ttm_compliance = 1 - (ttm_breaches / total_cats if total_cats else 0)
        overall_compliance = (ttd_compliance + ttm_compliance) / 2
        
        dimensions.extend([
            {"dimension": "TTD SLA Compliance Rate",   "value": round(ttd_compliance, 2)},
            {"dimension": "TTM SLA Compliance Rate",   "value": round(ttm_compliance, 2)},
            {"dimension": "Overall SLA Compliance",    "value": round(overall_compliance, 2)},
        ])
    
    return {
        "dimensions": dimensions,
        "normalized_per_category": per_category,
        "sla_metadata": {
            "sla_enabled": bool(sla_thresholds),
            "ttd_breaches": ttd_breaches,
            "ttm_breaches": ttm_breaches,
        }
    }
```

---

## Summary: Best Practices

### Without SLA (Phase A)
✅ Use **severity-based thresholds** (60s for critical, 120s for severe)
✅ Weight **p95 percentile** heavily (30-35% weight)
✅ **Couple** TTD score with detection rate
✅ Include **std_dev/CV stability factor**

### With SLA (Phase B+C)
✅ Extract SLA from `ground_truth/{category}/sla.yaml`
✅ Use **SLA threshold as normalization ceiling** (not fixed 1800)
✅ Apply **breach penalties** (overage ratio > 1.5x = score 0)
✅ Add **SLA compliance rate** as scorecard dimension
✅ Track **per-category SLA breaches** for hypothesis testing

---

## References
- [Current Code: scorecard_builder.py](./cert_builder/scripts/computation/scorecard_builder.py)
- [Current Config: scorecard_config.yaml](./cert_builder/config/scorecard_config.yaml)
- [SLA Plan Doc: SLA_PLAN.md](./documents/SLA_PLAN.md)
- [Aggregator Logic: numeric_aggregation.py](./aggregator/scripts/numeric_aggregation.py)
