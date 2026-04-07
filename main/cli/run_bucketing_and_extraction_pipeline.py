"""
CLI entry point: Fault Bucketing → Metric Extraction (Phase 0+1).

Usage:
    python run_bucketing_and_extraction_pipeline.py \
        --trace-file <path/to/trace.json> \
        --output-dir <output_directory> \
        [--batch-size 10] [--store]
"""

import argparse
import asyncio

from main.services.pipeline_service import BucketPipelineService


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

    results = asyncio.run(
        BucketPipelineService().execute_pipeline(
            trace_file=args.trace_file,
            output_dir=args.output_dir,
            batch_size=args.batch_size,
            store_to_mongodb=args.store,
        )
    )

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
