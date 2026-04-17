# H-05: Consistency & Predictability (Variance Stability) — Deep Dive

> **Primary metric:** `time_to_detect` (seconds)  
> **Data:** 160 detected runs across 3 categories, 8 sub-faults

---

## 0. The Question

> "Can you rely on this agent to behave the same way every time — or is it fast one minute and slow the next?"

An agent that detects in 120s one time and 800s the next is operationally unreliable, even if its average is acceptable. H-05 quantifies this unpredictability.

---

## 1. The Two Tools

### 1.1 Coefficient of Variation (CV)

**What:** CV = standard deviation / mean. A scale-free measure of relative variability.

**Why CV instead of just standard deviation?**

| Metric | Mean | Std | CV | Interpretation |
|--------|------|-----|----|----|
| TTD (app) | 127.5s | 31.1s | 0.24 | Moderate — most runs land within ±25% of the mean |
| TTD (net) | 233.7s | 271.9s | 1.16 | UNSTABLE — std exceeds the mean! Values range from 13s to 853s |
| Hallucination (app) | 0.08 | 0.09 | 1.14 | UNSTABLE — but this is inherent to near-zero metrics |

Without CV, you'd compare std=31s vs std=272s and conclude "network is worse." But you'd miss that 31s relative to a 128s mean (24% variation) is actually quite different from 272s relative to 234s mean (116% variation).

**Thresholds (from SRE practice):**

```
CV < 0.15  →  ✅ STABLE      Most runs within ±15% of mean
0.15-0.30  →  ⚠️ MODERATE    Some variability, use CI bounds not point estimates
CV ≥ 0.30  →  ❌ UNSTABLE    Agent is erratic — cannot be relied on
```

### 1.2 Levene's Test

**What:** Tests whether the *spread* (variance) of data is equal across groups.

**Why not just compare CVs directly?** Two categories might have different CVs just by chance. Levene's test tells you whether the variance difference is statistically significant.

**Why Levene's (not Bartlett's)?** Bartlett's test assumes normal distributions. Levene's uses deviations from the *median*, making it robust to non-normal data — critical for our bimodal network fault data.

---

## 2. Raw Data

### 2.1 time_to_detect — Per Sub-Fault Variance

| Category | Sub-Fault | n | Mean | Std | CV | Flag |
|----------|-----------|---|------|-----|----|----|
| **application_fault** | container-kill | 28 | 123.4s | 25.1s | 0.20 | ⚠️ moderate |
| | pod-delete | 23 | 132.6s | 37.1s | 0.28 | ⚠️ moderate |
| | **POOLED** | **51** | **127.5s** | **31.1s** | **0.24** | **⚠️ moderate** |
| **network_fault** | pod-dns-error | 13 | 190.6s | 259.5s | 1.36 | ❌ unstable |
| | pod-network-corruption | 14 | 310.5s | 323.7s | 1.04 | ❌ unstable |
| | pod-network-loss | 12 | 190.7s | 215.4s | 1.13 | ❌ unstable |
| | **POOLED** | **39** | **233.7s** | **271.9s** | **1.16** | **❌ unstable** |
| **resource_fault** | disk-fill | 25 | 216.9s | 45.1s | 0.21 | ⚠️ moderate |
| | pod-cpu-hog | 25 | 234.7s | 66.7s | 0.28 | ⚠️ moderate |
| | pod-memory-hog | 20 | 280.3s | 76.8s | 0.27 | ⚠️ moderate |
| | **POOLED** | **70** | **241.4s** | **67.5s** | **0.28** | **⚠️ moderate** |

**Key observations:**
- **Application:** Tight cluster. Both sub-faults have CV ~0.20-0.28. Predictable.
- **Network:** Every sub-fault has CV > 1.0. This is the bimodal distribution at work — some detections are fast (20-80s), others extremely slow (500-850s). The agent either nails it or completely struggles.
- **Resource:** Moderate variability (CV 0.21-0.28). Reasonable for SRE.

---

## 3. Step-by-Step: The Pipeline

### 3.1 Computing CV

For each category, pool all sub-fault values and compute:

```
CV = std(pooled) / mean(pooled)
```

**Application fault:**
```
mean = 127.51s
std  = 31.10s  (using ddof=1, sample std)
CV   = 31.10 / 127.51 = 0.244
Classification: 0.15 ≤ 0.244 < 0.30 → ⚠️ MODERATE
```

**Network fault:**
```
mean = 233.69s
std  = 271.90s  (std > mean! classic bimodal indicator)
CV   = 271.90 / 233.69 = 1.164
Classification: 1.164 ≥ 0.30 → ❌ UNSTABLE
```

**Resource fault:**
```
mean = 241.35s
std  = 67.47s
CV   = 67.47 / 241.35 = 0.280
Classification: 0.15 ≤ 0.280 < 0.30 → ⚠️ MODERATE
```

### 3.2 Levene's Test

**How it works internally:**

1. For each group, compute the median
2. Replace each value with its absolute deviation from the group median: `d_i = |x_i - median|`
3. Run a standard ANOVA (F-test) on these deviations
4. If the F-test is significant, the groups have unequal spread

**Why median, not mean?** Using median makes Levene's test robust to outliers and non-normal data.

**Our data:**

```
Step 1: Compute group medians
  application: median = 123.5s
  network:     median =  80.2s
  resource:    median = 250.4s

Step 2: Absolute deviations from median
  application: |123.4 - 123.5| = 0.1, |120.1 - 123.5| = 3.4, ...
  network:     |12.9 - 80.2| = 67.3, |34.5 - 80.2| = 45.7, ...
  resource:    |111.8 - 250.4| = 138.6, |134.0 - 250.4| = 116.4, ...

Step 3: ANOVA F-test on the deviations
  F = 16.9828
  p ≈ 0.000000
```

**Interpretation:**
- F = 16.98 with df = (2, 157), p ≈ 0 → **Variances are NOT equal**
- The spread of detection times differs significantly across categories
- Network fault (std=272s) has **dramatically** more variance than application (std=31s) — an 8.7× difference

```
Variance comparison:
  application:  ▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░  std = 31s
  resource:     ▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░  std = 68s
  network:      ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  std = 272s
                └──────────────────────────────┘
                0s                           300s
```

---

## 4. Results Across All Metrics

### 4.1 Summary Table

| Metric | app CV | net CV | res CV | Levene p | Variances equal? |
|--------|--------|--------|--------|----------|-----------------|
| time_to_detect | 0.24 ⚠️ | **1.16 ❌** | 0.28 ⚠️ | ≈0.000 | No |
| time_to_mitigate | 0.23 ⚠️ | **0.64 ❌** | **0.31 ❌** | ≈0.000 | No |
| reasoning_score | 0.29 ⚠️ | **0.38 ❌** | 0.28 ⚠️ | 0.955 | Yes |
| hallucination_score | **1.14 ❌** | **0.56 ❌** | **0.87 ❌** | 0.0001 | No |

### 4.2 Interpretation per Metric

**time_to_detect (CV: 0.24 / 1.16 / 0.28):**
- Network is wildly unpredictable (CV=1.16 — std exceeds mean)
- Application and resource are moderate — workable with CI bounds
- Levene confirms: variance is unequal (p≈0)

**time_to_mitigate (CV: 0.23 / 0.64 / 0.31):**
- Network again the worst (CV=0.64)
- Resource crosses into unstable territory (CV=0.31) — mitigation times are more variable than detection
- Application is the most predictable

**reasoning_quality_score (CV: 0.29 / 0.38 / 0.28):**
- Interesting: Levene says variances ARE equal (p=0.95) — all three categories have similar spread
- But network's CV=0.38 crosses the unstable threshold because its mean is low (4.4/10)
- This means the agent's reasoning is *consistently* mediocre for network faults, but *inconsistently* mediocre

**hallucination_score (CV: 1.14 / 0.56 / 0.87):**
- ALL categories are unstable — hallucination scores are inherently noisy
- High CV is expected for near-zero metrics (app mean=0.08, so even small fluctuations create high CV)
- Levene confirms unequal variance (p=0.0001) — network has higher absolute hallucination variance

---

## 5. Decision Flow

```
                    ┌──────────────────────────────┐
                    │  INPUT: Pooled values per     │
                    │  category (from sub-faults)   │
                    └──────────────┬───────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
     ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
     │ Application    │  │  Network       │  │  Resource      │
     │ mean=127.5s    │  │  mean=233.7s   │  │  mean=241.4s   │
     │ std=31.1s      │  │  std=271.9s    │  │  std=67.5s     │
     │ CV=0.24 ⚠️     │  │  CV=1.16 ❌    │  │  CV=0.28 ⚠️    │
     └────────┬───────┘  └────────┬───────┘  └────────┬───────┘
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  Levene's Test               │
                    │  H₀: σ²_app = σ²_net = σ²_res│
                    │  F = 16.98,  p ≈ 0.000       │
                    │  → REJECT H₀                  │
                    │  Variances are UNEQUAL        │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  VERDICT                     │
                    │  variance_instability_detected│
                    │  Unstable: network_fault     │
                    │  (CV=1.16, all sub-faults ❌) │
                    └──────────────────────────────┘
```

---

## 6. Final Verdict

### H-05 Result: `variance_instability_detected`

| Aspect | Finding |
|--------|---------|
| **H₀ (Null)** | Variance is equal across all fault categories |
| **Decision** | ❌ **REJECT H₀** — variances differ significantly (Levene p≈0) |
| **Most unstable** | **network_fault** — CV=1.16 for TTD, 0.64 for TTM |
| **Most predictable** | **application_fault** — CV=0.24 for TTD, 0.23 for TTM |
| **Universal instability** | Hallucination scores are unstable for ALL categories (CV > 0.56) |

### What this means for certification:

1. **Network fault is unreliable:** The agent sometimes detects in 20s, sometimes in 850s. You cannot set meaningful SLA expectations.
2. **Application fault is the safest bet:** Tight variance (CV=0.24). CI bounds from H-01 are actionable.
3. **Hallucination is inherently noisy:** All categories have high CV. This is partly a scale artifact (near-zero means amplify CV), but also genuine unpredictability.
4. **Operational recommendation:** For network faults, implement fallback mechanisms or ensemble approaches. Do NOT rely on single-agent detection.

### Connection to other hypotheses:

| H-05 finding | Explains... |
|-------------|-------------|
| Network CV=1.16 (TTD) | H-01's wide CI for network (78s–271s width) |
| Network all sub-faults unstable | H-03's bimodal distribution flagged by Shapiro-Wilk |
| Hallucination universally unstable | Why H-01 hallucination CIs are relatively wide for all categories |

---

## 7. Why Each Method Was Chosen

| Method | Why | Alternative considered |
|--------|-----|----------------------|
| **Levene's test** (median) | Robust to non-normal data, outlier-resistant | Bartlett's — requires normality, fails on network data |
| **CV** (not std) | Scale-free — comparable across TTD (seconds), reasoning (0-10), hallucination (0-1) | Raw std — not comparable across scales |
| **CV thresholds** | From SRE/operations practice where <15% variation is considered reliable | No universal standard — thresholds are configurable |
| **Pooled aggregation** | Levene needs raw distributions, not summary statistics | Equal-weight would produce only 2-3 values per category |

---

## Appendix: Method References

1. **Levene's Test** — Levene (1960), *Contributions to Probability and Statistics*. `scipy.stats.levene(center='median')`
2. **Coefficient of Variation** — Pearson (1896). Computed as `std(ddof=1) / mean`.
3. **Brown-Forsythe variant** — Brown & Forsythe (1974). Same as Levene with `center='median'` (our implementation).
