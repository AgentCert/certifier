"""
Aggregation + (optional) Statistical Hypothesis pipeline.

Runs Steps 1–2 from the full certification pipeline and produces the
aggregated scorecard JSON that ``run_certification_pipeline.py`` consumes.

Usage
-----
::

    python run_aggregation_pipeline.py \
        --metrics-dir <dir_with_metrics_json> \
        --output-dir  <output_dir> \
        --agent-id    <agent_id> \
        --agent-name  <agent_name> \
        [--certification-run-id <run_id>] \
        [--runs-per-fault 30] \
        [--advanced-analysis] \
        [--ground-truth-dir <gt_dir>] \
        [--fault-categories-config <path>] \
        [--min-runs 30] [--alpha 0.05] [--n-resamples 10000] \
        [--random-state <int>] [--debug]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
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
except ImportError:
    AzureLLMClient = None
    ConfigLoader = None
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

from utils.custom_errors import MyCustomError, OrchestratorError

from aggregator.scripts.aggregation import (
    AggregationOrchestrator,
    DirectoryQueryService,
    _distinct_run_ids,
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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=4, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
    except MyCustomError:
        raise
    except Exception as exc:
        raise OrchestratorError(
            f"Failed to write JSON to '{path}'",
            original_exception=exc,
        ) from exc


def _load_fault_categories_config(path: Path) -> Dict[str, List[str]]:
    if not path.exists():
        logger.warning(
            f"Fault categories config not found at {path}; returning empty map."
        )
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise OrchestratorError(
            f"Failed to load fault categories config from '{path}'",
            original_exception=exc,
        ) from exc
    if isinstance(raw, dict) and isinstance(raw.get("categories"), dict):
        return {k: list(v) for k, v in raw["categories"].items()}
    if isinstance(raw, dict):
        return {k: list(v) for k, v in raw.items() if isinstance(v, list)}
    logger.warning(f"Unexpected schema in {path}; returning empty map.")
    return {}


def _doc_fault_name(doc: Dict[str, Any]) -> Optional[str]:
    return (
        doc.get("fault_name")
        or doc.get("quantitative", {}).get("injected_fault_name")
    )


def _doc_fault_category(
    doc: Dict[str, Any],
    subfault_to_category: Dict[str, str],
) -> Optional[str]:
    sub = _doc_fault_name(doc)
    if sub and sub in subfault_to_category:
        return subfault_to_category[sub]
    return None


def _group_docs_by_category(
    docs: List[Dict[str, Any]],
    fault_categories: Dict[str, List[str]],
) -> Dict[str, List[Dict[str, Any]]]:
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
    return dict(grouped), skipped


# ──────────────────────────────────────────────────────────────────────
# Grouped docs query service
# ──────────────────────────────────────────────────────────────────────


class GroupedDocsQueryService:
    def __init__(self, grouped_docs: Dict[str, List[Dict[str, Any]]]):
        self.grouped_docs = grouped_docs

    def query_runs_by_agent(self, agent_id: str) -> List[Dict[str, Any]]:
        all_docs = []
        for docs in self.grouped_docs.values():
            all_docs.extend(docs)
        return all_docs

    def query_runs_by_fault_category(
        self,
        fault_category: str,
        agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.grouped_docs.get(fault_category, [])

    def get_all_fault_categories(
        self,
        agent_id: Optional[str] = None,
    ) -> List[str]:
        return sorted(self.grouped_docs.keys())


# ──────────────────────────────────────────────────────────────────────
# Hypothesis gate + invocation
# ──────────────────────────────────────────────────────────────────────


def _build_skip_block(
    reason: str,
    message: str,
    *,
    min_required: int,
    validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    observed: Dict[str, int] = {}
    total_runs = 0
    if validation:
        per_cat = validation.get("per_category") or {}
        for cat, val in per_cat.items():
            if isinstance(val, dict):
                count = val.get("total") or val.get("count") or 0
            else:
                count = val
            try:
                observed[cat] = int(count)
            except (TypeError, ValueError):
                observed[cat] = 0
        try:
            total_runs = int(validation.get("total_runs") or 0)
        except (TypeError, ValueError):
            total_runs = 0
    return {
        "status": "skipped",
        "reason": reason,
        "min_required": min_required,
        "observed_per_category": observed,
        "total_runs": total_runs,
        "message": message,
    }


def _run_hypothesis_with_gate(
    grouped_runs: Dict[str, List[Dict[str, Any]]],
    gt_dir: Optional[Path],
    *,
    min_runs: int,
    alpha: float,
    n_resamples: int,
    random_state: Optional[int],
    metrics_dir: Path,
) -> Dict[str, Any]:
    try:
        from hypothesis_framework.scripts.utils import (
            validate_min_total_runs,
        )
    except Exception as exc:
        logger.warning(
            f"Could not import hypothesis_framework utils: {exc}. "
            "Skipping statistical hypothesis analysis."
        )
        return _build_skip_block(
            reason="import_error",
            message=f"Statistical hypothesis framework unavailable: {exc}",
            min_required=min_runs,
        )

    passed, validation = validate_min_total_runs(
        grouped_runs, min_runs=min_runs
    )

    if not passed:
        logger.info(
            f"Per-category min-runs gate failed: {validation['message']}"
        )
        return _build_skip_block(
            reason="insufficient_runs",
            message=(
                f"Statistical hypothesis testing requires \u2265{min_runs} runs "
                "per fault category. Section omitted; see Experiment Scope "
                "for details."
            ),
            min_required=min_runs,
            validation=validation,
        )

    logger.info(
        "Per-category min-runs gate passed: "
        f"{validation['total_runs']} runs across "
        f"{len(validation['per_category'])} categories."
    )

    ground_truth_provided = False
    if gt_dir and Path(gt_dir).exists():
        ground_truth_provided = True
        logger.info(f"Ground truth directory found: {gt_dir}")
    else:
        logger.warning(
            f"Ground truth directory not provided or not found: {gt_dir!s}. "
            "SLA-aware hypothesis tests (H06, H07) will be skipped; "
            "other tests will proceed normally."
        )

    try:
        from hypothesis_framework.scripts.run_statistical_hypothesis import (
            run_all_hypothesis_tests_from_runs,
        )
    except Exception as exc:
        logger.warning(
            f"Could not import hypothesis runner: {exc}. Skipping."
        )
        return _build_skip_block(
            reason="import_error",
            message=f"Statistical hypothesis runner unavailable: {exc}",
            min_required=min_runs,
            validation=validation,
        )

    try:
        result = run_all_hypothesis_tests_from_runs(
            all_runs=grouped_runs,
            gt_dir=Path(gt_dir) if gt_dir else Path("/dev/null"),
            min_runs=min_runs,
            alpha=alpha,
            n_resamples=n_resamples,
            random_state=random_state,
            data_dir=str(metrics_dir),
        )
    except Exception as exc:
        logger.warning(
            f"Statistical hypothesis analysis raised: {exc}",
            exc_info=True,
        )
        return _build_skip_block(
            reason="hypothesis_error",
            message=f"Hypothesis framework raised an exception: {exc}",
            min_required=min_runs,
            validation=validation,
        )

    if isinstance(result, dict) and result.get("error"):
        logger.warning(
            f"Hypothesis analysis returned error: {result.get('error')} — "
            f"{result.get('message')}"
        )
        return _build_skip_block(
            reason=str(result.get("error")),
            message=str(
                result.get("message")
                or "Hypothesis framework returned an error result."
            ),
            min_required=min_runs,
            validation=validation,
        )

    observed: Dict[str, int] = {}
    per_cat = (validation or {}).get("per_category") or {}
    for cat, val in per_cat.items():
        if isinstance(val, dict):
            count = val.get("total") or val.get("count") or 0
        else:
            count = val
        try:
            observed[cat] = int(count)
        except (TypeError, ValueError):
            observed[cat] = 0
    return {
        "status": "ok",
        "min_required": min_runs,
        "observed_per_category": observed,
        "ground_truth_provided": ground_truth_provided,
        "results": result,
    }


# ──────────────────────────────────────────────────────────────────────
# Pretty-printer
# ──────────────────────────────────────────────────────────────────────


def _print_aggregation_summary(
    scorecard: Dict[str, Any],
    agent_id: str,
    agent_name: str,
) -> None:
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
    print("=" * 70)


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
    """Run aggregation + (optional, gated) hypothesis pipeline.

    Returns the aggregated scorecard dict. Writes it to output_dir.
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

    try:
        llm_client = AzureLLMClient(config=config) if AzureLLMClient else None
    except Exception as exc:
        raise OrchestratorError(
            "Failed to initialize AzureLLMClient",
            original_exception=exc,
        ) from exc

    try:
        # ──────────────────────────────────────────────────────────────
        # Step 1: Aggregation (with fault_categories.json normalization)
        # ──────────────────────────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("STEP 1: Aggregation")
        logger.info("=" * 60)

        try:
            query_service = DirectoryQueryService(metrics_dir)

            agent_docs = query_service.query_runs_by_agent(agent_id)
            if not agent_docs:
                logger.error(
                    f"No per-run metric documents found for agent_id='{agent_id}' "
                    f"in directory '{metrics_dir}'."
                )
                return {}

            logger.info(
                f"Found {len(agent_docs)} per-run documents for agent_id='{agent_id}'"
            )

            cfg_path = (
                Path(fault_categories_config)
                if fault_categories_config
                else _DEFAULT_FAULT_CATEGORIES_CONFIG
            )
            fault_cats = _load_fault_categories_config(cfg_path)
            grouped_runs, unclassified_runs = _group_docs_by_category(agent_docs, fault_cats)

            grouped_query_service = GroupedDocsQueryService(grouped_runs)
            categories = grouped_query_service.get_all_fault_categories(agent_id=agent_id)

            if not categories:
                logger.error(f"No fault categories found after mapping with fault_categories config.")
                return {}

            logger.info(f"Found canonical fault categories: {categories}")

            orchestrator = AggregationOrchestrator(
                llm_client=llm_client,
                query_service=grouped_query_service,
                db_client=None,
            )

            aggregated_scorecard = await orchestrator.aggregate_all(
                agent_id=agent_id,
                agent_name=agent_name,
                certification_run_id=certification_run_id,
                runs_per_fault=runs_per_fault,
                store_results=False,
                total_input_runs=len(_distinct_run_ids(agent_docs)),
            )
        except MyCustomError:
            raise
        except Exception as exc:
            logger.error(f"Aggregation step failed: {exc}", exc_info=True)
            raise OrchestratorError(
                "Aggregation step failed",
                original_exception=exc,
            ) from exc

        _print_aggregation_summary(aggregated_scorecard, agent_id, agent_name)

        # ──────────────────────────────────────────────────────────────
        # Step 2 (optional): Statistical Hypothesis (with per-category gate)
        # ──────────────────────────────────────────────────────────────
        if advanced_analysis:
            logger.info("=" * 60)
            logger.info("STEP 2: Statistical Hypothesis Analysis (gated)")
            logger.info("=" * 60)

            try:
                gt_path = Path(ground_truth_dir) if ground_truth_dir else None

                hypothesis_block = _run_hypothesis_with_gate(
                    grouped_runs=grouped_runs,
                    gt_dir=gt_path,
                    min_runs=min_runs,
                    alpha=alpha,
                    n_resamples=n_resamples,
                    random_state=random_state,
                    metrics_dir=Path(metrics_dir),
                )
                aggregated_scorecard["statistical_hypothesis"] = hypothesis_block
                logger.info(
                    f"statistical_hypothesis status: {hypothesis_block['status']}"
                )
            except MyCustomError:
                raise
            except Exception as exc:
                logger.error(
                    f"Statistical hypothesis step failed: {exc}",
                    exc_info=True,
                )
                raise OrchestratorError(
                    "Statistical hypothesis step failed",
                    original_exception=exc,
                ) from exc
        else:
            logger.info(
                "Advanced analysis flag not set; statistical_hypothesis key "
                "will not be added to the scorecard."
            )

        # Persist aggregated scorecard
        scorecard_path = output_path / f"aggregated_scorecard_output_{agent_id}.json"
        _save_json(aggregated_scorecard, scorecard_path)
        logger.info(f"Aggregated scorecard written to {scorecard_path}")

        # Summary
        sh = aggregated_scorecard.get("statistical_hypothesis", {})
        summary = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "certification_run_id": certification_run_id,
            "metrics_dir": str(Path(metrics_dir).resolve()),
            "total_documents": len(agent_docs),
            "total_fault_categories": len(categories),
            "fault_categories": categories,
            "advanced_analysis": advanced_analysis,
            "statistical_hypothesis_status": sh.get("status") if sh else "not_requested",
            "statistical_hypothesis_reason": sh.get("reason"),
            "aggregated_scorecard_path": str(scorecard_path),
        }
        summary_path = output_path / "pipeline_summary.json"
        _save_json(summary, summary_path)

        logger.info("=" * 60)
        logger.info("Aggregation Pipeline Complete")
        logger.info("=" * 60)
        logger.info(f"  Agent              : {agent_name} ({agent_id})")
        logger.info(f"  Fault categories   : {len(categories)}")
        logger.info(f"  Per-run documents  : {len(agent_docs)}")
        logger.info(f"  Advanced analysis  : {advanced_analysis}")
        logger.info(
            f"  Hypothesis status  : {summary['statistical_hypothesis_status']}"
        )
        logger.info(f"  Output directory   : {output_path}")
        logger.info(f"  Scorecard          : {scorecard_path.name}")

        return aggregated_scorecard

    finally:
        if llm_client:
            try:
                await llm_client.close()
            except Exception:
                pass
            logger.info("LLM client connection closed.")


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregation + (optional, gated) Statistical Hypothesis pipeline. "
            "Produces aggregated scorecard JSON for run_certification_pipeline.py."
        )
    )
    parser.add_argument("--metrics-dir", required=True,
                        help="Directory containing per-run *metrics.json files.")
    parser.add_argument("--output-dir", required=True,
                        help="Directory for all pipeline outputs.")
    parser.add_argument("--agent-id", required=True,
                        help="Agent ID to aggregate metrics for.")
    parser.add_argument("--agent-name", required=True,
                        help="Agent name for the certification scorecard.")
    parser.add_argument("--certification-run-id", type=str, default="",
                        help="Optional run identifier appended to output dir.")
    parser.add_argument("--runs-per-fault", type=int, default=30,
                        help="Expected number of runs per fault for aggregation reporting.")

    parser.add_argument("--advanced-analysis", action="store_true",
                        help="Attempt the statistical hypothesis framework.")
    parser.add_argument("--ground-truth-dir", type=str, default=None,
                        help="Ground truth directory for --advanced-analysis.")
    parser.add_argument("--fault-categories-config", type=str, default=None,
                        help="Path to fault categories JSON (defaults to configs/fault_categories.json).")

    parser.add_argument("--min-runs", type=int, default=30,
                        help="Minimum total runs per fault category for hypothesis gate.")
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="Hypothesis: significance level.")
    parser.add_argument("--n-resamples", type=int, default=10000,
                        help="Hypothesis: bootstrap resamples.")
    parser.add_argument("--random-state", type=int, default=None,
                        help="Hypothesis: random seed.")

    parser.add_argument("--debug", action="store_true",
                        help="Persist intermediate outputs / verbose logging.")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.advanced_analysis and not args.ground_truth_dir:
        logger.warning(
            "--advanced-analysis is set but --ground-truth-dir was not provided. "
            "Hypothesis framework will be skipped at the ground-truth gate."
        )

    try:
        scorecard = asyncio.run(
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
    except MyCustomError as exc:
        logger.error(f"Pipeline aborted: {exc}")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Unexpected pipeline error: {exc}", exc_info=True)
        sys.exit(1)

    if scorecard:
        print("\nAggregation Pipeline Complete")
        print("=" * 50)
        print("  Aggregated scorecard generated successfully.")
        print(f"  Output: {args.output_dir}")
    else:
        print("\nPipeline failed. Check logs for details.")


if __name__ == "__main__":
    main()
