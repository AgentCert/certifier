"""Unit tests for utils.mongodb_util.

pymongo is fully mocked — no real database. MongoClient is patched at the
module level so MongoDBClient never opens a socket.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel
from pymongo.errors import ConnectionFailure, DuplicateKeyError, OperationFailure

import utils.mongodb_util as mu
from utils.mongodb_util import MongoDBClient, MongoDBConfig


# --------------------------------------------------------------------------- #
# MongoDBConfig
# --------------------------------------------------------------------------- #
class TestMongoDBConfig:
    def test_loads_from_config(self):
        cfg = MongoDBConfig({
            "mongodb": {
                "connection_string_env": "mongodb://host:1",
                "database": "mydb",
                "collections": {"metrics": "my_metrics"},
                "vector_search": {
                    "index_name": "vi", "embedding_field": "vec",
                    "dimensions": 512, "similarity": "dotProduct",
                    "num_candidates": 50, "limit": 5,
                },
            }
        })
        assert cfg.connection_string == "mongodb://host:1"
        assert cfg.database_name == "mydb"
        assert cfg.metrics_collection == "my_metrics"
        assert cfg.vector_index_name == "vi"
        assert cfg.embedding_field == "vec"
        assert cfg.embedding_dimensions == 512
        assert cfg.similarity_metric == "dotProduct"
        assert cfg.num_candidates == 50
        assert cfg.vector_limit == 5

    def test_empty_config_uses_inline_defaults(self):
        cfg = MongoDBConfig({})
        assert cfg.database_name == "agentcert"
        assert cfg.metrics_collection == "agent_run_metrics"
        assert cfg.embedding_dimensions == 1536
        assert cfg.vector_limit == 10

    def test_none_config_triggers_defaults_path(self, monkeypatch):
        monkeypatch.setenv("MONGODB_CONNECTION_STRING", "mongodb://envhost")
        # config=None → .get raises AttributeError → _set_defaults
        cfg = MongoDBConfig(None)
        assert cfg.connection_string == "mongodb://envhost"
        assert cfg.database_name == "agentcert"
        assert cfg.similarity_metric == "cosine"


# --------------------------------------------------------------------------- #
# MongoDBClient: connection management
# --------------------------------------------------------------------------- #
@pytest.fixture
def mock_mongo(monkeypatch):
    """Patch MongoClient. Returns (client_class_mock, db_mock, collection_mock)."""
    collection = MagicMock(name="collection")
    db = MagicMock(name="db")
    db.__getitem__.return_value = collection
    client_inst = MagicMock(name="client_inst")
    client_inst.__getitem__.return_value = db
    client_class = MagicMock(name="MongoClient", return_value=client_inst)
    monkeypatch.setattr(mu, "MongoClient", client_class)
    return client_class, client_inst, db, collection


def _client():
    return MongoDBClient(MongoDBConfig({}))


class TestConnection:
    def test_lazy_connect_once(self, mock_mongo, monkeypatch):
        monkeypatch.setenv("MONGODB_CONNECTION_STRING", "mongodb://localhost:27017")
        client_class, client_inst, db, _ = mock_mongo
        c = MongoDBClient(MongoDBConfig({}))
        assert c._sync_client is None
        first = c._get_sync_client()
        second = c._get_sync_client()
        assert first is second
        client_class.assert_called_once_with("mongodb://localhost:27017")

    def test_sync_db_property(self, mock_mongo):
        _, _, db, _ = mock_mongo
        c = _client()
        assert c.sync_db is db

    def test_close_resets_state(self, mock_mongo):
        _, client_inst, _, _ = mock_mongo
        c = _client()
        c._get_sync_client()
        c.close()
        client_inst.close.assert_called_once()
        assert c._sync_client is None
        assert c._sync_db is None

    def test_close_noop_when_not_connected(self, mock_mongo):
        c = _client()
        c.close()  # should not raise

    def test_health_check_ok(self, mock_mongo):
        _, client_inst, _, _ = mock_mongo
        c = _client()
        assert c.health_check() is True
        client_inst.admin.command.assert_called_once_with("ping")

    def test_health_check_failure(self, mock_mongo):
        _, client_inst, _, _ = mock_mongo
        client_inst.admin.command.side_effect = ConnectionFailure("down")
        c = _client()
        assert c.health_check() is False


# --------------------------------------------------------------------------- #
# Collection initialization
# --------------------------------------------------------------------------- #
class TestInitialization:
    def test_initialize_creates_missing_collection(self, mock_mongo):
        _, _, db, collection = mock_mongo
        db.list_collection_names.return_value = []
        c = _client()
        results = c.initialize_collections()
        db.create_collection.assert_called_once_with("agent_run_metrics")
        assert results["metrics"] is True
        # 6 indexes created on the metrics collection
        assert collection.create_index.call_count == 6

    def test_initialize_skips_existing_collection(self, mock_mongo):
        _, _, db, _ = mock_mongo
        db.list_collection_names.return_value = ["agent_run_metrics"]
        c = _client()
        c.initialize_collections()
        db.create_collection.assert_not_called()

    def test_init_metrics_collection_failure_returns_false(self, mock_mongo):
        _, _, db, collection = mock_mongo
        db.list_collection_names.return_value = ["agent_run_metrics"]
        collection.create_index.side_effect = RuntimeError("index boom")
        c = _client()
        results = c.initialize_collections()
        assert results["metrics"] is False


# --------------------------------------------------------------------------- #
# Vector search index
# --------------------------------------------------------------------------- #
class TestVectorSearchIndex:
    def test_create_index_success(self, mock_mongo):
        _, _, _, collection = mock_mongo
        c = _client()
        assert c.create_vector_search_index() is True
        collection.create_search_index.assert_called_once()

    def test_create_index_already_exists_is_ok(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.create_search_index.side_effect = OperationFailure("index already exists")
        c = _client()
        assert c.create_vector_search_index() is True

    def test_create_index_operation_failure(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.create_search_index.side_effect = OperationFailure("nope")
        c = _client()
        assert c.create_vector_search_index() is False

    def test_create_index_generic_error(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.create_search_index.side_effect = RuntimeError("boom")
        c = _client()
        assert c.create_vector_search_index() is False


# --------------------------------------------------------------------------- #
# Insert
# --------------------------------------------------------------------------- #
class _Model(BaseModel):
    experiment_id: str = "exp1"
    run_id: str = "run1"
    agent_name: str = "agent"
    agent_id: str = "aid"
    injected_fault_category: str = "compute"
    injected_fault_name: str = "pod-delete"


class TestInsertMetrics:
    def test_insert_new_document(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.insert_one.return_value = MagicMock(inserted_id="OID123")
        c = _client()
        doc_id = c.insert_metrics(
            quantitative={"experiment_id": "e1", "run_id": "r1",
                           "injected_fault_category": "net", "injected_fault_name": "loss"},
            qualitative={"summary": "ok"},
            embedding=[0.1, 0.2],
            metadata={"src": "test"},
        )
        assert doc_id == "OID123"
        inserted = collection.insert_one.call_args[0][0]
        assert inserted["experiment_id"] == "e1"
        assert inserted["fault_category"] == "net"
        assert inserted["fault_name"] == "loss"
        assert inserted["embedding"] == [0.1, 0.2]
        assert inserted["metadata"] == {"src": "test"}
        assert "created_at" in inserted

    def test_insert_accepts_pydantic_models(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.insert_one.return_value = MagicMock(inserted_id="OID")
        c = _client()
        c.insert_metrics(quantitative=_Model(), qualitative=_Model())
        inserted = collection.insert_one.call_args[0][0]
        assert inserted["experiment_id"] == "exp1"
        assert inserted["fault_category"] == "compute"

    def test_insert_generates_experiment_id_when_missing(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.insert_one.return_value = MagicMock(inserted_id="OID")
        c = _client()
        c.insert_metrics(quantitative={}, qualitative={})
        inserted = collection.insert_one.call_args[0][0]
        assert inserted["experiment_id"]  # a uuid string

    def test_insert_duplicate_key_replaces(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.insert_one.side_effect = DuplicateKeyError("dup")
        collection.find_one.return_value = {"_id": "EXISTING"}
        c = _client()
        doc_id = c.insert_metrics(
            quantitative={"experiment_id": "e1"}, qualitative={})
        collection.replace_one.assert_called_once()
        assert doc_id == "EXISTING"

    def test_insert_duplicate_key_missing_after_replace(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.insert_one.side_effect = DuplicateKeyError("dup")
        collection.find_one.return_value = None
        c = _client()
        doc_id = c.insert_metrics(quantitative={"experiment_id": "e1"}, qualitative={})
        assert doc_id == ""


# --------------------------------------------------------------------------- #
# Query operations
# --------------------------------------------------------------------------- #
class TestQueries:
    def test_find_by_experiment_id(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.find_one.return_value = {"experiment_id": "e1"}
        c = _client()
        assert c.find_by_experiment_id("e1") == {"experiment_id": "e1"}
        collection.find_one.assert_called_once_with({"experiment_id": "e1"})

    def test_find_by_fault_category(self, mock_mongo):
        _, _, _, collection = mock_mongo
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.limit.return_value = ["doc1", "doc2"]
        collection.find.return_value = cursor
        c = _client()
        out = c.find_by_fault_category("compute", limit=5)
        assert out == ["doc1", "doc2"]
        collection.find.assert_called_once_with({"fault_category": "compute"})
        cursor.limit.assert_called_once_with(5)

    def test_find_by_fault_name(self, mock_mongo):
        _, _, _, collection = mock_mongo
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.limit.return_value = ["d"]
        collection.find.return_value = cursor
        c = _client()
        assert c.find_by_fault_name("pod-delete") == ["d"]
        collection.find.assert_called_once_with({"fault_name": "pod-delete"})

    def test_find_by_agent_id_no_limit(self, mock_mongo):
        _, _, _, collection = mock_mongo
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        collection.find.return_value = cursor
        # list(cursor) iterates the sorted cursor
        cursor.__iter__.return_value = iter(["a", "b"])
        c = _client()
        out = c.find_by_agent_id("aid")
        assert out == ["a", "b"]
        cursor.limit.assert_not_called()

    def test_find_by_agent_id_with_limit(self, mock_mongo):
        _, _, _, collection = mock_mongo
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.limit.return_value = ["x"]
        collection.find.return_value = cursor
        c = _client()
        assert c.find_by_agent_id("aid", limit=3) == ["x"]
        cursor.limit.assert_called_once_with(3)


# --------------------------------------------------------------------------- #
# Vector search
# --------------------------------------------------------------------------- #
class TestVectorSearch:
    def test_vector_search_builds_pipeline(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.aggregate.return_value = iter([{"_id": 1, "search_score": 0.9}])
        c = _client()
        out = c.vector_search([0.1, 0.2], filter_query={"fault_category": "net"}, limit=3)
        assert out == [{"_id": 1, "search_score": 0.9}]
        pipeline = collection.aggregate.call_args[0][0]
        vs = pipeline[0]["$vectorSearch"]
        assert vs["queryVector"] == [0.1, 0.2]
        assert vs["limit"] == 3
        assert vs["filter"] == {"fault_category": "net"}
        # embedding field projected out
        assert pipeline[-1]["$project"]["embedding"] == 0

    def test_vector_search_no_filter(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.aggregate.return_value = iter([])
        c = _client()
        c.vector_search([0.1])
        vs = collection.aggregate.call_args[0][0][0]["$vectorSearch"]
        assert "filter" not in vs
        # default limit from config
        assert vs["limit"] == 10

    def test_vector_search_operation_failure_returns_empty(self, mock_mongo):
        _, _, _, collection = mock_mongo
        collection.aggregate.side_effect = OperationFailure("index missing")
        c = _client()
        assert c.vector_search([0.1]) == []
