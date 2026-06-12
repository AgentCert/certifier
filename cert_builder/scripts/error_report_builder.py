"""
Error report builders for pipeline failure cases.

build_error_report: metrics validation failure (extractor returned empty payload)
build_insufficient_runs_report: aggregation produced 0 eligible fault categories
    because every category had fewer runs than the min_runs_per_category threshold.
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


def build_insufficient_runs_report(aggregated_scorecard: Dict[str, Any]) -> Dict[str, Any]:
    """Build a minimal 3-section report when 0 fault categories passed the min-runs gate.

    This fires when the aggregator skipped every observed fault category because
    each had fewer documents than min_runs_per_category.  The cert_builder cannot
    run meaningfully on empty scorecard data, so we emit this report instead.

    Args:
        aggregated_scorecard: The aggregated scorecard dict (total_fault_categories == 0).

    Returns:
        Minimal certification report dict with Executive Summary, Methodology,
        and an Insufficient Runs Notice.
    """
    agent_name = aggregated_scorecard.get("agent_name", "Unknown Agent")
    agent_id = aggregated_scorecard.get("agent_id", "unknown-id")
    cert_date = aggregated_scorecard.get("created_at", "Unknown Date")
    total_runs = aggregated_scorecard.get("total_runs", 0)

    # Pull per-category run counts from statistical_hypothesis if available,
    # otherwise fall back to a generic message.
    sh = aggregated_scorecard.get("statistical_hypothesis") or {}
    observed = sh.get("observed_per_category", {})
    if observed:
        category_lines = ", ".join(
            f"{cat}: {count} run(s)" for cat, count in observed.items()
        )
        runs_detail = (
            f"The following fault categories were observed but had too few runs to be "
            f"eligible for certification: {category_lines}. "
        )
    else:
        runs_detail = (
            "All observed fault categories had fewer runs than the minimum required "
            "for certification. "
        )

    scope_body = (
        f"This certification evaluates the {agent_name} across a structured fault-injection "
        f"campaign designed to measure resilience, diagnostic quality, and safety compliance. "
        f"A total of {total_runs} independent run(s) were recorded. "
        f"{runs_detail}"
        f"At least 3 runs per fault category are required for aggregation-level certification; "
        f"30 or more are required for statistical hypothesis analysis."
    )

    report = {
        "meta": {
            "agent_name": agent_name,
            "agent_id": agent_id,
            "certification_run_id": aggregated_scorecard.get("certification_run_id", ""),
            "certification_date": cert_date,
            "total_runs": total_runs,
            "total_faults_tested": aggregated_scorecard.get("total_faults_tested", 0),
            "total_fault_categories": 0,
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
                    {"type": "heading", "title": "1.1 Agent Identity Card"},
                    {
                        "type": "identity_card",
                        "fields": [
                            {"label": "Agent Name", "value": agent_name},
                            {"label": "Agent ID", "value": agent_id},
                            {"label": "Certification Run ID", "value": aggregated_scorecard.get("certification_run_id", "—")},
                            {"label": "Certification Date", "value": cert_date},
                        ],
                    },
                    {"type": "heading", "title": "1.2 Experiment Scope"},
                    {"type": "text", "body": scope_body},
                    {
                        "type": "scope_metrics",
                        "metrics": [
                            {"value": 0, "label": "Fault Categories"},
                            {"value": aggregated_scorecard.get("total_faults_tested", 0), "label": "Faults Tested"},
                            {"value": total_runs, "label": "Total Runs"},
                        ],
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
                            {"severity": "note", "text": bullet}
                            for bullet in get_methodology_bullets()
                        ],
                    },
                ],
            },
            {
                "id": "insufficient_runs_notice",
                "number": 3,
                "part": None,
                "title": "Certification Halted — Insufficient Fault Category Runs",
                "intro": None,
                "content": [
                    {
                        "type": "text",
                        "body": (
                            "The certification pipeline could not produce a full report because "
                            "no fault category accumulated enough runs to meet the minimum threshold "
                            "required for meaningful statistical aggregation. "
                            "Quantitative and qualitative scoring, performance tables, charts, and "
                            "narrative sections are unavailable until more runs are collected."
                        ),
                        "style": "error",
                    },
                    {"type": "heading", "title": "What this means"},
                    {
                        "type": "text",
                        "body": (
                            "AgentCert requires a minimum of 3 runs per fault category to compute "
                            "aggregated detection rates, mitigation times, and qualitative scores. "
                            "Statistical hypothesis testing requires 30 or more runs per category. "
                            "With only the runs recorded so far, the aggregator skipped all fault "
                            "categories and the certification builder had no data to work with."
                        ),
                    },
                    {"type": "heading", "title": "Recommended next steps"},
                    {
                        "type": "text",
                        "body": (
                            "1. Run additional fault-injection experiments for each fault category "
                            "until at least 3 runs per category are available.\n"
                            "2. Re-trigger the certification pipeline once sufficient runs have been collected.\n"
                            "3. For full statistical analysis, aim for 30+ runs per fault category."
                        ),
                    },
                ],
            },
        ],
    }
    return report
