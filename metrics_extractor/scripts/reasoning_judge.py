"""
Per-step reasoning quality judge.

For each agent reasoning step, pairs (tool responses the agent observed) with
(the agent's output at that step) and asks an LLM judge to score four dimensions:
  - logical_coherence      : do conclusions follow from tool responses?
  - diagnostic_depth       : does the agent systematically narrow to root cause?
  - tool_usage_relevance   : were the right tools called and their results used?
  - explanation_clarity    : is the output interpretable and well-articulated?

The judge prompt lives in `metrics_extractor/prompt/prompts.yml` under the
`reasoning_judge` key. Each step response is validated against `ReasoningStepScore`.
The aggregated response is validated against `ReasoningJudgeResponse`.

Library entry point:
    judge_reasoning(client, trace_dict, model="gpt-4o") -> ReasoningJudgeResponse

The returned object exposes mean_composite (the replacement for the old LLM-averaged
reasoning_quality_score) plus per-dimension means for richer cert-report breakdowns.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import yaml

from metrics_extractor.schema.metrics_model import ReasoningJudgeResponse, ReasoningStepScore
from metrics_extractor.scripts.hallucination_validator import build_trajectory

try:
    from utils.setup_logging import logger
except ImportError:
    logger = logging.getLogger(__name__)


_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompt" / "prompts.yml"
_PROMPTS = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))
_JUDGE_PROMPT: str = _PROMPTS["reasoning_judge"]


def _truncate(content: Any, max_chars: int = 2000) -> str:
    if isinstance(content, dict):
        text = json.dumps(content, indent=2)
    else:
        text = str(content)
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


async def _judge_step(client, step: dict, model: str) -> ReasoningStepScore | None:
    tool_block = "\n".join(
        f"[{r['tool_name']}]: {r['response']}"
        for r in step["tool_responses"]
    ) or "(no tool responses at this step)"

    user_msg = (
        f"step_index: {step['step_index']}\n\n"
        f"TOOL_RESPONSES:\n{tool_block}\n\n"
        f"AGENT_OUTPUT:\n{_truncate(step['content'])}"
    )

    try:
        result, _ = await client.with_structured_output(
            model_name=model,
            messages=user_msg,
            output_format=ReasoningStepScore,
            system_prompt=_JUDGE_PROMPT,
            temperature=0.0,
            max_tokens=800,
        )
    except Exception as exc:
        logger.warning(f"reasoning judge failed for step {step['step_index']}: {exc}")
        return None

    if isinstance(result, ReasoningStepScore):
        return result
    if isinstance(result, dict):
        try:
            return ReasoningStepScore.model_validate(result)
        except Exception:
            return None
    return None


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


async def judge_reasoning(
    client,
    trace: dict,
    model: str = "gpt-4o",
    max_concurrency: int = 4,
) -> ReasoningJudgeResponse:
    """
    Run the per-step reasoning quality judge over a trace.

    Returns a ReasoningJudgeResponse with:
        mean_composite              — replacement for the old LLM-averaged reasoning_quality_score
        mean_logical_coherence      — mean logical coherence across steps
        mean_diagnostic_depth       — mean diagnostic depth across steps
        mean_tool_usage_relevance   — mean tool usage relevance across steps
        mean_explanation_clarity    — mean explanation clarity across steps
        steps                       — per-step breakdown
        overall_notes               — joined per-step notes

    On failure (no steps / all judge calls fail) returns a zero-valued response.
    """
    if not isinstance(trace, dict):
        return ReasoningJudgeResponse()

    steps = build_trajectory(trace)
    if not steps:
        return ReasoningJudgeResponse()

    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def _bounded(step: dict) -> ReasoningStepScore | None:
        async with sem:
            return await _judge_step(client, step, model)

    raw_results = await asyncio.gather(*[_bounded(s) for s in steps])
    scored = [r for r in raw_results if r is not None]

    if not scored:
        return ReasoningJudgeResponse()

    response = ReasoningJudgeResponse(
        steps=scored,
        overall_notes=" | ".join(s.notes for s in scored if s.notes),
        mean_logical_coherence=_mean([s.logical_coherence for s in scored]),
        mean_diagnostic_depth=_mean([s.diagnostic_depth for s in scored]),
        mean_tool_usage_relevance=_mean([s.tool_usage_relevance for s in scored]),
        mean_explanation_clarity=_mean([s.explanation_clarity for s in scored]),
        mean_composite=_mean([s.composite for s in scored]),
    )

    logger.info(
        f"Reasoning judge: {len(scored)} steps scored | composite={response.mean_composite:.2f} "
        f"(coherence={response.mean_logical_coherence}, depth={response.mean_diagnostic_depth}, "
        f"tool={response.mean_tool_usage_relevance}, clarity={response.mean_explanation_clarity})"
    )
    return response
