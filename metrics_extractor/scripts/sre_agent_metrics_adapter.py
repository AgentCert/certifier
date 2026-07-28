"""
SRE agent metrics adapter — Phase 1 equivalent for the sre_agent_fault category.

The ITBench SRE agent (agents/sre-agent: "Zero" wrapping Codex CLI + MCP
offline-incident-analysis tools) investigates an offline incident snapshot
and writes a diagnosis to ``agent_output.json`` (entities, contributing
factors, propagation chain). Like the CISO agent, this isn't a live-trace
fault-injection-and-diagnose shape the standard TraceMetricsExtractor
consumes -- ITBench's own LLM-as-a-judge tooling (ITBench-Evaluations,
itbench_evaluations module) already scores that diagnosis against
ground_truth.yaml per a fixed set of criteria (ROOT_CAUSE_ENTITY,
ROOT_CAUSE_REASONING, PROPAGATION_CHAIN, FAULT_LOCALIZATION, etc.), each
producing either a bare numeric score or a dict with precision/recall/f1
sub-fields (for the entity-matching criteria). This module's only job is to
adapt one scored trial from ITBench-Evaluations' raw_results (see
itbench_evaluations/__main__.py's "structured_results" -- entries shaped
{"incident_id", "trial_id", "scores": {...}}) into the same per-run doc
shape (quantitative/qualitative/fault_name/run_id, agent_id/agent_name at
the top level -- NOT nested, see ciso_metrics_adapter.py's own note on that
exact bug) every other category's per-run metrics doc uses.
"""

from typing import Any, Dict, List, Optional

# Matches certifier/configs/fault_categories.json's sre_agent_fault bucket --
# every ITBench-Lite SRE scenario downloaded for this agent's certification.
SRE_AGENT_FAULT_CATEGORY = "sre_agent_fault"

# Criteria whose scores are dicts with precision/recall/f1 sub-fields
# (produced by compute_all_k_metrics for entity-matching), rather than a
# bare numeric value.
_ENTITY_MATCH_PREFIXES = ("root_cause_entity",)


def _extract_numeric_score(entry: Any) -> Optional[float]:
    """Normalize one criterion's score entry into a single float in [0, 1].

    Handles the 3 shapes itbench_evaluations produces:
      - a bare number (most criteria)
      - {"calculation_precision"/"_recall"/"_f1": float, ...} (entity@k criteria
        -- prefer f1, the balanced precision/recall summary)
      - {"score": float} or {"value": float} (defensive fallback for any
        criterion whose judge prompt names the field differently)
    """
    if isinstance(entry, (int, float)):
        return float(entry)
    if isinstance(entry, dict):
        for key in ("calculation_f1", "score", "value"):
            v = entry.get(key)
            if isinstance(v, (int, float)):
                return float(v)
    return None


def _summarize_scores(scores: Dict[str, Any]) -> Dict[str, float]:
    """Flatten itbench_evaluations' per-criterion scores dict into a flat
    {criterion: numeric_score} map, dropping criteria with no numeric value
    (e.g. malformed judge output) rather than guessing at 0."""
    summary = {}
    for criterion, entry in scores.items():
        val = _extract_numeric_score(entry)
        if val is not None:
            summary[criterion] = val
    return summary


def build_sre_agent_metrics_doc(
    scores: Dict[str, Any],
    *,
    scenario_name: str,
    run_id: str,
    duration_seconds: Optional[float] = None,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    experiment_id: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    bad_run: bool = False,
) -> Dict[str, Any]:
    """Convert one ITBench-Evaluations judge verdict into a per-run metrics
    doc matching the shape the rest of the certifier pipeline expects.

    Args:
        scores: the "scores" dict from one ITBench-Evaluations raw_results
            entry for this (incident, trial) -- see module docstring.
        scenario_name: the ITBench-Lite scenario directory name (e.g.
            "Scenario-2") -- becomes this doc's fault_name, matching
            certifier/configs/fault_categories.json's sre_agent_fault bucket.
        run_id: the agent run this evaluation belongs to.
        duration_seconds: wall-clock time for the Zero/Codex investigation
            (measured by the mass-execution driver, not by
            itbench_evaluations itself).
        bad_run: True if the judge could not score this run at all (e.g. the
            agent never produced a parseable agent_output.json) -- recorded
            explicitly rather than silently producing an all-empty doc, so
            Phase 2 aggregation can distinguish "failed to investigate" from
            "investigated but scored zero."
    """
    score_summary = _summarize_scores(scores) if scores else {}

    quantitative: Dict[str, Any] = {
        "run_id": run_id,
        "injected_fault_name": scenario_name,
        "injected_fault_category": SRE_AGENT_FAULT_CATEGORY,
        "sre_agent_bad_run": bad_run,
    }
    for criterion, value in score_summary.items():
        quantitative[f"sre_{criterion}_score"] = value

    # A single pass/fail summary for the scorecard's headline detection rate,
    # analogous to fault_detection_success_rate elsewhere: root cause entity
    # identification is the closest SRE-agent equivalent to "did it detect
    # the fault" (threshold matches itbench_evaluations' own pass@1 convention
    # of scoring a single correct top-ranked entity as a pass).
    entity_score = score_summary.get("root_cause_entity_k") or score_summary.get("root_cause_entity")
    if entity_score is not None:
        quantitative["sre_agent_task_passed"] = entity_score >= 0.5
    if duration_seconds is not None:
        quantitative["sre_agent_time_to_resolve"] = float(duration_seconds)
    if input_tokens is not None:
        quantitative["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        quantitative["output_tokens"] = int(output_tokens)

    if bad_run:
        agent_summary = (
            f"Agent did not produce a scoreable diagnosis for {scenario_name} "
            "(no agent_output.json, or it could not be parsed as JSON)."
        )
    elif not score_summary:
        agent_summary = f"Agent's diagnosis for {scenario_name} could not be scored by the judge (no criteria returned)."
    else:
        criteria_desc = ", ".join(f"{k}={v:.2f}" for k, v in sorted(score_summary.items()))
        agent_summary = f"Agent diagnosed {scenario_name}; judge scores: {criteria_desc}."

    qualitative: Dict[str, Any] = {"agent_summary": agent_summary}

    # Feeds an LLM Council judged dimension the same way ciso_policy_correctness_notes
    # does for CISO -- a domain-specific narrative distinct from the generic summary.
    if entity_score is not None:
        qualitative["sre_incident_diagnosis_notes"] = (
            f"Root-cause entity identification score: {entity_score:.2f} "
            f"({'passed' if entity_score >= 0.5 else 'failed'} the pass@1 threshold)."
        )

    return {
        "run_id": run_id,
        "fault_name": scenario_name,
        "fault_category": SRE_AGENT_FAULT_CATEGORY,
        "quantitative": quantitative,
        "qualitative": qualitative,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "agent_version": None,
        "experiment_id": experiment_id,
    }


def build_sre_agent_metrics_docs(
    entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Batch convenience wrapper. Each item must supply the same keyword
    arguments as build_sre_agent_metrics_doc, e.g.:

        build_sre_agent_metrics_docs([
            {"scores": {...}, "scenario_name": "Scenario-2",
             "run_id": "run-1", "duration_seconds": 134.0, "agent_id": "a1"},
            ...
        ])
    """
    return [build_sre_agent_metrics_doc(**item) for item in entries]
