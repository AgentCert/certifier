# Statistical Methods Reference

[[_TOC_]]

> Plain-English guide to every statistical method used in the AgentCert Hypothesis Framework. Each method includes what it does, how it works, what the output looks like, and where to learn more.

**10 Methods - 5 Hypothesis Tests (H-01 -- H-05)**

---

## Methods at a Glance

| # | Method |
|---|--------|
| Ex | Running Example: SRE-Agent v2.1 Certification Dataset |
| 1 | Wilson Confidence Interval |
| 2 | Bootstrap BCa Confidence Interval |
| 3 | Interquartile Mean (IQM) |
| 4 | Shapiro-Wilk Normality Test |
| 5 | Kruskal-Wallis H Test |
| 6 | Mann-Whitney U Test |
| 7 | Vargha-Delaney A12 Effect Size |
| 8 | Welch's ANOVA |
| 9 | Chi-Square / Fisher's Exact Test |
| 10 | Levene's Test |

---

## Running Example: SRE-Agent v2.1 Certification

> *Fabricated illustration dataset - Used throughout all methods below - All numbers are internally consistent*

### 1 -- Scenario

**SRE-Agent v2.1** (powered by GPT-4o) is submitted for AgentCert certification on a production-grade Kubernetes cluster. The evaluation consists of **90 total fault injection runs** -- 30 per fault category (application crash, network partition, resource exhaustion) -- measuring detection rate, time-to-detect (TTD), mitigation success, and reasoning quality. The agent must demonstrate reliable, consistent, and timely fault handling across all categories to achieve full certification.

*All data below is fabricated but internally consistent, designed to illustrate how each statistical method applies to a realistic certification scenario.*

### 2 -- Key Results

| Category | Runs | Detection Rate | Avg TTD | Median TTD | IQM TTD | P95 TTD | Mitigation Rate | Reasoning |
|----------|------|---------------|---------|------------|---------|---------|-----------------|-----------|
| **Application** | 30 | **90% (27/30)** | 152s | 141s | 143s | 278s | 87% (26/30) | 8.4 / 10 |
| **Network** | 30 | **50% (15/30)** | 289s | 94s | 266s | 845s | 40% (12/30) | 5.2 / 10 |
| **Resource** | 30 | **70% (21/30)** | 280s | 252s | 258s | 512s | 60% (18/30) | 7.1 / 10 |
| **Overall** | 90 | 70% (63/90) | 228s | 175s | 214s | 635s | 62% (56/90) | 6.9 / 10 |

TTD = time-to-detect (seconds), computed only for successful detections. Mitigation rate = faults both detected *and* resolved, out of total runs. Reasoning = average LLM-as-judge score across all 30 runs per category. Note: Network median (94s) is far below its mean (289s) because of bimodal distribution -- most successful detections are fast, but the slow ones are *very* slow.

### 3 -- Sample Raw Data (10 TTD values per category, sorted)

```
APPLICATION (10 of 27 successful detections) -- tight cluster, one outlier:
  98   112   125   131   139   145   152   161   178   287
  <----------- tight cluster ~100-180s ---------->   ^^^
                                                    outlier
  Character: Stable, predictable. Most runs detect in ~2-3 min.
  The 287s outlier is a pod crash requiring extra restart cycles.

NETWORK (10 of 15 successful detections) -- bimodal, very high spread:
  18    32    47    63    79    94   112   658   843   944
  <----- fast cluster ~18-112s ----->   <-- slow cluster ~658-944s -->
                 gap: 546s
  Character: Strongly bimodal. 7 runs detected quickly (simple partition),
  3 runs took 6-10x longer (cascading failure). 15 of 30 runs never
  detected the fault at all (50% detection rate).

RESOURCE (10 of 21 successful detections) -- right-skewed, moderate spread:
  138   165   189   215   242   261   298   341   412   539
  <------- bulk of values ~140-300s ------->  <-- right tail -->
  Character: Most detections take 3-5 min, but a long tail extends
  past 8 min as gradual resource exhaustion is harder to pinpoint.
```

### 4 -- How Each Hypothesis Uses This Data

| Test | Question | Method | Result from This Data |
|------|----------|--------|-----------------------|
| **H-01** | What is the plausible range for detection time? | Bootstrap BCa CI on IQM of time_to_detect | **App:** IQM = 143s [128s, 159s] -- tight CI, predictable. **Net:** IQM = 266s [127s, 418s] -- CI width 291s, extremely uncertain. **Res:** IQM = 258s [221s, 301s] -- moderate CI width |
| **H-02** | What is the true detection rate? | Wilson CI on detection_rate | **Overall:** 70.0% [60.0%, 78.6%] -- lower bound 60% is below 65% floor. **App:** 90.0% [74.4%, 96.5%] -- safely above threshold. **Net:** 50.0% [33.2%, 66.9%] -- upper bound 66.9% barely crosses 65%. **Res:** 70.0% [52.1%, 83.3%] -- lower bound includes failure zone |
| **H-03** | Does the agent handle all fault types equally fast? | Kruskal-Wallis then Mann-Whitney U post-hoc + A12 | **Kruskal-Wallis:** H = 9.87, p = 0.007 -- at least one group differs. **Post-hoc:** App vs Net: U=42, p_adj=0.003, A12=0.81 (large effect). App vs Res: U=98, p_adj=0.018, A12=0.78 (large). Net vs Res: U=134, p_adj=0.412, A12=0.53 (negligible) |
| **H-04** | Is the success rate uniform across categories? | Fisher's Exact Test (3x2 table) | **Fisher-Freeman-Halton:** p = 0.003 -- rates are NOT uniform. **Post-hoc:** App vs Net: p_adj=0.002 (Network significantly worse). App vs Res: p_adj=0.104, Net vs Res: p_adj=0.152 (not significant after correction) |
| **H-05** | Is the agent consistently predictable? | Levene's Test + CV | **Levene:** F = 4.72, p = 0.012 -- variances differ significantly. **CV:** App = 0.38 (moderate), **Net = 1.40 (extreme)**, Res = 0.42 (moderate). Network CV exceeds 0.50 instability threshold by nearly 3x |

### 5 -- Certification Verdict

| Category | Verdict | Rationale |
|----------|---------|-----------|
| **Application** | **CERTIFIED** | Detection 90% >= 80% threshold. Wilson lower bound 74.4% safely above 65% floor. CV = 0.38 < 0.50 (predictable). Reasoning 8.4 >= 7.0. IQM CI is tight [128s, 159s]. All gates pass. |
| **Network** | **WITHHELD** | Detection 50% < 65% minimum floor. Wilson upper bound only 66.9% -- even best-case is marginal. CV = 1.40 > 0.50 (wildly inconsistent). IQM CI spans 291s [127s-418s]. Fisher's exact confirms significantly worse than Application (p=0.002). Multiple gate failures. |
| **Resource** | **CONDITIONAL** | Detection 70% >= 65% floor, but Wilson lower bound 52.1% dips into failure zone. CV = 0.42 < 0.50 (acceptable). Reasoning 7.1 >= 7.0 (borderline). Passes core gates but with limited margin -- requires monitoring and re-evaluation at next cycle. |
| **Overall** | **CONDITIONAL** | **SRE-Agent v2.1 receives conditional certification with network fault exclusion.** Certified for application faults. Conditionally certified for resource faults with mandatory re-evaluation. Network fault handling excluded from certification scope pending remediation of detection rate (target >=65%) and variance reduction (target CV < 0.50). |

---

## 1. Wilson Confidence Interval

> `H-01` `H-02` - a.k.a. Wilson Score Interval - For binary rates (yes/no outcomes)

### What Problem Does This Solve?

When the agent either succeeds or fails at detecting/fixing a fault, the success rate is a simple fraction (e.g., 24 out of 30 = 80%). But **how sure are you that 80% is the real rate?** The Wilson interval gives you a range -- "the true rate is almost certainly between 62% and 91%." The lower bound is your *safety floor* (worst plausible performance). Unlike the naive formula you learn in textbooks, Wilson works correctly even when the rate is 0% or 100%, and never gives nonsensical negative values.

### How It Works

1. Count successes (*x*) out of total trials (*n*). Compute rate: p-hat = x/n.
2. Pick your confidence level (95% -> z = 1.96).
3. The Wilson formula "centers" p-hat toward 0.5 by adding z^2/2n imaginary trials, then adjusts the margin of error:

```
lower, upper = (p-hat + z^2/2n +/- z*sqrt(p-hat*(1-p-hat)/n + z^2/4n^2)) / (1 + z^2/n)
```

This "shrinkage" toward 0.5 is what makes Wilson safe at extreme rates (0% or 100%) and small samples.

### In Simple Terms

> Think of a **bathroom scale**. You step on it and it says 70 kg. But how accurate is that scale? Wilson CI is like stepping on and off 100 times and noting the range -- "this scale reads between 68 and 72 kg." You trust the range more than any single reading. For certification, we care about the *bottom of the range* -- even in the worst plausible case, is the agent still above the minimum bar?

### Hypothesis Reference

> **H-02 (Success Rate Estimation):** Wilson CI is the primary method. The pipeline computes Wilson CIs for detection rate, mitigation rate, and any binary outcome. The **lower bound** is compared against the certification floor (e.g., 65%). If Wilson lower bound < floor, the agent fails H-02 for that category.
>
> **H-01 (Metric Estimation):** Wilson CI also feeds into H-01 when the metric is binary. For continuous metrics like time-to-detect, Bootstrap BCa (Method 2) is used instead.

### Why Not the Textbook Formula?

| Problem | Textbook (Wald) | Wilson |
|---------|----------------|-------|
| 0 out of 30 observed | CI = [0.0, 0.0] -- falsely says rate is exactly 0 | CI = [0.0, 0.12] -- correctly admits uncertainty |
| 30 out of 30 observed | CI = [1.0, 1.0] -- falsely certain | CI = [0.88, 1.0] -- honest upper bound |
| Can give negative bounds? | Yes (nonsensical) | Never -- always stays within [0, 1] |
| Accuracy at small n | Often wrong -- actual coverage ~85% instead of 95% | Close to 95% even at n=10 |

### Output Example *(from Running Example)*

```
Detection rate per fault category (n=30 each):
  Application:  27/30 = 90.0%   Wilson 95% CI: [0.744, 0.965]
  Network:      15/30 = 50.0%   Wilson 95% CI: [0.332, 0.669]
  Resource:     21/30 = 70.0%   Wilson 95% CI: [0.521, 0.833]
  Overall:      63/90 = 70.0%   Wilson 95% CI: [0.600, 0.786]

  Key insight: Network upper bound (66.9%) barely crosses the 65% floor.
  Even in the best plausible case, network detection is marginal.
  Application lower bound (74.4%) safely above threshold -- robust pass.
```

### Sources

- **Wilson, E.B. (1927)** "Probable inference, the law of succession, and statistical inference." *JASA*, 22(158), 209-212 -- [DOI](https://doi.org/10.1080/01621459.1927.10502953)
- **Brown, Cai & DasGupta (2001)** "Interval estimation for a binomial proportion." *Statistical Science* -- [Project Euclid](https://projecteuclid.org/euclid.ss/1009213286)
- [Wikipedia: Wilson Score Interval](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval#Wilson_score_interval)
- Python: `statsmodels.stats.proportion.proportion_confint(method='wilson')`

---

## 2. Bootstrap BCa Confidence Interval

> `H-01` - Bias-Corrected and Accelerated Bootstrap - For continuous metrics (times, scores)

### Simulating 50,000 Experiments From 30 Runs

Imagine you could re-run all 30 experiments 50,000 times. Each time, the numbers would shift slightly. Bootstrap *simulates* this by reshuffling your existing data with replacement 50,000 times, computing the metric each time, and looking at how much it varies. The BCa variant is the "smart" version that corrects for two biases: (1) the average of reshuffled results being systematically off from the true value, and (2) the uncertainty being different at different true values (skewness).

### How It Works

1. **Resample**: From your n observations, randomly draw n values *with replacement* (some values get picked multiple times, some skipped). This is one "bootstrap sample."
2. **Compute**: Calculate the statistic (mean, P95, IQM) on that bootstrap sample.
3. **Repeat 50,000 times**: Now you have 50,000 estimates. This is the "bootstrap distribution."
4. **Bias-correction (z0)**: Measure how far the center of the bootstrap distribution is from the original estimate.
5. **Acceleration (a)**: Use jackknife (leave-one-out) to measure how the standard error changes with the parameter value.
6. **Adjusted percentiles**: Instead of naively taking the 2.5th and 97.5th percentiles, adjust the cutoffs using z0 and a to get a more accurate interval.

### In Simple Terms

> Imagine you have a **jar with 30 numbered balls** (one for each run's detection time). You reach in, grab a ball, write down its number, *put it back*, and repeat 30 times -- that's one "bootstrap sample." Some balls get picked twice, some not at all. Now compute the average of those 30 draws. Repeat this 50,000 times. The range of those 50,000 averages tells you how much uncertainty your original average carries. It's like asking: "If I ran this experiment on 50,000 different days, how much would my answer bounce around?"

### Hypothesis Reference

> **H-01 (Metric Estimation):** Bootstrap BCa is the primary CI method for all continuous metrics -- time_to_detect, reasoning_quality_score, P95 latency. It wraps around IQM (Method 3) to produce intervals like "IQM = 143s [128s, 159s]." The CI width itself is a diagnostic: narrow = predictable agent, wide = unreliable estimate. In the running example, Network's 291s CI width is a red flag independent of the point estimate.

### Why 50,000 Resamples?

[Agarwal et al. (NeurIPS 2021)](https://arxiv.org/abs/2108.13264) showed that at B=10,000, the tail percentiles are estimated from only ~250 samples each, making CIs unstable. At B=50,000, each tail uses ~1,250 samples -- much more stable. Modern hardware completes this in seconds.

### Output Example *(from Running Example)*

```
time_to_detect (IQM) per fault category (50,000 bootstrap resamples):
  Application:  IQM = 143s   95% BCa CI: [128s, 159s]   width:  31s  (tight)
  Network:      IQM = 266s   95% BCa CI: [127s, 418s]   width: 291s  (very wide!)
  Resource:     IQM = 258s   95% BCa CI: [221s, 301s]   width:  80s  (moderate)

  Interpretation: Application is fast and predictable (tight CI).
  Network CI spans 291s -- we cannot pin down typical detection time at all.
  This extreme CI width is itself a red flag for certification.
```

### Sources

- **Efron, B. (1987)** "Better Bootstrap Confidence Intervals." *JASA*, 82(397), 171-185 -- [DOI](https://doi.org/10.1080/01621459.1987.10478410)
- **Efron & Tibshirani (1993)** *An Introduction to the Bootstrap*. Chapman & Hall.
- **Agarwal et al. (2021)** "Deep RL at the Edge of the Statistical Precipice." NeurIPS -- [arXiv:2108.13264](https://arxiv.org/abs/2108.13264)
- [Wikipedia: Bootstrapping (statistics)](https://en.wikipedia.org/wiki/Bootstrapping_(statistics))
- Python: `scipy.stats.bootstrap`

---

## 3. Interquartile Mean (IQM)

> `H-01` - 25% Trimmed Mean - Robust central tendency

### The Outlier-Proof Average

Sort all your numbers, throw away the bottom 25% and top 25%, then average what's left. This gives you a "typical" value that isn't thrown off by a few crazy-fast or crazy-slow runs. It's more honest than the mean (which one outlier can distort) and more informative than the median (which ignores half your data).

### How It Works

1. Sort all n values from smallest to largest.
2. Remove the bottom 25% and top 25%.
3. Average the remaining middle 50%.

```
IQM = average of values between Q1 (25th percentile) and Q3 (75th percentile)
```

| Metric | Outlier-proof? | Uses how much data? | Efficiency |
|--------|---------------|-------------------|------------|
| Mean | No -- one extreme value wrecks it | 100% | Best (if no outliers) |
| Median | Yes | ~1 data point | Low (~64%) |
| **IQM** | **Yes** | **50%** | **High (~87%)** |

### In Simple Terms

> Think of **Olympic diving scores**. Judges give their scores, then the highest and lowest are thrown out before averaging. This protects against one biased judge ruining the result. IQM does the same thing but throws away the top 25% and bottom 25% instead of just one each. The result is a "typical" score that represents the middle-of-the-pack experience, immune to flukes in either direction.

### Hypothesis Reference

> **H-01 (Primary Metric):** IQM is the *default* point estimate for all continuous metrics in the framework. When the pipeline reports "time_to_detect = 143s," that's the IQM, not the mean. Bootstrap BCa (Method 2) then wraps a confidence interval around this IQM. This combination -- IQM as point estimate, BCa as uncertainty -- is recommended by [Agarwal et al. (2021)](https://arxiv.org/abs/2108.13264) for agent evaluation.

### Output Example *(from Running Example)*

```
time_to_detect for NETWORK faults (15 successful detections):
  Mean:    289s   (pulled up by slow-cluster values: 658s, 843s, 944s)
  Median:   94s   (lands in fast cluster -- ignores slow cluster entirely)
  IQM:    266s   (trims 25% extremes each side, still reflects bimodal shape)

  Why the huge gap?  Mean - Median = 195s.
  Bimodal data makes both mean and median misleading in different ways.
  IQM provides the best single-number summary, but CI width (291s) reveals
  that even IQM struggles with this distribution. <-- primary reported metric
```

### Sources

- **Agarwal et al. (2021)** -- Recommends IQM as primary aggregate for agent evaluation -- [arXiv:2108.13264](https://arxiv.org/abs/2108.13264)
- [Wikipedia: Interquartile Mean](https://en.wikipedia.org/wiki/Interquartile_mean)
- Python: `scipy.stats.trim_mean(data, 0.25)`

---

## 4. Shapiro-Wilk Normality Test

> `H-03` - Pre-test gate - Decides if data follows a bell curve

### The Gatekeeper: Is Your Data Bell-Shaped?

Many statistical tests assume your data looks like a bell curve (normal distribution). Shapiro-Wilk checks: **"Does this data actually look like a bell curve?"** If yes (p > 0.05), you can use the more powerful parametric tests. If no, you must use rank-based tests that don't need this assumption. It's a gatekeeper -- it decides *which path* the analysis takes.

### How It Works

1. Sort the data and compute the "expected" values if the data *were* normal.
2. Compute the W statistic: how well the data matches a perfect normal distribution. W ranges from 0 to 1, where 1 = exactly normal.
3. If W is close to 1 -> p-value is high -> data looks normal.
4. If W is low -> p-value is small -> data is NOT normal.

```
Decision rule:
  p > 0.05  -->  Pass (data is normal enough)  -->  allow Welch's ANOVA
  p <= 0.05 -->  Fail (data is NOT normal)     -->  use Kruskal-Wallis instead
```

### In Simple Terms

> Imagine a **bouncer at "Door A" of a nightclub**. Door A leads to the VIP area (Welch's ANOVA -- more powerful tests). The bouncer checks if your data looks "normal enough" to enter. If your data is well-behaved (bell-shaped), you get in and enjoy the VIP treatment. If your data is messy (bimodal, heavily skewed), the bouncer redirects you to "Door B" (Kruskal-Wallis) which lets everyone in but isn't as fancy. Either way, you get tested -- but the path matters for statistical power.

### Hypothesis Reference

> **H-03 (Gate):** Shapiro-Wilk is the first step in the H-03 pipeline. It runs on each group's data independently. In the running example, Network TTD fails normality (W=0.681, p<0.001) because of its bimodal distribution -- this single failure forces the entire H-03 comparison down the Kruskal-Wallis path, even though Application and Resource pass individually. The test is conservative by design: one non-normal group means ALL groups use the non-parametric route.

### Output Example *(from Running Example)*

```
Shapiro-Wilk normality test on time_to_detect per fault category:
  application_fault (n=27): W=0.942, p=0.145 --> NORMAL  (one outlier at 287s, not extreme enough to fail)
  network_fault    (n=15): W=0.681, p<0.001 --> NOT NORMAL  (strongly bimodal: 18-112s vs 658-944s)
  resource_fault   (n=21): W=0.952, p=0.234 --> NORMAL  (right-skewed but within tolerance)

Decision: Network fails normality (bimodal) --> use Kruskal-Wallis (non-parametric path).
Note: Even one failing group forces the non-parametric route for the overall comparison.
```

### Sources

- **Shapiro & Wilk (1965)** "An analysis of variance test for normality." *Biometrika*, 52(3-4), 591-611 -- [DOI](https://doi.org/10.1093/biomet/52.3-4.591)
- [Wikipedia: Shapiro-Wilk test](https://en.wikipedia.org/wiki/Shapiro%E2%80%93Wilk_test)
- Python: `scipy.stats.shapiro`

---

## 5. Kruskal-Wallis H Test

> `H-03` - Primary comparison test - Rank-based ANOVA alternative

### Detecting Hidden Weaknesses Across Categories

You have detection times for three fault types. **Are they all roughly the same, or does one type take significantly longer?** Kruskal-Wallis answers this without assuming bell-curve data. It works by ranking all observations (fastest = rank 1, slowest = rank 30) and checking if one group has suspiciously high or low ranks. Think of it as: "If I shuffled these into one pile and ranked them, would one group cluster at the top or bottom?"

### How It Works

1. **Pool** all observations from all groups into one list.
2. **Rank** them from 1 (smallest) to N (largest).
3. **Compute** the average rank for each group.
4. **Compare**: If groups are truly equal, average ranks should be similar. The H statistic measures how different the group ranks are.
5. If H is large enough (p < 0.05), at least one group is detectably different.

**Important:** It tells you "at least one group is different" but NOT which one. That's where Mann-Whitney U (Method 6) comes in as a follow-up.

### In Simple Terms

> Imagine a **principal ranking students across 3 schools** on a test. She puts all students in one big list from lowest to highest score, then checks each school's average rank. If School B's students cluster near the bottom while School A's cluster near the top, there's a real difference -- even if she can't pinpoint exactly which students caused it. Kruskal-Wallis does this ranking across fault categories to ask: "Is the agent equally good at all fault types, or is it struggling with one?"

### Hypothesis Reference

> **H-03 (Omnibus Test):** Kruskal-Wallis is the primary "are groups different?" test for H-03 when data fails normality (which it usually does). In the running example, H=9.87, p=0.007 tells us detection times are NOT equal across fault types. This triggers the post-hoc cascade: Mann-Whitney U (Method 6) identifies *which* pairs differ, and A12 (Method 7) measures *how much* they differ. Without this omnibus test first, running pairwise comparisons would inflate false positive risk.

### Output Example *(from Running Example)*

```
Kruskal-Wallis test for time_to_detect across fault categories:
  Groups: Application (n=27), Network (n=15), Resource (n=21)
  H statistic: 9.87
  df: 2
  p-value: 0.007
  Interpretation: At least one fault type has significantly different detection time
                  (p < 0.05 --> difference is real, not noise).

  Next step: Run pairwise Mann-Whitney U to find WHICH categories differ.
```

### Sources

- **Kruskal & Wallis (1952)** "Use of ranks in one-criterion variance analysis." *JASA*, 47(260), 583-621 -- [DOI](https://doi.org/10.1080/01621459.1952.10483441)
- [Wikipedia: Kruskal-Wallis test](https://en.wikipedia.org/wiki/Kruskal%E2%80%93Wallis_one-way_analysis_of_variance)
- Python: `scipy.stats.kruskal`

---

## 6. Mann-Whitney U Test

> `H-03` - Pairwise post-hoc - Two-group rank comparison

### Pinpointing the Weak Link

After Kruskal-Wallis tells you "at least one group is different," Mann-Whitney U pinpoints **which pair** is different. It takes two groups and asks: *"If I randomly pick one value from group A and one from group B, what is the probability that A is larger than B?"* If this probability is far from 50/50, the groups are meaningfully different.

### How It Works

1. Take two groups (e.g., application faults vs. network faults).
2. For every pair (one value from each group), count how many times group A's value is larger.
3. The U statistic represents this count. Convert U to a p-value.
4. **Holm-Bonferroni correction**: When comparing multiple pairs (A vs B, A vs C, B vs C), adjust p-values upward to prevent false positives from multiple testing.

### In Simple Terms

> Think of a **fire alarm vs. a firefighter**. Kruskal-Wallis is the fire alarm -- it screams "there's a fire somewhere in the building!" Mann-Whitney U is the firefighter who goes room by room to find *exactly which room* is on fire. In certification terms: the alarm (Kruskal-Wallis p=0.007) went off, and the firefighter (Mann-Whitney) found the fire in the App-vs-Network comparison (p=0.003) and App-vs-Resource comparison (p=0.018), but not in Network-vs-Resource (p=0.412).

### Hypothesis Reference

> **H-03 (Post-hoc):** Mann-Whitney U runs only after Kruskal-Wallis finds a significant difference. It performs all pairwise comparisons (with k groups, that's k*(k-1)/2 tests -- 3 pairs for our 3 categories). P-values are Holm-Bonferroni corrected to control family-wise error rate. Each significant pair is then measured with A12 (Method 7) to determine *practical* significance. In the running example: App is significantly faster than both Network and Resource, but Network and Resource are statistically indistinguishable despite different distributions.

### Output Example *(from Running Example)*

```
Pairwise Mann-Whitney U for time_to_detect (with Holm-Bonferroni correction):
  App vs Network:     U = 42,   p_adj = 0.003 --> SIGNIFICANT (App faster)
  App vs Resource:    U = 98,   p_adj = 0.018 --> SIGNIFICANT (App faster)
  Network vs Resource: U = 134, p_adj = 0.412 --> not significant

Conclusion: Application faults are detected significantly faster than both
  Network and Resource faults. Network and Resource detection times are
  statistically indistinguishable (despite different distributions).
```

### Sources

- **Mann & Whitney (1947)** "On a test of whether one of two random variables is stochastically larger." *Annals of Mathematical Statistics*, 18(1), 50-60 -- [DOI](https://doi.org/10.1214/aoms/1177730491)
- [Wikipedia: Mann-Whitney U test](https://en.wikipedia.org/wiki/Mann%E2%80%93Whitney_U_test)
- Python: `scipy.stats.mannwhitneyu`

---

## 7. Vargha-Delaney A12 Effect Size

> `H-03` - Probability-based effect size - "How big is the gap?"

### Beyond p-Values: How Big Is the Gap?

A p-value tells you a difference is *real*, but not how *big* it is. A12 answers the really intuitive question: **"If I randomly pick one run from group A and one from group B, what is the probability that A's value is larger?"** A12 = 0.50 means the groups are identical. A12 = 0.92 means group A is larger 92% of the time -- a huge gap.

### How It Works

```
A12 = P(X > Y) + 0.5 * P(X = Y)

where X is from group A, Y is from group B
```

| A12 Value | Effect Size | Meaning |
|-----------|-------------|---------|
| 0.50 | None | Groups are identical |
| 0.56 | Small | Barely noticeable difference |
| 0.64 | Medium | Meaningful difference |
| 0.71+ | Large | Groups are clearly different |

Always reported alongside p-values because a "statistically significant" difference might be tiny in practice (p=0.04 but A12=0.52), or a meaningful difference might not reach significance at small n.

### In Simple Terms

> Imagine a **coin flip game**. You randomly pick one detection time from Application and one from Network. A12 = 0.81 means Application is faster 81% of the time. That's not a fair coin -- it's heavily loaded. If A12 were 0.53, it's basically 50/50 -- no practical difference. This is more intuitive than a p-value because it directly answers "how often does group A beat group B?" rather than the abstract "is the difference statistically unlikely under the null hypothesis?"

### Hypothesis Reference

> **H-03 (Effect Size):** A12 is reported for every significant Mann-Whitney pair. A significant p-value alone can't drive a certification decision -- the gap must also be *practically* meaningful. In the running example, App-vs-Network has A12=0.81 (large effect), confirming the statistical significance reflects a real operational gap. Net-vs-Resource has A12=0.53 (negligible) -- even though their distributions look different (bimodal vs. right-skewed), their overall detection times are statistically indistinguishable.

### Output Example *(from Running Example)*

```
Effect sizes for time_to_detect (reported alongside Mann-Whitney p-values):
  App vs Network:     A12 = 0.81 (LARGE)  --> App is faster 81% of the time
  App vs Resource:    A12 = 0.78 (LARGE)  --> App is faster 78% of the time
  Network vs Resource: A12 = 0.53 (NEGLIGIBLE) --> coin-flip, no practical gap

  Insight: The App-vs-Net gap is not just statistically significant (p=0.003),
  it is practically large (81% dominance). Net-vs-Res is not significant AND
  has negligible effect size -- consistent evidence of no real difference.
```

### Sources

- **Vargha & Delaney (2000)** "A Critique and Improvement of the CL Common Language Effect Size." *JBES*, 25(2), 101-132 -- [DOI](https://doi.org/10.3102/10769986025002101)
- [Wikipedia: Vargha-Delaney A](https://en.wikipedia.org/wiki/Effect_size#Vargha_and_Delaney's_A)
- **Arcuri & Briand (2011)** "A practical guide for using statistical tests to assess randomized algorithms in software engineering." *ICSE* -- [DOI](https://doi.org/10.1145/1985793.1985795)

---

## 8. Welch's ANOVA

> `H-03` - Conditional parametric test - Only used when data is normal

### More Statistical Power -- If You Earn It

This is the *powerful but picky* version of Kruskal-Wallis. It's **more likely to detect real differences** (higher statistical power), but it only works when your data follows a bell curve. That's why Shapiro-Wilk (Method 4) acts as a gate: only when ALL groups pass the normality check does Welch's ANOVA get used. Unlike standard ANOVA, Welch's version doesn't require equal variances across groups, making it more practical for real data.

### How It Works

```
Shapiro-Wilk for ALL groups:
  ALL pass (p > 0.05)?  --YES-->  Use Welch's ANOVA (more powerful)
                        --NO--->  Use Kruskal-Wallis (always safe)
```

### In Simple Terms

> Think of **a metal detector vs. a ground-penetrating radar**. The metal detector (Kruskal-Wallis) works on any terrain but only finds large objects. The ground-penetrating radar (Welch's ANOVA) finds much smaller objects but requires flat, open ground to operate. Shapiro-Wilk checks if the ground is flat enough. In our running example, the Network data is like rough, broken terrain (bimodal) -- so we're stuck with the metal detector. But for reasoning scores (smooth, bell-shaped data), we get to use the radar and detect subtler differences.

### Hypothesis Reference

> **H-03 (Parametric Path):** Welch's ANOVA is the *alternate* omnibus test for H-03 -- used only when all groups pass Shapiro-Wilk. In the running example, it's used for reasoning_quality_score (F=11.34, p=0.002) because those scores are normally distributed. For time_to_detect, Network's bimodal distribution forces the non-parametric path (Kruskal-Wallis). The framework always reports *which* path was taken and why, so the reader knows whether the comparison had full statistical power or was conservatively tested.

### Output Example *(from Running Example)*

```
Welch's ANOVA for reasoning_quality_score:
  Groups: App (mean=8.4, n=30), Net (mean=5.2, n=30), Res (mean=7.1, n=30)
  F statistic: 11.34
  p-value: 0.002
  Interpretation: Reasoning quality differs significantly across fault categories.

  (Used because reasoning scores passed Shapiro-Wilk in all 3 groups.
   For time_to_detect, we used Kruskal-Wallis instead because
   Network TTD failed the normality test due to bimodal distribution.)
```

### Sources

- **Welch, B.L. (1951)** "On the comparison of several mean values." *Biometrika*, 38, 330-336 -- [DOI](https://doi.org/10.1093/biomet/38.3-4.330)
- [Wikipedia: Welch's t-test / ANOVA generalization](https://en.wikipedia.org/wiki/Welch%27s_t-test)
- Python: `scipy.stats.f_oneway` (then use pingouin for Welch's variant)

---

## 9. Chi-Square / Fisher's Exact Test

> `H-04` - Rates comparison - Is success rate uniform across fault types?

### When the Average Lies About Success

If 90% of application faults are detected but only 50% of network faults, the average 70% hides a problem. **Chi-Square tests whether these pass rates are really different or just random variation.** It compares the actual success/failure counts per category against what you'd *expect* if the rate were truly the same everywhere. Fisher's Exact is the small-sample backup -- it calculates the *exact* probability instead of an approximation.

### How It Works

1. Build a table: rows = fault categories, columns = success vs failure counts.
2. **Expected counts**: If the overall rate were the same for everyone, how many successes/failures would each category have?
3. **Chi-squared statistic**: Sum of (observed - expected)^2 / expected. Large value = big discrepancy.
4. If any expected cell count is < 5, switch to Fisher's Exact (computes exact probability using combinatorics).

### In Simple Terms

> Imagine a **student with a 3.0 GPA** -- looks decent on paper. But when you break it down by subject: Math = A, English = A, Science = F, History = A. The average "hides" a critical failure in one subject. Fisher's Exact does this for your agent: sure, the *overall* detection rate is 70%, but is that 70% spread evenly, or is there a hidden "F" in one fault category? In our running example, Network's 50% is the "F in Science" that the overall 70% GPA tries to paper over.

### Hypothesis Reference

> **H-04 (Uniformity):** Fisher's Exact is the sole method for H-04. It tests whether detecting faults is independent of fault category. In the running example, p=0.003 rejects uniformity -- the agent's success rate depends heavily on fault type. Post-hoc pairwise tests (also Fisher's, Holm-corrected) pinpoint Network as significantly worse than Application (p_adj=0.002). This directly impacts the certification verdict: even if the overall rate passes the threshold, a significantly non-uniform distribution triggers per-category evaluation.

### Output Example *(from Running Example)*

```
Fisher's Exact Test for detection success across fault categories:
                    Detected   Not Detected
  Application          27           3        (90%)
  Network              15          15        (50%)
  Resource             21           9        (70%)

  Fisher-Freeman-Halton exact test: p = 0.003
  Interpretation: Detection rates are NOT uniform across fault types.

  Post-hoc pairwise Fisher's exact (Holm-Bonferroni corrected):
    App vs Network:     p_adj = 0.002 --> SIGNIFICANT (Network worse)
    App vs Resource:    p_adj = 0.104 --> not significant (after correction)
    Network vs Resource: p_adj = 0.152 --> not significant (after correction)

  Conclusion: The overall 70% average masks a critical gap.
  Network (50%) is significantly worse than Application (90%).
```

### Sources

- **Pearson, K. (1900)** "On the criterion that a given system of deviations..." *Phil. Mag.* -- [DOI](https://doi.org/10.1080/14786440009463897)
- **Fisher, R.A. (1922)** "On the interpretation of chi-squared from contingency tables." *JRSS*
- [Wikipedia: Chi-squared test](https://en.wikipedia.org/wiki/Chi-squared_test)
- [Wikipedia: Fisher's exact test](https://en.wikipedia.org/wiki/Fisher%27s_exact_test)
- Python: `scipy.stats.chi2_contingency`, `scipy.stats.fisher_exact`

---

## 10. Levene's Test

> `H-05` - Variance stability - Is the agent consistently good (or bad)?

### Is Your Agent Reliably Good -- Or Just Lucky?

An agent that detects faults in 10s, then 500s, then 30s, then 800s is *unpredictable* even if its average looks okay. Levene's test checks: **"Is the spread (variability) of results the same for all fault types, or is the agent wildly inconsistent for some types?"** High variability in one category means you can't trust the average for that category.

### How It Works

1. For each observation, compute its **absolute deviation** from the group median: \|xi - median\|.
2. Now treat these deviations as data and run a regular ANOVA on them.
3. If the "deviation ANOVA" is significant (p < 0.05), the groups have different amounts of spread.

Also reports **CV (Coefficient of Variation)** per group = std_dev / mean:

| CV Range | Rating | Meaning |
|----------|--------|---------|
| < 0.15 | **Stable** | Predictable, low spread |
| 0.15 - 0.30 | **Moderate** | Some variability |
| > 0.30 | **High** | Wild swings -- unreliable |

### In Simple Terms

> Think of a **taxi service**. You order a taxi and it arrives in 5 minutes. Next time, 45 minutes. Then 3 minutes, then 60 minutes. The *average* is 28 minutes -- sounds fine on paper. But you'd never trust that service because you have no idea if you'll wait 3 minutes or an hour. Levene's test measures this unpredictability. CV (Coefficient of Variation) puts a number on it: CV = 0.15 means "reliably on time," CV = 1.40 means "might as well flip a coin." In our running example, the Network category has CV = 1.40 -- it's the unreliable taxi service of fault detection.

### Hypothesis Reference

> **H-05 (Variance Stability):** Levene's test + CV is the sole method for H-05. The test itself (F=4.72, p=0.012) tells us variances differ across categories. But the certification decision relies on the **per-category CV**: any group exceeding the 0.50 CV threshold is flagged as "unstable." In the running example, Network's CV=1.40 is nearly 3x the threshold -- this alone is sufficient to withhold certification for that category, regardless of whether the detection rate itself passes. An agent that's right 50% of the time but wildly unpredictable about *when* it's right is operationally untrustable.

### Output Example *(from Running Example)*

```
Levene's test for time_to_detect variance across fault categories:
  Levene statistic (median-based): 4.72
  df: (2, 60)
  p-value: 0.012
  Interpretation: Variances are NOT equal across fault types.

  Per-category Coefficient of Variation (CV = SD / Mean):
    Application:  SD =  57.8s, Mean = 152s  --> CV = 0.38  (moderate)
    Network:      SD = 404.6s, Mean = 289s  --> CV = 1.40  (EXTREME)
    Resource:     SD = 117.6s, Mean = 280s  --> CV = 0.42  (moderate)

  FLAGGED: Network CV = 1.40 exceeds the 0.50 instability threshold.
  The agent's network fault detection is wildly unpredictable --
  sometimes 18s, sometimes 944s. This variance alone blocks certification.
```

### Sources

- **Levene, H. (1960)** "Robust tests for equality of variances." In *Contributions to Probability and Statistics*, Stanford Univ. Press
- **Brown & Forsythe (1974)** "Robust tests for the equality of variances." *JASA*, 69, 364-367 -- [DOI](https://doi.org/10.1080/01621459.1974.10482955)
- [Wikipedia: Levene's test](https://en.wikipedia.org/wiki/Levene%27s_test)
- Python: `scipy.stats.levene`

---

## Quick Reference: Which Method Goes Where?

| Hypothesis | Question | Methods Used |
|------------|----------|-------------|
| **H-01** | What are the plausible ranges for all metrics? | Wilson CI, Bootstrap BCa, IQM |
| **H-02** | What is the true success/failure rate? | Wilson CI |
| **H-03** | Does the agent handle all fault types equally? | Shapiro-Wilk -> Kruskal-Wallis or Welch's ANOVA -> Mann-Whitney U + A12 |
| **H-04** | Is the success rate uniform across fault types? | Chi-Square / Fisher's Exact |
| **H-05** | Is the agent consistent or unpredictable? | Levene's Test + CV |

**Multiple-comparison correction:** When running many tests, p-values are adjusted using **Holm-Bonferroni** (for safety-critical metrics) or **Benjamini-Hochberg FDR** (for quality gates) to prevent false positives. [Learn more](https://en.wikipedia.org/wiki/Holm%E2%80%93Bonferroni_method)

---

*AgentCert - Statistical Hypothesis Framework Reference - 10 Methods - April 2026*

Built from: [Agarwal et al. 2021](https://arxiv.org/abs/2108.13264) - [Arcuri & Briand 2011](https://doi.org/10.1145/1985793.1985795) - [Brown, Cai & DasGupta 2001](https://projecteuclid.org/euclid.ss/1009213286)
