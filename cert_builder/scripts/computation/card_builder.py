"""
Sub-Phase 2F -- Card data builder.

What this script does:
  1. Reads Phase 1 parsed context (meta section).
  2. Builds 3 card data structures for the Executive Summary.

Cards produced:
  1. identity_card    -- Agent Name, Agent ID, Certification Run, Date
  2. scope_card       -- Fault Categories, Faults Tested, Total Runs
  3. categories_card  -- Per-category fault name and run count

Input:  phase1_parsed_context.json -> meta
Output: {"cards": {"identity_card": {...}, "scope_card": {...}, "categories_card": {...}}}
"""

import json
from pathlib import Path

from cert_builder.schema.intermediate import CardsResult


def _build_identity_card(meta):
    """Agent identity card with 4 key-value items."""
    run_id = meta.get("certification_run_id") or "\u2014"
    return {
        "title": "Agent Identity",
        "items": [
            {"label": "Agent Name", "value": meta.get("agent_name", "\u2014")},
            {"label": "Agent ID", "value": meta.get("agent_id", "\u2014")},
            {"label": "Certification Run", "value": run_id},
            {"label": "Certification Date", "value": meta.get("certification_date", "\u2014")},
        ],
    }


def _build_scope_card(meta):
    """Evaluation scope card with 3 key-value items."""
    return {
        "title": "Evaluation Scope",
        "items": [
            {"label": "Fault Categories", "value": meta.get("total_fault_categories", 0)},
            {"label": "Faults Tested", "value": meta.get("total_faults_tested", 0)},
            {"label": "Total Runs", "value": meta.get("total_runs", 0)},
        ],
    }


def _build_categories_card(meta):
    """Fault categories tested card with per-category details."""
    summaries = meta.get("categories_summary", [])
    items = []
    for s in summaries:
        name = s.get("name", "Unknown")
        fault = s.get("fault", "unknown")
        runs = s.get("runs", 0)
        items.append({
            "label": f"{name} Fault",
            "value": f"{fault} ({runs} runs)",
        })
    return {
        "title": "Fault Categories Tested",
        "items": items,
    }


# -- Public API ---------------------------------------------------------------

def build_all_cards(meta):
    """Build all 3 card data structures from Phase 1 meta.

    Args:
        meta: dict from phase1_parsed_context.json["meta"].

    Returns:
        {"cards": {"identity_card": {...}, "scope_card": {...}, "categories_card": {...}}}
    """
    result = CardsResult.model_validate({
        "cards": {
            "identity_card": _build_identity_card(meta),
            "scope_card": _build_scope_card(meta),
            "categories_card": _build_categories_card(meta),
        }
    })
    return result.model_dump(mode="json")


def build_from_file(phase1_path):
    """Load Phase 1 output and build all cards.

    Args:
        phase1_path: path to phase1_parsed_context.json.
    """
    ctx = json.loads(Path(phase1_path).read_text(encoding="utf-8"))
    return build_all_cards(ctx["meta"])
