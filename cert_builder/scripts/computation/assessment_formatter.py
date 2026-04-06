"""
Sub-Phase 2D -- Assessment formatter.

Reformats per-category LLM Council qualitative assessments from Phase 1
into structured AssessmentBlock dicts. This is a pure passthrough --
consensus summaries are copied verbatim, metadata fields are mapped
to standardized keys.

Input:  phase1_parsed_context.json -> categories[].textual
Output: {"assessments": {"Application": [4 blocks], "Network": [...], ...}}

Each block: {title, rating, confidence, agreement, body}
"""

import json
from pathlib import Path

from cert_builder.schema.intermediate import AssessmentsResult

# Ordered mapping: (source_field, display_title, has_rating)
# Order matches certification_report.json Section 10
FIELD_MAP = [
    ("agent_summary",                          "Agent Summary",                  False),
    ("overall_response_and_reasoning_quality",  "Response & Reasoning Quality",  True),
    ("security_compliance_summary",             "Security Compliance",           True),
    ("rai_check_summary",                       "RAI Compliance",                True),
]


def _format_assessment(textual_obj, title, has_rating):
    """Map one Phase 1 textual object to one assessment dict."""
    return {
        "title": title,
        "rating": textual_obj.get("severity_label") if has_rating else None,
        "confidence": textual_obj.get("confidence", "High"),
        "agreement": textual_obj.get("inter_judge_agreement", 1.0),
        "body": textual_obj.get("consensus_summary", ""),
    }


def build_all_assessments(categories):
    """Build assessment blocks for all categories.

    Args:
        categories: list of category dicts from Phase 1.

    Returns:
        {"assessments": {"Application": [{...}, ...], ...}}
    """
    assessments = {}
    for cat in categories:
        label = cat.get("label", "Unknown")
        textual = cat.get("textual", {})
        blocks = []
        for source_key, title, has_rating in FIELD_MAP:
            obj = textual.get(source_key, {})
            if obj:
                blocks.append(_format_assessment(obj, title, has_rating))
        assessments[label] = blocks
    result = AssessmentsResult.model_validate({"assessments": assessments})
    return result.model_dump(mode="json")


def build_from_file(phase1_path):
    """Load Phase 1 output and build all assessment blocks.

    Args:
        phase1_path: path to phase1_parsed_context.json.
    """
    ctx = json.loads(Path(phase1_path).read_text(encoding="utf-8"))
    return build_all_assessments(ctx["categories"])
