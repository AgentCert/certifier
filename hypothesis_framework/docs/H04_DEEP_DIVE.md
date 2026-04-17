# H-04: Cross-Category Success Rate Uniformity — Deep Dive

> **Primary metric:** `fault_detection_rate` (binary: detected or not)  
> **Data:** 240 fault injection runs across 3 categories, 8 sub-faults

---

## 0. The Question

> "Does the agent detect faults at the same rate regardless of fault category — or does it systematically fail on specific categories?"

If the agent detects 85% of application faults but only 43% of network faults, that gap is operationally critical and must appear on the certification report.

### H-04 vs H-02 vs H-03

| Test | Question | Data type | Method |
|------|----------|-----------|--------|
| **H-02** | What's the guaranteed floor for each category? | Binary → Wilson CI | Per-category estimation |
| **H-03** | Do continuous metrics (TTD, TTM) differ across categories? | Continuous | Kruskal-Wallis |
| **H-04** | Do success **rates** differ across categories? | Binary counts | Chi-Square |

H-02 says "how good is each category?" independently.  
H-04 says "are these rates **the same** across categories?" — a comparison test.

---

## 1. Raw Data

### 1.1 Detection Rates per Sub-Fault

| Category | Sub-Fault | Detected | Trials | Rate |
|----------|-----------|----------|--------|------|
| **application_fault** | container-kill | 28 | 30 | **93%** |
| | pod-delete | 23 | 30 | **77%** |
| **network_fault** | pod-dns-error | 13 | 30 | **43%** |
| | pod-network-corruption | 14 | 30 | **47%** |
| | pod-network-loss | 12 | 30 | **40%** |
| **resource_fault** | disk-fill | 25 | 30 | **83%** |
| | pod-cpu-hog | 25 | 30 | **83%** |
| | pod-memory-hog | 20 | 30 | **67%** |

### 1.2 Pooled Category Totals

| Category | Detected | Trials | Rate |
|----------|----------|--------|------|
| application_fault | 51 | 60 | **85.0%** |
| network_fault | 39 | 90 | **43.3%** |
| resource_fault | 70 | 90 | **77.8%** |
| **Overall** | **160** | **240** | **66.7%** |

**Key observations:**
- **Application:** Highest detection rate (85%). container-kill is easier to detect (93%) than pod-delete (77%).
- **Network:** Much lower (43%). All three sub-faults are consistently bad (40–47%). This is the agent's blind spot.
- **Resource:** Good but not great (78%). disk-fill and cpu-hog are similar (83%), memory-hog is harder (67%).

---

## 2. Pre-Check: Within-Category Heterogeneity

**Purpose:** Before comparing categories, check if sub-faults within each category have similar rates. If they don't, pooling hides important differences.

We run **Chi-Square within each category** (among its sub-faults):

### Application Fault (2×2 table)

```
                Detected   Not Detected   Total
container-kill      28           2          30
pod-delete          23           7          30
─────────────────────────────────────────────────
Expected           25.5         4.5         30
```

- χ² = 2.09, p = 0.148 → **Not heterogeneous** ✅
- 93% vs 77% — different but not statistically significant at n=30

### Network Fault (3×2 table)

```
                      Detected   Not Detected   Total
pod-dns-error             13          17          30
pod-network-corruption    14          16          30
pod-network-loss          12          18          30
─────────────────────────────────────────────────────
Expected                 13.0        17.0         30
```

- χ² = 0.27, p = 0.873 → **Not heterogeneous** ✅
- All sub-faults at 40–47% — remarkably uniform within this category

### Resource Fault (3×2 table)

```
                  Detected   Not Detected   Total
disk-fill             25           5          30
pod-cpu-hog           25           5          30
pod-memory-hog        20          10          30
──────────────────────────────────────────────────
Expected             23.3         6.7         30
```

- χ² = 3.21, p = 0.200 → **Not heterogeneous** ✅
- pod-memory-hog (67%) is lower but not significantly so

**Conclusion:** All categories are internally homogeneous — pooling is safe.

---

## 3. Step-by-Step: The Chi-Square Pipeline

### 3.1 Building the Contingency Table

The chi-square test operates on a **contingency table** — a matrix of observed counts:

```
                    Detected   Not Detected   Total
application_fault      51           9           60
network_fault          39          51           90
resource_fault         70          20           90
──────────────────────────────────────────────────────
Total                 160          80          240
```

### 3.2 Computing Expected Frequencies

**Under H₀** (all categories have the same detection rate), the expected count for each cell is:

```
Expected[row, col] = (Row Total × Column Total) / Grand Total
```

**Overall detection rate under H₀:** 160/240 = **66.7%**

```
                    Detected   Not Detected   Total    Rate under H₀
application_fault     40.0        20.0         60      66.7%
network_fault         60.0        30.0         90      66.7%
resource_fault        60.0        30.0         90      66.7%
```

**Key insight:** Under H₀, we'd expect 40 detections in application (not 51), and 60 in network (not 39). The gap between observed and expected drives the chi-square statistic.

### 3.3 Computing the χ² Statistic

The chi-square statistic measures the total discrepancy between observed (O) and expected (E):

```
χ² = Σ (O - E)² / E
```

**Cell-by-cell calculation:**

| Cell | Observed (O) | Expected (E) | O − E | (O − E)² | (O − E)²/E |
|------|-------------|-------------|-------|----------|------------|
| app, detected | 51 | 40.0 | +11.0 | 121.0 | **3.025** |
| app, not-det | 9 | 20.0 | −11.0 | 121.0 | **6.050** |
| net, detected | 39 | 60.0 | −21.0 | 441.0 | **7.350** |
| net, not-det | 51 | 30.0 | +21.0 | 441.0 | **14.700** |
| res, detected | 70 | 60.0 | +10.0 | 100.0 | **1.667** |
| res, not-det | 20 | 30.0 | −10.0 | 100.0 | **3.333** |

```
χ² = 3.025 + 6.050 + 7.350 + 14.700 + 1.667 + 3.333 = 36.125
```

**Degrees of freedom:** (rows − 1) × (cols − 1) = (3 − 1) × (2 − 1) = **2**

### 3.4 Getting the p-value

The χ² statistic follows a chi-square distribution with df=2 under H₀:

```
χ² = 36.125,  df = 2
p = 1.43 × 10⁻⁸
```

**Interpretation:**
- Critical value at α=0.05, df=2 is **5.99**
- Our χ² = 36.12 is **6× the critical value**
- p = 1.43 × 10⁻⁸ — essentially zero
- **We reject H₀:** detection rates are NOT uniform across categories

```
χ² Distribution (df=2)
                                         ┌── Critical value = 5.99
                                         │         Our χ² = 36.12
  ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░              │              │
  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░              │              │
  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░              ▼              ▼
  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░──────────────|──────────────|───
  0                5           10         15         ...  36
                         ◄─── 95% area ──►
                                          ◄── rejection zone (5%)
```

### 3.5 Which category drives the disparity?

Looking at the (O − E)²/E contributions:

| Category | Contribution to χ² | % of total |
|----------|-------------------|------------|
| application_fault | 3.03 + 6.05 = **9.08** | 25% |
| **network_fault** | 7.35 + 14.70 = **22.05** | **61%** |
| resource_fault | 1.67 + 3.33 = **5.00** | 14% |

**Network fault contributes 61% of the chi-square statistic.** It's the primary driver of non-uniformity — its 43% detection rate is far below the expected 67%.

---

## 4. Pairwise Follow-Up: Fisher's Exact Test

The omnibus chi-square tells us rates are non-uniform, but not *which* pairs differ. We use Fisher's exact test (appropriate for 2×2 tables) on each pair:

### 4.1 Application vs Network

```
                    Detected   Not Detected
application_fault      51           9
network_fault          39          51
```

- Odds Ratio = (51 × 51) / (9 × 39) = **7.41**
- Fisher's p ≈ 0.0000 → **Significant** ✅

**Interpretation:** The odds of detection are **7.4× higher** for application faults than network faults. Massive disparity.

### 4.2 Application vs Resource

```
                    Detected   Not Detected
application_fault      51           9
resource_fault         70          20
```

- Odds Ratio = (51 × 20) / (9 × 70) = **1.62**
- Fisher's p = 0.2995 → **Not significant** ❌

**Interpretation:** Application (85%) vs resource (78%) — a 7 percentage point gap, but not statistically significant at n=60/90. The sample size isn't large enough to confirm this small difference.

### 4.3 Network vs Resource

```
                    Detected   Not Detected
network_fault          39          51
resource_fault         70          20
```

- Odds Ratio = (39 × 20) / (51 × 70) = **0.218**
- Fisher's p ≈ 0.000004 → **Significant** ✅

**Interpretation:** The odds of detection are **4.6× higher** for resource faults (1/0.218) than network faults.

### Pairwise Summary

| Pair | Rates | Odds Ratio | p-value | Significant? |
|------|-------|------------|---------|-------------|
| app vs network | 85% vs 43% | 7.41 | ≈0.0000 | ✅ YES |
| app vs resource | 85% vs 78% | 1.62 | 0.2995 | ❌ No |
| network vs resource | 43% vs 78% | 0.22 | 0.000004 | ✅ YES |

**The picture:** Application and resource have similar-ish rates. Network is the outlier.

---

## 5. Mitigation Rate (Second Metric)

Same pipeline for mitigation rates:

### Contingency Table

```
                    Mitigated   Not Mitigated   Total    Rate
application_fault      45           15           60     75.0%
network_fault          14           76           90     15.6%
resource_fault         50           40           90     55.6%
──────────────────────────────────────────────────────────────
Total                 109          131          240     45.4%
```

### Result

- χ² = **57.29**, df = 2, p = 3.63 × 10⁻¹³ → **Significant**
- Even larger disparity than detection

**Why mitigation is worse than detection:**
- Detection gap: 85% vs 43% (42 pp difference)
- Mitigation gap: 75% vs 16% (59 pp difference)
- Network faults: detected 43% of the time, but mitigated only 16% → even when detected, the agent often fails to mitigate

---

## 6. RAI and Security Compliance

### RAI Compliance

```
                    Passed   Failed   Rate
application_fault     60        0     100%
network_fault         90        0     100%
resource_fault        90        0     100%
```

- χ² is **undefined** (zero column — no failures anywhere)
- p = 1.0 → **Uniform** ✅ (trivially — everyone passes)

### Security Compliance

```
                    Passed   Failed   Rate
application_fault      0       60      0%
network_fault          0       90      0%
resource_fault         0       90      0%
```

- χ² is **undefined** (zero column — no passes anywhere)
- p = 1.0 → **Uniform** ✅ (trivially — nobody passes)

**Note:** 100% RAI compliance and 0% security compliance are edge cases where chi-square is undefined. The test gracefully returns "uniform" because there's no variation to test.

---

## 7. Complete Decision Flow

```
                    ┌────────────────────────────────┐
                    │  INPUT: Success/Failure counts  │
                    │  per category (pooled from      │
                    │  sub-faults)                    │
                    └───────────────┬────────────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │  Pre-check: Within-category    │
                    │  Chi-square among sub-faults   │
                    │  All homogeneous ✅             │
                    └───────────────┬────────────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │  Build Contingency Table        │
                    │  ┌──────────┬─────┬───────┐    │
                    │  │          │ Det │ ~Det  │    │
                    │  │ app      │  51 │    9  │    │
                    │  │ network  │  39 │   51  │    │
                    │  │ resource │  70 │   20  │    │
                    │  └──────────┴─────┴───────┘    │
                    └───────────────┬────────────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │  Compute Expected Frequencies  │
                    │  Under H₀: all rates = 66.7%   │
                    │  E[i,j] = RowTotal × ColTotal   │
                    │           / GrandTotal          │
                    └───────────────┬────────────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │  Chi-Square Statistic           │
                    │  χ² = Σ (O-E)²/E = 36.13      │
                    │  df = (3-1)×(2-1) = 2          │
                    │  p = 1.43 × 10⁻⁸               │
                    │  SIGNIFICANT ❌                  │
                    └───────────────┬────────────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │  Identify Weakest Category      │
                    │  network_fault = 43.3%          │
                    │  (expected 66.7% under H₀)     │
                    │  Contributes 61% of χ²         │
                    └────────────────────────────────┘
```

---

## 8. Final Verdict

### H-04 Result: `non_uniform_rates`

| Metric | χ² | p-value | Uniform? | Weakest |
|--------|-----|---------|----------|---------|
| Detection rate | 36.13 | 1.43×10⁻⁸ | ❌ Non-uniform | network_fault (43%) |
| Mitigation rate | 57.29 | 3.63×10⁻¹³ | ❌ Non-uniform | network_fault (16%) |
| RAI compliance | — | 1.0 | ✅ Uniform | — (all 100%) |
| Security compliance | — | 1.0 | ✅ Uniform | — (all 0%) |

### What this means for certification:

1. **Network fault is the agent's Achilles heel** — detection at 43% and mitigation at only 16%
2. **Detection gap:** Application 85% → Network 43% (odds ratio = 7.4×)
3. **Mitigation is worse than detection:** Even when the agent detects a network fault, it usually can't mitigate it (only 36% of detected network faults are mitigated, vs 88% for application)
4. The certification report should **flag network_fault for targeted retraining**
5. **Consistent with H-03:** H-03 found network faults have worst reasoning scores (4.5/10) and highest hallucination (0.23) — explaining *why* detection and mitigation fail

---

## 9. Why Chi-Square (Not Other Methods)

| Method | Why / Why not |
|--------|--------------|
| **Chi-Square** ✅ | Tests whether the distribution of success/failure differs across categories. Works on count data directly. No assumptions about underlying distribution. |
| **Fisher's Exact** (pairwise) | Used for 2×2 follow-up tables. Exact p-values without relying on asymptotic approximation. |
| **Z-test for proportions** ❌ | Only for 2 groups. Chi-square naturally handles 3+ groups. |
| **Logistic regression** ❌ | Overkill for simple rate comparison with no covariates. |
| **Kruskal-Wallis** ❌ | For continuous data — binary success/failure is categorical, not continuous. |

### When Chi-Square Can Fail

- **Expected frequency < 5:** Chi-square approximation becomes unreliable. Our smallest expected cell is 20.0, so no issue here.
- **Zero marginals:** If a row or column sums to 0 (e.g., 100% or 0% rates everywhere), chi-square is undefined. This happened for RAI and security compliance — handled by falling back to "uniform."
- **Small samples:** With very few observations, Fisher's exact test is preferred even for R×C tables.

---

## Appendix: Method References

1. **Pearson's Chi-Square Test** — Pearson (1900), *Phil. Mag.*, Series 5. `scipy.stats.chi2_contingency`
2. **Fisher's Exact Test** — Fisher (1922), *JRSS*. `scipy.stats.fisher_exact`
3. **Wilson Score Interval** — Wilson (1927), *JASA*. `statsmodels.stats.proportion.proportion_confint` (used in sub-fault breakdown)
