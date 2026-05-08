"""
End-to-end pipeline: Aggregation (+ optional Statistical Hypothesis Analysis).

Reads per-run ``*metrics.json`` files from a flat (or nested) directory,
aggregates them into a fault-category and agent-level scorecard, and
optionally runs the H01–H09 statistical hypothesis framework against the
same metrics. The hypothesis output is merged into the aggregated
scorecard under the key ``statistical_hypothesis``.

Usage::

    python run_aggregation_and_hypothesis_pipeline.py \\
        --metrics-dir <directory_with_metrics_json_files> \\
        --output-dir <output_directory> \\
        --agent-id <agent_id> \\
        --agent-name <agent_name> \\
        [--certification-run-id <run_id>] \\
        [--runs-per-fault 30] \\
        [--advanced-analysis] \\
        [--ground-truth-dir <gt_dir>] \\
        [--fault-categories-config <path>] \\
        [--min-runs 30] [--alpha 0.05] [--n-resamples 10000] \\
        [--random-state <int>] [--debug]

When ``--advanced-analysis`` is omitted, the ``statistical_hypothesis``
key is not included in the output. When the flag is set but the
hypothesis run errors or ground truth is missing/invalid, the pipeline
logs a warning and returns the aggregation result without the
``statistical_hypothesis`` key (no failure).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from utils.azure_openai_util import AzureLLMClient
    from utils.load_config import ConfigLoader
    from utils.setup_logging import logger
except ImportError:  # pragma: no cover - standalone fallback
    AzureLLMClient = None
    ConfigLoader = None
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

from aggregator.scripts.aggregation import (
    AggregationOrchestrator,
    DirectoryQueryService,
)


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent
_DEFAULT_FAULT_CATEGORIES_CONFIG = _REPO_ROOT / "configs" / "fault_categories.json"


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _save_json(data: dict, path: Path) -> None:
    """Write dict to JSON file (UTF-8, indented)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=4, default=str, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_fault_categories_config(path: Path) -> Dict[str, List[str]]:
    """Load the fault category → sub-fault names mapping."""
    if not path.exists():
        logger.warning(f"Fault categories config not found at {path}; returning empty map.")
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("categories"), dict):
        return {k: list(v) for k, v in raw["categories"].items()}
    if isinstance(raw, dict):
        return {k: list(v) for k, v in raw.items() if isinstance(v, list)}
    logger.warning(f"Unexpected schema in {path}; returning empty map.")
    return {}


def _doc_fault_name(doc: Dict[str, Any]) -> Optional[str]:
    """Extract the sub-fault name from a metrics document."""
    return (
        doc.get("fault_name")
        or doc.get("quantitative", {}).get("injected_fault_name")
    )


def _doc_fault_category(
    doc: Dict[str, Any],
    subfault_to_category: Dict[str, str],
) -> Optional[str]:
    """Determine fault_category for a metrics doc.

    Resolution order:
        1. ``fault_category`` (top-level)
        2. ``quantitative.injected_fault_category``
        3. lookup ``fault_name`` / ``quantitative.injected_fault_name`` in
           the supplied sub-fault → category map.
    """
    cat = doc.get("fault_category") or doc.get("quantitative", {}).get(
        "injected_fault_category"
    )
    if cat:
        return cat
    sub = _doc_fault_name(doc)
    if sub and sub in subfault_to_category:
        return subfault_to_category[sub]
    return None


def _group_docs_by_category(
    docs: List[Dict[str, Any]],
    fault_categories: Dict[str, List[str]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Group flat metrics docs into ``{category: [doc, ...]}``.

    Uses the fault_categories config as a fallback mapping when a doc has
    no ``fault_category`` key.
    """
    subfault_to_category: Dict[str, str] = {}
    for cat, subs in fault_categories.items():
        for s in subs:
            subfault_to_category[s] = cat

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    skipped = 0
    for doc in docs:
        cat = _doc_fault_category(doc, subfault_to_category)
        if cat:
            grouped[cat].append(doc)
        else:
            skipped += 1
            logger.warning(
                "Could not determine fault_category for doc with "
                f"fault_name={_doc_fault_name(doc)!r}; skipping."
            )
    if skipped:
        logger.warning(
            f"{skipped} metric docs could not be mapped to a fault_category."
        )
    logger.info(
        "Grouped docs into categories: "
        + ", ".join(f"{k}={len(v)}" for k, v in grouped.items())
    )
    return dict(grouped)


def _run_hypothesis_safely(
    grouped_runs: Dict[str, List[Dict[str, Any]]],
    gt_dir: Path,
    *,
    min_runs: int,
    alpha: float,
    n_resamples: int,
    random_state: Optional[int],
    metrics_dir: Path,
) -> Optional[Dict[str, Any]]:
    """Invoke the hypothesis framework. Return None on any failure."""
    if not gt_dir or not gt_dir.exists():
        logger.warning(
            f"Ground truth directory not found: {gt_dir!s}. "
            "Skipping statistical hypothesis analysis."
        )
        return None

    try:
        from hypothesis_framework.scripts.run_statistical_hypothesis import (
            run_all_hypothesis_tests_from_runs,
        )
    except Exception as exc:  # pragma: no cover - import safety net
        logger.warning(
            f"Could not import hypothesis framework: {exc}. "
            "Skipping statistical hypothesis analysis."
        )
        return None

    try:
        result = run_all_hypothesis_tests_from_runs(
            all_runs=grouped_runs,
            gt_dir=gt_dir,
            min_runs=min_runs,
            alpha=alpha,
            n_resamples=n_resamples,
            random_state=random_state,
            data_dir=str(metrics_dir),
        )
    except Exception as exc:
        logger.warning(
            f"Statistical hypothesis analysis raised an exception: {exc}. "
            "Returning aggregation only.",
            exc_info=True,
        )
        return None

    if isinstance(result, dict) and result.get("error"):
        logger.warning(
            "Statistical hypothesis analysis returned error "
            f"({result.get('error')}): {result.get('message')}. "
            "Returning aggregation only."
        )
        return None

    return result


# ──────────────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────────────


async def run_pipeline(
    metrics_dir: str,
    output_dir: str,
    agent_id: str,
    agent_name: str,
    certification_run_id: str = "",
    runs_per_fault: int = 30,
    advanced_analysis: bool = False,
    ground_truth_dir: Optional[str] = None,
    fault_categories_config: Optional[str] = None,
    min_runs: int = 30,
    alpha: float = 0.05,
    n_resamples: int = 10000,
    random_state: Optional[int] = None,
    debug: bool = False,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run aggregation and (optionally) statistical hypothesis analysis.

    Returns the merged scorecard dictionary. When ``advanced_analysis`` is
    ``True`` and the hypothesis run succeeds, the dictionary contains the
    extra key ``statistical_hypothesis``.
    """
    if config is None and ConfigLoader:
        try:
            config = ConfigLoader.load_config()
        except Exception as exc:
            logger.warning(f"Could not load config: {exc}. Using defaults.")
            config = {}
    config = config or {}

    output_path = (
        (Path(output_dir) / certification_run_id)
        if certification_run_id
        else Path(output_dir)
    )
    output_path.mkdir(parents=True, exist_ok=True)

    llm_client = AzureLLMClient(config=config) if AzureLLMClient else None

    try:
        # ──────────────────────────────────────────────────────────────
        # Step 1: Load metrics + Aggregation
        # ──────────────────────────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("STEP 1: Aggregation")
        logger.info("=" * 60)

        query_service = DirectoryQueryService(metrics_dir)

        agent_docs = query_service.query_runs_by_agent(agent_id)
        if not agent_docs:
            logger.error(
                f"No per-run metric documents found for agent_id='{agent_id}' "
                f"in directory '{metrics_dir}'. "
                "Ensure per-run metrics have been generated first."
            )
            return {}

        logger.info(
            f"Found {len(agent_docs)} per-run documents for agent_id='{agent_id}'"
        )

        categories = query_service.get_all_fault_categories(agent_id=agent_id)
        if not categories:
            logger.error(f"No fault categories found for agent_id='{agent_id}'.")
            return {}

        logger.info(f"Found fault categories: {categories}")

        orchestrator = AggregationOrchestrator(
            llm_client=llm_client,
            query_service=query_service,
            db_client=None,
        )

        aggregated_scorecard = await orchestrator.aggregate_all(
            agent_id=agent_id,
            agent_name=agent_name,
            certification_run_id=certification_run_id,
            runs_per_fault=runs_per_fault,
            store_results=False,
        )

        _print_aggregation_summary(aggregated_scorecard, agent_id, agent_name)

        # ──────────────────────────────────────────────────────────────
        # Step 2 (optional): Statistical Hypothesis Analysis
        # ──────────────────────────────────────────────────────────────
        if advanced_analysis:
            logger.info("=" * 60)
            logger.info("STEP 2: Statistical Hypothesis Analysis")
            logger.info("=" * 60)

            cfg_path = Path(fault_categories_config) if fault_categories_config else _DEFAULT_FAULT_CATEGORIES_CONFIG
            fault_cats = _load_fault_categories_config(cfg_path)

            grouped_runs = _group_docs_by_category(agent_docs, fault_cats)

            gt_path = Path(ground_truth_dir) if ground_truth_dir else None

            hypothesis_result = _run_hypothesis_safely(
                grouped_runs=grouped_runs,
                gt_dir=gt_path,
                min_runs=min_runs,
                alpha=alpha,
                n_resamples=n_resamples,
                random_state=random_state,
                metrics_dir=Path(metrics_dir),
            )

            if hypothesis_result is not None:
                aggregated_scorecard["statistical_hypothesis"] = hypothesis_result
                logger.info("Merged statistical_hypothesis into aggregated scorecard.")
            else:
                logger.info(
                    "statistical_hypothesis key omitted (analysis skipped or failed)."
                )
        else:
            logger.info(
                "Advanced analysis disabled; skipping statistical hypothesis step."
            )

        # ──────────────────────────────────────────────────────────────
        # Persist outputs
        # ──────────────────────────────────────────────────────────────
        scorecard_path = output_path / f"aggregated_with_hypothesis_{agent_id}.json"
        _save_json(aggregated_scorecard, scorecard_path)
        logger.info(f"Aggregated scorecard written to {scorecard_path}")

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
            "output_path": str(scorecard_path),
        }
        summary_path = output_path / "pipeline_summary.json"
        _save_json(summary, summary_path)

        logger.info("=" * 60)
        logger.info("Pipeline Complete")
        logger.info("=" * 60)
        logger.info(f"  Agent            : {agent_name} ({agent_id})")
        logger.info(f"  Fault categories : {len(categories)}")
        logger.info(f"  Per-run documents: {len(agent_docs)}")
        logger.info(f"  Advanced analysis: {advanced_analysis}")
        logger.info(
            f"  Hypothesis merged: {'yes' if 'statistical_hypothesis' in aggregated_scorecard else 'no'}"
        )
        logger.info(f"  Output directory : {output_path}")

        return aggregated_scorecard

    finally:
        if llm_client:
            await llm_client.close()
            logger.info("LLM client connection closed.")


# ──────────────────────────────────────────────────────────────────────
# Pretty-printer
# ──────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end pipeline: Aggregation (+ optional Statistical Hypothesis Analysis)."
    )
    parser.add_argument("--metrics-dir", required=True,
                        help="Directory containing per-run *metrics.json files.")
    parser.add_argument("--output-dir", required=True,
                        help="Directory for all pipeline outputs.")
    parser.add_argument("--agent-id", required=True,
                        help="Agent ID to aggregate metrics for.")
    parser.add_argument("--agent-name", required=True,
                        help="Agent name for the aggregated scorecard.")
    parser.add_argument("--certification-run-id", type=str, default="",
                        help="Optional run identifier appended to output dir.")
    parser.add_argument("--runs-per-fault", type=int, default=30,
                        help="Expected number of runs per fault (default: 30).")

    parser.add_argument("--advanced-analysis", action="store_true",
                        help="If set, also runs the statistical hypothesis framework "
                             "and merges results under the 'statistical_hypothesis' key.")
    parser.add_argument("--ground-truth-dir", type=str, default=None,
                        help="Ground truth directory required when --advanced-analysis is set.")
    parser.add_argument("--fault-categories-config", type=str, default=None,
                        help="Path to fault categories JSON (defaults to configs/fault_categories.json).")

    parser.add_argument("--min-runs", type=int, default=30,
                        help="Hypothesis: minimum detected runs per category (default 30).")
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="Hypothesis: significance level (default 0.05).")
    parser.add_argument("--n-resamples", type=int, default=10000,
                        help="Hypothesis: bootstrap resamples (default 10000).")
    parser.add_argument("--random-state", type=int, default=None,
                        help="Hypothesis: random seed (default None).")

    parser.add_argument("--debug", action="store_true",
                        help="Persist intermediate outputs / verbose logging.")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.advanced_analysis and not args.ground_truth_dir:
        logger.warning(
            "--advanced-analysis is set but --ground-truth-dir was not provided. "
            "Statistical hypothesis analysis will be skipped."
        )

    result = asyncio.run(
        run_pipeline(
            metrics_dir=args.metrics_dir,
            output_dir=args.output_dir,
            agent_id=args.agent_id,
            agent_name=args.agent_name,
            certification_run_id=args.certification_run_id,
            runs_per_fault=args.runs_per_fault,
            advanced_analysis=args.advanced_analysis,
            ground_truth_dir=args.ground_truth_dir,
            fault_categories_config=args.fault_categories_config,
            min_runs=args.min_runs,
            alpha=args.alpha,
            n_resamples=args.n_resamples,
            random_state=args.random_state,
            debug=args.debug,
        )
    )

    if result:
        print("\nPipeline Complete")
        print("=" * 50)
        print(f"  Output: {args.output_dir}")
        print(
            "  statistical_hypothesis included: "
            f"{'yes' if 'statistical_hypothesis' in result else 'no'}"
        )
    else:
        print("\nPipeline failed. Check logs for details.")


if __name__ == "__main__":
    main()
