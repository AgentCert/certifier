"""Unit tests for cert_builder/scripts/error_report_builder.py."""

from cert_builder.scripts import error_report_builder as erb


def _scorecard():
    return {
        "agent_name": "ErrAgent",
        "agent_id": "err-1",
        "certification_run_id": "run-err",
        "created_at": "2026-03-01",
        "total_runs": 12,
        "total_faults_tested": 4,
        "total_fault_categories": 2,
        "runs_per_fault": 6,
        "fault_category_scorecards": [
            {"fault_category": "application_fault", "faults_tested": ["container-kill"],
             "distinct_runs": 6, "total_runs": 6},
            {"fault_category": "network_fault", "faults_tested": ["pod-network-loss"],
             "total_runs": 6},
        ],
    }


def test_report_has_three_sections():
    report = erb.build_error_report(_scorecard())
    assert len(report["sections"]) == 3
    numbers = [s["number"] for s in report["sections"]]
    assert numbers == [1, 2, 3]


def test_meta_carried_through():
    report = erb.build_error_report(_scorecard())
    meta = report["meta"]
    assert meta["agent_name"] == "ErrAgent"
    assert meta["total_runs"] == 12
    assert meta["runs_per_fault"] == 6


def test_meta_defaults_for_missing_fields():
    report = erb.build_error_report({})
    meta = report["meta"]
    assert meta["agent_name"] == "Unknown Agent"
    assert meta["agent_id"] == "unknown-id"
    assert meta["total_runs"] == 0


def test_scope_narrative_includes_fault_types():
    report = erb.build_error_report(_scorecard())
    scope_text = report["sections"][0]["content"][3]["body"]
    assert "container-kill" in scope_text
    assert "pod-network-loss" in scope_text
    assert "12 independent runs" in scope_text


def test_scope_narrative_without_faults():
    sc = _scorecard()
    sc["total_faults_tested"] = 0
    report = erb.build_error_report(sc)
    scope_text = report["sections"][0]["content"][3]["body"]
    # still produces a coherent narrative
    assert "ErrAgent" in scope_text


def test_fault_pills_built_from_scorecards():
    report = erb.build_error_report(_scorecard())
    pills_block = report["sections"][0]["content"][-1]
    assert pills_block["type"] == "fault_pills"
    assert pills_block["items"][0]["category"] == "application_fault"
    assert pills_block["items"][0]["runs"] == 6
    # second uses total_runs fallback for distinct_runs
    assert pills_block["items"][1]["runs"] == 6


def test_methodology_section_uses_hardcoded_bullets():
    report = erb.build_error_report(_scorecard())
    method = report["sections"][1]
    items = method["content"][0]["items"]
    assert len(items) >= 1
    assert all(i["severity"] == "note" for i in items)


def test_failure_notice_section():
    report = erb.build_error_report(_scorecard())
    notice = report["sections"][2]
    assert "Metrics Extraction Failure" in notice["title"]
    assert notice["content"][0]["style"] == "error"


def test_empty_scorecards_yields_empty_pills():
    sc = _scorecard()
    sc["fault_category_scorecards"] = []
    report = erb.build_error_report(sc)
    pills_block = report["sections"][0]["content"][-1]
    assert pills_block["items"] == []
