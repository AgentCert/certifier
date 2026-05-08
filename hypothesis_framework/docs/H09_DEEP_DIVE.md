# H-09: Temporal Stability & Drift Detection — Deep Dive

> **Primary metrics:** `time_to_detect` (seconds), `time_to_mitigate` (seconds)  
> **Data:** 160 detected runs across 3 categories, 8 sub-faults (in run order)  
> **Mode:** Always active — no SLA thresholds required

---

## 0. The Question

> "Is the agent's performance stable over time — or is it getting worse (or better) as runs progress?"

H-01 through H-08 treat all runs as exchangeable — they don't care about ordering. But what if the agent degrades over time? Run 1 might detect in 50s and run 30 in 500s. H-09 uses control charts to detect this drift.

### H-09 vs H-05

| Test | Question | Method |
|------|----------|--------|
| **H-05** | Is variance consistent across categories? | Levene's test + CV |
| **H-09** | Is performance drifting *over time*? | CUSUM + EWMA control charts |

H-05 measures spread. H-09 measures trend.

---

## 1. The Two Tools

### 1.1 CUSUM (Cumulative Sum Control Chart)

**What:** Tracks cumulative deviations from a target value. Sensitive to sustained shifts.

**How it works:**
```
For each observation x_i:
  S_i = max(0, S_{i-1} + (x_i - target) - k)

where:
  target = IQM of the data (baseline performance)
  k = 0.5 × std  (allowable slack — small random deviations don't accumulate)
  h = 5 × std    (alarm threshold)

If S_i > h → ALARM (systematic upward drift detected)
```

**Intuition:** Each observation above the target adds to the sum. Each below subtracts. Random fluctuations cancel out (aided by the slack `k`). Only a sustained shift causes the sum to accumulate past the threshold.

### 1.2 EWMA (Exponentially Weighted Moving Average)

**What:** A smoothed moving average that gives more weight to recent observations.

**How it works:**
```
Z_i = λ × x_i + (1 - λ) × Z_{i-1}

where:
  λ = 0.2 (smoothing factor — 20% weight on current, 80% on history)
  Z_0 = target (IQM of data)

Control limits:
  UCL = target + L × σ × √(λ / (2 - λ))
  LCL = target - L × σ × √(λ / (2 - λ))

If Z_i > UCL or Z_i < LCL → ALARM
```

**Intuition:** EWMA is like a weighted average that "remembers" recent history. If performance gradually worsens, the EWMA drifts toward the control limit and triggers an alarm. It's more sensitive to gradual drift than CUSUM.

### Why both CUSUM and EWMA?

| Method | Sensitive to | Blind spot |
|--------|-------------|------------|
| **CUSUM** | Sustained step changes | Gradual drift may take longer to detect |
| **EWMA** | Gradual smooth trends | May alarm on temporary spikes |

Using both provides complementary coverage. Drift is declared if **either** alarms.

---

## 2. Drift Verdicts

### Per-Sub-Fault

| Verdict | Condition |
|---------|-----------|
| **STABLE** | Neither CUSUM nor EWMA alarm |
| **DRIFT_DETECTED** | CUSUM and/or EWMA alarm triggered |
| **LOW_POWER** | n < 8 — too few observations for reliable drift detection |

### Category Rollup

| Condition | Category Verdict |
|-----------|-----------------|
| Any sub-fault DRIFT_DETECTED | DRIFT_DETECTED |
| All sub-faults LOW_POWER | LOW_POWER |
| Otherwise | STABLE |

---

## 3. Raw Data (TTD)

| Category | Sub-Fault | n | CUSUM alarm | EWMA alarm | Verdict |
|----------|-----------|---|-------------|------------|---------|
| application | container-kill | 28 | No | No | ✅ STABLE |
| application | pod-delete | 23 | No | No | ✅ STABLE |
| network | pod-dns-error | 13 | No | No | ✅ STABLE |
| network | pod-net-corruption | 14 | No | No | ✅ STABLE |
| network | pod-net-loss | 12 | No | No | ✅ STABLE |
| resource | disk-fill | 25 | No | No | ✅ STABLE |
| resource | pod-cpu-hog | 25 | No | No | ✅ STABLE |
| resource | pod-memory-hog | 20 | No | No | ✅ STABLE |

**Key observation:** No drift detected in any sub-fault. The agent's performance is stable over time — neither improving nor degrading across runs.

---

## 4. Step-by-Step Example: container-kill

```
Input: 28 detected TTD values in run order
  [112.4, 108.7, 145.2, 99.8, 123.4, ..., 135.1, 120.8]

Step 1: Compute target and thresholds
  target (IQM) = trim_mean(values, 0.25) ≈ 122.7s
  std = 25.1s (sample std)
  k = 0.5 × 25.1 = 12.55  (CUSUM slack)
  h = 5 × 25.1 = 125.5    (CUSUM alarm threshold)

Step 2: CUSUM computation
  S_0 = 0
  S_1 = max(0, 0 + (112.4 - 122.7) - 12.55) = max(0, -22.85) = 0
  S_2 = max(0, 0 + (108.7 - 122.7) - 12.55) = max(0, -26.55) = 0
  S_3 = max(0, 0 + (145.2 - 122.7) - 12.55) = max(0, 9.95) = 9.95
  ... values fluctuate but never exceed h=125.5
  CUSUM final = small value, no alarm

Step 3: EWMA computation
  λ = 0.2, L = 3.0
  EWMA_se = 25.1 × √(0.2 / 1.8) = 8.37
  UCL = 122.7 + 3 × 8.37 = 147.8
  LCL = 122.7 - 3 × 8.37 = 97.6

  Z_0 = 122.7
  Z_1 = 0.2 × 112.4 + 0.8 × 122.7 = 120.6
  Z_2 = 0.2 × 108.7 + 0.8 × 120.6 = 118.2
  ... all Z values stay within [97.6, 147.8]
  No alarm

Verdict: STABLE — agent performance is consistent over time
```

---

## 5. What Drift Would Look Like

Even though our data shows no drift, here's what drift patterns look like:

```
Gradual degradation (EWMA catches first):
  Run 1-10:  TTD ≈ 120s (normal)
  Run 11-20: TTD ≈ 150s (slowly worsening)
  Run 21-30: TTD ≈ 200s (clearly worse)
  → EWMA smoothly tracks upward, crosses UCL around run 15-20

Step change (CUSUM catches first):
  Run 1-15:  TTD ≈ 120s (normal)
  Run 16-30: TTD ≈ 250s (sudden jump)
  → CUSUM accumulates rapidly after run 16, crosses h quickly

Cyclic pattern (may trigger false alarms):
  Run 1-10:  TTD ≈ 100s
  Run 11-20: TTD ≈ 200s
  Run 21-30: TTD ≈ 100s
  → May trigger alarm during the high phase, but it's cyclical not drift
```

---

## 6. Final Verdict

### H-09 Result: `no_drift_detected`

| Aspect | Finding |
|--------|---------|
| **Overall** | ✅ No drift detected |
| **All categories** | STABLE |
| **All sub-faults** | STABLE |
| **Implication** | Performance issues (H-06, H-07) are systemic, not degrading |

### What this means for certification:

1. **Performance is consistent over time:** The high detection times and SLA breaches from H-06/H-07 are not getting worse — they're a stable (bad) baseline.
2. **No retraining urgency:** If drift were detected, it would suggest model degradation requiring retraining. Stable performance means the issues are architectural, not temporal.
3. **SLA assessments are reliable:** H-01 through H-08 results can be trusted because the underlying data is stationary. Drift would invalidate time-aggregated statistics.
4. **Caveat — small samples:** With 12-28 runs per sub-fault, we have limited power to detect gradual drift. CUSUM/EWMA are directional indicators at this sample size.

### Connection to other hypotheses:

| H-09 finding | Implication for... |
|-------------|-----------------|
| No drift | H-01 CIs are valid (stationarity assumption holds) |
| Stable bad performance | H-06/H-07 failures are systemic, not temporal |
| All sub-faults stable | H-05 variance is intrinsic, not caused by drift |

---

## 7. Why Each Method Was Chosen

| Method | Why | Alternative |
|--------|-----|------------|
| **CUSUM** | Optimal for detecting sustained step changes | Moving average — less sensitive to shifts |
| **EWMA** | Catches gradual trends with exponential weighting | ARIMA — requires more data and assumptions |
| **IQM as target** | Robust baseline — outliers don't skew the reference | Mean — inflated by extreme values |
| **Both CUSUM + EWMA** | Complementary sensitivity profiles | Single method — would miss some drift patterns |
| **Per-sub-fault** | Different faults may drift independently | Pooled — would mask sub-fault-specific drift |
| **LOW_POWER threshold (n < 8)** | Control charts need minimum observations for reliability | No threshold — would give false confidence |

---

## Appendix: Method References

1. **CUSUM** — Page, E.S. (1954), *Biometrika*, 41(1-2). Upper one-sided cumulative sum control chart.
2. **EWMA** — Roberts, S.W. (1959), *Technometrics*. Exponentially weighted moving average with steady-state control limits.
3. **Control chart theory** — Montgomery, D.C. (2012), *Introduction to Statistical Quality Control*, 7th ed.
