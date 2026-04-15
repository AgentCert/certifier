# Statistical Hypothesis Framework

[[_TOC_]]

**Advanced Inference Strategy for AgentCert Certification Pipeline**

`AgentCert v2` | `Single Agent - No SLA - n >= 30` | `alpha = 0.05` | `5 Hypothesis Tests` | `15 Research Papers`

---

## Section A -- What Is a Statistical Hypothesis Framework in AgentCert?

A **statistical hypothesis framework** replaces single-number summaries (means, rates) with formal probabilistic inference. Instead of asking *"what is the mean time_to_detect?"*, we ask *"what is the confidence interval around mean time_to_detect, how consistent is it across fault categories, and is there evidence of drift over time?"*

> **Framework Context:** This evaluates **one agent** without predefined SLA thresholds, using **30+ runs per fault category** to ensure statistical validity. The framework provides confidence intervals, cross-category comparisons, effect sizes, and variance stability checks.

### What This Framework Delivers

| Capability | Description |
|---|---|
| **Uncertainty Quantification** | Wilson CI, Bootstrap CI, and IQM provide confidence intervals -- not just point estimates. |
| **Cross-Category Analysis** | Kruskal-Wallis, Mann-Whitney U, Welch's ANOVA, and Fisher's Exact tests compare fault types. |
| **Effect Size Measurement** | Vargha-Delaney A12 quantifies how large differences actually are -- beyond statistical significance. |
| **Stability Testing** | Levene's test and CV analysis ensure consistent performance across runs. |

---

## Section B -- How to Use This Framework (Step-by-Step)

### Three-Phase Workflow

#### Phase 1: BUILD HYPOTHESIS
- Choose from 5 pre-defined hypotheses (H-01 to H-05)
- Select appropriate statistical method
- Document expected "steady state" behavior

#### Phase 2: SET UP MONITORING
- Deploy Langfuse OTEL trace store
- Configure LiteLLM Proxy for multi-model routing
- Set up MongoDB for 30+ runs per fault type
- Enable bucketing & extraction pipeline

#### Phase 3: CONDUCT EXPERIMENT & GENERATE CERTIFICATION
- Run agent 30+ times per fault category (application, network, resource)
- Inject faults using Litmus Chaos
- Collect traces via MCP protocol
- Apply statistical tests (Wilson CI, Bootstrap, Kruskal-Wallis, etc.)
- Generate comprehensive certification report (scorecard, findings, assessments, recommendations)

> **References:** Wilson (1927) \| Efron (1979) \| Kruskal & Wallis (1952) \| Vargha & Delaney (2000) \| Basiri et al. (2016) \| [Aktas et al. (2025)](https://arxiv.org/pdf/2506.14281) \| [LitmusChaos](https://litmuschaos.io/) \| [Chaos Mesh](https://chaos-mesh.org/)

---

### Detailed Pipeline Workflow

End-to-end technical flow from fault injection to certificate generation:

:::mermaid
flowchart TD
    subgraph LOOP["LOOP: 30+ ITERATIONS"]
        A["1 FAULT INJECTION<br/>Define fault combination<br/>Compute experiment_id = SHA-256"]
        B["2 AGENT EXECUTION run #1...#30+<br/>Agent runs against fault set<br/>Generates run_id UUID"]
        C["3 FAULT BUCKETING<br/>Trace decomposed into FaultBucket objects<br/>Tagged with experiment_id + run_id"]
        D["4 METRICS EXTRACTION<br/>Extract quantitative + qualitative metrics<br/>Store with full provenance"]
        A --> B --> C --> D
        D -->|"Repeat until 30+ runs"| A
    end
    LOOP --> E["5 AGGREGATION<br/>Combines all 30+ runs<br/>Numeric aggregates, derived rates, LLM Council"]
    E --> F["6 STATISTICAL TESTING<br/>H-01 to H-05 per experiment<br/>CI estimation, cross-category, variance stability"]
    F --> G["7 CERTIFICATION REPORT<br/>Scorecard, Key Findings, Performance Tables<br/>Charts, LLM Assessments, Recommendations"]
:::

**Certification Report Sections:**

| Section | Description |
|---------|-------------|
| Scorecard | 7-dimension radar (detection speed, mitigation speed, reasoning, etc.) |
| Key Findings | Severity-tagged insights (concern / good / note) |
| Performance Tables | Speed metrics (TTD, TTM), rates (detection, mitigation) |
| Charts | Time series, grouped bars, distribution plots |
| LLM Assessments | Reasoning quality, RAI compliance, security (with confidence ratings) |
| Fault Analysis | Per-category deep dives with narratives |
| Recommendations | Prioritized improvement actions with rationale |

Output: `certification_report.json` (validated against Pydantic schema)

> **Key Principle:** Statistical hypothesis tests (H-01--H-05) run **per experiment**, never across experiments. Results feed into the aggregation layer which generates the comprehensive certification report with multi-dimensional assessment.

---

## Section C -- Statistical Hypothesis Framework for Agent Certification

### C.1 Understanding the Problem: Testing AI Agents Under Chaos

AgentCert evaluates how well AI agents handle real Kubernetes production failures by injecting controlled faults and measuring the agent's ability to detect, diagnose, and remediate them. We test across **three fault categories** that mirror real-world outages:

**Application Faults**
- **container-kill** -- Pod crashes, container restarts, CrashLoopBackOff
- **pod-delete** -- Pod termination, replica set reconciliation

*Tests: Restart detection, service restoration*

**Network Faults**
- **pod-network-loss** -- Packet drop, network partitions
- **pod-network-corruption** -- Packet corruption, data integrity failures
- **pod-network-rate-limit** -- Bandwidth throttling, latency spikes
- **pod-dns-error** -- DNS resolution failures, service discovery issues

*Tests: Connectivity diagnosis, routing problem detection*

**Resource Faults**
- **disk-fill** -- Disk exhaustion, I/O pressure, storage saturation
- **pod-cpu-hog** -- CPU saturation, throttling, performance degradation
- **pod-memory-hog** -- Memory pressure, OOMKill risk
- **pod-autoscaler** -- HPA/VPA misconfiguration, scaling failures

*Tests: Resource constraint identification, remediation*

> **Real-World Production Scenarios**
> - **Application:** Pods OOMKilled, app crashes, deployment rollout failures, pod evictions
> - **Network:** Microservices unable to reach databases/APIs, DNS outages, intermittent packet loss, bandwidth saturation
> - **Resource:** Nodes running out of ephemeral storage, CPU throttling, memory pressure, autoscaler deadlocks

We run the agent **~30 times per fault** across **10 fault types** (2 application x 4 network x 4 resource) and aggregate results at **three levels**:

- **Per-Fault Level:** 30 runs of pod-delete -> mean, median, std dev, P95
- **Fault-Category Level:** All application faults (container-kill + pod-delete) -> weighted avg, worst-case status
- **Overall Agent Level:** All categories (app + network + resource) -> certification scorecard

---

### C.2 The Five Hypotheses: What We Test and Why

At each level, we test **five statistical hypotheses** to ensure the agent is reliable, consistent, and safe:

#### H-01: Confidence Intervals for Continuous Metrics

**Question:** What's the agent's *true typical* performance with precision bounds?

**Metrics:** time_to_detect, time_to_mitigate, reasoning_score, hallucination_score

**Workflow:**
1. Collect n=30-40 runs per fault category
2. Compute: **IQM** (25% trimmed mean), Median, P95, Mean (reference)
3. Bootstrap CI: Resample 10k times from your 30-40 runs -> distribution of IQMs -> 95% CI

**Why:** Single point estimate from lucky/unlucky runs misleads. IQM trims outliers (top/bottom 25%) yet retains efficiency (uses 50% of data). CI reveals precision. Bootstrap doesn't assume normality.

**Benefit:** IQM is **robust to LLM retry loops and transient failures** (Agarwal NeurIPS 2021). Median + P95 provide tail behavior. Mean shown for reference.

> **Output (Statistical):**
> ```
> time_to_detect: IQM=245s [210-280s BCa CI], median=242s, P95=452s, mean=293s
> ```
> **Narrative:** "The agent demonstrates **reliable fault detection** with typical response time of 4.1 minutes (IQM). The narrow confidence interval (210-280s) indicates consistent performance. 95% of detections complete within 7.5 minutes, meeting production SLA. **Note: Raw mean (293s) inflated by 3 outlier runs (800s+) from LLM retry loops -- IQM provides more accurate typical-case estimate.**"

---

#### H-02: Success Rate Estimation with Safety Floor

**Question:** What % of faults does the agent successfully detect and mitigate?

**Derived Metrics:** fault_detection_success_rate, fault_mitigation_success_rate, false_negative_rate, false_positive_rate, rai_compliance_rate, security_compliance_rate

**Workflow:**
1. Count successes out of n=30-40 runs
2. Calculate rate: successes / total
3. Wilson score interval -> 95% CI
4. Report **lower bound** as safety floor

**Why:** 24/30 = 80% nominally, but true rate could be 62%-91%. Lower bound is conservative safety guarantee.

**Benefit:** Certification based on worst-case estimate. Wilson handles small n and extreme proportions (0%, 100%).

> **Output (Statistical):**
> ```
> fault_detection_success_rate: 24/30 = 80.0% [62.1%-91.4% Wilson CI] | Certified Floor: 62%
> ```
> **Narrative:** "Under adversarial testing conditions, the agent successfully detected 80% of injected faults (24 out of 30 test runs). **Conservative certification guarantees a minimum 62% detection rate** with 95% confidence. This performance exceeds industry baseline (50%) and qualifies for production deployment. False negative rate of 20% (6 missed faults) is within acceptable tolerance for non-critical infrastructure monitoring."

---

#### H-03: Cross-Category Performance Comparison

**Question:** Does the agent handle all fault categories equally, or struggle with specific types?

**Compare:** Application vs. Network vs. Resource -- time_to_detect, time_to_mitigate, reasoning_score

**Workflow:**
1. **Kruskal-Wallis (omnibus):** "Any difference?" -> one p-value (controls Type I error)
2. **IF p<0.05:** Mann-Whitney U pairwise -> "Which pairs differ?" (App-Net, App-Res, Net-Res)
3. **For each pair:** Vargha-Delaney A12 -> "How big?" (0.5=same, 0.71+=large gap)

**Why:** Overall average hides category-specific weaknesses. Resource faults 3x slower = deployment risk.

**Benefit:** Identifies hidden gaps. A12 separates statistical significance from practical importance.

> **Output (Statistical):**
> ```
> Kruskal-Wallis H=18.4, p=0.0001 | Resource vs App: Mann-Whitney U p=0.007, A12=0.92 (large)
> ```
> **Narrative:** "Significant performance disparity detected across fault categories (p<0.001). **Resource exhaustion faults (CPU, memory, disk) take 3x longer to detect** than application faults (median 612s vs 198s). The effect size (A12=0.92) indicates Resource faults are slower in 92% of head-to-head comparisons -- a **critical operational gap**. Recommend dedicated resource monitoring alerts to compensate for agent latency in this category."

---

#### H-04: Cross-Category Success Rate Uniformity

**Question:** Are success rates uniform across categories, or does the agent fail certain fault types?

**Compare:** fault_detection_success_rate, fault_mitigation_success_rate, rai_compliance_rate across App/Net/Res

**Workflow:**
1. Build 2x3 contingency table: success/fail x app/network/resource
2. Fisher's Exact Test -> p-value (exact, no approximation)
3. If p<0.05: identify weakest category

**Why:** 90% app + 50% network + 70% resource = 70% overall. Average masks 50% network failure rate.

**Benefit:** Reveals category-specific failure modes. Critical for deployment planning (avoid network faults!).

> **Output (Statistical):**
> ```
> App: 27/30 (90%) | Network: 15/30 (50%) | Resource: 21/30 (70%) | Fisher's Exact p=0.003
> ```
> **Narrative:** "**Network fault mitigation shows unacceptable failure rate** (50% success vs. 90% for application faults). Statistical analysis confirms this disparity is not due to random chance (Fisher's p=0.003). Root cause: DNS errors and packet loss conditions trigger LLM hallucination -- agent incorrectly diagnoses network faults as application-layer issues. **Deployment recommendation: Bypass agent for network-category faults; use rule-based detectors instead.** Application and resource categories meet certification threshold."

---

#### H-05: Consistency & Predictability (Variance Stability)

**Question:** Is the agent's performance *consistent* or wildly unpredictable?

**Metrics:** Std Dev (sigma) and Coefficient of Variation (CV = sigma/mu) for time_to_detect, time_to_mitigate, reasoning_score

**Workflow:**
1. Compute sigma and CV per category
2. Levene's test: "Does variance differ across categories?" -> p-value
3. CV interpretation: <0.15 stable \| 0.15-0.30 moderate \| >0.30 unstable (red flag)

**Why:** Agent detecting disk-fill in 10s, then 500s, then 30s = **unreliable** (even if mean=180s looks OK).

**Benefit:** Identifies erratic categories. High variance = certification concern. Users need predictable performance.

> **Output (Statistical):**
> ```
> Network: sigma=404s, CV=1.40 | Resource: sigma=106s, CV=0.38 | App: sigma=52s, CV=0.14 | Levene's p=0.02
> ```
> **Narrative:** "**Network fault detection exhibits unacceptable variance** (CV=1.40, classified as unstable). Detection times range unpredictably from 45 seconds to 18 minutes for identical fault types. This erratic behavior (confirmed by Levene's test, p=0.02) poses **operational risk -- users cannot rely on consistent SLA performance**. Application faults show stable, predictable behavior (CV=0.14). Resource faults demonstrate moderate consistency (CV=0.38). **Certification withheld for network category pending model retraining with augmented DNS/packet-loss examples.**"

---

### C.3 Statistical Foundation

#### Hypothesis Framework: Questions, Formal Tests & Certification Use

| Test | Question | H0 (Null Hypothesis) | Ha (Alternative) | Key Output & Use |
|------|----------|----------------------|-------------------|------------------|
| **H-01** | How fast/good on average? | Population mean (mu) is unknown. *Estimate mu using X-bar +/- CI* | **No formal Ha** -- This is **estimation**. *Goal: Quantify uncertainty. Narrow CI = precise* | **Output:** IQM +/- CI, Median, P95. **Use:** Performance guarantee *(IQM = 25% trimmed mean, robust to outliers)* |
| **H-02** | What % success rate? | Population rate (p) is unknown. *Estimate p using p-hat +/- Wilson CI* | **No formal Ha** -- This is **estimation**. *Goal: Certify at worst-case lower bound (62%)* | **Output:** Rate with lower bound. **Use:** Safety floor |
| **H-03** | Any category slower/worse? | **H0:** Performance identically distributed across categories. *Median_App = Median_Net = Median_Res* | **Ha:** At least one category differs significantly. *If p<0.05 -> pairwise Mann-Whitney U* | **Output:** p-value + A12 effect size. **Use:** Hidden weaknesses |
| **H-04** | Any category low success? | **H0:** Success rates uniform across categories. *p_App = p_Net = p_Res* | **Ha:** Success rates differ significantly. *If p<0.05 -> flag weakest category* | **Output:** Per-category rates + p-value. **Use:** Failure mode ID |
| **H-05** | Is performance consistent? | **H0:** Variance equal across categories. *Var_App = Var_Net = Var_Res* | **Ha:** At least one category has different variance. *If Levene p<0.05 OR CV>0.30 -> flag unstable* | **Output:** CV per category (stability). **Use:** Reliability flag |

> **Key Distinction:** **H-01 and H-02** are **estimation problems** (no H0/Ha framework -- we simply quantify uncertainty). **H-03, H-04, H-05** are **hypothesis tests** (we start with H0 "no difference" and reject it if p < alpha). Both serve certification: estimation sets performance baselines, testing identifies hidden weaknesses.

---

### C.4 Method Justification: Why These Tests?

#### Critical Analysis of Simple vs. Robust Methods

##### H-01 -- Confidence Intervals

**Why NOT Basic Tests:**
- **z-test CI:** Invalid -- requires known population sigma (we don't have it).
- **t-test CI:** Agent data has **long-tail outliers** (10x slow runs from LLM retries). Shapiro-Wilk test would likely reject normality. At n=30-40, CLT is **marginal for skewed distributions**.

**Why ONLY This Method Works:**
- **Bootstrap CI (BCa) + IQM:** No assumptions -- works with ANY data shape.
- **IQM (25% trimmed mean)** as statistic -- robust to LLM outliers.
- BCa method corrects for bias & skewness.
- Only loses 5% efficiency vs t-test IF data is perfectly normal (unlikely).

**Decision Workflow:** Recommended: Use Bootstrap + IQM (distribution-free). Alternative: 1. Run Shapiro-Wilk -> 2. IF p>0.05: t-test CI (mean) -> 3. ELSE: Bootstrap (IQM)

```python
from scipy.stats import bootstrap, trim_mean

def iqm(x):
    trim_mean(x, 0.25)

bootstrap((data,), iqm, n_resamples=10000, method='BCa')
```

##### H-02 -- Success Rates

**Why NOT Basic Tests:**
- **Normal Approximation:** Mathematically invalid. 29/30 success (96.7%) -> CI: [88.3%, **105.1%**] (exceeds 100%!). 0/30 success -> CI: [-0%, 0%] (useless). Poor coverage: actual 95% CI behaves like 85% CI at n=30-40.

**Why ONLY This Method Works:**
- **Wilson Score Interval:** Always stays [0-100%] -- mathematically guaranteed.
- Industry standard for n<100 (medical trials, A/B testing).
- Handles 0%, 100%, and all boundary cases correctly.
- **True 95% coverage** at n=30-40.

**Decision Workflow:** MANDATORY: 1. Count successes -> 2. Wilson CI -> 3. Report lower bound as safety floor. *No alternatives valid at n=30-40.*

```python
from statsmodels.stats.proportion import proportion_confint

proportion_confint(count, nobs, method='wilson')
```

##### H-03 -- Cross-Category Comparison (Continuous)

**Why NOT Basic Tests:**
- **One-Way ANOVA:** Assumes normality + equal variance. Network faults: **bimodal** (fast OR timeout). Resource faults: **right-skewed** (5s vs 500s). Equal variance unlikely (Levene's test would fail).
- **Welch's ANOVA:** Still assumes normality (violated).

**Why ONLY This Method Works:**
- **Kruskal-Wallis + Mann-Whitney U:** **Distribution-free** -- rank-based, not mean-based.
- More statistical power with skewed data.
- Outlier-resistant (uses median comparison).
- **A12 effect size:** "Resource slower 92% of time" -> actionable insight, not just p<0.05.

**Decision Workflow:** 3-Step: 1. Kruskal-Wallis (omnibus) -> 2. IF p<0.05: Run pairwise Mann-Whitney -> 3. Always calc A12 effect size. *Recommended: Always KW (safe).*

```python
from scipy.stats import kruskal, mannwhitneyu

# Step 1
kruskal(*groups)

# Step 2
mannwhitneyu(a, b)

# Step 3
vargha_delaney()
```

##### H-04 -- Cross-Category Comparison (Binary Rates)

**Why NOT Basic Tests:**
- **Chi-Square Test:** Fails at low cell counts. Requires all expected cells >=5. App: 29/30 success -> 1 failure (expected cell = **1**). **With 95%+ success rates, chi-square breaks.**

**Why ONLY This Method Works:**
- **Fisher's Exact Test:** **No assumptions** -- exact p-value (not approximation).
- Gold standard for 2xk contingency tables.
- Handles ANY cell counts (including 0).
- Instant computation for 2x3 tables (scipy optimized).
- **Mathematically always correct.**

**Decision Workflow:** MANDATORY: 1. Build 2x3 contingency -> 2. Fisher's test -> 3. IF p<0.05: Flag weakest category. *Chi-square invalid -- don't use.*

```python
from scipy.stats import fisher_exact

table = [
    [s_app, f_app],
    [s_net, f_net],
    [s_res, f_res]
]

fisher_exact(table)
```

##### H-05 -- Variance Stability

**Why NOT Basic Tests:**
- **Bartlett's Test:** Extremely sensitive to normality. Rejects H0 due to skewness (not true variance difference). False positive rate high with real data.
- **F-test:** Only 2 groups (we have 3), assumes normality.

**Why ONLY This Method Works:**
- **Coefficient of Variation (CV = sigma/mu):** **Self-explanatory:** "Network has 140% variability" = unstable.
- Clear thresholds: <0.15 stable, 0.15-0.30 moderate, >0.30 unreliable.
- **Levene's test optional** -- CV alone is actionable.

**Decision Workflow:** SIMPLE: 1. Compute CV per category -> 2. IF CV>0.30: Flag unstable -> 3. (Optional) Levene's test for p-value. *CV sufficient for cert.*

```python
import numpy as np

cv = np.std(data) / np.mean(data)

if cv > 0.30:
    flag_unstable

# Optional
levene(a, b, c)
```

---

### C.5 Implementation Details & Reference

#### LLM Council for Textual Metrics

> Narrative fields (agent_summary, rai_check_summary, security_compliance_summary, known_limitations, recommendations) cannot be averaged numerically. Instead, we use an **LLM Council** -- a panel of k=3-5 independent LLM judges that read all 30 per-run narratives and produce a consensus summary, severity label, and confidence score (High/Medium/Low). This reduces single-model bias and provides calibrated confidence via inter-judge agreement.

#### Key Concepts Glossary

| Concept | One-Sentence Explanation |
|---|---|
| **Confidence Interval (CI)** | A range of values where we're 95% sure the true answer lies -- like saying "the temperature is between 68F and 72F" instead of just "it's 70F." |
| **p-value** | The probability that the difference we observed is just due to random luck -- if it's less than 5% (p < 0.05), we consider the difference real. |
| **Effect Size (A12)** | How big a difference actually is -- even if a difference is "real" (significant), it might be tiny and not worth worrying about. |
| **IQM (Interquartile Mean)** | 25% trimmed mean -- the average of the middle 50% of values after dropping the top and bottom quarters, robust to outliers from LLM retry loops. |
| **Bootstrap (BCa Method)** | A technique to measure uncertainty by repeatedly re-shuffling your data and seeing how much the answer changes -- like shuffling a deck of cards 50,000 times. BCa corrects for bias and skewness. |
| **Wilson CI** | Confidence interval for success rates (proportions) that works correctly even with small samples or extreme values (0% or 100%) -- unlike the normal approximation which can give impossible results like 105%. |
| **Kruskal-Wallis / Mann-Whitney** | Tests that compare groups by ranking all values -- doesn't assume data follows a bell curve, which is critical because agent behavior is often unpredictable with long-tail outliers. |
| **Fisher's Exact Test** | Comparison test for success rates across fault categories that works even when sample sizes are small (n=30-40) -- computes exact probabilities instead of relying on approximations. |
| **Levene's Test + CV** | Tests for variance stability across categories. CV (Coefficient of Variation) = std/mean, showing if variability is proportional to magnitude -- critical for detecting reliability issues. |
| **Shapiro-Wilk** | A check to see if data follows a bell curve (normal distribution) -- determines which comparison tests we're allowed to use. |
| **Holm-Bonferroni** | A correction for running multiple tests at once -- without it, the more tests you run, the more likely you are to find a "significant" result by pure chance. |
| **Worst-Category Assessment** | The weakest link determines the chain's strength -- we judge the agent by its worst fault category, not its best. A decision rule, not a statistical test. |

---

## Section D -- Research Grounding -- Peer-Reviewed Methodology

This framework is built on **15 peer-reviewed papers** from top-tier venues in AI evaluation, software testing, causal inference, and statistical methodology. Each hypothesis and method is grounded in established research.

### Statistical Methods (Core Papers)

| Paper | Key Contribution | Application in Framework |
|---|---|---|
| [Agarwal et al. (NeurIPS 2021)](https://arxiv.org/abs/2108.13264) | IQM, Bootstrap B=50k, Performance Profiles | **H-01:** IQM as primary metric for continuous aggregates. 25% trimmed mean robust to LLM outliers. Bootstrap B=10,000 for n=30-40. |
| [Arcuri & Briand (ICSE 2011)](https://doi.org/10.1145/1985793.1985795) | A12 Effect Size, Mann-Whitney U, Holm Correction | **H-03:** Vargha-Delaney A12 for pairwise effect size. Holm-Bonferroni multi-comparison correction for Tier 1 metrics. |
| [Henderson et al. (AAAI 2018)](https://arxiv.org/abs/1709.06560) | CI-First Reporting, Normality Checking | **H-01/H-02:** Confidence intervals reported before p-values. Shapiro-Wilk gating for parametric vs non-parametric tests. |
| [Klees et al. (CCS 2018)](https://doi.org/10.1145/3243734.3243804) | Minimum 30 Trials, Mann-Whitney U Default | **Sample size:** n>=30 per fault category operational standard. Mann-Whitney U for pairwise post-hoc (H-03). |

### Robustness & Certification (Core Papers)

| Paper | Key Contribution | Application in Framework |
|---|---|---|
| [Sagawa et al. (ICLR 2020)](https://arxiv.org/abs/1911.08731) | Worst-Group Accuracy, Group DRO | **Conceptual inspiration:** Worst-group evaluation principle -- judge systems by their weakest subgroup. Our framework adopts this as a simple min() decision rule across fault categories, not the Group DRO optimization algorithm. |
| [Ribeiro et al. (ACL 2020)](https://arxiv.org/abs/2005.04118) | CheckList, Capability-Based Testing | **Conceptual inspiration:** Structured capability-based test design -- testing distinct capabilities independently rather than aggregate accuracy. Motivates our per-fault-category evaluation, though CheckList is NLP-specific and does not define tiered metrics. |
| Basiri et al. (ICSE 2019) | Chaos Engineering, Steady-State Hypothesis | **Baseline:** Fault injection testing design -- defining steady-state hypotheses and injecting failures to measure resilience. Directly relevant to our fault injection methodology. |
| [Peters et al. (JRSS-B 2016)](https://doi.org/10.1111/rssb.12167) | Causal Invariance Across Environments | **Conceptual inspiration for H-05:** The principle that invariant relationships across environments indicate robustness. Motivates cross-category variance checking. Note: Levene's test and CV thresholds are standard statistical methods, not from this paper. |

### Supporting Methods

| Paper | Key Contribution | Application in Framework |
|---|---|---|
| [Scholkopf et al. (Proc. IEEE 2021)](https://doi.org/10.1109/JPROC.2021.3058954) | Causal Representation Learning, Invariance | **Conceptual inspiration:** Invariance principles from causal inference -- stable relationships should hold across environments. Motivates cross-category consistency checks. Transfer risk formula is our own design, not from this paper. |
| [Chen et al. (ACM CSUR 2018)](https://doi.org/10.1145/3143561) | Metamorphic Testing | Cross-category comparison logic: metamorphic relations expect consistent behavior across input transformations. Analogous to our expectation that agent performance remains stable across fault types. |
| [Ma et al. (2024)](https://arxiv.org/abs/2401.13178) | Progress Rate, Multi-Dimensional Analysis | Multi-metric dashboard: tracking multiple evaluation dimensions jointly rather than reducing to a single score. Supports our multi-metric approach (time_to_detect, hallucination_score, reasoning_score). |
| [Lightman et al. (2023)](https://arxiv.org/abs/2305.20050) | Process Supervision for LLMs | **Conceptual inspiration:** Process-level evaluation (step correctness) complements outcome-level evaluation. Motivates reasoning_score as a process metric alongside outcome metrics. |
| [Hendrycks & Dietterich (ICLR 2019)](https://arxiv.org/abs/1903.12261) | Corruption Robustness Benchmarks | **Conceptual inspiration:** The principle of testing across diverse corruption types to measure robustness. Originally for image classifiers; our framework applies this idea to K8s fault categories (application, network, resource). |
| [Sinha et al. (ICLR 2018)](https://arxiv.org/abs/1710.10571) | Wasserstein DRO for Adversarial Robustness | **Loose analogy:** DRO provides robustness guarantees under distributional uncertainty. Our worst-category assessment is a simpler discrete version of this principle -- evaluating the worst-performing group -- not the Wasserstein optimization itself. |
| [McKay et al. (Technometrics 1979)](https://doi.org/10.2307/1268522) | Latin Hypercube Sampling | **Conceptual inspiration:** Efficient coverage of parameter space with limited samples. Motivates diverse fault scenario selection to maximize coverage with n=30 runs per category. |

> **Research Validation:** All 15 papers are from top-tier venues (NeurIPS, ICLR, AAAI, ACL, ICSE, CCS) or established journals (JRSS-B, Proc. IEEE, Technometrics). Core statistical methods (IQM, Bootstrap, A12, Mann-Whitney, Holm correction, n>=30) are directly grounded in Agarwal et al., Arcuri & Briand, Henderson et al., and Klees et al. Robustness and supporting papers provide conceptual inspiration for framework design principles (worst-group evaluation, fault diversity, process metrics) rather than direct algorithmic adoption.
>
> **Full bibliography:** [RESEARCH_FINDINGS_HYPOTHESIS_FRAMEWORK.md](RESEARCH_FINDINGS_HYPOTHESIS_FRAMEWORK.md)

---

## Section E -- Statistical Evidence JSON Schema

The advanced strategy extends each `fault_category_scorecards[]` entry with a new `statistical_evidence` block. Since no SLA exists, the schema focuses on confidence intervals, cross-category comparison results, and stability flags:

```json
{
  "fault_category": "application_fault",
  "total_runs": 30,
  "numeric_metrics": {},
  "derived_metrics": {},
  "boolean_status_metrics": {},
  "textual_metrics": {},

  "statistical_evidence": {
    "mode": "no_sla",
    "assessment": "FLAGGED",
    "consistency_flag": "variable",
    "sample_adequacy": "sufficient",

    "confidence_intervals": {
      "time_to_detect_iqm": {
        "ci_95": [220.5, 480.1],
        "method": "bootstrap_bca",
        "B": 10000,
        "ci_width": 259.6
      },
      "time_to_detect_median": {
        "ci_95": [205.0, 470.0],
        "method": "bootstrap_bca",
        "B": 10000,
        "ci_width": 265.0
      },
      "time_to_detect_p95": {
        "ci_95": [520.0, 584.6],
        "method": "bootstrap_bca",
        "B": 10000,
        "ci_width": 64.6
      },
      "time_to_detect_mean": {
        "ci_95": [180.2, 552.4],
        "method": "bootstrap_bca",
        "B": 10000,
        "ci_width": 372.2,
        "note": "reference_only"
      },
      "time_to_mitigate_iqm": {
        "ci_95": [280.1, 620.5],
        "method": "bootstrap_bca",
        "B": 10000,
        "ci_width": 340.4
      },
      "fault_detection_success_rate": {
        "ci_95": [0.04, 0.62],
        "method": "wilson",
        "ci_width": 0.58
      },
      "reasoning_score_iqm": {
        "ci_95": [8.0, 8.8],
        "method": "bootstrap_bca",
        "B": 10000,
        "ci_width": 0.8
      },
      "hallucination_score_iqm": {
        "ci_95": [0.0, 0.0],
        "method": "bootstrap_bca",
        "B": 10000,
        "ci_width": 0.0
      }
    },

    "cross_category_tests": {
      "shapiro_wilk": {
        "test": "shapiro_wilk_per_group",
        "app_pval": 0.32,
        "net_pval": 0.08,
        "res_pval": 0.41,
        "result": "normality_not_confirmed_use_nonparametric"
      },
      "kruskal_wallis_ttd": {
        "test": "kruskal_wallis",
        "H_stat": 9.8,
        "p_value": 0.007,
        "epsilon_sq": 0.28,
        "result": "significant_disparity"
      },
      "mann_whitney_posthoc": [
        {
          "pair": "app_vs_net",
          "U": 8,
          "p_value": 0.31,
          "a12": 0.64,
          "effect": "medium"
        },
        {
          "pair": "app_vs_res",
          "U": 2,
          "p_value": 0.008,
          "a12": 0.92,
          "effect": "large"
        },
        {
          "pair": "net_vs_res",
          "U": 3,
          "p_value": 0.016,
          "a12": 0.88,
          "effect": "large"
        }
      ],
      "fisher_exact_detection": {
        "test": "fisher_freeman_halton",
        "p_value": 0.014,
        "cramers_v": 0.53,
        "result": "non_uniform"
      },
      "levene_ttd": {
        "test": "levene",
        "f_stat": 5.8,
        "p_value": 0.018,
        "result": "unequal_variance"
      },
      "worst_category": {
        "metric": "fault_detection_success_rate",
        "worst_cat": "network_fault",
        "value": 0.0,
        "ci_lower": 0.0,
        "transfer_risk": 0.20
      },
      "correction_method": "holm_bonferroni"
    },

    "hypothesis_results": [
      {
        "hypothesis_id": "H-01",
        "scenario": "Continuous Metric CI Estimation",
        "test_name": "Bootstrap CI (BCa, B=10000)",
        "metrics": [
          "time_to_detect",
          "time_to_mitigate",
          "reasoning_score",
          "hallucination_score"
        ],
        "iqm": 350.2,
        "median": 342.0,
        "p95": 552.3,
        "mean": 366.3,
        "ci_95": [280.5, 420.1],
        "ci_width": 139.6,
        "assessment": "precise_at_n30"
      },
      {
        "hypothesis_id": "H-02",
        "scenario": "Binary Rate CI Estimation",
        "test_name": "Wilson CI",
        "metrics": [
          "fault_detection_success_rate",
          "fault_mitigation_success_rate"
        ],
        "rate": 0.80,
        "ci_95": [0.62, 0.91],
        "ci_width": 0.29,
        "assessment": "actionable"
      },
      {
        "hypothesis_id": "H-03",
        "scenario": "Cross-Category Comparison (Continuous)",
        "test_name": "Kruskal-Wallis H",
        "normality_check": "shapiro_wilk_failed",
        "p_value": 0.007,
        "p_value_adjusted": 0.021,
        "epsilon_squared": 0.28,
        "posthoc": "mann_whitney_u_holm",
        "effect_sizes": {
          "app_vs_res": 0.92,
          "net_vs_res": 0.88
        },
        "assessment": "significant_category_disparity"
      },
      {
        "hypothesis_id": "H-04",
        "scenario": "Cross-Category Comparison (Binary Rates)",
        "test_name": "Fisher's Exact Test",
        "p_value": 0.014,
        "cramers_v": 0.53,
        "assessment": "non_uniform_rates"
      },
      {
        "hypothesis_id": "H-05",
        "scenario": "Variance Stability",
        "test_name": "Levene's Test",
        "p_value": 0.018,
        "f_stat": 5.8,
        "cv_values": {
          "app": 0.22,
          "net": 0.35,
          "res": 0.19
        },
        "variance_ratio": 3.4,
        "assessment": "variance_instability_detected"
      }
    ],

    "worst_category": {
      "binding_metric": "fault_detection_success_rate",
      "worst_cat": "network_fault",
      "worst_value": 0.0,
      "worst_ci_lower": 0.0,
      "transfer_risk": 0.20
    },
    "tiered_assessment": {
      "tier1_metrics": [
        "fault_detection_success_rate",
        "fault_mitigation_success_rate",
        "time_to_detect",
        "hallucination_score"
      ],
      "tier2_metrics": [
        "false_positive_rate",
        "root_cause_correctness",
        "action_correctness_score",
        "reasoning_score"
      ],
      "tier1_status": "FLAGGED",
      "tier2_status": "CONSISTENT",
      "tier1_correction": "holm_bonferroni",
      "tier2_correction": "bh_fdr"
    }
  }
}
```

---

*AgentCert -- Statistical Hypothesis Framework (No-SLA Mode) \| Generated 2026-04-10 \| Methods: Wilson CI, Bootstrap CI, IQM, Shapiro-Wilk, Kruskal-Wallis, Mann-Whitney U, A12, Welch's ANOVA, Fisher's Exact, Levene's \| Corrections: Holm-Bonferroni, BH-FDR \| Grounded in 15 research papers*
