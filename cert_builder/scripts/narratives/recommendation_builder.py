"""
Phase 3F — Recommendation Enrichment & Consolidation Builder.

Takes existing Phase 2 recommendations, merges cross-category duplicates,
labels each with a classification, enriches descriptions, and discovers
additional recommendations. This is LLM Call 6 of 6 (JSON output).

Input:  Phase 1 + Phase 2 + Phase 3E limitations output.
Output: {"recommendations_enriched": {"items": [...], "source": ..., "model": ..., "tokens_used": ...}}
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from cert_builder.scripts.narratives.llm_client import get_client, call_llm

# ---------------------------------------------------------------------------
# Load prompt config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "recommendation_prompt.yaml"
_CONFIG = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Pydantic models (intermediate — not part of certified report)
# ---------------------------------------------------------------------------

class EnrichedRecommendation(BaseModel):
    """Single enriched recommendation item."""
    index:          int = Field(..., ge=1)
    priority:       Literal["Critical", "High", "Medium", "Low"]
    category:       str = Field(..., min_length=1)
    label:          str | None
    recommendation: str = Field(..., min_length=1)


class RecommendationsEnrichedResponse(BaseModel):
    """Schema enforced on the LLM response via structured output."""
    items: list[EnrichedRecommendation] = Field(..., min_length=6, max_length=10)


class RecommendationsEnriched(BaseModel):
    """Envelope for Call 6 output."""
    items:       list[EnrichedRecommendation] = Field(..., min_length=6, max_length=10)
    source:      Literal["llm", "fallback"] = "llm"
    model:       str | None = None
    tokens_used: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Context assembly (reuse _format_table from phase3e)
# ---------------------------------------------------------------------------

def _format_table(table: dict) -> str:
    """Format a phase2 table (headers + rows) as readable text."""
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    if not headers or not rows:
        return "  (no data)"

    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(val)))

    header_line = "  " + "  ".join(f"{str(h):<{widths[i]}}" for i, h in enumerate(headers))
    separator = "  " + "  ".join("-" * w for w in widths)
    data_lines = []
    for row in rows:
        data_lines.append("  " + "  ".join(f"{str(v):<{widths[i]}}" for i, v in enumerate(row)))

    return "\n".join([header_line, separator] + data_lines)


def _build_recommendations_context(
    phase2: dict, limitations_enriched: dict
) -> tuple[str, str, str]:
    """Build existing recs table, supporting tables, and limitations block."""
    tables = phase2.get("tables", {})

    # Existing recommendations grouped by category
    rec_rows = tables.get("recommendations", {}).get("rows", [])
    by_cat: dict[str, list] = {}
    for row in rec_rows:
        cat = row[3]
        by_cat.setdefault(cat, []).append(row)

    rec_lines = []
    for cat, rows in by_cat.items():
        rec_lines.append(f"  {cat.upper()} ({len(rows)} items):")
        for r in rows:
            rec_lines.append(f"    R{r[0]} [{r[1]:8s}] {r[2]}")
    existing_table = "\n".join(rec_lines) if rec_lines else "  (no recommendations)"

    # Supporting tables
    support_parts = []
    for key, heading in [
        ("detection_rates", "Detection & Rates"),
        ("ttd_stats", "TTD Timing"),
        ("ttm_stats", "TTM Timing"),
        ("safety_summary", "Safety Summary"),
        ("token_usage", "Token Usage"),
        ("action_correctness", "Action Correctness"),
    ]:
        tbl = tables.get(key, {})
        support_parts.append(f"{heading}:\n{_format_table(tbl)}")

    dims = phase2.get("scorecard", {}).get("dimensions", [])
    sc_lines = "\n".join(f"  {d['dimension']:30s} {d['value']}" for d in dims)
    support_parts.append(f"Scorecard Dimensions:\n{sc_lines}")
    supporting_block = "\n\n".join(support_parts)

    # Enriched limitations from Call 5
    lim_items = limitations_enriched.get("items", [])
    lim_lines = []
    for item in lim_items:
        label_str = item.get("label") or "—"
        lim_lines.append(
            f"  L{item['index']} [{item['severity']:6s}] {item['category']:12s} "
            f"<{label_str:14s}> {item['limitation'][:150]}"
        )
    enriched_lim_block = "\n".join(lim_lines) if lim_lines else "  (no limitations)"

    return existing_table, supporting_block, enriched_lim_block


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def _fallback_recommendations(phase2: dict) -> list[dict]:
    """Deterministic merge by keyword grouping."""
    rec_rows = phase2.get("tables", {}).get("recommendations", {}).get("rows", [])
    pri_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

    # Group merge patterns
    items = []

    # R1+R2+R3 (indices 1,2,3 in phase2 = Critical detection across categories)
    det_recs = [r for r in rec_rows if r[1] == "Critical"]
    if det_recs:
        cats = ", ".join(r[3] for r in det_recs)
        items.append({
            "index": 1, "priority": "Critical", "category": "Cross-cutting",
            "label": "Detection",
            "recommendation": f"Improve fault detection algorithms across all categories. Affected: {cats}.",
        })

    # R4+R5+R7 (High latency items)
    lat_recs = [r for r in rec_rows if r[1] == "High" and any(
        w in r[2].lower() for w in ["latency", "time to", "reduce", "optimize detection"]
    )]
    if lat_recs:
        cats = ", ".join(r[3] for r in lat_recs)
        items.append({
            "index": 2, "priority": "High", "category": "Cross-cutting",
            "label": "Latency",
            "recommendation": f"Reduce detection and mitigation latency through optimized workflows. Affected: {cats}.",
        })

    # R6 hallucination (High, Resource)
    halluc_recs = [r for r in rec_rows if "hallucination" in r[2].lower()]
    for r in halluc_recs:
        items.append({
            "index": len(items) + 1, "priority": r[1], "category": r[3],
            "label": "Behavioral",
            "recommendation": r[2],
        })

    # R8+R10 token items (Medium)
    token_recs = [r for r in rec_rows if any(w in r[2].lower() for w in ["token", "output"])]
    if token_recs:
        cats = ", ".join(r[3] for r in token_recs)
        items.append({
            "index": len(items) + 1, "priority": "Medium", "category": "Cross-cutting",
            "label": "Data Quality",
            "recommendation": f"Fix output token instrumentation to capture remediation logs. Affected: {cats}.",
        })

    # R9 behavioral (Medium, Network)
    behav_recs = [r for r in rec_rows if "broaden" in r[2].lower() or "hypothesis" in r[2].lower()]
    for r in behav_recs:
        items.append({
            "index": len(items) + 1, "priority": r[1], "category": r[3],
            "label": "Behavioral",
            "recommendation": r[2],
        })

    # Sort by priority and re-index
    items.sort(key=lambda x: pri_order.get(x["priority"], 9))
    for i, item in enumerate(items, 1):
        item["index"] = i

    # Ensure minimum 6 items — add remaining ungrouped if needed
    used_texts = {it["recommendation"][:50] for it in items}
    for r in rec_rows:
        if len(items) >= 10:
            break
        if r[2][:50] not in used_texts:
            items.append({
                "index": len(items) + 1, "priority": r[1], "category": r[3],
                "label": None, "recommendation": r[2],
            })

    return items[:10]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_recommendations(
    phase1: dict, phase2: dict, limitations_enriched: dict
) -> dict:
    """
    Enrich, merge, and label recommendations.

    Args:
        limitations_enriched: Output from Phase 3E (Call 5).

    Returns:
        {"recommendations_enriched": {"items": [...], "source": ..., "model": ..., "tokens_used": ...}}
    """
    existing_table, supporting_block, enriched_lim_block = _build_recommendations_context(
        phase2, limitations_enriched
    )
    user_prompt = _CONFIG["user_prompt_template"].format(
        existing_recommendations_table=existing_table,
        supporting_tables_block=supporting_block,
        enriched_limitations=enriched_lim_block,
    )

    try:
        client = get_client()
        result = call_llm(
            client,
            _CONFIG["system_prompt"],
            user_prompt,
            response_schema=RecommendationsEnrichedResponse,
        )

        parsed = result["content"]  # already validated Pydantic model

        # Sort by priority and re-index
        pri_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        sorted_items = sorted(parsed.items, key=lambda x: pri_rank.get(x.priority, 9))
        for i, item in enumerate(sorted_items, 1):
            item.index = i

        envelope = RecommendationsEnriched(
            items=sorted_items,
            source="llm",
            model=result.get("model"),
            tokens_used=result.get("tokens_used", 0),
        )

    except Exception as exc:
        print(f"[phase3f] LLM call failed: {exc}")
        print("[phase3f] Using fallback recommendations.")
        envelope = RecommendationsEnriched(
            items=_fallback_recommendations(phase2),
            source="fallback",
        )

    return {"recommendations_enriched": envelope.model_dump(mode="json")}
