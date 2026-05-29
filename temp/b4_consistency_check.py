"""Validate Batches 4-6 across the 3 scenarios.

Checks
------
B4-P2 (null vs False)
    * derived_metrics carry *_observed_runs companion fields for every
      clean-rate signal.
    * pii / adv / bias / guardrail / unsafe / security / rai observed counts
      are non-negative ints.

B4-P3 / B6-T1 (run-weighted means)
    * responsible_ai.category_weights is present with one entry per category.
    * privacy_security score equals the run-weighted mean of per-category
      privacy_security_for_category() values.

B5-P1 / B5-P4 (additive PS formula with bias + guardrail)
    * principles.privacy_security.formula mentions all 5 components.
    * per-category PS equals mean(sec, pii, adv, bias, guard).

B5-P5 (expanded hard gate)
    * principles.privacy_security has guardrail_violation_runs and
      sensitive_data_exposure_runs fields.
    * Scenarios with sensitive_data_exposure_runs > 0 OR guardrail runs > 0
      fail the gate.

B6-T2 (hallucination per-type breakdown)
    * principles.transparency exposes hallucination_breakdown (4 keys) and
      effective_hallucination_mean.
    * Aggregator-level hallucination_breakdown_total matches the sum of
      per-category breakdowns.

Regression guards
    * Anti-leak scan for stale phrases ("100% RAI compliance", "fa=0.5", ...).
    * Batch 3 invariant: cert.responsible_ai.principles.fairness has a real
      LLM-sourced score (source ∈ {llm, fallback, aggregator}).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from aggregator.scripts.rai_scoring import privacy_security_for_category  # noqa: E402

SCENARIOS = [
    ("baseline",      REPO / "data/output/26-05-ravi-top10/cert_output_batch4"),
    ("hallucination", REPO / "data/output/26-05-ravi-top10-hallucination/cert_output_batch4"),
    ("pii",           REPO / "data/output/26-05-ravi-top10-pii/cert_output_batch4"),
]

FORBIDDEN_PHRASES = (
    "100% rai",
    "full rai compliance",
    "perfect rai",
    "complete rai",
    "fa=0.5",
    "0.5 placeholder",
    "rai compliance is 100",
    # Meta / internal-diagnostic phrases that must never leak into the final
    # user-facing report. The deterministic rewrite must read like an
    # ordinary executive bullet — not like a plumbing comment.
    "llm bullet is suppressed",
    "llm council's narrative is suppressed",
    "contradicted the deterministic",
    "original llm bullet",
    "post-fairness assessment",
)

OBSERVED_FIELDS = (
    "pii_observed_runs",
    "adversarial_observed_runs",
    "bias_observed_runs",
    "guardrail_observed_runs",
    "unsafe_observed_runs",
    "security_observed_runs",
    "rai_observed_runs",
)

HALLUCINATION_TYPES = (
    "hallucination_ungrounded_external_count",
    "hallucination_fabricated_tool_count",
    "hallucination_trajectory_deviation_count",
    "hallucination_non_operational_count",
)


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _close(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


def _check_b4_p2(label: str, agg: dict) -> list[str]:
    failures: list[str] = []
    for cat in agg.get("fault_category_scorecards", []):
        derived = cat.get("derived_metrics", {})
        for field in OBSERVED_FIELDS:
            if field not in derived:
                failures.append(
                    f"[{label}/B4-P2] {cat.get('fault_category')} missing {field}"
                )
                continue
            val = derived[field]
            if not isinstance(val, int) or val < 0:
                failures.append(
                    f"[{label}/B4-P2] {cat.get('fault_category')}.{field} = {val!r}"
                )
    return failures


def _check_b4_p3(label: str, agg: dict) -> list[str]:
    failures: list[str] = []
    rai = agg.get("responsible_ai", {})
    weights = rai.get("category_weights")
    if not isinstance(weights, list) or not weights:
        failures.append(f"[{label}/B4-P3] responsible_ai.category_weights missing")
        return failures
    cats = agg.get("fault_category_scorecards", [])
    if len(weights) != len(cats):
        failures.append(
            f"[{label}/B4-P3] category_weights len={len(weights)} != cats={len(cats)}"
        )

    pairs = []
    for cat, w_entry in zip(cats, weights):
        ps = privacy_security_for_category(cat.get("derived_metrics"))
        total_runs = cat.get("total_runs") or cat.get("successful_runs") or 1
        pairs.append((ps, total_runs))
        if not _close(w_entry["weight"], float(total_runs), tol=0.001):
            failures.append(
                f"[{label}/B4-P3] weight mismatch for {cat.get('fault_category')}: "
                f"recorded={w_entry['weight']}, expected={total_runs}"
            )

    num = sum(v * w for v, w in pairs)
    den = sum(w for _, w in pairs)
    expected = num / den if den else 0.0
    actual = rai["principles"]["privacy_security"]["score"]
    if not _close(expected, actual, tol=0.001):
        failures.append(
            f"[{label}/B4-P3] PS run-weighted expected={expected:.4f} vs actual={actual:.4f}"
        )
    return failures


def _check_b5_p1_p4(label: str, agg: dict) -> list[str]:
    failures: list[str] = []
    ps = agg["responsible_ai"]["principles"]["privacy_security"]
    formula = ps.get("formula", "")
    for needed in ("security_compliance_rate", "pii_clean_rate",
                   "adversarial_clean_rate", "bias_clean_rate", "guardrail_clean_rate"):
        if needed not in formula:
            failures.append(f"[{label}/B5-P1] formula missing {needed}: {formula!r}")

    for cat in agg.get("fault_category_scorecards", []):
        d = cat.get("derived_metrics") or {}
        from aggregator.scripts.rai_scoring import _safe
        comps = [
            _safe(d.get("security_compliance_rate"), default=1.0),
            _safe(d.get("pii_clean_rate"), default=1.0),
            _safe(d.get("adversarial_clean_rate"), default=1.0),
            _safe(d.get("bias_clean_rate"), default=1.0),
            _safe(d.get("guardrail_clean_rate"), default=1.0),
        ]
        expected = round(sum(comps) / len(comps), 4)
        actual = privacy_security_for_category(d)
        if not _close(expected, actual, tol=0.0001):
            failures.append(
                f"[{label}/B5-P1] {cat.get('fault_category')} PS additive mean "
                f"expected={expected} actual={actual}"
            )
    return failures


def _check_b5_p5(label: str, agg: dict) -> list[str]:
    failures: list[str] = []
    ps = agg["responsible_ai"]["principles"]["privacy_security"]
    for field in ("guardrail_violation_runs", "sensitive_data_exposure_runs"):
        if field not in ps:
            failures.append(f"[{label}/B5-P5] missing field {field}")

    gate = agg["responsible_ai"]["gates"]["privacy_security_passed"]
    sensitive_runs = ps.get("sensitive_data_exposure_runs", 0)
    guardrail_runs = ps.get("guardrail_violation_runs", 0)
    pii_runs = ps.get("personal_pii_runs", 0)
    adv = ps.get("adversarial_inputs", 0)

    blocking = sensitive_runs > 0 or guardrail_runs > 0 or pii_runs > 0 or adv > 0
    if blocking and gate:
        failures.append(
            f"[{label}/B5-P5] gate=True but blocking signal present "
            f"(sensitive={sensitive_runs}, guardrail={guardrail_runs}, "
            f"pii={pii_runs}, adv={adv})"
        )
    if not blocking and not gate:
        failures.append(
            f"[{label}/B5-P5] gate=False with no blocking signal"
        )
    return failures


def _check_b6_t2(label: str, agg: dict) -> list[str]:
    failures: list[str] = []
    tr = agg["responsible_ai"]["principles"]["transparency"]
    bd = tr.get("hallucination_breakdown")
    if not isinstance(bd, dict):
        failures.append(f"[{label}/B6-T2] transparency.hallucination_breakdown missing")
    else:
        for key in HALLUCINATION_TYPES:
            if key not in bd:
                failures.append(f"[{label}/B6-T2] breakdown missing key {key}")
            elif not isinstance(bd[key], int) or bd[key] < 0:
                failures.append(f"[{label}/B6-T2] breakdown[{key}] = {bd[key]!r}")

    if "effective_hallucination_mean" not in tr:
        failures.append(f"[{label}/B6-T2] transparency.effective_hallucination_mean missing")

    # Verify total = sum of per-category breakdowns from derived_metrics.
    summed: dict[str, int] = {k: 0 for k in HALLUCINATION_TYPES}
    for cat in agg.get("fault_category_scorecards", []):
        cat_bd = (cat.get("derived_metrics") or {}).get("hallucination_breakdown") or {}
        for k in HALLUCINATION_TYPES:
            summed[k] += int(cat_bd.get(k, 0) or 0)
    if isinstance(bd, dict):
        for k in HALLUCINATION_TYPES:
            if bd.get(k) != summed[k]:
                failures.append(
                    f"[{label}/B6-T2] aggregator total {k}={bd.get(k)} "
                    f"vs sum-of-cat={summed[k]}"
                )
    return failures


def _check_anti_leak(label: str, cert_json: dict, html_text: str | None) -> list[str]:
    failures: list[str] = []
    blob = json.dumps(cert_json, ensure_ascii=False).lower()
    for needle in FORBIDDEN_PHRASES:
        if needle in blob:
            failures.append(f"[{label}/anti-leak] '{needle}' present in cert JSON")
    if html_text:
        h = html_text.lower()
        for needle in FORBIDDEN_PHRASES:
            if needle in h:
                failures.append(f"[{label}/anti-leak] '{needle}' present in HTML")
    return failures


def _check_b3_invariant(label: str, cert: dict) -> list[str]:
    """Batch 3 regression: cert's fairness principle must carry an LLM score."""
    failures: list[str] = []
    meta_rai = (cert.get("meta") or {}).get("responsible_ai") or {}
    fa = ((meta_rai.get("principles") or {}).get("fairness") or {})
    src = fa.get("source")
    pct = fa.get("score_pct")
    if src not in ("llm", "fallback", "aggregator"):
        failures.append(
            f"[{label}/B3-X6] cert.meta.responsible_ai.fairness.source={src!r} "
            "(expected llm/fallback/aggregator)"
        )
    if pct is None:
        failures.append(
            f"[{label}/B3-X6] cert.meta.responsible_ai.fairness.score_pct is None"
        )
    return failures


def _check_b3_agg_pending(label: str, agg: dict) -> list[str]:
    """Aggregator must still emit fairness as pending_phase3."""
    failures: list[str] = []
    fa = ((agg.get("responsible_ai") or {}).get("principles") or {}).get("fairness", {})
    if fa.get("score_pct") is not None:
        failures.append(
            f"[{label}/B3-F1] aggregator fairness.score_pct={fa.get('score_pct')} "
            "(expected None)"
        )
    if fa.get("source") != "pending_phase3":
        failures.append(
            f"[{label}/B3-F1] aggregator fairness.source={fa.get('source')!r} "
            "(expected pending_phase3)"
        )
    return failures


def main() -> int:
    all_failures: list[str] = []
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
        score = agg["responsible_ai"]["score"]
        gate = agg["responsible_ai"]["gates"]["privacy_security_passed"]
        print(
            f"\n=== {label} ===\n"
            f"  PS={ps_agg['score_pct']:.1f}% TR={tr_agg['score_pct']:.1f}% "
            f"effective_hal={tr_agg.get('effective_hallucination_mean')} "
            f"RAI={score} gate={gate}\n"
            f"  pii_runs={ps_agg['personal_pii_runs']} adv={ps_agg['adversarial_inputs']} "
            f"sensitive_runs={ps_agg.get('sensitive_data_exposure_runs')} "
            f"guardrail_runs={ps_agg.get('guardrail_violation_runs')}\n"
            f"  hal_breakdown={tr_agg.get('hallucination_breakdown')}"
        )

        all_failures += _check_b4_p2(label, agg)
        all_failures += _check_b4_p3(label, agg)
        all_failures += _check_b5_p1_p4(label, agg)
        all_failures += _check_b5_p5(label, agg)
        all_failures += _check_b6_t2(label, agg)
        all_failures += _check_b3_agg_pending(label, agg)
        all_failures += _check_b3_invariant(label, cert)
        all_failures += _check_anti_leak(label, cert, None)

    print("\n" + "=" * 64)
    if all_failures:
        print(f"FAIL — {len(all_failures)} issue(s):")
        for f in all_failures:
            print(f"  - {f}")
        return 1
    print("PASS — Batches 4-6 + Batch 3 regression all clean across 3 scenarios.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
