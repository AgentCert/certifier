"""Read-only view over ``ParsedContext.statistical_hypothesis``.

Decouples downstream cert_builder consumers (computation, narratives,
assembler) from the raw block shape produced by the orchestrator. All
helpers tolerate a missing block by treating it as ``status=not_requested``.
"""

from __future__ import annotations

from typing import Any, Optional


def _block(ctx: Any) -> dict:
    """Return the statistical_hypothesis dict from ctx (or a neutral default)."""
    block = getattr(ctx, "statistical_hypothesis", None)
    if not block:
        return {"status": "not_requested"}
    return block


def status(ctx: Any) -> str:
    """One of ``ok``, ``skipped``, ``not_requested``."""
    return _block(ctx).get("status", "not_requested")


def is_ok(ctx: Any) -> bool:
    return status(ctx) == "ok"


def is_skipped(ctx: Any) -> bool:
    return status(ctx) == "skipped"


def is_not_requested(ctx: Any) -> bool:
    return status(ctx) == "not_requested"


def skip_reason(ctx: Any) -> Optional[str]:
    """e.g. ``insufficient_runs``, ``import_error``, ``ground_truth_missing``."""
    return _block(ctx).get("reason")


def skip_message(ctx: Any) -> Optional[str]:
    """Human-readable explanation for the skip / not_requested state."""
    return _block(ctx).get("message")


def min_required(ctx: Any) -> Optional[int]:
    """Minimum runs per fault category enforced by the gate."""
    return _block(ctx).get("min_required")


def observed_per_category(ctx: Any) -> dict[str, int]:
    """``{category_name: total_run_count}`` as recorded by the gate."""
    return _block(ctx).get("observed_per_category") or {}


def results(ctx: Any) -> Optional[dict[str, Any]]:
    """H01–H09 hypothesis test results, available only when ``is_ok``.

    The orchestrator stores the framework's full output under ``results``,
    which itself contains ``{metadata, validation, results}``. The h01..h09
    dict we want is the *inner* ``results``. We tolerate either shape so
    that callers always receive ``{h01: {...}, h02: {...}, ...}``.
    """
    if not is_ok(ctx):
        return None
    outer = _block(ctx).get("results") or {}
    inner = outer.get("results")
    if isinstance(inner, dict) and any(k.startswith("h0") for k in inner.keys()):
        return inner
    return outer
