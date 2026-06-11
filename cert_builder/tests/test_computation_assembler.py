"""Unit tests for cert_builder/scripts/computation/assembler.py."""

import json

import pytest

from utils.custom_errors import CertBuilderError, MyCustomError

from cert_builder.scripts.computation import assembler as asm
from cert_builder.tests._fixtures import make_category, make_meta


def _write_phase1(tmp_path):
    ctx = {
        "meta": make_meta(),
        "categories": [make_category()],
        "statistical_hypothesis": {"status": "not_requested"},
    }
    p = tmp_path / "phase1.json"
    p.write_text(json.dumps(ctx))
    return p


def test_run_builder_passthrough():
    assert asm._run_builder("ok", lambda x: x + 1, 1) == 2


def test_run_builder_wraps_generic_exception():
    def boom():
        raise ValueError("kaboom")
    with pytest.raises(CertBuilderError) as exc:
        asm._run_builder("bad", boom)
    assert "Computation builder 'bad' failed" in str(exc.value)


def test_run_builder_reraises_custom_error():
    def boom():
        raise MyCustomError("domain error")
    with pytest.raises(MyCustomError):
        asm._run_builder("bad", boom)


def test_assemble_merges_all_keys(tmp_path):
    p = _write_phase1(tmp_path)
    out = asm.ComputationAssembler(p).assemble()
    assert set(out) >= {
        "scorecard", "findings", "tables", "charts",
        "assessments", "hardcoded", "cards"}
    # spot-check a couple of nested structures survived the merge
    assert len(out["scorecard"]["dimensions"]) == 7
    assert "judge_models" in out["tables"]
    assert "Application" in out["assessments"]


def test_assemble_wraps_builder_failure(tmp_path):
    # Point assembler at a non-existent phase1 file -> first builder raises,
    # wrapped into CertBuilderError.
    bad = tmp_path / "missing.json"
    with pytest.raises(CertBuilderError):
        asm.ComputationAssembler(bad).assemble()
