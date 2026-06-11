"""Unit tests for the DETERMINISTIC pure helpers in
cert_builder/scripts/report_assembler.py.

The full section assembly is LLM-driven (Agent Performance Summary, table
findings, etc.) and is intentionally NOT covered here. Only the pure,
side-effect-free block builders and formatters are tested.
"""

from cert_builder.scripts import report_assembler as ra
from cert_builder.scripts.narratives.hypothesis_overlay_builder import HypothesisOverlay


# ── simple block builders ────────────────────────────────────────────

def test_text_block():
    assert ra._text("hi") == {"type": "text", "body": "hi"}
    assert ra._text("hi", style="info") == {"type": "text", "body": "hi", "style": "info"}


def test_heading_block():
    assert ra._heading("T") == {"type": "heading", "title": "T"}
    assert ra._heading("T", detail="d") == {"type": "heading", "title": "T", "detail": "d"}


def test_findings_block():
    assert ra._findings([{"severity": "good", "text": "x"}]) == {
        "type": "findings", "items": [{"severity": "good", "text": "x"}]}


def test_table_block():
    assert ra._table(["h"], [[1]]) == {"type": "table", "headers": ["h"], "rows": [[1]]}
    assert ra._table(["h"], [[1]], title="T")["title"] == "T"


def test_card_block():
    assert ra._card([{"label": "L", "value": "V"}], title="C") == {
        "type": "card", "items": [{"label": "L", "value": "V"}], "title": "C"}


def test_chart_block_adds_type():
    assert ra._chart({"chart_type": "radar"}) == {"chart_type": "radar", "type": "chart"}


def test_scope_stats_fault_pills_part_banner():
    assert ra._scope_stats([{"value": "1", "label": "L"}])["type"] == "scope_stats"
    fp = ra._fault_pills([{"category": "App"}], title="Faults")
    assert fp["type"] == "fault_pills" and fp["title"] == "Faults"
    assert ra._part_banner("Part I", "Cap") == {
        "type": "part_banner", "label": "Part I", "title": "Cap"}


def test_interpretation_scale_and_taxonomy():
    assert ra._interpretation_scale(["a", "b"])["bands"] == ["a", "b"]
    tx = ra._taxonomy_table(["h"], [[1]], title="T", footnote="fn")
    assert tx["type"] == "taxonomy_table"
    assert tx["footnote"] == "fn"


def test_enumerated_item():
    item = ra._enumerated_item(kind="limitation", index=2, severity="High",
                               scope="App", body="b", tags=["T"], frequency="2/3")
    assert item["index"] == 2
    assert item["tags"] == ["T"]
    assert item["frequency"] == "2/3"


def test_enumerated_item_defaults_tags():
    item = ra._enumerated_item(kind="recommendation", index=1, severity="Low",
                               scope="X", body="b")
    assert item["tags"] == []
    assert "frequency" not in item


# ── category prettifier ──────────────────────────────────────────────

def test_pretty_category_known():
    assert ra._pretty_category("application_fault") == "Application"
    assert ra._pretty_category("network_fault") == "Network"


def test_pretty_category_unknown():
    assert ra._pretty_category("weird_thing_fault") == "Weird Thing"


# ── verdict thresholds ───────────────────────────────────────────────

def test_verdict_for_higher_is_better():
    assert ra._verdict_for(0.9, good=0.8, fair=0.5) == "pass"
    assert ra._verdict_for(0.6, good=0.8, fair=0.5) == "inconclusive"
    assert ra._verdict_for(0.3, good=0.8, fair=0.5) == "flag"


def test_verdict_for_none_is_inconclusive():
    assert ra._verdict_for(None, good=0.8, fair=0.5) == "inconclusive"


def test_verdict_for_lower_is_better():
    assert ra._verdict_for(0.1, good=0.2, fair=0.5, lower_is_better=True) == "pass"
    assert ra._verdict_for(0.4, good=0.2, fair=0.5, lower_is_better=True) == "inconclusive"
    assert ra._verdict_for(0.9, good=0.2, fair=0.5, lower_is_better=True) == "flag"


def test_det_strip():
    strip = ra._det_strip(hypothesis_id="H1", metric_label="TTD",
                          verdict="pass", summary="s", method="M")
    assert strip["type"] == "hypothesis_strip"
    assert strip["facts"] == []
    assert strip["method"] == "M"


def test_det_strip_no_method():
    strip = ra._det_strip(hypothesis_id="H2", metric_label="X",
                          verdict="flag", summary="s")
    assert "method" not in strip


# ── p-value formatter ────────────────────────────────────────────────

def test_p_str():
    assert ra._p_str(None) == "—"
    assert ra._p_str(0.0005) == "< 0.001"
    assert ra._p_str(0.04) == "0.040"
    assert ra._p_str("not-a-number") == "not-a-number"


# ── K/N scrubber ─────────────────────────────────────────────────────

def test_scrub_kn_text_removes_fractions():
    out = ra._scrub_kn_text("Detected 31/31 runs (62/62, 100.0%) cleanly.")
    assert "/" not in out
    assert "31" not in out
    assert "Detected" in out and "cleanly." in out


def test_scrub_kn_text_empty():
    assert ra._scrub_kn_text("") == ""


# ── table note generator ─────────────────────────────────────────────

def test_build_table_note_known_columns():
    note = ra._build_table_note(["Category", "Mean (s)", "Unknown"])
    assert note.startswith("**How to read this table:**")
    assert "Category" in note and "Mean (s)" in note


def test_build_table_note_no_known_columns():
    assert ra._build_table_note(["Foo", "Bar"]) is None


# ── CI-bar chart builders ────────────────────────────────────────────

def test_h01_ci_bar():
    out = ra._h01_ci_bar(
        [{"category": "application_fault", "iqm": 12.0, "ci_lower": 10.0, "ci_upper": 15.0}],
        title="T", y_label="s")
    assert out["chart_type"] == "ci_bar"
    assert out["points"][0] == {"label": "Application", "value": 12.0,
                                "ci_low": 10.0, "ci_high": 15.0}


def test_h01_ci_bar_skips_none_iqm_returns_none():
    assert ra._h01_ci_bar([{"category": "x", "iqm": None}], title="T", y_label="s") is None


def test_h02_ci_bar_groups():
    out = ra._h02_ci_bar(
        [{"category": "application_fault", "rate": 0.8, "wilson_lower": 0.6, "wilson_upper": 0.95}],
        [{"category": "application_fault", "rate": 0.7, "wilson_lower": 0.5, "wilson_upper": 0.85}],
        title="T")
    groups = [p["group"] for p in out["points"]]
    assert groups == ["Detection", "Mitigation"]


def test_h02_ci_bar_empty_returns_none():
    assert ra._h02_ci_bar([], [], title="T") is None


def test_h02_compliance_ci_bar_groups():
    out = ra._h02_compliance_ci_bar(
        [{"category": "application_fault", "rate": 1.0, "wilson_lower": 0.9, "wilson_upper": 1.0}],
        [{"category": "application_fault", "rate": 1.0, "wilson_lower": 0.9, "wilson_upper": 1.0}],
        title="T")
    groups = [p["group"] for p in out["points"]]
    assert groups == ["RAI Compliance", "Security Compliance"]


# ── phase1 h01/h02 extraction ────────────────────────────────────────

def test_phase1_h01_h02_double_nested():
    p1 = {"statistical_hypothesis": {"results": {"results": {"h01": {"a": 1}, "h02": {"b": 2}}}}}
    assert ra._phase1_h01_h02(p1) == ({"a": 1}, {"b": 2})


def test_phase1_h01_h02_empty():
    assert ra._phase1_h01_h02({}) == ({}, {})


# ── findings-from-text parser ────────────────────────────────────────

def test_findings_from_text_symbols():
    block = ra._findings_from_text("✓ Good thing ⚠ Bad thing")
    items = block["items"]
    assert items[0]["severity"] == "good"
    assert items[1]["severity"] == "concern"


def test_findings_from_text_empty():
    assert ra._findings_from_text("   ") is None


def test_findings_from_text_newline_fallback():
    block = ra._findings_from_text("line one\nline two")
    assert len(block["items"]) == 2
    assert all(i["severity"] == "note" for i in block["items"])


# ── combine H2 rate strips ───────────────────────────────────────────

def test_combine_h02_rate_strips_worst_verdict_and_facts():
    det = [{"verdict": "pass", "facts": [{"label": "App", "text": "t", "tone": "warning"}],
            "findings": "detfind", "method": "M"}]
    mit = [{"verdict": "flag", "facts": [{"label": "Net", "text": "t2", "tone": "good"}],
            "findings": "mitfind"}]
    out = ra._combine_h02_rate_strips(det, mit)
    assert out["verdict"] == "flag"  # worst of pass/flag
    assert out["facts"][0]["label"] == "Detection — App"
    assert out["facts"][0]["tone"] == "warn"  # warning normalized
    assert out["facts"][1]["label"] == "Mitigation — Net"
    assert "Detection. detfind" in out["findings"]
    assert "Mitigation. mitfind" in out["findings"]
    assert out["method"] == "M"


def test_combine_h02_rate_strips_none_when_empty():
    assert ra._combine_h02_rate_strips([], []) is None


# ── statistical findings synthesis ───────────────────────────────────

def test_build_statistical_findings_suppressed_returns_empty():
    overlay = HypothesisOverlay(suppressed=True)
    assert ra._build_statistical_findings(overlay, {}) == []


def test_build_statistical_findings_none_overlay():
    assert ra._build_statistical_findings(None, {}) == []


def test_build_statistical_findings_h2_h4():
    overlay = HypothesisOverlay(suppressed=False)
    phase1 = {"statistical_hypothesis": {"results": {"results": {
        "h02": {"fault_detection_success_rate": {"per_category": [
            {"category": "application_fault", "wilson_lower": 0.4, "wilson_upper": 0.7}]}},
        "h04": {"fault_detection_success_rate": {
            "significant": True, "statistic": 5.5, "p_value": 0.02,
            "weakest_category": "network_fault"}},
    }}}}
    findings = ra._build_statistical_findings(overlay, phase1)
    texts = " ".join(f["text"] for f in findings)
    # H2 weakest floor < 60% -> concern
    assert "weakest certified detection-rate floor: Application" in texts
    assert any(f["severity"] == "concern" for f in findings)
    # H4 significant disparity -> concern, weakest Network
    assert "statistically significant" in texts
    assert "Network" in texts
