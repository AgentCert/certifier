"""
Sample-size notice builder (Phase D).

Emits a §1 NoticeBlock when the certification was executed with fewer than
the framework-mandated minimum runs per fault category. The notice text is
generated dynamically from the upstream ``statistical_hypothesis`` block —
``observed_per_category``, ``total_runs``, and ``min_required`` — so the
numbers always reflect the actual run.

Trigger rule:
    Notice is emitted ONLY when the upstream statistical-hypothesis framework
    was requested (i.e., the run was launched with --advanced-analysis) AND
    was then skipped because runs_per_fault < the framework minimum.
    Specifically, the notice fires iff:
        statistical_hypothesis.status == "skipped"

    When status is "ok" (framework executed) or "not_requested" / missing
    (framework was never asked to run), no notice is emitted. The raw
    runs_per_fault value alone is NOT sufficient to fire the notice.
"""

from __future__ import annotations

from typing import Any

DEFAULT_MIN_RUNS_PER_FAULT: int = 30

# Back-compat alias for older callers / notebooks. Prefer
# ``DEFAULT_MIN_RUNS_PER_FAULT`` in new code.
MIN_RUNS_PER_FAULT: int = DEFAULT_MIN_RUNS_PER_FAULT

NOTICE_TITLE: str = "Inadequate sample size — statistical framework not applied"


def _format_runs_per_category(observed: dict[str, int]) -> str:
    """Render the per-category run-count phrase used inside the notice body.

    * Empty / missing → ``"n = 0 independent runs per fault category"``.
    * All categories share the same count → single ``"n = X"`` phrase.
    * Mixed counts → ``"n = {cat1: x, cat2: y}"`` for transparency.
    """
    if not observed:
        return "n = 0 independent runs per fault category"

    counts = list(observed.values())
    if len(set(counts)) == 1:
        cat_count = len(observed)
        cat_word = "category" if cat_count == 1 else "categories"
        return (
            f"n = {counts[0]} independent runs per fault category "
            f"across {cat_count} {cat_word}"
        )

    breakdown = ", ".join(f"{cat}: {n}" for cat, n in observed.items())
    return f"n = {{{breakdown}}} independent runs per fault category"


def _build_notice_body(
    observed: dict[str, int],
    total_runs: int,
    min_required: int,
) -> str:
    runs_phrase = _format_runs_per_category(observed)
    return (
        f"This certification executed {runs_phrase} "
        f"({total_runs} runs total), which is below the framework-mandated "
        f"minimum of n \u2265 {min_required} per category required for valid "
        "statistical inference. As a result, the 9-hypothesis statistical "
        "framework (H-01 \u2013 H-09) has not been evaluated for this run. "
        "Metrics reported below (means, medians, P95, success rates) are "
        "directional indicators only and must not be interpreted as "
        "statistically robust bounds or confidence-certified estimates. "
        f"Re-certify with n \u2265 {min_required} per category to activate "
        "the full statistical framework."
    )


def build_sample_size_notice(
    statistical_hypothesis: dict[str, Any] | None,
) -> dict | None:
    """Return a NoticeBlock dict iff stat-framework was requested but skipped.

    The notice text is generated from the upstream skip block:
        * ``observed_per_category`` → per-category run counts
        * ``total_runs``            → distinct runs across categories
        * ``min_required``          → framework minimum (default 30)

    Status handling:
        * ``status == "skipped"``      → emit notice (requested but blocked)
        * ``status == "ok"``           → no notice (framework executed)
        * ``status == "not_requested"`` or missing → no notice (not asked)
    """
    if not isinstance(statistical_hypothesis, dict):
        return None
    if statistical_hypothesis.get("status") != "skipped":
        return None

    observed_raw = statistical_hypothesis.get("observed_per_category") or {}
    observed: dict[str, int] = {}
    for cat, val in observed_raw.items():
        try:
            observed[str(cat)] = int(val)
        except (TypeError, ValueError):
            observed[str(cat)] = 0

    try:
        min_required = int(
            statistical_hypothesis.get("min_required")
            or DEFAULT_MIN_RUNS_PER_FAULT
        )
    except (TypeError, ValueError):
        min_required = DEFAULT_MIN_RUNS_PER_FAULT

    try:
        total_runs = int(statistical_hypothesis.get("total_runs") or 0)
    except (TypeError, ValueError):
        total_runs = 0
    if total_runs <= 0 and observed:
        # Fall back to the largest per-category count (a reasonable lower
        # bound on distinct runs when the upstream block omits total_runs).
        total_runs = max(observed.values())

    return {
        "type": "notice",
        "severity": "warning",
        "title": NOTICE_TITLE,
        "body": _build_notice_body(observed, total_runs, min_required),
    }
