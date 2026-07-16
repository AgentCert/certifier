"""
Lightweight fault metadata extractor for the Langfuse evaluator method.

Scans a trace for ``fault: *`` spans deterministically (no LLM) and
returns minimal FaultBucket objects — metadata only, no event assignment.
These buckets are used to build the known_faults_context injected into
Langfuse observations before triggering the fault-event-classifier-lf
evaluator.

This module is intentionally independent of FaultBucketingPipeline and
FaultEventClassifier so the Langfuse evaluator method can run without
any Azure OpenAI dependency.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fault_analyzer.schema.data_models import (
    FaultBucket,
    parse_iso_timestamp,
    safe_parse_python_literal,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trace loading
# ---------------------------------------------------------------------------

def _load_trace(trace_file: Path) -> List[Dict[str, Any]]:
    """Load a trace JSON file and return the list of observation dicts."""
    if not trace_file.exists():
        raise FileNotFoundError(f"Trace file not found: {trace_file}")
    with open(trace_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array, got {type(data).__name__}")
    logger.info(f"Loaded {len(data)} observations from {trace_file.name}")
    return data


def _sort_chronologically(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort observations by startTime (ISO-8601). Nulls sort last."""
    def _key(e: Dict[str, Any]):
        ts = parse_iso_timestamp(e.get("startTime"))
        return ts if ts else datetime.max.replace(tzinfo=timezone.utc)
    return sorted(events, key=_key)


# ---------------------------------------------------------------------------
# Fault span identification
# ---------------------------------------------------------------------------

def _is_fault_span(event: Dict[str, Any]) -> bool:
    name = event.get("name", "")
    return isinstance(name, str) and name.startswith("fault:") and bool(name[len("fault:"):].strip())


def _fault_name(event: Dict[str, Any]) -> Optional[str]:
    name = event.get("name", "")
    if isinstance(name, str) and name.startswith("fault:"):
        n = name[len("fault:"):].strip()
        return n if n else None
    return None


# ---------------------------------------------------------------------------
# Metadata extraction helpers
# ---------------------------------------------------------------------------

def _parse_metadata(event: Dict[str, Any]) -> Dict[str, Any]:
    raw = event.get("metadata")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _extract_injection_metadata(event: Dict[str, Any]) -> Dict[str, Any]:
    """Build the structured injection_metadata dict from a fault: * span."""
    raw_meta = event.get("metadata")
    if isinstance(raw_meta, str):
        try:
            raw_meta = json.loads(raw_meta)
        except (json.JSONDecodeError, TypeError):
            raw_meta = {}
    if not isinstance(raw_meta, dict):
        raw_meta = {}
    attrs = raw_meta.get("attributes", {})
    if not isinstance(attrs, dict):
        attrs = {}

    result: Dict[str, Any] = {}

    if "fault.name" in attrs:
        result["name"] = attrs["fault.name"]
    if "fault.engine_name" in attrs:
        result["engine_name"] = attrs["fault.engine_name"]
    if "fault.namespace" in attrs:
        result["namespace"] = attrs["fault.namespace"]
    result["status"] = "injected"
    if "fault.injection_timestamp" in attrs:
        result["injection_timestamp"] = attrs["fault.injection_timestamp"]
    if "fault.injection_end_timestamp" in attrs:
        result["injection_end_timestamp"] = attrs["fault.injection_end_timestamp"]

    target_ns = attrs.get("fault.target_namespace")
    infra_ns = attrs.get("fault.namespace")
    if target_ns:
        target: Dict[str, Any] = {"namespace": target_ns}
        if "fault.target_label" in attrs:
            target["label"] = attrs["fault.target_label"]
        if "fault.target_kind" in attrs:
            target["kind"] = attrs["fault.target_kind"].lower()
        if "fault.target.workload_ref" in attrs:
            target["workload_ref"] = attrs["fault.target.workload_ref"]
        target["degraded"] = (
            target_ns == infra_ns and "fault.target_label" not in attrs
        )
        result["target"] = target

    timing: Dict[str, Any] = {}
    if "fault.timing.total_chaos_duration_sec" in attrs:
        timing["total_chaos_duration_sec"] = int(attrs["fault.timing.total_chaos_duration_sec"])
    if "fault.timing.ramp_time_sec" in attrs:
        timing["ramp_time_sec"] = int(attrs["fault.timing.ramp_time_sec"])
    if "fault.timing.chaos_interval_sec" in attrs:
        timing["chaos_interval_sec"] = int(attrs["fault.timing.chaos_interval_sec"])
    if timing:
        result["timing"] = timing

    return result


def _extract_ground_truth(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract ground truth from a fault span's metadata or input."""
    metadata = _parse_metadata(event)
    gt = metadata.get("ground_truth") or metadata.get("attributes", {}).get("ground_truth")

    if gt is None:
        raw_input = event.get("input")
        if raw_input:
            parsed = safe_parse_python_literal(raw_input)
            if isinstance(parsed, dict):
                gt = parsed.get("ground_truth")

    if isinstance(gt, str):
        gt = safe_parse_python_literal(gt)
    return gt if isinstance(gt, dict) else None


def _extract_agent_metadata(sorted_events: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """Extract agent_id, agent_name, agent_version, experiment_id, run_id from early spans."""
    result: Dict[str, Optional[str]] = {
        "agent_id": None,
        "agent_name": None,
        "agent_version": None,
        "experiment_id": None,
        "run_id": None,
    }

    for event in sorted_events:
        if _is_fault_span(event):
            break
        for field_name in ("input", "metadata"):
            raw = event.get(field_name)
            if not raw:
                continue
            parsed = safe_parse_python_literal(raw)
            if not isinstance(parsed, dict):
                continue
            for d in [parsed, parsed.get("attributes", {})] if isinstance(parsed.get("attributes"), dict) else [parsed]:
                if not result["agent_id"]:
                    result["agent_id"] = d.get("agent_id") or d.get("agentid")
                if not result["agent_name"]:
                    result["agent_name"] = d.get("agent_name")
                if not result["agent_version"]:
                    result["agent_version"] = d.get("agent_version")
                if not result["experiment_id"]:
                    result["experiment_id"] = d.get("experiment_id") or d.get("experiment.id")
                if not result["run_id"]:
                    result["run_id"] = d.get("run_id") or d.get("experiment.run_id")

        if all(result.values()):
            break

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_fault_metadata(trace_file: Path) -> Dict[str, FaultBucket]:
    """Extract fault metadata from a trace by scanning ``fault: *`` spans.

    Performs only the deterministic Pass 1 of FaultBucketingPipeline:
    no LLM call, no event assignment, no Azure OpenAI dependency.

    Returns a dict of fault_id → FaultBucket (metadata only, events=[])
    ready to feed inject_context_all_generations in evaluate_existing_trace.py.

    If no ``fault: *`` span is found, returns a single ``single_fault`` bucket
    so the evaluator still has a non-empty context to work with.
    """
    events = _sort_chronologically(_load_trace(trace_file))
    agent_meta = _extract_agent_metadata(events)

    buckets: Dict[str, FaultBucket] = {}
    closed: Dict[str, FaultBucket] = {}

    for event in events:
        if not _is_fault_span(event):
            continue

        name = _fault_name(event)
        if not name:
            continue

        # Dedup: skip if an active bucket with the same fault name exists
        if any(b.fault_name == name for b in buckets.values()):
            logger.info(f"Bucket '{name}' already active, skipping duplicate span.")
            continue

        # Unique fault_id when a closed bucket with the same name exists
        fault_id = name
        counter = 1
        while fault_id in closed:
            counter += 1
            fault_id = f"{name}_{counter}"

        metadata = _parse_metadata(event)
        attrs = metadata.get("attributes", {})
        injection_metadata = _extract_injection_metadata(event)
        ground_truth = _extract_ground_truth(event)

        sla = None
        ideal_course_of_action = None
        ideal_tool_usage_trajectory = None
        if ground_truth:
            sla = ground_truth.get("sla")
            ideal_course_of_action = ground_truth.get("ideal_course_of_action")
            ideal_tool_usage_trajectory = ground_truth.get("ideal_tool_usage_trajectory")

        bucket = FaultBucket(
            fault_id=fault_id,
            fault_name=name,
            target_pod=attrs.get("fault.target_label"),
            namespace=attrs.get("fault.target_namespace"),
            events=[],
            status="active",
            injection_timestamp=event.get("startTime"),
            injection_end_timestamp=attrs.get("fault.injection_end_timestamp"),
            injection_metadata=injection_metadata,
            ground_truth=ground_truth,
            sla=sla,
            ideal_course_of_action=ideal_course_of_action,
            ideal_tool_usage_trajectory=ideal_tool_usage_trajectory,
            agent_id=agent_meta["agent_id"],
            agent_name=agent_meta["agent_name"],
            agent_version=agent_meta["agent_version"],
            experiment_id=agent_meta["experiment_id"],
            run_id=agent_meta["run_id"],
        )
        buckets[fault_id] = bucket
        logger.info(f"Fault bucket created: {fault_id}")

    if not buckets:
        logger.info("No 'fault: *' spans found — creating single_fault fallback bucket.")
        buckets["single_fault"] = FaultBucket(
            fault_id="single_fault",
            fault_name="unknown",
            events=[],
            status="active",
            agent_id=agent_meta["agent_id"],
            agent_name=agent_meta["agent_name"],
            agent_version=agent_meta["agent_version"],
            experiment_id=agent_meta["experiment_id"],
            run_id=agent_meta["run_id"],
        )

    logger.info(f"Fault metadata extraction complete: {len(buckets)} bucket(s)")
    return buckets
