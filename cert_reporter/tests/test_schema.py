"""Unit tests for cert_reporter.pipeline.schema.

Covers the deterministic document-normalisation helpers. No LLM, no I/O.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from cert_reporter.pipeline.schema import _is_framework_format, normalise_document


class _Doc(BaseModel):
    meta: dict
    sections: list
    footer: str = ""


class TestIsFrameworkFormat:
    def test_true_when_meta_and_sections_present(self):
        assert _is_framework_format({"meta": {}, "sections": []}) is True

    def test_false_when_meta_missing(self):
        assert _is_framework_format({"sections": []}) is False

    def test_false_when_sections_missing(self):
        assert _is_framework_format({"meta": {}}) is False

    def test_false_for_empty_dict(self):
        assert _is_framework_format({}) is False


class TestNormaliseDocument:
    def test_non_framework_passthrough(self):
        raw = {"hello": "world"}
        # Even with a schema class, a non-canonical doc is returned untouched.
        assert normalise_document(raw, schema_class=_Doc) is raw

    def test_schema_none_returns_raw_unchanged(self):
        raw = {"meta": {"a": 1}, "sections": []}
        assert normalise_document(raw, schema_class=None) is raw

    def test_valid_schema_validates_and_dumps(self):
        raw = {"meta": {"agent": "x"}, "sections": [{"id": "s1"}], "footer": "f"}
        out = normalise_document(raw, schema_class=_Doc)
        # model_validate + model_dump roundtrip — a plain dict, not the input obj
        assert out == {"meta": {"agent": "x"}, "sections": [{"id": "s1"}], "footer": "f"}
        assert out is not raw

    def test_validation_failure_falls_back_to_raw(self):
        # `sections` should be a list; passing an int makes validation fail.
        raw: dict[str, Any] = {"meta": {}, "sections": 123}
        out = normalise_document(raw, schema_class=_Doc)
        assert out is raw

    def test_default_schema_class_is_none(self):
        raw = {"meta": {}, "sections": []}
        assert normalise_document(raw) is raw
