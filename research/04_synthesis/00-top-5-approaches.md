# AgentCert Token Optimization — Synthesis & Top 5 Approaches

> Cross-referencing 33+ arxiv papers, 20 industry case studies, 15 eval-framework vendor patterns. Mapped to AgentCert Phase 0 (`fault_analyzer/`) and Phase 1 (`metrics_extractor/`).

## Current baseline (measured)

**Phase 0 — Fault Bucketing**
- System prompt v2: ~1,500 tok (`fault_analyzer/prompt/v2/prompt.yml`)
- Known Faults block (verbose): 500–2,000 tok (`classifier.py:173–258`)
- Event input + output verbatim: 500–1,500 tok
- **Total: ~18K–40K input per trace**

**Phase 1 — Metrics Extraction (per 30-span bucket)**
- 1 span identification + 0–2 timestamp validation
- 5 quantitative batches + **5 redundant qualitative batches** (same 6 spans re-sent)
- 5+ combined-judge calls per reasoning step
- **Total: ~20 calls / ~47K input + ~25K output per bucket**

**Target:** 5–10× cost reduction, no certification accuracy loss.

---

## Approach 1 — Deterministic First, LLM Last (OTel + Drain3 + Regex Cascade)
**Saves 60–80% of Phase 0 calls and 40% of Phase 1 calls.** Lowest risk.

**Why this works.** Litmus and Chaos Mesh already emit OpenTelemetry attributes (`k8s.event.reason`, `chaosUID`, `phase`) — these *are* the lifecycle bucket labels. Langfuse stores them. AWS CloudWatch Logs anomaly-detection pattern: LLM only on residue. Inspect AI: `match()` first, `model_graded_qa()` only if regex fails. Drain3/LILAC: 5–20× compression with no accuracy loss on log templates.

**Concrete changes.**
1. Add `fault_analyzer/preprocessor.py` that walks the raw trace, lifts OTel attributes (`chaosUID`, `experimentName`, `phase`, timestamps) into a `pre_bucket` dict. Spans already labelled by Litmus/Chaos Mesh skip the LLM entirely.
2. Run **Drain3** over span names + log messages → template IDs. Identical templates collapse to one exemplar before LLM call.
3. Phase 1 timestamps (`start_time`, `end_time`, TTD, TTR) derived from OTel attributes, not LLM extraction. LLM only for *qualitative* fields (root-cause narrative, severity rationale).

**Research grounding.**
- OpenTelemetry Semantic Conventions for k8s events (CNCF)
- Drain3 / LILAC — arxiv [2310.01796](https://arxiv.org/abs/2310.01796)
- LogParser-LLM — arxiv [2408.13727](https://arxiv.org/abs/2408.13727)
- AWS CloudWatch Logs Anomaly Detection (re:Invent 2023)
- MonitorAssistant (Huawei, FSE'24)

**Effort:** 2–3 days. **Risk:** very low (LLM remains fallback).

---

## Approach 2 — Cascade Router with Cheap-First, Frontier-on-Disagreement
**Saves 50–70% of LLM dollars across both phases.** Universally adopted by Uber, GitHub Copilot, Gemini, AutoGen.

**Why this works.** FrugalGPT, RouteLLM, Hybrid LLM, MonitorAssistant all show: send everything to a cheap model first; escalate only the residue where the cheap model is uncertain. Gemini Flash is ~1/10 of Pro; Haiku ~1/7 of Sonnet. Anthropic's "Panel of Diverse Models" — 3 small judges beat single GPT-4 at ~1/7 cost.

**Concrete changes.**
1. Wrap `AzureLLMClient` in a `RoutingLLMClient` with a `tier` arg (`cheap`/`flagship`/`reasoning`). Routes Phase 0 bucketing → Haiku/Phi-3-mini; Phase 1 quant → Haiku; Phase 1 qual → Sonnet only if Haiku log-prob confidence < threshold.
2. Implement **confidence-based escalation**: cheap model returns `{label, confidence_logprob}`. If `confidence < 0.85` *or* `schema_validation_failed`, retry on flagship.
3. Add `cost_per_call` to `metrics_extractor` output for the same closed-loop telemetry New Relic ships.

**Research grounding.**
- FrugalGPT — arxiv [2305.05176](https://arxiv.org/abs/2305.05176)
- RouteLLM — arxiv [2406.18665](https://arxiv.org/abs/2406.18665)
- Hybrid LLM — arxiv [2404.14618](https://arxiv.org/abs/2404.14618)
- Uber GenAI Gateway (Uber Blog 2024)
- GitHub Copilot multi-model choice (Oct 2024)

**Effort:** 1 week. **Risk:** medium (need calibration set per phase).

---

## Approach 3 — Aggressive Prompt Caching + Batch API + Schema-Constrained Output
**Saves ~85% on Phase 1 input tokens and 50% on the nightly Phase 2 batch.** Almost free.

**Why this works.** Anthropic prompt-caching: cache reads = **10% of base price**, reported **90% cost / 85% latency reduction** on cached prefixes. OpenAI Batch API = **50% discount** for 24h SLA. XGrammar/SGLang constrained decoding eliminates JSON-retry waste. AgentCert's Phase 0 system prompt + schema header is fixed per run — perfect cache candidate.

**Concrete changes.**
1. Restructure all Phase 0/1 prompts so the *fixed* prefix (system, schema, few-shots) comes first. Add `cache_control: ephemeral` (Anthropic) or `cached_tokens` annotation (Azure OpenAI with prompt caching).
2. Phase 2 aggregation and Phase 3 narrative builders that run **per run, not per request** → submit as **OpenAI Batch jobs** (50% off).
3. Replace free-text JSON parsing with **Outlines / XGrammar / SGLang** constrained decoding — no more retries on malformed JSON, ~20–80% output-token reduction.
4. Enable the existing `fault_pruning: true` + `cache_enabled: true` flags (zero-line config flip; already measured 84% reduction on faults block).

**Research grounding.**
- Anthropic Prompt Caching announcement (Aug 2024)
- OpenAI Batch API docs (50% discount)
- XGrammar — arxiv [2411.15100](https://arxiv.org/abs/2411.15100)
- SGLang — arxiv [2312.07104](https://arxiv.org/abs/2312.07104)
- Outlines — arxiv [2307.09702](https://arxiv.org/abs/2307.09702)

**Effort:** 1–2 days. **Risk:** very low (no model change).

---

## Approach 4 — Merge Quant+Qual Pass + Chain-of-Draft + ChainPoll Early-Stop
**Saves ~40% of Phase 1 calls and ~90% of completion tokens.** Highest leverage on output cost.

**Why this works.** Phase 1 currently sends *the same 6 spans twice* (quant pass, then qual pass). Chain-of-Draft (Zoom Communications, 2025) shows reasoning at **7.6% of CoT tokens with equal accuracy**. ChainPoll (Galileo) self-consistency early-stop = drop the remaining N samples once 3-of-5 agree. Batch Prompting (Cheng et al.) coalesces multiple inputs into one call for 5× reduction.

**Concrete changes.**
1. Merge `quantitative_batch_extractor` and `qualitative_batch_extractor` into a single prompt that emits a JSON object with both sections (`{"quant": {...}, "qual": {...}}`).
2. Replace any multi-step CoT in `combined_judge.py` with Chain-of-Draft: "think in ≤5-word drafts before answering."
3. Add **early-stop ChainPoll**: for Phase 2 LLM Council, run k=5 cheap judges; if 3 agree in the first 3 samples, drop calls 4–5. Otherwise continue.
4. Batch 3–5 buckets per prompt where they share the same fault label (Batch Prompting).

**Research grounding.**
- Chain of Draft — arxiv [2502.18600](https://arxiv.org/abs/2502.18600)
- Skeleton-of-Thought — arxiv [2307.15337](https://arxiv.org/abs/2307.15337)
- Galileo ChainPoll — galileo.ai/blog/chainpoll
- Batch Prompting — arxiv [2301.08721](https://arxiv.org/abs/2301.08721)
- Adaptive Self-Consistency — arxiv [2305.11860](https://arxiv.org/abs/2305.11860)

**Effort:** 1 week. **Risk:** medium (need regression harness on golden buckets).

---

## Approach 5 — Distilled Small-Model Extractor + NLI Verifier (long-term reimplementation)
**Saves 90%+ on steady-state cost.** This is the redesign path.

**Why this works.** AWS Bedrock Distillation: teacher (Claude/Llama-405B) → student → **75% cheaper, 500% faster, <2% accuracy delta** on classification/summarization. Meta Self-Taught Evaluator: Llama-3-70B judge matches GPT-4 on RewardBench without human labels. RAGAS HHEM / Patronus Lynx-8B: open-weights judges beat Claude-Sonnet by 8.6% on hallucination at near-zero cost. Each shipped Phase-1 output is *already labelled training data*.

**Concrete changes (phased).**
1. **Now:** start logging every Phase 0/1 input + GPT-4 output to a `distillation_corpus/` (already half-done in MongoDB).
2. **Month 1:** fine-tune **Phi-3-mini / Mistral-7B** on the bucketing task using LoRA + 4-bit QLoRA. Serve via vLLM/TGI on a single A10G. Target accuracy ≥ GPT-4 on golden set.
3. **Month 2:** replace LLM-Council qualitative judges with **Vectara HHEM-2.1-Open** (NLI, CPU) for atomic-claim verification; flagship LLM only on the meta-synthesis step.
4. **Month 3:** retire `extraction_model` GPT-4 calls — keep `reasoning_model` only for Phase 3 final narrative.

**Research grounding.**
- AWS Bedrock Model Distillation (GA Dec 2024)
- Meta Self-Taught Evaluator — arxiv [2408.02666](https://arxiv.org/abs/2408.02666)
- Patronus Lynx — patronus.ai/blog/lynx
- Phi-3 — arxiv [2404.14219](https://arxiv.org/abs/2404.14219)
- Gisting / ICAE compression — arxiv [2304.08467](https://arxiv.org/abs/2304.08467), [2307.06945](https://arxiv.org/abs/2307.06945)
- Uber Michelangelo (hybrid in-house fine-tunes)
- BloombergGPT (domain pre-training)

**Effort:** 1–3 months. **Risk:** high (training infra, ongoing maintenance), but irreversible cost win.

---

## Combined cost-stack math (if you stack 1+2+3+4)

| Lever | Multiplier on Phase 0+1 tokens |
|---|---|
| Approach 1 (OTel + Drain3 deterministic pre-pass) | 0.3–0.5× |
| Approach 2 (cascade routing, Haiku-first) | 0.3–0.5× |
| Approach 3 (caching + batch + constrained output) | 0.4–0.6× |
| Approach 4 (merge passes + Chain-of-Draft + ChainPoll) | 0.5–0.7× |
| **Compounded** | **0.018–0.105× → ~10–50× total reduction** |

Approach 5 then collapses the residual to in-house inference cost.

---

## Recommendation

Ship in this order:
1. **Week 1:** Approach 3 (caching + batch + constrained output) — almost free, no model change, no calibration risk.
2. **Week 2–3:** Approach 1 (OTel + Drain3) — kills the biggest LLM waste (deterministic data sent through a stochastic model).
3. **Month 1:** Approach 4 (merge passes + Chain-of-Draft + ChainPoll) — needs a golden-set regression harness.
4. **Month 1–2:** Approach 2 (cascade routing) — once telemetry from #1–3 reveals where Haiku is enough.
5. **Quarter 2:** Approach 5 (distillation) — only after you have ≥ 10K labelled traces from #1–4.

---

## References — Full Source Index

See sibling files:
- `02_papers/01-new-arxiv-papers.md` — 17 new arxiv papers not in previous report
- `03_industry/01-company-case-studies.md` — 20 industry sources
- `03_industry/02-chaos-llmops-sources.md` — 33 chaos-eng/LLMOps/RCA sources
- `03_industry/03-llm-judge-frameworks.md` — 15 eval-framework vendor patterns
- `token-optimization-research.md` — original baseline survey (29 papers)
