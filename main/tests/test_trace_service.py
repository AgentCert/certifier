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

    async def test_trace_id_threaded_to_fetch(self, tmp_path, monkeypatch):
        # source.trace_id must reach _fetch_langfuse_observations as the
        # trace_id kwarg -- this is what lets agent-sidecar-instrumented runs
        # (which set Langfuse trace_id = NOTIFY_ID but never write
        # experiment_id/experiment_run_id metadata) be looked up at all.
        monkeypatch.setenv("LANGFUSE_HOST", "http://lf")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

        captured = {}

        def fake_fetch(**kwargs):
            captured.update(kwargs)
            return [{"id": "obs-1", "startTime": "2024-01-01T00:00:00Z"}]

        monkeypatch.setattr(ts, "_fetch_langfuse_observations", fake_fetch)
        svc = TraceService()
        source = LangfuseTraceSource(type="langfuse", trace_id="notify-abc-123")
        await svc.acquire_trace(source, tmp_path / "out", experiment_id="e", run_id="r")
        assert captured["trace_id"] == "notify-abc-123"

    async def test_missing_trace_id_defaults_to_empty_string(self, tmp_path, monkeypatch):
        # A LangfuseTraceSource with no trace_id set must thread through "" (falsy),
        # not None, so _fetch_langfuse_observations's `if trace_id:` fallback branch
        # stays simple.
        monkeypatch.setenv("LANGFUSE_HOST", "http://lf")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

        captured = {}

        def fake_fetch(**kwargs):
            captured.update(kwargs)
            return [{"id": "obs-1", "startTime": "2024-01-01T00:00:00Z"}]

        monkeypatch.setattr(ts, "_fetch_langfuse_observations", fake_fetch)
        svc = TraceService()
        source = LangfuseTraceSource(type="langfuse")
        await svc.acquire_trace(source, tmp_path / "out", experiment_id="e", run_id="r")
        assert captured["trace_id"] == ""


# ── _fetch_langfuse_observations / _get_full_trace (trace_id direct lookup) ───

class _FakeTrace:
    def __init__(self, id, observations=None):
        self.id = id
        self.observations = observations or []


class _FakeObservation:
    def __init__(self, **kwargs):
        self._data = kwargs

    def dict(self):
        return dict(self._data)


class _FakeTraceAPI:
    def __init__(self, traces_by_id=None):
        self._traces_by_id = traces_by_id or {}
        self.get_calls = []

    def get(self, trace_id):
        self.get_calls.append(trace_id)
        if trace_id not in self._traces_by_id:
            raise RuntimeError("404 not found")
        return self._traces_by_id[trace_id]

    def list(self, filter, page, limit):
        raise AssertionError("trace.list() must not be called when trace_id is given")


class _FakeLangfuseClient:
    def __init__(self, trace_api):
        self.api = type("_Api", (), {"trace": trace_api})()


class TestFetchLangfuseObservationsByTraceId:
    def test_direct_lookup_bypasses_metadata_filter(self, monkeypatch):
        obs = _FakeObservation(id="obs-1", startTime="2024-01-01T00:00:00Z")
        trace = _FakeTrace(id="notify-123", observations=[obs])
        trace_api = _FakeTraceAPI(traces_by_id={"notify-123": trace})
        monkeypatch.setattr("langfuse.Langfuse", lambda **kwargs: _FakeLangfuseClient(trace_api))

        result = ts._fetch_langfuse_observations(
            base_url="http://lf", public_key="pk", secret_key="sk",
            experiment_id="", run_id="", trace_id="notify-123",
            page_size=50, max_pages=10, include_observations=True,
        )
        assert [o["id"] for o in result] == ["obs-1"]
        assert trace_api.get_calls == ["notify-123"]

    def test_direct_lookup_not_found_raises_trace_not_found(self, monkeypatch):
        trace_api = _FakeTraceAPI(traces_by_id={})
        monkeypatch.setattr("langfuse.Langfuse", lambda **kwargs: _FakeLangfuseClient(trace_api))

        with pytest.raises(TraceIngestionError) as e:
            ts._fetch_langfuse_observations(
                base_url="http://lf", public_key="pk", secret_key="sk",
                experiment_id="", run_id="", trace_id="missing-id",
                page_size=50, max_pages=10, include_observations=True,
            )
        assert e.value.error_code == "TRACE_NOT_FOUND"

    def test_include_observations_false_skips_body(self, monkeypatch):
        trace = _FakeTrace(id="notify-123", observations=[_FakeObservation(id="obs-1")])
        trace_api = _FakeTraceAPI(traces_by_id={"notify-123": trace})
        monkeypatch.setattr("langfuse.Langfuse", lambda **kwargs: _FakeLangfuseClient(trace_api))

        result = ts._fetch_langfuse_observations(
            base_url="http://lf", public_key="pk", secret_key="sk",
            experiment_id="", run_id="", trace_id="notify-123",
            page_size=50, max_pages=10, include_observations=False,
        )
        assert result == []

    def test_empty_trace_id_falls_back_to_metadata_filter(self, monkeypatch):
        called = {}

        def fake_list_traces(client, experiment_id, run_id, page_size, max_pages):
            called["experiment_id"] = experiment_id
            called["run_id"] = run_id
            return []

        monkeypatch.setattr(ts, "_list_traces", fake_list_traces)
        monkeypatch.setattr(
            "langfuse.Langfuse", lambda **kwargs: _FakeLangfuseClient(_FakeTraceAPI())
        )

        with pytest.raises(TraceIngestionError) as e:
            ts._fetch_langfuse_observations(
                base_url="http://lf", public_key="pk", secret_key="sk",
                experiment_id="exp-1", run_id="run-1", trace_id="",
                page_size=50, max_pages=10, include_observations=True,
            )
        assert called == {"experiment_id": "exp-1", "run_id": "run-1"}
        assert e.value.error_code == "TRACE_NOT_FOUND"
        assert "exp-1" in str(e.value)


class TestGetFullTrace:
    def test_returns_trace_on_success(self):
        trace_api = _FakeTraceAPI(traces_by_id={"t1": _FakeTrace(id="t1")})
        client = _FakeLangfuseClient(trace_api)
        result = ts._get_full_trace(client, "t1")
        assert result.id == "t1"

    def test_returns_none_on_any_exception(self):
        trace_api = _FakeTraceAPI(traces_by_id={})
        client = _FakeLangfuseClient(trace_api)
        assert ts._get_full_trace(client, "missing") is None
