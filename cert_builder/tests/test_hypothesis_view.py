"""Unit tests for cert_builder/scripts/ingestion/hypothesis_view.py."""

from types import SimpleNamespace

from cert_builder.scripts.ingestion import hypothesis_view as hv


def _ctx(block):
    return SimpleNamespace(statistical_hypothesis=block)


def test_status_defaults_when_missing():
    assert hv.status(SimpleNamespace()) == "not_requested"
    assert hv.status(_ctx(None)) == "not_requested"
    assert hv.status(_ctx({})) == "not_requested"


def test_status_passthrough():
    assert hv.status(_ctx({"status": "ok"})) == "ok"


def test_is_helpers():
    assert hv.is_ok(_ctx({"status": "ok"}))
    assert hv.is_skipped(_ctx({"status": "skipped"}))
    assert hv.is_not_requested(_ctx({"status": "not_requested"}))
    assert not hv.is_ok(_ctx({"status": "skipped"}))


def test_skip_reason_and_message():
    block = {"status": "skipped", "reason": "insufficient_runs", "message": "too few"}
    assert hv.skip_reason(_ctx(block)) == "insufficient_runs"
    assert hv.skip_message(_ctx(block)) == "too few"


def test_skip_reason_none_when_absent():
    assert hv.skip_reason(_ctx({"status": "ok"})) is None


def test_min_required_and_observed():
    block = {"status": "skipped", "min_required": 30,
             "observed_per_category": {"application_fault": 10}}
    assert hv.min_required(_ctx(block)) == 30
    assert hv.observed_per_category(_ctx(block)) == {"application_fault": 10}


def test_observed_defaults_empty():
    assert hv.observed_per_category(_ctx({"status": "ok"})) == {}


def test_results_none_when_not_ok():
    assert hv.results(_ctx({"status": "skipped"})) is None


def test_results_double_nested_inner():
    block = {"status": "ok", "results": {"results": {"h01": {}, "h02": {}}}}
    res = hv.results(_ctx(block))
    assert "h01" in res and "h02" in res


def test_results_single_nested_fallback():
    # outer.results is not a dict-of-h0x -> return outer
    block = {"status": "ok", "results": {"h01": {}, "metadata": {}}}
    res = hv.results(_ctx(block))
    assert "h01" in res
