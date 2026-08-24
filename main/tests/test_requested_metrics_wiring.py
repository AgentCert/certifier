"""End-to-end wiring tests for the ``requested_metrics`` parameter.

Covers three levels of the call chain:

1. HTTP router  — POST /api/v1/bucketing-extraction with requested_metrics in the
                  body → run_task() is enqueued with the correct request object.
2. Task runner  — run_task() passes request.requested_metrics to
                  pipeline_svc.execute_pipeline().
3. Pipeline svc — execute_pipeline(requested_metrics=...) passes it through to
                  run_extraction(requested=...), and only DeterministicGroup runs
                  when the requested set is a subset of its metrics.

MongoDB is not available in CI, so the HTTP and task-runner layers are tested
with mocked session/trace services (same pattern as test_routers.py and
test_bucket_task_runner.py).  The pipeline-service layer is exercised with the
real BucketPipelineService but with FaultBucketingPipeline mocked out so that
no LLM calls are made.
"""
import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from fastapi.testclient import TestClient

import main.main as main_mod
from main.models.bucket_requests import BucketingExtractionRequest
from main.routers import bucketing_extraction as buck_router
from main.services.pipeline_service import BucketPipelineService
from main.workers import bucket_task_runner as btr
from main.workers.bucket_task_runner import run_task

# ---------------------------------------------------------------------------
# Shared fixture: a real cached fault bucket with non-empty events
# ---------------------------------------------------------------------------

_BUCKET_PATH = Path(
    "/srv/projects/intern/cyril/ace-monorepo/certifier/baseline_output"
    "/phase0_scratch/2ce67cf3-e997-4c43-8451-fb79e4652868"
    "/raw_trace_sequential_bucket_pod-cpu-hog.json"
)


@pytest.fixture
def real_bucket_data():
    """Load a real fault bucket from the on-disk baseline cache."""
    return json.loads(_BUCKET_PATH.read_text())


# ---------------------------------------------------------------------------
# Level 1: HTTP router passes requested_metrics to run_task
# ---------------------------------------------------------------------------

@pytest.fixture
def app_state_for_router():
    """Minimal app.state population so the router handlers work without MongoDB."""
    app = main_mod.app
    app.state.task_col = MagicMock()
    app.state.cert_tasks_col = MagicMock()
    app.state.cert_meta_col = MagicMock()
    app.state.agg_cat_col = MagicMock()
    app.state.gridfs_bucket = None
    app.state.semaphore = asyncio.Semaphore(1)
    app.state.cert_semaphore = asyncio.Semaphore(1)
    app.state.config = {}
    settings = MagicMock()
    settings.workspace_dir = MagicMock()
    app.state.settings = settings
    yield app
    app.dependency_overrides.clear()


class TestHTTPLayerPassesRequestedMetrics:
    """Verify the router enqueues run_task with the full BucketingExtractionRequest
    object, so requested_metrics survives deserialization into the background task."""

    def test_requested_metrics_present_in_enqueued_request(
        self, app_state_for_router, monkeypatch
    ):
        """POST with requested_metrics → run_task sees request.requested_metrics."""
        client = TestClient(app_state_for_router)

        session_svc = AsyncMock()
        session_svc.find_active_task.return_value = None
        session_svc.create_task.return_value = None
        app_state_for_router.dependency_overrides[buck_router._session_svc] = (
            lambda: session_svc
        )

        captured_request = {}

        # Capture the `request` kwarg passed to run_task instead of running it
        def fake_run_task(**kwargs):
            captured_request.update(kwargs)

        monkeypatch.setattr(buck_router, "run_task", fake_run_task)

        body = {
            "agent_id": "agent-test",
            "experiment_id": "exp-1",
            "run_id": "run-1",
            "trace_source": {"type": "file", "file_path": "/tmp/trace.json"},
            "requested_metrics": ["input_tokens", "output_tokens"],
        }

        resp = client.post("/api/v1/bucketing-extraction", json=body)
        assert resp.status_code == 202, resp.text

        req: BucketingExtractionRequest = captured_request["request"]
        assert req.requested_metrics == ["input_tokens", "output_tokens"], (
            f"Expected ['input_tokens', 'output_tokens'], got {req.requested_metrics}"
        )

    def test_omitted_requested_metrics_is_none(
        self, app_state_for_router, monkeypatch
    ):
        """POST without requested_metrics → request.requested_metrics is None
        (backward-compatible default)."""
        client = TestClient(app_state_for_router)

        session_svc = AsyncMock()
        session_svc.find_active_task.return_value = None
        session_svc.create_task.return_value = None
        app_state_for_router.dependency_overrides[buck_router._session_svc] = (
            lambda: session_svc
        )

        captured_request = {}

        def fake_run_task(**kwargs):
            captured_request.update(kwargs)

        monkeypatch.setattr(buck_router, "run_task", fake_run_task)

        body = {
            "agent_id": "agent-test",
            "experiment_id": "exp-1",
            "run_id": "run-1",
            "trace_source": {"type": "file", "file_path": "/tmp/trace.json"},
            # requested_metrics intentionally omitted
        }

        resp = client.post("/api/v1/bucketing-extraction", json=body)
        assert resp.status_code == 202, resp.text

        req: BucketingExtractionRequest = captured_request["request"]
        assert req.requested_metrics is None, (
            f"Expected None (all metrics), got {req.requested_metrics}"
        )


# ---------------------------------------------------------------------------
# Level 2: run_task threads requested_metrics to execute_pipeline
# ---------------------------------------------------------------------------

def _make_settings(tmp_path):
    s = MagicMock()
    s.workspace_dir = tmp_path / "ws"
    return s


class TestRunTaskThreadsRequestedMetrics:
    """Verify run_task passes request.requested_metrics to
    pipeline_svc.execute_pipeline as the ``requested_metrics`` kwarg."""

    @pytest.mark.anyio
    async def test_filtered_metrics_passed_to_pipeline(self, tmp_path):
        req = BucketingExtractionRequest(
            agent_id="a", experiment_id="e", run_id="r",
            trace_source={"type": "file", "file_path": str(tmp_path / "t.json")},
            requested_metrics=["input_tokens", "output_tokens"],
        )
        session = AsyncMock()
        trace = AsyncMock()
        trace.acquire_trace.return_value = (tmp_path / "raw.json", 5)

        pipeline = AsyncMock()
        pipeline.execute_pipeline.return_value = []

        settings = _make_settings(tmp_path)
        run_dir = (
            settings.workspace_dir / "a" / "e" / "fault-bucketing" / "r"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "pipeline_summary.json").write_text(
            '{"bucketing_tokens": {}, "extraction_tokens": {}}'
        )

        await run_task(
            "task-filtered", req, session, trace, pipeline,
            asyncio.Semaphore(1), settings, {},
        )

        pipeline.execute_pipeline.assert_awaited_once()
        call_kwargs = pipeline.execute_pipeline.await_args.kwargs
        assert call_kwargs["requested_metrics"] == ["input_tokens", "output_tokens"], (
            f"execute_pipeline got requested_metrics={call_kwargs.get('requested_metrics')!r}"
        )

    @pytest.mark.anyio
    async def test_omitted_metrics_passes_none_to_pipeline(self, tmp_path):
        req = BucketingExtractionRequest(
            agent_id="a", experiment_id="e", run_id="r",
            trace_source={"type": "file", "file_path": str(tmp_path / "t.json")},
            # requested_metrics omitted → None
        )
        session = AsyncMock()
        trace = AsyncMock()
        trace.acquire_trace.return_value = (tmp_path / "raw.json", 5)

        pipeline = AsyncMock()
        pipeline.execute_pipeline.return_value = []

        settings = _make_settings(tmp_path)
        run_dir = (
            settings.workspace_dir / "a" / "e" / "fault-bucketing" / "r"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "pipeline_summary.json").write_text(
            '{"bucketing_tokens": {}, "extraction_tokens": {}}'
        )

        await run_task(
            "task-full", req, session, trace, pipeline,
            asyncio.Semaphore(1), settings, {},
        )

        pipeline.execute_pipeline.assert_awaited_once()
        call_kwargs = pipeline.execute_pipeline.await_args.kwargs
        assert call_kwargs["requested_metrics"] is None, (
            f"expected None (full run), got {call_kwargs.get('requested_metrics')!r}"
        )


# ---------------------------------------------------------------------------
# Level 3: execute_pipeline with real run_extraction
# ---------------------------------------------------------------------------

class TestExecutePipelineRequestedMetricsFiltering:
    """Exercise the real BucketPipelineService.execute_pipeline with:
    - Phase 0 (FaultBucketingPipeline) mocked to avoid LLM calls
    - Phase 1 (run_extraction) called for real to verify group filtering

    Verifies:
    - Filtered request: only DeterministicGroup metrics are returned, no LLM tokens used
    - Full request: run_extraction is called with requested=None
    """

    def _make_fake_bucket(self, bucket_data, fault_analyzer_module):
        """Build a real FaultBucket object from the cached bucket dict."""
        FaultBucket = fault_analyzer_module.FaultBucket
        return FaultBucket(
            fault_id=bucket_data["fault_id"],
            fault_name=bucket_data["fault_name"],
            severity=bucket_data.get("severity"),
            events=bucket_data["events"],
            experiment_id=bucket_data.get("experiment_id"),
            run_id=bucket_data.get("run_id"),
        )

    @pytest.mark.anyio
    async def test_filtered_metrics_only_deterministic_group_runs(
        self, tmp_path, real_bucket_data
    ):
        """requested_metrics=["input_tokens","output_tokens"] → only DeterministicGroup
        runs; no LLM tokens consumed; the returned metrics contain exactly those keys."""
        from fault_analyzer.schema import data_models as dm

        fake_bucket = self._make_fake_bucket(real_bucket_data, dm)

        trace_file = tmp_path / "trace.json"
        trace_file.write_text("[]")

        with patch(
            "main.services.pipeline_service.FaultBucketingPipeline"
        ) as mock_phase0:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(
                return_value={fake_bucket.fault_id: fake_bucket}
            )
            mock_instance.total_input_tokens = 0
            mock_instance.total_output_tokens = 0
            mock_phase0.return_value = mock_instance

            svc = BucketPipelineService()
            results = await svc.execute_pipeline(
                trace_file=str(trace_file),
                output_dir=str(tmp_path / "out"),
                batch_size=5,
                store_to_mongodb=False,
                agent_id="test-agent",
                requested_metrics=["input_tokens", "output_tokens"],
                config={},
            )

        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        r = results[0]

        # Deterministic metrics should be present
        quant = r["quantitative"]
        assert "input_tokens" in quant, f"input_tokens missing from {list(quant.keys())}"
        assert "output_tokens" in quant, f"output_tokens missing from {list(quant.keys())}"

        # The definitive proof that no LLM groups ran is zero token consumption.
        # LLM-only metrics (fault_detected, detection_success, etc.) are present
        # in the dump because LLMQuantitativeExtraction defines defaults for them
        # ("Unknown", None, …).  We do NOT assert they're absent — only that we
        # consumed zero LLM tokens.
        token_usage = r["token_usage"]
        assert token_usage["input_tokens"] == 0, (
            f"Expected 0 LLM input tokens for deterministic-only run, "
            f"got {token_usage['input_tokens']}"
        )
        assert token_usage["output_tokens"] == 0, (
            f"Expected 0 LLM output tokens for deterministic-only run, "
            f"got {token_usage['output_tokens']}"
        )

        # LLM-derived fields that KubernetesQuantitativeBatchGroup would populate
        # should be at their Pydantic default sentinel (None or "Unknown"), not a
        # real extracted value, since that group did not run.
        assert quant.get("fault_detected") in (None, "Unknown"), (
            f"fault_detected should be at its default, not an LLM-extracted value: "
            f"{quant.get('fault_detected')!r}"
        )
        assert quant.get("detection_success") is None, (
            f"detection_success should be None (SpanIdentificationGroup skipped): "
            f"{quant.get('detection_success')!r}"
        )

    @pytest.mark.anyio
    async def test_full_run_passes_requested_none_to_run_extraction(
        self, tmp_path, real_bucket_data
    ):
        """requested_metrics=None → run_extraction is called with requested=None
        (backward-compatible: all groups are eligible to run).

        We don't actually run the LLM groups — we spy on run_extraction instead
        so this test stays free of LLM calls.
        """
        from fault_analyzer.schema import data_models as dm
        from metrics_extractor.scripts.metric_groups import ExtractionResult
        from metrics_extractor import ExtractionResult as ExtractionResultPub

        fake_bucket = self._make_fake_bucket(real_bucket_data, dm)

        trace_file = tmp_path / "trace.json"
        trace_file.write_text("[]")

        # Minimal ExtractionResult so the pipeline can build its result dict
        from metrics_extractor.schema.metrics_model import (
            LLMQuantitativeExtraction,
            LLMQualitativeExtraction,
        )
        from metrics_extractor import TokenUsage

        stub_result = ExtractionResultPub(
            quantitative=LLMQuantitativeExtraction(),
            qualitative=LLMQualitativeExtraction(),
            token_usage=TokenUsage(input_tokens=0, output_tokens=0),
        )

        captured_kwargs = {}

        async def spy_run_extraction(**kwargs):
            captured_kwargs.update(kwargs)
            return stub_result

        with (
            patch("main.services.pipeline_service.FaultBucketingPipeline") as mock_phase0,
            patch("main.services.pipeline_service.run_extraction", spy_run_extraction),
        ):
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(
                return_value={fake_bucket.fault_id: fake_bucket}
            )
            mock_instance.total_input_tokens = 0
            mock_instance.total_output_tokens = 0
            mock_phase0.return_value = mock_instance

            svc = BucketPipelineService()
            results = await svc.execute_pipeline(
                trace_file=str(trace_file),
                output_dir=str(tmp_path / "out2"),
                batch_size=5,
                store_to_mongodb=False,
                agent_id="test-agent",
                requested_metrics=None,   # ← full run
                config={},
            )

        assert "requested" in captured_kwargs, (
            "run_extraction was not called (or did not receive 'requested' kwarg)"
        )
        assert captured_kwargs["requested"] is None, (
            f"Expected requested=None for full run, got {captured_kwargs['requested']!r}"
        )
