"""Unit tests for main.services.trace_service.

Covers the pure normalisation helpers (_fmt_ts, _to_json_str, _compute_depths,
_format_observations, _load_and_validate) and the async TraceService.acquire_trace
file path (copy + validate) plus the langfuse credential pre-flight. No network.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from main.services import trace_service as ts
from main.services.trace_service import (
    TraceIngestionError,
    TraceService,
    _compute_depths,
    _fmt_ts,
    _format_observations,
    _load_and_validate,
    _to_json_str,
)

# trace_service uses duck typing on the trace_source object; the concrete
# discriminated-union models live in main.models.bucket_requests.
from main.models.bucket_requests import FileTraceSource, LangfuseTraceSource


# ── _fmt_ts ────────────────────────────────────────────────────────────────

class TestFmtTs:
    def test_none(self):
        assert _fmt_ts(None) is None

    def test_datetime_millis(self):
        dt = datetime(2024, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
        assert _fmt_ts(dt) == "2024-01-02T03:04:05.123Z"

    def test_iso_string_with_z(self):
        assert _fmt_ts("2024-01-02T03:04:05.500Z") == "2024-01-02T03:04:05.500Z"

    def test_unparseable_returned_unchanged(self):
        assert _fmt_ts("not-a-date") == "not-a-date"


# ── _to_json_str ───────────────────────────────────────────────────────────

class TestToJsonStr:
    def test_none(self):
        assert _to_json_str(None) is None

    def test_string_passthrough(self):
        assert _to_json_str("already") == "already"

    def test_dict_serialised(self):
        assert _to_json_str({"a": 1}) == '{"a": 1}'


# ── _compute_depths ────────────────────────────────────────────────────────

class TestComputeDepths:
    def test_root_and_children(self):
        obs = [
            {"id": "root"},
            {"id": "child", "parentObservationId": "root"},
            {"id": "grand", "parentObservationId": "child"},
        ]
        depths = _compute_depths(obs)
        assert depths == {"root": 0, "child": 1, "grand": 2}

    def test_unknown_parent_is_root(self):
        obs = [{"id": "a", "parentObservationId": "missing"}]
        assert _compute_depths(obs) == {"a": 0}

    def test_snake_case_parent_key(self):
        obs = [{"id": "root"}, {"id": "c", "parent_observation_id": "root"}]
        assert _compute_depths(obs) == {"root": 0, "c": 1}


# ── _format_observations ───────────────────────────────────────────────────

class TestFormatObservations:
    def test_sorted_by_start_time_and_fields(self):
        raw = [
            {"id": "b", "startTime": "2024-01-01T00:00:02Z", "type": "SPAN"},
            {"id": "a", "startTime": "2024-01-01T00:00:01Z", "type": "GENERATION",
             "usage": {"total": 5}},
        ]
        out = _format_observations(raw)
        assert [o["id"] for o in out] == ["a", "b"]
        assert out[0]["usage"] == '{"total": 5}'  # dict serialised to JSON str
        assert out[0]["depth"] == 0

    def test_empty(self):
        assert _format_observations([]) == []


# ── _load_and_validate ─────────────────────────────────────────────────────

class TestLoadAndValidate:
    def test_valid(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(json.dumps([{"id": "x"}]))
        assert _load_and_validate(str(p)) == [{"id": "x"}]

    def test_not_a_list(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(json.dumps({"id": "x"}))
        with pytest.raises(TraceIngestionError) as e:
            _load_and_validate(str(p))
        assert e.value.error_code == "TRACE_PARSE_ERROR"

    def test_empty_list(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text("[]")
        with pytest.raises(TraceIngestionError) as e:
            _load_and_validate(str(p))
        assert e.value.error_code == "TRACE_PARSE_ERROR"

    def test_missing_id_field(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(json.dumps([{"no_id": 1}]))
        with pytest.raises(TraceIngestionError) as e:
            _load_and_validate(str(p))
        assert "id" in str(e.value)


# ── TraceService.acquire_trace (file source) ───────────────────────────────

class TestAcquireTraceFile:
    async def test_copies_and_counts(self, tmp_path):
        src = tmp_path / "src.json"
        src.write_text(json.dumps([{"id": "a"}, {"id": "b"}]))
        dest_dir = tmp_path / "out"
        svc = TraceService()
        source = FileTraceSource(type="file", file_path=str(src))

        path, count = await svc.acquire_trace(source, dest_dir)
        assert path == dest_dir / "raw_trace.json"
        assert path.exists()
        assert count == 2

    async def test_missing_file_raises_trace_not_found(self, tmp_path):
        svc = TraceService()
        source = FileTraceSource(type="file", file_path=str(tmp_path / "nope.json"))
        with pytest.raises(TraceIngestionError) as e:
            await svc.acquire_trace(source, tmp_path / "out")
        assert e.value.error_code == "TRACE_NOT_FOUND"


# ── TraceService langfuse credential pre-flight ────────────────────────────

class TestAcquireTraceLangfuse:
    async def test_missing_credentials_raises(self, tmp_path, monkeypatch):
        for var in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            monkeypatch.delenv(var, raising=False)
        svc = TraceService()
        source = LangfuseTraceSource(type="langfuse")
        with pytest.raises(TraceIngestionError) as e:
            await svc.acquire_trace(source, tmp_path / "out",
                                    experiment_id="e", run_id="r")
        assert e.value.error_code == "LANGFUSE_FETCH_ERROR"
        assert "LANGFUSE_HOST" in str(e.value)

    async def test_fetch_writes_and_validates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LANGFUSE_HOST", "http://lf")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

        # Patch the synchronous fetch helper so no SDK / network is touched.
        def fake_fetch(**kwargs):
            return [{"id": "obs-1", "startTime": "2024-01-01T00:00:00Z"}]

        monkeypatch.setattr(ts, "_fetch_langfuse_observations", fake_fetch)
        svc = TraceService()
        source = LangfuseTraceSource(type="langfuse")
        path, count = await svc.acquire_trace(
            source, tmp_path / "out", experiment_id="e", run_id="r"
        )
        assert count == 1
        data = json.loads(path.read_text())
        assert data[0]["id"] == "obs-1"

    async def test_fetch_exception_wrapped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LANGFUSE_HOST", "http://lf")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

        def boom(**kwargs):
            raise RuntimeError("sdk exploded")

        monkeypatch.setattr(ts, "_fetch_langfuse_observations", boom)
        svc = TraceService()
        source = LangfuseTraceSource(type="langfuse")
        with pytest.raises(TraceIngestionError) as e:
            await svc.acquire_trace(source, tmp_path / "out",
                                    experiment_id="e", run_id="r")
        assert e.value.error_code == "LANGFUSE_FETCH_ERROR"
        assert "sdk exploded" in str(e.value)

    async def test_trace_ingestion_error_passthrough(self, tmp_path, monkeypatch):
        # A TraceIngestionError raised inside the fetch helper must propagate
        # with its original error_code (not be re-wrapped as LANGFUSE_FETCH_ERROR).
        monkeypatch.setenv("LANGFUSE_HOST", "http://lf")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

        def raise_not_found(**kwargs):
            raise TraceIngestionError("TRACE_NOT_FOUND", "nothing in langfuse")

        monkeypatch.setattr(ts, "_fetch_langfuse_observations", raise_not_found)
        svc = TraceService()
        source = LangfuseTraceSource(type="langfuse")
        with pytest.raises(TraceIngestionError) as e:
            await svc.acquire_trace(source, tmp_path / "out",
                                    experiment_id="e", run_id="r")
        assert e.value.error_code == "TRACE_NOT_FOUND"
