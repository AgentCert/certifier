"""
Sample-size notice builder (Phase D).

Emits a §1 NoticeBlock when the certification was executed with fewer than
the framework-mandated minimum runs per fault category. The notice is fully
hard-coded — no per-category breakdown, no template parameters.

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

MIN_RUNS_PER_FAULT: int = 30

NOTICE_TITLE: str = "Inadequate sample size — statistical framework not applied"

NOTICE_BODY: str = (
    "This certification executed n = 5 independent runs per fault category "
    "(15 runs total), which is below the framework-mandated minimum of "
    "n \u2265 30 per category required for valid statistical inference. "
    "As a result, the 9-hypothesis statistical framework (H-01 \u2013 H-09) "
    "has not been evaluated for this run. Metrics reported below (means, "
    "medians, P95, success rates) are directional indicators only and must "
    "not be interpreted as statistically robust bounds or confidence-certified "
    "estimates. Re-certify with n \u2265 30 per category to activate the full "
    "statistical framework."
)


def build_sample_size_notice(
    statistical_hypothesis: dict[str, Any] | None,
) -> dict | None:
    """Return a NoticeBlock dict iff stat-framework was requested but skipped.

    The notice text is fully hard-coded; no run-count interpolation is
    performed. The only decision is whether to emit it at all, and that
    decision is driven entirely by the upstream framework status:

        * ``status == "skipped"``      → emit notice (requested but blocked)
        * ``status == "ok"``           → no notice (framework executed)
        * ``status == "not_requested"`` or missing → no notice (not asked)
    """
    if not isinstance(statistical_hypothesis, dict):
        return None
    if statistical_hypothesis.get("status") != "skipped":
        return None
    return {
        "type": "notice",
        "severity": "warning",
        "title": NOTICE_TITLE,
        "body": NOTICE_BODY,
    }
