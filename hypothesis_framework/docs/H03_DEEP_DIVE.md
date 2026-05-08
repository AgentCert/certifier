# H-03: Cross-Category Performance Comparison — Deep Dive

> **Metric used throughout:** `time_to_detect` (seconds)
> **Data:** 240 fault injection runs across 3 categories, 8 sub-faults

---

## 0. The Question

> "Does the SRE-Agent handle all fault categories equally — or does it struggle with specific ones?"

If the agent takes 125s to detect application faults but 250s for resource faults, that's a real operational gap that should appear on the certification report.

---

## 1. Raw Data

### 1.1 Sub-Fault Breakdown (detected-only runs)

Each category has multiple sub-faults. Only runs where `fault_detected == "Yes"` are included:

| Category | Sub-Fault | n | Min | Median | Mean | IQM | Max | Std |
|----------|-----------|---|-----|--------|------|-----|-----|-----|
| **application_fault** | container-kill | 28 | 70.1s | 120.1s | 123.4s | 122.9s | 195.2s | 25.1s |
| | pod-delete | 23 | 69.9s | 130.9s | 132.6s | 131.2s | 197.4s | 37.1s |
| **network_fault** | pod-dns-error | 13 | 12.9s | 60.0s | 190.6s | 70.0s | 782.9s | 259.5s |
| | pod-network-corruption | 14 | 51.9s | 136.3s | 310.5s | 215.4s | 852.7s | 323.7s |
| | pod-network-loss | 12 | 24.0s | 77.0s | 190.7s | 86.7s | 644.4s | 215.4s |
| **resource_fault** | disk-fill | 25 | 111.8s | 226.7s | 216.9s | 223.4s | 283.8s | 45.1s |
| | pod-cpu-hog | 25 | 131.9s | 262.7s | 234.7s | 235.5s | 354.4s | 66.7s |
| | pod-memory-hog | 20 | 121.2s | 289.6s | 280.3s | 290.7s | 412.4s | 76.8s |

**Key observations from raw data:**
- **Application:** Tight, stable distribution. Both sub-faults cluster around 120-130s.
- **Network:** Wild bimodal spread (many fast detections + a few extreme outliers 600-850s). Mean >> Median because of right tail.
- **Resource:** Higher baseline (200-290s range) with moderate spread. pod-memory-hog is notably slower.

### 1.2 Pooled Category Summary

All sub-fault values combined per category:

| Category | n | Median | Mean | IQM | P25 | P75 | P95 | Std |
|----------|---|--------|------|-----|-----|-----|-----|-----|
| application_fault | 51 | 123.5s | 127.5s | 125.2s | 104.6s | 144.4s | 188.5s | 31.1s |
| network_fault | 39 | 80.2s | 233.7s | 120.6s | 59.5s | 413.3s | 797.5s | 271.9s |
| resource_fault | 70 | 250.4s | 241.4s | 241.9s | 190.2s | 283.4s | 354.3s | 67.5s |

**Notice:** Network's mean (233.7s) is 3× its median (80.2s) — classic sign of a heavily right-skewed/bimodal distribution. This is exactly why we use nonparametric tests.

---

## 2. Pre-Checks

### 2.1 Shapiro-Wilk Normality Test

**Purpose:** Check if the data is normally distributed. This is *informational* — we always use the nonparametric path (KW + MW) because SRE data is rarely normal.

| Category | W statistic | p-value | Normal? |
|----------|-------------|---------|---------|
| application_fault | 0.974063 | 0.3236 | ✅ Yes (p ≥ 0.05) |
| network_fault | 0.711807 | 0.0000 | ❌ No (p < 0.05) |
| resource_fault | 0.980881 | 0.3617 | ✅ Yes (p ≥ 0.05) |

**Interpretation:**
- Application and resource faults are approximately normal.
- **Network fault is strongly non-normal** (W=0.71, p≈0). This is the bimodal distribution we saw in the raw data.
- Since at least one category is non-normal, parametric tests (Welch ANOVA) would be unreliable. **We use Kruskal-Wallis.**

### 2.2 Within-Category Heterogeneity Check

**Purpose:** Do sub-faults within each category behave similarly? If not, pooling them may mask important differences.

We run **Kruskal-Wallis within each category** (among its sub-faults):

| Category | KW H | p-value | Sub-fault IQMs | Heterogeneous? |
|----------|------|---------|----------------|----------------|
| application_fault | 0.8429 | 0.3586 | container-kill: 122.9s, pod-delete: 131.2s | ❌ No |
| network_fault | 3.2055 | 0.2013 | dns-error: 70.0s, net-corruption: 215.4s, net-loss: 86.7s | ❌ No |
| resource_fault | 10.0377 | **0.0066** | disk-fill: 223.4s, cpu-hog: 235.5s, memory-hog: 290.7s | ⚠️ **Yes** |

**Interpretation:**
- **Application:** Both sub-faults are similar (~123s vs ~131s). Pooling is fine.
- **Network:** Despite visually different IQMs (70 vs 215 vs 87), the high variance within each sub-fault means the KW test can't distinguish them statistically (p=0.20). Pooling is acceptable.
- **Resource:** ⚠️ Sub-faults **are** significantly different (p=0.007). pod-memory-hog (IQM=291s) is notably slower than disk-fill (223s). Pooling this category mixes genuinely different sub-fault behaviors. **Cross-category results involving resource_fault should be interpreted with caution.**

---

## 3. Step-by-Step: The 3-Stage Pipeline

### 3.1 STAGE 1 — Kruskal-Wallis H Test (Omnibus)

**What it does:** Tests whether *at least one* of the k groups (categories) comes from a different distribution. It's a rank-based test — it doesn't assume normality.

**How it works internally:**
1. Pool ALL values from all categories and rank them (1 = smallest, N = largest)
2. Compute mean rank for each category
3. H statistic measures how much the mean ranks differ from what you'd expect if all groups were identical
4. Under H₀ (no difference), H follows a χ² distribution with k-1 degrees of freedom

**Our data (3 groups, df=2):**

```
Input:
  application_fault: 51 values (ranks tend to be LOW — fast detection)
  network_fault:     39 values (ranks are MIXED — bimodal)
  resource_fault:    70 values (ranks tend to be HIGH — slow detection)

Result:
  H = 55.1634
  p = 1.05 × 10⁻¹²
  df = 2
```

**Interpretation:**
- H = 55.16 with df=2 is **extremely large** (critical value at α=0.05 is ~5.99)
- p = 1.05 × 10⁻¹² — essentially zero
- **We reject H₀**: at least one category has a significantly different detection time distribution
- **But:** KW doesn't tell us *which* pair(s) differ. For that → Stage 2.

```
          ┌─────────────────┐
          │  Kruskal-Wallis  │
          │   H = 55.16     │
          │   p ≈ 0.000     │
          │   SIGNIFICANT    │
          └────────┬────────┘
                   │
         ┌─────────▼──────────┐
         │  Which pairs differ?│
         │   → Mann-Whitney U  │
         └─────────┬──────────┘
                   │
    ┌──────────────┼──────────────┐
    ▼              ▼              ▼
  App vs Net    App vs Res    Net vs Res
```

### 3.2 STAGE 2 — Mann-Whitney U (Pairwise Post-Hoc)

**What it does:** For each pair of categories, tests whether one distribution is shifted relative to the other. Non-parametric (rank-based).

**How it works internally:**
1. For each pair (A, B), rank all values from both groups combined
2. U statistic = sum of ranks in group A minus the minimum possible sum
3. Large U means group A tends to have higher ranks (higher values)
4. Small U means group A tends to have lower ranks (lower values)

**Our 3 pairwise tests:**

| # | Pair | n₁ | n₂ | U statistic | p-value (raw) |
|---|------|----|----|-------------|---------------|
| 1 | application vs network | 51 | 39 | 1245.0 | 0.041790 |
| 2 | application vs resource | 51 | 70 | 211.0 | ≈0.000000 |
| 3 | network vs resource | 39 | 70 | 784.0 | 0.000243 |

**Reading the U statistic:**
- Max possible U = n₁ × n₂ (every value in group A beats every value in group B)
- For app vs resource: U = 211 out of max 3570 (51×70) = 5.9% → resource almost always higher
- For app vs network: U = 1245 out of max 1989 (51×39) = 62.6% → app slightly higher

#### 3.2.1 Holm-Bonferroni Multiple Testing Correction

**Why needed:** When you do 3 tests at α=0.05, the probability of *at least one* false positive is ~14% (1 - 0.95³), not 5%. Holm-Bonferroni adjusts p-values upward to maintain the family-wise error rate at 5%.

**How it works (step by step):**

1. **Sort** all raw p-values from smallest to largest
2. **Multiply** the smallest by m (number of tests), the next by m-1, etc.
3. **Enforce monotonicity** — each adjusted p must be ≥ the previous one

```
Step 1: Sort raw p-values
  Rank 1 (smallest): app vs resource     p_raw = 0.000000
  Rank 2:            net vs resource      p_raw = 0.000243
  Rank 3 (largest):  app vs network       p_raw = 0.041790

Step 2: Multiply by (m - rank + 1) where m = 3
  Rank 1: 0.000000 × 3 = 0.000000
  Rank 2: 0.000243 × 2 = 0.000486
  Rank 3: 0.041790 × 1 = 0.041790

Step 3: Enforce monotonicity (each ≥ previous)
  Rank 1: max(0.000000)           = 0.000000  ← final p_adj
  Rank 2: max(0.000000, 0.000486) = 0.000486  ← final p_adj
  Rank 3: max(0.000486, 0.041790) = 0.041790  ← final p_adj
```

**Final results after Holm-Bonferroni:**

| Pair | p_raw | Multiplier | p_adjusted | Significant? |
|------|-------|------------|------------|-------------|
| application vs resource | ≈0.0000 | ×3 | ≈0.0000 | ✅ YES |
| network vs resource | 0.000243 | ×2 | 0.000486 | ✅ YES |
| application vs network | 0.041790 | ×1 | 0.041790 | ✅ YES |

All 3 pairs remain significant after correction. In cases where a borderline p-value (e.g., 0.04) gets multiplied, it could become non-significant — that's the correction working as intended.

### 3.3 STAGE 3 — Vargha-Delaney A₁₂ (Effect Size)

**What it does:** Quantifies *how large* the difference is between two groups. Statistical significance (p < 0.05) tells you a difference *exists*; A₁₂ tells you if it *matters*.

**How it works:**
- A₁₂ = P(random value from group A > random value from group B)
- Computed from the Mann-Whitney U: **A₁₂ = U / (n₁ × n₂)**
- A₁₂ = 0.50 means the groups are identical
- A₁₂ > 0.50 means A tends to be larger
- A₁₂ < 0.50 means B tends to be larger

**Magnitude thresholds (Vargha & Delaney, 2000):**

```
|A₁₂ - 0.50| < 0.06  →  negligible  (practically no difference)
|A₁₂ - 0.50| < 0.14  →  small       (noticeable but minor)
|A₁₂ - 0.50| < 0.21  →  medium      (meaningful in practice)
|A₁₂ - 0.50| ≥ 0.21  →  LARGE       (substantial operational impact)
```

**Our results:**

| Pair | U | n₁×n₂ | A₁₂ = U/(n₁×n₂) | |A₁₂-0.5| | Effect | Plain English |
|------|---|--------|------------------|-----------|--------|---------------|
| app vs net | 1245 | 1989 | **0.626** | 0.126 | **small** | App detection time > network 63% of the time |
| app vs res | 211 | 3570 | **0.059** | 0.441 | **LARGE** | Resource detection time > app 94% of the time |
| net vs res | 784 | 2730 | **0.287** | 0.213 | **LARGE** | Resource detection time > network 71% of the time |

**Detailed interpretation:**

1. **Application vs Network (A₁₂ = 0.626, small):**
   - A random application detection is slower than a random network detection 63% of the time
   - Surprising? Not really — network has many fast detections (median=80s) but also extreme outliers
   - The IQMs are actually similar (app=125s, net=121s), the distributions just have different shapes

2. **Application vs Resource (A₁₂ = 0.059, LARGE):**
   - A random application detection is slower than resource only 6% of the time
   - In other words, **resource fault detection is higher 94% of the time**
   - This is a massive gap: app median=124s vs resource median=250s

3. **Network vs Resource (A₁₂ = 0.287, LARGE):**
   - A random network detection is slower than resource only 29% of the time
   - **Resource fault detection is higher 71% of the time**
   - Despite network having some extreme outliers, resource is consistently high

---

## 4. Complete Decision Flow

```
                        ┌──────────────────────────┐
                        │     INPUT: 3 categories   │
                        │   app(n=51) net(n=39)     │
                        │   resource(n=70)          │
                        └────────────┬─────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │  Pre-check: Shapiro-Wilk  │
                        │  (informational only)     │
                        │  app=normal, net=NON-NORMAL│
                        │  → confirms nonparametric │
                        └────────────┬─────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │  Pre-check: Within-cat KW │
                        │  resource ⚠️ heterogeneous │
                        │  (sub-faults differ)      │
                        └────────────┬─────────────┘
                                     │
              ┌──────────────────────▼──────────────────────┐
              │      STAGE 1: Kruskal-Wallis Omnibus        │
              │      H = 55.16, p = 1.05×10⁻¹²             │
              │      SIGNIFICANT → proceed to pairwise      │
              └──────────────────────┬──────────────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         │                           │                           │
         ▼                           ▼                           ▼
  ┌─────────────┐           ┌─────────────┐           ┌─────────────┐
  │ App vs Net  │           │ App vs Res  │           │ Net vs Res  │
  │ MW p=0.042  │           │ MW p≈0.000  │           │ MW p=0.0002 │
  └──────┬──────┘           └──────┬──────┘           └──────┬──────┘
         │                         │                         │
         ▼                         ▼                         ▼
  ┌─────────────┐           ┌─────────────┐           ┌─────────────┐
  │ Holm p=0.042│           │ Holm p≈0.000│           │ Holm p=0.0005│
  │ → SIG ✅    │           │ → SIG ✅    │           │ → SIG ✅     │
  └──────┬──────┘           └──────┬──────┘           └──────┬──────┘
         │                         │                         │
         ▼                         ▼                         ▼
  ┌─────────────┐           ┌─────────────┐           ┌─────────────┐
  │ A₁₂ = 0.626│           │ A₁₂ = 0.059│           │ A₁₂ = 0.287│
  │ SMALL effect│           │ LARGE effect│           │ LARGE effect│
  │ app slightly│           │ resource WAY│           │ resource    │
  │ higher      │           │ higher      │           │ higher      │
  └─────────────┘           └─────────────┘           └─────────────┘
```

---

## 5. Final Verdict

### H-03 Result: `significant_category_disparity`

| Aspect | Finding |
|--------|---------|
| **H₀ (Null)** | Performance is the same across all fault categories |
| **Decision** | ❌ **REJECT H₀** — categories differ significantly |
| **Weakest category** | **resource_fault** — consistently slowest detection (IQM=242s) |
| **Strongest category** | **application_fault** — fastest and most predictable (IQM=125s, std=31s) |
| **Network caveat** | Bimodal distribution — fast when it works, extremely slow when it doesn't |
| **⚠️ Caveat** | resource_fault is internally heterogeneous — pod-memory-hog (291s) is much slower than disk-fill (223s) |

### What this means for certification:
- The agent has a **proven weakness** on resource fault detection
- **94% of the time**, resource faults take longer to detect than application faults
- The certification report should **flag resource_fault as the weakest category**
- If SLA thresholds are defined (e.g., TTD ≤ 300s), resource fault may breach it more often (→ feeds into H-06 and H-07)

---

## 6. Why Each Method Was Chosen

| Method | Why not the alternative? |
|--------|--------------------------|
| **Kruskal-Wallis** (not Welch ANOVA) | Network fault data is non-normal. KW is rank-based and works regardless of distribution shape. |
| **Mann-Whitney U** (not t-test) | Pairwise version of KW. Consistent nonparametric path — no mixed parametric/nonparametric pipeline. |
| **Holm-Bonferroni** (not Bonferroni) | Holm is strictly more powerful. Regular Bonferroni multiplies ALL p-values by m. Holm multiplies the smallest by m, next by m-1, etc. — less conservative, fewer false negatives. |
| **Vargha-Delaney A₁₂** (not Cohen's d) | A₁₂ is nonparametric and doesn't assume normality. Cohen's d requires normal distributions with similar variances. |
| **Pooled aggregation** (not equal-weight) | KW and MW need raw sample distributions. Equal-weight IQM gives a single number per category — not enough for distribution tests. |

---

## Appendix: Method References

1. **Kruskal-Wallis H Test** — Kruskal & Wallis (1952), *JASA*, 47(260). `scipy.stats.kruskal`
2. **Mann-Whitney U Test** — Mann & Whitney (1947), *Annals of Mathematical Statistics*, 18(1). `scipy.stats.mannwhitneyu`
3. **Holm-Bonferroni** — Holm (1979), *Scandinavian Journal of Statistics*, 6(2). Custom implementation.
4. **Vargha-Delaney A₁₂** — Vargha & Delaney (2000), *JBES*, 25(2). Derived from Mann-Whitney U.
5. **Shapiro-Wilk** — Shapiro & Wilk (1965), *Biometrika*, 52(3-4). `scipy.stats.shapiro`
