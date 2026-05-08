# Research Findings: SLA-Aware Statistical Hypothesis Framework

## Overview

This document compiles research findings from multiple academic papers and industry standards on how Service Level Agreements (SLAs) can be statistically tested, validated, and monitored for AI agent certification. These findings inform the design of SLA-aware hypotheses (H-SLA-01 through H-SLA-08) that extend the existing No-SLA framework (H-01 through H-05).

---

## 1. SLA Compliance Testing via One-Sample Tests

### Problem
When an SLA defines a threshold (e.g., "time_to_detect ≤ 300s"), we need to test whether the agent's performance reliably stays below that threshold — not just on average, but with statistical confidence.

### Methods & Papers

#### 1a. One-Sample Wilcoxon Signed-Rank Test
- **Source**: Wilcoxon, F. (1945). "Individual comparisons by ranking methods." *Biometrics Bulletin*, 1(6), 80–83.
- **What it does**: Tests whether the median of a distribution differs from a specified value (the SLA threshold). Non-parametric — no normality assumption.
- **Application**: H₀: median(TTD) ≥ SLA_threshold vs Hₐ: median(TTD) < SLA_threshold. One-sided test.
- **Decision rule**: If p < 0.05, we have evidence that the agent typically performs within SLA.
- **Why this over t-test**: Agent response times are typically right-skewed with heavy tails (LLM retry loops). The Wilcoxon test uses ranks, making it robust to outliers.
- **Python**: `scipy.stats.wilcoxon(data - threshold, alternative='less')`

#### 1b. One-Sample t-Test (Parametric Alternative)
- **Source**: Student (1908). "The probable error of a mean." *Biometrika*.
- **Application**: Only used when Shapiro-Wilk confirms normality. Tests H₀: μ ≥ SLA vs Hₐ: μ < SLA.
- **Python**: `scipy.stats.ttest_1samp(data, SLA_threshold, alternative='less')`

#### 1c. Bootstrap CI vs SLA Threshold
- **Source**: Efron, B. (1979). "Bootstrap methods: Another look at the jackknife." *Annals of Statistics*.
- **Application**: Compute bootstrap BCa CI for IQM. If the **upper bound** of the 95% CI is below the SLA threshold, the agent passes with 95% confidence.
- **Advantage**: Distribution-free, naturally extends H-01 from the No-SLA framework.

### How They Apply to SLA-Aware Certification
- **New Hypothesis H-SLA-01**: "Does the agent's typical performance meet the SLA threshold?"
  - Compute IQM + Bootstrap CI
  - If CI upper bound ≤ SLA: **PASS** (strong evidence of compliance)
  - If CI contains SLA: **CONDITIONAL** (may or may not comply)
  - If CI lower bound > SLA: **FAIL** (even best estimate violates SLA)
  - Supplement with Wilcoxon one-sample test for formal p-value

---

## 2. SLA Breach Rate Estimation

### Problem
SLAs often specify an acceptable breach rate (e.g., "99.9% of detections must complete within 300s" or "≤5% false negative rate"). We need to estimate the true breach rate and test whether it's within acceptable bounds.

### Methods & Papers

#### 2a. Exact Binomial Test
- **Source**: Clopper, C.J. & Pearson, E.S. (1934). "The use of confidence or fiducial limits." *Biometrika*, 26(4), 404–413.
- **What it does**: Given x breaches out of n trials, tests H₀: breach_rate ≥ target vs Hₐ: breach_rate < target.
- **Application**: If SLA says "≤5% breach rate" and we observe 1/30 breaches, is this sufficient evidence?
- **Exact Clopper-Pearson CI**: [0.001, 0.167] — the upper bound 16.7% exceeds 5%, so we cannot certify at n=30.
- **Key insight**: At n=30, proving breach_rate ≤ 5% requires observing 0 breaches. This drives sample size requirements.
- **Python**: `scipy.stats.binomtest(x, n, p=target, alternative='less')`

#### 2b. Wilson Score for Breach Rate
- **Source**: Wilson, E.B. (1927). Already used in H-02.
- **Application**: Wilson CI applied to breach count (complement of success rate). Lower bound of compliance rate = upper bound of breach rate.

#### 2c. Sequential Probability Ratio Test (SPRT)
- **Source**: Wald, A. (1945). "Sequential tests of statistical hypotheses." *Annals of Mathematical Statistics*, 16(2), 117–186.
- **What it does**: Tests SLA compliance sequentially as data arrives, making early accept/reject decisions.
- **Application**: During certification runs, SPRT can flag "this agent will clearly fail/pass SLA" before all 30 runs complete, saving resources.
- **Decision boundaries**: Upper boundary (reject H₀: compliant) and lower boundary (accept H₀: compliant), computed from acceptable error rates α and β.
- **Why relevant**: For expensive fault injection tests, early stopping saves time and compute.

### How They Apply
- **New Hypothesis H-SLA-02**: "Is the SLA breach rate within acceptable limits?"
  - Exact binomial test against SLA breach target
  - Wilson CI on breach rate
  - Report minimum sample size needed to certify at given breach rate

---

## 3. TOST (Two One-Sided Tests) for Equivalence Testing

### Problem
Standard hypothesis tests prove "the agent is different from the SLA threshold." But for certification, we often want to prove the *opposite*: "the agent's performance is *within* acceptable bounds of the SLA." This is **equivalence testing**.

### Methods & Papers

#### 3a. TOST Procedure
- **Source**: Schuirmann, D.J. (1987). "A comparison of the two one-sided tests procedure and the power approach for assessing the equivalence of average bioavailability." *Journal of Pharmacokinetics and Biopharmaceutics*, 15(6), 657–680.
- **Source**: Lakens, D. (2017). "Equivalence Tests: A Practical Primer for t Tests, Correlations, and Meta-Analyses." *Social Psychological and Personality Science*, 8(4), 355–362. https://doi.org/10.1177/1948550617697177
- **What it does**: Runs TWO one-sided tests:
  1. Test 1: H₀: μ ≤ SLA_lower vs Hₐ: μ > SLA_lower
  2. Test 2: H₀: μ ≥ SLA_upper vs Hₐ: μ < SLA_upper
  - If BOTH reject, the metric is within [SLA_lower, SLA_upper] with confidence.
- **Application**: Prove that TTD is within [0, 300s] SLA band. Or prove that detection_rate is within [0.95, 1.00].
- **Key advantage over standard CI**: TOST has formal power analysis — you can compute the sample size needed to prove equivalence at a given effect size.
- **Python**: `statsmodels.stats.weightstats.ttost_ind()` or manual implementation with `ttest_1samp`

#### 3b. Non-Parametric TOST (Rank-Based)
- **Source**: Munk, A. & Czado, C. (1998). "Nonparametric validation of similar distributions." *Journal of the Royal Statistical Society B*.
- **Application**: When data is non-normal, use Wilcoxon-based TOST instead of t-test-based.

### How They Apply
- **New Hypothesis H-SLA-03**: "Is the agent's performance demonstrably within SLA bounds?"
  - TOST procedure with SLA-defined equivalence margins
  - If both one-sided tests reject: **PASS** (proven within bounds)
  - If neither rejects: **FAIL** (outside bounds or insufficient power)
  - Report required sample size for adequate power

---

## 4. Tail Risk Analysis (CVaR / Expected Shortfall)

### Problem
SLA compliance on *average* doesn't prevent catastrophic individual violations. An agent meeting SLA 95% of the time might still have 5% of runs where TTD is 30 minutes (catastrophic). We need to quantify **how bad the worst cases are**.

### Methods & Papers

#### 4a. Conditional Value-at-Risk (CVaR)
- **Source**: Rockafellar, R.T. & Uryasev, S. (2000). "Optimization of conditional value-at-risk." *Journal of Risk*, 2(3), 21–42.
- **Source**: Artzner, P., Delbaen, F., Eber, J.M., & Heath, D. (1999). "Coherent measures of risk." *Mathematical Finance*, 9(3), 203–228.
- **What it does**: CVaR₉₅ = average of the worst 5% of outcomes. More informative than P95 (single point) because it captures the *severity* of tail events.
- **Application**: SLA might say "P95 TTD ≤ 300s." But CVaR₉₅ tells you: "When TTD exceeds P95, the average violation is 450s" — this is critical for incident response planning.
- **Formula**: CVaR_α = E[X | X > VaR_α] where VaR_α is the α-th percentile

#### 4b. Tail Conditional Expectation
- **Source**: Same as CVaR literature.
- **Application**: Expected SLA overshoot: E[X - SLA | X > SLA] — average amount by which violations exceed the threshold.

### How They Apply
- **New Hypothesis H-SLA-04**: "When SLA is breached, how severe are the violations?"
  - CVaR₉₅ for continuous metrics (TTD, TTM)
  - Expected SLA overshoot
  - Maximum observed violation
  - Tail probability estimation

---

## 5. Per-Fault SLA Mapping

### Problem
Different faults may have different SLA requirements. A pod-delete (application fault) might have SLA: TTD ≤ 120s, while disk-fill (resource fault) might have SLA: TTD ≤ 600s. The framework must handle heterogeneous SLAs.

### Methods & Papers

#### 5a. Stratified Testing
- **Source**: Cochran, W.G. (1977). *Sampling Techniques*, 3rd ed. Wiley.
- **Application**: Test each fault type against its own SLA. Aggregate using weighted combination where weights reflect fault severity/frequency.

#### 5b. Multiple Testing Correction with Different Thresholds
- **Source**: Benjamini, Y. & Hochberg, Y. (1995). "Controlling the false discovery rate." *JRSS-B*, 57(1), 289–300.
- **Application**: When testing k fault types each against their own SLA, apply BH-FDR correction to control false discovery rate. This is less conservative than Bonferroni when SLAs differ.

#### 5c. Hierarchical SLA Structure
- **Industry standard**: ITIL v4 (2019). SLAs defined at service level, decomposed into SLOs (Service Level Objectives) per component, and SLIs (Service Level Indicators) as raw measurements.
- **Google SRE Book**: Beyer, B. et al. (2016). *Site Reliability Engineering*. O'Reilly. Chapter 4: Service Level Objectives.
  - SLI → SLO → SLA hierarchy
  - Error budgets: allowed breach count per time window
  - Burn rate alerting

### How They Apply
- **New Hypothesis H-SLA-05**: "Do all fault types individually meet their respective SLAs?"
  - Per-fault SLA compliance test (Wilcoxon/TOST per fault)
  - Worst-fault assessment (min compliance across faults)
  - Error budget tracking (remaining SLA margin per fault)

---

## 6. Survival Analysis for SLA Breach Modeling

### Problem
SLAs often define time-based guarantees (e.g., "99% of faults must be detected within 300s"). Survival analysis models the probability of "surviving" (not breaching SLA) over time.

### Methods & Papers

#### 6a. Kaplan-Meier Estimator
- **Source**: Kaplan, E.L. & Meier, P. (1958). "Nonparametric estimation from incomplete observations." *JASA*, 53(282), 457–481.
- **What it does**: Estimates the survival function S(t) = P(TTD > t). At the SLA threshold, S(SLA_threshold) gives the breach probability.
- **Application**: Plot "what fraction of runs are still undetected at time t?" The curve crossing the SLA threshold shows expected compliance rate.
- **Handles censoring**: Runs where detection never occurred (censored at experiment timeout) are properly modeled.

#### 6b. Log-Rank Test
- **Source**: Mantel, N. (1966). "Evaluation of survival data and two new rank order statistics." *Cancer Chemotherapy Reports*.
- **Application**: Compare survival curves across fault categories. "Does the agent detect application faults faster than network faults?" — same as H-03 but using survival framework, which handles censored (undetected) runs properly.

### How They Apply
- **New Hypothesis H-SLA-06**: "What is the time-dependent SLA compliance probability?"
  - Kaplan-Meier survival curve per fault category
  - S(SLA_threshold) = breach probability at SLA time
  - Log-rank test for cross-category survival comparison
  - Handles right-censored data (faults never detected within timeout)

---

## 7. SLA Drift Detection (Control Charts / CUSUM)

### Problem
An agent might meet SLA initially but degrade over time (model drift, environment changes). We need to detect when performance begins to drift toward SLA violation.

### Methods & Papers

#### 7a. CUSUM (Cumulative Sum Control Chart)
- **Source**: Page, E.S. (1954). "Continuous inspection schemes." *Biometrika*, 41(1-2), 100–115.
- **What it does**: Tracks cumulative deviations from a target value. When the cumulative sum exceeds a threshold, signals a change point.
- **Application**: Track (TTD_i - SLA_target) cumulatively. Rising CUSUM indicates drift toward SLA violation.
- **Formula**: S_t = max(0, S_{t-1} + (x_t - k)) where k = allowable slack, signal when S_t > h

#### 7b. EWMA (Exponentially Weighted Moving Average)
- **Source**: Roberts, S.W. (1959). "Control chart tests based on geometric moving averages." *Technometrics*.
- **Application**: Weighted average giving more weight to recent observations. Detects gradual SLA degradation.

#### 7c. Change Point Detection
- **Source**: Killick, R. & Eckley, I.A. (2014). "changepoint: An R Package for Changepoint Analysis." *Journal of Statistical Software*.
- **Application**: Detect structural breaks in agent performance — e.g., after a model update, did TTD distribution shift?

### How They Apply
- **New Hypothesis H-SLA-07**: "Is the agent's SLA compliance stable over time or drifting?"
  - CUSUM chart on SLA margin (SLA_threshold - observed_value)
  - EWMA smoothing for trend detection
  - Change point detection for structural breaks
  - Alert when trending toward SLA violation before actual breach

---

## 8. Composite SLA Score and Error Budgets

### Problem
Multiple SLA dimensions (TTD, TTM, detection rate, reasoning quality) need to be combined into an overall compliance assessment. How to aggregate?

### Methods & Papers

#### 8a. Error Budget Model
- **Source**: Google SRE Book (2016). Chapter 3: "Embracing Risk."
- **What it does**: Each SLA has a budget of allowable failures. 99.9% uptime = 0.1% error budget = ~43 minutes/month. Track consumption.
- **Application**: Per fault category, compute error budget consumption: (observed_breach_rate / allowed_breach_rate) × 100%.

#### 8b. Weighted Composite Score
- **Source**: Standard multi-criteria decision analysis (MCDA).
- **Application**: Weighted average of per-SLA compliance scores. Weights from fault severity × frequency.

### How They Apply
- **New Hypothesis H-SLA-08**: "Overall SLA compliance composite"
  - Error budget consumption per SLA dimension
  - Weighted composite score
  - Worst-dimension assessment (binding constraint)

---

## Summary: Mapping Old Hypotheses to SLA-Aware Hypotheses

| No-SLA (Current) | SLA-Aware (New) | Change |
|---|---|---|
| H-01: CI Estimation | H-SLA-01: SLA Threshold Test | CI upper bound compared against SLA; Wilcoxon one-sample test added |
| H-02: Success Rate Floor | H-SLA-02: SLA Breach Rate | Exact binomial test against SLA breach target; SPRT for early stopping |
| H-03: Cross-Category (Continuous) | H-SLA-03: TOST Equivalence | TOST proves metric is *within* SLA bounds (not just different from) |
| H-04: Cross-Category (Binary) | H-SLA-05: Per-Fault SLA Compliance | Each fault tested against its own SLA; BH-FDR correction |
| H-05: Variance Stability | H-SLA-07: SLA Drift Detection | CUSUM/EWMA for temporal drift toward SLA violation |
| (new) | H-SLA-04: Tail Risk (CVaR) | Severity of SLA violations when they occur |
| (new) | H-SLA-06: Survival Analysis | Time-to-detection survival curves at SLA threshold |
| (new) | H-SLA-08: Composite SLA Score | Error budget consumption and weighted aggregate |

---

## Key References (Full Bibliography)

1. Wilcoxon, F. (1945). "Individual comparisons by ranking methods." *Biometrics Bulletin*, 1(6), 80–83.
2. Clopper, C.J. & Pearson, E.S. (1934). "The use of confidence or fiducial limits." *Biometrika*, 26(4), 404–413.
3. Wald, A. (1945). "Sequential tests of statistical hypotheses." *Annals of Mathematical Statistics*, 16(2), 117–186.
4. Schuirmann, D.J. (1987). "A comparison of the two one-sided tests procedure." *J. Pharmacokinetics and Biopharmaceutics*, 15(6), 657–680.
5. Lakens, D. (2017). "Equivalence Tests: A Practical Primer." *Social Psychological and Personality Science*, 8(4), 355–362.
6. Rockafellar, R.T. & Uryasev, S. (2000). "Optimization of conditional value-at-risk." *Journal of Risk*, 2(3), 21–42.
7. Artzner, P. et al. (1999). "Coherent measures of risk." *Mathematical Finance*, 9(3), 203–228.
8. Kaplan, E.L. & Meier, P. (1958). "Nonparametric estimation from incomplete observations." *JASA*, 53(282), 457–481.
9. Mantel, N. (1966). "Evaluation of survival data." *Cancer Chemotherapy Reports*, 50(3), 163–170.
10. Page, E.S. (1954). "Continuous inspection schemes." *Biometrika*, 41(1-2), 100–115.
11. Roberts, S.W. (1959). "Control chart tests based on geometric moving averages." *Technometrics*, 1(3), 239–250.
12. Beyer, B. et al. (2016). *Site Reliability Engineering*. O'Reilly Media.
13. Benjamini, Y. & Hochberg, Y. (1995). "Controlling the false discovery rate." *JRSS-B*, 57(1), 289–300.
14. Cochran, W.G. (1977). *Sampling Techniques*, 3rd ed. Wiley.
15. Killick, R. & Eckley, I.A. (2014). "changepoint: An R Package for Changepoint Analysis." *J. Statistical Software*, 58(3).
16. Munk, A. & Czado, C. (1998). "Nonparametric validation of similar distributions." *JRSS-B*.
17. Wilson, E.B. (1927). "Probable inference." *JASA*, 22(158), 209–212. (already in No-SLA framework)
18. Efron, B. (1979). "Bootstrap methods." *Annals of Statistics*. (already in No-SLA framework)
19. Agarwal, R. et al. (2021). "Deep RL at the Edge of the Statistical Precipice." *NeurIPS*. (already in No-SLA framework)
20. Arcuri, A. & Briand, L. (2011). "A practical guide for using statistical tests." *ICSE*. (already in No-SLA framework)

---

*Generated: 2026-04-15 | For AgentCert v2 SLA-Aware Extension*
