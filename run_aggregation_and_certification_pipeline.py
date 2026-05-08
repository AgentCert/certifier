"""
End-to-end pipeline: Aggregation → (optional) Statistical Hypothesis → Certification.

Reads per-run *metrics.json files from a directory, aggregates them into a
fault-category and agent-level scorecard, optionally runs the H01–H09
statistical hypothesis framework against the same metrics (merged into the
scorecard under the ``statistical_hypothesis`` key), then feeds the
aggregated scorecard into the certification framework to produce the final
certification report.

Usage:
    python -m agentcert.run_aggregation_and_certification_pipeline \
        --metrics-dir <directory_with_metrics_json_files> \
        --output-dir <output_directory> \
        --agent-id <agent_id> \
        --agent-name <agent_name> \
        [--certification-run-id <run_id>] \
        [--runs-per-fault 30] \
        [--advanced-analysis] \
        [--ground-truth-dir <gt_dir>] \
        [--fault-categories-config <path>] \
        [--min-runs 30] [--alpha 0.05] [--n-resamples 10000] \
        [--random-state <int>] \
        [--debug]
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from utils.custom_errors import MyCustomError, OrchestratorError, ConfigLoaderError
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from utils.azure_openai_util import AzureLLMClient
    from utils.load_config import ConfigLoader
    from utils.setup_logging import logger
except ImportError:
    AzureLLMClient = None
    ConfigLoader = None
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

from aggregator.scripts.aggregation import (
    AggregationOrchestrator,
    DirectoryQueryService,
)
from cert_builder.scripts.certification_pipeline import CertificationPipeline

# Reuse hypothesis-grouping helpers from the agg+hypothesis pipeline to keep
# the two pipelines in lockstep without duplicating logic.
from run_aggregation_and_hypothesis_pipeline import (
    _DEFAULT_FAULT_CATEGORIES_CONFIG,
    _group_docs_by_category,
    _load_fault_categories_config,
    _run_hypothesis_safely,
)


def _save_json(data: dict, path: Path) -> None:
    """Write dict to JSON file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=4, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        raise OrchestratorError(
            f"Failed to write JSON file: {path}",
            original_exception=exc,
        ) from exc


async def run_pipeline(
    metrics_dir: str,
    output_dir: str,
    agent_id: str,
    agent_name: str,
    certification_run_id: str = "",
    runs_per_fault: int = 30,
    debug: bool = False,
    config: Optional[Dict[str, Any]] = None,
    advanced_analysis: bool = False,
    ground_truth_dir: Optional[str] = None,
    fault_categories_config: Optional[str] = None,
    min_runs: int = 30,
    alpha: float = 0.05,
    n_resamples: int = 10000,
    random_state: Optional[int] = None,
) -> Dict[str, Any]:
    """Run the full pipeline: aggregation, optional hypothesis testing, certification.

    Args:
        metrics_dir: Directory containing per-run *metrics.json files.
        output_dir: Directory for all pipeline outputs.
        agent_id: Agent ID to aggregate metrics for.
        agent_name: Agent name for the certification scorecard.
        certification_run_id: Optional certification run identifier.
        runs_per_fault: Expected number of runs per fault.
        debug: If True, persist intermediate outputs.
        config: Optional configuration dict. Loaded from ConfigLoader if None.
        advanced_analysis: If True, run the H01–H09 hypothesis framework
            and merge its output into the aggregated scorecard under the
            ``statistical_hypothesis`` key (before certification).
        ground_truth_dir: Ground truth directory required when
            ``advanced_analysis`` is True. Missing/invalid GT logs a warning
            and skips the hypothesis step (does not fail the pipeline).
        fault_categories_config: Optional path to fault categories JSON
            (defaults to ``configs/fault_categories.json``).
        min_runs / alpha / n_resamples / random_state: Hypothesis parameters.

    Returns:
        The final certification report dict.
    """
    if config is None and ConfigLoader:
        try:
            config = ConfigLoader.load_config()
        except Exception as exc:
            logger.warning(f"Could not load config: {exc}. Using defaults.")
            config = {}
    config = config or {}

    output_path = (Path(output_dir) / certification_run_id) if certification_run_id else Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    try:
        llm_client = AzureLLMClient(config=config) if AzureLLMClient else None
    except Exception as exc:
        raise OrchestratorError(
            "Failed to initialize AzureLLMClient",
            original_exception=exc,
        ) from exc

    try:
        # ------------------------------------------------------------------
        # Step 1: Aggregation
        # ------------------------------------------------------------------
        logger.info("=" * 60)
        logger.info("STEP 1: Aggregation")
        logger.info("=" * 60)
        try:
            query_service = DirectoryQueryService(metrics_dir)

            # Verify documents exist
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
                db_client=None,  # No MongoDB storage; output goes to file
            )

            aggregated_scorecard = await orchestrator.aggregate_all(
                agent_id=agent_id,
                agent_name=agent_name,
                certification_run_id=certification_run_id,
                runs_per_fault=runs_per_fault,
                store_results=False,
            )
        except MyCustomError:
            raise
        except Exception as exc:
            logger.error(f"Aggregation step failed: {exc}", exc_info=True)
            raise OrchestratorError(
                "Aggregation step failed",
                original_exception=exc,
            ) from exc
        # Persist aggregated scorecard
        scorecard_path = output_path / f"aggregated_scorecard_output_{agent_id}.json"
        _save_json(aggregated_scorecard, scorecard_path)
        logger.info(f"Aggregated scorecard written to {scorecard_path}")

        # ------------------------------------------------------------------
        # Step 2: Certification
        # ------------------------------------------------------------------
        logger.info("=" * 60)
        logger.info("STEP 2: Certification")
        logger.info("=" * 60)

        report_path = output_path / f"certification_report_{agent_id}.json"
        try:
            cert_pipeline = CertificationPipeline(
                input_path=scorecard_path,
                output_path=report_path,
                debug=debug,
            )
            report = await cert_pipeline.run()
        except MyCustomError:
            raise
        except Exception as exc:
            logger.error(f"Certification step failed: {exc}", exc_info=True)
            raise OrchestratorError(
                "Certification step failed",
                original_exception=exc,
            ) from exc

        logger.info(f"Certification report written to {report_path}")

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        summary = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "certification_run_id": certification_run_id,
            "metrics_dir": str(Path(metrics_dir).resolve()),
            "total_documents": len(agent_docs),
            "total_fault_categories": len(categories),
            "fault_categories": categories,
            "advanced_analysis": advanced_analysis,
            "ground_truth_dir": str(Path(ground_truth_dir).resolve())
            if ground_truth_dir
            else None,
            "statistical_hypothesis_included": "statistical_hypothesis"
            in aggregated_scorecard,
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
        if llm_client:
            await llm_client.close()
            logger.info("LLM client connection closed.")


def _print_aggregation_summary(
    scorecard: Dict[str, Any],
    agent_id: str,
    agent_name: str,
) -> None:
    """Print a human-readable summary of the aggregated scorecard."""
    print("\n" + "=" * 70)
    print("AGGREGATION SUMMARY")
    print("=" * 70)
    print(f"  Agent: {agent_name} ({agent_id})")
    print(f"  Total categories: {scorecard.get('total_fault_categories', 0)}")
    print(f"  Total faults tested: {scorecard.get('total_faults_tested', 0)}")
    print(f"  Total runs: {scorecard.get('total_runs', 0)}")

    for sc in scorecard.get("fault_category_scorecards", []):
        print(f"\n  Category: {sc['fault_category']}")
        print(f"    Total runs: {sc['total_runs']}")
        print(f"    Faults tested: {', '.join(sc.get('faults_tested', []))}")

        derived = sc.get("derived_metrics", {})
        print(f"    Detection success rate : {derived.get('fault_detection_success_rate')}")
        print(f"    Mitigation success rate: {derived.get('fault_mitigation_success_rate')}")
        print(f"    RAI compliance rate    : {derived.get('rai_compliance_rate')}")
        print(f"    Security compliance    : {derived.get('security_compliance_rate')}")

        num = sc.get("numeric_metrics", {})
        ttd = num.get("time_to_detect", {})
        if ttd.get("median") is not None:
            print(f"    Time to detect (median) : {ttd['median']}s")
        ttm = num.get("time_to_mitigate", {})
        if ttm.get("median") is not None:
            print(f"    Time to mitigate (median): {ttm['median']}s")

    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end pipeline: Aggregation -> (optional Hypothesis) -> Certification"
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
        "--debug",
        action="store_true",
        help="Persist intermediate outputs for debugging.",
    )
    parser.add_argument(
        "--advanced-analysis",
        action="store_true",
        help="If set, run the H01-H09 statistical hypothesis framework "
             "between aggregation and certification, merging results into "
             "the aggregated scorecard under the 'statistical_hypothesis' key.",
    )
    parser.add_argument(
        "--ground-truth-dir",
        type=str,
        default=None,
        help="Ground truth directory (per-fault YAMLs). Required when "
             "--advanced-analysis is set; otherwise hypothesis is skipped.",
    )
    parser.add_argument(
        "--fault-categories-config",
        type=str,
        default=None,
        help="Path to fault categories JSON (defaults to "
             "configs/fault_categories.json).",
    )
    parser.add_argument(
        "--min-runs",
        type=int,
        default=30,
        help="Hypothesis: minimum detected runs per category (default 30).",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.05,
        help="Hypothesis: significance level (default 0.05).",
    )
    parser.add_argument(
        "--n-resamples",
        type=int,
        default=10000,
        help="Hypothesis: bootstrap resamples (default 10000).",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=None,
        help="Hypothesis: random seed (default None).",
    )
    args = parser.parse_args()

    try:
        report = asyncio.run(
            run_pipeline(
                metrics_dir=args.metrics_dir,
                output_dir=args.output_dir,
                agent_id=args.agent_id,
                agent_name=args.agent_name,
                certification_run_id=args.certification_run_id,
                runs_per_fault=args.runs_per_fault,
                debug=args.debug,
            )
        )
    except MyCustomError as exc:
        logger.error(f"Pipeline aborted: {exc}")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Unexpected pipeline error: {exc}", exc_info=True)
        sys.exit(1)

    if report:
        print(f"\nPipeline Complete")
        print(f"{'=' * 50}")
        print(f"  Certification report generated successfully.")
        print(f"  Output: {args.output_dir}")
    else:
        print("\nPipeline failed. Check logs for details.")


if __name__ == "__main__":
    main()
