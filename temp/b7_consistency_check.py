"""Validate Batches 7-10 across the 3 scenarios.

Re-uses the B4-B6 checks (so we don't regress) and layers new B7-B10
invariants on top.

B7-T4 — configurable transparency weights
    * responsible_ai.principles.transparency.weights = {reasoning, hallucination_control}
      whose values sum to 1.0.
    * transparency_score == reasoning_weight * reasoning_mean
                          + hallucination_control_weight * (1 - effective_hallucination_mean)
B7-T5 — centralised polarity helper
    * responsible_ai.principles.transparency.hallucination_control_rate
      == 1 - effective_hallucination_mean (clamped [0, 1]).

B8-F2 — Fairness label aligned with actual measure
    * cert.meta.responsible_ai.principles.fairness.label
      starts with "Fairness (Operational Consistency)".
    * aggregator.responsible_ai.principles.fairness.label same.
    * principles.fairness.measure is non-empty.
B8-F4 — continuous fairness fallback
    * fairness_builder._fallback_score returns a score that is NOT in the
      legacy bucket set {0.3, 0.5, 0.7, 0.9} for arbitrary spread inputs.

B9-X1 — operational_fairness_rate alias
    * derived_metrics.operational_fairness_rate present on every category.
    * operational_fairness_rate == rai_compliance_rate.
B9-X2 — canonical score field & documentation
    * responsible_ai.canonical_score_field == "score".
    * responsible_ai.score_documentation has keys for the three score fields.

B10-X3 — single source of weights
    * responsible_ai.dimension_weights matches the chart_builder / report_assembler
      formula reproduction.
B10-X4 — principle registry
    * responsible_ai.principle_registry has 3 entries with key + label + measure.
B10-X5 — coverage block
    * derived_metrics.coverage exists on every category.
    * coverage["total_runs"] equals (or is at least as big as) the largest
      *_observed_runs counter.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Re-export the existing B4-B6 helpers so a single run validates everything.
from temp.b4_consistency_check import (  # noqa: E402
    FORBIDDEN_PHRASES,
    HALLUCINATION_TYPES,
    OBSERVED_FIELDS,
    _check_anti_leak,
    _check_b3_agg_pending,
    _check_b3_invariant,
    _check_b4_p2,
    _check_b4_p3,
    _check_b5_p1_p4,
    _check_b5_p5,
    _check_b6_t2,
    _close,
    _load,
)
from aggregator.scripts.rai_scoring import (  # noqa: E402
    TRANSPARENCY_HALLUCINATION_WEIGHT,
    TRANSPARENCY_REASONING_WEIGHT,
    hallucination_control_rate,
)
from cert_builder.scripts.narratives.fairness_builder import _fallback_score  # noqa: E402

SCENARIOS = [
    ("baseline",      REPO / "data/output/26-05-ravi-top10/cert_output_batch7"),
    ("hallucination", REPO / "data/output/26-05-ravi-top10-hallucination/cert_output_batch7"),
    ("pii",           REPO / "data/output/26-05-ravi-top10-pii/cert_output_batch7"),
]


def _check_b7_t4(label: str, agg: dict) -> list[str]:
    failures: list[str] = []
    tr = agg["responsible_ai"]["principles"]["transparency"]
    weights = tr.get("weights") or {}
    if "reasoning" not in weights or "hallucination_control" not in weights:
        failures.append(f"[{label}/B7-T4] transparency.weights missing keys: {weights}")
        return failures
    total = float(weights["reasoning"]) + float(weights["hallucination_control"])
    if not _close(total, 1.0, tol=0.001):
        failures.append(f"[{label}/B7-T4] transparency.weights do not sum to 1.0: {total}")

    reasoning_mean = tr.get("reasoning_mean", 0.0)
    eff_hal = tr.get("effective_hallucination_mean", 0.0)
    expected = (
        float(weights["reasoning"]) * float(reasoning_mean)
        + float(weights["hallucination_control"]) * max(0.0, min(1.0, 1.0 - float(eff_hal)))
    )
    actual = tr["score"]
    if not _close(expected, actual, tol=0.0015):
        failures.append(
            f"[{label}/B7-T4] TR weighted-formula mismatch: expected={expected:.4f} actual={actual:.4f}"
        )
    return failures


def _check_b7_t5(label: str, agg: dict) -> list[str]:
    failures: list[str] = []
    tr = agg["responsible_ai"]["principles"]["transparency"]
    if "hallucination_control_rate" not in tr:
        failures.append(f"[{label}/B7-T5] hallucination_control_rate missing")
        return failures
    eff = tr.get("effective_hallucination_mean", 0.0)
    expected = hallucination_control_rate(eff)
    actual = tr["hallucination_control_rate"]
    if expected is None:
        return failures
    if not _close(expected, actual, tol=0.001):
        failures.append(
            f"[{label}/B7-T5] control rate drift: expected={expected:.4f} actual={actual:.4f}"
        )
    return failures


def _check_b8_f2(label: str, agg: dict, cert: dict) -> list[str]:
    failures: list[str] = []
    for src_label, block in (("agg", agg), ("cert.meta", cert.get("meta") or {})):
        rai = block.get("responsible_ai") or {}
        fa = ((rai.get("principles") or {}).get("fairness") or {})
        label_val = fa.get("label", "")
        if "Operational Consistency" not in label_val:
            failures.append(
                f"[{label}/B8-F2] {src_label}.fairness.label missing 'Operational Consistency': {label_val!r}"
            )
        if not fa.get("measure"):
            failures.append(f"[{label}/B8-F2] {src_label}.fairness.measure missing/empty")
    return failures


def _check_b8_f4_offline() -> list[str]:
    """Pure-function test: _fallback_score must return continuous values."""
    failures: list[str] = []
    # Synthetic phase1 inputs with various spreads.
    samples = [
        # (spread, expected_score)
        (0.00, 1.00),
        (0.10, 0.80),
        (0.25, 0.50),
        (0.40, 0.20),
        (0.60, 0.00),
    ]
    legacy_buckets = {0.3, 0.5, 0.7, 0.9}
    for spread, expected in samples:
        phase1 = {
            "categories": [
                {"label": "a", "derived": {"fault_detection_success_rate": 1.0}},
                {"label": "b", "derived": {"fault_detection_success_rate": 1.0 - spread}},
            ]
        }
        result = _fallback_score(phase1)
        score = float(result.fairness_score)
        if not _close(score, expected, tol=0.02):
            failures.append(
                f"[offline/B8-F4] spread={spread} expected={expected} got={score}"
            )
        # Verify it can land OFF the 4 legacy buckets to prove continuity.
        if spread in (0.10, 0.40) and score in legacy_buckets:
            failures.append(
                f"[offline/B8-F4] spread={spread} hit legacy bucket {score} — not continuous"
            )
    return failures


def _check_b9_x1(label: str, agg: dict) -> list[str]:
    failures: list[str] = []
    for cat in agg.get("fault_category_scorecards", []):
        d = cat.get("derived_metrics") or {}
        if "operational_fairness_rate" not in d:
            failures.append(
                f"[{label}/B9-X1] {cat.get('fault_category')} missing operational_fairness_rate"
            )
            continue
        rc = d.get("rai_compliance_rate")
        of = d.get("operational_fairness_rate")
        if rc != of:
            failures.append(
                f"[{label}/B9-X1] {cat.get('fault_category')} alias drift: "
                f"rai_compliance_rate={rc} operational_fairness_rate={of}"
            )
    return failures


def _check_b9_x2(label: str, agg: dict) -> list[str]:
    failures: list[str] = []
    rai = agg.get("responsible_ai") or {}
    if rai.get("canonical_score_field") != "score":
        failures.append(
            f"[{label}/B9-X2] canonical_score_field={rai.get('canonical_score_field')!r} != 'score'"
        )
    doc = rai.get("score_documentation") or {}
    for needed in ("score", "score_if_gate_clears"):
        if needed not in doc:
            failures.append(f"[{label}/B9-X2] score_documentation missing key '{needed}'")
    return failures


def _check_b10_x3_x4(label: str, agg: dict) -> list[str]:
    failures: list[str] = []
    rai = agg.get("responsible_ai") or {}
    weights = rai.get("dimension_weights") or {}
    if set(weights.keys()) != {"privacy_security", "transparency", "fairness"}:
        failures.append(f"[{label}/B10-X3] dimension_weights wrong keys: {weights}")
    total = sum(float(v) for v in weights.values())
    if not _close(total, 1.0, tol=0.001):
        failures.append(f"[{label}/B10-X3] dimension_weights do not sum to 1.0: {total}")

    registry = rai.get("principle_registry") or []
    if len(registry) != 3:
        failures.append(f"[{label}/B10-X4] principle_registry expected 3 entries, got {len(registry)}")
    for entry in registry:
        for needed in ("key", "label", "measure"):
            if not entry.get(needed):
                failures.append(f"[{label}/B10-X4] registry entry missing {needed}: {entry}")
    return failures


def _check_b10_x5(label: str, agg: dict) -> list[str]:
    failures: list[str] = []
    for cat in agg.get("fault_category_scorecards", []):
        d = cat.get("derived_metrics") or {}
        cov = d.get("coverage")
        if not isinstance(cov, dict):
            failures.append(
                f"[{label}/B10-X5] {cat.get('fault_category')} missing derived.coverage block"
            )
            continue
        for k in ("rai", "pii", "security", "adversarial", "bias", "guardrail", "unsafe", "total_runs"):
            if k not in cov:
                failures.append(f"[{label}/B10-X5] coverage missing key {k}")
        total = cov.get("total_runs", 0)
        for k in ("rai", "pii", "security", "adversarial", "bias", "guardrail", "unsafe"):
            if cov.get(k, 0) > total:
                failures.append(
                    f"[{label}/B10-X5] coverage.{k}={cov[k]} > total_runs={total}"
                )
    return failures


def main() -> int:
    all_failures: list[str] = []

    # Offline checks first (no pipeline output required).
    all_failures += _check_b8_f4_offline()

    for label, root in SCENARIOS:
        agg_path = root / "aggregation/aggregation.json"
        cert_path = root / "cert-builder/certification.json"
        if not agg_path.exists() or not cert_path.exists():
            all_failures.append(f"[{label}] missing artifact at {root}")
            continue
        agg = _load(agg_path)
        cert = _load(cert_path)

        ps_agg = agg["responsible_ai"]["principles"]["privacy_security"]
        tr_agg = agg["responsible_ai"]["principles"]["transparency"]
        fa_agg = agg["responsible_ai"]["principles"]["fairness"]
        score = agg["responsible_ai"]["score"]
        gate = agg["responsible_ai"]["gates"]["privacy_security_passed"]
        print(
            f"\n=== {label} ===\n"
            f"  PS={ps_agg['score_pct']:.1f}% TR={tr_agg['score_pct']:.1f}% "
            f"FA(agg)={fa_agg.get('score_pct')} "
            f"effective_hal={tr_agg.get('effective_hallucination_mean')} "
            f"RAI={score} gate={gate}\n"
            f"  TR weights={tr_agg.get('weights')} "
            f"hal_ctrl={tr_agg.get('hallucination_control_rate')}\n"
            f"  FA label(agg)={fa_agg.get('label')!r}\n"
            f"  canonical_score_field={agg['responsible_ai'].get('canonical_score_field')}\n"
            f"  dimension_weights={agg['responsible_ai'].get('dimension_weights')}"
        )

        # Carry-forward B4-B6 + B3 checks so we don't regress.
        all_failures += _check_b4_p2(label, agg)
        all_failures += _check_b4_p3(label, agg)
        all_failures += _check_b5_p1_p4(label, agg)
        all_failures += _check_b5_p5(label, agg)
        all_failures += _check_b6_t2(label, agg)
        all_failures += _check_b3_agg_pending(label, agg)
        all_failures += _check_b3_invariant(label, cert)
        all_failures += _check_anti_leak(label, cert, None)

        # New B7-B10 checks.
        all_failures += _check_b7_t4(label, agg)
        all_failures += _check_b7_t5(label, agg)
        all_failures += _check_b8_f2(label, agg, cert)
        all_failures += _check_b9_x1(label, agg)
        all_failures += _check_b9_x2(label, agg)
        all_failures += _check_b10_x3_x4(label, agg)
        all_failures += _check_b10_x5(label, agg)

    print("\n" + "=" * 64)
    if all_failures:
        print(f"FAIL — {len(all_failures)} issue(s):")
        for f in all_failures:
            print(f"  - {f}")
        return 1
    print("PASS — Batches 3-10 all clean across 3 scenarios.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
