"""
Phase 3E — Limitation Enrichment & Labeling Builder.

Takes existing Phase 2 limitations, labels each with a classification,
enriches descriptions with specific numbers, and discovers additional
limitations from the data. This is LLM Call 5 of 6 (JSON output).

Input:  Phase 1 parsed context + Phase 2 computed content.
Output: {"limitations_enriched": {"items": [...], "source": ..., "model": ..., "tokens_used": ...}}
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from cert_builder.scripts.narratives.llm_client import get_client, call_llm

try:
    from aggregator.scripts.rai_scoring import privacy_security_for_category
except ImportError:
    def privacy_security_for_category(derived):
        d = derived or {}
        def _f(v, default=1.0):
            try: return float(v) if v is not None else default
            except Exception: return default
        return round(_f(d.get("security_compliance_rate")) * _f(d.get("pii_clean_rate")) * _f(d.get("adversarial_clean_rate")), 4)

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
    items:          list[EnrichedLimitation] = Field(..., min_length=10, max_length=13)
    source:         Literal["llm", "fallback"] = "llm"
    model:          str | None = None
    tokens_used:    int = Field(default=0, ge=0)
    input_tokens:   int = Field(default=0, ge=0)
    output_tokens:  int = Field(default=0, ge=0)


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

    # Per-category derived rates — SRE and CISO have different schemas
    cats = phase1.get("categories", [])
    _ciso_shaped = {"ciso_fault"}
    derived_lines = []
    for c in cats:
        d = c.get("derived") or {}
        if c.get("fault_category", "") in _ciso_shaped:
            pass_rate = d.get("ciso_task_pass_rate")
            rate_str = f"{pass_rate*100:.0f}%" if isinstance(pass_rate, (int, float)) else "N/A"
            derived_lines.append(
                f"  {c['label']}: policy_pass_rate={rate_str} "
                f"(CISO compliance — no detection/mitigation timeline)"
            )
        else:
            derived_lines.append(
                f"  {c['label']}: det={d.get('fault_detection_success_rate', 0)*100:.0f}%, "
                f"mit={d.get('fault_mitigation_success_rate', 0)*100:.0f}%, "
                f"fn={d.get('false_negative_rate', 0)*100:.0f}%, "
                f"fp={d.get('false_positive_rate', 0)*100:.0f}%, "
                f"ps_rai={privacy_security_for_category(d)*100:.0f}%, "
                f"fairness_pass={d.get('rai_compliance_rate', 0)*100:.0f}%, "
                f"sec={d.get('security_compliance_rate', 0)*100:.0f}%"
            )
    support_parts.append(f"Per-category Derived Rates:\n" + "\n".join(derived_lines))

    # Per-category boolean flags
    bool_lines = []
    for c in cats:
        b = c["boolean"]
        bool_lines.append(
            f"  {c['label']}: PII={'Yes' if (b.get('pii_detection') or b.get('personal_pii') or {}).get('any_detected') else 'No'}, "
            f"Hallucination={'Yes' if b['hallucination_detection']['any_detected'] else 'No'}"
        )
    support_parts.append(f"Per-category Boolean Flags:\n" + "\n".join(bool_lines))

    # Subfault-level TTD/TTM breakdown
    sf_lines = []
    for c in cats:
        ttd_data = (c.get("numeric") or {}).get("time_to_detect", {})
        ttm_data = (c.get("numeric") or {}).get("time_to_mitigate", {})
        ttd_sf = ttd_data.get("subfault", {})
        ttm_sf = ttm_data.get("subfault", {})
        if ttd_sf or ttm_sf:
            sf_lines.append(f"  {c['label']}:")
            all_sfs = sorted(set(list(ttd_sf.keys()) + list(ttm_sf.keys())))
            for sf in all_sfs:
                td = ttd_sf.get(sf, {})
                tm = ttm_sf.get(sf, {})
                parts = [f"    {sf}:"]
                if td:
                    sla = td.get("sla_seconds")
                    parts.append(f"TTD score={td.get('weighted_score', 'N/A')}, det_rate={td.get('detection_rate', 'N/A')}, n={td.get('n_attempted', 'N/A')}" + (f", SLA={sla}s" if sla else ""))
                if tm:
                    sla = tm.get("sla_seconds")
                    parts.append(f"TTM score={tm.get('weighted_score', 'N/A')}, mit_rate={tm.get('detection_rate', 'N/A')}, n={tm.get('n_attempted', 'N/A')}" + (f", SLA={sla}s" if sla else ""))
                sf_lines.append(" | ".join(parts))
    if sf_lines:
        support_parts.append(f"Subfault TTD/TTM Breakdown:\n" + "\n".join(sf_lines))

    # Sensitive exposure counts with per-category reasoning
    pii_lines = []
    for c in cats:
        pii_data = (c.get("numeric") or {}).get("sensitive_exposure", {})
        notes_val = (c.get("textual") or {}).get("sensitive_data_exposure_notes", "")
        exposure_notes = notes_val.get("consensus_summary", "") if isinstance(notes_val, dict) else (notes_val or "")
        count_str = f"{pii_data['sum']:.0f}" if pii_data and "sum" in pii_data else "N/A"
        line = f"  {c['label']}: {count_str} sensitive exposures"
        if exposure_notes:
            line += f" — {exposure_notes[:180]}"
        pii_lines.append(line)
    if pii_lines:
        support_parts.append(f"Sensitive Data Exposure (per category):\n" + "\n".join(pii_lines))

    # Qualitative assessment summaries
    qual_lines = []
    for c in cats:
        textual = c.get("textual", {})
        if not textual:
            continue
        cat_parts = []
        for key, label in [
            ("rai_check_summary", "RAI"),
            ("overall_response_and_reasoning_quality", "Reasoning"),
            ("security_compliance_summary", "Security"),
        ]:
            entry = textual.get(key, {})
            if isinstance(entry, dict) and entry.get("severity_label"):
                cat_parts.append(f"{label}={entry['severity_label']}")
        if cat_parts:
            qual_lines.append(f"  {c['label']}: {', '.join(cat_parts)}")
    if qual_lines:
        support_parts.append(f"Qualitative Assessment Ratings:\n" + "\n".join(qual_lines))

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
    items = items[:13]
    for i, item in enumerate(items, 1):
        item["index"] = i

    return items


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_KN_PAREN_RE = re.compile(r"\s*\(?\s*\d{1,3}\s*/\s*\d{1,3}(?:\s+runs)?(?:,\s*\d{1,3}(?:\.\d+)?\s*%?)?\s*\)?")
_KN_INLINE_RE = re.compile(r"\b\d{1,3}\s*/\s*\d{1,3}\b")


def _scrub_kn(text: str) -> str:
    if not text:
        return text
    out = _KN_PAREN_RE.sub("", text)
    out = _KN_INLINE_RE.sub("", out)
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


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
            item.frequency = _scrub_kn(item.frequency) or "All successful runs"
            item.limitation = _scrub_kn(item.limitation)

        envelope = LimitationsEnriched(
            items=sorted_items,
            source="llm",
            model=result.get("model"),
            tokens_used=result.get("tokens_used", 0),            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),        )

    except Exception as exc:
        print(f"[phase3e] LLM call failed: {exc}")
        print("[phase3e] Using fallback limitations.")
        envelope = LimitationsEnriched(
            items=_fallback_limitations(phase2),
            source="fallback",
        )

    return {"limitations_enriched": envelope.model_dump(mode="json")}
