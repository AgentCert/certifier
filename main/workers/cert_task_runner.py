import asyncio
import json
import logging
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)

from motor.motor_asyncio import AsyncIOMotorCollection

from main.config.settings import Settings
from main.models.cert_requests import AggregationCertificationRequest
from main.services.pipeline_service import CertPipelineService, generate_cert_report_documents
from main.services.session_service import CertSessionService


def resolve_cert_output_dir(workspace_dir: Path, agent_id: str, experiment_id: str) -> Path:
    """Return ``workspace/{agent_id}/{experiment_id}/``, creating it if needed.

    Validates both segments against directory-traversal characters to prevent
    user-supplied IDs from escaping the workspace root.
    """
    for segment in (agent_id, experiment_id):
        if "/" in segment or "\\" in segment or ".." in segment:
            raise ValueError(f"Path segment contains illegal characters: {segment!r}")
    path = workspace_dir / agent_id / experiment_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def classify_cert_error(exc: Exception) -> str:
    """Map a pipeline exception to a structured *error_code* string.

    Uses lightweight keyword matching on the lowercased message because the
    pipeline modules do not expose typed exception hierarchies.  Falls back to
    ``PIPELINE_FAILED`` for anything unrecognised.
    """
    msg = str(exc).lower()
    if "aggregat" in msg or "council" in msg or "scorecard" in msg:
        return "AGGREGATION_FAILED"
    if "certif" in msg or "cert_builder" in msg or "report" in msg:
        return "CERT_GENERATION_FAILED"
    if "storage" in msg or isinstance(exc, OSError):
        return "STORAGE_ERROR"
    return "PIPELINE_FAILED"


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def _write_certification_metadata(
    cert_meta_col: AsyncIOMotorCollection,
    certification_id: str,
    cert_task_id: str,
    agent_id: str,
    agent_name: str,
    experiment_id: str,
    certification_run_id: str,
    summary: dict,
    cert_output_dir: Path,
    elapsed: float,
) -> None:
    """Insert one certification_metadata document for the completed run.

    One document per successful certification; the ``certification_id`` UUID
    links this record to the per-category rows in aggregated_category_metadata.
    """
    await cert_meta_col.insert_one({
        "certification_id": certification_id,
        "cert_task_id": cert_task_id,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "experiment_id": experiment_id,
        "certification_run_id": certification_run_id,
        "status": "success",
        "created_at": datetime.now(timezone.utc),
        "storage_paths": {
            "aggregated_scorecard": str(cert_output_dir / "aggregation" / "aggregation.json"),
            "certification_report": str(cert_output_dir / "cert-builder" / "certification.json"),
            "summary": str(cert_output_dir / "pipeline_summary.json"),
        },
        "summary": {
            "total_documents": summary.get("total_documents", 0),
            "total_fault_categories": summary.get("total_fault_categories", 0),
            "fault_categories": summary.get("fault_categories", []),
        },
        "processing_time_seconds": round(elapsed, 1),
        "error_message": None,
    })


async def _write_aggregated_category_metadata(
    agg_cat_col: AsyncIOMotorCollection,
    certification_id: str,
    agent_id: str,
    experiment_id: str,
    cert_output_dir: Path,
) -> None:
    """Insert one aggregated_category_metadata document per fault category scorecard.

    Reads the aggregated scorecard JSON produced by the pipeline and fans out
    each ``fault_category_scorecards`` entry into its own document, keyed by
    ``(certification_id, fault_category)`` (enforced unique by DB index).
    """
    scorecard_path = cert_output_dir / "aggregation" / "aggregation.json"
    # Read the scorecard from disk in a thread to avoid blocking the event loop
    aggregated_scorecard = await asyncio.to_thread(_read_json, scorecard_path)
    now = datetime.now(timezone.utc)
    docs = []
    for sc in aggregated_scorecard.get("fault_category_scorecards", []):
        docs.append({
            "fault_category": sc["fault_category"],
            "certification_id": certification_id,
            "agent_id": agent_id,
            "experiment_id": experiment_id,
            "total_runs": sc.get("total_runs", 0),
            "faults_tested": sc.get("faults_tested", []),
            "numeric_metrics": sc.get("numeric_metrics", {}),
            "derived_metrics": sc.get("derived_metrics", {}),
            "created_at": now,
        })
    if docs:
        await agg_cat_col.insert_many(docs)


async def run_cert_task(
    cert_task_id: str,
    request: AggregationCertificationRequest,
    cert_session_svc: CertSessionService,
    cert_pipeline_svc: CertPipelineService,
    cert_semaphore: asyncio.Semaphore,
    cert_meta_col: AsyncIOMotorCollection,
    agg_cat_col: AsyncIOMotorCollection,
    settings: Settings,
    app_config: dict,
) -> None:
    """Background coroutine that drives a single aggregation-certification task.

    Stage flow:
        PENDING → RUNNING / fetching_metrics
                → RUNNING / running_pipeline   (inside semaphore)
                → RUNNING / storing_metadata
                → COMPLETED  (on success)
                → FAILED      (on any error)

    The function never raises — all exceptions are caught and recorded as FAILED.
    """
    # Transition to RUNNING and set initial stage before any heavy work
    await cert_session_svc.set_started(cert_task_id)

    # ── Resolve output directory ───────────────────────────────────────────────
    try:
        cert_output_dir = resolve_cert_output_dir(
            settings.workspace_dir, request.agent_id, request.experiment_id
        )
    except ValueError as exc:
        await cert_session_svc.set_failed(
            cert_task_id, "INVALID_REQUEST", str(exc), "fetching_metrics",
            traceback.format_exc(),
        )
        return

    # ── Stage: running_pipeline (inside semaphore) ────────────────────────────
    # The semaphore limits simultaneous heavy cert pipeline runs to prevent OOM
    report_paths: Dict[str, str] = {}
    try:
        async with cert_semaphore:
            await cert_session_svc.update_stage(cert_task_id, "running_pipeline")
            start = time.monotonic()
            result = await cert_pipeline_svc.execute_pipeline(
                metrics_dir=request.storage_config.metrics_dir,
                output_dir=str(cert_output_dir),
                agent_id=request.agent_id,
                agent_name=request.agent_name,
                certification_run_id=request.certification_run_id,
                runs_per_fault=request.runs_per_fault,
                config=app_config,
            )

            # An empty result means no metrics were found — bail before report gen
            if not result:
                await cert_session_svc.set_failed(
                    cert_task_id, "METRICS_NOT_FOUND",
                    f"No metrics documents found for agent_id='{request.agent_id}'",
                    "running_pipeline", "",
                )
                return

            # ── Stage: generating_report ──────────────────────────────────────
            # Run cert_reporter pipeline to produce HTML + PDF from certification.json
            await cert_session_svc.update_stage(cert_task_id, "generating_report")
            cert_json_path = cert_output_dir / "cert-builder" / "certification.json"
            report_output_dir = cert_output_dir / "certification"
            try:
                report_paths = await asyncio.to_thread(
                    generate_cert_report_documents,
                    cert_json_path,
                    report_output_dir,
                )
            except Exception as exc:
                # Report generation failure is non-fatal — log and continue
                log.warning("cert_reporter pipeline failed (non-fatal): %s", exc)
                report_paths = {}

            elapsed = time.monotonic() - start

    except Exception as exc:
        await cert_session_svc.set_failed(
            cert_task_id, classify_cert_error(exc), str(exc), "running_pipeline",
            traceback.format_exc(),
        )
        return

    # ── Stage: storing_metadata ────────────────────────────────────────────────
    # Write the certification result to MongoDB after the pipeline succeeds
    await cert_session_svc.update_stage(cert_task_id, "storing_metadata")

    try:
        summary = await asyncio.to_thread(
            _read_json, cert_output_dir / "pipeline_summary.json"
        )
        # A single UUID ties the metadata doc to all its category rows
        certification_id = str(uuid.uuid4())

        await _write_certification_metadata(
            cert_meta_col=cert_meta_col,
            certification_id=certification_id,
            cert_task_id=cert_task_id,
            agent_id=request.agent_id,
            agent_name=request.agent_name,
            experiment_id=request.experiment_id,
            certification_run_id=request.certification_run_id,
            summary=summary,
            cert_output_dir=cert_output_dir,
            elapsed=elapsed,
        )
        await _write_aggregated_category_metadata(
            agg_cat_col=agg_cat_col,
            certification_id=certification_id,
            agent_id=request.agent_id,
            experiment_id=request.experiment_id,
            cert_output_dir=cert_output_dir,
        )
    except Exception as exc:
        await cert_session_svc.set_failed(
            cert_task_id, "STORAGE_ERROR", str(exc), "storing_metadata",
            traceback.format_exc(),
        )
        return

    # ── Complete ───────────────────────────────────────────────────────────────
    task_result = {
        "total_documents": summary.get("total_documents", 0),
        "total_fault_categories": summary.get("total_fault_categories", 0),
        "fault_categories": summary.get("fault_categories", []),
        "certification_id": certification_id,
        "storage_paths": {
            "aggregated_scorecard": str(cert_output_dir / "aggregation" / "aggregation.json"),
            "certification_report": str(cert_output_dir / "cert-builder" / "certification.json"),
            "summary": str(cert_output_dir / "pipeline_summary.json"),
            "html_report": report_paths.get("html_path", ""),
            "pdf_report": report_paths.get("pdf_path", ""),
        },
        "processing_time_seconds": round(elapsed, 1),
    }
    await cert_session_svc.set_completed(cert_task_id, task_result)
