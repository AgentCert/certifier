"""Unit tests for cert_builder/scripts/ingestion/ingestor.py."""

import json
from dataclasses import asdict

from cert_builder.scripts.ingestion import ingestor as ing


def _raw_scorecard():
    return {
        "agent_name": "MyAgent",
        "agent_id": "a-1",
        "certification_run_id": "run-9",
        "created_at": "2026-02-03T12:00:00Z",
        "total_runs": 30,
        "total_successful_runs": 28,
        "total_failed_runs": 2,
        "total_faults_tested": 5,
        "total_fault_categories": 2,
        "runs_per_fault": 15,
        "responsible_ai": {"score": 90},
        "fault_category_scorecards": [
            {
                "fault_category": "application_fault",
                "faults_tested": ["container-kill"],
                "total_runs": 15,
                "successful_runs": 14,
                "failed_runs": 1,
                "distinct_runs": 12,
                "numeric_metrics": {
                    "time_to_detect": {"mean": 10},
                    "input_tokens": {"sum": 100},
                    "sensitive_data_exposure_count": {"sum": 2, "mean": 0.1},
                },
                "derived_metrics": {"fault_detection_success_rate": 0.9},
                "boolean_status_metrics": {"pii_detection": {"any_detected": False}},
                "textual_metrics": {"agent_summary": {"consensus_summary": "ok"}},
            },
            {
                "fault_category": "custom_unknown",
                "faults_tested": [],
                "total_runs": 0,
                "numeric_metrics": {},
                "derived_metrics": {},
            },
        ],
    }


def test_ingest_meta_fields():
    ctx = ing.ingest(_raw_scorecard())
    assert ctx.meta["agent_name"] == "MyAgent"
    assert ctx.meta["successful_runs"] == 28
    assert ctx.meta["failed_runs"] == 2
    assert ctx.meta["responsible_ai"] == {"score": 90}


def test_ingest_date_truncated_at_t():
    ctx = ing.ingest(_raw_scorecard())
    assert ctx.meta["certification_date"] == "2026-02-03"


def test_ingest_category_label_mapping():
    ctx = ing.ingest(_raw_scorecard())
    labels = [c["label"] for c in ctx.categories]
    assert labels[0] == "Application"
    # unknown category title-cased from underscored name
    assert labels[1] == "Custom Unknown"


def test_ingest_numeric_missing_field_emits_warning():
    ctx = ing.ingest(_raw_scorecard())
    # time_to_mitigate missing for application -> warning + empty dict
    assert any("time_to_mitigate" in w for w in ctx.warnings)
    assert ctx.categories[0]["numeric"]["time_to_mitigate"] == {}


def test_ingest_pii_field_remapped():
    ctx = ing.ingest(_raw_scorecard())
    # sensitive_data_exposure_count -> sensitive_exposure
    assert ctx.categories[0]["numeric"]["sensitive_exposure"] == {"sum": 2, "mean": 0.1}
    # adversarial missing -> default zero dict
    assert ctx.categories[0]["numeric"]["adversarial_inputs"] == {"sum": 0.0, "mean": 0.0}


def test_ingest_distinct_runs_defaults_to_total():
    ctx = ing.ingest(_raw_scorecard())
    # second category has no distinct_runs -> falls back to total_runs (0)
    assert ctx.categories[1]["distinct_runs"] == 0
    assert ctx.categories[0]["distinct_runs"] == 12


def test_ingest_categories_summary():
    ctx = ing.ingest(_raw_scorecard())
    summ = ctx.meta["categories_summary"][0]
    assert summ["name"] == "Application"
    assert summ["fault"] == "container-kill"
    assert summ["runs"] == 12
    assert summ["n_faults"] == 1


def test_ingest_statistical_hypothesis_default():
    raw = _raw_scorecard()
    ctx = ing.ingest(raw)
    assert ctx.statistical_hypothesis == {"status": "not_requested"}


def test_ingest_statistical_hypothesis_passthrough():
    raw = _raw_scorecard()
    raw["statistical_hypothesis"] = {"status": "ok", "results": {}}
    ctx = ing.ingest(raw)
    assert ctx.statistical_hypothesis["status"] == "ok"


def test_compute_runs_per_fault():
    assert ing._compute_runs_per_fault({"runs_per_fault": 7}) == 7
    assert ing._compute_runs_per_fault({}) == 0


def test_ingest_from_file_and_save(tmp_path):
    inp = tmp_path / "raw.json"
    inp.write_text(json.dumps(_raw_scorecard()))
    ctx = ing.ingest_from_file(inp)
    assert ctx.meta["agent_id"] == "a-1"

    out = tmp_path / "nested" / "ctx.json"
    ing.save_context(ctx, out)
    assert out.exists()
    saved = json.loads(out.read_text())
    assert saved == asdict(ctx)


def test_ingest_empty_scorecards():
    ctx = ing.ingest({"fault_category_scorecards": []})
    assert ctx.categories == []
    assert ctx.meta["agent_name"] == ""
