"""
Consolidated per-step hallucination + reasoning quality judge.

Single LLM call per reasoning step produces both claim-grounding classifications
AND four-dimension reasoning quality scores.

Library entry point:
    judge_combined(client, trace_dict, model="gpt-4o")
    -> CombinedJudgeResponse

CombinedJudgeResponse exposes:
  Hallucination side:
    hallucination_count     — UNGROUNDED + UNGROUNDED_EXTERNAL + FABRICATED_TOOL_CALL +
                              TRAJECTORY_DEVIATION + IGNORED_ERROR across all steps
    total_response_count    — sum(total_claims) across steps
    hallucination_notes     — joined per-step summaries
    breakdown               — per-type counts dict

  Reasoning side:
    mean_composite, mean_logical_coherence, mean_diagnostic_depth,
    mean_tool_usage_relevance, mean_explanation_clarity
    overall_reasoning_notes — joined per-step reasoning notes
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import yaml

from metrics_extractor.schema.metrics_model import (
    CombinedJudgeResponse,
    CombinedStepJudgment,
)

try:
    from utils.setup_logging import logger
except ImportError:
    logger = logging.getLogger(__name__)


_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompt" / "prompts.yml"
_PROMPTS = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))
_JUDGE_PROMPT: str = _PROMPTS["hallucination_reasoning_judge"]


def _parse(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


def build_trajectory(trace: dict) -> list[dict]:
    """
    Reconstruct reasoning steps from raw trace events.

    Treats any event whose output.content is non-empty as a reasoning step.
    Captures tool responses visible to the agent at that point (role == 'tool'
    in input.messages) and the agent's claim text (output.content).
    """
    events = trace.get("events", [])
    steps: list[dict] = []

    for i, e in enumerate(events):
        out = _parse(e.get("output", {}))
        inp = _parse(e.get("input", {}))

        if not isinstance(out, dict):
            continue

        content = out.get("content", "")
        if not content:
            continue

        tool_responses = []
        if isinstance(inp, dict):
            for msg in inp.get("messages", []):
                if msg.get("role") == "tool":
                    tool_responses.append({
                        "tool_name": msg.get("name", "unknown"),
                        "response": str(msg.get("content", ""))[:1500],
                    })

        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:
                pass

        steps.append({
            "step_index": i,
            "content": content,
            "tool_responses": tool_responses,
        })

    return steps


def _truncate(content: Any, max_chars: int = 2000) -> str:
    if isinstance(content, dict):
        text = json.dumps(content, indent=2)
    else:
        text = str(content)
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


async def _judge_step(client, step: dict, model: str) -> CombinedStepJudgment:
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
            output_format=CombinedStepJudgment,
            system_prompt=_JUDGE_PROMPT,
            temperature=0.0,
            max_tokens=2500,
        )
    except Exception as exc:
        logger.warning(f"Combined judge failed for step {step['step_index']}: {exc}")
        return CombinedStepJudgment(step_index=step["step_index"], tool_usage_relevance=0.5)

    if isinstance(result, CombinedStepJudgment):
        return result
    if isinstance(result, dict):
        try:
            return CombinedStepJudgment.model_validate(result)
        except Exception:
            return CombinedStepJudgment(step_index=step["step_index"], tool_usage_relevance=0.5)
    return CombinedStepJudgment(step_index=step["step_index"], tool_usage_relevance=0.5)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


async def judge_combined(
    client,
    trace: dict,
    model: str = "gpt-4o",
    max_concurrency: int = 4,
) -> CombinedJudgeResponse:
    """
    Run the consolidated hallucination + reasoning judge over a trace.

    Returns a CombinedJudgeResponse with both hallucination and reasoning fields.
    On failure returns a zero-valued response.
    """
    if not isinstance(trace, dict):
        return CombinedJudgeResponse()

    steps = build_trajectory(trace)
    if not steps:
        return CombinedJudgeResponse()

    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def _bounded(step: dict) -> CombinedStepJudgment:
        async with sem:
            return await _judge_step(client, step, model)

    results = await asyncio.gather(*[_bounded(s) for s in steps])

    breakdown = {
        "ungrounded_operational": sum(r.ungrounded_count for r in results),
        "ungrounded_external": sum(r.ungrounded_external_count for r in results),
        "fabricated_tool_calls": sum(r.fabricated_tool_call_count for r in results),
        "trajectory_deviations": sum(r.trajectory_deviation_count for r in results),
        "ignored_errors": sum(r.ignored_error_count for r in results),
        "non_operational": sum(r.non_operational_count for r in results),
    }
    hallucination_count = (
        breakdown["ungrounded_operational"]
        + breakdown["ungrounded_external"]
        + breakdown["fabricated_tool_calls"]
        + breakdown["trajectory_deviations"]
        + breakdown["ignored_errors"]
    )
    total_response_count = sum(r.total_claims for r in results)
    h_notes = " | ".join(r.hallucination_summary for r in results if r.hallucination_summary)

    scored = [r for r in results if r.composite > 0 or r.logical_coherence > 0]
    r_notes = " | ".join(r.reasoning_notes for r in results if r.reasoning_notes)

    response = CombinedJudgeResponse(
        hallucination_count=hallucination_count,
        total_response_count=total_response_count,
        hallucination_notes=h_notes,
        breakdown=breakdown,
        mean_composite=_mean([r.composite for r in scored]),
        mean_logical_coherence=_mean([r.logical_coherence for r in scored]),
        mean_diagnostic_depth=_mean([r.diagnostic_depth for r in scored]),
        mean_tool_usage_relevance=_mean([r.tool_usage_relevance for r in scored]),
        mean_explanation_clarity=_mean([r.explanation_clarity for r in scored]),
        overall_reasoning_notes=r_notes,
    )

    logger.info(
        f"Combined judge: {len(steps)} steps | "
        f"hallucination={hallucination_count}/{total_response_count} "
        f"(ext={breakdown['ungrounded_external']} fab={breakdown['fabricated_tool_calls']} "
        f"traj={breakdown['trajectory_deviations']}) | "
        f"reasoning composite={response.mean_composite:.3f} "
        f"(coh={response.mean_logical_coherence} depth={response.mean_diagnostic_depth} "
        f"tool={response.mean_tool_usage_relevance} clarity={response.mean_explanation_clarity})"
    )
    return response
