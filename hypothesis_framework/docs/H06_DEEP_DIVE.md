# H-06: SLA Threshold Compliance — Deep Dive

> **Primary metrics:** `time_to_detect` (seconds), `time_to_mitigate` (seconds)  
> **Data:** 160 detected runs across 3 categories, 8 sub-faults  
> **SLA source:** `data/groundtruth/kubernetes/*/ground_truth.yaml`

---

## 0. The Question

> "Does the agent meet the SLA thresholds defined for each fault type — or does it systematically violate them?"

An agent that detects `pod-delete` faults in 131s against a 60s SLA is certifiably non-compliant. H-06 tests each sub-fault against its own SLA threshold from the ground truth, then rolls up verdicts to category and overall level.

### H-06 vs H-01 vs H-05

| Test | Question | Method |
|------|----------|--------|
| **H-01** | What's the plausible range of typical performance? | Bootstrap CI on IQM |
| **H-05** | Is the agent's behavior consistent? | Levene's test + CV |
| **H-06** | Does performance **meet SLA requirements**? | Wilcoxon signed-rank vs SLA |

H-01 estimates *how good* the agent is. H-05 checks *how reliable* it is. H-06 asks the operational question: *does it comply with the contract?*

### Why per-sub-fault SLA?

Different faults have different severity and expected response times. A `node-restart` (SLA: 30s TTD) is an obvious cluster event that should be detected immediately. A `pod-network-corruption` (SLA: 240s TTD) is subtle — corrupted packets may take time to surface as application errors. A single SLA for all faults would be meaningless.

---

## 1. SLA Thresholds from Ground Truth

Each fault's ground truth YAML defines three SLA metrics:

| Fault | TTD SLA (s) | TTM SLA (s) | Max Tool Calls |
|-------|-------------|-------------|----------------|
| disk-fill | 60 | 180 | 20 |
| node-restart | 30 | 300 | 20 |
| pod-autoscaler | 120 | 180 | 24 |
| pod-cpu-hog | 120 | 300 | 21 |
| pod-delete | 60 | 120 | 18 |
| pod-dns-error | 60 | 180 | 21 |
| pod-memory-hog | 90 | 240 | 21 |
| pod-network-corruption | 240 | 420 | 21 |
| pod-network-loss | 180 | 360 | 21 |
| pod-network-rate-limit | 180 | 360 | 26 |

**⚠️ Missing:** `container-kill` has no ground truth YAML → marked `NO_SLA_DEFINED`.

### Category-to-sub-fault mapping (in dataset)

| Category | Sub-faults | SLA range (TTD) |
|----------|-----------|-----------------|
| application_fault | container-kill, pod-delete | —, 60s |
| network_fault | pod-dns-error, pod-network-corruption, pod-network-loss | 60s, 240s, 180s |
| resource_fault | disk-fill, pod-cpu-hog, pod-memory-hog | 60s, 120s, 90s |

---

## 2. The Three Tools

### 2.1 Wilcoxon Signed-Rank Test (Primary)

**What:** One-sample non-parametric test — is the median significantly below the SLA threshold?

**Why Wilcoxon (not t-test)?** Our data is non-normal (bimodal network faults, skewed resource faults). Wilcoxon tests the median without normality assumptions.

**Hypotheses:**
```
H₀: median(data) ≥ SLA_threshold     (agent does NOT meet SLA)
Hₐ: median(data) < SLA_threshold     (agent DOES meet SLA)
```

A small p-value means the agent is reliably below the SLA. A large p-value means we cannot confirm compliance.

### 2.2 Bootstrap BCa CI on IQM (Supplementary)

**What:** Bias-corrected and accelerated bootstrap confidence interval on the Interquartile Mean (trimmed mean excluding top/bottom 25%).

**Why:** The CI upper bound gives a worst-case estimate. If `CI_upper ≤ SLA`, we're confident the true typical performance meets the SLA even accounting for sampling uncertainty.

### 2.3 TOST Equivalence Test (Supplementary)

**What:** Two One-Sided Tests — proves the population mean lies *within* [0, SLA].

**Why:** Wilcoxon tests "is it below?" TOST tests "is it demonstrably within bounds?" — a stronger claim of equivalence. When TOST passes, you can assert the metric is operationally within the SLA band, not just marginally below it.

---

## 3. Raw Data vs SLA

### 3.1 time_to_detect

| Category | Sub-Fault | n | Median (s) | SLA (s) | Median/SLA | Status |
|----------|-----------|---|-----------|---------|------------|--------|
| application | container-kill | 28 | 120.1 | — | — | No SLA |
| application | pod-delete | 23 | 130.9 | 60 | 2.18× | ❌ Over |
| network | pod-dns-error | 13 | 60.0 | 60 | 1.00× | ⚠️ Borderline |
| network | pod-network-corruption | 14 | 136.3 | 240 | 0.57× | ✅ Under |
| network | pod-network-loss | 12 | 77.0 | 180 | 0.43× | ✅ Under |
| resource | disk-fill | 25 | 226.7 | 60 | 3.78× | ❌ Over |
| resource | pod-cpu-hog | 25 | 262.7 | 120 | 2.19× | ❌ Over |
| resource | pod-memory-hog | 20 | 289.6 | 90 | 3.22× | ❌ Over |

**Key observations:**
- **pod-delete:** Median is 2.18× the SLA. The agent is consistently slow — 131s vs 60s target.
- **Resource faults are catastrophic:** All three sub-faults exceed SLA by 2-4×. disk-fill at 3.78× is the worst.
- **Network faults show hope:** pod-network-corruption and pod-network-loss medians are well under their (generous) SLAs. But pod-dns-error is right at the boundary.

### 3.2 time_to_mitigate

| Category | Sub-Fault | n | Median (s) | SLA (s) | Median/SLA |
|----------|-----------|---|-----------|---------|------------|
| application | container-kill | 28 | 297.0 | — | — |
| application | pod-delete | 23 | 327.5 | 120 | 2.73× ❌ |
| network | pod-dns-error | 13 | 321.9 | 180 | 1.79× ❌ |
| network | pod-network-corruption | 14 | 521.4 | 420 | 1.24× ❌ |
| network | pod-network-loss | 12 | 429.8 | 360 | 1.19× ❌ |
| resource | disk-fill | 25 | 437.2 | 180 | 2.43× ❌ |
| resource | pod-cpu-hog | 25 | 499.8 | 300 | 1.67× ❌ |
| resource | pod-memory-hog | 20 | 576.6 | 240 | 2.40× ❌ |

**Every sub-fault with an SLA fails mitigation.** The agent is universally slow at mitigation.

---

## 4. Step-by-Step: The Pipeline

### 4.1 Per-Sub-Fault Testing (Example: pod-delete TTD)

```
Input: 23 detected values, SLA = 60s
Median = 130.9s  (already above SLA — bad sign)

Step 1: Wilcoxon signed-rank
  differences = values - 60  (mostly positive since most values > 60)
  H₀: median ≥ 60   vs   Hₐ: median < 60
  W-statistic: very high (ranks of positive differences dominate)
  p = 1.000000  →  Cannot reject H₀
  → Agent does NOT meet the 60s SLA

Step 2: Bootstrap BCa CI on IQM
  IQM (trimmed mean, 25% each side) ≈ 134s
  95% CI: [?, 150.1]
  CI_upper = 150.1 > SLA (60)
  → Even the best-case IQM estimate is 2.5× the SLA

Step 3: TOST equivalence [0, 60]
  mean ≈ 133s >> 60
  Test 2 (H₀: μ ≥ 60): p ≈ 1.0  →  Cannot reject
  TOST: NOT EQUIVALENT
  → Agent is definitively not operating within the SLA band

Verdict: median > SLA AND Wilcoxon not significant → FAIL
```

### 4.2 Per-Sub-Fault Testing (Example: pod-network-loss TTD)

```
Input: 12 detected values, SLA = 180s
Median = 77.0s  (well below SLA — promising)

Step 1: Wilcoxon signed-rank
  differences = values - 180  (mix of negative and positive)
  p = 0.3386  →  Not significant at α=0.05
  → Cannot confirm compliance (sample too small or too variable)

Step 2: Bootstrap BCa CI on IQM
  CI_upper = 323.9 > SLA (180)
  → The CI includes values above SLA — uncertainty is high

Step 3: TOST equivalence [0, 180]
  TOST: NOT EQUIVALENT (high variance prevents equivalence claim)

Verdict: Neither clear PASS nor FAIL → CONDITIONAL
```

**Why CONDITIONAL?** The median (77s) looks good, but with only 12 samples and high variance (CV=1.13 from H-05), the CI is wide. We can't statistically confirm compliance. More data would resolve this.

### 4.3 Category Rollup

```
application_fault:
  container-kill → NO_SLA_DEFINED (no ground truth)
  pod-delete     → FAIL
  Category: FAIL (any FAIL → category FAIL)

network_fault:
  pod-dns-error           → CONDITIONAL
  pod-network-corruption  → CONDITIONAL
  pod-network-loss        → CONDITIONAL
  Category: CONDITIONAL (all CONDITIONAL → category CONDITIONAL)

resource_fault:
  disk-fill     → FAIL
  pod-cpu-hog   → FAIL
  pod-memory-hog → FAIL
  Category: FAIL (any FAIL → category FAIL)
```

### 4.4 Overall Assessment

```
Any category FAIL → sla_non_compliant
```

---

## 5. Verdict Logic

### 5.1 Sub-Fault Verdict Decision Tree

```
                    ┌──────────────────────────────┐
                    │  Sub-fault: values + SLA      │
                    └──────────────┬───────────────┘
                                   │
                         ┌─────────▼─────────┐
                         │  SLA defined?      │
                         └────┬──────────┬────┘
                              │ No       │ Yes
                              ▼          ▼
                        NO_SLA_DEFINED   ┌──────────────────────┐
                                         │  Run Wilcoxon,       │
                                         │  Bootstrap CI, TOST  │
                                         └──────────┬───────────┘
                                                    │
                                    ┌───────────────▼───────────────┐
                                    │  Wilcoxon p < α               │
                                    │  AND CI_upper ≤ SLA?          │
                                    └───────┬───────────────┬───────┘
                                            │ Yes           │ No
                                            ▼               │
                                          PASS    ┌─────────▼─────────┐
                                                  │  Median > SLA     │
                                                  │  AND Wilcoxon     │
                                                  │  not significant? │
                                                  └────┬─────────┬────┘
                                                       │ Yes     │ No
                                                       ▼         ▼
                                                     FAIL    CONDITIONAL
```

### 5.2 Category Rollup Rules

| Sub-fault verdicts | Category verdict |
|-------------------|-----------------|
| All PASS | **PASS** |
| Any FAIL | **FAIL** |
| Any NO_SLA_DEFINED (none FAIL) | **INCOMPLETE** |
| Mix of PASS + CONDITIONAL (none FAIL) | **CONDITIONAL** |
| All NO_DATA | **NO_DATA** |

### 5.3 Overall Rollup

| Category verdicts | Overall |
|------------------|---------|
| All PASS | `sla_compliant` |
| Any FAIL | `sla_non_compliant` |
| Any INCOMPLETE (none FAIL) | `incomplete_coverage` |
| Otherwise | `conditional_compliance` |

---

## 6. Results

### 6.1 time_to_detect

| Category | Sub-Fault | n | Median | SLA | Wilcoxon p | CI Upper | TOST | Verdict |
|----------|-----------|---|--------|-----|-----------|----------|------|---------|
| application | container-kill | 28 | 120.1s | — | — | — | — | ❓ NO_SLA |
| application | pod-delete | 23 | 130.9s | 60s | 1.0000 | 150.1 | ❌ | ❌ FAIL |
| network | pod-dns-error | 13 | 60.0s | 60s | 0.7928 | 279.5 | ❌ | ⚠️ COND |
| network | pod-net-corruption | 14 | 136.3s | 240s | 0.5484 | 534.8 | ❌ | ⚠️ COND |
| network | pod-net-loss | 12 | 77.0s | 180s | 0.3386 | 323.9 | ❌ | ⚠️ COND |
| resource | disk-fill | 25 | 226.7s | 60s | 1.0000 | 241.2 | ❌ | ❌ FAIL |
| resource | pod-cpu-hog | 25 | 262.7s | 120s | 1.0000 | 270.4 | ❌ | ❌ FAIL |
| resource | pod-memory-hog | 20 | 289.6s | 90s | 1.0000 | 325.6 | ❌ | ❌ FAIL |

**Category rollup:** application=FAIL, network=CONDITIONAL, resource=FAIL  
**Overall:** `sla_non_compliant`

### 6.2 time_to_mitigate

| Category | Sub-Fault | Median | SLA | Verdict |
|----------|-----------|--------|-----|---------|
| application | container-kill | 297.0s | — | ❓ NO_SLA |
| application | pod-delete | 327.5s | 120s | ❌ FAIL |
| network | pod-dns-error | 321.9s | 180s | ❌ FAIL |
| network | pod-net-corruption | 521.4s | 420s | ❌ FAIL |
| network | pod-net-loss | 429.8s | 360s | ❌ FAIL |
| resource | disk-fill | 437.2s | 180s | ❌ FAIL |
| resource | pod-cpu-hog | 499.8s | 300s | ❌ FAIL |
| resource | pod-memory-hog | 576.6s | 240s | ❌ FAIL |

**Overall:** `sla_non_compliant` — every assessed sub-fault fails mitigation SLA.

---

## 7. Final Verdict

### H-06 Result: `sla_non_compliant`

| Aspect | Finding |
|--------|---------|
| **H₀ (Null)** | The agent's true median performance does NOT meet the SLA |
| **Decision (TTD)** | ❌ **Cannot reject H₀** for any sub-fault — agent fails or is inconclusive |
| **Decision (TTM)** | ❌ **Cannot reject H₀** for any sub-fault — universal mitigation failure |
| **Worst performer** | disk-fill TTD — median 3.78× over SLA (227s vs 60s) |
| **Closest to passing** | pod-network-loss TTD — median 0.43× SLA but insufficient data |
| **Coverage gap** | container-kill has no ground truth SLA definition |

### What this means for certification:

1. **Detection is slow across the board:** Only network faults (with generous SLAs of 180-240s) come close. Resource faults are catastrophically over SLA.
2. **Mitigation is universally non-compliant:** Even the most generous SLAs (pod-network-corruption at 420s) are exceeded.
3. **Network CONDITIONAL ≠ passing:** The CIs are wide (from H-05's CV > 1.0). More data might resolve these as PASS or FAIL.
4. **container-kill coverage gap:** Cannot certify application_fault fully without SLA for container-kill.
5. **Operational recommendation:** The agent needs significant performance improvements before SLA certification. Focus on resource faults first (3-4× over SLA).

### Connection to other hypotheses:

| H-06 finding | Explained by... |
|-------------|-----------------|
| Network faults CONDITIONAL (not PASS/FAIL) | H-05: network CV=1.16 → wide CIs → statistical uncertainty |
| Resource faults decisive FAIL (p=1.0) | H-01: resource IQM CI well above SLA thresholds |
| pod-dns-error exactly at SLA boundary | H-03: dns-error median=60s but bimodal distribution |
| No TOST equivalence anywhere | H-01: all CI widths too large relative to SLA ranges |

---

## 8. Why Each Method Was Chosen

| Method | Why | Alternative considered |
|--------|-----|----------------------|
| **Wilcoxon signed-rank** | Non-parametric — works for skewed/bimodal data | One-sample t-test — requires normality (fails for network faults) |
| **Bootstrap BCa CI** | Bias-corrected CI on IQM — robust to outliers | Percentile bootstrap — biased for skewed distributions |
| **TOST** | Proves equivalence within [0, SLA] — stronger than "not above" | Simple comparison — doesn't give statistical confidence |
| **Per-sub-fault SLA** | Different faults have different expected response times | Single SLA — meaningless for mixed-severity faults |
| **Category rollup** | Conservative (FAIL if any sub-fault FAIL) | Majority vote — masks individual failures |

---

## 9. Design: Why Per-Sub-Fault (Not Per-Category)

The original H-06 design used a single SLA threshold for all categories. This was fundamentally flawed:

```
Old design (single SLA):
  ┌─────────────────────────────────────────┐
  │  SLA = 300s for ALL categories          │
  │  application: median=128s  → PASS ✅     │
  │  network:     median=234s  → PASS ✅     │
  │  resource:    median=241s  → PASS ✅     │
  │  Overall: sla_compliant                 │
  └─────────────────────────────────────────┘
  
  But this ignores that pod-delete should detect in 60s,
  and disk-fill in 60s! A 300s blanket SLA is meaningless.

New design (per-sub-fault SLA):
  ┌─────────────────────────────────────────┐
  │  pod-delete:  median=131s vs SLA=60s  ❌ │
  │  disk-fill:   median=227s vs SLA=60s  ❌ │
  │  pod-net-loss: median=77s vs SLA=180s ⚠️ │
  │  Overall: sla_non_compliant             │
  └─────────────────────────────────────────┘
  
  Each fault tested against its operational SLA.
```

Category-level statistics (pooled median, pooled Wilcoxon) are **not computed** because different sub-faults have different SLA thresholds. Pooling 60s-SLA data with 240s-SLA data produces meaningless results. Instead, category verdict is a rollup of sub-fault verdicts.

---

## Appendix: Method References

1. **Wilcoxon Signed-Rank** — Wilcoxon (1945), *Biometrics Bulletin*, 1(6). One-sample variant against fixed threshold.
2. **Bootstrap BCa CI** — Efron (1987), *JASA*, 82(397). Bias-corrected and accelerated bootstrap.
3. **TOST Equivalence** — Schuirmann (1987), *J. Pharmacokinetics and Biopharmaceutics*, 15(6).
4. **IQM (Interquartile Mean)** — Agarwal et al. (2021), *NeurIPS*. Trimmed mean robust to outliers.
