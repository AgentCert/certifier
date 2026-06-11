"""Unit tests for main.workers.cert_task_runner.

Tests the output-dir guard, error classifier, GridFS upload helper, the metadata
fan-out writers and the run_cert_task coroutine's stage transitions / error
capture with all I/O mocked.
"""
import asyncio
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from main.models.cert_requests import AggregationCertificationRequest
from main.workers import cert_task_runner as ctr
from main.workers.cert_task_runner import (
    _store_report_in_gridfs,
    _write_aggregated_category_metadata,
    classify_cert_error,
    resolve_cert_output_dir,
    run_cert_task,
)


# ── resolve_cert_output_dir ────────────────────────────────────────────────

class TestResolveCertOutputDir:
    def test_creates(self, tmp_path):
        out = resolve_cert_output_dir(tmp_path, "a", "e")
        assert out == tmp_path / "a" / "e"
        assert out.is_dir()

    @pytest.mark.parametrize("seg", ["a/b", "a\\b", ".."])
    def test_rejects_traversal(self, tmp_path, seg):
        with pytest.raises(ValueError, match="illegal characters"):
            resolve_cert_output_dir(tmp_path, seg, "e")


# ── classify_cert_error ────────────────────────────────────────────────────

class TestClassifyCertError:
    @pytest.mark.parametrize("msg,expected", [
        ("aggregation council failed", "AGGREGATION_FAILED"),
        ("scorecard invalid", "AGGREGATION_FAILED"),
        ("certification report broke", "CERT_GENERATION_FAILED"),
        ("storage write fail", "STORAGE_ERROR"),
        ("something totally else", "PIPELINE_FAILED"),
    ])
    def test_keyword_mapping(self, msg, expected):
        assert classify_cert_error(Exception(msg)) == expected

    def test_oserror_is_storage(self):
        assert classify_cert_error(OSError("disk")) == "STORAGE_ERROR"


# ── _store_report_in_gridfs ────────────────────────────────────────────────

class TestStoreReportInGridfs:
    async def test_uploads_and_returns_id(self, tmp_path):
        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF-data")
        bucket = MagicMock()
        bucket.upload_from_stream = AsyncMock(return_value="file-id-123")
        out = await _store_report_in_gridfs(bucket, f, "a", "e", "pdf")
        assert out == "file-id-123"
        kwargs = bucket.upload_from_stream.await_args.kwargs
        assert kwargs["metadata"]["format"] == "pdf"
        assert kwargs["metadata"]["content_type"] == "application/pdf"


# ── _write_aggregated_category_metadata ────────────────────────────────────

class TestWriteAggregatedCategoryMetadata:
    async def test_fans_out_per_category(self, tmp_path):
        agg_dir = tmp_path / "aggregation"
        agg_dir.mkdir()
        (agg_dir / "aggregation.json").write_text(json.dumps({
            "fault_category_scorecards": [
                {"fault_category": "resource", "total_runs": 5,
                 "faults_tested": ["PodKill"], "numeric_metrics": {},
                 "derived_metrics": {}},
                {"fault_category": "network", "total_runs": 3},
            ]
        }))
        col = AsyncMock()
        await _write_aggregated_category_metadata(col, "cert-id", "a", "e", tmp_path)
        col.insert_many.assert_awaited_once()
        docs = col.insert_many.await_args.args[0]
        assert len(docs) == 2
        assert docs[0]["fault_category"] == "resource"
        assert docs[0]["certification_id"] == "cert-id"

    async def test_no_scorecards_no_insert(self, tmp_path):
        agg_dir = tmp_path / "aggregation"
        agg_dir.mkdir()
        (agg_dir / "aggregation.json").write_text(json.dumps({}))
        col = AsyncMock()
        await _write_aggregated_category_metadata(col, "cid", "a", "e", tmp_path)
        col.insert_many.assert_not_awaited()


# ── run_cert_task ──────────────────────────────────────────────────────────

def _make_request(storage_type="local", metrics_dir=""):
    return AggregationCertificationRequest(
        agent_id="a", agent_name="Name", experiment_id="e",
        storage_config={"type": storage_type, "metrics_dir": metrics_dir},
    )


def _settings(tmp_path):
    s = MagicMock()
    s.workspace_dir = tmp_path / "ws"
    return s


def _write_pipeline_outputs(out_dir: Path):
    """Create the on-disk files run_cert_task reads after a successful pipeline."""
    (out_dir / "aggregation").mkdir(parents=True, exist_ok=True)
    (out_dir / "aggregation" / "aggregation.json").write_text(json.dumps({
        "fault_category_scorecards": [{"fault_category": "resource", "total_runs": 5}]
    }))
    (out_dir / "pipeline_summary.json").write_text(json.dumps({
        "total_documents": 5, "total_fault_categories": 1,
        "fault_categories": ["resource"],
    }))


class TestRunCertTask:
    async def test_happy_path(self, tmp_path, monkeypatch):
        req = _make_request()
        session = AsyncMock()
        pipeline = AsyncMock()
        pipeline.execute_pipeline.return_value = {"report": "data"}
        settings = _settings(tmp_path)
        cert_meta_col = AsyncMock()
        agg_cat_col = AsyncMock()

        out_dir = settings.workspace_dir / "a" / "e"

        async def fake_pipeline(**kwargs):
            _write_pipeline_outputs(out_dir)
            return {"report": "data"}

        pipeline.execute_pipeline.side_effect = fake_pipeline
        # report doc generation is a sync call wrapped in to_thread — stub it
        monkeypatch.setattr(ctr, "generate_cert_report_documents",
                            lambda *a, **k: {"html_path": str(out_dir / "r.html")})

        await run_cert_task(
            "c1", req, session, pipeline, asyncio.Semaphore(1),
            cert_meta_col, agg_cat_col, settings, {}, gridfs_bucket=None,
        )

        session.set_started.assert_awaited_once_with("c1")
        session.set_completed.assert_awaited_once()
        result = session.set_completed.await_args.args[1]
        assert result["total_documents"] == 5
        assert result["fault_categories"] == ["resource"]
        cert_meta_col.insert_one.assert_awaited_once()
        agg_cat_col.insert_many.assert_awaited_once()
        # stages observed
        stages = [c.args[1] for c in session.update_stage.await_args_list]
        assert "running_pipeline" in stages
        assert "storing_metadata" in stages

    async def test_invalid_path_segment_fails(self, tmp_path):
        # agent_id with a path separator would be rejected by Pydantic at the
        # router, but resolve_cert_output_dir guards again. Build a request that
        # bypasses validation by constructing the model and mutating the field.
        req = _make_request()
        object.__setattr__(req, "agent_id", "a/evil")
        session = AsyncMock()
        await run_cert_task(
            "c1", req, session, AsyncMock(), asyncio.Semaphore(1),
            AsyncMock(), AsyncMock(), _settings(tmp_path), {},
        )
        session.set_failed.assert_awaited_once()
        assert session.set_failed.await_args.args[1] == "INVALID_REQUEST"

    async def test_empty_result_sets_metrics_not_found(self, tmp_path):
        req = _make_request()
        session = AsyncMock()
        pipeline = AsyncMock()
        pipeline.execute_pipeline.return_value = {}  # no metrics
        await run_cert_task(
            "c1", req, session, pipeline, asyncio.Semaphore(1),
            AsyncMock(), AsyncMock(), _settings(tmp_path), {},
        )
        session.set_failed.assert_awaited_once()
        assert session.set_failed.await_args.args[1] == "METRICS_NOT_FOUND"

    async def test_pipeline_exception_classified(self, tmp_path):
        req = _make_request()
        session = AsyncMock()
        pipeline = AsyncMock()
        pipeline.execute_pipeline.side_effect = RuntimeError("aggregation council exploded")
        await run_cert_task(
            "c1", req, session, pipeline, asyncio.Semaphore(1),
            AsyncMock(), AsyncMock(), _settings(tmp_path), {},
        )
        session.set_failed.assert_awaited_once()
        assert session.set_failed.await_args.args[1] == "AGGREGATION_FAILED"
        assert session.set_failed.await_args.args[3] == "running_pipeline"

    async def test_metadata_write_failure_sets_storage_error(self, tmp_path, monkeypatch):
        req = _make_request()
        session = AsyncMock()
        pipeline = AsyncMock()
        settings = _settings(tmp_path)
        out_dir = settings.workspace_dir / "a" / "e"

        async def fake_pipeline(**kwargs):
            _write_pipeline_outputs(out_dir)
            return {"report": "data"}

        pipeline.execute_pipeline.side_effect = fake_pipeline
        monkeypatch.setattr(ctr, "generate_cert_report_documents", lambda *a, **k: {})
        cert_meta_col = AsyncMock()
        cert_meta_col.insert_one.side_effect = RuntimeError("db down")

        await run_cert_task(
            "c1", req, session, pipeline, asyncio.Semaphore(1),
            cert_meta_col, AsyncMock(), settings, {},
        )
        session.set_failed.assert_awaited_once()
        assert session.set_failed.await_args.args[1] == "STORAGE_ERROR"
        assert session.set_failed.await_args.args[3] == "storing_metadata"

    async def test_gridfs_upload_path(self, tmp_path, monkeypatch):
        req = _make_request(storage_type="mongodb")
        session = AsyncMock()
        pipeline = AsyncMock()
        settings = _settings(tmp_path)
        out_dir = settings.workspace_dir / "a" / "e"

        async def fake_pipeline(**kwargs):
            _write_pipeline_outputs(out_dir)
            return {"report": "data"}

        pipeline.execute_pipeline.side_effect = fake_pipeline
        html_file = out_dir / "report.html"

        def fake_gen(cert_json_path, report_output_dir):
            report_output_dir.mkdir(parents=True, exist_ok=True)
            html_file.write_text("<html></html>")
            return {"html_path": str(html_file)}

        monkeypatch.setattr(ctr, "generate_cert_report_documents", fake_gen)

        gridfs = MagicMock()
        gridfs.upload_from_stream = AsyncMock(return_value="gid-1")

        await run_cert_task(
            "c1", req, session, pipeline, asyncio.Semaphore(1),
            AsyncMock(), AsyncMock(), settings, {}, gridfs_bucket=gridfs,
        )

        session.set_completed.assert_awaited_once()
        result = session.set_completed.await_args.args[1]
        assert result["storage_paths"]["html_report"] == "gridfs:gid-1"
        assert result["storage_mode"] == "mongodb"
