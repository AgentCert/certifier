"""Unit tests for main.services.session_service.

SessionService / CertSessionService are thin async wrappers over a Motor
collection. We inject an AsyncMock collection and assert the exact filters and
update documents issued, plus the lifecycle guard behaviour (set_completed
raising when the task is not RUNNING).
"""
from unittest.mock import AsyncMock

import pytest

from main.services.session_service import CertSessionService, SessionService


def _mock_col():
    col = AsyncMock()
    return col


# ── SessionService ─────────────────────────────────────────────────────────

class TestSessionService:
    @pytest.fixture
    def col(self):
        return _mock_col()

    @pytest.fixture
    def svc(self, col):
        return SessionService(col)

    async def test_create_task_inserts_pending(self, svc, col):
        await svc.create_task("t1", "a", "e", "r", {"snap": 1})
        col.insert_one.assert_awaited_once()
        doc = col.insert_one.await_args.args[0]
        assert doc["task_id"] == "t1"
        assert doc["status"] == "PENDING"
        assert doc["stage"] == "pending"
        assert doc["agent_id"] == "a"
        assert doc["experiment_id"] == "e"
        assert doc["run_id"] == "r"
        assert doc["request"] == {"snap": 1}
        assert doc["result"] is None
        assert doc["error"] is None
        assert doc["started_at"] is None

    async def test_set_started_guards_on_pending(self, svc, col):
        await svc.set_started("t1")
        flt, update = col.update_one.await_args.args
        assert flt == {"task_id": "t1", "status": "PENDING"}
        assert update["$set"]["status"] == "RUNNING"
        assert update["$set"]["stage"] == "acquiring_trace"
        assert "started_at" in update["$currentDate"]

    async def test_update_stage(self, svc, col):
        await svc.update_stage("t1", "running_pipeline")
        flt, update = col.update_one.await_args.args
        assert flt == {"task_id": "t1"}
        assert update["$set"] == {"stage": "running_pipeline"}

    async def test_set_completed_success(self, svc, col):
        col.update_one.return_value = AsyncMock(matched_count=1)
        await svc.set_completed("t1", {"r": 1})
        flt, update = col.update_one.await_args.args
        assert flt == {"task_id": "t1", "status": "RUNNING"}
        assert update["$set"]["status"] == "COMPLETED"
        assert update["$set"]["result"] == {"r": 1}

    async def test_set_completed_not_running_raises(self, svc, col):
        col.update_one.return_value = AsyncMock(matched_count=0)
        with pytest.raises(ValueError, match="not in RUNNING state"):
            await svc.set_completed("t1", {})

    async def test_set_failed_accepts_pending_or_running(self, svc, col):
        await svc.set_failed("t1", "E_CODE", "boom", "acquiring_trace", "tb")
        flt, update = col.update_one.await_args.args
        assert flt["status"] == {"$in": ["PENDING", "RUNNING"]}
        err = update["$set"]["error"]
        assert err == {
            "error_code": "E_CODE",
            "message": "boom",
            "failed_stage": "acquiring_trace",
            "detail": "tb",
        }
        assert update["$set"]["status"] == "FAILED"

    async def test_get_task_excludes_id(self, svc, col):
        col.find_one.return_value = {"task_id": "t1"}
        out = await svc.get_task("t1")
        assert out == {"task_id": "t1"}
        flt, proj = col.find_one.await_args.args
        assert flt == {"task_id": "t1"}
        assert proj == {"_id": 0}

    async def test_get_task_by_run_sorts_recent(self, svc, col):
        col.find_one.return_value = None
        await svc.get_task_by_run("e", "r")
        flt, proj = col.find_one.await_args.args
        assert flt == {"experiment_id": "e", "run_id": "r"}
        assert col.find_one.await_args.kwargs["sort"] == [("created_at", -1)]

    async def test_find_active_task_filters_nonterminal(self, svc, col):
        col.find_one.return_value = None
        await svc.find_active_task("a", "e", "r")
        flt, proj = col.find_one.await_args.args
        assert flt["status"] == {"$in": ["PENDING", "RUNNING"]}
        assert flt["agent_id"] == "a"


# ── CertSessionService ─────────────────────────────────────────────────────

class TestCertSessionService:
    @pytest.fixture
    def col(self):
        return _mock_col()

    @pytest.fixture
    def svc(self, col):
        return CertSessionService(col)

    async def test_create_task_inserts_pending(self, svc, col):
        await svc.create_task("c1", "a", "Name", "e", "runid", {"s": 1})
        doc = col.insert_one.await_args.args[0]
        assert doc["cert_task_id"] == "c1"
        assert doc["agent_name"] == "Name"
        assert doc["certification_run_id"] == "runid"
        assert doc["status"] == "PENDING"

    async def test_set_started_sets_fetching_metrics(self, svc, col):
        await svc.set_started("c1")
        flt, update = col.update_one.await_args.args
        assert flt == {"cert_task_id": "c1", "status": "PENDING"}
        assert update["$set"]["stage"] == "fetching_metrics"

    async def test_set_completed_not_running_raises(self, svc, col):
        col.update_one.return_value = AsyncMock(matched_count=0)
        with pytest.raises(ValueError, match="not in RUNNING state"):
            await svc.set_completed("c1", {})

    async def test_set_completed_success(self, svc, col):
        col.update_one.return_value = AsyncMock(matched_count=1)
        await svc.set_completed("c1", {"ok": True})
        _, update = col.update_one.await_args.args
        assert update["$set"]["result"] == {"ok": True}

    async def test_set_failed(self, svc, col):
        await svc.set_failed("c1", "AGG", "msg", "running_pipeline", "tb")
        _, update = col.update_one.await_args.args
        assert update["$set"]["error"]["error_code"] == "AGG"

    async def test_get_task_by_experiment(self, svc, col):
        col.find_one.return_value = None
        await svc.get_task_by_experiment("e")
        flt, proj = col.find_one.await_args.args
        assert flt == {"experiment_id": "e"}
        assert col.find_one.await_args.kwargs["sort"] == [("created_at", -1)]

    async def test_find_active_task(self, svc, col):
        col.find_one.return_value = {"cert_task_id": "c1"}
        out = await svc.find_active_task("a", "e")
        assert out == {"cert_task_id": "c1"}
        flt, _ = col.find_one.await_args.args
        assert flt["status"] == {"$in": ["PENDING", "RUNNING"]}
