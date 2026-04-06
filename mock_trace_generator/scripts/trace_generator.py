"""
Multi-fault OTEL-compliant trace generator for ITOps agent scenarios.

Generates a single Langfuse-format trace JSON simulating a scenario where
multiple Kubernetes faults are injected simultaneously and an autonomous
ITOps agent detects, triages, and remediates all of them using the tools
defined in available_tools.md (Kubernetes + Prometheus).
"""

import hashlib
import json
import random
import string
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from utils.setup_logging import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

from mock_trace_generator.schema.data_models import (
    ClusterScanResult,
    FaultDefinition,
    FaultInvestigationResult,
    FinalStabilityCheck,
    MultiFaultScenario,
    PostRemediationCheck,
    RemediationResult,
    SingleFaultDetail,
    ToolCallDetail,
    TriageDecision,
)
from mock_trace_generator.scripts.tools_registry import (
    AVAILABLE_TOOLS,
)


class MultiFaultTraceGenerator:
    """Generates OTEL-compliant Langfuse-format traces for multi-fault scenarios."""

    SYSTEM_PROMPT = (
        "You are an expert Kubernetes ITOps AI agent simulator. "
        "You generate realistic trace data that mimics an autonomous agent "
        "handling MULTIPLE simultaneous Kubernetes infrastructure faults. "
        "The agent uses specific tools (Kubernetes API, Prometheus) to detect, "
        "triage, investigate, remediate, and confirm resolution of faults. "
        "Your outputs must be technically accurate, referencing real Kubernetes "
        "concepts, realistic pod names, log excerpts, metrics, and tool outputs. "
        "The agent must demonstrate cross-fault awareness — understanding how "
        "one fault impacts another and prioritizing accordingly.\n\n"
        "Available Tools:\n" +
        "\n".join(
            f"- {k}: {v['name']} — {v['description']}"
            for k, v in AVAILABLE_TOOLS.items()
        )
    )

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        model_name: str = "extraction_model",
        agent_metadata: Optional[Dict[str, Any]] = None,
    ):
        self.llm_client = llm_client
        self.model_name = model_name
        self.agent_metadata = agent_metadata or {}

    async def _call_llm_structured(
        self, prompt: str, output_format, system_prompt: str = None, max_retries: int = 2
    ):
        """Call LLM with structured output parsing and retry on validation failure."""
        if self.llm_client is None:
            raise RuntimeError(
                "LLM client not initialized. Set AZURE_OPENAI_ENDPOINT, "
                "AZURE_OPENAI_API_KEY, and AZURE_OPENAI_CHAT_DEPLOYMENT_NAME env vars."
            )

        schema_json = json.dumps(output_format.model_json_schema(), indent=2)
        augmented_prompt = (
            f"{prompt}\n\n"
            f"IMPORTANT: Respond ONLY with a JSON object containing actual generated data "
            f"(not the schema definition). The JSON must conform to this schema:\n"
            f"{schema_json}\n\n"
            f"Return the JSON object directly with populated values. "
            f"Do NOT return the schema itself or wrap it in a 'properties' key."
        )

        last_error = None
        for attempt in range(max_retries + 1):
            response, cost = await self.llm_client.with_structured_output(
                model_name=self.model_name,
                messages=augmented_prompt,
                output_format=output_format,
                system_prompt=system_prompt or self.SYSTEM_PROMPT,
            )
            logger.info(f"LLM call cost (attempt {attempt + 1}): {cost}")

            if isinstance(response, output_format):
                return response

            if isinstance(response, dict):
                raw = response.get("response", response)
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw.strip().strip("`").removeprefix("json").strip())
                    except (json.JSONDecodeError, ValueError):
                        last_error = f"Could not parse raw text as JSON on attempt {attempt + 1}"
                        logger.warning(last_error)
                        continue

                if isinstance(raw, dict):
                    try:
                        return output_format.model_validate(raw)
                    except Exception as e:
                        last_error = f"Pydantic validation failed on attempt {attempt + 1}: {e}"
                        logger.warning(last_error)
                        continue

        raise RuntimeError(
            f"Failed to get valid structured output after {max_retries + 1} attempts. "
            f"Last error: {last_error}"
        )

    # --- OTEL span helpers ---

    @staticmethod
    def _make_span_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _ts(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    @staticmethod
    def _make_trace_id() -> str:
        return "".join(random.choices(string.hexdigits[:16], k=32))

    @staticmethod
    def generate_experiment_id(faults: List[FaultDefinition]) -> str:
        """Generate a deterministic experiment_id from the sorted fault names."""
        key = "|".join(sorted(f.name for f in faults))
        return hashlib.sha256(key.encode()).hexdigest()[:24]

    def _build_span(
        self,
        *,
        trace_id: str,
        parent_span_id: Optional[str],
        span_type: str,
        name: str,
        start_time: datetime,
        end_time: Optional[datetime],
        input_data: Dict[str, Any],
        output_data: Any,
        metadata: Dict[str, Any],
        experiment_id: str = "",
        run_id: str = "",
    ) -> Dict[str, Any]:
        span_id = self._make_span_id()
        short_id = span_id[:8]
        metadata["experiment_id"] = experiment_id
        metadata["run_id"] = run_id
        span = {
            "id": span_id,
            "traceId": trace_id,
            "type": span_type,
            "name": f"{name} ({short_id})",
            "startTime": self._ts(start_time),
            "endTime": self._ts(end_time) if end_time else None,
            "depth": 1 if parent_span_id else 0,
            "parentObservationId": parent_span_id,
            "input": json.dumps(input_data),
            "output": json.dumps(output_data) if isinstance(output_data, dict) else output_data,
            "metadata": json.dumps(metadata),
        }
        return span

    def _build_tool_span(
        self,
        *,
        trace_id: str,
        parent_span_id: Optional[str],
        tool_call: ToolCallDetail,
        start_time: datetime,
        duration_seconds: float,
        agent_id: str,
        experiment_id: str = "",
        run_id: str = "",
    ) -> List[Dict[str, Any]]:
        """Build a tool_call SPAN + a reasoning GENERATION span for one tool invocation."""
        end_time = start_time + timedelta(seconds=duration_seconds)
        reasoning_start = end_time + timedelta(seconds=random.uniform(0.2, 1.0))
        reasoning_end = reasoning_start + timedelta(seconds=random.uniform(0.5, 2.0))

        tool_info = AVAILABLE_TOOLS.get(tool_call.tool_key, {})
        tokens = random.randint(200, 600) if tool_call.agent_reasoning else 0

        tool_span = self._build_span(
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            span_type="SPAN",
            name=f"tool_call:{tool_call.tool_key}",
            start_time=start_time,
            end_time=end_time,
            input_data={
                "tool_key": tool_call.tool_key,
                "tool_name": tool_call.tool_name,
                "category": tool_info.get("category", "unknown"),
                "parameters": tool_call.input_params,
                "agent_id": agent_id,
                "timestamp": self._ts(start_time),
            },
            output_data=tool_call.raw_output,
            metadata={
                "action": "tool_call",
                "method": tool_call.tool_key,
                "tool_name": tool_call.tool_name,
                "tool_category": tool_info.get("category", "unknown"),
                "timestamp": self._ts(start_time),
                "duration_seconds": round(duration_seconds, 3),
                "anomalies_found": tool_call.anomalies_found,
                "llm_used": False,
                "tokens_consumed": 0,
                "confidence_score": None,
            },
            experiment_id=experiment_id,
            run_id=run_id,
        )

        reasoning_span = self._build_span(
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            span_type="GENERATION",
            name=f"tool_reasoning:{tool_call.tool_key}",
            start_time=reasoning_start,
            end_time=reasoning_end,
            input_data={
                "tool_key": tool_call.tool_key,
                "tool_output_summary": tool_call.raw_output[:500],
                "anomalies_found": tool_call.anomalies_found,
                "agent_id": agent_id,
                "timestamp": self._ts(reasoning_start),
            },
            output_data=tool_call.agent_reasoning,
            metadata={
                "action": "tool_reasoning",
                "llm_used": True,
                "tokens_consumed": tokens,
                "confidence_score": None,
            },
            experiment_id=experiment_id,
            run_id=run_id,
        )

        return [tool_span, reasoning_span]

    def _build_agent_onboarding_span(
        self,
        *,
        trace_id: str,
        agent_id: str,
        start_time: datetime,
        faults: List[FaultDefinition],
        experiment_id: str = "",
        run_id: str = "",
    ) -> Dict[str, Any]:
        """Build the agent_onboarding span — always the first span in a trace."""
        agent_name = self.agent_metadata.get("agent_name", "ITOps Autonomous Agent")
        agent_version = self.agent_metadata.get("agent_version", "1.0.0")
        agent_description = self.agent_metadata.get(
            "agent_description",
            "Autonomous Kubernetes ITOps agent that detects, triages, and "
            "remediates infrastructure faults using Kubernetes API and Prometheus tools.",
        )
        capabilities = self.agent_metadata.get("agent_capabilities", [
            "fault_detection",
            "fault_triage",
            "fault_investigation",
            "fault_remediation",
            "post_remediation_verification",
            "cluster_health_monitoring",
        ])
        supported_tool_categories = self.agent_metadata.get(
            "supported_tool_categories", ["kubernetes", "prometheus"]
        )

        available_tool_names = {
            k: v["name"] for k, v in AVAILABLE_TOOLS.items()
        }

        end_time = start_time + timedelta(seconds=random.uniform(0.5, 1.5))

        return self._build_span(
            trace_id=trace_id,
            parent_span_id=None,
            span_type="SPAN",
            name="agent_onboarding",
            start_time=start_time,
            end_time=end_time,
            input_data={
                "agent_id": agent_id,
                "agent_name": agent_name,
                "agent_version": agent_version,
                "agent_description": agent_description,
                "capabilities": capabilities,
                "supported_tool_categories": supported_tool_categories,
                "available_tools": available_tool_names,
                "num_tools": len(available_tool_names),
                "scenario_type": "multi_fault",
                "num_faults": len(faults),
                "faults": [
                    {"name": f.name, "description": f.description} for f in faults
                ],
                "onboarded_at": self._ts(start_time),
            },
            output_data={
                "status": "onboarded",
                "message": f"Agent '{agent_name}' (v{agent_version}) initialized "
                           f"with {len(available_tool_names)} tools for "
                           f"{len(faults)}-fault scenario.",
            },
            metadata={
                "action": "agent_onboarding",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "agent_version": agent_version,
                "agent_description": agent_description,
                "capabilities": capabilities,
                "supported_tool_categories": supported_tool_categories,
                "num_tools_available": len(available_tool_names),
                "llm_used": False,
                "tokens_consumed": 0,
                "confidence_score": None,
                **{k: v for k, v in self.agent_metadata.items()
                   if k not in ("agent_name", "agent_version", "agent_description",
                                "agent_capabilities", "supported_tool_categories")},
            },
            experiment_id=experiment_id,
            run_id=run_id,
        )

    def _build_fault_injection_spans(
        self,
        *,
        trace_id: str,
        faults: List[FaultDefinition],
        scenario: MultiFaultScenario,
        start_time: datetime,
        experiment_id: str = "",
        run_id: str = "",
    ) -> List[Dict[str, Any]]:
        """Build FAULT_DATA spans for each injected fault.

        These spans carry the ground-truth metadata that the downstream
        fault-bucketing pipeline uses for evaluation.  One span per fault,
        emitted right after agent onboarding.
        """
        spans: List[Dict[str, Any]] = []
        fault_desc_map = {f.name: f.description for f in faults}
        current = start_time

        for fd in scenario.fault_scenarios:
            current += timedelta(seconds=random.uniform(0.1, 0.5))
            end = current + timedelta(seconds=random.uniform(0.1, 0.3))

            ground_truth = {
                "fault_name": fd.fault_name,
                "fault_description": fault_desc_map.get(fd.fault_name, fd.fault_name),
                "goal": f"Detect and remediate {fd.fault_name}",
                "remediation": ", ".join(fd.remediation_actions),
                "severity": fd.severity,
                "target_pod": fd.target_pod_prefix,
                "namespace": fd.target_namespace,
                "symptoms": fd.symptoms,
                "detection_signals": fd.detection_signals,
            }

            input_data = {
                "ground_truth": ground_truth,
                "ideal_course_of_action": fd.remediation_actions,
                "ideal_tool_usage_trajectory": fd.remediation_tools,
            }

            span = self._build_span(
                trace_id=trace_id,
                parent_span_id=None,
                span_type="FAULT_DATA",
                name=fd.fault_name,
                start_time=current,
                end_time=end,
                input_data=input_data,
                output_data={"status": "injected"},
                metadata={
                    "action": "fault_injection",
                    "fault_name": fd.fault_name,
                    "severity": fd.severity,
                    "target_pod": fd.target_pod_prefix,
                    "namespace": fd.target_namespace,
                    "llm_used": False,
                    "tokens_consumed": 0,
                    "confidence_score": None,
                },
                experiment_id=experiment_id,
                run_id=run_id,
            )
            spans.append(span)
            current = end

        return spans

    # --- LLM generation methods ---

    async def generate_multi_fault_scenario(
        self, faults: List[FaultDefinition]
    ) -> MultiFaultScenario:
        fault_list = "\n".join(
            f"  {i+1}. {f.name}: {f.description}" for i, f in enumerate(faults)
        )
        available_tool_keys = ", ".join(AVAILABLE_TOOLS.keys())
        prompt = (
            f"Generate a detailed multi-fault Kubernetes scenario where ALL of the "
            f"following faults are injected simultaneously into a cluster:\n\n"
            f"{fault_list}\n\n"
            f"For each fault, provide realistic pod names, symptoms, detection signals, "
            f"log excerpts, resource metrics, and remediation steps. "
            f"The remediation_tools for each fault must use tool keys from this list: "
            f"{available_tool_keys}\n\n"
            f"Also describe how the faults interact with each other (cross-fault impacts) "
            f"and provide a recommended triage order based on severity and dependencies."
        )
        return await self._call_llm_structured(prompt, MultiFaultScenario)

    async def generate_cluster_scan(
        self, scenario: MultiFaultScenario, faults: List[FaultDefinition]
    ) -> ClusterScanResult:
        fault_details = "\n".join(
            f"  - {fs.fault_name}: pod={fs.target_pod_prefix}, ns={fs.target_namespace}, "
            f"symptoms={fs.symptoms}"
            for fs in scenario.fault_scenarios
        )
        prompt = (
            f"Generate realistic cluster health scan output for a Kubernetes cluster "
            f"'{scenario.cluster_name}' that has these {len(faults)} active faults:\n\n"
            f"{fault_details}\n\n"
            f"The agent runs these tools for the initial scan:\n"
            f"  1. Pods: List (all namespaces) — show pods in various states\n"
            f"  2. Events: List — show warning events from the faults\n"
            f"  3. Nodes: Top — show node resource usage\n\n"
            f"The outputs should clearly show symptoms of ALL the injected faults "
            f"but interspersed with normal healthy pods/events. The agent's reasoning "
            f"should note multiple anomalies across different namespaces/pods."
        )
        return await self._call_llm_structured(prompt, ClusterScanResult)

    async def generate_triage_decision(
        self, scenario: MultiFaultScenario
    ) -> TriageDecision:
        fault_summary = "\n".join(
            f"  - {fs.fault_name} (severity={fs.severity}): {', '.join(fs.symptoms[:2])}"
            for fs in scenario.fault_scenarios
        )
        prompt = (
            f"The ITOps agent has detected {len(scenario.fault_scenarios)} simultaneous faults "
            f"in cluster '{scenario.cluster_name}':\n\n"
            f"{fault_summary}\n\n"
            f"Cross-fault interactions:\n"
            f"{chr(10).join('  - ' + i for i in scenario.cross_fault_interactions)}\n\n"
            f"Generate the agent's triage reasoning. The agent must decide which fault "
            f"to remediate first based on severity, dependencies, and cross-fault impact. "
            f"Explain the reasoning in detail."
        )
        return await self._call_llm_structured(prompt, TriageDecision)

    async def generate_fault_investigation(
        self, fault_detail: SingleFaultDetail, scenario: MultiFaultScenario
    ) -> FaultInvestigationResult:
        available_tool_keys = ", ".join(AVAILABLE_TOOLS.keys())
        prompt = (
            f"Generate the investigation phase for an ITOps agent diagnosing this fault:\n\n"
            f"Fault: {fault_detail.fault_name}\n"
            f"Target Pod: {fault_detail.target_pod_prefix}\n"
            f"Namespace: {fault_detail.target_namespace}\n"
            f"Symptoms: {', '.join(fault_detail.symptoms)}\n"
            f"Detection Signals: {', '.join(fault_detail.detection_signals)}\n\n"
            f"Context: This is one of {len(scenario.fault_scenarios)} concurrent faults. "
            f"Other active faults: "
            f"{', '.join(fs.fault_name for fs in scenario.fault_scenarios if fs.fault_name != fault_detail.fault_name)}\n\n"
            f"Generate 3-5 tool calls the agent makes to investigate. Each tool_call must use "
            f"a tool_key from: {available_tool_keys}\n"
            f"Include realistic raw_output for each tool (multi-line terminal output showing "
            f"the fault symptoms). The agent should progressively build confidence in the diagnosis."
        )
        return await self._call_llm_structured(prompt, FaultInvestigationResult)

    async def generate_fault_remediation(
        self, fault_detail: SingleFaultDetail, investigation: FaultInvestigationResult
    ) -> RemediationResult:
        available_tool_keys = ", ".join(AVAILABLE_TOOLS.keys())
        prompt = (
            f"Generate remediation execution for this fault:\n\n"
            f"Fault: {fault_detail.fault_name}\n"
            f"Diagnosis: {investigation.diagnosis}\n"
            f"Root Cause: {investigation.root_cause}\n"
            f"Target Pod: {fault_detail.target_pod_prefix}\n"
            f"Namespace: {fault_detail.target_namespace}\n"
            f"Available Remediation Tools: {', '.join(fault_detail.remediation_tools)}\n"
            f"Planned Actions: {', '.join(fault_detail.remediation_actions)}\n\n"
            f"Generate 1-3 tool calls the agent makes to remediate. Each tool_call must use "
            f"a tool_key from: {available_tool_keys}\n"
            f"Include realistic output showing the remediation being applied."
        )
        return await self._call_llm_structured(prompt, RemediationResult)

    async def generate_post_remediation_check(
        self, fault_detail: SingleFaultDetail
    ) -> PostRemediationCheck:
        available_tool_keys = ", ".join(AVAILABLE_TOOLS.keys())
        prompt = (
            f"Generate post-remediation verification for this fault:\n\n"
            f"Fault: {fault_detail.fault_name} (just remediated)\n"
            f"Target Pod: {fault_detail.target_pod_prefix}\n"
            f"Namespace: {fault_detail.target_namespace}\n\n"
            f"Generate 1-2 tool calls to verify the fault is resolved. Each tool_call must use "
            f"a tool_key from: {available_tool_keys}\n"
            f"The output should show the pod/service returning to healthy state."
        )
        return await self._call_llm_structured(prompt, PostRemediationCheck)

    async def generate_final_stability_check(
        self, scenario: MultiFaultScenario, faults: List[FaultDefinition]
    ) -> FinalStabilityCheck:
        available_tool_keys = ", ".join(AVAILABLE_TOOLS.keys())
        prompt = (
            f"Generate a final cross-fault stability check. All {len(faults)} faults have "
            f"been remediated in cluster '{scenario.cluster_name}':\n\n"
            f"Faults remediated: {', '.join(f.name for f in faults)}\n\n"
            f"Generate 2-3 tool calls for a comprehensive cluster-wide health check. "
            f"Each tool_call must use a tool_key from: {available_tool_keys}\n"
            f"The outputs should confirm all pods are healthy, no warning events, "
            f"and metrics are within normal ranges. Provide recommendations for prevention."
        )
        return await self._call_llm_structured(prompt, FinalStabilityCheck)

    # --- Core trace assembly ---

    async def generate_trace(
        self,
        faults: List[FaultDefinition],
        num_detection_cycles: int = 3,
        agent_id: str = "",
        experiment_id: str = "",
        run_id: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Generate a complete OTEL-compliant trace for a multi-fault scenario.

        Trace lifecycle:
          Phase 0a: Agent onboarding (agent metadata + initialization)
          Phase 0b: Cluster health scan (Pods: List, Events: List, Nodes: Top)
          Phase 1: Multi-fault detection (fault_detected spans for each fault)
          Phase 2: Triage & prioritization (reasoning GENERATION)
          Phase 3: Per-fault loop: investigate -> remediate -> verify -> confirm
          Phase 4: Final cross-fault stability check
        """
        logger.info(f"Generating multi-fault trace for {len(faults)} faults: "
                     f"{', '.join(f.name for f in faults)}")

        trace_id = self._make_trace_id()
        if not agent_id:
            agent_id = str(uuid.uuid4())
        if not experiment_id:
            experiment_id = self.generate_experiment_id(faults)
        if not run_id:
            run_id = str(uuid.uuid4())
        base_time = datetime.now(timezone.utc)
        current_time = base_time
        spans: List[Dict[str, Any]] = []

        # ── Phase 0a: Agent onboarding ──
        logger.info("Phase 0a: Generating agent onboarding span...")
        onboarding_span = self._build_agent_onboarding_span(
            trace_id=trace_id,
            agent_id=agent_id,
            start_time=current_time,
            faults=faults,
            experiment_id=experiment_id,
            run_id=run_id,
        )
        spans.append(onboarding_span)
        current_time += timedelta(seconds=random.uniform(1.5, 3.0))

        # ── Step 1: Generate scenario via LLM ──
        scenario = await self.generate_multi_fault_scenario(faults)
        logger.info(f"Scenario generated: cluster={scenario.cluster_name}, "
                     f"severity={scenario.overall_severity}")

        # Build fault lookup by name
        fault_detail_map = {fs.fault_name: fs for fs in scenario.fault_scenarios}

        # ── Phase 0a-ii: Fault injection (FAULT_DATA) spans ──
        logger.info("Phase 0a-ii: Generating fault injection (FAULT_DATA) spans...")
        fault_data_spans = self._build_fault_injection_spans(
            trace_id=trace_id,
            faults=faults,
            scenario=scenario,
            start_time=current_time,
            experiment_id=experiment_id,
            run_id=run_id,
        )
        spans.extend(fault_data_spans)
        if fault_data_spans:
            # Advance current_time past the last FAULT_DATA span
            last_end = fault_data_spans[-1].get("endTime")
            if last_end:
                current_time = datetime.strptime(
                    last_end, "%Y-%m-%dT%H:%M:%S.%fZ"
                ).replace(tzinfo=timezone.utc)
            current_time += timedelta(seconds=random.uniform(0.5, 1.5))

        # ── Phase 0b: Cluster health scan ──
        logger.info("Phase 0b: Generating cluster health scan...")
        scan = await self.generate_cluster_scan(scenario, faults)

        # Tool call: Pods: List
        scan_start = current_time
        current_time += timedelta(seconds=random.uniform(2.0, 5.0))

        pods_list_span = self._build_span(
            trace_id=trace_id,
            parent_span_id=None,
            span_type="SPAN",
            name="tool_call:k8s_pods_list",
            start_time=scan_start,
            end_time=scan_start + timedelta(seconds=random.uniform(1.0, 3.0)),
            input_data={
                "tool_key": "k8s_pods_list",
                "tool_name": "Pods: List",
                "parameters": {"all_namespaces": True},
                "agent_id": agent_id,
                "timestamp": self._ts(scan_start),
            },
            output_data=scan.pods_list_output,
            metadata={
                "action": "tool_call",
                "method": "k8s_pods_list",
                "tool_name": "Pods: List",
                "tool_category": "kubernetes",
                "llm_used": False,
                "tokens_consumed": 0,
                "confidence_score": None,
            },
            experiment_id=experiment_id,
            run_id=run_id,
        )
        spans.append(pods_list_span)

        # Tool call: Events: List
        current_time += timedelta(seconds=random.uniform(1.0, 2.0))
        events_span = self._build_span(
            trace_id=trace_id,
            parent_span_id=None,
            span_type="SPAN",
            name="tool_call:k8s_events_list",
            start_time=current_time,
            end_time=current_time + timedelta(seconds=random.uniform(1.0, 3.0)),
            input_data={
                "tool_key": "k8s_events_list",
                "tool_name": "Events: List",
                "parameters": {"all_namespaces": True},
                "agent_id": agent_id,
                "timestamp": self._ts(current_time),
            },
            output_data=scan.events_output,
            metadata={
                "action": "tool_call",
                "method": "k8s_events_list",
                "tool_name": "Events: List",
                "tool_category": "kubernetes",
                "llm_used": False,
                "tokens_consumed": 0,
                "confidence_score": None,
            },
            experiment_id=experiment_id,
            run_id=run_id,
        )
        spans.append(events_span)

        # Tool call: Nodes: Top
        current_time += timedelta(seconds=random.uniform(1.0, 2.0))
        nodes_span = self._build_span(
            trace_id=trace_id,
            parent_span_id=None,
            span_type="SPAN",
            name="tool_call:k8s_nodes_top",
            start_time=current_time,
            end_time=current_time + timedelta(seconds=random.uniform(1.0, 2.0)),
            input_data={
                "tool_key": "k8s_nodes_top",
                "tool_name": "Nodes: Top",
                "parameters": {},
                "agent_id": agent_id,
                "timestamp": self._ts(current_time),
            },
            output_data=scan.nodes_top_output,
            metadata={
                "action": "tool_call",
                "method": "k8s_nodes_top",
                "tool_name": "Nodes: Top",
                "tool_category": "kubernetes",
                "llm_used": False,
                "tokens_consumed": 0,
                "confidence_score": None,
            },
            experiment_id=experiment_id,
            run_id=run_id,
        )
        spans.append(nodes_span)

        # Reasoning span: initial scan analysis
        current_time += timedelta(seconds=random.uniform(1.0, 3.0))
        scan_reasoning_span = self._build_span(
            trace_id=trace_id,
            parent_span_id=None,
            span_type="GENERATION",
            name="cluster_scan_reasoning",
            start_time=current_time,
            end_time=current_time + timedelta(seconds=random.uniform(1.0, 2.0)),
            input_data={
                "anomalies_detected": scan.initial_anomalies,
                "num_faults_suspected": len(faults),
                "agent_id": agent_id,
                "timestamp": self._ts(current_time),
            },
            output_data=scan.agent_reasoning,
            metadata={
                "action": "cluster_scan_reasoning",
                "llm_used": True,
                "tokens_consumed": random.randint(500, 900),
                "confidence_score": None,
            },
            experiment_id=experiment_id,
            run_id=run_id,
        )
        spans.append(scan_reasoning_span)

        # ── Phase 1: Multi-fault detection ──
        logger.info("Phase 1: Generating fault detection spans...")
        current_time += timedelta(seconds=random.uniform(2.0, 5.0))

        for fd in scenario.fault_scenarios:
            current_time += timedelta(seconds=random.uniform(1.0, 4.0))
            detection_span = self._build_span(
                trace_id=trace_id,
                parent_span_id=None,
                span_type="SPAN",
                name="fault_detected",
                start_time=current_time,
                end_time=current_time + timedelta(seconds=random.uniform(0.1, 0.5)),
                input_data={
                    "fault_name": fd.fault_name,
                    "pod": fd.target_pod_prefix,
                    "namespace": fd.target_namespace,
                    "severity": fd.severity,
                    "detection_signals": fd.detection_signals,
                    "message": f"Fault detected: {fd.fault_name} on {fd.target_pod_prefix}",
                    "agent_id": agent_id,
                    "detected_at": self._ts(current_time),
                },
                output_data={"status": "logged"},
                metadata={
                    "action": "fault_detected",
                    "method": "multi_fault_scan",
                    "fault_name": fd.fault_name,
                    "severity": fd.severity,
                    "timestamp": self._ts(current_time),
                    "details": {
                        "pod": fd.target_pod_prefix,
                        "namespace": fd.target_namespace,
                        "symptoms": fd.symptoms,
                        "detection_signals": fd.detection_signals,
                    },
                    "llm_used": False,
                    "tokens_consumed": 0,
                    "confidence_score": None,
                },
                experiment_id=experiment_id,
                run_id=run_id,
            )
            spans.append(detection_span)

        # ── Phase 2: Triage & prioritization ──
        logger.info("Phase 2: Generating triage decision...")
        triage = await self.generate_triage_decision(scenario)
        current_time += timedelta(seconds=random.uniform(3.0, 6.0))

        triage_span = self._build_span(
            trace_id=trace_id,
            parent_span_id=None,
            span_type="GENERATION",
            name="triage_reasoning",
            start_time=current_time,
            end_time=current_time + timedelta(seconds=random.uniform(2.0, 4.0)),
            input_data={
                "num_faults": len(scenario.fault_scenarios),
                "faults": [
                    {"name": fp.fault_name, "severity": fp.severity, "priority": fp.priority}
                    for fp in triage.prioritized_faults
                ],
                "cross_fault_interactions": scenario.cross_fault_interactions,
                "agent_id": agent_id,
                "timestamp": self._ts(current_time),
            },
            output_data=triage.reasoning_text,
            metadata={
                "action": "triage_reasoning",
                "llm_used": True,
                "tokens_consumed": random.randint(800, 1500),
                "triage_order": [fp.fault_name for fp in triage.prioritized_faults],
                "risk_assessment": triage.risk_assessment,
                "estimated_total_remediation_seconds": triage.estimated_total_remediation_seconds,
                "confidence_score": None,
            },
            experiment_id=experiment_id,
            run_id=run_id,
        )
        spans.append(triage_span)

        # ── Phase 3: Per-fault remediation loop (in triage priority order) ──
        remediation_order = [fp.fault_name for fp in triage.prioritized_faults]
        logger.info(f"Phase 3: Remediating faults in order: {remediation_order}")

        for fault_name in remediation_order:
            fd = fault_detail_map.get(fault_name)
            if fd is None:
                logger.warning(f"Fault '{fault_name}' from triage not found in scenario details, skipping")
                continue

            logger.info(f"  Processing fault: {fd.fault_name}")

            # --- 3a: Investigation ---
            investigation = await self.generate_fault_investigation(fd, scenario)
            current_time += timedelta(seconds=random.uniform(2.0, 5.0))

            invest_start = current_time
            invest_parent_span = self._build_span(
                trace_id=trace_id,
                parent_span_id=None,
                span_type="SPAN",
                name=f"investigate:{fd.fault_name}",
                start_time=invest_start,
                end_time=None,
                input_data={
                    "fault_name": fd.fault_name,
                    "pod": fd.target_pod_prefix,
                    "namespace": fd.target_namespace,
                    "agent_id": agent_id,
                    "timestamp": self._ts(current_time),
                },
                output_data={"diagnosis": investigation.diagnosis, "root_cause": investigation.root_cause},
                metadata={
                    "action": "investigate",
                    "fault_name": fd.fault_name,
                    "llm_used": True,
                    "tokens_consumed": 0,
                    "confidence_score": investigation.confidence_score,
                },
                experiment_id=experiment_id,
                run_id=run_id,
            )
            invest_parent_id = invest_parent_span["id"]

            for tc in investigation.tool_calls:
                current_time += timedelta(seconds=random.uniform(1.5, 4.0))
                duration = random.uniform(1.0, 3.0)
                tc_spans = self._build_tool_span(
                    trace_id=trace_id,
                    parent_span_id=invest_parent_id,
                    tool_call=tc,
                    start_time=current_time,
                    duration_seconds=duration,
                    agent_id=agent_id,
                    experiment_id=experiment_id,
                    run_id=run_id,
                )
                spans.extend(tc_spans)
                current_time += timedelta(seconds=duration + random.uniform(0.5, 1.5))

            invest_parent_span["endTime"] = self._ts(current_time)
            spans.append(invest_parent_span)

            # Investigation diagnosis reasoning
            current_time += timedelta(seconds=random.uniform(1.0, 2.0))
            diagnosis_span = self._build_span(
                trace_id=trace_id,
                parent_span_id=None,
                span_type="GENERATION",
                name=f"diagnosis_reasoning:{fd.fault_name}",
                start_time=current_time,
                end_time=current_time + timedelta(seconds=random.uniform(1.0, 2.0)),
                input_data={
                    "fault_name": fd.fault_name,
                    "investigation_summary": investigation.diagnosis,
                    "root_cause": investigation.root_cause,
                    "agent_id": agent_id,
                    "timestamp": self._ts(current_time),
                },
                output_data=investigation.diagnosis,
                metadata={
                    "action": "diagnosis_reasoning",
                    "fault_name": fd.fault_name,
                    "llm_used": True,
                    "tokens_consumed": random.randint(400, 800),
                    "confidence_score": investigation.confidence_score,
                },
                experiment_id=experiment_id,
                run_id=run_id,
            )
            spans.append(diagnosis_span)

            # --- 3b: Remediation ---
            remediation = await self.generate_fault_remediation(fd, investigation)
            current_time += timedelta(seconds=random.uniform(1.0, 3.0))

            remediate_start = current_time
            remediate_parent_span = self._build_span(
                trace_id=trace_id,
                parent_span_id=None,
                span_type="SPAN",
                name=f"remediate:{fd.fault_name}",
                start_time=remediate_start,
                end_time=None,
                input_data={
                    "fault_name": fd.fault_name,
                    "pod": fd.target_pod_prefix,
                    "namespace": fd.target_namespace,
                    "action_summary": remediation.action_summary,
                    "agent_id": agent_id,
                    "timestamp": self._ts(current_time),
                },
                output_data={
                    "success": remediation.success,
                    "recovery_time_seconds": round(remediation.recovery_time_seconds, 3),
                },
                metadata={
                    "action": "remediate",
                    "fault_name": fd.fault_name,
                    "llm_used": True,
                    "tokens_consumed": 0,
                    "confidence_score": remediation.confidence_score,
                },
                experiment_id=experiment_id,
                run_id=run_id,
            )
            remediate_parent_id = remediate_parent_span["id"]

            for tc in remediation.tool_calls:
                current_time += timedelta(seconds=random.uniform(1.5, 4.0))
                duration = random.uniform(1.0, 5.0)
                tc_spans = self._build_tool_span(
                    trace_id=trace_id,
                    parent_span_id=remediate_parent_id,
                    tool_call=tc,
                    start_time=current_time,
                    duration_seconds=duration,
                    agent_id=agent_id,
                    experiment_id=experiment_id,
                    run_id=run_id,
                )
                spans.extend(tc_spans)
                current_time += timedelta(seconds=duration + random.uniform(0.5, 1.5))

            current_time += timedelta(seconds=remediation.recovery_time_seconds)
            remediate_parent_span["endTime"] = self._ts(current_time)
            spans.append(remediate_parent_span)

            # --- 3c: Post-remediation verification ---
            post_check = await self.generate_post_remediation_check(fd)
            current_time += timedelta(seconds=random.uniform(2.0, 5.0))

            verify_start = current_time
            verify_parent_span = self._build_span(
                trace_id=trace_id,
                parent_span_id=None,
                span_type="SPAN",
                name=f"verify:{fd.fault_name}",
                start_time=verify_start,
                end_time=None,
                input_data={
                    "fault_name": fd.fault_name,
                    "pod": fd.target_pod_prefix,
                    "namespace": fd.target_namespace,
                    "agent_id": agent_id,
                    "timestamp": self._ts(current_time),
                },
                output_data={
                    "fault_resolved": post_check.fault_resolved,
                    "system_stable": post_check.system_stable,
                },
                metadata={
                    "action": "verify",
                    "method": "post_remediation_verification",
                    "fault_name": fd.fault_name,
                    "llm_used": True,
                    "tokens_consumed": 0,
                    "confidence_score": post_check.confidence_score,
                },
                experiment_id=experiment_id,
                run_id=run_id,
            )
            verify_parent_id = verify_parent_span["id"]

            for tc in post_check.tool_calls:
                current_time += timedelta(seconds=random.uniform(1.0, 3.0))
                duration = random.uniform(1.0, 2.5)
                tc_spans = self._build_tool_span(
                    trace_id=trace_id,
                    parent_span_id=verify_parent_id,
                    tool_call=tc,
                    start_time=current_time,
                    duration_seconds=duration,
                    agent_id=agent_id,
                    experiment_id=experiment_id,
                    run_id=run_id,
                )
                spans.extend(tc_spans)
                current_time += timedelta(seconds=duration + random.uniform(0.3, 1.0))

            verify_parent_span["endTime"] = self._ts(current_time)
            spans.append(verify_parent_span)

            # Verify reasoning
            current_time += timedelta(seconds=random.uniform(0.5, 1.5))
            verify_reasoning_span = self._build_span(
                trace_id=trace_id,
                parent_span_id=None,
                span_type="GENERATION",
                name=f"verify_reasoning:{fd.fault_name}",
                start_time=current_time,
                end_time=current_time + timedelta(seconds=random.uniform(1.0, 2.0)),
                input_data={
                    "fault_name": fd.fault_name,
                    "fault_resolved": post_check.fault_resolved,
                    "system_stable": post_check.system_stable,
                    "agent_id": agent_id,
                    "timestamp": self._ts(current_time),
                },
                output_data=post_check.reasoning_text,
                metadata={
                    "action": "verify_reasoning",
                    "fault_name": fd.fault_name,
                    "llm_used": True,
                    "tokens_consumed": random.randint(400, 700),
                    "confidence_score": post_check.confidence_score,
                },
                experiment_id=experiment_id,
                run_id=run_id,
            )
            spans.append(verify_reasoning_span)

            # confirm span
            current_time += timedelta(seconds=random.uniform(0.5, 1.5))
            confirm_span = self._build_span(
                trace_id=trace_id,
                parent_span_id=None,
                span_type="SPAN",
                name=f"confirm:{fd.fault_name}",
                start_time=current_time,
                end_time=current_time + timedelta(seconds=random.uniform(0.1, 0.5)),
                input_data={
                    "fault_name": fd.fault_name,
                    "pod": fd.target_pod_prefix,
                    "message": f"Fault '{fd.fault_name}' remediated and confirmed stable",
                    "fault_resolved": post_check.fault_resolved,
                    "system_stable": post_check.system_stable,
                    "agent_id": agent_id,
                    "detected_at": self._ts(current_time),
                },
                output_data={"status": "logged"},
                metadata={
                    "action": "confirm",
                    "method": "remediation_confirmation",
                    "fault_name": fd.fault_name,
                    "llm_used": False,
                    "tokens_consumed": 0,
                    "confidence_score": post_check.confidence_score,
                },
                experiment_id=experiment_id,
                run_id=run_id,
            )
            spans.append(confirm_span)

        # ── Phase 4: Final cross-fault stability check ──
        logger.info("Phase 4: Generating final stability check...")
        final_check = await self.generate_final_stability_check(scenario, faults)
        current_time += timedelta(seconds=random.uniform(5.0, 10.0))

        final_start = current_time
        final_parent_span = self._build_span(
            trace_id=trace_id,
            parent_span_id=None,
            span_type="SPAN",
            name="final_stability_check",
            start_time=final_start,
            end_time=None,
            input_data={
                "num_faults_remediated": len(faults),
                "faults": [f.name for f in faults],
                "agent_id": agent_id,
                "timestamp": self._ts(current_time),
            },
            output_data={
                "all_faults_resolved": final_check.all_faults_resolved,
                "cluster_health": final_check.cluster_health,
            },
            metadata={
                "action": "final_stability_check",
                "llm_used": True,
                "tokens_consumed": 0,
                "confidence_score": final_check.confidence_score,
            },
            experiment_id=experiment_id,
            run_id=run_id,
        )
        final_parent_id = final_parent_span["id"]

        for tc in final_check.tool_calls:
            current_time += timedelta(seconds=random.uniform(1.0, 3.0))
            duration = random.uniform(1.0, 3.0)
            tc_spans = self._build_tool_span(
                trace_id=trace_id,
                parent_span_id=final_parent_id,
                tool_call=tc,
                start_time=current_time,
                duration_seconds=duration,
                agent_id=agent_id,
                experiment_id=experiment_id,
                run_id=run_id,
            )
            spans.extend(tc_spans)
            current_time += timedelta(seconds=duration + random.uniform(0.5, 1.0))

        final_parent_span["endTime"] = self._ts(current_time)
        spans.append(final_parent_span)

        # Final stability reasoning
        current_time += timedelta(seconds=random.uniform(1.0, 2.0))
        final_reasoning_span = self._build_span(
            trace_id=trace_id,
            parent_span_id=None,
            span_type="GENERATION",
            name="final_stability_reasoning",
            start_time=current_time,
            end_time=current_time + timedelta(seconds=random.uniform(2.0, 4.0)),
            input_data={
                "all_faults_resolved": final_check.all_faults_resolved,
                "cluster_health": final_check.cluster_health,
                "recommendations": final_check.recommendations,
                "agent_id": agent_id,
                "timestamp": self._ts(current_time),
            },
            output_data=final_check.reasoning_text,
            metadata={
                "action": "final_stability_reasoning",
                "llm_used": True,
                "tokens_consumed": random.randint(600, 1200),
                "confidence_score": final_check.confidence_score,
                "recommendations": final_check.recommendations,
            },
            experiment_id=experiment_id,
            run_id=run_id,
        )
        spans.append(final_reasoning_span)

        # Final success_confirmed
        current_time += timedelta(seconds=random.uniform(0.5, 2.0))
        experiment_duration = (current_time - base_time).total_seconds()
        success_span = self._build_span(
            trace_id=trace_id,
            parent_span_id=None,
            span_type="SPAN",
            name="success_confirmed",
            start_time=current_time,
            end_time=current_time + timedelta(seconds=0.1),
            input_data={
                "message": "All faults remediated and cluster stability confirmed",
                "num_faults": len(faults),
                "faults_remediated": [f.name for f in faults],
                "total_duration_seconds": round(experiment_duration, 3),
                "cluster_health": final_check.cluster_health,
                "agent_id": agent_id,
                "detected_at": self._ts(current_time),
            },
            output_data={"status": "logged"},
            metadata={
                "action": "success_confirmed",
                "method": "multi_fault_final_confirmation",
                "timestamp": self._ts(current_time),
                "total_duration_seconds": round(experiment_duration, 3),
                "faults_count": len(faults),
                "all_resolved": final_check.all_faults_resolved,
                "llm_used": False,
                "tokens_consumed": 0,
                "confidence_score": final_check.confidence_score,
            },
            experiment_id=experiment_id,
            run_id=run_id,
        )
        spans.append(success_span)

        logger.info(f"Generated {len(spans)} spans for {len(faults)} faults "
                     f"(duration: {experiment_duration:.1f}s)")
        return spans

    async def generate_and_save(
        self,
        faults: List[FaultDefinition],
        output_dir: str,
        num_detection_cycles: int = 3,
        agent_id: str = "",
        experiment_id: str = "",
        run_id: str = "",
    ) -> Path:
        """Generate a multi-fault trace and save it to a JSON file."""
        if not experiment_id:
            experiment_id = self.generate_experiment_id(faults)
        if not run_id:
            run_id = str(uuid.uuid4())

        spans = await self.generate_trace(
            faults=faults,
            num_detection_cycles=num_detection_cycles,
            agent_id=agent_id,
            experiment_id=experiment_id,
            run_id=run_id,
        )

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        fault_names_slug = "_".join(f.name for f in faults)[:60]
        filename = f"trace-multi_fault_{run_id}.json"
        filepath = output_path / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(spans, f, indent=2, ensure_ascii=False)

        logger.info(f"Multi-fault trace saved to {filepath} ({len(spans)} spans)")
        return filepath
