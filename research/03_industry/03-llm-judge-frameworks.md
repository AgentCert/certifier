# LLM-as-Judge Cost Optimization: 2024-2026 Vendor & OSS Patterns

Concrete pattern survey across 15 eval frameworks for a 4-phase pipeline (bucket → extract → aggregate → certify).

---

## 1. Arize Phoenix
- **Link:** https://arize.com/docs/phoenix/evaluation/llm-evals
- **Pattern:** "Evals on top of evals" — benchmark judge against 50–200 human-labeled rows. Ship pre-tuned templates (HALLUCINATION, QA_CORRECTNESS, RELEVANCE) calibrated for `gpt-4o-mini`. Token-probability scoring (logits for "yes"/"no") preferred over free-text — 1 token instead of paragraphs and removes parser fragility.
- **AgentCert adopt:** Phase 1/2 golden-set regression harness before swapping models.

## 2. LangSmith / LangChain
- **Link:** https://docs.langchain.com/langsmith/llm-as-judge
- **Pattern:** Reference-free + reference-based split. Few-shot examples in judge prompt = lever. Pairwise evaluators for "directly scoring is difficult but comparing is straightforward" — useful for Phase 2 narrative tie-breaking.

## 3. Patronus AI — Lynx
- **Link:** https://www.patronus.ai/blog/lynx-state-of-the-art-open-source-hallucination-detection-model
- **Pattern:** Open-weights specialized judge. **Lynx-8B beats Claude-3-Sonnet by 8.6%** on HaluBench; Lynx-70B **+8.3% over GPT-4o on PubMedQA**. Run via Ollama/vLLM at ~zero per-token cost.

## 4. Galileo — ChainPoll
- **Link:** https://www.galileo.ai/blog/chainpoll
- **Pattern:** Self-consistency voting — N samples of same cheap judge with CoT, majority. **AUROC 0.781**, +11% vs next-best. Stop polling early once vote margin > threshold.
- **AgentCert adopt:** Phase 2 narrative claims — `gpt-4o-mini` 5× at temp=0.7, drop calls if first 3 agree.

## 5. Braintrust
- **Link:** https://www.braintrust.dev/docs/guides/evals/write
- **Pattern:** Pairwise > pointwise for subjective; pointwise (single call, cheaper) for objective. CI smoke-test: `--first 10` on PRs, full on merge — budget guardrail.

## 6. RAGAS
- **Link:** https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/
- **Pattern:** `FaithfulnesswithHHEM` uses **Vectara HHEM-2.1-Open** — T5-class NLI, CPU/GPU near-zero cost replaces GPT-4 for claim verification. Decompose claims with cheap LLM, verify with NLI.
- **AgentCert adopt:** Phase 1 "is X supported by trace Y?" → NLI first.

## 7. DeepEval — G-Eval
- **Link:** https://deepeval.com/docs/metrics-llm-evals
- **Pattern:** Score normalization via **token logprobs** (weighted sum over score-token probs) — reduces variance enough to stay on smaller model. Auto-generates `evaluation_steps` from one-line `criteria`. `DAGMetric` for deterministic decompositions when eval is a decision tree.

## 8. W&B Weave
- **Link:** https://weave-docs.wandb.ai/guides/core-types/evaluations
- **Pattern:** Async-by-default `Evaluation.evaluate()` runs scorers concurrently. `trials=N` for self-consistency. Trace UI spots expensive scorer on critical path.

## 9. Anthropic — Prompt Caching & Eval Cookbook
- **Links:** https://claude.com/blog/prompt-caching, https://github.com/anthropics/anthropic-cookbook
- **Pattern:** Cache rubric+few-shots as system prompt — cache reads cost **10% of base**; writes 125%; reported up to **90% cost / 85% latency reduction**. Evaluator workloads: 1 long fixed rubric + many short variable inputs = ideal cache shape.
- **AgentCert adopt:** Phase 2/3 — `cache_control: ephemeral` on rubric + 5 calibration examples; Haiku default, escalate to Sonnet on disagreement.

## 10. OpenAI Evals + Batch API
- **Link:** https://platform.openai.com/docs/guides/batch
- **Pattern:** **50% discount** on Chat Completions when submitted as `.jsonl` batch with 24h SLA. Up to 50K requests / 200 MB / file. Combine with `gpt-4o-mini` → ~95% off frontier pricing.
- **AgentCert adopt:** Any aggregation that runs nightly = batch job, not real-time.

## 11. NVIDIA NeMo Evaluator
- **Link:** https://docs.nvidia.com/nemo/microservices/latest/evaluation
- **Pattern:** Microservice with pluggable judge endpoint — run Llama-3.1-8B or Nemotron on your GPU and bill compute not per-token.

## 12. MLflow LLM Evaluate
- **Link:** https://mlflow.org/docs/latest/llms/llm-evaluate
- **Pattern:** `mlflow.evaluate(..., extra_metrics=[mlflow.metrics.genai.faithfulness(model="openai:/gpt-4o-mini")])` — single call evaluates **all metrics over a dataframe**. Pattern: one judge invocation per row across N metrics by stuffing rubric for all N into one prompt with JSON-array spec.

## 13. TruLens / TruEra
- **Link:** https://www.trulens.org/getting_started/core_concepts/feedback_functions
- **Pattern:** "Medium LLMs are the sweet spot." Ships NLI-based groundedness, sentence-level provider that scores each sentence separately.

## 14. Inspect AI (UK AISI)
- **Link:** https://inspect.aisi.org.uk/scorers.html
- **Pattern:** `model_graded_qa()` with multiple grading models + majority voting in one config. Deferred-scoring via `--no-score` lets you generate now, score later with cheaper judge. Mix `match()` / `pattern()` deterministic with `model_graded_*` so LLM is only invoked when regex fails.

## 15. Verdict (Haize Labs)
- **Link:** https://verdict.haizelabs.com/, https://github.com/haizelabs/verdict
- **Pattern:** Declarative `JudgeUnit` → `Layer` → `Pipeline`. "Small judge + verification layer often matches reasoning model performance at lower cost." Built-in debate, hierarchical voting, pairwise.
- **AgentCert adopt:** Model the LLM Council as a Verdict pipeline (k JudgeUnits → MajorityVote Layer → meta-judge Unit).

---

## Bias-Reduction Patterns That Also Save Tokens

1. **Pairwise > pointwise for subjective metrics.** 1 prompt for 2 candidates, removes scale-calibration drift. Swap positions and average for position bias.
2. **Structured output via JSON-schema / token-logprobs, not free-text.** Phoenix + DeepEval score via `P("yes")` vs `P("no")` — 1 token, removes verbosity bias.
3. **Small specialist judge + frontier meta-judge only on disagreement.** Lynx-8B / Haiku first; escalate on variance. eugeneyan.com: "Panel of Diverse Models" — 3 small LLMs beat single GPT-4 at **~1/7 the cost**.
4. **Decompose into atomic claims, verify with NLI.** RAGAS HHEM, TruLens NLI-groundedness — sub-100M-param classifier removes self-preference bias + drops to CPU cost.
5. **Cache the rubric, vary only the input.** Anthropic 10% read price — 29/30 calls = cache hits across runs.

---

## Phase-by-Phase Recommendations

| Phase | Cheap pattern to adopt |
|---|---|
| 0 Bucket | Deterministic regex/heuristic pre-pass (Inspect-style), LLM only on residual events. |
| 1 Extract | `gpt-4o-mini` + Anthropic-style cached rubric; logprob scoring for booleans. |
| 2 Aggregate | LLM Council = Verdict pipeline (3× Haiku + Sonnet meta on disagreement); ChainPoll early-stop; OpenAI Batch (50% off) for nightly. |
| 3 Certify | Pairwise comparison of narrative drafts when 2+ candidates exist; pointwise + golden-set regression otherwise. |
