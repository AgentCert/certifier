"""Shared deterministic fixtures for cert_builder computation tests.

These mirror the Phase-1 parsed-context shape consumed by the builders.
Numbers are chosen so that expected outputs are easy to derive by hand.
"""


def make_category(label="Application", fault_category="application_fault"):
    """A fully-populated category dict with derivable numbers."""
    return {
        "fault_category": fault_category,
        "label": label,
        "faults_tested": ["container-kill", "pod-delete"],
        "total_runs": 20,
        "successful_runs": 18,
        "failed_runs": 2,
        "distinct_runs": 10,
        "runs_per_fault": 10,
        "numeric": {
            "time_to_detect": {
                "category": {
                    "n_sub_faults": 2,
                    "n_attempted": 18,
                    "sla_compliance": 0.5,
                    "detection_rate": 0.8,
                    "category_score": 0.6,
                },
                "subfault": {
                    "pod-delete": {
                        "n_attempted": 9,
                        "sla_compliance": 1.0,
                        "detection_rate": 0.9,
                        "mean_s": 12.34,
                        "median_s": 10.0,
                        "p95_s": 20.0,
                    },
                },
            },
            "time_to_mitigate": {
                "category": {
                    "n_sub_faults": 2,
                    "n_attempted": 16,
                    "sla_compliance": 0.25,
                    "detection_rate": 0.7,
                    "category_score": 0.4,
                },
                "subfault": {
                    "pod-delete": {
                        "n_attempted": 8,
                        "sla_compliance": 0.5,
                        "detection_rate": 0.6,
                        "mean_s": 100.0,
                        "median_s": 90.0,
                        "p95_s": 150.0,
                    },
                },
            },
            "action_correctness": {"mean": 1.0, "median": 1.0, "std_dev": 0.0},
            "reasoning_score": {"mean": 0.8, "median": 0.75},
            "hallucination_score": {"mean": 0.0, "max": 0.0},
            "input_tokens": {"sum": 1000, "mean": 100.0},
            "output_tokens": {"sum": 500, "mean": 50.0},
            "sensitive_exposure": {"sum": 0.0, "mean": 0.0},
            "adversarial_inputs": {"sum": 0.0, "mean": 0.0},
        },
        "derived": {
            "fault_detection_success_rate": 0.8,
            "fault_mitigation_success_rate": 0.7,
            "false_negative_rate": 0.2,
            "false_positive_rate": 0.0,
            "rai_compliance_rate": 1.0,
            "security_compliance_rate": 1.0,
            "pii_clean_rate": 1.0,
            "adversarial_clean_rate": 1.0,
        },
        "boolean": {
            "pii_detection": {"any_detected": False, "detection_rate": 0.0},
            "hallucination_detection": {"any_detected": False, "detection_rate": 0.0},
        },
        "textual": {
            "agent_summary": {
                "consensus_summary": "Agent handled faults well.",
                "confidence": "High",
                "inter_judge_agreement": 0.95,
            },
            "overall_response_and_reasoning_quality": {
                "severity_label": "Strong",
                "confidence": "High",
                "inter_judge_agreement": 0.9,
                "consensus_summary": "Strong reasoning.",
            },
            "security_compliance_summary": {
                "severity_label": "Clean",
                "confidence": "Medium",
                "inter_judge_agreement": 0.8,
                "consensus_summary": "No security issues.",
            },
            "rai_check_summary": {
                "severity_label": "Clean",
                "confidence": "High",
                "inter_judge_agreement": 0.85,
            },
            "known_limitations": {
                "ranked_items": [
                    {"limitation": "Slow on network faults", "severity": "High", "frequency": 3},
                    {"limitation": "Minor logging gap", "severity": "Low", "frequency": 1},
                ],
            },
            "recommendations": {
                "prioritized_items": [
                    {"recommendation": "Tune detection threshold", "priority": "High"},
                    {"recommendation": "Improve logging", "priority": "Low"},
                ],
            },
        },
    }


def make_meta():
    return {
        "agent_name": "TestAgent",
        "agent_id": "agent-007",
        "certification_run_id": "run-42",
        "certification_date": "2026-01-01",
        "total_runs": 20,
        "successful_runs": 18,
        "failed_runs": 2,
        "total_faults_tested": 4,
        "total_fault_categories": 2,
        "runs_per_fault": 10,
        "categories_summary": [
            {"name": "Application", "fault": "container-kill, pod-delete", "runs": 10},
            {"name": "Network", "fault": "pod-network-loss", "runs": 8},
        ],
        "responsible_ai": {
            "score": 92,
            "rai_decision": "PASS",
            "gates": {"privacy_security_passed": True},
            "principles": {
                "privacy_security": {
                    "label": "Privacy & Security", "score": 0.92, "score_pct": 92,
                    "personal_pii_runs": 0, "adversarial_inputs": 0,
                    "sensitive_data_exposure_total": 0,
                },
                "transparency": {"label": "Transparency", "score": 0.8, "score_pct": 80},
                "fairness": {"label": "Fairness", "score": 0.7, "score_pct": 70},
            },
            "evidence": [
                {"principle": "Privacy & Security", "severity": "Low", "finding": "None"},
            ],
        },
    }
