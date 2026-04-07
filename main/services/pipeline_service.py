import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from utils.azure_openai_util import AzureLLMClient
    from utils.load_config import ConfigLoader
    from utils.setup_logging import logger
except ImportError:
    # Fallback when running outside the full certifier package (e.g. unit tests
    # that only import this module).  Pipeline calls that need an LLM client will
    # receive None and must handle it gracefully.
    AzureLLMClient = None
    ConfigLoader = None
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

from fault_analyzer import FaultBucketingPipeline
from metrics_extractor import ExtractionResult, TraceMetricsExtractor
from aggregator.scripts.aggregation import AggregationOrchestrator, DirectoryQueryService
from cert_builder.scripts.certification_pipeline import CertificationPipeline


# ── Bucketing / Extraction helpers ────────────────────────────────────────────

def _build_fault_config_from_bucket(bucket_data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a fault bucket's metadata dict into the ``fault_configuration`` format
    expected by :class:`~metrics_extractor.TraceMetricsExtractor`.

    The bucket may store ideal-action fields at the top level or nested inside
    ``ground_truth``; this function normalises both layouts.
    """
    ground_truth = bucket_data.get("ground_truth") or {}

    # Promote top-level ideal fields into ground_truth if present
    ideal_course = bucket_data.get("ideal_course_of_action")
    ideal_trajectory = bucket_data.get("ideal_tool_usage_trajectory")
    if ideal_course is not None:
        ground_truth["ideal_course_of_action"] = ideal_course
    if ideal_trajectory is not None:
        ground_truth["ideal_tool_usage_trajectory"] = ideal_trajectory

    return {
        "fault_id": bucket_data.get("fault_id", "unknown"),
        "fault_name": bucket_data.get("fault_name", "unknown"),
        # "severity" in bucket maps to "fault_category" in the extractor schema
        "fault_category": bucket_data.get("severity", "unknown"),
        "experiment_id": bucket_data.get("experiment_id"),
        "run_id": bucket_data.get("run_id"),
        # Fall back to detected_at if injection_timestamp is absent
        "injection_timestamp": bucket_data.get("injection_timestamp") or bucket_data.get("detected_at"),
        "fault_configuration": {
            "target_service": bucket_data.get("target_pod", ""),
            "target_namespace": bucket_data.get("namespace", ""),
        },
        "ground_truth": ground_truth,
        "agent": {
            "agent_id": bucket_data.get("agent_id"),
            "agent_name": bucket_data.get("agent_name"),
            "agent_version": bucket_data.get("agent_version"),
        },
    }


# ── Aggregation / Certification helpers ───────────────────────────────────────

def _save_json(data: dict, path: Path) -> None:
    """Atomically write *data* as indented JSON to *path*, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=4, default=str, ensure_ascii=False),
        encoding="utf-8",
    )


def _print_aggregation_summary(
    scorecard: Dict[str, Any],
    agent_id: str,
    agent_name: str,
) -> None:
    """Log a human-readable summary of the aggregated scorecard at INFO level."""
    logger.info("=" * 70)
    logger.info("AGGREGATION SUMMARY")
    logger.info("=" * 70)
    logger.info(f"  Agent: {agent_name} ({agent_id})")
    logger.info(f"  Total categories: {scorecard.get('total_fault_categories', 0)}")
    logger.info(f"  Total faults tested: {scorecard.get('total_faults_tested', 0)}")
    logger.info(f"  Total runs: {scorecard.get('total_runs', 0)}")
    for sc in scorecard.get("fault_category_scorecards", []):
        logger.info(f"\n  Category: {sc['fault_category']}")
        logger.info(f"    Total runs: {sc['total_runs']}")
        logger.info(f"    Faults tested: {', '.join(sc.get('faults_tested', []))}")
        derived = sc.get("derived_metrics", {})
        logger.info(f"    Detection success rate : {derived.get('fault_detection_success_rate')}")
        logger.info(f"    Mitigation success rate: {derived.get('fault_mitigation_success_rate')}")
        logger.info(f"    RAI compliance rate    : {derived.get('rai_compliance_rate')}")
        logger.info(f"    Security compliance    : {derived.get('security_compliance_rate')}")
        num = sc.get("numeric_metrics", {})
        ttd = num.get("time_to_detect", {})
        if ttd.get("median") is not None:
            logger.info(f"    Time to detect (median) : {ttd['median']}s")
        ttm = num.get("time_to_mitigate", {})
        if ttm.get("median") is not None:
            logger.info(f"    Time to mitigate (median): {ttm['median']}s")
    logger.info("=" * 70)


# ── Services ──────────────────────────────────────────────────────────────────

class BucketPipelineService:
    async def execute_pipeline(
        self,
        trace_file: str,
        output_dir: str,
        batch_size: int,
        store_to_mongodb: bool,
        config: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Run fault bucketing then per-bucket metric extraction (Phase 0+1).

        Args:
            trace_file:       Absolute path to the raw Langfuse trace JSON.
            output_dir:       Root directory for all outputs (buckets + metrics).
            batch_size:       Number of trace events per LLM classification batch.
            store_to_mongodb: When True, persist extracted metrics to MongoDB.
            config:           App config dict; loaded from ``configs/configs.json``
                              if not provided.

        Returns:
            List of per-fault result dicts, each containing ``quantitative``,
            ``qualitative``, and ``token_usage`` sub-dicts.
        """
        if config is None and ConfigLoader:
            try:
                config = ConfigLoader.load_config()
            except Exception as exc:
                logger.warning(f"Could not load config: {exc}. Using defaults.")
                config = {}
        config = config or {}

        output_path = Path(output_dir)
        buckets_dir = output_path / "fault_buckets"
        metrics_dir = output_path / "metrics"
        metrics_dir.mkdir(parents=True, exist_ok=True)

        logger.info("=" * 60)
        logger.info("STEP 1: Fault Bucketing")
        logger.info("=" * 60)

        pipeline = FaultBucketingPipeline(
            trace_file_path=trace_file,
            output_dir=str(buckets_dir),
            config=config,
            batch_size=batch_size,
        )
        buckets = await pipeline.run()

        if not buckets:
            logger.warning("No fault buckets produced. Nothing to extract.")
            return []

        logger.info(
            f"Fault bucketing produced {len(buckets)} bucket(s). "
            f"Output at: {buckets_dir}"
        )

        logger.info("=" * 60)
        logger.info("STEP 2: Metric Extraction from Fault Buckets")
        logger.info("=" * 60)

        results: List[Dict[str, Any]] = []

        for fault_id, bucket in buckets.items():
            logger.info(f"--- Extracting metrics for fault: {fault_id} ---")
            bucket_dict = bucket.to_dict()
            events = bucket_dict.get("events", [])

            if not events:
                logger.warning(f"Bucket '{fault_id}' has no events, skipping.")
                continue

            run_id = bucket_dict.get("run_id", "")
            # Build a filesystem-safe filename prefix from the fault + run identifiers
            safe_name = (
                f"{fault_id}_{run_id}".replace("/", "_").replace(" ", "_")
                if run_id
                else fault_id.replace("/", "_").replace(" ", "_")
            )

            # Write the bucket's events to a temp file so TraceMetricsExtractor
            # can consume them via its standard file-based interface
            trace_tmp = metrics_dir / f"{safe_name}_trace.json"
            with open(trace_tmp, "w", encoding="utf-8") as f:
                json.dump(events, f, indent=2, default=str)

            # Assemble and persist the fault config that accompanies the trace
            fault_cfg = _build_fault_config_from_bucket(bucket_dict)
            fault_cfg_tmp = metrics_dir / f"{safe_name}_fault_config.json"
            with open(fault_cfg_tmp, "w", encoding="utf-8") as f:
                json.dump(fault_cfg, f, indent=2, default=str)

            extractor = TraceMetricsExtractor(
                config=config,
                fault_config_path=str(fault_cfg_tmp),
            )
            try:
                extraction_result: ExtractionResult = (
                    await extractor.extract_metrics_async(
                        str(trace_tmp), store_to_mongodb=store_to_mongodb
                    )
                )
            except Exception as exc:
                logger.error(f"Metric extraction failed for '{fault_id}': {exc}")
                continue

            result_dict = {
                "fault_id": fault_id,
                "run_id": run_id,
                "fault_name": bucket.fault_name,
                "quantitative": extraction_result.quantitative.model_dump(mode="json"),
                "qualitative": extraction_result.qualitative.model_dump(mode="json"),
                "token_usage": extraction_result.token_usage.to_dict(),
            }
            # Only include the MongoDB doc ID when the caller requested storage
            if extraction_result.mongodb_document_id:
                result_dict["mongodb_document_id"] = extraction_result.mongodb_document_id

            metrics_file = metrics_dir / f"{safe_name}_metrics.json"
            with open(metrics_file, "w", encoding="utf-8") as f:
                json.dump(result_dict, f, indent=2, default=str)

            logger.info(
                f"Metrics for '{fault_id}' written to {metrics_file.name}. "
                f"Tokens used: {extraction_result.token_usage.total_tokens}"
            )
            results.append(result_dict)

        # Aggregate token counts from all per-fault extraction results for the summary
        summary_file = output_path / "pipeline_summary.json"
        summary = {
            "trace_file": str(Path(trace_file).name),
            "run_id": results[0].get("run_id", "") if results else "",
            "total_faults": len(buckets),
            "faults_extracted": len(results),
            "bucketing_tokens": {
                "input": pipeline.total_input_tokens,
                "output": pipeline.total_output_tokens,
                "total": pipeline.total_input_tokens + pipeline.total_output_tokens,
            },
            "extraction_tokens": {
                "input": sum(r["token_usage"]["input_tokens"] for r in results),
                "output": sum(r["token_usage"]["output_tokens"] for r in results),
                "total": sum(r["token_usage"]["total_tokens"] for r in results),
            },
            "fault_results": [
                {
                    "fault_id": r["fault_id"],
                    "fault_name": r["fault_name"],
                    "mongodb_document_id": r.get("mongodb_document_id"),
                }
                for r in results
            ],
        }
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)

        logger.info("=" * 60)
        logger.info("Pipeline Complete")
        logger.info("=" * 60)
        logger.info(f"  Faults bucketed : {len(buckets)}")
        logger.info(f"  Metrics extracted: {len(results)}")
        logger.info(f"  Output directory : {output_path}")
        logger.info(f"  Summary file     : {summary_file.name}")

        return results


class CertPipelineService:
    async def execute_pipeline(
        self,
        metrics_dir: str,
        output_dir: str,
        agent_id: str,
        agent_name: str,
        certification_run_id: str = "",
        runs_per_fault: int = 30,
        debug: bool = False,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run aggregation then certification (Phase 2+3).

        Args:
            metrics_dir:          Directory containing ``*metrics.json`` files from Phase 1.
            output_dir:           Root directory for aggregated scorecard + cert report.
            agent_id:             Filters metric documents to this agent.
            agent_name:           Human-readable name written into the scorecard.
            certification_run_id: Optional identifier for this certification run.
            runs_per_fault:       Expected N runs per fault used for statistical
                                  significance checks in the aggregator.
            debug:                When True, the cert pipeline retains intermediate
                                  outputs for post-mortem inspection.
            config:               App config dict; loaded from ``configs/configs.json``
                                  if not provided.

        Returns:
            The certification report dict, or an empty dict if no metric docs were found.
        """
        if config is None and ConfigLoader:
            try:
                config = ConfigLoader.load_config()
            except Exception as exc:
                logger.warning(f"Could not load config: {exc}. Using defaults.")
                config = {}
        config = config or {}

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # LLM client is used by the aggregator's Council and cert builder's narrative sections
        llm_client = AzureLLMClient(config=config) if AzureLLMClient else None

        try:
            logger.info("=" * 60)
            logger.info("STEP 1: Aggregation")
            logger.info("=" * 60)

            # DirectoryQueryService reads *metrics.json files from disk
            query_service = DirectoryQueryService(metrics_dir)
            agent_docs = query_service.query_runs_by_agent(agent_id)
            if not agent_docs:
                logger.error(
                    f"No per-run metric documents found for agent_id='{agent_id}' "
                    f"in directory '{metrics_dir}'. "
                    "Ensure per-run metrics have been generated first."
                )
                return {}

            logger.info(f"Found {len(agent_docs)} per-run documents for agent_id='{agent_id}'")

            categories = query_service.get_all_fault_categories(agent_id=agent_id)
            if not categories:
                logger.error(f"No fault categories found for agent_id='{agent_id}'.")
                return {}

            logger.info(f"Found fault categories: {categories}")

            orchestrator = AggregationOrchestrator(
                llm_client=llm_client,
                query_service=query_service,
                db_client=None,  # No MongoDB storage; scorecard goes to file only
            )

            aggregated_scorecard = await orchestrator.aggregate_all(
                agent_id=agent_id,
                agent_name=agent_name,
                certification_run_id=certification_run_id,
                runs_per_fault=runs_per_fault,
                store_results=False,
            )

            scorecard_path = output_path / f"aggregated_scorecard_output_{agent_id}.json"
            _save_json(aggregated_scorecard, scorecard_path)
            logger.info(f"Aggregated scorecard written to {scorecard_path}")

            _print_aggregation_summary(aggregated_scorecard, agent_id, agent_name)

            logger.info("=" * 60)
            logger.info("STEP 2: Certification")
            logger.info("=" * 60)

            report_path = output_path / f"certification_report_{agent_id}.json"

            cert_pipeline = CertificationPipeline(
                input_path=scorecard_path,
                output_path=report_path,
                debug=debug,
            )
            report = await cert_pipeline.run()

            logger.info(f"Certification report written to {report_path}")

            # Write a lightweight summary alongside the full report for quick inspection
            summary = {
                "agent_id": agent_id,
                "agent_name": agent_name,
                "certification_run_id": certification_run_id,
                "metrics_dir": str(Path(metrics_dir).resolve()),
                "total_documents": len(agent_docs),
                "total_fault_categories": len(categories),
                "fault_categories": categories,
                "aggregated_scorecard_path": str(scorecard_path),
                "certification_report_path": str(report_path),
            }
            summary_path = output_path / "pipeline_summary.json"
            _save_json(summary, summary_path)

            logger.info("=" * 60)
            logger.info("Pipeline Complete")
            logger.info("=" * 60)
            logger.info(f"  Agent            : {agent_name} ({agent_id})")
            logger.info(f"  Fault categories : {len(categories)}")
            logger.info(f"  Per-run documents: {len(agent_docs)}")
            logger.info(f"  Output directory : {output_path}")
            logger.info(f"  Summary file     : {summary_path.name}")

            return report

        finally:
            # Always close the LLM client connection pool, even on exception
            if llm_client:
                await llm_client.close()
                logger.info("LLM client connection closed.")
