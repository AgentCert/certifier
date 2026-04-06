"""
Phase 3E — Limitation Enrichment & Labeling Builder.

Takes existing Phase 2 limitations, labels each with a classification,
enriches descriptions with specific numbers, and discovers additional
limitations from the data. This is LLM Call 5 of 6 (JSON output).

Input:  Phase 1 parsed context + Phase 2 computed content.
Output: {"limitations_enriched": {"items": [...], "source": ..., "model": ..., "tokens_used": ...}}
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

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "limitation_prompt.yaml"
_CONFIG = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))

_VALID_LABELS = {"Data Quality", "Detection Gap", "Latency", "Coverage Gap", "Behavioral"}


# ---------------------------------------------------------------------------
# Pydantic models (intermediate — not part of certified report)
# ---------------------------------------------------------------------------

class EnrichedLimitation(BaseModel):
    """Single enriched limitation item."""
    index:      int = Field(..., ge=1)
    severity:   Literal["High", "Medium", "Low"]
    category:   str = Field(..., min_length=1)
    label:      str | None
    frequency:  str = Field(..., min_length=1)
    limitation: str = Field(..., min_length=1)


class LimitationsEnrichedResponse(BaseModel):
    """Schema enforced on the LLM response via structured output."""
    items: list[EnrichedLimitation] = Field(..., min_length=10, max_length=13)


class LimitationsEnriched(BaseModel):
    """Envelope for Call 5 output."""
    items:       list[EnrichedLimitation] = Field(..., min_length=10, max_length=13)
    source:      Literal["llm", "fallback"] = "llm"
    model:       str | None = None
    tokens_used: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def _format_table(table: dict) -> str:
    """Format a phase2 table (headers + rows) as readable text."""
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    if not headers or not rows:
        return "  (no data)"

    # Calculate column widths
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


def _build_limitations_context(phase1: dict, phase2: dict) -> tuple[str, str]:
    """Build existing limitations table and supporting tables block."""
    tables = phase2.get("tables", {})

    # Existing limitations
    lim = tables.get("limitations", {})
    lim_lines = []
    for row in lim.get("rows", []):
        idx, text, cat, sev, freq = row[0], row[1], row[2], row[3], row[4]
        lim_lines.append(f"  {idx:>2}  [{sev:6s}]  {cat:12s}  {text}")
    existing_table = "\n".join(lim_lines) if lim_lines else "  (no limitations)"

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

    # Scorecard dimensions
    dims = phase2.get("scorecard", {}).get("dimensions", [])
    sc_lines = "\n".join(f"  {d['dimension']:30s} {d['value']}" for d in dims)
    support_parts.append(f"Scorecard Dimensions:\n{sc_lines}")

    # Per-category derived rates
    cats = phase1.get("categories", [])
    derived_lines = []
    for c in cats:
        d = c["derived"]
        derived_lines.append(
            f"  {c['label']}: det={d['fault_detection_success_rate']*100:.0f}%, "
            f"mit={d['fault_mitigation_success_rate']*100:.0f}%, "
            f"fn={d['false_negative_rate']*100:.0f}%, "
            f"fp={d['false_positive_rate']*100:.0f}%, "
            f"rai={d['rai_compliance_rate']*100:.0f}%, "
            f"sec={d['security_compliance_rate']*100:.0f}%"
        )
    support_parts.append(f"Per-category Derived Rates:\n" + "\n".join(derived_lines))

    # Per-category boolean flags
    bool_lines = []
    for c in cats:
        b = c["boolean"]
        bool_lines.append(
            f"  {c['label']}: PII={'Yes' if b['pii_detection']['any_detected'] else 'No'}, "
            f"Hallucination={'Yes' if b['hallucination_detection']['any_detected'] else 'No'}"
        )
    support_parts.append(f"Per-category Boolean Flags:\n" + "\n".join(bool_lines))

    supporting_block = "\n\n".join(support_parts)
    return existing_table, supporting_block


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def _classify_label(text: str) -> str | None:
    """Deterministic label assignment based on keywords."""
    t = text.lower()
    if any(w in t for w in ["detection", "false negative", "detect"]):
        return "Detection Gap"
    if any(w in t for w in ["ttd", "ttm", "latency", "slow", "time to", "variab"]):
        return "Latency"
    if any(w in t for w in ["token", "instrumentation", "zero output", "zero record"]):
        return "Data Quality"
    if any(w in t for w in ["hallucination", "scope", "diagnostic", "narrow"]):
        return "Behavioral"
    if any(w in t for w in ["n/a", "coverage", "not instrumented"]):
        return "Coverage Gap"
    return None


def _fallback_limitations(phase2: dict) -> list[dict]:
    """Keep original 10 items with deterministic labeling."""
    lim_rows = phase2.get("tables", {}).get("limitations", {}).get("rows", [])
    sev_order = {"High": 0, "Medium": 1, "Low": 2}

    items = []
    for row in lim_rows:
        idx, text, cat, sev, freq = row[0], row[1], row[2], row[3], row[4]
        items.append({
            "index": idx,
            "severity": sev,
            "category": cat,
            "label": _classify_label(text),
            "frequency": f"{freq}/{5} runs" if isinstance(freq, int) else str(freq),
            "limitation": text,
        })

    items.sort(key=lambda x: (sev_order.get(x["severity"], 9), x["index"]))
    for i, item in enumerate(items, 1):
        item["index"] = i

    return items


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_limitations(phase1: dict, phase2: dict) -> dict:
    """
    Enrich and label limitations.

    Returns:
        {"limitations_enriched": {"items": [...], "source": ..., "model": ..., "tokens_used": ...}}
    """
    existing_table, supporting_block = _build_limitations_context(phase1, phase2)
    user_prompt = _CONFIG["user_prompt_template"].format(
        existing_limitations_table=existing_table,
        supporting_tables_block=supporting_block,
    )

    try:
        client = get_client()
        result = call_llm(
            client,
            _CONFIG["system_prompt"],
            user_prompt,
            response_schema=LimitationsEnrichedResponse,
        )

        parsed = result["content"]  # already validated Pydantic model

        # Sort by severity and re-index
        sev_rank = {"High": 0, "Medium": 1, "Low": 2}
        sorted_items = sorted(parsed.items, key=lambda x: sev_rank.get(x.severity, 9))
        for i, item in enumerate(sorted_items, 1):
            item.index = i

        envelope = LimitationsEnriched(
            items=sorted_items,
            source="llm",
            model=result.get("model"),
            tokens_used=result.get("tokens_used", 0),
        )

    except Exception as exc:
        print(f"[phase3e] LLM call failed: {exc}")
        print("[phase3e] Using fallback limitations.")
        envelope = LimitationsEnriched(
            items=_fallback_limitations(phase2),
            source="fallback",
        )

    return {"limitations_enriched": envelope.model_dump(mode="json")}
