"""
Fault Bucketing Pipeline for Multi-Fault Langfuse Traces.

Implements the Log Preprocessing & Fault Bucketing algorithm described in
Section 1.4 of the AgentCert Methodologies wiki. Streams Langfuse trace events
in chronological order, identifies fault lifecycle phases (detection →
investigation → remediation → verification → confirmation), and uses an LLM
classifier to assign interleaved events into per-fault buckets.

Supports both multi-fault traces (multiple fault_detected events) and
single-fault traces (creates one pass-through bucket).

Output: per-fault JSON files for downstream metrics extraction.
"""

import argparse
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fault_analyzer.scripts.classifier import FaultEventClassifier
from fault_analyzer.schema.data_models import (
    EventClassification,
    FaultBucket,
    parse_iso_timestamp,
    safe_parse_python_literal,
)

# Optional imports — gracefully handle if not available
try:
    from utils.load_config import ConfigLoader
    from utils.setup_logging import logger
except ImportError:
    ConfigLoader = None
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _MODULE_DIR / "config" / "fault_bucketing_config.json"


def _load_module_config() -> Dict[str, Any]:
    """Load the fault bucketing module configuration from JSON."""
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# FaultBucketingPipeline
# ---------------------------------------------------------------------------

class FaultBucketingPipeline:
    """
    Preprocesses a Langfuse trace by separating interleaved events into
    per-fault buckets so each fault's lifecycle can be evaluated independently.

    Algorithm:
      1. Initialize empty active-faults list and bucket dictionary.
      2. Extract FAULT_DATA events (injected ground truth from chaos platform).
      3. Stream remaining events in temporal order through the LLM in batches.
      4. The LLM determines fault detection, mitigation, and event assignment.
      5. On LLM-identified fault detection → create bucket, add to active faults.
      6. On LLM-identified mitigation → close bucket, remove from active faults.
      7. Output per-fault JSON files.
    """

    def __init__(
        self,
        trace_file_path: str,
        output_dir: str,
        config: Optional[Dict[str, Any]] = None,
        batch_size: Optional[int] = None,
    ):
        # Load module-level settings
        module_config = _load_module_config()
        pipeline_config = module_config.get("pipeline", {})
        default_batch_size = pipeline_config.get("default_batch_size", 10)
        self._max_filename_stem_length = pipeline_config.get("max_filename_stem_length", 80)

        self.trace_file_path = Path(trace_file_path)
        self.output_dir = Path(output_dir)
        self.batch_size = batch_size if batch_size is not None else default_batch_size

        # Load config
        if config:
            self.config = config
        elif ConfigLoader:
            self.config = ConfigLoader.load_config()
        else:
            self.config = {}

        # LLM classifier
        self._classifier = FaultEventClassifier(config=self.config)

        # Pipeline state
        self.active_faults: Dict[str, FaultBucket] = {}
        self.closed_faults: Dict[str, FaultBucket] = {}
        self.injected_faults: Dict[str, FaultBucket] = {}  # from FAULT_DATA
        self.unclassified_events: List[Dict[str, Any]] = []

        # Agent metadata extracted from the first trace event
        self.agent_id: Optional[str] = None
        self.agent_name: Optional[str] = None
        self.agent_version: Optional[str] = None
        self.experiment_id: Optional[str] = None
        self.run_id: Optional[str] = None

    @property
    def total_input_tokens(self) -> int:
        return self._classifier.total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._classifier.total_output_tokens

    # ------------------------------------------------------------------
    # Extract agent metadata
    # ------------------------------------------------------------------

    def _extract_agent_metadata(self, sorted_events: List[Dict[str, Any]]) -> None:
        """Extract agent_id, agent_name, agent_version, experiment_id, and run_id from the first trace event.

        Looks at the input and metadata fields of the first event for agent
        onboarding information.
        """
        if not sorted_events:
            return

        first_event = sorted_events[0]
        # Try input field first, then metadata
        for field_name in ("input", "metadata"):
            raw = first_event.get(field_name)
            if not raw:
                continue
            parsed = safe_parse_python_literal(raw)
            if isinstance(parsed, dict):
                if not self.agent_id and parsed.get("agent_id"):
                    self.agent_id = parsed["agent_id"]
                if not self.agent_name and parsed.get("agent_name"):
                    self.agent_name = parsed["agent_name"]
                if not self.agent_version and parsed.get("agent_version"):
                    self.agent_version = parsed["agent_version"]
                if not self.experiment_id and parsed.get("experiment_id"):
                    self.experiment_id = parsed["experiment_id"]
                if not self.run_id and parsed.get("run_id"):
                    self.run_id = parsed["run_id"]

        if self.agent_id:
            logger.info(
                f"Agent metadata extracted: id={self.agent_id}, "
                f"name={self.agent_name}, version={self.agent_version}, "
                f"experiment_id={self.experiment_id}, run_id={self.run_id}"
            )

    # ------------------------------------------------------------------
    # Load and sort events
    # ------------------------------------------------------------------

    def _load_trace(self) -> List[Dict[str, Any]]:
        """Load the trace JSON file and validate it's a list of span objects."""
        if not self.trace_file_path.exists():
            raise FileNotFoundError(
                f"Trace file not found: {self.trace_file_path}"
            )
        with open(self.trace_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError(
                f"Expected a JSON array of trace events, "
                f"got {type(data).__name__}"
            )
        logger.info(
            f"Loaded {len(data)} events from {self.trace_file_path.name}"
        )
        return data

    @staticmethod
    def _sort_events_chronologically(
        events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Sort trace events by startTime (ISO-8601). Nulls sort last."""

        def _sort_key(event: Dict[str, Any]):
            ts = parse_iso_timestamp(event.get("startTime"))
            return ts if ts else datetime.max.replace(tzinfo=None)

        return sorted(events, key=_sort_key)

    # ------------------------------------------------------------------
    # FAULT_DATA identification and ground-truth extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _is_fault_injection_event(event: Dict[str, Any]) -> bool:
        """Return True if the event represents an injected fault (ground truth).

        These events have ``type == "FAULT_DATA"`` and are emitted by the
        Fault Pod via the OTEL Collector at experiment start.
        """
        return event.get("type") == "FAULT_DATA"

    @staticmethod
    def _extract_ground_truth(event: Dict[str, Any]) -> FaultBucket:
        """Parses a FAULT_DATA event's input field to extract ground_truth,
        ideal_course_of_action, and ideal_tool_usage_trajectory, returning a FaultBucket.

        Parses the ``input`` field (Python literal or JSON) to extract:
          - ground_truth (fault_description_goal_remediation, etc.)
          - ideal_course_of_action
          - ideal_tool_usage_trajectory

        The fault name is taken from the event's ``name`` field.
        """
        fault_name = event.get("name", "unknown")
        input_data = safe_parse_python_literal(event.get("input", "{}"))

        ground_truth: Optional[Dict[str, Any]] = None
        ideal_course: Optional[List[Any]] = None
        ideal_trajectory: Optional[List[Any]] = None

        if isinstance(input_data, dict):
            ground_truth = input_data.get("ground_truth")
            ideal_course = input_data.get("ideal_course_of_action")
            ideal_trajectory = input_data.get("ideal_tool_usage_trajectory")

        fault_id = fault_name

        return FaultBucket(
            fault_id=fault_id,
            fault_name=fault_name,
            events=[event],
            status="active",
            detected_at=event.get("startTime"),
            ground_truth=ground_truth,
            ideal_course_of_action=ideal_course,
            ideal_tool_usage_trajectory=ideal_trajectory,
        )

    # ------------------------------------------------------------------
    # Ground-truth enrichment
    # ------------------------------------------------------------------

    def _enrich_bucket_with_ground_truth(self, bucket: FaultBucket) -> None:
        """Match a detected-fault bucket to an injected FAULT_DATA bucket
        and carry over ground_truth, ideal_course_of_action, and
        ideal_tool_usage_trajectory.

        Matching is done by comparing the detected fault_name against
        injected fault names using substring / normalized comparison
        (e.g. "pod-delete" matches "pod_delete").
        """
        if not self.injected_faults:
            return

        detected_name = bucket.fault_name.lower().replace("-", "_")

        for _fid, injected in self.injected_faults.items():
            injected_name = injected.fault_name.lower().replace("-", "_")
            if detected_name == injected_name or detected_name in injected_name or injected_name in detected_name:
                bucket.ground_truth = injected.ground_truth
                bucket.ideal_course_of_action = injected.ideal_course_of_action
                bucket.ideal_tool_usage_trajectory = injected.ideal_tool_usage_trajectory
                bucket.injection_timestamp = injected.detected_at
                logger.info(
                    f"Enriched bucket '{bucket.fault_id}' with ground truth "
                    f"from injected fault '{injected.fault_name}'"
                )
                return

    # ------------------------------------------------------------------
    # Event batching helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _create_event_batches(
        events: List[Dict[str, Any]], batch_size: int
    ) -> List[List[Dict[str, Any]]]:
        """Split events into batches of the given size, preserving order."""
        return [
            events[i : i + batch_size]
            for i in range(0, len(events), batch_size)
        ]

    # ------------------------------------------------------------------
    # Place classified events into buckets
    # ------------------------------------------------------------------

    def _place_event_in_buckets(
        self, event: Dict[str, Any], classification: EventClassification
    ) -> None:
        """Place an event into the bucket(s) indicated by its classification."""
        placed = False
        all_buckets = {**self.active_faults, **self.closed_faults}

        for fault_id in classification.related_faults:
            bucket = all_buckets.get(fault_id)
            if bucket:
                bucket.events.append(event)
                placed = True

        if not placed:
            self.unclassified_events.append(event)

    # ------------------------------------------------------------------
    # Close a fault bucket
    # ------------------------------------------------------------------

    def _close_fault(self, fault_id: str, mitigated_at: Optional[str] = None) -> None:
        """Move a fault from active to closed."""
        if fault_id in self.active_faults:
            bucket = self.active_faults.pop(fault_id)
            bucket.status = "closed"
            bucket.mitigated_at = mitigated_at
            self.closed_faults[fault_id] = bucket
            logger.info(
                f"Fault bucket closed: {fault_id} "
                f"({len(bucket.events)} events)"
            )

    # ------------------------------------------------------------------
    # Main orchestration
    # ------------------------------------------------------------------

    async def run(self) -> Dict[str, FaultBucket]:
        """
        Execute the fault bucketing pipeline.

        All fault detection and mitigation decisions are made by the LLM.
        Events are processed in chronological batches; the LLM identifies
        which events represent new fault detections, which confirm
        mitigations, and which faults each event relates to.

        Returns:
            Dictionary of fault_id → FaultBucket (all buckets, active + closed).
        """
        # Load and sort
        raw_events = self._load_trace()
        sorted_events = self._sort_events_chronologically(raw_events)

        # ----------------------------------------------------------
        # Extract agent metadata from the first trace event
        # ----------------------------------------------------------
        self._extract_agent_metadata(sorted_events)

        # ----------------------------------------------------------
        # Pass 0: Extract FAULT_DATA events (injected fault ground truth)
        # ----------------------------------------------------------
        non_fault_data_events: List[Dict[str, Any]] = []

        for event in sorted_events:
            if self._is_fault_injection_event(event):
                bucket = self._extract_ground_truth(event)
                bucket.agent_id = self.agent_id
                bucket.agent_name = self.agent_name
                bucket.agent_version = self.agent_version
                bucket.experiment_id = self.experiment_id
                bucket.run_id = self.run_id
                if bucket.fault_id in self.injected_faults:
                    self.injected_faults[bucket.fault_id].events.append(event)
                else:
                    self.injected_faults[bucket.fault_id] = bucket
                    logger.info(
                        f"Fault injected (FAULT_DATA): {bucket.fault_id} "
                        f"(ground_truth={bucket.ground_truth is not None})"
                    )
            else:
                non_fault_data_events.append(event)

        if not non_fault_data_events:
            self._write_output()
            return {**self.active_faults, **self.closed_faults}

        # ----------------------------------------------------------
        # Process all events via LLM in chronological batches.
        # The LLM determines fault detection, mitigation, and
        # event-to-fault assignment — no heuristic rules.
        # ----------------------------------------------------------
        logger.info(
            f"Processing {len(non_fault_data_events)} events via LLM "
            f"(batch_size={self.batch_size})"
        )
        batches = self._create_event_batches(
            non_fault_data_events, self.batch_size
        )

        for batch_idx, batch in enumerate(batches):
            # Provide all known faults (active + closed) as context
            known_faults = {**self.active_faults, **self.closed_faults}

            classifications = await self._classifier.classify_batch(
                batch, known_faults, self.injected_faults
            )

            # Build a map of event_id → classification
            classification_map: Dict[str, EventClassification] = {
                c.event_id: c for c in classifications
            }

            for event in batch:
                eid = event.get("id", "")
                classification = classification_map.get(eid)

                if not classification:
                    self.unclassified_events.append(event)
                    continue

                # --- Handle new fault detection identified by LLM ---
                if classification.fault_detected:
                    fault_id = classification.fault_detected
                    if (
                        fault_id not in self.active_faults
                        and fault_id not in self.closed_faults
                    ):
                        # Create a new fault bucket from LLM classification
                        new_bucket = FaultBucket(
                            fault_id=fault_id,
                            fault_name=fault_id,
                            severity=classification.detected_fault_severity,
                            target_pod=classification.detected_fault_target_pod,
                            namespace=classification.detected_fault_namespace,
                            detection_signals=classification.detected_fault_signals,
                            events=[event],
                            status="active",
                            detected_at=event.get("startTime"),
                            agent_id=self.agent_id,
                            agent_name=self.agent_name,
                            agent_version=self.agent_version,
                            experiment_id=self.experiment_id,
                            run_id=self.run_id,
                        )
                        self._enrich_bucket_with_ground_truth(new_bucket)
                        self.active_faults[fault_id] = new_bucket
                        logger.info(
                            f"Fault detected (LLM): {fault_id} "
                            f"(severity={new_bucket.severity}, "
                            f"pod={new_bucket.target_pod})"
                        )
                    else:
                        # Fault already known — add event to existing bucket
                        target = (
                            self.active_faults.get(fault_id)
                            or self.closed_faults.get(fault_id)
                        )
                        if target:
                            target.events.append(event)

                    # Also assign to other related faults beyond the detected one
                    for related_id in classification.related_faults:
                        if related_id != fault_id:
                            target = (
                                self.active_faults.get(related_id)
                                or self.closed_faults.get(related_id)
                            )
                            if target:
                                target.events.append(event)
                else:
                    # No new fault detection — place in related fault buckets
                    self._place_event_in_buckets(event, classification)

                # --- Handle fault mitigation identified by LLM ---
                if classification.fault_mitigated:
                    mid = classification.fault_mitigated
                    mitigation_ts = (
                        event.get("startTime") or event.get("endTime")
                    )
                    self._close_fault(mid, mitigated_at=mitigation_ts)

            logger.info(
                f"Batch {batch_idx + 1}/{len(batches)} processed "
                f"({len(batch)} events)"
            )

        # ----------------------------------------------------------
        # Fallback: if no faults were discovered → single-fault trace
        # ----------------------------------------------------------
        if not self.active_faults and not self.closed_faults:
            logger.info(
                "No faults identified by LLM. "
                "Treating as single-fault trace (one bucket)."
            )
            single_bucket = FaultBucket(
                fault_id="single_fault",
                fault_name="unknown",
                events=non_fault_data_events,
                status="closed",
                detected_at=(
                    non_fault_data_events[0].get("startTime")
                    if non_fault_data_events
                    else None
                ),
                mitigated_at=(
                    non_fault_data_events[-1].get("endTime")
                    if non_fault_data_events
                    else None
                ),
                agent_id=self.agent_id,
                agent_name=self.agent_name,
                agent_version=self.agent_version,
                experiment_id=self.experiment_id,
                run_id=self.run_id,
            )
            self.closed_faults["single_fault"] = single_bucket

        # Re-sort events within each bucket to maintain chronological order
        all_buckets = {**self.active_faults, **self.closed_faults}
        for bucket in all_buckets.values():
            bucket.events = self._sort_events_chronologically(bucket.events)

        # Log summary
        total_events = sum(len(b.events) for b in all_buckets.values())
        logger.info(
            f"Bucketing complete: {len(self.injected_faults)} injected faults, "
            f"{len(all_buckets)} buckets, "
            f"{total_events} events assigned, "
            f"{len(self.unclassified_events)} unclassified, "
            f"LLM tokens used: {self.total_input_tokens + self.total_output_tokens}"
        )

        self._write_output()
        return all_buckets

    # ------------------------------------------------------------------
    # Output writer
    # ------------------------------------------------------------------

    def _write_output(self) -> None:
        """Write per-fault bucket JSON files and a summary manifest."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        trace_stem = self.trace_file_path.stem  # filename without extension
        # Truncate trace stem to keep filenames within OS limits
        max_stem = self._max_filename_stem_length
        short_stem = trace_stem[:max_stem] if len(trace_stem) > max_stem else trace_stem

        all_buckets = {**self.active_faults, **self.closed_faults}
        manifest_entries: List[Dict[str, Any]] = []

        for fault_id, bucket in all_buckets.items():
            # Use fault_name (shorter) for the filename; keep full fault_id in JSON
            safe_name = bucket.fault_name.replace("/", "_").replace(" ", "_")
            bucket_filename = f"{short_stem}_bucket_{safe_name}.json"
            bucket_path = self.output_dir / bucket_filename

            bucket_output = bucket.to_dict()

            with open(bucket_path, "w", encoding="utf-8") as f:
                json.dump(bucket_output, f, indent=2, default=str)

            manifest_entries.append({
                "fault_id": fault_id,
                "fault_name": bucket.fault_name,
                "severity": bucket.severity,
                "target_pod": bucket.target_pod,
                "namespace": bucket.namespace,
                "status": bucket.status,
                "event_count": len(bucket.events),
                "detected_at": bucket.detected_at,
                "mitigated_at": bucket.mitigated_at,
                "ground_truth": bucket.ground_truth,
                "ideal_course_of_action": bucket.ideal_course_of_action,
                "ideal_tool_usage_trajectory": bucket.ideal_tool_usage_trajectory,
                "agent_id": bucket.agent_id,
                "agent_name": bucket.agent_name,
                "agent_version": bucket.agent_version,
                "output_file": bucket_filename,
            })

            logger.info(
                f"Wrote bucket file: {bucket_filename} "
                f"({len(bucket.events)} events)"
            )

        # Write unclassified events if any
        if self.unclassified_events:
            unclassified_filename = f"{short_stem}_unclassified.json"
            unclassified_path = self.output_dir / unclassified_filename
            with open(unclassified_path, "w", encoding="utf-8") as f:
                json.dump(self.unclassified_events, f, indent=2, default=str)
            logger.info(
                f"Wrote unclassified events: {unclassified_filename} "
                f"({len(self.unclassified_events)} events)"
            )

        # Write manifest
        manifest_filename = f"{short_stem}_bucketing_manifest.json"
        manifest_path = self.output_dir / manifest_filename
        # Summarize injected faults (ground truth from FAULT_DATA)
        injected_faults_summary = [
            {
                "fault_id": fid,
                "fault_name": fb.fault_name,
                "ground_truth": fb.ground_truth,
                "ideal_course_of_action": fb.ideal_course_of_action,
                "ideal_tool_usage_trajectory": fb.ideal_tool_usage_trajectory,
            }
            for fid, fb in self.injected_faults.items()
        ]

        manifest = {
            "trace_file": self.trace_file_path.name,
            "total_injected_faults": len(self.injected_faults),
            "total_faults": len(all_buckets),
            "total_events_assigned": sum(
                len(b.events) for b in all_buckets.values()
            ),
            "unclassified_event_count": len(self.unclassified_events),
            "llm_tokens_used": {
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "total_tokens": self.total_input_tokens + self.total_output_tokens,
            },
            "injected_faults": injected_faults_summary,
            "buckets": manifest_entries,
        }

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, default=str)

        logger.info(f"Wrote manifest: {manifest_filename}")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for running the fault bucketing pipeline."""
    # Load default batch size from module config
    module_config = _load_module_config()
    default_batch_size = module_config.get("pipeline", {}).get("default_batch_size", 10)

    parser = argparse.ArgumentParser(
        description="Fault Bucketing Pipeline — preprocess Langfuse traces "
        "into per-fault buckets for metrics extraction."
    )
    parser.add_argument(
        "--trace-file",
        required=True,
        help="Path to the Langfuse trace JSON file.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where per-fault bucket JSON files will be written.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=default_batch_size,
        help=f"Number of events per LLM classification batch "
        f"(default: {default_batch_size}).",
    )

    args = parser.parse_args()

    # Load config
    config = {}
    if ConfigLoader:
        try:
            config = ConfigLoader.load_config()
        except Exception as e:
            logger.warning(f"Could not load config: {e}. Using defaults.")

    pipeline = FaultBucketingPipeline(
        trace_file_path=args.trace_file,
        output_dir=args.output_dir,
        config=config,
        batch_size=args.batch_size,
    )

    result = asyncio.run(pipeline.run())

    # Print summary
    print(f"\nFault Bucketing Complete")
    print(f"{'=' * 50}")
    for fault_id, bucket in result.items():
        print(
            f"  [{bucket.status.upper():>6}] {fault_id}: "
            f"{len(bucket.events)} events "
            f"(severity={bucket.severity})"
        )
    print(f"  Unclassified: {len(pipeline.unclassified_events)} events")
    print(f"  Output: {pipeline.output_dir}")


if __name__ == "__main__":
    main()
