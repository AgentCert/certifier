"""Evaluation utilities for the fault bucketing pipeline.

Exposes pure-Python helpers to:
  * score predicted fault assignments against manually labelled ground
    truth (exact-match accuracy, Jaccard, micro/macro P/R/F1),
  * produce per-event mismatch tables for prompt debugging,
  * track input/output LLM token usage and dollar cost across iterations,
  * append per-iteration results to a CSV log so prompt / batch-size /
    payload changes can be compared over time.

Designed to be driven from the companion notebook
``fault_bucketing_evaluation.ipynb`` but each function is independently
importable and unit-testable.
"""

from fault_analyzer.tests.evaluation.evaluator import (
    EvaluationResult,
    LabelRecord,
    build_label_records,
    compute_metrics,
    evaluate,
    load_ground_truth,
    load_predictions,
    load_labels_from_bucket_dir,
    log_iteration,
    mismatch_table,
    token_cost_report,
)

__all__ = [
    "EvaluationResult",
    "LabelRecord",
    "build_label_records",
    "compute_metrics",
    "evaluate",
    "load_ground_truth",
    "load_predictions",
    "load_labels_from_bucket_dir",
    "log_iteration",
    "mismatch_table",
    "token_cost_report",
]
