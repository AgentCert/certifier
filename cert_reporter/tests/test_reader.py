"""Unit tests for cert_reporter.pipeline.reader (preprocess_node + helpers)."""

from __future__ import annotations

import json

from cert_reporter.pipeline.reader import (
    _ensure_dicts,
    _extract_chart_blocks,
    preprocess_node,
)


class _Block:
    """Object exposing model_dump (Pydantic-like)."""

    def __init__(self, data):
        self._data = data

    def model_dump(self, mode="python"):
        return dict(self._data)


class _PlainObj:
    """Object exposing only __dict__."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestExtractChartBlocks:
    def test_assigns_ids_to_chart_blocks(self):
        sections = [
            {
                "id": "sec1",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "chart", "chart_type": "radar"},
                ],
            }
        ]
        charts = _extract_chart_blocks(sections)
        assert len(charts) == 1
        assert charts[0]["_chart_id"] == "sec1_radar_1"
        assert charts[0]["chart_type"] == "radar"

    def test_unknown_chart_type_label(self):
        sections = [{"id": "s", "content": [{"type": "chart"}]}]
        charts = _extract_chart_blocks(sections)
        assert charts[0]["_chart_id"] == "s_unknown_0"

    def test_converts_model_dump_block_in_place(self):
        block = _Block({"type": "chart", "chart_type": "line"})
        sections = [{"id": "s", "content": [block]}]
        charts = _extract_chart_blocks(sections)
        assert charts[0]["chart_type"] == "line"
        # Block was replaced by a dict in the section content
        assert isinstance(sections[0]["content"][0], dict)

    def test_converts_dunder_dict_block(self):
        obj = _PlainObj(type="chart", chart_type="bar")
        sections = [{"id": "s", "content": [obj]}]
        charts = _extract_chart_blocks(sections)
        assert charts[0]["chart_type"] == "bar"

    def test_skips_unconvertible_block(self):
        sections = [{"id": "s", "content": [42]}]
        assert _extract_chart_blocks(sections) == []

    def test_no_chart_blocks_returns_empty(self):
        sections = [{"id": "s", "content": [{"type": "text"}]}]
        assert _extract_chart_blocks(sections) == []


class TestEnsureDicts:
    def test_passthrough_plain_dicts(self):
        sections = [{"id": "s", "content": [{"type": "text"}]}]
        out = _ensure_dicts(sections)
        assert out == [{"id": "s", "content": [{"type": "text"}]}]
        # returns a copy, not the same object
        assert out[0] is not sections[0]

    def test_model_dump_section(self):
        sec = _Block({"id": "m", "content": [{"type": "text"}]})
        out = _ensure_dicts([sec])
        assert out[0]["id"] == "m"

    def test_skips_non_dict_non_model_section(self):
        assert _ensure_dicts([99]) == []

    def test_converts_content_block_variants(self):
        sec = {
            "id": "s",
            "content": [
                {"type": "a"},
                _Block({"type": "b"}),
                _PlainObj(type="c"),
                42,  # dropped
            ],
        }
        out = _ensure_dicts([sec])
        types = [b["type"] for b in out[0]["content"]]
        assert types == ["a", "b", "c"]


class TestPreprocessNode:
    def _write(self, tmp_path, payload):
        p = tmp_path / "report.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return str(p)

    def test_missing_input_path_errors(self):
        state = {"input_path": ""}
        out = preprocess_node(state)
        assert any("input_path not set" in e for e in out["errors"])

    def test_load_failure_errors(self, tmp_path):
        state = {"input_path": str(tmp_path / "nope.json")}
        out = preprocess_node(state)
        assert any("Failed to load JSON" in e for e in out["errors"])

    def test_happy_path_parses_and_extracts_charts(self, tmp_path):
        payload = {
            "meta": {"agent_name": "A"},
            "header": {"findings": []},
            "sections": [
                {
                    "id": "sec1",
                    "content": [
                        {"type": "text", "text": "x"},
                        {"type": "chart", "chart_type": "radar"},
                    ],
                }
            ],
            "footer": "the footer",
        }
        state = {"input_path": self._write(tmp_path, payload)}
        out = preprocess_node(state)

        assert out["errors"] == []
        assert out["meta"] == {"agent_name": "A"}
        assert out["header"] == {"findings": []}
        assert out["footer"] == "the footer"
        assert len(out["sections"]) == 1
        assert len(out["charts_to_render"]) == 1
        assert out["charts_to_render"][0]["_chart_id"] == "sec1_radar_1"
        # downstream fields initialised
        assert out["chart_results"] == {}
        assert out["enriched_sections"] == {}
        assert out["html_path"] == ""
        # raw_doc is the parsed JSON; with schema_class=None it shares section
        # objects with `sections`, so chart-block extraction tags it in place.
        assert out["raw_doc"]["meta"] == {"agent_name": "A"}
        assert out["raw_doc"]["footer"] == "the footer"

    def test_non_canonical_doc_defaults(self, tmp_path):
        # Missing meta/sections → normalise passthrough, then .get() defaults.
        payload = {"something": "else"}
        state = {"input_path": self._write(tmp_path, payload)}
        out = preprocess_node(state)
        assert out["meta"] == {}
        assert out["header"] == {}
        assert out["sections"] == []
        assert out["footer"] == ""
        assert out["charts_to_render"] == []

    def test_verbose_logs_no_crash(self, tmp_path):
        payload = {"meta": {}, "sections": [], "footer": ""}
        state = {"input_path": self._write(tmp_path, payload), "verbose": True}
        out = preprocess_node(state)
        assert out["errors"] == []
