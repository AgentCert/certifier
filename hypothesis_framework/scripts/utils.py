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

def validate_min_total_runs(
    all_runs: Dict[str, List[dict]],
    min_runs: int = 30,
) -> Tuple[bool, Dict[str, Any]]:
    """Check that every fault category has >= ``min_runs`` total runs.

    Granularity is per **fault_category** (not per individual fault_name).
    A "run" is one distinct ``run_id`` (one trace), NOT one metrics file —
    a single trace can produce multiple metrics docs in the same category
    (e.g. ``pod-cpu-hog`` and ``pod-memory-hog`` both fall under
    ``resource_fault``) and must be counted once. Falls back to per-doc
    identity when ``run_id`` is missing.

    Counts **total** runs only (does NOT filter by ``fault_detected``).

    Returns:
        ``(passed, details_dict)`` where details_dict has:
          - minimum_runs_met (bool)
          - min_required_per_category (int)
          - total_runs (int) — distinct run_ids across all categories
          - per_category (dict[category, distinct_run_count])
          - failed_categories (list[str]) — entries like "category: n (need m)"
          - message (str)
    """
    per_category: Dict[str, int] = {}
    failed: List[str] = []
    all_run_ids: set = set()

    for cat, runs in all_runs.items():
        seen: set = set()
        for idx, run in enumerate(runs):
            rid = run.get("run_id") if isinstance(run, dict) else None
            if not rid:
                # Fall back to a per-doc unique key so the run is still counted
                rid = (
                    run.get("_source_file")
                    if isinstance(run, dict) else None
                ) or f"__no_run_id__:{cat}:{idx}"
            seen.add(rid)
            all_run_ids.add(rid)

        n = len(seen)
        per_category[cat] = n
        if n < min_runs:
            failed.append(f"{cat}: {n} runs (need {min_runs})")

    total_runs = len(all_run_ids)

    passed = len(failed) == 0
    if passed:
        message = (
            f"All fault categories meet the minimum total-run criterion "
            f"({min_runs} runs per category)."
        )
    else:
        message = (
            f"Insufficient total runs per category (need {min_runs} each): "
            + "; ".join(failed)
        )

    return passed, {
        "minimum_runs_met": passed,
        "min_required_per_category": min_runs,
        "total_runs": total_runs,
        "per_category": per_category,
        "failed_categories": failed,
        "message": message,
    }


# ── Data Builders ─────────────────────────────────────────────────────


def build_subfault_data(
    all_runs: Dict[str, List[dict]],
    metric_field: str,
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = "Yes",
    section: str = "quantitative",
) -> Dict[str, Dict[str, List[float]]]:
    """Build nested data for continuous metrics (eligible runs only).

    Groups runs by *category → sub-fault*, extracting ``metric_field`` from
    the specified section block (quantitative or qualitative).
    Optionally filters by ``filter_field == filter_value``.

    * Excludes runs with null / missing metric values.
    * Preserves filename sort order for H09 temporal compatibility.

    Args:
        all_runs: Pre-loaded runs grouped by category.
        metric_field: Field name to extract (e.g. "time_to_detect" or "reasoning_quality_score").
        filter_field: Optional eligibility gate field name.
        filter_value: Expected value for the gate.
        section: Section to extract from ("quantitative" or "qualitative").

    Returns:
        ``{category: {sub_fault: [values]}}``
    """
    result: Dict[str, Dict[str, List[float]]] = {}

    for cat, runs in all_runs.items():
        subfaults: Dict[str, List[float]] = {}
        for run in runs:
            data_section = run.get(section, {})

            if filter_field and data_section.get(filter_field) != filter_value:
                continue

            val = data_section.get(metric_field)
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


def build_subfault_counts_from_status(
    all_runs: Dict[str, List[dict]],
    success_field: str,
    success_value: str | list[str],
    section: str = "qualitative",
) -> Dict[str, Dict[str, Tuple[int, int]]]:
    """Build success / trial counts for status-field rate metrics (from qualitative section).

    Similar to build_subfault_counts, but:
    - Extracts from a specified section (e.g. qualitative)
    - Handles success_value as either string or list of strings for matching
    
    Args:
        all_runs: Pre-loaded runs grouped by category.
        success_field: Field name in the section (e.g. "rai_check_status").
        success_value: String or list of strings representing success states.
        section: Section to extract from (default "qualitative").

    Returns:
        ``{category: {sub_fault: (successes, trials)}}``
    """
    # Normalize success_value to list for uniform checking
    if isinstance(success_value, str):
        success_values = [success_value]
    else:
        success_values = list(success_value)

    result: Dict[str, Dict[str, Tuple[int, int]]] = {}

    for cat, runs in all_runs.items():
        subfaults: Dict[str, Tuple[int, int]] = {}
        for run in runs:
            data_section = run.get(section, {})
            fname = run.get("fault_name", "unknown")
            
            field_value = data_section.get(success_field)
            is_success = field_value in success_values
            
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

    # Handle non-existent or unreadable gt_dir gracefully
    if not gt_dir.exists():
        logger.warning(f"Ground truth directory does not exist: {gt_dir}")
        return thresholds

    try:
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
    except Exception as exc:
        logger.warning(f"Error iterating ground truth directory {gt_dir}: {exc}")

    return thresholds
