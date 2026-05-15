"""
Error report builder for metrics validation failures.

Generates a minimal 3-section certification report when metrics validation fails:
- Section 1: Executive Summary (with scope and fault categories)
- Section 2: Evaluation Methodology (from hardcoded content)
- Section 3: Metrics Extraction Failure Notice
"""

from typing import Any, Dict

from cert_builder.scripts.computation.hardcoded_loader import get_methodology_bullets


def build_error_report(aggregated_scorecard: Dict[str, Any]) -> Dict[str, Any]:
    """Build a minimal hardcoded 3-section error report when metrics validation fails.
    
    Args:
        aggregated_scorecard: The aggregated scorecard dict with metadata and category scorecards.
    
    Returns:
        Minimal certification report dict with Executive Summary, Methodology, Error Notice.
    """
    # Extract metadata from top-level keys
    agent_name = aggregated_scorecard.get("agent_name", "Unknown Agent")
    agent_id = aggregated_scorecard.get("agent_id", "unknown-id")
    cert_date = aggregated_scorecard.get("created_at", "Unknown Date")
    total_runs = aggregated_scorecard.get("total_runs", 0)
    total_faults_tested = aggregated_scorecard.get("total_faults_tested", 0)
    total_fault_categories = aggregated_scorecard.get("total_fault_categories", 0)
    
    # Build scope narrative with category details
    category_scorecards = aggregated_scorecard.get("fault_category_scorecards", [])
    scope_body = (
        f"This certification evaluates the {agent_name} across a structured fault-injection campaign "
        f"designed to measure resilience, diagnostic quality, and safety compliance under realistic failure conditions. "
        f"The experiment targeted {total_fault_categories} distinct fault categories "
    )
    
    if category_scorecards and total_faults_tested > 0:
        fault_types = []
        for sc in category_scorecards:
            faults = sc.get("faults_tested", [])
            if faults:
                fault_types.append(faults[0])  # Use first fault type as representative
        
        if fault_types:
            scope_body += f"— {', '.join(fault_types)} — "
        else:
            scope_body += "— "
    else:
        scope_body += "— "
    
    scope_body += (
        f"each exercised by representative fault types. "
        f"A total of {total_runs} independent runs were executed to establish statistically grounded performance baselines. "
        f"Each run subjected the agent to a controlled Kubernetes fault scenario and evaluated its ability to "
        f"detect, diagnose, and remediate the injected fault while adhering to responsible AI and security compliance standards."
    )
    
    report = {
        "meta": {
            "agent_name": agent_name,
            "agent_id": agent_id,
            "certification_run_id": aggregated_scorecard.get("certification_run_id", ""),
            "certification_date": cert_date,
            "total_runs": total_runs,
            "total_faults_tested": total_faults_tested,
            "total_fault_categories": total_fault_categories,
            "runs_per_fault": aggregated_scorecard.get("runs_per_fault", 0),
        },
        "sections": [
            {
                "id": "executive_summary",
                "number": 1,
                "part": None,
                "title": "Executive Summary",
                "intro": "Agent Identity and Experiment Scope",
                "content": [
                    {
                        "type": "heading",
                        "title": "1.1 Agent Identity Card",
                    },
                    {
                        "type": "identity_card",
                        "fields": [
                            {"label": "Agent Name", "value": agent_name},
                            {"label": "Agent ID", "value": agent_id},
                            {"label": "Certification Run ID", "value": aggregated_scorecard.get("certification_run_id", "—")},
                            {"label": "Certification Date", "value": cert_date},
                        ],
                    },
                    {
                        "type": "heading",
                        "title": "1.2 Experiment Scope",
                    },
                    {
                        "type": "text",
                        "body": scope_body,
                    },
                    {
                        "type": "scope_metrics",
                        "metrics": [
                            {"value": total_fault_categories, "label": "Fault Categories"},
                            {"value": total_faults_tested, "label": "Faults Tested"},
                            {"value": total_runs, "label": "Total Runs"},
                        ],
                    },
                    {
                        "type": "heading",
                        "title": "Fault Categories Tested",
                    },
                    {
                        "type": "fault_pills",
                        "items": [
                            {
                                "category": sc.get("fault_category", ""),
                                "faults": sc.get("faults_tested", []),
                                "runs": sc.get("distinct_runs", sc.get("total_runs", 0)),
                            }
                            for sc in category_scorecards
                        ] if category_scorecards else [],
                    },
                ],
            },
            {
                "id": "methodology",
                "number": 2,
                "part": None,
                "title": "Evaluation Methodology",
                "intro": "Evaluation Lifecycle and Metrics Collection",
                "content": [
                    {
                        "type": "findings",
                        "items": [
                            {
                                "severity": "note",
                                "text": bullet,
                            }
                            for bullet in get_methodology_bullets()
                        ],
                    },
                ],
            },
            {
                "id": "metrics_failure_notice",
                "number": 3,
                "part": None,
                "title": "Certification Halted — Metrics Extraction Failure",
                "intro": None,
                "content": [
                    {
                        "type": "text",
                        "body": "The certification pipeline executed end-to-end, but the metrics extractor returned an empty payload. As a result, no quantitative or qualitative scoring is available for this agent and the certification cannot be issued in its standard form. The remainder of this report is intentionally suppressed until the underlying issue is resolved and the pipeline is re-run.",
                        "style": "error",
                    },
                    {
                        "type": "heading",
                        "title": "What this means",
                    },
                    {
                        "type": "text",
                        "body": "Both quantitative metrics (TTD, TTM, success rates, token usage) and qualitative metrics (reasoning, RAI, security assessments) returned empty. Likely root causes: malformed or truncated trace input, schema drift between the trace producer and the extractor, or an internal extractor failure. Root cause cannot be determined automatically — manual investigation of the raw trace, extractor logs, and pipeline configuration is required.",
                    },
                    {
                        "type": "heading",
                        "title": "Recommended next steps",
                    },
                    {
                        "type": "text",
                        "body": "1. Validate the input trace against the extractor's expected schema and confirm it is well-formed.\n2. Review the metrics-extractor logs for parse errors or unhandled exceptions to pinpoint the failure.\n3. After fixing the identified issues, re-run the experiment to generate a complete certification report.",
                    },
                ],
            },
        ],
    }
    
    return report
