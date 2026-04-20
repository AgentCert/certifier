"""
Shared utilities for the statistical hypothesis testing pipeline.

Provides common data-loading, metric extraction, SLA threshold loading,
and validation functions used by run_statistical_hypothesis.py.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Data Loading ──────────────────────────────────────────────────────


def load_runs(data_dir: Path) -> Dict[str, List[dict]]:
    """Load all JSON run files from category subdirectories.

    Reads ``{data_dir}/{category}/*.json``, sorted by filename to preserve
    run ordering (required for H09 temporal drift detection).

    Returns:
        ``{category_name: [run_dict, ...]}`` sorted by filename within each
        category.
    """
    all_runs: Dict[str, List[dict]] = {}
    data_dir = Path(data_dir)

    for category_dir in sorted(data_dir.iterdir()):
        if not category_dir.is_dir() or category_dir.name.startswith(("_", ".")):
            continue
        runs: List[dict] = []
        for f in sorted(category_dir.glob("*.json")):
            try:
                run = json.loads(f.read_text(encoding="utf-8"))
                run["_source_file"] = f.name
                runs.append(run)
            except Exception as exc:
                logger.warning("Failed to load %s: %s", f, exc)
        if runs:
            all_runs[category_dir.name] = runs

    return all_runs


# ── Validation ────────────────────────────────────────────────────────


def validate_minimum_runs(
    all_runs: Dict[str, List[dict]],
    min_runs: int = 30,
) -> Tuple[bool, Dict[str, Any]]:
    """Check minimum run criteria.

    Requirements (per user specification):
      - 30+ detected runs across **all** experiments.
      - 30+ detected runs per **fault category**.
      - Sub-faults may have fewer than 30.

    Returns:
        ``(passed, details_dict)`` — *details_dict* includes counts and a
        human-readable message.
    """
    per_category: Dict[str, Dict[str, int]] = {}
    total_runs = 0
    total_detected = 0
    category_issues: List[str] = []

    for cat, runs in all_runs.items():
        n_total = len(runs)
        n_detected = sum(
            1
            for r in runs
            if r.get("quantitative", {}).get("fault_detected") == "Yes"
        )
        total_runs += n_total
        total_detected += n_detected
        per_category[cat] = {"total": n_total, "detected": n_detected}
        if n_detected < min_runs:
            category_issues.append(
                f"{cat}: {n_detected} detected runs (need {min_runs})"
            )

    passed = total_detected >= min_runs and len(category_issues) == 0
    if total_detected < min_runs:
        message = (
            f"Minimum run criteria not qualified: total detected runs "
            f"({total_detected}) is below the required minimum ({min_runs})."
        )
    elif category_issues:
        message = (
            "Minimum run criteria not qualified for categories: "
            + "; ".join(category_issues)
        )
    else:
        message = "All criteria met"

    return passed, {
        "minimum_runs_met": passed,
        "total_runs": total_runs,
        "total_detected": total_detected,
        "per_category": per_category,
        "message": message,
    }


# ── Data Builders ─────────────────────────────────────────────────────


def build_subfault_data(
    all_runs: Dict[str, List[dict]],
    metric_field: str,
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = "Yes",
) -> Dict[str, Dict[str, List[float]]]:
    """Build nested data for continuous metrics (eligible runs only).

    Groups runs by *category → sub-fault*, extracting ``metric_field`` from
    the ``quantitative`` block.  Optionally filters by
    ``filter_field == filter_value``.

    * Excludes runs with null / missing metric values.
    * Preserves filename sort order for H09 temporal compatibility.

    Returns:
        ``{category: {sub_fault: [values]}}``
    """
    result: Dict[str, Dict[str, List[float]]] = {}

    for cat, runs in all_runs.items():
        subfaults: Dict[str, List[float]] = {}
        for run in runs:
            q = run.get("quantitative", {})

            if filter_field and q.get(filter_field) != filter_value:
                continue

            val = q.get(metric_field)
            if val is None:
                continue
            try:
                val = float(val)
            except (ValueError, TypeError):
                continue

            fname = run.get("fault_name", "unknown")
            subfaults.setdefault(fname, []).append(val)

        if subfaults:
            result[cat] = subfaults

    return result


def build_subfault_data_all(
    all_runs: Dict[str, List[dict]],
    metric_field: str,
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = "Yes",
) -> Dict[str, Dict[str, List[float]]]:
    """Build data including **all** runs for breach-rate analysis (H07).

    Non-eligible runs (filter check fails) or runs with missing metric
    values are recorded as ``float('inf')`` to represent automatic SLA
    breach.

    When *filter_field* is ``None``, every run is eligible (no censoring).

    Returns:
        ``{category: {sub_fault: [values_with_inf]}}``
    """
    result: Dict[str, Dict[str, List[float]]] = {}

    for cat, runs in all_runs.items():
        subfaults: Dict[str, List[float]] = {}
        for run in runs:
            q = run.get("quantitative", {})
            fname = run.get("fault_name", "unknown")

            if filter_field and q.get(filter_field) != filter_value:
                subfaults.setdefault(fname, []).append(float("inf"))
                continue

            val = q.get(metric_field)
            if val is None:
                subfaults.setdefault(fname, []).append(float("inf"))
                continue

            try:
                subfaults.setdefault(fname, []).append(float(val))
            except (ValueError, TypeError):
                subfaults.setdefault(fname, []).append(float("inf"))

        if subfaults:
            result[cat] = subfaults

    return result


def build_subfault_counts(
    all_runs: Dict[str, List[dict]],
    success_field: str,
    success_value: str = "Yes",
) -> Dict[str, Dict[str, Tuple[int, int]]]:
    """Build success / trial counts for rate metrics (H02, H04).

    Returns:
        ``{category: {sub_fault: (successes, trials)}}``
    """
    result: Dict[str, Dict[str, Tuple[int, int]]] = {}

    for cat, runs in all_runs.items():
        subfaults: Dict[str, Tuple[int, int]] = {}
        for run in runs:
            q = run.get("quantitative", {})
            fname = run.get("fault_name", "unknown")
            is_success = q.get(success_field) == success_value
            s, t = subfaults.get(fname, (0, 0))
            subfaults[fname] = (s + (1 if is_success else 0), t + 1)

        if subfaults:
            result[cat] = subfaults

    return result


# ── SLA Threshold Loading ─────────────────────────────────────────────


def load_sla_thresholds(
    gt_dir: Path,
    sla_key: str,
) -> Dict[str, float]:
    """Load SLA thresholds from ground truth YAML files.

    Reads ``{gt_dir}/{fault_name}/ground_truth.yaml`` and extracts
    ``ground_truth.sla.{sla_key}.threshold``.

    Returns:
        ``{fault_name: threshold_value}``
    """
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed; cannot load SLA thresholds")
        return {}

    gt_dir = Path(gt_dir)
    thresholds: Dict[str, float] = {}

    for fault_dir in sorted(gt_dir.iterdir()):
        if not fault_dir.is_dir():
            continue
        gt_file = fault_dir / "ground_truth.yaml"
        if not gt_file.exists():
            continue
        try:
            data = yaml.safe_load(gt_file.read_text(encoding="utf-8"))
            sla = data.get("ground_truth", {}).get("sla", {})
            entry = sla.get(sla_key, {})
            threshold = entry.get("threshold") if isinstance(entry, dict) else None
            if threshold is not None:
                thresholds[fault_dir.name] = float(threshold)
        except Exception as exc:
            logger.warning("Failed to parse SLA from %s: %s", gt_file, exc)

    return thresholds
