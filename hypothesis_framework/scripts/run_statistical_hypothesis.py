"""
Unified Statistical Hypothesis Testing Runner.

Orchestrates all nine hypothesis tests (H01–H09) across multiple metrics
(TTD, TTM, tool_calls, detection rate, mitigation rate), loading JSON run
data and ground truth SLA thresholds as input.

Usage (CLI)::

    python -m hypothesis_framework.scripts.run_statistical_hypothesis \\
        --data-dir hypothesis_framework/data/input \\
        --gt-dir  hypothesis_framework/data/groundtruth/kubernetes \\
        --output-file results.json

Usage (Python)::

    from hypothesis_framework.scripts.run_statistical_hypothesis import (
        run_all_hypothesis_tests,
    )
    result = run_all_hypothesis_tests("data/input", "data/groundtruth/kubernetes")
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from hypothesis_framework.scripts.utils import (
    build_subfault_counts,
    build_subfault_counts_from_status,
    build_subfault_data,
    build_subfault_data_all,
    load_runs,
    load_sla_thresholds,
    validate_min_total_runs,
)

logger = logging.getLogger(__name__)


# ── Metric specifications ─────────────────────────────────────────────
#
# Continuous metrics are tested by H01, H03, H05, H06, H07, H08, H09.
# Rate metrics are tested by H02, H04.
#
# Each continuous spec defines:
#   field        – JSON key under quantitative
#   filter_field – eligibility gate (None = all runs)
#   filter_value – expected value for the gate
#   sla_key      – ground truth YAML key
#   breach_inf   – whether non-eligible runs → inf for H07

CONTINUOUS_METRICS: Dict[str, Dict[str, Any]] = {
    "time_to_detect": {
        "field": "time_to_detect",
        "filter_field": "fault_detected",
        "filter_value": "Yes",
        "sla_key": "time_to_detect",
        "breach_inf": True,
    },
    "time_to_mitigate": {
        "field": "time_to_mitigate",
        "filter_field": "fault_mitigated",
        "filter_value": "Yes",
        "sla_key": "time_to_mitigate",
        "breach_inf": True,
    },
    "tool_calls": {
        "field": "trajectory_steps",
        "filter_field": None,
        "filter_value": None,
        "sla_key": "max_tool_calls",
        "breach_inf": False,
    },
    "reasoning_quality_score": {
        "field": "reasoning_quality_score",
        "filter_field": None,
        "filter_value": None,
        "sla_key": None,
        "breach_inf": False,
        "section": "qualitative",
    },
    "hallucination_score": {
        "field": "hallucination_score",
        "filter_field": None,
        "filter_value": None,
        "sla_key": None,
        "breach_inf": False,
        "section": "qualitative",
    },
}

RATE_METRICS: Dict[str, Dict[str, Any]] = {
    "fault_detection_success_rate": {
        "success_field": "fault_detected",
        "success_value": "Yes",
    },
    "fault_mitigation_success_rate": {
        "success_field": "fault_mitigated",
        "success_value": "Yes",
    },
    "rai_compliance_rate": {
        "success_field": "rai_check_status",
        "success_value": "Passed",
        "section": "qualitative",
    },
    "security_compliance_rate": {
        "success_field": "security_compliance_status",
        "success_value": "Compliant",
        "section": "qualitative",
    },
}


# ── Lazy import helper ────────────────────────────────────────────────


def _import_hypothesis_tests() -> Dict[str, Callable]:
    """Import all hypothesis test entry-point functions."""
    from hypothesis_framework.scripts.hypothesis_tests.h01_confidence_intervals import (
        run_confidence_interval_test,
    )
    from hypothesis_framework.scripts.hypothesis_tests.h02_success_rate_estimation import (
        run_success_rate_test,
    )
    from hypothesis_framework.scripts.hypothesis_tests.h03_cross_category_comparison import (
        run_cross_category_test,
    )
    from hypothesis_framework.scripts.hypothesis_tests.h04_success_rate_uniformity import (
        run_uniformity_test,
    )
    from hypothesis_framework.scripts.hypothesis_tests.h05_consistency_predictability import (
        run_consistency_test,
    )
    from hypothesis_framework.scripts.hypothesis_tests.h06_sla_threshold_compliance import (
        run_sla_compliance_test,
    )
    from hypothesis_framework.scripts.hypothesis_tests.h07_sla_breach_rate import (
        run_breach_rate_test,
    )
    from hypothesis_framework.scripts.hypothesis_tests.h08_tail_risk_analysis import (
        run_tail_risk_test,
    )
    from hypothesis_framework.scripts.hypothesis_tests.h09_temporal_stability import (
        run_drift_test,
    )
    return {
        "h01": run_confidence_interval_test,
        "h02": run_success_rate_test,
        "h03": run_cross_category_test,
        "h04": run_uniformity_test,
        "h05": run_consistency_test,
        "h06": run_sla_compliance_test,
        "h07": run_breach_rate_test,
        "h08": run_tail_risk_test,
        "h09": run_drift_test,
    }


# ── Safe runner wrapper ───────────────────────────────────────────────


def _safe_run(func: Callable, label: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Execute a hypothesis test and return ``model_dump()`` or an error dict."""
    try:
        result = func(*args, **kwargs)
        return result.model_dump()
    except Exception as exc:
        logger.error("Error running %s: %s", label, exc, exc_info=True)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


# ── Main entry point ──────────────────────────────────────────────────


def run_all_hypothesis_tests(
    data_dir: Any,
    gt_dir: Any,
    min_runs: int = 30,
    alpha: float = 0.05,
    n_resamples: int = 10000,
    random_state: Optional[int] = None,
) -> Dict[str, Any]:
    """Run all H01–H09 hypothesis tests across multiple metrics.

    Args:
        data_dir:      Directory containing category subdirectories with
                       JSON run files.
        gt_dir:        Ground truth directory with per-fault YAML files
                       (e.g., ``data/groundtruth/kubernetes``).
        min_runs:      Minimum detected runs required (total **and** per
                       category).  Default 30.
        alpha:         Significance level for hypothesis tests.
        n_resamples:   Bootstrap resamples for CI-based tests (H01, H06).
        random_state:  Random seed for reproducibility.

    Returns:
        Merged results dictionary::

            {
                "metadata":   { ... },
                "validation": { ... },
                "results": {
                    "h01": {"time_to_detect": {...}, "time_to_mitigate": {...}, "tool_calls": {...}},
                    "h02": {"fault_detection_success_rate": {...}, ...},
                    ...
                    "h09": { ... }
                }
            }

        On validation failure the dict has ``"error"`` and ``"message"``
        keys instead.
    """
    data_dir = Path(data_dir)
    gt_dir = Path(gt_dir)

    # ── 1. Load all runs ──────────────────────────────────────────────
    logger.info("Loading runs from %s", data_dir)
    all_runs = load_runs(data_dir)
    if not all_runs:
        return {
            "error": "no_data",
            "message": f"No run files found in {data_dir}",
        }

    return run_all_hypothesis_tests_from_runs(
        all_runs=all_runs,
        gt_dir=gt_dir,
        min_runs=min_runs,
        alpha=alpha,
        n_resamples=n_resamples,
        random_state=random_state,
        data_dir=str(data_dir),
    )


def run_all_hypothesis_tests_from_runs(
    all_runs: Dict[str, list],
    gt_dir: Any,
    min_runs: int = 30,
    alpha: float = 0.05,
    n_resamples: int = 10000,
    random_state: Optional[int] = None,
    data_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Run all H01–H09 hypothesis tests using a pre-loaded runs dictionary.

    Identical behavior to :func:`run_all_hypothesis_tests` but accepts an
    in-memory ``{category: [run_dict, ...]}`` mapping instead of reading
    JSON files from disk. Useful for callers that have already loaded the
    runs (e.g., the aggregation+hypothesis pipeline that reads a flat
    metrics directory and groups by ``fault_category``).

    Args:
        all_runs:      Pre-loaded runs grouped by category. Each run dict
                       must follow the per-run metrics schema (with
                       ``quantitative.*`` keys).
        gt_dir:        Ground truth directory with per-fault YAML files.
        min_runs:      Minimum detected runs required.
        alpha:         Significance level.
        n_resamples:   Bootstrap resamples.
        random_state:  Random seed.
        data_dir:      Optional source directory string for metadata only.

    Returns:
        Same structure as :func:`run_all_hypothesis_tests`.
    """
    gt_dir = Path(gt_dir)

    if not all_runs:
        return {
            "error": "no_data",
            "message": "No runs provided",
        }

    # ── 2. Validate minimum runs (per fault category) ────────────────
    logger.info("Validating minimum run criteria (min=%d per category)", min_runs)
    valid, validation = validate_min_total_runs(all_runs, min_runs)
    if not valid:
        return {
            "error": "minimum_run_criteria_not_qualified",
            "message": validation["message"],
            "validation": validation,
        }

    # ── 3. Import hypothesis test functions ───────────────────────────
    tests = _import_hypothesis_tests()

    # ── 4. Load SLA thresholds for each continuous metric ─────────────
    sla_by_metric: Dict[str, Dict[str, float]] = {}
    for metric_key, cfg in CONTINUOUS_METRICS.items():
        sla_by_metric[metric_key] = load_sla_thresholds(gt_dir, cfg["sla_key"])

    # ── 5. Initialise results structure ───────────────────────────────
    results: Dict[str, Dict[str, Any]] = {f"h{i:02d}": {} for i in range(1, 10)}
    warnings: list[str] = []

    # ── 6. Continuous-metric hypothesis tests ─────────────────────────
    for metric_key, cfg in CONTINUOUS_METRICS.items():
        logger.info("Processing continuous metric: %s", metric_key)

        # Determine which section to extract from (quantitative or qualitative)
        section = cfg.get("section", "quantitative")

        # Filtered data (eligible runs only)
        data_filtered = build_subfault_data(
            all_runs,
            cfg["field"],
            cfg["filter_field"],
            cfg.get("filter_value", "Yes"),
            section=section,
        )

        # All-runs data for H07 (non-eligible → inf when breach_inf)
        if cfg["breach_inf"]:
            data_all = build_subfault_data_all(
                all_runs,
                cfg["field"],
                cfg["filter_field"],
                cfg.get("filter_value", "Yes"),
            )
        else:
            # No censoring concept — reuse filtered data (all runs eligible)
            data_all = build_subfault_data(all_runs, cfg["field"], section=section)

        sla = sla_by_metric.get(metric_key, {})

        # Check data availability
        total_values = sum(
            len(v) for sf in data_filtered.values() for v in sf.values()
        )
        if total_values == 0:
            msg = f"{metric_key}: no valid data points available, skipping"
            logger.warning(msg)
            warnings.append(msg)
            for hkey in ["h01", "h03", "h05", "h06", "h07", "h08", "h09"]:
                results[hkey][metric_key] = {
                    "status": "skipped",
                    "reason": "no_valid_data",
                }
            continue

        # — H01: Confidence Intervals ─────────────────────────────────
        results["h01"][metric_key] = _safe_run(
            tests["h01"],
            f"H01/{metric_key}",
            data_filtered,
            metric_key,
            alpha,
            n_resamples,
            random_state,
        )

        # — H03: Cross-Category Comparison ────────────────────────────
        results["h03"][metric_key] = _safe_run(
            tests["h03"],
            f"H03/{metric_key}",
            data_filtered,
            metric_key,
            alpha,
        )

        # — H05: Consistency & Predictability ─────────────────────────
        results["h05"][metric_key] = _safe_run(
            tests["h05"],
            f"H05/{metric_key}",
            data_filtered,
            metric_key,
            alpha,
        )

        # — H06: SLA Threshold Compliance (requires SLA) ─────────────
        if sla:
            results["h06"][metric_key] = _safe_run(
                tests["h06"],
                f"H06/{metric_key}",
                data_filtered,
                sla,
                metric_key,
                alpha,
                n_resamples,
                random_state,
            )
        else:
            results["h06"][metric_key] = {
                "status": "skipped",
                "reason": "no_sla_thresholds_available",
            }

        # — H07: SLA Breach Rate (requires SLA, uses all runs) ────────
        if sla:
            results["h07"][metric_key] = _safe_run(
                tests["h07"],
                f"H07/{metric_key}",
                data_all,
                sla,
                0.05,
                metric_key,
                alpha,
            )
        else:
            results["h07"][metric_key] = {
                "status": "skipped",
                "reason": "no_sla_thresholds_available",
            }

        # — H08: Tail Risk Analysis (SLA optional) ────────────────────
        results["h08"][metric_key] = _safe_run(
            tests["h08"],
            f"H08/{metric_key}",
            data_filtered,
            metric_key,
            0.95,
            sla if sla else None,
        )

        # — H09: Temporal Stability ───────────────────────────────────
        results["h09"][metric_key] = _safe_run(
            tests["h09"],
            f"H09/{metric_key}",
            data_filtered,
            metric_key,
        )

    # ── 7. Rate-metric hypothesis tests ───────────────────────────────
    for metric_key, cfg in RATE_METRICS.items():
        logger.info("Processing rate metric: %s", metric_key)

        # Determine if this is a status-field metric (from qualitative section)
        is_status_field = cfg.get("section") == "qualitative"
        
        if is_status_field:
            # Status-field metrics need special handling
            from hypothesis_framework.scripts.utils import (
                build_subfault_counts_from_status,
            )
            counts = build_subfault_counts_from_status(
                all_runs,
                cfg["success_field"],
                cfg.get("success_value", "Yes"),
                section="qualitative",
            )
        else:
            # Boolean field metrics (fault_detected, fault_mitigated)
            counts = build_subfault_counts(
                all_runs,
                cfg["success_field"],
                cfg.get("success_value", "Yes"),
            )

        if not counts:
            msg = f"{metric_key}: no data available, skipping"
            logger.warning(msg)
            warnings.append(msg)
            results["h02"][metric_key] = {"status": "skipped", "reason": "no_data"}
            results["h04"][metric_key] = {"status": "skipped", "reason": "no_data"}
            continue

        # — H02: Success Rate Estimation ──────────────────────────────
        results["h02"][metric_key] = _safe_run(
            tests["h02"],
            f"H02/{metric_key}",
            counts,
            metric_key,
            alpha,
        )

        # — H04: Success Rate Uniformity ──────────────────────────────
        results["h04"][metric_key] = _safe_run(
            tests["h04"],
            f"H04/{metric_key}",
            counts,
            metric_key,
            alpha,
        )

    # ── 8. Assemble final output ──────────────────────────────────────
    detected_runs = sum(
        1
        for runs in all_runs.values()
        for r in runs
        if r.get("quantitative", {}).get("fault_detected") == "Yes"
    )
    output: Dict[str, Any] = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data_dir": str(data_dir) if data_dir else None,
            "gt_dir": str(gt_dir),
            "total_runs": validation["total_runs"],
            "detected_runs": detected_runs,
            "per_category": validation["per_category"],
            "metrics_processed": {
                "continuous": list(CONTINUOUS_METRICS.keys()),
                "rate": list(RATE_METRICS.keys()),
            },
            "sla_thresholds": {k: v for k, v in sla_by_metric.items() if v},
            "parameters": {
                "alpha": alpha,
                "n_resamples": n_resamples,
                "min_runs": min_runs,
                "random_state": random_state,
            },
        },
        "validation": validation,
        "results": results,
    }

    if warnings:
        output["warnings"] = warnings

    logger.info("All hypothesis tests complete.")
    return output


# ── CLI entry point ───────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run all statistical hypothesis tests (H01–H09).",
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Path to input data directory with category subdirectories.",
    )
    parser.add_argument(
        "--gt-dir",
        required=True,
        help="Path to ground truth directory with per-fault YAML files.",
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help="Path to save merged JSON output (prints to stdout if omitted).",
    )
    parser.add_argument("--min-runs", type=int, default=30)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--n-resamples", type=int, default=10000)
    parser.add_argument("--random-state", type=int, default=None)
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    result = run_all_hypothesis_tests(
        data_dir=args.data_dir,
        gt_dir=args.gt_dir,
        min_runs=args.min_runs,
        alpha=args.alpha,
        n_resamples=args.n_resamples,
        random_state=args.random_state,
    )

    output_json = json.dumps(result, indent=4, default=str, ensure_ascii=False)

    if args.output_file:
        out = Path(args.output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output_json, encoding="utf-8")
        logger.info("Results saved to %s", out)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
