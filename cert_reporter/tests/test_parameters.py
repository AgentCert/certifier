"""Unit tests for cert_reporter.pipeline.parameters models."""

from __future__ import annotations

from cert_reporter.pipeline.parameters import (
    ChartResult,
    LLMConfig,
    TokenUsage,
)


class TestTokenUsage:
    def test_defaults_zero(self):
        t = TokenUsage()
        assert t.input_tokens == 0
        assert t.output_tokens == 0
        assert t.total == 0

    def test_add_accumulates(self):
        t = TokenUsage()
        t.add(10, 5)
        t.add(3, 2)
        assert t.input_tokens == 13
        assert t.output_tokens == 7
        assert t.total == 20

    def test_total_is_sum(self):
        assert TokenUsage(input_tokens=4, output_tokens=6).total == 10


class TestChartResult:
    def test_required_and_defaults(self):
        cr = ChartResult(chart_id="c1", chart_type="radar", title="T")
        assert cr.chart_id == "c1"
        assert cr.svg == ""
        assert cr.alt_text == ""
        assert cr.width_px == 600
        assert cr.height_px == 400
        assert cr.error is None

    def test_explicit_values(self):
        cr = ChartResult(
            chart_id="c2", chart_type="line", title="L",
            svg="<svg/>", alt_text="alt", width_px=100, height_px=50,
            error="boom",
        )
        assert cr.svg == "<svg/>"
        assert cr.error == "boom"
        assert cr.width_px == 100


class TestLLMConfig:
    def test_defaults(self):
        c = LLMConfig()
        assert c.model == "gpt-4.1-mini"
        assert c.temperature == 0.4
        assert c.max_tokens == 4096
        assert c.provider == "openai"

    def test_overrides(self):
        c = LLMConfig(model="gpt-4o", temperature=0.0, provider="anthropic")
        assert c.model == "gpt-4o"
        assert c.temperature == 0.0
        assert c.provider == "anthropic"
