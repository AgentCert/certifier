"""
CISO metrics adapter — Phase 1 equivalent for the ciso_fault category.

CISO scenarios (compliance-as-code policy generation/remediation via
Kyverno/OPA/Ansible) are not fault-injection-and-diagnose shaped, so they
don't go through TraceMetricsExtractor's LLM-based trace interpretation —
ITBench's own evaluation tooling (evaluate.yml + evaluation.py /
evaluation/main.py per scenario type) already produces a deterministic,
structured pass/fail result by re-querying the live cluster/host. This
module's only job is to adapt that ITBench-native output into the same
per-run doc shape (``quantitative``/``qualitative``/``fault_name``/``run_id``)
every other category's per-run metrics doc uses, so it can flow through the
existing aggregator unchanged.

ITBench's own output shapes (confirmed against scenarios/ciso/*/playbooks/evaluate.yml
and evaluation.py / evaluation/main.py in itbench-hub/ITBench):
  - Scenario 1 (Gen-CIS-b-K8s-Kyverno):        {"pass": bool, "tasks": {...}}
  - Scenario 2 (Gen-CIS-b-K8s-Kubectl-OPA):    {"pass": bool, "details": str} or
                                                {"pass": false, "errors": [{"code","message"}]}
  - Scenario 3 (Gen-CIS-b-RHEL9-Ansible-OPA):  same shape as scenario 2
  - Scenario 4 (Upd-CIS-b-K8s-Kyverno):        {"pass": bool, "details": [{"pass","message","error"?}]}

None of them report a numeric score or duration themselves — timing is
measured by whatever harness invokes the agent (mirroring ITBench's own
leaderboard layer, which reports "Mean Agent Execution Duration" /
"Time To Resolve" from outside the per-scenario evaluation).
"""

from typing import Any, Dict, List, Optional, Tuple

# The 4 ITBench CISO scenario types, matching certifier/configs/fault_categories.json's
# ciso_fault bucket and docs/Methodologies/02-Experiment-Design/2.3-Fault-Taxonomy.md section 6.
CISO_SCENARIO_TYPES = {
    "Gen-CIS-b-K8s-Kyverno",
    "Gen-CIS-b-K8s-Kubectl-OPA",
    "Gen-CIS-b-RHEL9-Ansible-OPA",
    "Upd-CIS-b-K8s-Kyverno",
}


def _extract_pass_and_reason(evaluation_result: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Normalize the 3 distinct ITBench evaluation output shapes into (pass, failure_reason).

    Returns failure_reason=None when pass=True (nothing to explain).
    """
    passed = bool(evaluation_result.get("pass", False))
    if passed:
        return True, None

    # Scenario 2/3 error path: {"pass": false, "errors": [{"code","message"}, ...]}
    errors = evaluation_result.get("errors")
    if errors:
        return False, "; ".join(
            f"{e.get('code', 'error')}: {e.get('message', '')}".strip(": ")
            for e in errors
            if isinstance(e, dict)
        )

    # Scenario 4 shape: {"pass": false, "details": [{"pass": bool, "message": str, "error"?: str}, ...]}
    details = evaluation_result.get("details")
    if isinstance(details, list):
        failing = [d for d in details if isinstance(d, dict) and d.get("pass") is False]
        if failing:
            return False, "; ".join(
                d.get("error") or d.get("message", "unspecified check failed") for d in failing
            )

    # Scenario 4 top-level exception path: {"pass": false, "details": [], "error": str}
    if evaluation_result.get("error"):
        return False, str(evaluation_result["error"])

    # Scenario 1 shape carries no failure text at all -- {"pass": false, "tasks": {...}}
    tasks = evaluation_result.get("tasks")
    if isinstance(tasks, dict):
        failing_tasks = [k for k, v in tasks.items() if v is False]
        if failing_tasks:
            return False, f"failed task(s): {', '.join(failing_tasks)}"

    return False, "evaluation reported pass=false with no further detail"


def _extract_unchanged_policy_preservation(evaluation_result: Dict[str, Any]) -> Optional[bool]:
    """Scenario 4 (Upd-CIS-b-K8s-Kyverno) only: did the agent avoid corrupting
    OTHER, unrelated existing policies while remediating the target one?
    (ITBench's evaluation.py:check_unchanged_policies via compare_dicts against
    manifests/existing-policy.yaml.) Returns None for scenario types where this
    check doesn't apply -- it must not be scored as a failure when it was never run.
    """
    details = evaluation_result.get("details")
    if not isinstance(details, list):
        return None
    unchanged_checks = [
        d for d in details
        if isinstance(d, dict) and "unchanged" in str(d.get("message", "")).lower()
    ]
    if not unchanged_checks:
        return None
    return all(d.get("pass") is True for d in unchanged_checks)


def build_ciso_metrics_doc(
    evaluation_result: Dict[str, Any],
    *,
    scenario_type: str,
    run_id: str,
    duration_seconds: Optional[float] = None,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    experiment_id: Optional[str] = None,
    tool_selection_accuracy: Optional[float] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Convert one ITBench CISO evaluation result into a per-run metrics doc
    matching the shape the rest of the certifier pipeline (aggregator,
    fault_categories.json grouping) already expects.

    Args:
        evaluation_result: the raw dict ITBench's evaluate.yml/evaluation.py
            produced for this run (see module docstring for the 3 shapes).
        scenario_type: one of CISO_SCENARIO_TYPES -- becomes this doc's
            fault_name, matching certifier/configs/fault_categories.json's
            ciso_fault bucket so _group_docs_by_category routes it correctly.
        run_id: the agent run this evaluation belongs to.
        duration_seconds: wall-clock time from task start to evaluation,
            analogous to ITBench's own leaderboard "Time To Resolve" /
            "Mean Agent Execution Duration" (measured by the harness, not by
            evaluate.yml/evaluation.py themselves -- see module docstring).
        tool_selection_accuracy: optional passthrough if the harness can
            compute it (e.g. did the agent invoke kubectl/opa/kyverno/ansible
            appropriately) -- feeds the existing generic action_correctness
            metric unchanged.

    Raises:
        ValueError: if scenario_type isn't a recognized CISO scenario type.
    """
    if scenario_type not in CISO_SCENARIO_TYPES:
        raise ValueError(
            f"scenario_type={scenario_type!r} is not a recognized CISO scenario type "
            f"(expected one of {sorted(CISO_SCENARIO_TYPES)}). Check "
            "certifier/configs/fault_categories.json's ciso_fault bucket."
        )

    passed, failure_reason = _extract_pass_and_reason(evaluation_result)
    unchanged_preserved = _extract_unchanged_policy_preservation(evaluation_result)

    # Extract per-task sub-check booleans (Scenario 1/4 shape only; None for others).
    tasks: Optional[Dict[str, Any]] = evaluation_result.get("tasks")
    execute_policy: Optional[bool] = None
    generate_policy: Optional[bool] = None
    evidence_available: Optional[bool] = None
    if isinstance(tasks, dict):
        execute_policy = tasks.get("generate_assessment_posture")
        generate_policy = tasks.get("generate_policy")
        evidence_available = tasks.get("evidence_available")

    quantitative: Dict[str, Any] = {
        "run_id": run_id,
        "injected_fault_name": scenario_type,
        "injected_fault_category": "ciso_fault",
        "ciso_task_passed": passed,
    }
    if execute_policy is not None:
        quantitative["ciso_execute_policy"] = execute_policy
    if generate_policy is not None:
        quantitative["ciso_generate_policy"] = generate_policy
    if evidence_available is not None:
        quantitative["ciso_evidence_available"] = evidence_available
    if duration_seconds is not None:
        quantitative["ciso_time_to_resolve"] = float(duration_seconds)
    if unchanged_preserved is not None:
        quantitative["ciso_unchanged_policies_preserved"] = unchanged_preserved
    if tool_selection_accuracy is not None:
        quantitative["tool_selection_accuracy"] = float(tool_selection_accuracy)
    if input_tokens is not None:
        quantitative["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        quantitative["output_tokens"] = int(output_tokens)

    qualitative: Dict[str, Any] = {
        "agent_summary": (
            f"Agent completed the {scenario_type} CISO task successfully."
            if passed
            else f"Agent did not satisfy the {scenario_type} CISO task: {failure_reason}"
        ),
    }
    if failure_reason:
        qualitative["ciso_failure_reason"] = failure_reason

    # Feeds the LLM Council's ciso_policy_correctness_notes judged dimension
    # (aggregator/scripts/llm_council.py) -- a CISO-specific narrative distinct
    # from the generic agent_summary, covering both the live re-check verdict
    # and (when applicable) whether unrelated existing policies were preserved.
    correctness_note = (
        f"Policy submission for {scenario_type} passed ITBench's live re-check "
        "(Kyverno PolicyReport / OPA Rego evaluation)."
        if passed
        else f"Policy submission for {scenario_type} FAILED ITBench's live re-check: {failure_reason}."
    )
    if unchanged_preserved is True:
        correctness_note += " Unrelated existing policies were left unchanged."
    elif unchanged_preserved is False:
        correctness_note += " WARNING: one or more unrelated existing policies were altered."
    if generate_policy is not None or evidence_available is not None:
        sub = []
        if execute_policy is not None:
            sub.append(f"execute_policy={'yes' if execute_policy else 'no'}")
        if generate_policy is not None:
            sub.append(f"generate_policy={'yes' if generate_policy else 'no'}")
        if evidence_available is not None:
            sub.append(f"evidence_available={'yes' if evidence_available else 'no'}")
        correctness_note += f" Sub-checks: {', '.join(sub)}."
    qualitative["ciso_policy_correctness_notes"] = correctness_note

    return {
        "run_id": run_id,
        "fault_name": scenario_type,
        "fault_category": "ciso_fault",
        "quantitative": quantitative,
        "qualitative": qualitative,
        # Top-level, not nested under "agent" -- aggregator/scripts/aggregation.py's
        # _extract_agent_id()/_extract_agent_name() only check doc["agent_id"] /
        # doc["quantitative"]["agent_id"] (matching every other category's per-run
        # doc shape), so nesting these under "agent" silently dropped agent_id/
        # agent_name from every CISO doc and broke query_runs_by_agent() filtering.
        "agent_id": agent_id,
        "agent_name": agent_name,
        "agent_version": None,
        "experiment_id": experiment_id,
    }


def build_ciso_metrics_docs(
    evaluation_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Batch convenience wrapper. Each item in *evaluation_results* must supply
    the same keyword arguments as build_ciso_metrics_doc, e.g.:

        build_ciso_metrics_docs([
            {"evaluation_result": {...}, "scenario_type": "Gen-CIS-b-K8s-Kyverno",
             "run_id": "run-1", "duration_seconds": 134.0, "agent_id": "a1"},
            ...
        ])
    """
    return [build_ciso_metrics_doc(**item) for item in evaluation_results]
