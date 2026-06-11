"""Unit tests for cert_builder/scripts/narratives/assembler.py.

All 6 LLM-backed builders are patched with plain functions so no LLM call
happens; only the assembler's orchestration / merge / token-accounting
logic is exercised. asyncio_mode=auto (pytest.ini) runs async tests directly.
"""

import json

import pytest

from cert_builder.scripts.narratives import assembler as na
from cert_builder.scripts.narratives.assembler import NarrativeAssembler


# ── _accumulate_tokens_from_output ───────────────────────────────────

def test_accumulate_tokens_separate_breakdown():
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    output = {"scope_narrative": {"text": "x", "input_tokens": 10, "output_tokens": 4}}
    NarrativeAssembler._accumulate_tokens_from_output(output, totals)
    assert totals == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}


def test_accumulate_tokens_fallback_to_total():
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    output = {"key": {"text": "x", "tokens_used": 7}}
    NarrativeAssembler._accumulate_tokens_from_output(output, totals)
    assert totals["total_tokens"] == 7
    assert totals["input_tokens"] == 0


def test_accumulate_tokens_ignores_non_dict_values():
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    NarrativeAssembler._accumulate_tokens_from_output({"k": "string"}, totals)
    assert totals["total_tokens"] == 0


# ── _safe_call ───────────────────────────────────────────────────────

async def test_safe_call_success():
    phase_id, output, error = await na._safe_call("p1", lambda a, b: {"r": a + b}, 1, 2)
    assert phase_id == "p1"
    assert output == {"r": 3}
    assert error is None


async def test_safe_call_captures_exception():
    def boom():
        raise ValueError("nope")
    phase_id, output, error = await na._safe_call("p2", boom)
    assert output is None
    assert error == {"phase": "p2", "error": "nope"}


# ── assemble ─────────────────────────────────────────────────────────

def _patch_builders(monkeypatch, *, fail=None, fallback=False):
    """Patch every builder the assembler imported. Each returns a tiny dict
    whose key matches the merged-output convention."""
    fail = fail or set()

    def mk(name, payload):
        def fn(*args, **kwargs):
            if name in fail:
                raise RuntimeError(f"{name} broke")
            return payload
        return fn

    monkeypatch.setattr(na, "build_scope_narrative",
                        mk("scope", {"scope_narrative": {"text": "scope",
                                                         "input_tokens": 5, "output_tokens": 2}}))
    monkeypatch.setattr(na, "build_key_findings",
                        mk("key", {"key_findings": {"items": [], "input_tokens": 3, "output_tokens": 1}}))
    monkeypatch.setattr(na, "build_qualitative_findings",
                        mk("qual", {"qualitative_findings": {"reasoning": [],
                                                             "source": "fallback" if fallback else "llm"}}))
    monkeypatch.setattr(na, "build_fault_analysis",
                        mk("fault", {"fault_category_analysis": {}}))
    monkeypatch.setattr(na, "build_limitations",
                        mk("lim", {"limitations_enriched": {"items": ["L1"]}}))
    monkeypatch.setattr(na, "build_fairness_score",
                        mk("fair", {"fairness": {"score": 0.9}}))
    monkeypatch.setattr(na, "build_recommendations",
                        mk("rec", {"recommendations_enriched": {"items": ["R1"]}}))


async def test_assemble_merges_and_counts_tokens(tmp_path, monkeypatch):
    _patch_builders(monkeypatch)
    p1 = tmp_path / "p1.json"
    p2 = tmp_path / "p2.json"
    p1.write_text(json.dumps({"meta": {}}))
    p2.write_text(json.dumps({"charts": {}}))

    out = await NarrativeAssembler(p1, p2).assemble()
    assert out["scope_narrative"]["text"] == "scope"
    assert out["recommendations_enriched"]["items"] == ["R1"]
    assert out["fallbacks_used"] is False
    assert out["errors"] == []
    # phase_3_tokens summed from scope(5/2) + key(3/1)
    assert out["phase_3_tokens"]["input_tokens"] == 8
    assert out["phase_3_tokens"]["output_tokens"] == 3
    assert out["phase_3_tokens"]["total_tokens"] == 11


async def test_assemble_records_builder_errors(tmp_path, monkeypatch):
    _patch_builders(monkeypatch, fail={"fault"})
    p1 = tmp_path / "p1.json"
    p2 = tmp_path / "p2.json"
    p1.write_text("{}")
    p2.write_text("{}")
    out = await NarrativeAssembler(p1, p2).assemble()
    assert any(e["phase"] == "fault_analysis" for e in out["errors"])
    # the rest still merged
    assert "scope_narrative" in out


async def test_assemble_detects_fallback_usage(tmp_path, monkeypatch):
    _patch_builders(monkeypatch, fallback=True)
    p1 = tmp_path / "p1.json"
    p2 = tmp_path / "p2.json"
    p1.write_text("{}")
    p2.write_text("{}")
    out = await NarrativeAssembler(p1, p2).assemble()
    assert out["fallbacks_used"] is True


async def test_assemble_recommendations_get_limitations_dependency(tmp_path, monkeypatch):
    captured = {}

    def rec(phase1, phase2, limitations_enriched):
        captured["limitations"] = limitations_enriched
        return {"recommendations_enriched": {}}

    _patch_builders(monkeypatch)
    monkeypatch.setattr(na, "build_recommendations", rec)
    p1 = tmp_path / "p1.json"
    p2 = tmp_path / "p2.json"
    p1.write_text("{}")
    p2.write_text("{}")
    await NarrativeAssembler(p1, p2).assemble()
    # recommendations builder received the limitations_enriched from limitations builder
    assert captured["limitations"] == {"items": ["L1"]}
