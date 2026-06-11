"""Unit tests for main.workers.bucket_task_runner.

Tests the path-segment guard, the result-builder, and the run_task background
coroutine's status transitions and error capture with all I/O mocked
(SessionService, TraceService, BucketPipelineService).
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from main.models.bucket_requests import BucketingExtractionRequest
from main.services.trace_service import TraceIngestionError
from main.workers import bucket_task_runner as btr
from main.workers.bucket_task_runner import _build_result, _resolve_run_dir, run_task


# ── _resolve_run_dir ───────────────────────────────────────────────────────

class TestResolveRunDir:
    def test_creates_nested_path(self, tmp_path):
        out = _resolve_run_dir(tmp_path, "a", "e", "r")
        assert out == tmp_path / "a" / "e" / "fault-bucketing" / "r"
        assert out.is_dir()

    @pytest.mark.parametrize("seg", ["a/b", "a\\b", ".."])
    def test_rejects_traversal(self, tmp_path, seg):
        with pytest.raises(ValueError, match="Illegal path segment"):
            _resolve_run_dir(tmp_path, seg, "e", "r")


# ── _build_result ──────────────────────────────────────────────────────────

class TestBuildResult:
    def test_assembles_payload(self, tmp_path):
        results = [{
            "fault_id": "f1",
            "fault_name": "PodKill",
            "quantitative": {
                "injected_fault_category": "resource",
                "fault_detected": "Yes",
                "agent_fault_detection_time": 5,
                "agent_fault_mitigation_time": 10,
            },
        }]
        summary = {
            "bucketing_tokens": {"input": 1, "output": 2, "total": 3},
            "extraction_tokens": {"input": 4, "output": 5, "total": 9},
        }
        out = _build_result(results, summary, total_observations=42,
                            run_dir=tmp_path, elapsed=1.234)
        assert out["total_observations"] == 42
        assert out["total_faults_detected"] == 1
        assert out["faults"][0]["status"] == "closed"  # fault_detected == Yes
        assert out["faults"][0]["severity"] == "resource"
        assert out["token_usage"]["total_tokens"] == 12
        assert out["processing_time_seconds"] == 1.2

    def test_open_status_when_not_detected(self, tmp_path):
        results = [{"fault_id": "f1", "quantitative": {"fault_detected": "No"}}]
        out = _build_result(results, {}, 1, tmp_path, 0.0)
        assert out["faults"][0]["status"] == "open"
        assert out["faults"][0]["fault_name"] == "f1"  # falls back to fault_id


# ── run_task ───────────────────────────────────────────────────────────────

def _make_request(tmp_path, storage_type="local"):
    return BucketingExtractionRequest(
        agent_id="a", experiment_id="e", run_id="r",
        trace_source={"type": "file", "file_path": str(tmp_path / "t.json")},
        storage_config={"type": storage_type},
    )


def _make_settings(tmp_path):
    s = MagicMock()
    s.workspace_dir = tmp_path / "ws"
    return s


class TestRunTask:
    async def test_happy_path(self, tmp_path):
        req = _make_request(tmp_path)
        session = AsyncMock()
        trace = AsyncMock()
        trace.acquire_trace.return_value = (tmp_path / "raw.json", 7)
        pipeline = AsyncMock()
        pipeline.execute_pipeline.return_value = [{"fault_id": "f1", "quantitative": {}}]
        settings = _make_settings(tmp_path)

        # Make summary read succeed by writing pipeline_summary.json under the run dir.
        run_dir = settings.workspace_dir / "a" / "e" / "fault-bucketing" / "r"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "pipeline_summary.json").write_text('{"bucketing_tokens": {}, "extraction_tokens": {}}')

        await run_task(
            "task-1", req, session, trace, pipeline,
            asyncio.Semaphore(1), settings, {},
        )

        session.set_started.assert_awaited_once_with("task-1")
        session.update_stage.assert_awaited_once_with("task-1", "running_pipeline")
        session.set_completed.assert_awaited_once()
        completed_result = session.set_completed.await_args.args[1]
        assert completed_result["total_observations"] == 7
        session.set_failed.assert_not_awaited()

    async def test_trace_ingestion_error_sets_failed(self, tmp_path):
        req = _make_request(tmp_path)
        session = AsyncMock()
        trace = AsyncMock()
        trace.acquire_trace.side_effect = TraceIngestionError("TRACE_NOT_FOUND", "missing")
        pipeline = AsyncMock()
        settings = _make_settings(tmp_path)

        await run_task("t1", req, session, trace, pipeline,
                       asyncio.Semaphore(1), settings, {})

        session.set_failed.assert_awaited_once()
        code = session.set_failed.await_args.args[1]
        stage = session.set_failed.await_args.args[3]
        assert code == "TRACE_NOT_FOUND"
        assert stage == "acquiring_trace"
        pipeline.execute_pipeline.assert_not_awaited()

    async def test_generic_acquire_error_maps_to_trace_not_found(self, tmp_path):
        req = _make_request(tmp_path)
        session = AsyncMock()
        trace = AsyncMock()
        trace.acquire_trace.side_effect = RuntimeError("weird")
        settings = _make_settings(tmp_path)

        await run_task("t1", req, session, trace, AsyncMock(),
                       asyncio.Semaphore(1), settings, {})
        assert session.set_failed.await_args.args[1] == "TRACE_NOT_FOUND"

    async def test_pipeline_failure_sets_failed(self, tmp_path):
        req = _make_request(tmp_path)
        session = AsyncMock()
        trace = AsyncMock()
        trace.acquire_trace.return_value = (tmp_path / "raw.json", 3)
        pipeline = AsyncMock()
        pipeline.execute_pipeline.side_effect = RuntimeError("pipeline boom")
        settings = _make_settings(tmp_path)

        await run_task("t1", req, session, trace, pipeline,
                       asyncio.Semaphore(1), settings, {})

        session.set_failed.assert_awaited_once()
        assert session.set_failed.await_args.args[1] == "PIPELINE_FAILED"
        assert session.set_failed.await_args.args[3] == "running_pipeline"

    async def test_summary_read_error_sets_storage_error(self, tmp_path):
        # Pipeline succeeds but pipeline_summary.json is absent → STORAGE_ERROR.
        req = _make_request(tmp_path)
        session = AsyncMock()
        trace = AsyncMock()
        trace.acquire_trace.return_value = (tmp_path / "raw.json", 1)
        pipeline = AsyncMock()
        pipeline.execute_pipeline.return_value = []
        settings = _make_settings(tmp_path)
        # Do NOT create pipeline_summary.json

        await run_task("t1", req, session, trace, pipeline,
                       asyncio.Semaphore(1), settings, {})
        session.set_failed.assert_awaited_once()
        assert session.set_failed.await_args.args[1] == "STORAGE_ERROR"

    async def test_mongodb_mode_uses_tempdir_and_clears_paths(self, tmp_path, monkeypatch):
        req = _make_request(tmp_path, storage_type="mongodb")
        session = AsyncMock()
        trace = AsyncMock()

        captured = {}

        async def fake_acquire(source, dest, experiment_id="", run_id=""):
            # dest is <tempdir>/traces — write the summary one level up
            run_dir = dest.parent
            captured["run_dir"] = run_dir
            (run_dir / "pipeline_summary.json").write_text(
                '{"bucketing_tokens": {}, "extraction_tokens": {}}')
            return dest / "raw.json", 2

        trace.acquire_trace.side_effect = fake_acquire
        pipeline = AsyncMock()
        pipeline.execute_pipeline.return_value = []
        settings = _make_settings(tmp_path)

        await run_task("t1", req, session, trace, pipeline,
                       asyncio.Semaphore(1), settings, {})

        session.set_completed.assert_awaited_once()
        result = session.set_completed.await_args.args[1]
        assert result["storage_paths"] == {"storage_mode": "mongodb"}
        # temp dir cleaned up
        assert not captured["run_dir"].exists()
