"""Integration-style tests for the FastAPI routers using TestClient.

The app is imported from main.main but its lifespan (which opens a real Motor
connection and creates indexes) is NOT run — TestClient is used without the
context-manager form, so no DB connection is opened. app.state is populated
manually with mocks, and the service-layer dependencies are overridden with
AsyncMock objects. Background workers (run_task / run_cert_task) are patched so
they never execute the real pipeline.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import main.main as main_mod
from main.routers import aggregation_certification as agg_router
from main.routers import bucketing_extraction as buck_router
from main.routers import certification_reports as rep_router


@pytest.fixture
def app_state():
    """Populate app.state with mocks so handlers that read it don't hit a DB."""
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


@pytest.fixture
def client(app_state):
    # No `with` → lifespan is not invoked → no Motor connection.
    return TestClient(app_state)


# ── POST /api/v1/bucketing-extraction ──────────────────────────────────────

class TestBucketingExtraction:
    def _body(self, **over):
        b = {
            "agent_id": "a", "experiment_id": "e", "run_id": "r",
            "trace_source": {"type": "file", "file_path": "/tmp/t.json"},
        }
        b.update(over)
        return b

    def test_success_returns_202(self, app_state, client, monkeypatch):
        svc = AsyncMock()
        svc.find_active_task.return_value = None
        svc.create_task.return_value = None
        app_state.dependency_overrides[buck_router._session_svc] = lambda: svc
        # Stop the real background worker from running
        monkeypatch.setattr(buck_router, "run_task", AsyncMock())

        r = client.post("/api/v1/bucketing-extraction", json=self._body())
        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "accepted"
        assert "task_id" in data
        assert "poll_url" in data
        svc.create_task.assert_awaited_once()

    def test_duplicate_active_returns_409(self, app_state, client):
        svc = AsyncMock()
        svc.find_active_task.return_value = {
            "task_id": "existing", "status": "RUNNING", "stage": "running_pipeline",
        }
        app_state.dependency_overrides[buck_router._session_svc] = lambda: svc
        r = client.post("/api/v1/bucketing-extraction", json=self._body())
        assert r.status_code == 409
        assert r.json()["detail"]["error_code"] == "TASK_ALREADY_ACTIVE"

    def test_bad_body_returns_422(self, app_state, client):
        r = client.post("/api/v1/bucketing-extraction",
                        json=self._body(agent_id="a/b"))
        assert r.status_code == 422

    def test_missing_field_returns_422(self, app_state, client):
        r = client.post("/api/v1/bucketing-extraction", json={"agent_id": "a"})
        assert r.status_code == 422


# ── GET /api/v1/tasks ──────────────────────────────────────────────────────

class TestGetTaskStatus:
    def test_found(self, app_state, client):
        svc = AsyncMock()
        svc.get_task_by_run.return_value = {"task_id": "t1", "status": "COMPLETED"}
        app_state.dependency_overrides[buck_router._session_svc] = lambda: svc
        r = client.get("/api/v1/tasks", params={"experiment_id": "e",
                                                "experiment_run_id": "r"})
        assert r.status_code == 200
        assert r.json()["task_id"] == "t1"

    def test_not_found_returns_404(self, app_state, client):
        svc = AsyncMock()
        svc.get_task_by_run.return_value = None
        app_state.dependency_overrides[buck_router._session_svc] = lambda: svc
        r = client.get("/api/v1/tasks", params={"experiment_id": "e",
                                                "experiment_run_id": "r"})
        assert r.status_code == 404
        assert r.json()["detail"]["error_code"] == "TASK_NOT_FOUND"

    def test_missing_query_param_422(self, app_state, client):
        r = client.get("/api/v1/tasks", params={"experiment_id": "e"})
        assert r.status_code == 422


# ── POST /api/v1/aggregation-certification ─────────────────────────────────

class TestAggregationCertification:
    def _body(self, **over):
        b = {"agent_id": "a", "agent_name": "Name", "experiment_id": "e",
             "storage_config": {"type": "mongodb"}}
        b.update(over)
        return b

    def test_mongodb_success_returns_202(self, app_state, client, monkeypatch):
        svc = AsyncMock()
        svc.find_active_task.return_value = None
        svc.create_task.return_value = None
        app_state.dependency_overrides[agg_router._cert_session_svc] = lambda: svc
        monkeypatch.setattr(agg_router, "run_cert_task", AsyncMock())
        r = client.post("/api/v1/aggregation-certification", json=self._body())
        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "accepted"
        assert "cert_task_id" in data

    def test_local_metrics_validation_failure_returns_400(self, app_state, client, monkeypatch):
        svc = AsyncMock()
        app_state.dependency_overrides[agg_router._cert_session_svc] = lambda: svc

        def boom(metrics_dir, agent_id):
            raise agg_router.MetricsValidationError(
                "METRICS_NOT_FOUND", "no metrics here")

        monkeypatch.setattr(agg_router, "_discover_and_validate", boom)
        body = self._body(storage_config={"type": "local", "metrics_dir": "/tmp/m"})
        r = client.post("/api/v1/aggregation-certification", json=body)
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "METRICS_NOT_FOUND"

    def test_local_success_passes_validation(self, app_state, client, monkeypatch):
        svc = AsyncMock()
        svc.find_active_task.return_value = None
        svc.create_task.return_value = None
        app_state.dependency_overrides[agg_router._cert_session_svc] = lambda: svc
        monkeypatch.setattr(agg_router, "_discover_and_validate", lambda md, aid: 3)
        monkeypatch.setattr(agg_router, "run_cert_task", AsyncMock())
        body = self._body(storage_config={"type": "local", "metrics_dir": "/tmp/m"})
        r = client.post("/api/v1/aggregation-certification", json=body)
        assert r.status_code == 202

    def test_duplicate_active_returns_409(self, app_state, client):
        svc = AsyncMock()
        svc.find_active_task.return_value = {
            "cert_task_id": "x", "status": "PENDING", "stage": "pending"}
        app_state.dependency_overrides[agg_router._cert_session_svc] = lambda: svc
        r = client.post("/api/v1/aggregation-certification", json=self._body())
        assert r.status_code == 409
        assert r.json()["detail"]["error_code"] == "TASK_ALREADY_ACTIVE"

    def test_create_task_failure_returns_500(self, app_state, client, monkeypatch):
        svc = AsyncMock()
        svc.find_active_task.return_value = None
        svc.create_task.side_effect = RuntimeError("db down")
        app_state.dependency_overrides[agg_router._cert_session_svc] = lambda: svc
        monkeypatch.setattr(agg_router, "run_cert_task", AsyncMock())
        r = client.post("/api/v1/aggregation-certification", json=self._body())
        assert r.status_code == 500
        assert r.json()["detail"]["error_code"] == "MONGODB_ERROR"

    def test_bad_body_422(self, app_state, client):
        r = client.post("/api/v1/aggregation-certification",
                        json={"agent_id": "a"})  # missing agent_name/experiment_id
        assert r.status_code == 422


# ── _discover_and_validate / _extract_agent_id_from_doc helpers ────────────

class TestDiscoverAndValidate:
    def test_extract_agent_id_top_level(self):
        assert agg_router._extract_agent_id_from_doc({"agent_id": "a"}) == "a"

    def test_extract_agent_id_nested(self):
        assert agg_router._extract_agent_id_from_doc(
            {"quantitative": {"agent_id": "b"}}) == "b"

    def test_extract_agent_id_none(self):
        assert agg_router._extract_agent_id_from_doc({"quantitative": {}}) is None

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(agg_router.MetricsValidationError) as e:
            agg_router._discover_and_validate(str(tmp_path / "nope"), "a")
        assert e.value.error_code == "METRICS_NOT_FOUND"

    def test_no_metrics_files_raises(self, tmp_path):
        with pytest.raises(agg_router.MetricsValidationError):
            agg_router._discover_and_validate(str(tmp_path), "a")

    def test_no_matching_agent_raises(self, tmp_path):
        (tmp_path / "x_metrics.json").write_text('{"agent_id": "other"}')
        with pytest.raises(agg_router.MetricsValidationError):
            agg_router._discover_and_validate(str(tmp_path), "a")

    def test_counts_matching_docs(self, tmp_path):
        (tmp_path / "a_metrics.json").write_text('{"agent_id": "a"}')
        (tmp_path / "b_metrics.json").write_text('[{"agent_id": "a"}, {"agent_id": "z"}]')
        # malformed file is skipped, not fatal
        (tmp_path / "bad_metrics.json").write_text("{not json")
        assert agg_router._discover_and_validate(str(tmp_path), "a") == 2


# ── GET /api/v1/cert-tasks ─────────────────────────────────────────────────

class TestGetCertTaskStatus:
    def test_found(self, app_state, client):
        svc = AsyncMock()
        svc.get_task_by_experiment.return_value = {"cert_task_id": "c1",
                                                   "status": "RUNNING"}
        app_state.dependency_overrides[agg_router._cert_session_svc] = lambda: svc
        r = client.get("/api/v1/cert-tasks", params={"experiment_id": "e"})
        assert r.status_code == 200
        assert r.json()["cert_task_id"] == "c1"

    def test_not_found_404(self, app_state, client):
        svc = AsyncMock()
        svc.get_task_by_experiment.return_value = None
        app_state.dependency_overrides[agg_router._cert_session_svc] = lambda: svc
        r = client.get("/api/v1/cert-tasks", params={"experiment_id": "e"})
        assert r.status_code == 404
        assert r.json()["detail"]["error_code"] == "TASK_NOT_FOUND"


# ── certification_reports endpoints ────────────────────────────────────────

class TestCertificationReports:
    def test_pdf_filesystem_fallback(self, app_state, client, tmp_path):
        # gridfs_bucket is None → fall back to filesystem
        cert_dir = tmp_path / "a" / "e" / "certification"
        cert_dir.mkdir(parents=True)
        pdf = cert_dir / "report.pdf"
        pdf.write_bytes(b"%PDF-1.4 data")
        app_state.state.settings.workspace_dir = tmp_path
        r = client.get("/api/v1/certification/pdf",
                       params={"agent_id": "a", "experiment_id": "e"})
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"

    def test_pdf_not_found_404(self, app_state, client, tmp_path):
        app_state.state.settings.workspace_dir = tmp_path
        r = client.get("/api/v1/certification/pdf",
                       params={"agent_id": "a", "experiment_id": "missing"})
        assert r.status_code == 404

    def test_html_filesystem_fallback(self, app_state, client, tmp_path):
        cert_dir = tmp_path / "a" / "e" / "certification"
        cert_dir.mkdir(parents=True)
        (cert_dir / "report.html").write_text("<html></html>")
        app_state.state.settings.workspace_dir = tmp_path
        r = client.get("/api/v1/certification/html",
                       params={"agent_id": "a", "experiment_id": "e"})
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_gridfs_stream_used_when_available(self, app_state, client, tmp_path, monkeypatch):
        # Provide a gridfs bucket and stub _gridfs_stream to return a response.
        from fastapi.responses import StreamingResponse
        import io as _io
        app_state.state.gridfs_bucket = MagicMock()

        async def fake_stream(bucket, agent_id, experiment_id, fmt):
            return StreamingResponse(_io.BytesIO(b"PDFBYTES"),
                                     media_type="application/pdf")

        monkeypatch.setattr(rep_router, "_gridfs_stream", fake_stream)
        r = client.get("/api/v1/certification/pdf",
                       params={"agent_id": "a", "experiment_id": "e"})
        assert r.status_code == 200
        assert r.content == b"PDFBYTES"
