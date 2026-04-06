# Research Findings — Statistical Hypothesis Framework
## 15 Papers Grounding the AgentCert Certification Pipeline

> **Purpose**: This document consolidates all research paper findings that inform the
> Statistical Hypothesis Framework (`STATISTICAL_HYPOTHESIS_FRAMEWORK.html`).
> Each paper includes: URL, venue, key recommendations, framework gaps identified,
> and enhancements applied. Papers are organized into three thematic batches.

---

# Batch 1 — Statistical Methodology Core (6 Papers)

---

## 1. Agarwal et al. (2021) — "Deep RL at the Edge of the Statistical Precipice"
**URL**: https://arxiv.org/abs/2108.13264
**Venue**: NeurIPS 2021 (Outstanding Paper)
**Library**: `rliable` — https://github.com/google-research/rliable

### Key Recommendations
- **IQM (Interquartile Mean)**: Primary aggregate metric. 25% trimmed mean — discard top/bottom 25%, average the rest. `scipy.stats.trim_mean(scores, 0.25)`. More robust than mean, more efficient than median.
- **Stratified Bootstrap CI**: Resample within each task/category independently (B=50,000). Preserves group structure. Percentile bootstrap for CI construction.
- **Performance Profiles**: Empirical survival function P(score >= tau). Shows full distributional picture. Sweep tau from 0 to max_score. Include bootstrap confidence bands.
- **Score Normalization**: Required before cross-task aggregation. `(raw - random) / (reference - random)`.
- **B=50,000 resamples** (not 10,000). Minimum 5 runs per task.
- **Against mean**: not robust to outliers, high variance at small n.
- **Against median**: discards information, poor statistical efficiency.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| IQM not in framework (uses only mean/median) | **Added Method 3: IQM** as primary robust central tendency |
| Bootstrap is BCa B=10,000, not stratified B=50,000 | **Updated to Stratified Bootstrap BCa, B=50,000** in H-01 through H-04 |
| No performance profiles | Noted as future enhancement (requires visualization layer) |
| No score normalization | Not applicable in no-SLA mode (no reference baseline) |

---

## 2. Arcuri & Briand (2011) — "Statistical Tests for Randomized Algorithms in SE"
**URL**: https://doi.org/10.1016/j.infsof.2011.02.004
**Venue**: Information and Software Technology (IST) 53(8)

### Key Recommendations
- **Non-parametric tests as DEFAULT** (not fallback): Mann-Whitney U for pairwise, Kruskal-Wallis for multi-group.
- **Vargha-Delaney A₁₂ effect size**: P(X > Y) — probability algorithm A outperforms B. Thresholds: 0.56 small, 0.64 medium, 0.71 large. Non-parametric, rank-based, directly interpretable. Preferred over Cohen's d.
- **Holm-Bonferroni correction**: Step-down procedure, uniformly more powerful than plain Bonferroni while controlling FWER. Recommended over both plain Bonferroni and BH for multiple comparisons.
- **Minimum 1,000 runs** (ideal), **30 runs** (floor). More is always better.
- **Always report effect sizes alongside p-values**.
- **Report distributions** (box plots) not just aggregates.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| A₁₂ not in framework (no effect size) | **Added Method 7: Vargha-Delaney A₁₂** as primary effect size |
| Mann-Whitney U not in framework | **Added Method 6: Mann-Whitney U** for pairwise post-hoc |
| Kruskal-Wallis is fallback, not primary | **Promoted Method 5: Kruskal-Wallis to PRIMARY** (Welch's ANOVA now conditional) |
| Holm correction missing | **Added Holm-Bonferroni** for Tier 1 safety-critical metrics |

---

## 3. Henderson et al. (2018) — "Deep RL that Matters"
**URL**: https://arxiv.org/abs/1709.06560
**Venue**: AAAI 2018

### Key Recommendations
- **CIs over p-values**: Report bootstrap CIs on average return.
- **Report full distribution** of results across seeds.
- **Hyperparameter sensitivity analysis** — show what happens when you vary key parameters.
- **Same codebase** for all comparisons.
- **Reproducibility checklist**: exact hyperparameters, seeds, hardware, code, environment versions, evaluation protocol.
- 3-5 seeds grossly insufficient; 10+ minimum.
- **Normality cannot be assumed** — use bootstrap/permutation tests.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| Shapiro-Wilk normality pre-test missing | **Added Method 4: Shapiro-Wilk** as mandatory pre-test before parametric inference |
| CI-first approach partially addressed | Framework already uses CI-first — reinforced with IQM and stratified bootstrap |

---

## 4. Klees et al. (2018) — "Evaluating Fuzz Testing"
**URL**: https://doi.org/10.1145/3243734.3243804
**Venue**: ACM CCS 2018

### Key Recommendations
- **Minimum 30 independent trials** per configuration.
- **Mann-Whitney U test** for pairwise comparison.
- **Vargha-Delaney A₁₂** alongside p-value.
- Follows Arcuri & Briand methodology.
- Report **time-series data** not just end-of-campaign numbers.
- Results from < 30 trials are highly unreliable.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| Mann-Whitney U missing | **Added Method 6** (also cited in Arcuri & Briand) |
| Minimum runs not emphasized enough | **Updated Decision Parameters**: n≥5 (current), n≥30 (recommended), n≥1,000 (ideal) with paper citations |

---

## 5. Sagawa et al. (2020) — "Distributionally Robust Neural Networks for Group Shifts"
**URL**: https://arxiv.org/abs/1911.08731
**Venue**: ICLR 2020

### Key Recommendations
- **Worst-Group Accuracy**: `min_g Accuracy(g)` — certification should be judged by weakest fault category.
- **Group DRO**: Optimize for worst-case expected loss. Upweight groups with higher loss.
- **Per-Group Reporting**: Report accuracy separately for each group.
- Strong regularization critical for worst-group generalization.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| No worst-category reporting as binding constraint | **Added H-09: Worst-Category Assessment** — `min_g CI_lower(metric_g)` as certification floor |
| Framework compares categories but doesn't surface minimum | **Added transfer risk metric**: `max(|metric_i - metric_j|)` across category pairs |

---

## 6. Hendrycks & Dietterich (2019) — "Benchmarking Robustness to Common Corruptions"
**URL**: https://arxiv.org/abs/1903.12261
**Venue**: ICLR 2019

### Key Recommendations
- **Corruption Error (CE)**: `CE_c = sum_s Error_s_c^model / sum_s Error_s_c^baseline` — normalized degradation per corruption type.
- **Mean CE (mCE)**: Average CE across all corruption types.
- **Relative CE**: Isolates additional error from corruption: `(Error_corrupted - Error_clean) / (Error_baseline_corrupted - Error_baseline_clean)`.
- **Severity levels** (5 levels per corruption type).
- 15 corruption types across 4 categories (noise, blur, weather, digital).

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| CE requires baseline model (unavailable in no-SLA mode) | Not directly applicable — noted for future multi-agent comparison mode |
| Severity levels concept not in framework | Noted as future enhancement for fault severity stratification |
| Normalized robustness concept relevant | Conceptually informs transfer risk metric in H-09 |

---

# Batch 2 — Advanced Methods (4 Papers)

---

## 7. Sinha et al. (2018) — "Certifiable Distributional Robustness with Principled Adversarial Training"
**URL**: https://arxiv.org/abs/1710.10571
**Venue**: ICLR 2018

### Key Recommendations
- **Wasserstein DRO Certificate**: Provable upper bound on worst-case loss when true distribution lies within Wasserstein ball of empirical distribution.
- **Formula**: `sup_{P: W(P, P_hat) <= rho} E_P[l] <= inf_gamma { gamma*rho + (1/n) sum_i sup_z [l(z) - gamma*c(z, z_i)] }`
- **Robustness radius rho**: Maximum distributional shift under which performance guarantees hold.
- **Finite-sample guarantee**: With probability >= 1-delta, `W(P_true, P_hat) <= rho_n(delta)`.
- **Certification level**: Agent is "certified at level (alpha, rho)" if worst-case failure rate bounded.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| No distributional robustness certificates | Noted as P3 future work — requires n >> 5 for meaningful certificates |
| Wasserstein ball concept | Conceptually informs worst-category assessment (H-09) |
| Certified worst-case per fault category | Transfer risk metric partially addresses this |

---

## 8. Peters et al. (2016) — "Causal Inference by Using Invariant Prediction"
**URL**: https://doi.org/10.1111/rssb.12167
**Venue**: Journal of the Royal Statistical Society Series B (JRSS-B) 78(5)

### Key Recommendations
- **Invariance Testing**: If set S is truly causal, conditional P(Y|X_S) is invariant across all environments.
- **H₀(S)**: Set S satisfies invariance property across all environments.
- **Test procedure**: (1) Fit linear model per environment, (2) Test equality of coefficients (Chow/F-test), (3) Test equality of residual distributions (KS test), (4) Combine.
- **Intersection Test**: `S_hat = intersection of all S where H₀(S) not rejected`. Conservative — captures true causal set with probability >= 1-alpha.
- **Coverage guarantee**: `P(S* ⊆ S_hat) >= 1 - alpha`.
- **Uses Levene's, F-test, KS test across environments**.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| Fault categories not treated as environments | **Conceptually adopted**: fault categories = environments in invariance framework |
| Levene's test already in framework | **Reinforced** with paper reference in Method 10 |
| Invariance testing for metric selection | Noted as P2 future work — needs more categories/data |

---

## 9. Chen et al. (2018) — "Metamorphic Testing: A Review of Challenges and Opportunities"
**URL**: https://doi.org/10.1145/3143561
**Venue**: ACM Computing Surveys 51(1)

### Key Recommendations
- **Metamorphic Relations (MRs)**: Check relations between multiple executions, not individual outputs. Solves oracle problem.
- **Statistical MRs for non-deterministic systems**: `|f(T(x)) - f(x)| <= epsilon with prob >= 1-delta`. Use KS test, chi-squared for distribution comparison.
- **Fault Detection Rate**: `FDR(MR_i) = faults_detected / total_faults`.
- **Mutation Score**: `killed_mutants / total_mutants` — meta-certification metric.
- **MR Diversity**: Multiple orthogonal MRs increase coverage.
- **False positives**: Use statistical tolerance bounds, not exact equality.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| Oracle-free evaluation aligns with no-SLA mode | **Conceptual validation**: no-SLA mode is correct paradigm |
| Statistical MRs use hypothesis testing | Already present in framework (Wilson CI, Bootstrap CI for violation significance) |
| Mutation score as meta-metric | Noted as P3 future work |

---

## 10. Scholkopf et al. (2021) — "Toward Causal Representation Learning"
**URL**: https://doi.org/10.1109/JPROC.2021.3058954
**Venue**: Proceedings of the IEEE 109(5)

### Key Recommendations
- **ICM Principle**: Causal mechanisms are autonomous — intervening on one doesn't affect others.
- **Invariant prediction**: Predictor is causally valid if P(Y|f(X)) invariant across all environments.
- **Worst-case environment risk**: `max_e E_{P^e}[l(f(X), Y)]` — certify against worst-case category.
- **Transfer risk**: `max_{i,j} |Risk_i - Risk_j|` — performance gap across fault categories.
- **Sparse mechanism shift**: Natural distribution shifts affect few mechanisms. Improvement in one shouldn't degrade another.
- **Mechanism independence test**: Conditional independence between fault category interventions.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| Worst-case risk = worst-category reporting | **Added H-09**: Worst-Category Assessment with `min_g CI_lower` |
| Transfer risk missing | **Added transfer risk metric** in H-09: `max(|metric_i - metric_j|)` |
| Mechanism independence concept | Reinforces cross-category comparison tests (H-05, H-06, H-08) |

---

# Batch 3 — Evaluation Frameworks + Experiment Design (5 Papers)

---

## 11. Ma et al. (2024) — "AgentBoard: An Analytical Evaluation Board of Multi-Turn LLM Agents"
**URL**: https://arxiv.org/abs/2401.13178
**Venue**: ACL 2024

### Key Recommendations
- **Progress Rate**: `completed_subgoals / total_subgoals` — continuous [0,1], enables parametric tests (richer than binary success/fail).
- **Multi-dimensional analysis**: Per-category, per-difficulty, across interaction rounds.
- **Per-category progress rates**: Mean per task category — enables ANOVA-style cross-category comparisons.
- **Performance gap**: `progress_rate - success_rate` — diagnostic for partial capability.
- **Radar/spider charts** for multi-category profiles.
- **Gap**: No CIs reported, no bootstrap, no formal statistical tests between models.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| Progress rate concept applicable | Continuous metrics in framework (reasoning_score, response_quality_score) fill this role |
| Multi-dimensional profiling | **Reinforced** per-category reporting across all hypotheses |
| No CIs in AgentBoard itself | AgentCert framework adds what AgentBoard lacks — CIs for all metrics |

---

## 12. Lightman et al. (2023) — "Let's Verify Step by Step"
**URL**: https://arxiv.org/abs/2305.20050
**Venue**: ICLR 2024

### Key Recommendations
- **Process supervision > Outcome supervision**: Step-level evaluation outperforms final-result-only evaluation.
- **First-error localization**: Identify first incorrect step — analogous to root-cause fault identification.
- **Best-of-N sampling**: Generate N solutions, select best with process reward model. Performance ~ log(N).
- **Diminishing returns after N~100**: Suggests ~100 runs per scenario may be sufficient.
- **Per-subject/category breakdown**: Performance stratified by problem category and difficulty.
- **Gap**: No CIs, no bootstrap, no formal significance testing.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| Step-level evaluation concept | Conceptually aligns with fault trace analysis (process-level certification) |
| log(N) diminishing returns | **Informs sample size guidance**: n≥30 floor, n≥100 for high-confidence |
| Per-category stratification | Already in framework — reinforced |

---

## 13. Basiri et al. (2019) — Netflix Chaos Engineering ("Automating Chaos Experiments in Production")
**URL**: https://arxiv.org/abs/1905.02400
**Venue**: ICSE 2019 / Netflix Tech Blog

### Key Recommendations
- **Steady-state hypothesis testing**: H₀ = "system maintains steady-state under fault injection." Control vs. treatment comparison.
- **Sequential testing (SPRT)**: Early termination when significance reached. Minimizes blast radius.
- **Effect size**: Cohen's d alongside p-values for magnitude quantification.
- **Percentile-based metrics**: p50, p95, p99, p999 latency — not just means.
- **Bootstrap CIs on steady-state metrics**: 95% CI for both control and experiment groups.
- **Power analysis**: Determine required sample size for desired effect detection.
- **Progressive expansion**: Start small (1% blast radius), expand if no deviation.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| Steady-state concept for agent behavior | Conceptually informs CUSUM (Method 11) — drift from agent's own historical behavior |
| Sequential testing for large-scale cert | Noted as future enhancement for production-scale certification |
| Power analysis concept | **Informs** sample size justification in Decision Parameters |
| Paper reference added | **Linked** in CUSUM (Method 11) reference section |

---

## 14. Ribeiro et al. (2020) — "Beyond Accuracy: Behavioral Testing of NLP Models with CheckList"
**URL**: https://arxiv.org/abs/2005.04118
**Venue**: ACL 2020 (Best Paper)

### Key Recommendations
- **Three test types**: MFT (Minimum Functionality Test), INV (Invariance Test), DIR (Directional Expectation Test).
- **Failure rate per test type**: `failed_cases / total_cases` with Clopper-Pearson 95% CI.
- **Capability × Test Type matrix**: Every capability tested with all test types.
- **Minimum 100 test cases per cell** for meaningful failure rates.
- **Template-based test generation**: Systematic, not ad hoc.
- **Per-capability profiles**: Single aggregate score masks weak areas.
- **Perturbation robustness**: Synonymous descriptions, noisy inputs — INV tests.

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| Capability-based testing structure | **Added H-10: Tiered Certification** — Tier 1 (safety) / Tier 2 (quality) |
| Clopper-Pearson CI for failure rates | Already in framework as fallback for Wilson CI (Method 1) |
| Per-capability profiles | **Tiered assessment** ensures safety metrics evaluated separately from quality |
| Capability × fault_category matrix | Structural concept for extended framework |

---

## 15. McKay, Beckman & Conover (1979) — Latin Hypercube Sampling
**URL**: https://doi.org/10.2307/1268522
**Venue**: Technometrics 21(2)

### Key Recommendations
- **LHS for efficient space coverage**: Stratified sampling guaranteeing each input parameter's range is evenly covered.
- **Variance reduction**: LHS variance ≤ random sampling variance for additive models. For monotonic functions, O(N²) vs O(N) improvement.
- **Sample size**: N ≥ 10k where k = number of varying parameters.
- **Stratified bootstrap**: Resample within strata to preserve structure.
- **Replicated LHS**: Multiple independent LHS designs for CI computation.
- **Sensitivity analysis**: Which parameters most affect output?

### Framework Gap Identified → Enhancement Applied
| Gap | Enhancement |
|-----|-------------|
| Experiment design not statistically justified | Noted as future work — LHS for fault injection design |
| Stratified bootstrap concept | **Adopted** in Methods 2-3: Stratified Bootstrap BCa (B=50,000) per Agarwal et al. |
| Sample size formula (N ≥ 10k) | **Informs** Decision Parameters sample size guidance |
| Sensitivity analysis concept | Future work — identify most impactful fault parameters |

---

# Cross-Paper Synthesis

## Enhancements Applied to Framework (11 Methods, 10 Hypotheses)

### New Methods Added (5)
| Method | Source Papers | Status |
|--------|-------------|--------|
| **3. IQM (Interquartile Mean)** | Agarwal et al. (2021) | ✅ Added |
| **4. Shapiro-Wilk Pre-Test** | Henderson et al. (2018), Arcuri & Briand (2011) | ✅ Added |
| **5. Kruskal-Wallis H (PRIMARY)** | Arcuri & Briand (2011), Henderson (2018), Klees (2018) | ✅ Added |
| **6. Mann-Whitney U** | Arcuri & Briand (2011), Klees et al. (2018) | ✅ Added |
| **7. Vargha-Delaney A₁₂** | Arcuri & Briand (2011) | ✅ Added |

### Methods Updated (6)
| Method | Change | Source Papers |
|--------|--------|-------------|
| **1. Wilson CI** | Added Clopper-Pearson for safety metrics | Ribeiro (2020) |
| **2. Bootstrap CI** | B=10,000 → B=50,000, stratified | Agarwal (2021), McKay (1979) |
| **8. Welch's ANOVA** | Demoted to conditional (Shapiro-Wilk must pass) | Henderson (2018) |
| **9. Chi-Square/Fisher's** | Fisher's promoted to primary at n=5 | Arcuri & Briand (2011) |
| **10. Levene's** | Added Peters (2016) reference | Peters (2016) |
| **11. CUSUM** | Added Basiri (2019) reference | Basiri (2019) |

### New Hypotheses Added (2)
| Hypothesis | Source Papers |
|-----------|-------------|
| **H-09: Worst-Category Assessment** | Sagawa (2020), Scholkopf (2021) |
| **H-10: Tiered Certification** | Ribeiro (2020) |

### Corrections Updated
| Correction | Change | Source |
|-----------|--------|--------|
| **Holm-Bonferroni** | Added for Tier 1 safety metrics (replaces plain Bonferroni) | Arcuri & Briand (2011) |
| **BH-FDR** | Retained for Tier 2 quality metrics | — |

### Decision Parameters Updated
| Parameter | Change | Source |
|----------|--------|--------|
| Bootstrap B | 10,000 → 50,000 | Agarwal (2021) |
| IQM trim | Added: α_trim = 0.25 | Agarwal (2021) |
| A₁₂ thresholds | Added: 0.56/0.64/0.71 (small/medium/large) | Arcuri & Briand (2011) |
| Sample size | Added ideal n≥1,000 | Arcuri & Briand (2011) |
| Worst-category floor | Added: min_g across categories | Sagawa (2020), Scholkopf (2021) |

---

## Priority Roadmap for Future Enhancements

### P1 — Near Term (Current n=5)
All P1 items are **already implemented** in the framework.

### P2 — Medium Term (Requires n≥30)
| Enhancement | Source Papers | Requirement |
|-------------|-------------|-------------|
| KS test for distribution equality | Peters (2016), Chen (2018) | n≥30 for statistical power |
| Invariance testing across fault categories | Peters (2016), Scholkopf (2021) | More fault categories needed |
| Performance profiles | Agarwal (2021) | Visualization layer + n≥30 |
| Severity-level stratification | Hendrycks & Dietterich (2019) | Fault severity metadata |

### P3 — Long Term (Requires n≥100+)
| Enhancement | Source Papers | Requirement |
|-------------|-------------|-------------|
| Wasserstein DRO certificates | Sinha et al. (2018) | Large n for meaningful certificates |
| Mutation score (meta-certification) | Chen et al. (2018) | Synthetic fault injection infrastructure |
| Sequential testing (SPRT) | Basiri et al. (2019) | Production-scale certification runs |
| LHS experiment design | McKay et al. (1979) | Multi-parameter fault injection |
| Score normalization | Agarwal et al. (2021) | Multi-agent comparison mode |

---

## Literature Gap Validation

Two major AI-for-IT benchmarks were also reviewed:
- **AIOpsLab** (Chen et al., 2025, Microsoft Research) — arxiv 2501.06706
- **ITBench** (Jha et al., 2025, IBM Research) — arxiv 2502.05352

**Finding**: Neither paper provides formal statistical testing (no CIs, no hypothesis tests, no effect sizes, no multiple comparison corrections). Both use simple success rate percentages without uncertainty quantification. This confirms that **AgentCert's statistical hypothesis framework fills a genuine gap** in the AI agent evaluation literature.

---

*Document generated: 2026-04-03 | Accompanies: STATISTICAL_HYPOTHESIS_FRAMEWORK.html*
*38 paper URL references linked in the HTML framework*
