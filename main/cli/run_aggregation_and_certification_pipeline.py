"""
CLI entry point: Aggregation → Certification (Phase 2+3).

Usage:
    python run_aggregation_and_certification_pipeline.py \
        --metrics-dir <directory_with_metrics_json_files> \
        --output-dir <output_directory> \
        --agent-id <agent_id> \
        --agent-name <agent_name> \
        [--certification-run-id <run_id>] \
        [--runs-per-fault 30] [--include-ciso-finops] [--debug]
"""

import argparse
import asyncio

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from main.services.pipeline_service import CertPipelineService


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end pipeline: Aggregation → Certification"
    )
    parser.add_argument(
        "--metrics-dir",
        required=True,
        help="Directory containing per-run *metrics.json files.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for all pipeline outputs.",
    )
    parser.add_argument(
        "--agent-id",
        required=True,
        help="Agent ID to aggregate metrics for.",
    )
    parser.add_argument(
        "--agent-name",
        required=True,
        help="Agent name for the certification scorecard.",
    )
    parser.add_argument(
        "--certification-run-id",
        type=str,
        default="",
        help="Optional certification run ID.",
    )
    parser.add_argument(
        "--runs-per-fault",
        type=int,
        default=30,
        help="Expected number of runs per fault (default: 30).",
    )
    parser.add_argument(
        "--include-ciso-finops",
        action="store_true",
        default=False,
        help=(
            "Include CISO and FinOps as tested dimensions in the scorecard/report "
            "(default: false). FinOps reports real data (ITBench scenario 38's "
            "HPA-misconfig runs); CISO has no execution path in ACE yet, so an "
            "explicit 'not implemented' note is added to the report's Limitations "
            "section instead of silently omitting it. When false (default), any "
            "ciso_fault/finops_fault-categorized runs are excluded from the "
            "scorecard entirely."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Persist intermediate outputs for debugging.",
    )
    args = parser.parse_args()

    report = asyncio.run(
        CertPipelineService().execute_pipeline(
            metrics_dir=args.metrics_dir,
            output_dir=args.output_dir,
            agent_id=args.agent_id,
            agent_name=args.agent_name,
            certification_run_id=args.certification_run_id,
            runs_per_fault=args.runs_per_fault,
            debug=args.debug,
            include_ciso_finops=args.include_ciso_finops,
        )
    )

    if report:
        print(f"\nPipeline Complete")
        print(f"{'=' * 50}")
        print(f"  Certification report generated successfully.")
        print(f"  Output: {args.output_dir}")
    else:
        print("\nPipeline failed. Check logs for details.")


if __name__ == "__main__":
    main()
