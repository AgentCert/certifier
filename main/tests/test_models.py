"""Unit tests for the Pydantic request/response models in main.models.

Covers construction, validation rules, defaults, discriminated unions and
serialisation for the bucketing and certification request/response models.
"""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from main.models.bucket_requests import (
    BucketingExtractionRequest,
    FileTraceSource,
    LangfuseTraceSource,
    StorageConfig,
)
from main.models.bucket_responses import TaskAcceptedResponse, TaskStatusResponse
from main.models.cert_requests import (
    AggregationCertificationRequest,
    CertStorageConfig,
)
from main.models.cert_responses import (
    CertTaskAcceptedResponse,
    CertTaskStatusResponse,
)


# ── bucket_requests ────────────────────────────────────────────────────────

class TestTraceSources:
    def test_file_trace_source_minimal(self):
        src = FileTraceSource(type="file", file_path="/tmp/trace.json")
        assert src.type == "file"
        assert src.file_path == "/tmp/trace.json"

    def test_file_trace_source_requires_nonempty_path(self):
        with pytest.raises(ValidationError):
            FileTraceSource(type="file", file_path="")

    def test_langfuse_trace_source_defaults(self):
        src = LangfuseTraceSource(type="langfuse")
        assert src.page_size == 50
        assert src.max_pages == 10
        assert src.include_observations is True

    @pytest.mark.parametrize("field,bad", [
        ("page_size", 0),
        ("page_size", 501),
        ("max_pages", 0),
        ("max_pages", 101),
    ])
    def test_langfuse_bounds(self, field, bad):
        with pytest.raises(ValidationError):
            LangfuseTraceSource(type="langfuse", **{field: bad})


class TestStorageConfig:
    def test_defaults(self):
        cfg = StorageConfig()
        assert cfg.type == "local"
        assert cfg.container_name == ""

    def test_rejects_unknown_type(self):
        with pytest.raises(ValidationError):
            StorageConfig(type="s3")

    @pytest.mark.parametrize("t", ["local", "blob_storage", "mongodb", "hybrid"])
    def test_accepts_valid_types(self, t):
        assert StorageConfig(type=t).type == t


class TestBucketingExtractionRequest:
    def _base(self, **over):
        data = {
            "agent_id": "agent-1",
            "experiment_id": "exp-1",
            "run_id": "run-1",
            "trace_source": {"type": "file", "file_path": "/tmp/t.json"},
        }
        data.update(over)
        return data

    def test_minimal_construction_and_defaults(self):
        req = BucketingExtractionRequest(**self._base())
        assert req.llm_batch_size == 5
        assert isinstance(req.storage_config, StorageConfig)
        assert req.storage_config.type == "local"
        # Discriminated union resolved to the file variant
        assert isinstance(req.trace_source, FileTraceSource)

    def test_discriminator_selects_langfuse(self):
        req = BucketingExtractionRequest(
            **self._base(trace_source={"type": "langfuse", "page_size": 100})
        )
        assert isinstance(req.trace_source, LangfuseTraceSource)
        assert req.trace_source.page_size == 100

    def test_bad_discriminator_rejected(self):
        with pytest.raises(ValidationError):
            BucketingExtractionRequest(**self._base(trace_source={"type": "bogus"}))

    @pytest.mark.parametrize("field", ["agent_id", "experiment_id", "run_id"])
    @pytest.mark.parametrize("bad", ["a/b", "a\\b", "..", "x..y"])
    def test_path_separators_rejected(self, field, bad):
        with pytest.raises(ValidationError) as exc:
            BucketingExtractionRequest(**self._base(**{field: bad}))
        assert "path separators" in str(exc.value)

    def test_empty_ids_rejected(self):
        with pytest.raises(ValidationError):
            BucketingExtractionRequest(**self._base(agent_id=""))

    def test_too_long_id_rejected(self):
        with pytest.raises(ValidationError):
            BucketingExtractionRequest(**self._base(agent_id="x" * 129))

    @pytest.mark.parametrize("bad", [0, 51])
    def test_batch_size_bounds(self, bad):
        with pytest.raises(ValidationError):
            BucketingExtractionRequest(**self._base(llm_batch_size=bad))


# ── bucket_responses ───────────────────────────────────────────────────────

class TestBucketResponses:
    def test_task_accepted_defaults(self):
        resp = TaskAcceptedResponse(task_id="t1", poll_url="/poll")
        assert resp.status == "accepted"
        assert resp.model_dump() == {
            "status": "accepted",
            "task_id": "t1",
            "poll_url": "/poll",
        }

    def test_task_status_optional_fields(self):
        now = datetime.now(timezone.utc)
        resp = TaskStatusResponse(
            task_id="t1",
            status="RUNNING",
            stage="running_pipeline",
            agent_id="a",
            experiment_id="e",
            run_id="r",
            created_at=now,
            updated_at=now,
        )
        assert resp.started_at is None
        assert resp.completed_at is None
        assert resp.data is None
        assert resp.error is None

    def test_task_status_roundtrip(self):
        now = datetime.now(timezone.utc)
        resp = TaskStatusResponse(
            task_id="t1", status="COMPLETED", stage="done",
            agent_id="a", experiment_id="e", run_id="r",
            created_at=now, updated_at=now,
            data={"k": 1}, error={"error_code": "X"},
        )
        dumped = resp.model_dump()
        assert dumped["data"] == {"k": 1}
        assert dumped["error"]["error_code"] == "X"


# ── cert_requests ──────────────────────────────────────────────────────────

class TestCertStorageConfig:
    def test_defaults(self):
        cfg = CertStorageConfig()
        assert cfg.type == "local"
        assert cfg.metrics_dir == ""
        assert cfg.container_name == ""

    def test_rejects_unknown_type(self):
        with pytest.raises(ValidationError):
            CertStorageConfig(type="hybrid")  # not allowed for cert config

    def test_accepts_mongodb(self):
        assert CertStorageConfig(type="mongodb").type == "mongodb"


class TestAggregationCertificationRequest:
    def _base(self, **over):
        data = {
            "agent_id": "agent-1",
            "agent_name": "My Agent",
            "experiment_id": "exp-1",
        }
        data.update(over)
        return data

    def test_minimal_and_defaults(self):
        req = AggregationCertificationRequest(**self._base())
        assert req.certification_run_id == ""
        assert req.runs_per_fault == 30
        assert isinstance(req.storage_config, CertStorageConfig)
        assert req.storage_config.type == "local"

    @pytest.mark.parametrize("field", ["agent_id", "experiment_id"])
    @pytest.mark.parametrize("bad", ["a/b", "a\\b", ".."])
    def test_path_separators_rejected(self, field, bad):
        with pytest.raises(ValidationError) as exc:
            AggregationCertificationRequest(**self._base(**{field: bad}))
        assert "path separators" in str(exc.value)

    def test_agent_name_not_path_validated(self):
        # agent_name has no path-separator validator
        req = AggregationCertificationRequest(**self._base(agent_name="a/b name"))
        assert req.agent_name == "a/b name"

    @pytest.mark.parametrize("bad", [0, 1001])
    def test_runs_per_fault_bounds(self, bad):
        with pytest.raises(ValidationError):
            AggregationCertificationRequest(**self._base(runs_per_fault=bad))

    def test_missing_required_agent_name(self):
        with pytest.raises(ValidationError):
            AggregationCertificationRequest(agent_id="a", experiment_id="e")


# ── cert_responses ─────────────────────────────────────────────────────────

class TestCertResponses:
    def test_accepted_defaults(self):
        resp = CertTaskAcceptedResponse(cert_task_id="c1", poll_url="/p")
        assert resp.status == "accepted"
        assert resp.cert_task_id == "c1"

    def test_status_optionals(self):
        now = datetime.now(timezone.utc)
        resp = CertTaskStatusResponse(
            cert_task_id="c1", status="PENDING", stage="pending",
            agent_id="a", agent_name="n", experiment_id="e",
            certification_run_id="", created_at=now, updated_at=now,
        )
        assert resp.started_at is None
        assert resp.data is None
        assert resp.error is None
