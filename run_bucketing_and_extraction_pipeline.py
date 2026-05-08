"""
End-to-end pipeline: Fault Bucketing → Metric Extraction.

Runs fault bucketing on a raw Langfuse trace to split interleaved events into
per-fault buckets, then runs metric extraction on each bucket to produce
quantitative and qualitative metrics per fault.

Usage:
    python -m agentcert.run_pipeline \
        --trace-file <trace.json> \
        --output-dir <output_directory> \
        [--batch-size 10] \
        [--store]
"""

import sys
import argparse
import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from utils.custom_errors import MyCustomError, OrchestratorError

try:
    from utils.load_config import ConfigLoader
    from utils.setup_logging import logger
except ImportError:
    ConfigLoader = None
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

from fault_analyzer import FaultBucketingPipeline
from metrics_extractor import (
    TraceMetricsExtractor,
    ExtractionResult,
)


async def run_pipeline(
    trace_file: str,
    output_dir: str,
    batch_size: int = 10,
    store_to_mongodb: bool = False,
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Run the full pipeline: fault bucketing then per-bucket metric extraction.

    Args:
        trace_file: Path to the raw Langfuse trace JSON file.
        output_dir: Directory for all pipeline outputs (buckets + metrics).
        batch_size: Batch size for the fault bucketing LLM classifier.
        store_to_mongodb: Whether to store extracted metrics to MongoDB.
        config: Optional configuration dict. Loaded from ConfigLoader if None.

    Returns:
        List of per-fault result dicts, each containing the fault_id and
        the extracted quantitative/qualitative metrics.
    """
    if config is None and ConfigLoader:
        try:
            config = ConfigLoader.load_config()
        except Exception as exc:
            logger.warning(f"Could not load config: {exc}. Using defaults.")
            config = {}
    config = config or {}

    base_output = Path(output_dir)

    # ------------------------------------------------------------------
    # Step 1: Fault Bucketing
    # ------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("STEP 1: Fault Bucketing")
    logger.info("=" * 60)

    # Run bucketing to a temporary location; experiment_id is only known
    # after the pipeline parses the trace.
    temp_buckets_dir = base_output / "fault_buckets"
    
    try:
        pipeline = FaultBucketingPipeline(
            trace_file_path=trace_file,
            output_dir=str(temp_buckets_dir),
            config=config,
            batch_size=batch_size,
        )
        buckets = await pipeline.run()
    except MyCustomError:
        # Already logged by the custom error; re-raise to abort the pipeline
        raise
    except Exception as exc:
        logger.error(f"Fault bucketing step failed: {exc}", exc_info=True)
        raise OrchestratorError(
            "Fault bucketing step failed", original_exception=exc
        ) from exc

    if not buckets:
        logger.warning("No fault buckets produced. Nothing to extract.")
        return []

    # Resolve output path with experiment_id extracted from the trace
    experiment_id = pipeline.experiment_id
    if experiment_id:
        output_path = base_output / experiment_id
    else:
        output_path = base_output

    buckets_dir = output_path / "fault_buckets"
    metrics_dir = output_path / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)

    # Move bucket files into the experiment_id-scoped directory
    if temp_buckets_dir != buckets_dir:
        if buckets_dir.exists():
            shutil.rmtree(buckets_dir)
        shutil.move(str(temp_buckets_dir), str(buckets_dir))

        # Move ground_truth folder (written as sibling of fault_buckets)
        temp_gt_dir = temp_buckets_dir.parent / "ground_truth"
        if temp_gt_dir.exists():
            final_gt_dir = output_path / "ground_truth"
            if final_gt_dir.exists():
                shutil.rmtree(final_gt_dir)
            shutil.move(str(temp_gt_dir), str(final_gt_dir))

    logger.info(
        f"Fault bucketing produced {len(buckets)} bucket(s). "
        f"Output at: {buckets_dir}"
    )

    # ------------------------------------------------------------------
    # Step 2: Metric Extraction per Bucket
    # ------------------------------------------------------------------
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

        # Write the full bucket JSON (metadata + events) for the extractor.
        # The extractor will auto-detect the bucket format and extract both
        # metadata and events from it.
        run_id = bucket_dict.get("run_id", "")
        safe_name = f"{fault_id}_{run_id}".replace("/", "_").replace(" ", "_") if run_id else fault_id.replace("/", "_").replace(" ", "_")
        trace_tmp = metrics_dir / f"{safe_name}_trace.json"
        
        try:
            with open(trace_tmp, "w", encoding="utf-8") as f:
                json.dump(bucket_dict, f, indent=2, default=str)
        except (OSError, TypeError) as exc:
            logger.error(
                f"Failed to write temp trace for '{fault_id}': {exc}. Skipping.",
                exc_info=True,
            )
            continue

        # Run metric extraction — bucket metadata is read from the trace file
        try:
            extractor = TraceMetricsExtractor(config=config)
        except MyCustomError as exc:
            logger.error(f"Extractor init failed for '{fault_id}': {exc}. Skipping.")
            continue
        except Exception as exc:
            logger.error(
                f"Unexpected extractor init error for '{fault_id}': {exc}. Skipping.",
                exc_info=True,
            )
            continue
        try:
            extraction_result: ExtractionResult = await extractor.extract_metrics_async(
                str(trace_tmp), store_to_mongodb=store_to_mongodb
            )
        except MyCustomError as exc:
            logger.error(f"Metric extraction failed for '{fault_id}' (custom): {exc}. Skipping.")
            continue
        except Exception as exc:
            logger.error(
                f"Metric extraction failed for '{fault_id}': {exc}. Skipping.",
                exc_info=True,
            )
            continue

        # Persist per-fault metrics to disk
        result_dict = {
            "fault_id": fault_id,
            "run_id": run_id,
            "fault_name": bucket.fault_name,
            "quantitative": extraction_result.quantitative.model_dump(mode="json"),
            "qualitative": extraction_result.qualitative.model_dump(mode="json"),
            "token_usage": extraction_result.token_usage.to_dict(),
        }
        if extraction_result.mongodb_document_id:
            result_dict["mongodb_document_id"] = extraction_result.mongodb_document_id

        metrics_file = metrics_dir / f"{safe_name}_metrics.json"
        
        try:
            with open(metrics_file, "w", encoding="utf-8") as f:
                json.dump(result_dict, f, indent=2, default=str)
        except (OSError, TypeError) as exc:
            logger.error(
                f"Failed to write metrics file for '{fault_id}': {exc}. Skipping.",
                exc_info=True,
            )
            continue

        logger.info(
            f"Metrics for '{fault_id}' written to {metrics_file.name}. "
            f"Tokens used: {extraction_result.token_usage.total_tokens}"
        )
        results.append(result_dict)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
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

    try:
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)
    except (OSError, TypeError) as exc:
        raise OrchestratorError(
            f"Failed to write pipeline summary: {summary_file}",
            original_exception=exc,
        ) from exc

    logger.info("=" * 60)
    logger.info("Pipeline Complete")
    logger.info("=" * 60)
    logger.info(f"  Faults bucketed : {len(buckets)}")
    logger.info(f"  Metrics extracted: {len(results)}")
    logger.info(f"  Output directory : {output_path}")
    logger.info(f"  Summary file     : {summary_file.name}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end pipeline: Fault Bucketing → Metric Extraction"
    )
    parser.add_argument(
        "--trace-file",
        required=True,
        help="Path to the raw Langfuse trace JSON file.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for all pipeline outputs (buckets + metrics).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of events per LLM classification batch (default: 10).",
    )
    parser.add_argument(
        "--store",
        action="store_true",
        help="Store extracted metrics to MongoDB.",
    )
    args = parser.parse_args()

    try:
        results = asyncio.run(
            run_pipeline(
                trace_file=args.trace_file,
                output_dir=args.output_dir,
                batch_size=args.batch_size,
                store_to_mongodb=args.store,
            )
        )
    except MyCustomError as exc:
        logger.error(f"Pipeline aborted: {exc}")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Unexpected pipeline error: {exc}", exc_info=True)
        sys.exit(1)

    # Print summary to console
    print(f"\nPipeline Complete")
    print(f"{'=' * 50}")
    for r in results:
        fault_id = r["fault_id"]
        quant = r["quantitative"]
        print(
            f"  {fault_id}: "
            f"TTD={quant.get('time_to_detect', 'N/A')}s, "
            f"TTR={quant.get('time_to_mitigate', 'N/A')}s, "
            f"steps={quant.get('trajectory_steps', 'N/A')}"
        )
    print(f"\n  Total faults: {len(results)}")
    print(f"  Output: {args.output_dir}")


if __name__ == "__main__":
    main()
