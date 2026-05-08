# H-07: SLA Breach Rate Estimation — Deep Dive

> **Primary metrics:** `time_to_detect` (seconds), `time_to_mitigate` (seconds)  
> **Data:** 240 total runs (including non-detected) across 3 categories, 8 sub-faults  
> **SLA source:** `data/groundtruth/kubernetes/*/ground_truth.yaml`

---

## 0. The Question

> "What fraction of runs violate the SLA — and is that fraction acceptably low?"

H-06 asks "does the *median* meet SLA?" H-07 asks a different question: "how often does the agent *breach* SLA?" An agent with a good median can still breach SLA 30% of the time if it has a long tail.

### H-07 vs H-06 vs H-08

| Test | Question | Data | Method |
|------|----------|------|--------|
| **H-06** | Does the median meet SLA? | Detected values | Wilcoxon signed-rank |
| **H-07** | Is the breach *rate* acceptably low? | ALL runs (including non-detected) | Exact binomial |
| **H-08** | How bad are the worst cases? | Detected values | CVaR tail analysis |

H-06 tests central tendency. H-07 tests frequency of violations. H-08 tests severity of violations.

### Why ALL runs (not just detected)?

Non-detected runs are the worst possible SLA breach. If the agent never detects a `pod-delete` fault, that's an infinite `time_to_detect` — a guaranteed SLA violation. Including non-detected runs as breaches prevents the survival bias of only looking at "successful" runs.

---

## 1. The Two Tools

### 1.1 Exact Binomial Test

**What:** Tests whether the true breach rate is below a target (default 5%).

**Hypotheses:**
```
H₀: breach_rate ≥ target_rate    (agent breaches too often)
Hₐ: breach_rate < target_rate    (agent breaches rarely enough)
```

**Why exact binomial (not z-test)?** With small sub-fault samples (n=12-30), the normal approximation is unreliable. The exact binomial test uses combinatorial probability — no approximation.

### 1.2 Wilson Confidence Interval

**What:** Confidence interval on the breach proportion using Wilson score method.

**Why:** The CI lower bound determines the FAIL verdict. If `CI_lower > target_rate`, we're confident the true breach rate exceeds the target even accounting for sampling uncertainty.

---

## 2. Raw Data — Breach Counts (TTD)

| Category | Sub-Fault | Total Runs | Breaches | Breach Rate | SLA | Verdict |
|----------|-----------|-----------|----------|-------------|-----|---------|
| application | container-kill | 30 | — | — | — | NO_SLA |
| application | pod-delete | 30 | 30 | 100% | 60s | ❌ FAIL |
| network | pod-dns-error | 30 | 23 | 77% | 60s | ❌ FAIL |
| network | pod-net-corruption | 30 | 21 | 70% | 240s | ❌ FAIL |
| network | pod-net-loss | 30 | 21 | 70% | 180s | ❌ FAIL |
| resource | disk-fill | 30 | 30 | 100% | 60s | ❌ FAIL |
| resource | pod-cpu-hog | 30 | 30 | 100% | 120s | ❌ FAIL |
| resource | pod-memory-hog | 30 | 30 | 100% | 90s | ❌ FAIL |

**Key observations:**
- **100% breach rates** for pod-delete, disk-fill, pod-cpu-hog, pod-memory-hog — every single run exceeds SLA
- **70-77% breach rates** for network faults — even with generous SLAs (180-240s), most runs breach
- The target is 5% breach rate — all sub-faults are catastrophically above this

---

## 3. Step-by-Step Example: pod-dns-error

```
Input: 30 total runs, SLA = 60s
  - 13 detected with time_to_detect values
  - 17 non-detected → treated as inf (breach)

Breach count: values > 60s
  - Of 13 detected: some above, some below 60s
  - 17 non-detected: all count as breaches (inf > 60)
  - Total breaches: 23/30 = 76.7%

Step 1: Exact Binomial Test
  H₀: p ≥ 0.05 (breach rate at or above 5%)
  23 breaches out of 30 trials, target p₀ = 0.05
  binomtest(23, 30, 0.05, alternative='less')
  p ≈ 1.0 → Cannot reject H₀ (obviously — 77% >> 5%)

Step 2: Wilson CI on breach rate
  Wilson 95% CI: [0.588, 0.889]
  CI lower = 0.588 > 0.05 (target)
  → Breach rate is provably above target

Verdict: CI_lower > target → FAIL
```

---

## 4. Verdict Logic

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
                                         │  Count breaches      │
                                         │  (value > SLA)       │
                                         │  Run binomial + CI   │
                                         └──────────┬───────────┘
                                                    │
                                    ┌───────────────▼───────────────┐
                                    │  Binomial p < α?              │
                                    │  (breach rate provably < 5%)  │
                                    └───────┬───────────────┬───────┘
                                            │ Yes           │ No
                                            ▼               │
                                          PASS    ┌─────────▼─────────┐
                                                  │  Wilson CI lower  │
                                                  │  > target rate?   │
                                                  └────┬─────────┬────┘
                                                       │ Yes     │ No
                                                       ▼         ▼
                                                     FAIL   INCONCLUSIVE
```

### Category Rollup

| Condition | Category Verdict |
|-----------|-----------------|
| Any FAIL | FAIL |
| Any NO_SLA (none FAIL) | INCOMPLETE |
| Any INCONCLUSIVE (none FAIL/INCOMPLETE) | INCONCLUSIVE |
| All PASS | PASS |

---

## 5. Final Verdict

### H-07 Result: `breach_rate_exceeds_target`

| Aspect | Finding |
|--------|---------|
| **Target breach rate** | ≤ 5% |
| **Actual breach rates** | 70% — 100% across all assessed sub-faults |
| **Worst performer** | pod-delete, disk-fill, pod-cpu-hog, pod-memory-hog (100% breach) |
| **Coverage gap** | container-kill has no SLA definition |

### What this means for certification:

1. **Universal SLA violation:** Every sub-fault with a defined SLA breaches it in ≥70% of runs.
2. **The 5% target is unreachable:** The agent would need to improve performance by 10-20× to reach < 5% breach rate.
3. **Non-detection amplifies breach rate:** For network faults, 17/30 runs were non-detected — automatic breaches.
4. **Operational impact:** SLA-based contracts cannot be offered until breach rates drop dramatically.

### Connection to other hypotheses:

| H-07 finding | Explained by... |
|-------------|-----------------|
| 100% breach rate for resource faults | H-06: median 2-4× over SLA |
| 70-77% breach for network faults | H-02: network detection rate only 43% |
| Non-detection as breach amplifier | H-04: network success rate significantly lower |

---

## 6. Why Each Method Was Chosen

| Method | Why | Alternative |
|--------|-----|------------|
| **Exact binomial** | Correct for small n, no normal approximation | z-test — inaccurate at n=12-30 |
| **Wilson CI** | Better coverage than Wald CI for extreme proportions (0% or 100%) | Clopper-Pearson — too conservative |
| **5% target rate** | Standard SRE/SLA threshold for "rare events" | Configurable per use case |
| **Per-sub-fault testing** | Different SLA thresholds → different breach rates | Single SLA — meaningless |
| **All runs (incl. non-detected)** | Non-detection is the ultimate SLA breach | Detected-only — survival bias |

---

## Appendix: Method References

1. **Exact Binomial Test** — `scipy.stats.binomtest`. Clopper & Pearson (1934), *Biometrika*, 26(4).
2. **Wilson Score CI** — Wilson (1927), *JASA*, 22(158). `statsmodels.stats.proportion.proportion_confint(method='wilson')`.
