"""
Report Assembler — merges phase1 + phase2 + phase3 into a CertificationReport.

Reads the three JSON outputs, maps them into the 12-section report structure,
validates against CertificationReport (Pydantic), and writes the final JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

from cert_builder.schema.certification_schema import CertificationReport


# ── Helpers ──────────────────────────────────────────────────────────

def _text(body: str, style: str | None = None) -> dict:
    block = {"type": "text", "body": body}
    if style:
        block["style"] = style
    return block


def _heading(title: str, detail: str | None = None) -> dict:
    block = {"type": "heading", "title": title}
    if detail:
        block["detail"] = detail
    return block


def _findings(items: list[dict]) -> dict:
    return {"type": "findings", "items": items}


def _table(headers: list, rows: list, title: str | None = None) -> dict:
    block = {"type": "table", "headers": headers, "rows": rows}
    if title:
        block["title"] = title
    return block


def _card(items: list[dict], title: str | None = None) -> dict:
    block = {"type": "card", "items": items}
    if title:
        block["title"] = title
    return block


def _chart(chart_data: dict) -> dict:
    return {**chart_data, "type": "chart"}


# ── Section builders ────────────────────────────────────────────────

def _section_executive_summary(phase1, phase2, phase3):
    """Section 1: Executive Summary."""
    scope_text = phase3["scope_narrative"]["text"]
    return {
        "id": "executive_summary",
        "number": 1,
        "part": None,
        "title": "Executive Summary",
        "intro": scope_text[:200] if len(scope_text) > 200 else scope_text,
        "content": [
            _heading("Agent Identity"),
            _card(phase2["cards"]["identity_card"]["items"]),
            _heading("Experiment Scope"),
            _text(scope_text),
            _card(phase2["cards"]["scope_card"]["items"]),
            _card(
                phase2["cards"]["categories_card"]["items"],
                title=phase2["cards"]["categories_card"].get("title"),
            ),
        ],
    }


def _section_methodology(phase2):
    """Section 2: Methodology."""
    intros = phase2["hardcoded"]["section_intros"]
    bullets = phase2["hardcoded"]["methodology_bullets"]

    method_findings = [{"severity": "note", "text": b} for b in bullets]

    return {
        "id": "methodology",
        "number": 2,
        "part": None,
        "title": "Methodology",
        "intro": intros.get("methodology", ""),
        "content": [
            _findings(method_findings),
            _heading("Judge Panel"),
            _text(intros.get("methodology", "")),
            _table(**phase2["tables"]["judge_models"]),
        ],
    }


def _section_scorecard(phase2, phase3):
    """Section 3: Scorecard Snapshot."""
    key_findings = [
        {"severity": f["severity"], "text": f"{f['headline']}: {f['detail']}"}
        for f in phase3["key_findings"]["items"]
    ]

    return {
        "id": "scorecard_snapshot",
        "number": 3,
        "part": None,
        "title": "Scorecard Snapshot",
        "intro": "Overall certification scorecard with radar visualization and key findings from the evaluation.",
        "content": [
            _heading("Overall Scorecard"),
            _chart(phase2["charts"]["scorecard_radar"]),
            _heading("Key Findings"),
            _findings(key_findings),
        ],
    }


def _section_qualitative_findings(phase2, phase3):
    """Section 4: Qualitative Findings."""
    intros = phase2["hardcoded"]["section_intros"]
    qf = phase3["qualitative_findings"]

    group1 = []
    for dim in ["detection", "mitigation", "action_correctness", "reasoning"]:
        for f in qf.get(dim, []):
            group1.append({"severity": f["severity"], "text": f"{f['headline']}: {f['detail']}"})

    group2 = []
    for dim in ["safety", "security"]:
        for f in qf.get(dim, []):
            group2.append({"severity": f["severity"], "text": f"{f['headline']}: {f['detail']}"})

    safety_table = phase2["tables"]["safety_summary"]

    group3 = []
    for f in qf.get("hallucination", []):
        group3.append({"severity": f["severity"], "text": f"{f['headline']}: {f['detail']}"})

    return {
        "id": "qualitative_findings",
        "number": 4,
        "part": None,
        "title": "Qualitative Findings",
        "intro": intros.get("reasoning", "Cross-category consensus from the LLM Council."),
        "content": [
            _heading("Response & Reasoning Quality"),
            _findings(group1),
            _heading("Safety & Compliance"),
            _findings(group2),
            _table(**safety_table),
            _heading("Hallucination Assessment"),
            _findings(group3),
        ],
    }


def _section_detection_response(phase2):
    """Section 5: Detection & Response."""
    defs = phase2["hardcoded"]["definitions"]
    stats = phase2["hardcoded"]["statistics"]

    return {
        "id": "detection_response",
        "number": 5,
        "part": "Agent Capability Assessment",
        "title": "Detection & Response",
        "intro": defs["ttd"],
        "content": [
            _text(defs["ttd"], style="info"),
            _text(defs["ttm"], style="info"),
            _heading("Time-to-Detect"),
            _chart(phase2["charts"]["ttd_bar"]),
            _table(**phase2["tables"]["ttd_stats"]),
            _heading("Time-to-Mitigate"),
            _chart(phase2["charts"]["ttm_bar"]),
            _table(**phase2["tables"]["ttm_stats"]),
            _heading("Detection & Mitigation Rates"),
            _text(defs["detection_rate"], style="info"),
            _chart(phase2["charts"]["rates_bar"]),
            _table(**phase2["tables"]["detection_rates"]),
            _text(stats["median_p95"], style="info"),
            _text(stats["detection_vs_mitigation"], style="info"),
        ],
    }


def _section_accuracy(phase2):
    """Section 6: Accuracy & Efficiency."""
    defs = phase2["hardcoded"]["definitions"]

    return {
        "id": "accuracy_efficiency",
        "number": 6,
        "part": "Agent Capability Assessment",
        "title": "Accuracy & Efficiency",
        "intro": defs["action_correctness"],
        "content": [
            _chart(phase2["charts"]["accuracy_heatmap"]),
            _heading("Action Correctness"),
            _table(**phase2["tables"]["action_correctness"]),
            _text(defs["na_explanation"], style="info"),
        ],
    }


def _section_reasoning(phase2):
    """Section 7: Reasoning Quality."""
    defs = phase2["hardcoded"]["definitions"]
    intros = phase2["hardcoded"]["section_intros"]

    return {
        "id": "reasoning_quality",
        "number": 7,
        "part": "Agent Capability Assessment",
        "title": "Reasoning Quality",
        "intro": intros.get("reasoning", ""),
        "content": [
            _heading("Reasoning & Response Scores"),
            _text(defs["reasoning_scale"], style="info"),
            _chart(phase2["charts"]["reasoning_bar"]),
            _table(**phase2["tables"]["reasoning_quality"]),
            _heading("Hallucination Scores"),
            _text(defs["hallucination_score"], style="info"),
            _chart(phase2["charts"]["hallucination_bar"]),
            _table(**phase2["tables"]["hallucination"]),
        ],
    }


def _section_safety(phase2):
    """Section 8: Safety & Compliance."""
    intros = phase2["hardcoded"]["section_intros"]

    return {
        "id": "safety_compliance",
        "number": 8,
        "part": "Agent Capability Assessment",
        "title": "Safety & Compliance",
        "intro": intros.get("safety", ""),
        "content": [
            _chart(phase2["charts"]["compliance_bar"]),
            _heading("RAI Compliance"),
            _table(**phase2["tables"]["rai_compliance"]),
            _heading("Security Compliance"),
            _table(**phase2["tables"]["security_compliance"]),
            _text(phase2["hardcoded"]["definitions"]["false_positive"], style="info"),
        ],
    }


def _section_resource(phase2):
    """Section 9: Resource Utilization."""
    defs = phase2["hardcoded"]["definitions"]
    intros = phase2["hardcoded"]["section_intros"]

    return {
        "id": "resource_utilization",
        "number": 9,
        "part": "Agent Capability Assessment",
        "title": "Resource Utilization",
        "intro": intros.get("token_usage", ""),
        "content": [
            _chart(phase2["charts"]["token_stacked"]),
            _table(**phase2["tables"]["token_usage"]),
            _text(intros.get("token_usage", ""), style="info"),
            _text(phase2["hardcoded"]["normalization"]["notes"], style="info"),
        ],
    }


def _section_fault_analysis(phase1, phase2, phase3):
    """Section 10: Fault Category Analysis."""
    intros = phase2["hardcoded"]["section_intros"]
    categories = phase1["categories"]
    assessments = phase2["assessments"]
    analysis = phase3["fault_category_analysis"]["categories"]

    content = []
    for cat in categories:
        label = cat["label"]

        # Put category stats into heading.detail (matches groundtruth)
        detail = None
        if label in analysis:
            detail = analysis[label].get("detail")

        content.append(_heading(f"{label} Faults", detail=detail))

        cat_assessments = assessments.get(label, [])
        for a in cat_assessments:
            content.append({"type": "assessment", **a})

    return {
        "id": "fault_category_analysis",
        "number": 10,
        "part": "Fault Injection Analysis",
        "title": "Fault Category Analysis",
        "intro": intros.get("fault_analysis", ""),
        "content": content,
    }


def _section_limitations(phase2, phase3):
    """Section 11: Limitations."""
    intros = phase2["hardcoded"]["section_intros"]
    items = phase3["limitations_enriched"]["items"]

    headers = ["#", "Limitation", "Category", "Severity", "Frequency", "Badge"]
    rows = []
    for item in items:
        rows.append([
            item["index"],
            item["limitation"],
            item["category"],
            item["severity"],
            item["frequency"],
            item.get("label"),
        ])

    return {
        "id": "limitations",
        "number": 11,
        "part": None,
        "title": "Limitations",
        "intro": intros.get("limitations", ""),
        "content": [_table(headers, rows)],
    }


def _section_recommendations(phase2, phase3):
    """Section 12: Recommendations."""
    intros = phase2["hardcoded"]["section_intros"]
    items = phase3["recommendations_enriched"]["items"]

    headers = ["#", "Priority", "Recommendation", "Category"]
    rows = []
    for item in items:
        rows.append([
            item["index"],
            item["priority"],
            item["recommendation"],
            item["category"],
        ])

    return {
        "id": "recommendations",
        "number": 12,
        "part": None,
        "title": "Recommendations",
        "intro": intros.get("recommendations", ""),
        "content": [_table(headers, rows)],
    }


# ── Meta + Header + Footer ──────────────────────────────────────────

def _build_meta(phase1):
    m = phase1["meta"]
    return {
        "agent_name": m["agent_name"],
        "agent_id": m["agent_id"],
        "certification_run_id": m.get("certification_run_id", ""),
        "certification_date": m["certification_date"],
        "subtitle": f"Resilience & Safety Evaluation \u2014 {m['agent_name']}",
        "total_runs": m["total_runs"],
        "total_faults": m["total_faults_tested"],
        "total_categories": m["total_fault_categories"],
        "runs_per_fault_configured": m["runs_per_fault"],
        "categories": m["categories_summary"],
    }


def _build_header(phase2, phase3):
    scorecard = phase2["scorecard"]["dimensions"]
    findings = [
        {"severity": f["severity"], "text": f"{f['headline']}: {f['detail']}"}
        for f in phase3["key_findings"]["items"]
    ]
    return {"scorecard": scorecard, "findings": findings}


def _build_footer(meta):
    return f"Agent Certification Report \u2014 {meta['agent_name']} \u2014 Generated {meta['certification_date']}"


# ── ReportAssembler class ──────────────────────────────────────────

class ReportAssembler:
    """Assembles Phase 1+2+3 outputs into the final CertificationReport.

    Args:
        phase1_path: path to phase1 parsed context JSON.
        phase2_path: path to phase2 computed content JSON.
        phase3_path: path to phase3 narratives JSON.
        debug: if True, write intermediate output.
    """

    def __init__(self, phase1_path, phase2_path, phase3_path, debug=False):
        self.phase1_path = Path(phase1_path)
        self.phase2_path = Path(phase2_path)
        self.phase3_path = Path(phase3_path)
        self.debug = debug

    def assemble(self) -> dict:
        """Merge all phases into a validated CertificationReport dict.

        Returns:
            Dict that passes CertificationReport.model_validate().
        """
        phase1 = json.loads(self.phase1_path.read_text(encoding="utf-8"))
        phase2 = json.loads(self.phase2_path.read_text(encoding="utf-8"))
        phase3 = json.loads(self.phase3_path.read_text(encoding="utf-8"))

        meta = _build_meta(phase1)
        header = _build_header(phase2, phase3)
        footer = _build_footer(meta)

        sections = [
            _section_executive_summary(phase1, phase2, phase3),
            _section_methodology(phase2),
            _section_scorecard(phase2, phase3),
            _section_qualitative_findings(phase2, phase3),
            _section_detection_response(phase2),
            _section_accuracy(phase2),
            _section_reasoning(phase2),
            _section_safety(phase2),
            _section_resource(phase2),
            _section_fault_analysis(phase1, phase2, phase3),
            _section_limitations(phase2, phase3),
            _section_recommendations(phase2, phase3),
        ]

        report_dict = {
            "meta": meta,
            "header": header,
            "sections": sections,
            "footer": footer,
        }

        # Validate against Pydantic schema
        report = CertificationReport.model_validate(report_dict)

        # Return validated dict
        return report.model_dump(mode="json")

    def assemble_and_save(self, output_path) -> dict:
        """Assemble and write the final certification report.

        Returns:
            The validated report dict.
        """
        result = self.assemble()

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"[report-assembler] Wrote {output_path.name} ({output_path.stat().st_size / 1024:.1f} KB)")
        return result
