# Research Findings — Statistical Hypothesis Framework

[[_TOC_]]

## 20 Papers Grounding the AgentCert Certification Pipeline

> **Purpose**: This document consolidates all research paper findings that inform the
> Statistical Hypothesis Framework (`STATISTICAL_HYPOTHESIS_FRAMEWORK.md`).
> Each paper includes: URL, venue, key recommendations, framework gaps identified,
> and enhancements applied. Papers are organized thematically.

---

# Part 1 — Statistical Methodology Core (6 Papers)

---

## 1. Agarwal et al. (2021) — "Deep RL at the Edge of the Statistical Precipice"
**URL**: https://arxiv.org/abs/2108.13264
**Venue**: NeurIPS 2021 (Outstanding Paper)
**Library**: `rliable` — https://github.com/google-research/rliable

### Key Recommendations
- **IQM (Interquartile Mean)**: Primary aggregate metric. 25% trimmed mean — discard top/bottom 25%, average the rest. `scipy.stats.trim_mean(scores, 0.25)`.
- **Stratified Bootstrap CI**: Resample within each task/category independently (B=50,000). Percentile bootstrap for CI construction.
- **Performance Profiles**: Empirical survival function P(score >= tau).
- **B=50,000 resamples** (not 10,000). Minimum 5 runs per task.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-01** | IQM as primary robust central tendency (Method 3) |
| **H-01** | Stratified Bootstrap BCa, B=10,000 (Method 2) |
| **H-06** | Bootstrap CI compared against SLA thresholds |

---

## 2. Arcuri & Briand (2011) — "Statistical Tests for Randomized Algorithms in SE"
**URL**: https://doi.org/10.1016/j.infsof.2011.02.004
**Venue**: Information and Software Technology (IST) 53(8)

### Key Recommendations
- **Non-parametric tests as DEFAULT**: Mann-Whitney U for pairwise, Kruskal-Wallis for multi-group.
- **Vargha-Delaney A₁₂ effect size**: P(X > Y). Thresholds: 0.56 small, 0.64 medium, 0.71 large.
- **Holm-Bonferroni correction**: Step-down procedure, more powerful than plain Bonferroni.
- **Minimum 1,000 runs** (ideal), **30 runs** (floor).

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-03** | A₁₂ as primary effect size (Method 7) |
| **H-03** | Mann-Whitney U for pairwise post-hoc (Method 6) |
| **H-03** | Kruskal-Wallis promoted to PRIMARY (Method 5) |

---

## 3. Henderson et al. (2018) — "Deep RL that Matters"
**URL**: https://arxiv.org/abs/1709.06560
**Venue**: AAAI 2018

### Key Recommendations
- **CIs over p-values**: Report bootstrap CIs on average return.
- **Normality cannot be assumed** — use bootstrap/permutation tests.
- **Reproducibility checklist**: exact hyperparameters, seeds, hardware.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-03** | Shapiro-Wilk pre-test as mandatory gate (Method 4) |
| **H-01/H-02** | CI-first approach reinforced |

---

## 4. Klees et al. (2018) — "Evaluating Fuzz Testing"
**URL**: https://doi.org/10.1145/3243734.3243804
**Venue**: ACM CCS 2018

### Key Recommendations
- **Minimum 30 independent trials** per configuration.
- **Mann-Whitney U test** for pairwise comparison.
- **Vargha-Delaney A₁₂** alongside p-value.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **Sample size** | n≥30 per fault category as operational standard |
| **H-03** | Mann-Whitney U confirmed as post-hoc method |

---

## 5. Sagawa et al. (2020) — "Distributionally Robust Neural Networks for Group Shifts"
**URL**: https://arxiv.org/abs/1911.08731
**Venue**: ICLR 2020

### Key Recommendations
- **Worst-Group Accuracy**: `min_g Accuracy(g)` — judge by weakest category.
- **Per-Group Reporting**: Report accuracy separately for each group.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-03/H-05** | Worst-category awareness informs cross-category comparison |
| **H-03** | Transfer risk metric: `max(\|metric_i - metric_j\|)` across pairs |

---

## 6. Hendrycks & Dietterich (2019) — "Benchmarking Robustness to Common Corruptions"
**URL**: https://arxiv.org/abs/1903.12261
**Venue**: ICLR 2019

### Key Recommendations
- **Corruption Error (CE)**: Normalized degradation per corruption type.
- **Severity levels** across corruption types.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **Conceptual** | Testing across diverse fault types for robustness |
| **Future** | Severity-level stratification for fault injection |

---

# Part 2 — SLA & Risk Analysis Methods (5 Papers)

---

## 7. Wilcoxon (1945) — "Individual Comparisons by Ranking Methods"
**Venue**: *Biometrics Bulletin*, 1(6), 80–83

### Key Contributions
- **Signed-rank test**: Tests whether the median of a distribution differs from a specified value.
- **Non-parametric**: No normality assumption — uses ranks, robust to outliers.
- **One-sample variant**: Compares observations against a fixed threshold.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-06** | One-sample Wilcoxon signed-rank test for SLA threshold compliance |
| **H-06** | Non-parametric alternative to one-sample t-test for skewed agent data |

---

## 8. Clopper & Pearson (1934) — "The Use of Confidence or Fiducial Limits"
**Venue**: *Biometrika*, 26(4), 404–413

### Key Contributions
- **Exact binomial CI**: Guaranteed coverage probability ≥ 1-α.
- **No approximation**: Uses exact binomial distribution, not normal approximation.
- **Conservative**: Wider than Wilson CI but with guaranteed coverage.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-07** | Exact binomial test for SLA breach rate estimation |
| **H-02** | Fallback CI method for safety-critical binary metrics |

---

## 9. Schuirmann (1987) / Lakens (2017) — TOST Equivalence Testing
**Schuirmann**: *J. Pharmacokinetics and Biopharmaceutics*, 15(6), 657–680
**Lakens**: *Social Psychological and Personality Science*, 8(4), 355–362. https://doi.org/10.1177/1948550617697177

### Key Contributions
- **Two One-Sided Tests (TOST)**: Proves a parameter is *within* specified bounds.
- **Equivalence margins**: Define acceptable range [lower, upper].
- **Power analysis**: Compute required sample size for given effect size.
- **Pharmaceutical origin**: FDA bioequivalence standard.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-06** | TOST procedure proving performance within SLA bounds |
| **H-06** | Formal power analysis for SLA certification sample sizing |

---

## 10. Rockafellar & Uryasev (2000) / Artzner et al. (1999) — Tail Risk Measures
**Rockafellar**: *Journal of Risk*, 2(3), 21–42
**Artzner**: *Mathematical Finance*, 9(3), 203–228

### Key Contributions
- **CVaR (Conditional Value-at-Risk)**: Average of worst α% of outcomes.
- **Coherent risk measure**: Satisfies subadditivity, monotonicity, positive homogeneity, translation invariance.
- **Superior to VaR/P95**: Captures severity of tail events, not just threshold.
- **Expected shortfall**: E[X - threshold | X > threshold].

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-08** | CVaR₉₅ for tail risk severity quantification |
| **H-08** | Expected SLA overshoot: average violation magnitude |
| **H-08** | Bootstrap CI on CVaR for uncertainty in tail estimates |

---

## 11. Kaplan & Meier (1958) — "Nonparametric Estimation from Incomplete Observations"
**Venue**: *JASA*, 53(282), 457–481

### Key Contributions
- **Survival function estimation**: S(t) = P(event has not yet occurred at time t).
- **Handles censoring**: Properly models observations where the event was not observed.
- **Non-parametric**: No distribution assumption.
- **Log-rank test**: Compare survival curves across groups.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-06** | Kaplan-Meier survival curves for time-dependent SLA compliance |
| **H-06** | S(SLA_threshold) = breach probability at SLA time |
| **H-03** | Log-rank test as alternative cross-category comparison for censored data |

---

# Part 3 — Control & Monitoring Methods (2 Papers)

---

## 12. Page (1954) — "Continuous Inspection Schemes"
**Venue**: *Biometrika*, 41(1-2), 100–115

### Key Contributions
- **CUSUM (Cumulative Sum)**: Tracks cumulative deviations from target.
- **Sequential detection**: Signals change point when cumulative sum exceeds threshold.
- **Parametric**: S_t = max(0, S_{t-1} + (x_t - k)), signal when S_t > h.
- **Sensitive to persistent shifts**: Superior to Shewhart charts for detecting gradual drift.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-09** | CUSUM chart for drift detection toward SLA violation |
| **H-09** | SLA mode: target = SLA threshold; No-SLA mode: target = IQM baseline |

---

## 13. Roberts (1959) / Killick & Eckley (2014) — EWMA & Change Point Detection
**Roberts**: *Technometrics*, 1(3), 239–250
**Killick**: *Journal of Statistical Software*, 58(3)

### Key Contributions
- **EWMA**: Exponentially weighted moving average for gradual trend detection.
- **Change point detection (PELT)**: Identifies structural breaks in time series using penalized likelihood.
- **Complementary**: EWMA detects gradual drift; change point detects abrupt shifts.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-09** | EWMA smoothing for trend detection in agent performance |
| **H-09** | PELT change point detection for structural breaks (model updates) |

---

# Part 4 — Robustness & Certification (3 Papers)

---

## 14. Sinha et al. (2018) — "Certifiable Distributional Robustness"
**URL**: https://arxiv.org/abs/1710.10571
**Venue**: ICLR 2018

### Key Contributions
- **Wasserstein DRO Certificate**: Provable upper bound on worst-case loss.
- **Robustness radius rho**: Maximum distributional shift under which guarantees hold.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **Conceptual** | Conceptually informs worst-category awareness in cross-category analysis |
| **Future** | Full Wasserstein DRO certificates require n >> 30 |

---

## 15. Peters et al. (2016) — "Causal Inference by Using Invariant Prediction"
**URL**: https://doi.org/10.1111/rssb.12167
**Venue**: JRSS-B 78(5)

### Key Contributions
- **Invariance Testing**: Causal mechanisms are stable across environments.
- **Cross-environment validation**: Test coefficient equality (Chow test) and residual equality (KS test).

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-05** | Fault categories as environments in invariance framework |
| **H-05** | Reinforces Levene's test (Method 10) |

---

## 16. Basiri et al. (ICSE 2019) — Netflix Chaos Engineering
**URL**: https://arxiv.org/abs/1905.02400
**Venue**: ICSE 2019

### Key Contributions
- **Steady-state hypothesis testing**: H₀ = "system maintains steady-state under fault injection."
- **Sequential testing (SPRT)**: Early termination when significance reached.
- **Power analysis**: Determine required sample size.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-09** | Steady-state concept for CUSUM drift detection |
| **H-07** | SPRT for early accept/reject of SLA compliance |
| **Sample size** | Power analysis informs n≥30 justification |

---

# Part 5 — Supporting Methods & Evaluation (4 Papers)

---

## 17. Ribeiro et al. (ACL 2020) — "CheckList: Behavioral Testing of NLP Models"
**URL**: https://arxiv.org/abs/2005.04118
**Venue**: ACL 2020 (Best Paper)

### Key Contributions
- **Capability-based testing**: MFT, INV, DIR test types.
- **Per-capability profiles**: Single aggregate masks weak areas.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **Conceptual** | Per-capability testing informs per-category analysis (H-03, H-04) |

---

## 18. Scholkopf et al. (Proc. IEEE 2021) — "Toward Causal Representation Learning"
**URL**: https://doi.org/10.1109/JPROC.2021.3058954
**Venue**: Proceedings of the IEEE 109(5)

### Key Contributions
- **Worst-case environment risk**: `max_e E_{P^e}[l(f(X), Y)]`.
- **Transfer risk**: `max_{i,j} |Risk_i - Risk_j|`.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-03** | Transfer risk metric: `max(\|metric_i - metric_j\|)` |
| **H-05** | Mechanism independence reinforces cross-category comparison |

---

## 19. Chen et al. (ACM CSUR 2018) — "Metamorphic Testing: A Review"
**URL**: https://doi.org/10.1145/3143561
**Venue**: ACM Computing Surveys 51(1)

### Framework Application: Cross-category consistency expectations (metamorphic relations).

---

## 20. Google SRE Book (2016) — Site Reliability Engineering
**Authors**: Beyer, B. et al.
**Publisher**: O'Reilly Media

### Key Contributions
- **SLI → SLO → SLA hierarchy**: Service Level Indicators (raw measurement), Objectives (target), Agreements (contract).
- **Error budgets**: Allowed breach count per time window.
- **Burn rate alerting**: Current consumption rate vs sustainable rate.

### Framework Application
| Applied To | Enhancement |
|-----|-------------|
| **H-07** | Error budget tracking: (observed_breach / allowed_breach) × 100% |
| **H-07** | Burn rate computation for SLA margin assessment |
| **H-07** | SLA breach rate hierarchy (SLI/SLO/SLA) |

---

# Cross-Paper Synthesis

## All 16 Statistical Methods and Their Research Grounding

| # | Method | Source Papers | Hypothesis |
|---|--------|--------------|-----------|
| 1 | Wilson CI | Wilson (1927), Brown et al. (2001) | H-01, H-02 |
| 2 | Bootstrap BCa CI | Efron (1987), Agarwal (2021) | H-01, H-06 |
| 3 | IQM (Interquartile Mean) | Agarwal (2021) | H-01 |
| 4 | Shapiro-Wilk | Henderson (2018), Arcuri & Briand (2011) | H-03 |
| 5 | Kruskal-Wallis H | Arcuri & Briand (2011), Henderson (2018), Klees (2018) | H-03 |
| 6 | Mann-Whitney U | Arcuri & Briand (2011), Klees (2018) | H-03 |
| 7 | Vargha-Delaney A₁₂ | Arcuri & Briand (2011) | H-03 |
| 8 | Welch's ANOVA | Henderson (2018) | H-03 |
| 9 | Fisher's Exact Test | Arcuri & Briand (2011) | H-04 |
| 10 | Levene's Test + CV | Peters (2016) | H-05 |
| 11 | Wilcoxon Signed-Rank | Wilcoxon (1945) | H-06 |
| 12 | Exact Binomial Test | Clopper & Pearson (1934) | H-07 |
| 13 | TOST | Schuirmann (1987), Lakens (2017) | H-06 |
| 14 | CVaR | Rockafellar & Uryasev (2000), Artzner et al. (1999) | H-08 |
| 15 | Kaplan-Meier | Kaplan & Meier (1958) | H-06 |
| 16 | CUSUM / EWMA | Page (1954), Roberts (1959) | H-09 |

## Hypothesis-to-Paper Mapping

| Hypothesis | Primary Papers | Supporting Papers |
|-----------|---------------|-------------------|
| H-01 | Agarwal (2021), Henderson (2018) | Efron (1987) |
| H-02 | Wilson (1927), Clopper & Pearson (1934) | |
| H-03 | Arcuri & Briand (2011), Klees (2018) | Henderson (2018) |
| H-04 | Fisher (1922), Arcuri & Briand (2011) | |
| H-05 | Peters (2016), Levene (1960) | Scholkopf (2021) |
| H-06 | Wilcoxon (1945), Schuirmann (1987), Kaplan & Meier (1958) | Lakens (2017), Agarwal (2021) |
| H-07 | Clopper & Pearson (1934), Wald (1945) | Google SRE (2016) |
| H-08 | Rockafellar & Uryasev (2000), Artzner et al. (1999) | |
| H-09 | Page (1954), Roberts (1959), Killick & Eckley (2014) | Basiri (2019) |

---

## Literature Gap Validation

Two major AI-for-IT benchmarks were reviewed:
- **AIOpsLab** (Chen et al., 2025, Microsoft Research) — arxiv 2501.06706
- **ITBench** (Jha et al., 2025, IBM Research) — arxiv 2502.05352

**Finding**: Neither paper provides formal statistical testing (no CIs, no hypothesis tests, no effect sizes, no multiple comparison corrections). Both use simple success rate percentages without uncertainty quantification. This confirms that **AgentCert's statistical hypothesis framework fills a genuine gap** in the AI agent evaluation literature.

---

## Priority Roadmap for Future Enhancements

### P1 — Implemented (Current Framework)
All core methods (1-16) and hypotheses (H-01 to H-09) are implemented.

### P2 — Medium Term (Requires n≥30+)
| Enhancement | Source Papers |
|-------------|-------------|
| KS test for distribution equality | Peters (2016), Chen (2018) |
| Performance profiles | Agarwal (2021) |
| Log-rank test for survival comparison | Mantel (1966) |

### P3 — Long Term (Requires n≥100+)
| Enhancement | Source Papers |
|-------------|-------------|
| Wasserstein DRO certificates | Sinha (2018) |
| SPRT early stopping | Wald (1945), Basiri (2019) |
| LHS experiment design | McKay et al. (1979) |
| Score normalization (multi-agent mode) | Agarwal (2021) |

---

*Document generated: 2026-04-15 | 20 papers, 16 methods, 9 hypotheses*
