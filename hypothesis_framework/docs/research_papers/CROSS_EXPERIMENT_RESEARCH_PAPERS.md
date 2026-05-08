# Cross-Experiment Analysis: Research Paper Summaries
## Why Per-Experiment Testing, Not Pooling — Supporting Literature

Compiled for AgentCert Statistical Hypothesis Framework, Section F2.
10 papers from clinical trials, causal inference, genomics, epidemiology, and ML evaluation.

---

## 1. Simpson's Paradox

### Paper 1: Bickel, Hammel & O'Connell (1975) — UC Berkeley Admissions
- **Title:** Sex Bias in Graduate Admissions: Data from Berkeley
- **Authors:** P.J. Bickel, E.A. Hammel, J.W. O'Connell
- **Journal:** Science, Vol. 187, No. 4175, pp. 398–404
- **DOI:** 10.1126/science.187.4175.398
- **PMID:** 17835295

**Key Findings:**
At the aggregate level, the data showed "a clear but misleading pattern of bias against female applicants." However, when broken down by department, the picture reversed. Department-level analysis revealed "few decision-making units that show statistically significant departures from expected frequencies of female admissions." Remarkably, "about as many units appear to favor women as to favor men."

When data were correctly pooled — accounting for "the autonomy of departmental decision making" — a small but statistically significant bias **in favor of women** emerged, the opposite of what the aggregate numbers suggested.

The authors attributed the misleading aggregate pattern not to discriminatory admissions committees, "which seem quite fair on the whole," but to systemic issues upstream. Women were "shunted by their socialization and education toward fields of graduate study that are generally more crowded, less productive of completed degrees, and less well funded."

**Relevance to AgentCert:** This is the canonical demonstration that naively pooling data across subgroups (departments = experiments) with different confounder distributions (application competitiveness = fault combinations) reverses the direction of the observed effect. Pooling f1 data from Experiment 1 (alongside f2, f3) and Experiment 2 (alongside f5) risks the same reversal.

---

### Paper 2: Pearl (2009) — Causality: Models, Reasoning, and Inference
- **Title:** Causality: Models, Reasoning, and Inference (2nd edition)
- **Authors:** Judea Pearl
- **Publisher:** Cambridge University Press
- **ISBN:** 978-0-521-89560-6
- **DOI:** 10.1017/CBO9780511803161

**Key Concepts:**
Pearl provides the definitive causal explanation of Simpson's Paradox: it arises from conflating observational conditional probabilities P(S|T) with interventional probabilities P(S|do(T)). When confounders exist, these diverge.

Pearl proved that a "causal sure-thing principle" holds: an action raising the probability of an outcome in every subpopulation must raise it overall, provided the action does not change the distribution of the subpopulations. The back-door criterion provides conditions for identifying when conditioning on variables eliminates confounding.

**Relevance to AgentCert:** The fault combination in an experiment is a confounder — correlated with both the treatment (fault injection) and the outcome (agent metrics). Pearl's framework proves that pooling across different experimental contexts conflates observational with interventional probabilities, producing invalid conclusions.

---

### Paper 3: Blyth (1972) — Simpson's Paradox and the Sure-Thing Principle
- **Title:** On Simpson's Paradox and the Sure-Thing Principle
- **Authors:** Colin R. Blyth
- **Journal:** J. American Statistical Association, 67(338): 364-366
- **Year:** 1972

**Key Finding:**
Simpson's Paradox directly challenges Savage's sure-thing principle in decision theory. An action that increases the probability of success in every subpopulation can decrease it in the pooled population, making naive pooling not just misleading but logically dangerous for decision-making.

**Relevance to AgentCert:** An agent that performs better on f1 in *every individual experiment* could appear to perform worse on f1 when data from all experiments is pooled — a logically dangerous reversal for certification decisions.

---

## 2. Batch Effects

### Paper 4: Johnson, Li & Rabinovic (2007) — ComBat Method
- **Title:** Adjusting Batch Effects in Microarray Expression Data Using Empirical Bayes Methods
- **Authors:** W. Evan Johnson, Cheng Li, Ariel Rabinovic
- **Journal:** Biostatistics, 8(1): 118-127
- **DOI:** 10.1093/biostatistics/kxj037
- **PMID:** 16632515

**Key Findings:**
The paper states explicitly: **"it is inappropriate to combine data sets without adjusting for batch effects."** Batch effects are "non-biological experimental variation" that is "commonly observed across multiple batches," making "the task of combining data from these batches difficult."

Existing approaches to filtering batch effects "are often complicated and require large batch sizes (> 25) to implement." The proposed empirical Bayes framework (ComBat) is "robust to outliers in small sample sizes" and "performs comparable to existing methods for large samples."

**Relevance to AgentCert:** Different fault combinations constitute different experimental batches. The explicit statement that combining data without batch adjustment is "inappropriate" directly supports per-experiment testing. Fault co-occurrence effects are the AgentCert analogue of batch effects in genomics.

---

### Paper 5: Leek et al. (2010) — Batch Effects in High-Throughput Data
- **Title:** Tackling the Widespread and Critical Impact of Batch Effects in High-Throughput Data
- **Authors:** Jeffrey T. Leek, Robert B. Scharpf, Hector Corrada Bravo, David Simcha, Benjamin Langmead, W. Evan Johnson, Donald Geman, Keith Baggerly, Rafael A. Irizarry
- **Journal:** Nature Reviews Genetics, 11(10): 733-739
- **DOI:** 10.1038/nrg2825
- **PMID:** 20838408

**Key Findings:**
1. **Normalization is insufficient:** Using a bladder cancer microarray dataset, even after applying RMA (quantile normalization), samples still "perfectly cluster by processing date." Hundreds of genes remained susceptible to batch-driven variation after normalization.

2. **Correlations reverse sign across batches:** "A large percentage of significant correlations reversed signs across batches." When compared with permuted batch labels, "a much smaller fraction of significant correlations change signs" — confirming this is a real batch effect, not noise.

3. **Most dangerous scenario:** Batch effects "occur because measurements are affected by laboratory conditions, reagent lots and personnel differences." They become especially dangerous when they correlate with the biological outcome under study.

4. **Recommended workflow:**
   - Step 1: Exploratory data analysis — visualize potential batch effects
   - Step 2: Adjustment using known batch indicators or surrogate variables
   - Step 3: Diagnostics to verify correction and preservation of biological signal

**Relevance to AgentCert:** The finding that correlations reverse sign across batches is directly analogous to agent metric correlations potentially reversing across experiments with different fault combinations. The recommended workflow (visualize → adjust → verify) maps to the homogeneity pre-test → conditional pooling → meta-analytic combination approach in Section F2.

---

## 3. Ecological Fallacy

### Paper 6: Robinson (1950) — Ecological Correlations and the Behavior of Individuals
- **Title:** Ecological Correlations and the Behavior of Individuals
- **Authors:** William S. Robinson
- **Journal:** American Sociological Review, 15(3): 351-357
- **DOI:** 10.1093/ije/dyn357 (2009 reprint in Int. J. Epidemiology)
- **Year:** 1950

**Key Finding:**
Robinson established the foundational principle that group-level correlations (ecological correlations) cannot be used as approximations for individual-level correlations. Group-level patterns can reverse or disappear at the individual level, making inferences from aggregated data about individual behavior fundamentally invalid. This remains one of the most cited methodology papers in social science.

**Relevance to AgentCert:** Experiment-level aggregate statistics (pooled across experiments) cannot approximate run-level behavior within a specific experiment. Conclusions drawn from pooled data about how the agent behaves in any specific experimental context are fundamentally invalid.

---

### Paper 7: Greenland (2001) — Ecologic versus Individual-Level Sources of Bias
- **Title:** Ecologic versus Individual-Level Sources of Bias in Ecologic Estimates of Contextual Health Effects
- **Authors:** Sander Greenland
- **Journal:** International Journal of Epidemiology, 30(6): 1343-1350
- **DOI:** 10.1093/ije/30.6.1343
- **Year:** 2001

**Key Finding:**
Greenland showed that ecological data provide "only marginal observations on the joint distribution of individually defined confounders and outcomes" and therefore cannot identify contextual or individual-level effects. He noted that "ecologic effect estimates are inevitably used as estimates of individual effects, despite disclaimers," and that bias arises because "social context is not randomized across typical analysis units." He advocates multilevel study designs incorporating both individual and group-level data.

**Relevance to AgentCert:** Pooled experiment data provides only a marginal view of per-experiment agent behavior. The experiment context (fault combination) is not randomized — it is deliberately chosen — making ecological bias a direct risk. The two-level architecture (per-experiment + meta-analysis) aligns with Greenland's advocacy for multilevel designs.

---

## 4. Meta-Analysis Methodology

### Paper 8: DerSimonian & Laird (1986) — Meta-Analysis in Clinical Trials
- **Title:** Meta-Analysis in Clinical Trials
- **Authors:** Rebecca DerSimonian, Nan Laird
- **Journal:** Controlled Clinical Trials, 7(3): 177-188
- **DOI:** 10.1016/0197-2456(86)90046-2
- **PMID:** 3802833
- **Year:** 1986

**Key Findings:**
1. **Heterogeneity assessment gap:** Reviewed eight published meta-analyses and found they "lack consistent assessment of homogeneity of treatment effect before pooling."

2. **Random effects approach:** Proposed a model that "incorporates the heterogeneity of effects in the analysis of the overall treatment efficacy."

3. **Practical estimation:** Introduced "a simple noniterative procedure for characterizing the distribution of treatment effects" — the DerSimonian-Laird (DSL) estimator, which became one of the most widely cited statistical techniques in medical research.

4. **Covariate extension:** The model can "include relevant covariates which would reduce the heterogeneity and allow for more specific therapeutic recommendations."

**Relevance to AgentCert:** The DSL random-effects model is the recommended approach for combining per-experiment estimates in Section F2. The critique that existing reviews "lack consistent assessment of homogeneity before pooling" directly motivates the mandatory homogeneity pre-test (Mann-Whitney U / Kolmogorov-Smirnov) before any data combination.

---

### Paper 9: Higgins & Thompson (2002) — Quantifying Heterogeneity (I² Statistic)
- **Title:** Quantifying Heterogeneity in a Meta-Analysis
- **Authors:** Julian P.T. Higgins, Simon G. Thompson
- **Journal:** Statistics in Medicine, 21(11): 1539-1558
- **DOI:** 10.1002/sim.1186
- **PMID:** 12111919
- **Year:** 2002

**Key Findings:**
1. **Problem:** Existing approaches for assessing heterogeneity depended on the number of studies and the treatment effect metric — not useful for standardized reporting.

2. **Three new measures:**
   - **H** = square root of chi² heterogeneity statistic / degrees of freedom
   - **R** = ratio of SE (random-effects) to SE (fixed-effect)
   - **I²** = "the proportion of total variation in study estimates that is due to heterogeneity" (rather than sampling error)

3. **I² interpretation thresholds** (from Cochrane Handbook):
   | I² Range | Interpretation |
   |----------|---------------|
   | 0–40% | Might not be important |
   | 30–60% | May represent moderate heterogeneity |
   | 50–90% | May represent substantial heterogeneity |
   | 75–100% | Considerable heterogeneity |

4. **Key recommendation:** H and I² are "particularly useful summaries of the impact of heterogeneity" and should be reported "in preference to the test for heterogeneity."

**Relevance to AgentCert:** I² is the gating metric in Section F2's cross-experiment analysis. If I² < 40%, pooling may be acceptable; if I² > 75%, data must be kept separate. This provides an objective, metric-independent criterion for deciding whether shared fault data can be combined.

---

### Paper 10: Borenstein, Hedges, Higgins & Rothstein (2010) — Fixed vs Random Effects
- **Title:** A Basic Introduction to Fixed-Effect and Random-Effects Models for Meta-Analysis
- **Authors:** Michael Borenstein, Larry V. Hedges, Julian P.T. Higgins, Hannah R. Rothstein
- **Journal:** Research Synthesis Methods, 1(2): 97-111
- **DOI:** 10.1002/jrsm.12
- **PMID:** 26061376
- **Year:** 2010

**Key Findings:**
1. **Not interchangeable:** Because both models "employ similar sets of formulas to compute statistics, and sometimes yield similar estimates," researchers may mistakenly assume they are interchangeable. However, "the models represent fundamentally different assumptions about the data."

2. **Fixed-effect:** Assumes all studies share a single true effect. Differences across studies are purely due to sampling error.

3. **Random-effects:** Assumes effects vary across studies. The combined estimate represents the average of a distribution of true effects. Yields wider confidence intervals reflecting between-study variance.

4. **Why selection matters:**
   - Proper model selection is "important to ensure that the various statistics are estimated correctly."
   - The model "provides a framework for the goals of the analysis as well as for the interpretation of the statistics."

**Relevance to AgentCert:** Different experiments with different fault combinations represent different experimental contexts — the assumption that they share a single true effect (fixed-effect) is rarely valid. Random-effects meta-analysis is the appropriate model for combining per-experiment evidence, and it properly reflects the additional uncertainty from between-experiment heterogeneity.

---

## 5. Guidelines and ML Evaluation

### Paper 11: Cochrane Handbook Chapter 10 (Deeks, Higgins et al., 2024)
- **Title:** Chapter 10: Analysing Data and Undertaking Meta-Analyses
- **Source:** Cochrane Handbook for Systematic Reviews of Interventions (version 6.5)
- **Authors:** Jonathan J. Deeks, Julian P.T. Higgins, Douglas G. Altman, Joanne E. McKenzie, Areti Angeliki Veroniki
- **URL:** https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-10

**Key Guidance on When NOT to Pool:**

1. **Mandatory requirement:** "Meta-analysis should only be considered when a group of studies is sufficiently homogeneous in terms of participants, interventions and outcomes."

2. **Direction inconsistency:** When results vary substantially — especially in direction — "it may be misleading to quote an average value for the intervention effect."

3. **Explicit statement:** "A systematic review need not contain any meta-analyses."

4. **Heterogeneity types:**
   - Clinical diversity (participants, interventions, outcomes)
   - Methodological diversity (study design, measurement, bias risk)
   - Statistical heterogeneity (effects differ more than expected by chance)

5. **I² thresholds (deliberately overlapping):**
   - 0–40%: Might not be important
   - 30–60%: May represent moderate heterogeneity
   - 50–90%: May represent substantial heterogeneity
   - 75–100%: Considerable heterogeneity

6. **Fixed vs random effects:**
   - Fixed-effect: "typical intervention effect" — ignores heterogeneity, CIs may be "too narrow"
   - Random-effects: "average intervention effect" — gives "relatively more weight to smaller studies"
   - "The choice between a fixed-effect and a random-effects meta-analysis should never be made on the basis of a statistical test for heterogeneity"

7. **Seven strategies for heterogeneity:** Verify data → forgo meta-analysis → explore heterogeneity → ignore (fixed-effect) → random-effects → reconsider effect measure → exclude outliers

**Relevance to AgentCert:** The Cochrane Handbook is the gold standard for meta-analysis methodology. Its explicit guidance that pooling should only occur when studies are "sufficiently homogeneous" directly supports the two-level architecture. Different fault combinations create clinical diversity (different interventions), making the homogeneity pre-test mandatory.

---

### Paper 12: Demšar (2006) — Statistical Comparisons of Classifiers over Multiple Datasets
- **Title:** Statistical Comparisons of Classifiers over Multiple Data Sets
- **Authors:** Janez Demšar
- **Journal:** Journal of Machine Learning Research, 7(1): 1-30
- **URL:** https://jmlr.org/papers/v7/demsar06a.html
- **Year:** 2006

**Key Findings:**
1. **Problem:** "The issue of statistical tests for comparisons of more algorithms on multiple data sets" had been "all but ignored" despite being critical to ML research.

2. **Recommendations:**
   - **Wilcoxon signed-ranks test** for comparing two classifiers across multiple datasets
   - **Friedman test** with post-hoc Nemenyi test for comparing multiple classifiers
   - **Critical difference (CD) diagrams** for visualization

3. **Why non-parametric:** Advocates for "simple, yet safe and robust non-parametric tests" because assumptions (normality, homogeneity of variance) are often violated in classifier comparison studies.

4. **Why not pool:** The recommended tests operate on **ranks** of classifier performance within each dataset, rather than pooling raw accuracy scores, respecting that different datasets have different scales and difficulty levels.

**Relevance to AgentCert:** This is ML's canonical argument against pooling performance metrics across heterogeneous experimental conditions. The recommended per-dataset ranking approach is directly analogous to per-experiment hypothesis testing in the AgentCert two-level architecture.

---

## Summary: Convergence Across Disciplines

All 12 papers (10 from Section F2 + 2 additional foundational papers) converge on the same conclusion:

| Discipline | Key Paper | Core Message |
|-----------|-----------|--------------|
| **Statistics** | Bickel et al. 1975 | Pooling across heterogeneous subgroups reverses conclusions |
| **Causal Inference** | Pearl 2009 | Pooling conflates observational with interventional probabilities |
| **Decision Theory** | Blyth 1972 | Pooled conclusions can violate the sure-thing principle |
| **Genomics** | Johnson et al. 2007 | "Inappropriate to combine without adjusting for batch effects" |
| **Genomics** | Leek et al. 2010 | Correlations reverse sign across batches |
| **Epidemiology** | Robinson 1950 | Group-level correlations ≠ individual-level correlations |
| **Epidemiology** | Greenland 2001 | Aggregate data provides only marginal observations |
| **Clinical Trials** | DerSimonian & Laird 1986 | Reviews lack heterogeneity assessment before pooling |
| **Meta-Analysis** | Higgins & Thompson 2002 | I² quantifies between-study heterogeneity |
| **Meta-Analysis** | Borenstein et al. 2010 | Fixed and random effects are not interchangeable |
| **Clinical Guidelines** | Cochrane Handbook 2024 | Pool only if sufficiently homogeneous |
| **ML Evaluation** | Demšar 2006 | Per-dataset comparison, not pooled performance |

**Conclusion:** Per-experiment hypothesis testing with meta-analytic combination is the only statistically valid approach when experimental contexts (fault combinations) differ.
