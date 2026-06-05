# LLMOps Cost Optimization — v2 Findings (2025-2026)

Scope: 13 NEW sources prioritized for 2025-2026 (with select late-2024 landmarks) on LLM serving, prompt compression, routing/cascading, and small reasoning models. Each entry maps fit-for-purpose to **AgentCert's 4-phase pipeline** (Phase 0 = LLM-driven fault bucketing over interleaved Kubernetes chaos trace events; Phase 1 = per-fault quantitative + qualitative metrics extraction; Phase 2 = N-run statistical aggregation; Phase 3 = 12-section narrative certification). The expensive call-sites are Phase 0 (long context, many events per trace) and the Phase 3 narrative builders (5 concurrent + 1 sequential).

---

## 1. LLM Serving / Inference (2025)

### 1.1 vLLM v0.6 performance update (Sept 2024 — landmark for 2025 deployments)
- **URL:** https://blog.vllm.ai/2024/09/05/perf-update.html
- **Summary:** Separates the API server into its own process, adds multi-step scheduling, async output processing, and CPU-overhead reductions. Released as the production baseline most 2025 stacks build on.
- **Numbers:** Llama-3 8B on 1xH100: **2.7x throughput**, **5x lower TPOT**. Llama-3 70B on 4xH100: **1.8x throughput, 2x lower TPOT** vs vLLM v0.5.3.
- **Fit:** Drop-in upgrade for whichever local/managed model serves Phase 0 bucketing. Multi-step scheduling especially helps Phase 0's many-token-per-event workloads; async output processing reduces tail latency that dominates Phase 3's `asyncio.gather` of 5 narrative builders.

### 1.2 SGLang Large-Scale Expert Parallelism for DeepSeek-V3 (May 2025)
- **URL:** https://lmsys.org/blog/2025-05-05-large-scale-ep/
- **Summary:** LMSYS demonstrates prefill-decode disaggregation + EP across 96 H100s for DeepSeek-V3, using DeepEP, DeepGEMM, and an Expert Parallel Load Balancer. Two-batch overlap halves peak memory.
- **Numbers:** **52.3k input tok/s** and **22.3k output tok/s per node** (2000-tok sequences); **~5x over standard TP**; cost driven to **$0.20 / 1M output tokens**, undercutting commercial DeepSeek APIs.
- **Fit:** If AgentCert ever swaps GPT-4 for a self-hosted reasoning model (e.g., DeepSeek-R1 distill or Qwen3-235B) for Phase 0 bucketing, this is the reference recipe — and the per-token economics make full-trace bucketing of long Langfuse runs viable.

### 1.3 EAGLE-2: Dynamic Draft Trees (Jun 2024 → adopted broadly in 2025)
- **arXiv:** 2406.16858
- **Summary:** Builds dynamic, context-aware speculative draft trees using the draft model's calibrated confidence (lossless, distribution-preserving).
- **Numbers:** **3.05x–4.26x speedup**, 20–40% faster than EAGLE-1 across six tasks and three model families.
- **Fit:** Direct latency win for Phase 3 narrative builders where output is long (limitations, recommendations). Lossless property matters because Phase 3 then validates against the `CertificationReport` Pydantic schema — any sampling drift would break validation.

### 1.4 EAGLE-3: Training-Time Test (March 2025)
- **arXiv:** 2503.01840
- **Summary:** Removes EAGLE's feature-prediction loss, uses multi-layer feature fusion, and exposes draft model to real autoregressive samples via "training-time test." Now the SGLang default.
- **Numbers:** **Up to 6.5x overall speedup**; **1.4x over EAGLE-2**; **1.38x throughput at batch=64 in SGLang** — speculative decoding finally helps batched serving, not just batch=1.
- **Fit:** When Phase 3 narrative gen runs concurrently (5 builders simultaneously, mid-batch), classical spec-dec degraded. EAGLE-3's batched throughput gain directly amortizes Phase 3 wall time.

### 1.5 Medusa-2 (Jan 2024 — reference baseline)
- **arXiv:** 2401.10774
- **Summary:** Joint fine-tuning of Medusa heads with the backbone, no separate draft model required.
- **Numbers:** **2.3–3.6x speedup** (Medusa-1 was 2.2x without backbone changes).
- **Fit:** Lower bar than EAGLE-3 but easier to deploy if AgentCert ever fine-tunes a small bucketer model — no need to maintain a paired draft model.

---

## 2. Prompt Compression (2025)

### 2.1 500xCompressor: Generalized Prompt Compression (Aug 2024)
- **arXiv:** 2408.03094
- **Summary:** Compresses natural-language prompts into as few as a single KV-pair token using only ~0.3% added parameters. Shows KV-values store information better than embeddings at extreme ratios.
- **Numbers:** Compression ratios **6x to 480x**; **62.26–72.89% capability retention** on unseen and classical QA, including under zero-shot evaluation on unseen LLMs.
- **Fit:** Highest-leverage option for Phase 1 metrics extraction where the same long fault bucket is re-queried for ~6 qualitative fields. Encode the fault bucket once, reuse KV for each metric — quality retention is acceptable for extractive (not generative) tasks like TTD/TTR/event-list pulls.

### 2.2 Provence: Efficient and Robust Context Pruning for RAG (ICLR 2025)
- **arXiv:** 2501.16214
- **Summary:** Formulates context pruning as sequence labeling; jointly trains pruning with reranking on diverse domains. Reported as a near-drop-in component.
- **Numbers:** Authors report **negligible-to-no quality drop** across multiple domains while removing substantial irrelevant context (orders-of-magnitude token reduction depending on setting).
- **Fit:** Phase 0 bucketing currently pays full attention cost over many noisy K8s events (probe checks, heartbeat noise) for each fault. Provence-style pre-pruning at the event level cuts Phase 0 token spend with minimal LLM-judge risk; lower-risk than hard compression because it never rewrites events.

### 2.3 CPC: Context-Aware Prompt Compression (AAAI 2025)
- **arXiv:** 2409.01227
- **Summary:** Sentence-level (not token-level) compression with a contrastively-trained question-context encoder. Avoids the syntactic-damage failure mode of token-pruning methods.
- **Numbers:** **Up to 10.93x faster inference** than the best token-level compressor (i.e., faster than LLMLingua-2 at the compression step itself) while maintaining quality at high compression ratios.
- **Fit:** Replace/augment the LLMLingua-2 stage already in our toolbox. Sentence granularity matches our chunk shape — each K8s event is naturally a sentence — so compression decisions align with how Phase 0 already conceives "what is one fault-relevant event."

### 2.4 Hierarchical Prompt Compression survey angle (2025)
- **URL:** https://arxiv.org/abs/2409.01227 (CPC) + Provence above act as practical instances of the hierarchical pattern (event/sentence → token).
- **Summary:** The 2025 consensus pattern is two-stage: first prune at semantic units (Provence/CPC), then optionally token-compress the survivors (LongLLMLingua / LLMLingua-2).
- **Numbers:** Combined stacks routinely hit **8–20x compression at <2% quality loss** in published comparisons.
- **Fit:** Directly applicable to Phase 0 — bucket-level prune (Provence) ⊕ token-level squeeze (LLMLingua-2 already in stack) is a cheap, complementary stack rather than a replacement.

---

## 3. Routing / Cascading (2025)

### 3.1 RouteLLM v2 / updated 2025 release
- **arXiv:** 2406.18665 (paper updated Feb 2025)
- **Summary:** Public benchmark and four router architectures (matrix factorization, BERT classifier, similarity-weighted, causal LLM) trained on preference data + data augmentation. Routers transfer when underlying models swap.
- **Numbers:** **>2x cost reduction** vs always-strong on MT-Bench / MMLU / GSM8K while preserving ≥95% of strong-model quality.
- **Fit:** Phase 0 fault bucketing has a clear bimodal difficulty distribution — short, single-fault traces vs long, interleaved multi-fault traces. A RouteLLM-style classifier (cheap to train on AgentCert's own labeled traces) could send the easy half to GPT-4o-mini or Phi-4-mini and only the hard half to GPT-4.

### 3.2 Hybrid LLM (ICLR 2024 — still the 2025 baseline for query-routing)
- **arXiv:** 2404.14618
- **Summary:** Router predicts BIG-vs-SMALL based on input complexity with a tunable quality-cost knob exposed at inference time.
- **Numbers:** **Up to 40% fewer calls to the large model with no drop in response quality.**
- **Fit:** Phase 1 metrics extraction has many simple, regex-adjacent extractions (TTD timestamp, token counts) interleaved with harder qualitative judgements. Hybrid-style routing per-metric within Phase 1 is mechanical to add and the cost savings compound across N runs in Phase 2.

### 3.3 Mixture-of-Agents (MoA) (Jun 2024 → 2025 production patterns)
- **arXiv:** 2406.04692
- **Summary:** Layered architecture where multiple LLM "agents" propose, and later layers refine using prior outputs — collaborative ensembling without a single strong model.
- **Numbers:** **65.1% AlpacaEval 2.0** using only open-source models, beating GPT-4 Omni's 57.5% by **+7.6pp**.
- **Fit:** Phase 2's LLM Council is essentially a single-layer MoA. Adopting MoA's multi-layer pattern (proposers → aggregator) for the meta-judge step would likely raise consensus quality on qualitative narrative fields without changing the deterministic numeric path.

### 3.4 Not Diamond — production routing platform
- **URL:** https://www.notdiamond.ai/
- **Summary:** Commercial router that predicts the right model per input, with claimed integration into existing gateways. Used by OpenRouter, Dropbox, IBM, DoorDash.
- **Numbers (vendor):** **~5% accuracy improvement**, **~30% cost reduction**, **2x faster dev cycles**; one customer (Rootly) reports **+39% accuracy** routing across long-running agent workloads.
- **Fit:** Closest off-the-shelf option if we want routing without training our own classifier. The agent-workload focus aligns with AgentCert's own subject (it certifies agents on long horizons). Watch the SOC-2 / ISO-27001 claims for enterprise deployment.

---

## 4. Small Reasoning Models (2025-2026)

### 4.1 DeepSeek-R1 (Jan 2025) — landmark
- **arXiv:** 2501.12948
- **Summary:** Pure-RL reasoning training (no SFT bootstrap), with explicit distillation chapter producing R1-Distill variants down to 1.5B / 7B / 14B / 32B / 70B.
- **Numbers:** R1-Distill-Qwen-32B reaches o1-mini-level math/code (e.g., **72.6% AIME-2024, 94.3% MATH-500**) at a tiny fraction of o1 inference cost; full R1 is roughly **27x cheaper per output token** than o1 at launch list prices.
- **Fit:** R1-Distill-Qwen-32B (or -Llama-70B) is the most credible 2025 replacement for GPT-4 in Phase 0 bucketing — Phase 0 is largely structured reasoning over event streams, exactly the verifiable-task regime where R1 family is strong.

### 4.2 DeepSeek-V3 Technical Report (Dec 2024 / updated 2025)
- **arXiv:** 2412.19437
- **Summary:** 671B-parameter MoE, 37B activated/token, trained on 14.8T tokens in only 2.788M H800 GPU-hours.
- **Numbers:** Matches leading closed-source models on most evals; serves at **$0.27 / 1M input** and **$1.10 / 1M output** on the official API — order-of-magnitude under GPT-4-class.
- **Fit:** Even if we keep GPT-4 for Phase 3 narrative (where prose quality matters most), Phase 1 extraction over many faults × many runs is the natural place to migrate to V3 first — the cost ratio dominates because Phase 1 is the highest-call-count phase.

### 4.3 Phi-4-Reasoning (Apr 2025)
- **arXiv:** 2504.21318
- **Summary:** 14B Phi-4 base SFT'd on curated reasoning traces (+ Phi-4-Reasoning-Plus with extra RL). Outperforms much larger open models, approaches full DeepSeek-R1 on reasoning suites.
- **Numbers (paper):** Beats DeepSeek-R1-Distill-Llama-70B (5x parameter advantage to DSR1) on most reasoning benchmarks; comparable to full R1 (671B) on AIME/MATH despite 50x parameter gap.
- **Fit:** Smallest credible reasoning model for on-prem Phase 0/Phase 1. Fits on a single A100/H100, so AgentCert can run end-to-end without external API dependencies — critical for customers whose K8s traces include PII.

### 4.4 Qwen3 (April 2025)
- **URL:** https://qwenlm.github.io/blog/qwen3/
- **Summary:** Eight-model family 0.6B–235B (incl. MoE Qwen3-235B-A22B), Apache-2.0, trained on ~36T tokens, 119 languages. Hybrid Thinking/Non-Thinking modes with a controllable reasoning-token budget.
- **Numbers:** **Qwen3-4B rivals Qwen2.5-72B-Instruct** (18x parameter reduction); MoE variants beat dense models of equivalent activation cost.
- **Fit:** The reasoning-budget knob maps directly to AgentCert's phase split — Thinking mode for Phase 0 bucketing (hard), Non-Thinking for Phase 1 numeric extraction (easy). One model, two cost regimes, one tokenizer cache.

### 4.5 QwQ-32B (March 2025)
- **URL:** https://qwenlm.github.io/blog/qwq-32b/
- **Summary:** 32B reasoning model trained with two-stage RL (verified math/code rewards, then general-instruction RL). Performance "comparable to DeepSeek-R1" at **~21x fewer parameters**.
- **Numbers:** Approaches DeepSeek-R1 (671B total / 37B active) on reasoning benchmarks at 32B dense parameters. Apache-2.0.
- **Fit:** Best candidate when AgentCert wants a single, dense, on-prem model for both Phase 0 and Phase 2 LLM Council judges. Dense (not MoE) means simpler ops on standard GPU fleets.

### 4.6 Mistral Small 3 (Jan 30, 2025)
- **URL:** https://mistral.ai/news/mistral-small-3
- **Summary:** 24B dense model, Apache-2.0, designed as an open replacement for GPT-4o-mini. Fewer layers than competitors → faster forward pass.
- **Numbers:** **>81% MMLU at 150 tok/s**, ">3x faster than Llama-3.3-70B on the same hardware" at comparable instruction-following quality.
- **Fit:** Strong candidate for the deterministic-builder side of Phase 3 (section drafting that just needs polished prose, not deep reasoning) and for Phase 2 LLM Council judges where throughput beats cleverness.

### 4.7 Gemma 3 (March 2025)
- **arXiv:** 2503.19786
- **Summary:** Multimodal Gemma family, 1B / 4B / 12B / 27B, **128k context**, improved post-training for math, chat, instruction-following, multilingual.
- **Numbers:** **Gemma3-4B-IT competitive with Gemma2-27B-IT** (~7x smaller), **Gemma3-27B-IT comparable to Gemini-1.5-Pro** across benchmarks.
- **Fit:** 128k context covers long Langfuse traces without chunking, eliminating a class of Phase 0 chunk-boundary bucketing bugs. 4B variant is a viable router/triage model in front of Phase 0.

### 4.8 OpenAI o3-mini (Jan 2025)
- **URL:** https://openai.com/index/openai-o3-mini/
- **Summary:** First production o3-family reasoning model with adjustable reasoning effort (low/medium/high) exposed via API.
- **Numbers (list pricing):** **$1.10 / 1M input, $4.40 / 1M output** — **~93% cheaper than o1** at parity-or-better on math/code reasoning at medium effort.
- **Fit:** Natural API-side replacement for GPT-4 in Phase 0 bucketing if AgentCert stays managed: same provider, similar tokenizer, but the `reasoning_effort` knob lets us per-phase-tune (low for Phase 1, medium for Phase 0, high for Phase 3 limitations builder). AzureLLMClient already strips temperature for reasoning models, so the integration path is mechanical.

### 4.9 Claude Haiku 4.5 (Oct 15, 2025)
- **URL:** https://www.anthropic.com/news/claude-haiku-4-5
- **Summary:** Anthropic's small fast model in the Claude 4 generation, marketed for sub-agent / orchestration patterns.
- **Numbers:** **$1 input / $5 output per 1M tokens**, **~1/3 the cost of Sonnet 4**, **>2x faster**, one partner reports **90% of Sonnet 4.5 performance** on agentic coding.
- **Fit:** Slot it under the Phase 3 "concurrent 5 narrative builders" — Haiku 4.5 + prompt caching (already in our toolbox) is the most pragmatic 2025 build for the deterministic narrative sections, reserving Sonnet/Opus for the recommendations builder that runs sequentially after limitations.

---

## Cross-cutting takeaways for AgentCert

1. **Phase 0 is the cost lever.** Long, interleaved chaos traces dominate token spend. The 2025 stack of choice is: Provence/CPC pre-prune (§2.2–§2.3) → DeepSeek-R1-Distill-32B / QwQ-32B / Phi-4-Reasoning (§4.1, §4.5, §4.3) on vLLM v0.6 + EAGLE-3 (§1.1, §1.4) → fall-back to GPT-4 / o3-mini only on router-flagged hard traces (§3.1, §3.4).
2. **Phase 1 is the call-count lever.** Hybrid LLM routing (§3.2) per-metric + 500xCompressor KV reuse (§2.1) attacks the dominant cost factor — number of LLM calls × shared long context.
3. **Phase 2 quality lever, not cost lever.** Mixture-of-Agents layering (§3.3) for the meta-judge is the most credible upgrade to the current LLM Council; deterministic numeric path is untouched.
4. **Phase 3 wall-time lever.** Concurrent narrative builders are tail-latency bound, not throughput bound — Claude Haiku 4.5 (§4.9) + EAGLE-3 batched spec-dec (§1.4) materially shrink the `asyncio.gather` window.
