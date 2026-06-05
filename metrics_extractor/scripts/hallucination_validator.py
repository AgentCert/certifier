"""
Per-step claim-grounding hallucination validator.

For each agent reasoning step, pair (the tool responses the agent observed)
with (the claims the agent then made) and ask an LLM judge to classify each
distinct claim as GROUNDED / INFERRED / UNGROUNDED / UNGROUNDED_EXTERNAL /
FABRICATED_TOOL_CALL / TRAJECTORY_DEVIATION / NON_OPERATIONAL / IGNORED_ERROR.

The judge prompt lives in `metrics_extractor/prompt/prompts.yml` under the
`hallucination_judge` key. The response is validated against the
`HallucinationJudgeResponse` Pydantic schema in `schema/metrics_model.py`.

Library entry point:
    judge_trace(client, trace_dict, model="gpt-4o")
    -> (hallucination_count, total_response_count, notes, breakdown)

hallucination_count = sum of UNGROUNDED + UNGROUNDED_EXTERNAL +
                      FABRICATED_TOOL_CALL + TRAJECTORY_DEVIATION +
                      IGNORED_ERROR across all steps.
NON_OPERATIONAL is tracked in breakdown but excluded from hallucination_count.

breakdown keys:
    ungrounded_operational, ungrounded_external, fabricated_tool_calls,
    trajectory_deviations, ignored_errors, non_operational
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import yaml

from metrics_extractor.schema.metrics_model import HallucinationJudgeResponse

try:
    from utils.setup_logging import logger
except ImportError:
    logger = logging.getLogger(__name__)


_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompt" / "prompts.yml"
_PROMPTS = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))
_JUDGE_PROMPT: str = _PROMPTS["hallucination_judge"]


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

    Iterates all events; treats any event whose ``output.content`` is non-empty
    as a reasoning step. For each step we capture the tool responses visible
    to the agent at that point (from ``input.messages`` where role == ``tool``)
    and the agent's claim text (``output.content``).
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
            continue  # pure tool-dispatch step, no agent claim to judge

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


def _truncate_content(content: Any, max_chars: int = 2000) -> str:
    if isinstance(content, dict):
        text = json.dumps(content, indent=2)
    else:
        text = str(content)
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


async def _judge_step(client, step: dict, model: str) -> HallucinationJudgeResponse:
    tool_block = "\n".join(
        f"[{r['tool_name']}]: {r['response']}"
        for r in step["tool_responses"]
    ) or "(no tool responses)"

    user_msg = (
        f"TOOL_RESPONSES:\n{tool_block}\n\n"
        f"AGENT_CLAIMS:\n{_truncate_content(step['content'])}"
    )

    try:
        result, _ = await client.with_structured_output(
            model_name=model,
            messages=user_msg,
            output_format=HallucinationJudgeResponse,
            system_prompt=_JUDGE_PROMPT,
            temperature=0.0,
            max_tokens=2000,
        )
    except Exception as exc:
        logger.warning(f"hallucination judge failed for step {step['step_index']}: {exc}")
        return HallucinationJudgeResponse()

    if isinstance(result, HallucinationJudgeResponse):
        return result
    if isinstance(result, dict):
        try:
            return HallucinationJudgeResponse.model_validate(result)
        except Exception:
            return HallucinationJudgeResponse()
    return HallucinationJudgeResponse()


async def judge_trace(
    client,
    trace: dict,
    model: str = "gpt-4o",
    max_concurrency: int = 4,
) -> tuple[int, int, str, dict]:
    """
    Run the per-step claim-grounding judge over a trace.

    Returns:
        (hallucination_count, total_response_count, notes, breakdown) where
            hallucination_count   = UNGROUNDED + UNGROUNDED_EXTERNAL +
                                    FABRICATED_TOOL_CALL + TRAJECTORY_DEVIATION +
                                    IGNORED_ERROR (NON_OPERATIONAL excluded)
            total_response_count  = sum(total_claims) across steps
            notes                 = " | "-joined step summaries from the judge
            breakdown             = per-type counts dict:
                {
                    "ungrounded_operational": int,
                    "ungrounded_external": int,
                    "fabricated_tool_calls": int,
                    "trajectory_deviations": int,
                    "ignored_errors": int,
                    "non_operational": int,
                }

    On failure returns (0, 0, "", {}) and the caller can fall back to bulk LLM count.
    """
    if not isinstance(trace, dict):
        return 0, 0, "", {}

    steps = build_trajectory(trace)
    if not steps:
        return 0, 0, "", {}

    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def _bounded(step: dict) -> HallucinationJudgeResponse:
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
    notes_parts = [r.summary for r in results if r.summary]
    notes = " | ".join(notes_parts) if notes_parts else ""
    return hallucination_count, total_response_count, notes, breakdown
