# Additional Related Work for AgentCert Pipeline Optimization

Survey of 14 NEW arxiv papers (2022-2025) relevant to reducing token/cost in the
two-phase trace-bucketing (Phase 0) and metrics-extraction (Phase 1) pipeline.
Excludes papers already surveyed (LLMLingua-2, LongLLMLingua, LILAC, DSPy/MIPRO,
Phi-3, Outlines, kNN few-shot, LogBERT, Lost-in-Middle, RECOMP, HyDE, SBERT).

---

## 1. Cascading / Router LLM Serving

### 1.1 FrugalGPT — arxiv 2305.05176 (2023)
*FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving
Performance* (Chen, Zaharia, Zou).
Proposes a three-stage cost-reduction stack: prompt adaptation, LLM
approximation, and an LLM **cascade** that escalates to stronger models only
when a query-difficulty scorer says so. Reports **up to 98% cost reduction**
matching GPT-4 quality, or +4% accuracy at parity cost.
*Fit:* AgentCert Phase 1 issues 20+ LLM calls per bucket — many are simple
field extractions (TTD, TTR, token counts) that a Phi-3-mini cascade could
answer, escalating to GPT-4 only on disagreement.

### 1.2 RouteLLM — arxiv 2406.18665 (2024)
*RouteLLM: Learning to Route LLMs with Preference Data* (Ong et al.).
Trains lightweight routers from preference data + data augmentation that pick
strong vs weak LLM per query at inference. Achieves **>2× cost reduction**
without quality loss; routers transfer across model pairs.
*Fit:* a router trained on a small AgentCert eval-set could send "obvious"
bucket boundaries to Phi-3 and only ambiguous fault-overlap regions to GPT-4.

### 1.3 Hybrid LLM — arxiv 2404.14618 (ICLR 2024)
*Hybrid LLM: Cost-Efficient and Quality-Aware Query Routing* (Ding et al.).
A difficulty-aware router that decouples small/large dispatch with an
adjustable quality dial at test time. Reports **40% fewer large-model calls
with no quality drop**.
*Fit:* an explicit knob lets Phase 0 hit a budget target (e.g., "stay under
20K tokens per trace") while preserving bucketing F1.

---

## 2. Token-Efficient Reasoning Alternatives

### 2.1 Chain of Draft — arxiv 2502.18600 (2025)
*Chain of Draft: Thinking Faster by Writing Less* (Xu et al.).
Forces the model to emit terse intermediate scratchpads instead of verbose CoT.
**Matches or beats CoT accuracy using only 7.6% of tokens (~92% reduction).**
*Fit:* Phase 1 qualitative-metric prompts currently let the model "think out
loud"; switching to CoD-style drafts should slash completion tokens with no
accuracy hit.

### 2.2 Skeleton-of-Thought — arxiv 2307.15337 (ICLR 2024)
*Skeleton-of-Thought: Prompting LLMs for Efficient Parallel Generation* (Ning
et al.). Generates an answer skeleton then fills points in parallel, giving
**considerable latency speedups across 12 LLMs** and sometimes better quality.
*Fit:* the Phase 3 Council narrative builders could be cast as SoT — one call
generates section headers, then concurrent fills, reducing wall-clock and
encouraging shorter, structured outputs.

### 2.3 Adaptive-Consistency — arxiv 2305.11860 (EMNLP 2023)
*Let's Sample Step by Step: Adaptive-Consistency for Efficient Reasoning*
(Aggarwal et al.). Replaces fixed-k self-consistency with a lightweight
early-stopping rule. **Up to 7.9× sample-budget reduction with <0.1% accuracy
drop.**
*Fit:* AgentCert's LLM Council (Phase 2) uses fixed k judges + meta-judge.
Adaptive stopping when judges already converge would directly slash judge
calls per metric.

---

## 3. Prompt / Context Compression

### 3.1 Gisting — arxiv 2304.08467 (NeurIPS 2023)
*Learning to Compress Prompts with Gist Tokens* (Mu, Li, Goodman).
Trains an LM to compress repeated prompt prefixes into cacheable "gist"
tokens. **Up to 26× prompt compression, 40% FLOPs reduction, 4.2% wall-time
speedup.**
*Fit:* the long "rules of the fault taxonomy" prefix that prefaces every
Phase 0 batch could be gisted once and reused across all batches of a trace.

### 3.2 In-Context Autoencoder (ICAE) — arxiv 2307.06945 (2023)
*In-context Autoencoder for Context Compression in a Large Language Model*
(Ge et al.). Pretrains an autoencoder that packs long contexts into memory
slots. **4× context compression** on Llama with measurable latency and KV-cache
savings.
*Fit:* the running fault history (already-classified events) carried across
Phase 0 batches is a natural compression target — feed prior buckets as ICAE
slots rather than raw JSON.

### 3.3 Activation Beacon — arxiv 2401.03462 (2024)
*Long Context Compression with Activation Beacon* (Zhang et al.). A plug-in
that compresses K/V activations layer-wise. **2× inference speedup, 8× KV
cache reduction**, extends 4K→128K context.
*Fit:* applicable when AgentCert runs Phase 1 over very long buckets
(multi-hundred event sequences) on a self-hosted model — turns OOM into a
fixed-budget extraction.

---

## 4. Constrained Generation / Serving

### 4.1 XGrammar — arxiv 2411.15100 (MLSys 2025)
*XGrammar: Flexible and Efficient Structured Generation Engine* (Dong, Chen
et al.). Partitions vocab into context-independent/dependent halves to
accelerate CFG-constrained decoding. **Up to 100× speedup** vs prior
constrained decoding, near-zero overhead end-to-end.
*Fit:* Phase 1 metric extraction emits strict JSON schemas — XGrammar
eliminates retries from malformed output and removes the schema-prompt token
overhead currently needed to coax the model.

### 4.2 SGLang — arxiv 2312.07104 (2023)
*SGLang: Efficient Execution of Structured Language Model Programs* (Zheng et
al.). Frontend DSL + runtime with RadixAttention for KV-cache reuse and
compressed FSMs for structured decoding. **Up to 6.4× higher throughput** on
agent/JSON/few-shot workloads.
*Fit:* RadixAttention's prefix sharing matches AgentCert's pattern of many
prompts that share the same long system prompt — a near-free win at the
serving layer.

---

## 5. Log / Trace Parsing (LLM-era)

### 5.1 LogParser-LLM — arxiv 2408.13727 (KDD 2024)
*LogParser-LLM: Advancing Efficient Log Parsing with Large Language Models*
(Zhong et al.). Combines LLM with statistical pre-grouping. **Only 272.5 LLM
calls per dataset on 3.6M-log corpora**, 90.6% grouping F1, 81.1% parsing
accuracy.
*Fit:* a model for Phase 0 — pre-cluster events with Drain-style hashing,
then ask the LLM only on novel templates, cutting trace-level calls by orders
of magnitude.

### 5.2 DivLog — arxiv 2307.09950 (ICSE 2024)
*Prompting for Automatic Log Template Extraction* (Xu et al.). Selects 5
**diverse** in-context examples (not nearest-neighbour) to cover template
space. **98.1% parsing accuracy** on 16 public datasets, training-free.
*Fit:* AgentCert already uses kNN few-shot for Phase 0; switching to DivLog's
diversity-based selection should improve bucketing on novel fault types
without changing prompt budget.

### 5.3 LogPrompt — arxiv 2308.07610 (ICPC 2024)
*Interpretable Online Log Analysis Using Large Language Models with Prompt
Strategies* (Liu et al.). Bundle of prompt strategies (self-prompt, in-context
prompt, CoT prompt) tuned for log tasks. **+380% over naive prompts, +55.9%
over prior SOTA**, no in-domain training.
*Fit:* gives concrete prompt-engineering recipes for Phase 0 anomaly /
boundary detection that are known to work in online (unseen-template)
settings — directly applicable to new fault taxonomies.

---

## 6. Batch Prompting & Example Selection

### 6.1 Batch Prompting — arxiv 2301.08721 (EMNLP 2023 Industry)
*Batch Prompting: Efficient Inference with Large Language Model APIs* (Cheng,
Kasai, Yu). Processes N samples in a single call. **Cost and latency drop
~inverse-linearly with batch size; up to 5× savings at batch=6** with no
accuracy drop.
*Fit:* Phase 1 issues ~20 calls per bucket — many are independent
field-extraction queries that can be coalesced into a single batched prompt,
likely 3-5× cheaper.

### 6.2 Coverage-based / Set-BSR — arxiv 2305.14907 (EMNLP 2023 Findings)
*Coverage-based Example Selection for In-Context Learning* (Gupta, Gardner,
Singh). Selects a *set* of examples maximising BERTScore-Recall coverage
rather than ranking each independently. **+17 points** on compositional tasks
vs kNN, training-free.
*Fit:* superior replacement for AgentCert's kNN few-shot retriever — same
token budget, materially better on long-tail / compositional fault traces.

---

## 7. Agent / Tool Evaluation Benchmark

### 7.1 tau-bench — arxiv 2406.12045 (2024)
*tau-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains*
(Yao, Shinn, Razavi, Narasimhan). Evaluates agents against domain rules and
introduces a **pass^k consistency metric** (k independent runs must all
succeed). GPT-4o scores <50% pass@1 and <25% pass^8 in retail.
*Fit:* the pass^k metric is directly importable into AgentCert's certification
report — quantifies "does the agent fix this fault *reliably*?" beyond simple
success rate, complementing existing TTD/TTR aggregates.

---

## Summary Table

| # | Paper | ID | Year | Headline Savings | Phase Fit |
|---|---|---|---|---|---|
| 1 | FrugalGPT | 2305.05176 | 2023 | 98% cost | P1 cascade |
| 2 | RouteLLM | 2406.18665 | 2024 | >2× cost | P0/P1 router |
| 3 | Hybrid LLM | 2404.14618 | 2024 | 40% calls | P0 budget |
| 4 | Chain of Draft | 2502.18600 | 2025 | 92% tokens | P1 CoT swap |
| 5 | Skeleton-of-Thought | 2307.15337 | 2023 | latency | P3 narrative |
| 6 | Adaptive Consistency | 2305.11860 | 2023 | 7.9× samples | P2 Council |
| 7 | Gisting | 2304.08467 | 2023 | 26× prefix | P0 prefix |
| 8 | ICAE | 2307.06945 | 2023 | 4× context | P0 history |
| 9 | Activation Beacon | 2401.03462 | 2024 | 8× KV cache | self-host |
| 10 | XGrammar | 2411.15100 | 2024 | 100× decode | P1 JSON |
| 11 | SGLang | 2312.07104 | 2023 | 6.4× tput | serving |
| 12 | LogParser-LLM | 2408.13727 | 2024 | 272 calls/3.6M | P0 |
| 13 | DivLog | 2307.09950 | 2023 | 98% acc | P0 few-shot |
| 14 | LogPrompt | 2308.07610 | 2023 | +56% F1 | P0 prompts |
| 15 | Batch Prompting | 2301.08721 | 2023 | 5× cost | P1 |
| 16 | Coverage Set-BSR | 2305.14907 | 2023 | +17 pts | P0 ICL |
| 17 | tau-bench | 2406.12045 | 2024 | pass^k metric | report |
