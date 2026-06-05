# AgentCert Token Optimization Research

> Deep research on reducing GPT-4 token consumption in Phase 0 (Fault Bucketing) and Phase 1 (Metrics Extraction) while maintaining certification accuracy.
> Covers prompt compression, embedding triage, SLMs, RAG, structured output, and prompt optimization techniques.

---

## Quick Reference (Summary View)

![Token Optimization Summary](token_optimization_summary.png)

---

## Detailed Research Report

![Token Optimization Detailed](token_optimization_detailed.png)

---

## Current Baseline (What We Measured)

### Phase 0 — Fault Bucketing

| Component | Tokens/Call | Hot Spot |
|---|---|---|
| System prompt (v2) | ~1,500 | `fault_analyzer/prompt/v2/prompt.yml:1–223` |
| Known Faults block (verbose) | ~500–2,000 | `classifier.py:173–258` |
| Event input + output (verbatim) | ~500–1,500 | `classifier.py:310–311` |
| **Total per trace** | **~18K–40K input** | — |

**Two flags already implemented but disabled by default:**
- `fault_pruning: true` → 84% reduction in faults block
- `cache_enabled: true` → ~50% rebate on system prompt after first batch

### Phase 1 — Metrics Extraction

| Call Type | Calls / 30-span bucket |
|---|---|
| Span identification | 1 |
| Timestamp validation | 0–2 |
| Quantitative batches (6 spans each) | 5 |
| Qualitative batches (same 6 spans again) | **5 — redundant** |
| Combined judge (per reasoning step) | 5+ |
| **Total** | **~20 calls / ~47K input + ~25K output** |

---

## Technique Priority

| # | Technique | Savings | Effort |
|---|---|---|---|
| 1 | Enable `fault_pruning` + `cache_enabled` | 84% faults block + 50% system prompt | Zero — config flip |
| 2 | Drain3 log template extraction | 5–20× on event text | Low (1 day) |
| 3 | Merge quant+qual into single pass | ~40% fewer calls | Medium |
| 4 | Event-driven temporal chunking | 10–50× trace tokens | Medium |
| 5 | FAISS embedding triage | 60–80% fewer LLM calls | Medium (1 week) |
| 6 | LLMLingua-2 pre-compression | 3–10× on LLM input | Medium |
| 7 | DSPy prompt optimization | +10–40pp accuracy | High |
| 8 | Phi-3-mini / Mistral-7B fine-tune | 50× cost reduction | High |

---

## Research Sources

29 arxiv papers reviewed. Key references:

- **LLMLingua-2** — `2403.12968` (Microsoft Research, 2024) — token classification compression, 3–10× savings
- **LongLLMLingua** — `2310.06839` (2023) — coarse-to-fine for 32K+ token traces, 5–10×
- **FAISS** — `1702.08734` + SBERT `1908.10084` — embedding triage, 60–80% LLM call reduction
- **Drain3** — `2310.01796` (LILAC) — log template extraction, 5–20× zero accuracy cost
- **DSPy / MIPRO** — `2310.03714`, `2406.11695` (Stanford NLP) — compiled prompts, +10–40pp accuracy
- **Phi-3-mini** — `2404.14219` (Microsoft, 2024) — 3.8B SLM at 94–97% of GPT-4 accuracy
- **Outlines** — `2307.09702` — constrained decoding, 20–80% output token reduction
- **kNN few-shot** — `2101.06804` (UT Austin) — dynamic example selection, +8–15pp
- **LogBERT** — `2103.04475` — 110M BERT for log anomaly, 5–20ms inference
- **Lost in the Middle** — `2307.03172` (Stanford) — relevant events should be at start/end of context
- **RECOMP** — `2310.04408` (CMU/Meta) — query-conditioned compression, 3–6× on retrieved docs
- **HyDE** — `2212.10496` (CMU) — hypothetical doc embeddings, +9–23% retrieval recall

---

## Intermediate Research Files

| File | Contents |
|---|---|
| `01-codebase-analysis.md` | Full token breakdown from codebase exploration |
| `02-external-research.md` | 812-line external paper survey |
| `token-optimization-summary.html` | Visual quick-reference card |
| `token-optimization-detailed.html` | Full detailed report with code sketches |
