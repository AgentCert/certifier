"""
MongoDB utility module for AgentCert metrics storage.
Provides sync MongoDB client with Atlas Vector Search support.
Uses a single `agent_run_metrics` collection for all data.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import ConnectionFailure, DuplicateKeyError, OperationFailure
from pymongo.operations import SearchIndexModel
from utils.setup_logging import logger


class MongoDBConfig:
    """MongoDB configuration loaded from configs.json."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._load_config(config)

    def _load_config(self, config) -> None:
        try:
            mongodb_config = config.get("mongodb", {})

            self.connection_string = mongodb_config.get(
                "connection_string_env", os.getenv("MONGODB_CONNECTION_STRING")
            )
            self.database_name = mongodb_config.get("database", "agentcert")

            collections = mongodb_config.get("collections", {})
            self.metrics_collection = collections.get("metrics", "agent_run_metrics")

            vector_config = mongodb_config.get("vector_search", {})
            self.vector_index_name = vector_config.get(
                "index_name", "metrics_vector_index"
            )
            self.embedding_field = vector_config.get("embedding_field", "embedding")
            self.embedding_dimensions = vector_config.get("dimensions", 1536)
            self.similarity_metric = vector_config.get("similarity", "cosine")
            self.num_candidates = vector_config.get("num_candidates", 100)
            self.vector_limit = vector_config.get("limit", 10)

        except (FileNotFoundError, AttributeError):
            logger.warning("Config loading failed, using defaults")
            self._set_defaults()
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing config file: {e}")
            self._set_defaults()

    def _set_defaults(self) -> None:
        self.connection_string = os.getenv(
            "MONGODB_CONNECTION_STRING", "mongodb://localhost:27017"
        )
        self.database_name = "agentcert"
        self.metrics_collection = "agent_run_metrics"
        self.vector_index_name = "metrics_vector_index"
        self.embedding_field = "embedding"
        self.embedding_dimensions = 1536
        self.similarity_metric = "cosine"
        self.num_candidates = 100
        self.vector_limit = 10


class MongoDBClient:
    """
    MongoDB client with Atlas Vector Search capabilities.
    Stores combined quantitative and qualitative metrics in a single collection.
    """

    def __init__(self, config: Optional[MongoDBConfig] = None):
        self.config = config or MongoDBConfig()
        self._sync_client: Optional[MongoClient] = None
        self._sync_db: Optional[Any] = None

    # ==================== CONNECTION MANAGEMENT ====================

    def _get_sync_client(self) -> MongoClient:
        if self._sync_client is None:
            self._sync_client = MongoClient(self.config.connection_string)
            self._sync_db = self._sync_client[self.config.database_name]
            logger.info(f"Connected to MongoDB: {self.config.database_name}")
        return self._sync_client

    @property
    def sync_db(self):
        self._get_sync_client()
        return self._sync_db

    def close(self) -> None:
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None
            self._sync_db = None
            logger.info("MongoDB connection closed")

    def health_check(self) -> bool:
        try:
            self._get_sync_client()
            self._sync_client.admin.command("ping")
            return True
        except ConnectionFailure as e:
            logger.error(f"MongoDB health check failed: {e}")
            return False

    # ==================== COLLECTION INITIALIZATION ====================

    def initialize_collections(self) -> Dict[str, bool]:
        """Initialize metrics collection with indexes if it does not already exist."""
        results = {}
        existing_collections = self.sync_db.list_collection_names()
        if self.config.metrics_collection not in existing_collections:
            self.sync_db.create_collection(self.config.metrics_collection)
        results["metrics"] = self._init_metrics_collection()
        return results

    def _init_metrics_collection(self) -> bool:
        try:
            collection = self.sync_db[self.config.metrics_collection]

            collection.create_index(
                [("fault_category", ASCENDING), ("fault_name", ASCENDING)]
            )
            collection.create_index(
                [("experiment_id", ASCENDING)], unique=True, sparse=True
            )
            collection.create_index(
                [("fault_category", ASCENDING), ("created_at", DESCENDING)]
            )
            collection.create_index(
                [("agent_name", ASCENDING)], sparse=True
            )
            collection.create_index(
                [("agent_id", ASCENDING)], sparse=True
            )
            collection.create_index(
                [("run_id", ASCENDING)], sparse=True
            )

            logger.info(f"Initialized collection: {self.config.metrics_collection}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize metrics collection: {e}")
            return False

    def create_vector_search_index(self, collection_name: Optional[str] = None) -> bool:
        """Create Atlas Vector Search index on a collection."""
        collection_name = collection_name or self.config.metrics_collection

        try:
            collection = self.sync_db[collection_name]

            search_index_model = SearchIndexModel(
                definition={
                    "fields": [
                        {
                            "type": "vector",
                            "path": self.config.embedding_field,
                            "numDimensions": self.config.embedding_dimensions,
                            "similarity": self.config.similarity_metric,
                        },
                        {"type": "filter", "path": "fault_category"},
                        {"type": "filter", "path": "fault_name"},
                    ]
                },
                name=self.config.vector_index_name,
                type="vectorSearch",
            )

            collection.create_search_index(model=search_index_model)
            logger.info(
                f"Created vector search index '{self.config.vector_index_name}' on {collection_name}"
            )
            return True

        except OperationFailure as e:
            if "already exists" in str(e):
                logger.info(f"Vector search index already exists on {collection_name}")
                return True
            logger.error(f"Failed to create vector search index: {e}")
            return False
        except Exception as e:
            logger.error(f"Vector search index creation error: {e}")
            return False

    # ==================== CRUD OPERATIONS ====================

    def insert_metrics(
        self,
        quantitative: Union[BaseModel, Dict[str, Any]],
        qualitative: Union[BaseModel, Dict[str, Any]],
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Insert combined quantitative and qualitative metrics.

        Top-level fields `experiment_id`, `fault_category`, and `fault_name`
        are extracted from the quantitative data to support indexed queries.
        """
        collection = self.sync_db[self.config.metrics_collection]

        quant_doc = (
            quantitative.model_dump(mode="json")
            if isinstance(quantitative, BaseModel)
            else dict(quantitative)
        )
        qual_doc = (
            qualitative.model_dump(mode="json")
            if isinstance(qualitative, BaseModel)
            else dict(qualitative)
        )

        experiment_id = quant_doc.get("experiment_id") or str(uuid.uuid4())
        run_id = quant_doc.get("run_id")

        doc = {
            "experiment_id": experiment_id,
            "run_id": run_id,
            "agent_name": quant_doc.get("agent_name"),
            "agent_id": quant_doc.get("agent_id"),
            "fault_category": quant_doc.get("injected_fault_category"),
            "fault_name": quant_doc.get("injected_fault_name"),
            "quantitative": quant_doc,
            "qualitative": qual_doc,
            "created_at": datetime.now(timezone.utc),
        }

        if embedding:
            doc[self.config.embedding_field] = embedding
        if metadata:
            doc["metadata"] = metadata

        try:
            result = collection.insert_one(doc)
            logger.debug(f"Inserted combined metrics: {result.inserted_id}")
            return str(result.inserted_id)
        except DuplicateKeyError:
            logger.info(
                f"Document with experiment_id '{experiment_id}' already exists, updating..."
            )
            collection.replace_one({"experiment_id": experiment_id}, doc)
            existing = collection.find_one({"experiment_id": experiment_id})
            return str(existing["_id"]) if existing else ""

    # ==================== QUERY OPERATIONS ====================

    def find_by_experiment_id(
        self, experiment_id: str
    ) -> Optional[Dict[str, Any]]:
        """Find metrics document by experiment_id."""
        collection = self.sync_db[self.config.metrics_collection]
        return collection.find_one({"experiment_id": experiment_id})

    def find_by_fault_category(
        self, fault_category: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Find metrics by fault_category, ordered by created_at descending."""
        collection = self.sync_db[self.config.metrics_collection]
        cursor = (
            collection.find({"fault_category": fault_category})
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        return list(cursor)

    def find_by_agent_id(
        self, agent_id: str, limit: int = 0
    ) -> List[Dict[str, Any]]:
        """Find all metrics documents for a given agent_id, ordered by created_at descending."""
        collection = self.sync_db[self.config.metrics_collection]
        cursor = collection.find({"agent_id": agent_id}).sort("created_at", DESCENDING)
        if limit > 0:
            cursor = cursor.limit(limit)
        return list(cursor)

    def find_by_fault_name(
        self, fault_name: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Find metrics by fault_name, ordered by created_at descending."""
        collection = self.sync_db[self.config.metrics_collection]
        cursor = (
            collection.find({"fault_name": fault_name})
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        return list(cursor)

    # ==================== VECTOR SEARCH ====================

    def vector_search(
        self,
        query_embedding: List[float],
        collection_name: Optional[str] = None,
        filter_query: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Perform vector similarity search using Atlas Vector Search."""
        collection_name = collection_name or self.config.metrics_collection
        limit = limit or self.config.vector_limit
        collection = self.sync_db[collection_name]

        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.config.vector_index_name,
                    "path": self.config.embedding_field,
                    "queryVector": query_embedding,
                    "numCandidates": self.config.num_candidates,
                    "limit": limit,
                }
            },
            {"$addFields": {"search_score": {"$meta": "vectorSearchScore"}}},
        ]

        if filter_query:
            pipeline[0]["$vectorSearch"]["filter"] = filter_query

        pipeline.append({"$project": {self.config.embedding_field: 0}})

        try:
            results = list(collection.aggregate(pipeline))
            logger.debug(f"Vector search returned {len(results)} results")
            return results
        except OperationFailure as e:
            logger.error(f"Vector search failed: {e}")
            return []


if __name__ == "__main__":
    print("Testing MongoDB connection...")

    from utils.load_config import ConfigLoader

    mongo_config = MongoDBConfig(ConfigLoader.load_config())
    client = MongoDBClient(mongo_config)

    try:
        if not client.health_check():
            print("❌ MongoDB connection failed")
            print("Make sure MongoDB is running and MONGODB_CONNECTION_STRING is set")
            exit(1)

        print("✅ MongoDB connection successful")

        print("\nInitializing collections...")
        results = client.initialize_collections()
        for collection, success in results.items():
            status = "✅" if success else "❌"
            print(f"  {status} {collection}")

        print("\n📝 Creating sample metrics document...")
        experiment_id = (
            f"exp_test_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        )

        quantitative_data = {
            "experiment_id": experiment_id,
            "fault_injection_time": "2026-02-15T10:00:00Z",
            "agent_fault_detection_time": "2026-02-15T10:00:15Z",
            "agent_fault_mitigation_time": "2026-02-15T10:01:30Z",
            "time_to_detect": 15.0,
            "time_to_mitigate": 90.0,
            "fault_detected": "Misconfig",
            "trajectory_steps": 12,
            "input_tokens": 5000,
            "output_tokens": 1500,
            "injected_fault_name": "pod-delete",
            "injected_fault_category": "compute",
            "detected_fault_type": "pod-delete",
            "fault_target_service": "payment-service",
            "fault_namespace": "production",
            "tool_calls": [
                {
                    "tool_name": "get_logs",
                    "arguments": {"service": "payment-service"},
                    "was_successful": True,
                    "response_summary": "Retrieved logs",
                    "timestamp": "2026-02-15T10:00:10Z",
                }
            ],
        }

        qualitative_data = {
            "rai_check_status": "Passed",
            "rai_check_notes": "No harmful content detected",
            "security_compliance_status": "Compliant",
            "security_compliance_notes": "No credentials exposed",
            "reasoning_quality_score": 9.0,
            "reasoning_quality_notes": "Clear and accurate reasoning",
            "agent_summary": "Agent detected misconfig in payment-service and remediated it.",
        }

        metadata = {
            "trace_file": "test_trace.json",
            "total_spans": 12,
            "extraction_token_usage": {
                "input_tokens": 3000,
                "output_tokens": 800,
                "total_tokens": 3800,
            },
        }

        doc_id = client.insert_metrics(
            quantitative=quantitative_data,
            qualitative=qualitative_data,
            metadata=metadata,
        )
        print(f"✅ Inserted metrics document with ID: {doc_id}")
        print(f"   Experiment ID: {experiment_id}")

        print("\n🔍 Verifying document insertion...")
        inserted_doc = client.find_by_experiment_id(experiment_id)
        if inserted_doc:
            print(f"✅ Document found in database")
            print(f"   fault_category: {inserted_doc.get('fault_category')}")
            print(f"   fault_name: {inserted_doc.get('fault_name')}")
        else:
            print("❌ Document not found after insertion")

        category_docs = client.find_by_fault_category("compute")
        print(f"\n📊 Documents with fault_category='compute': {len(category_docs)}")

        print(f"\n🗑️  Deleting test document (experiment_id: {experiment_id})...")
        collection = client.sync_db[client.config.metrics_collection]
        delete_result = collection.delete_one({"experiment_id": experiment_id})
        if delete_result.deleted_count > 0:
            print(f"✅ Test document deleted successfully")
        else:
            print("❌ Document deletion failed")

        print("\n✅ Test completed successfully!")

    except Exception as e:
        print(f"\n❌ Error during test: {e}")
        import traceback
        traceback.print_exc()

    finally:
        client.close()
        print("\n🔌 MongoDB connection closed")
