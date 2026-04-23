"""
Fault Bucketing Pipeline for Multi-Fault Langfuse Traces.

Creates per-fault buckets **deterministically** by scanning span names
for the ``fault: *`` pattern (e.g. ``fault: pod-delete``).  No LLM is
used for bucket creation.

Deduplication rules:
  - If an active (not yet closed) bucket with the same fault name exists,
    the new span is added to the existing bucket instead of creating a
    duplicate.
  - If all previous buckets with that name are closed, a new bucket is
    created with a numeric suffix for uniqueness.

Ground truth is extracted from the fault span's metadata when present.
Remaining non-fault events are assigned to fault buckets using an LLM
classifier that determines which fault(s) each event relates to, and
whether an event represents a fault mitigation.

Output: per-fault JSON files for downstream metrics extraction.
"""

import argparse
import asyncio
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.custom_errors import MyCustomError, FaultBucketingError

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
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise FaultBucketingError(
            f"Could not load module config: {_CONFIG_PATH}",
            original_exception=exc,
        ) from exc


# ---------------------------------------------------------------------------
# FaultBucketingPipeline
# ---------------------------------------------------------------------------

class FaultBucketingPipeline:
    """Preprocesses a Langfuse trace by separating interleaved events into
    per-fault buckets so each fault's lifecycle can be evaluated independently.

    Algorithm:
      1. Scan spans for the ``fault: *`` naming pattern to create fault
         buckets deterministically (no LLM).
      2. Deduplicate: skip creation if an active bucket with the same
         fault name exists; create a new bucket only if the previous one
         was closed.
      3. Extract ground truth from the fault span's metadata.
      4. Stream remaining (non-fault) events through the LLM classifier
         in batches to assign them to fault buckets and detect mitigations.
      5. Output per-fault JSON files.
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

        # LLM classifier for assigning non-fault events to buckets
        try:
            self._classifier = FaultEventClassifier(config=self.config)
        except MyCustomError:
            raise
        except Exception as exc:
            raise FaultBucketingError(
                "Failed to initialize FaultEventClassifier",
                original_exception=exc,
            ) from exc

        # Pipeline state
        self.active_faults: Dict[str, FaultBucket] = {}
        self.closed_faults: Dict[str, FaultBucket] = {}
        self.unclassified_events: List[Dict[str, Any]] = []
        self.other_detected_faults: List[Dict[str, Any]] = []

        # Agent metadata extracted from early trace events (before first fault span)
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

    def _all_agent_metadata_found(self) -> bool:
        """Return True if all agent metadata fields have been populated."""
        return all([
            self.agent_id,
            self.agent_name,
            self.agent_version,
            self.experiment_id,
            self.run_id,
        ])

    def _extract_agent_metadata(self, sorted_events: List[Dict[str, Any]]) -> None:
        """Extract agent_id, agent_name, agent_version, experiment_id, and run_id from early trace events.

        Scans events in chronological order, checking the input and metadata
        fields of each event, stopping as soon as a ``fault: *`` span is
        encountered or all metadata fields have been populated.
        """
        if not sorted_events:
            return

        for event in sorted_events:
            # Stop scanning once we hit a fault span
            if self._is_fault_name_span(event):
                break

            # Try input field first, then metadata
            for field_name in ("input", "metadata"):
                raw = event.get(field_name)
                if not raw:
                    continue
                parsed = safe_parse_python_literal(raw)
                if isinstance(parsed, dict):
                    # Check both top-level and nested "attributes" dict
                    search_dicts = [parsed]
                    if isinstance(parsed.get("attributes"), dict):
                        search_dicts.append(parsed["attributes"])

                    for d in search_dicts:
                        if not self.agent_id:
                            self.agent_id = d.get("agent_id") or d.get("agentid")
                        if not self.agent_name:
                            self.agent_name = d.get("agent_name")
                        if not self.agent_version:
                            self.agent_version = d.get("agent_version")
                        if not self.experiment_id:
                            self.experiment_id = d.get("experiment_id") or d.get("experiment.id")
                        if not self.run_id:
                            self.run_id = d.get("run_id") or d.get("experiment.run_id")

            # Stop early if all fields are populated
            if self._all_agent_metadata_found():
                break

        if self.agent_id:
            logger.info(
                f"Agent metadata extracted: id={self.agent_id}, "
                f"name={self.agent_name}, version={self.agent_version}, "
                f"experiment_id={self.experiment_id}, run_id={self.run_id}"
            )

    # ------------------------------------------------------------------
    # Fault span identification (deterministic bucketing)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_fault_name_span(event: Dict[str, Any]) -> bool:
        """Return True if the event's name matches the ``fault: *`` pattern."""
        name = event.get("name", "")
        if not isinstance(name, str):
            return False
        if name.startswith("fault:"):
            return bool(name[len("fault:"):].strip())
        return False

    @staticmethod
    def _extract_fault_name_from_span(event: Dict[str, Any]) -> Optional[str]:
        """Extract the fault name from a span with name ``fault: <name>``.

        E.g. ``fault: pod-delete`` → ``pod-delete``.
        """
        name = event.get("name", "")
        if isinstance(name, str) and name.startswith("fault:"):
            fault_name = name[len("fault:"):].strip()
            return fault_name if fault_name else None
        return None

    @staticmethod
    def _extract_metadata_dict(event: Dict[str, Any]) -> Dict[str, Any]:
        """Parse the metadata field of an event into a dictionary."""
        raw = event.get("metadata")
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
        return {}

    # ------------------------------------------------------------------
    # Load and sort events
    # ------------------------------------------------------------------

    def _load_trace(self) -> List[Dict[str, Any]]:
        """Load the trace JSON file and validate it's a list of span objects."""
        if not self.trace_file_path.exists():
            raise FaultBucketingError(
                f"Trace file not found: {self.trace_file_path}"
            )

        try:
            with open(self.trace_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise FaultBucketingError(
                f"Trace file is not valid JSON: {self.trace_file_path}",
                original_exception=exc,
            ) from exc
        except OSError as exc:
            raise FaultBucketingError(
                f"Could not read trace file: {self.trace_file_path}",
                original_exception=exc,
            ) from exc

        if not isinstance(data, list):
            raise FaultBucketingError(
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
            return ts if ts else datetime.max.replace(tzinfo=timezone.utc)

        return sorted(events, key=_sort_key)

    # ------------------------------------------------------------------
    # Ground-truth extraction from fault span metadata
    # ------------------------------------------------------------------

    def _extract_ground_truth_from_metadata(
        self, event: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Extract ground truth from a fault span's metadata or input.

        Looks for a ``ground_truth`` key in the following locations:
          1. Top-level metadata dict
          2. Metadata ``attributes`` sub-dict
          3. The event's ``input`` field (parsed)
        """
        metadata = self._extract_metadata_dict(event)
        gt = metadata.get("ground_truth")
        if gt is None:
            gt = metadata.get("attributes", {}).get("ground_truth")

        # Also check the input field (fault injection events may carry
        # ground truth there)
        if gt is None:
            raw_input = event.get("input")
            if raw_input:
                parsed_input = safe_parse_python_literal(raw_input)
                if isinstance(parsed_input, dict):
                    gt = parsed_input.get("ground_truth")

        if isinstance(gt, str):
            gt = safe_parse_python_literal(gt)
        return gt if isinstance(gt, dict) else None

    # ------------------------------------------------------------------
    # Deterministic fault bucket creation from "fault: *" spans
    # ------------------------------------------------------------------

    def _create_fault_bucket_from_span(self, event: Dict[str, Any]) -> None:
        """Create a fault bucket from a span whose name matches ``fault: *``.

        Injection spans are used only for metadata extraction (timestamps,
        ground truth, target info) and are **not** appended to the bucket's
        events list.

        Deduplication rules:
        - If an **active** bucket with the same fault name exists, the
          duplicate injection span is silently skipped.
        - If all previous buckets with the same fault name are **closed**,
          a new bucket is created (with a numeric suffix for uniqueness).

        Ground truth is extracted from the span's metadata if present.
        """
        fault_name = self._extract_fault_name_from_span(event)
        if not fault_name:
            return

        # Dedup: active bucket with same fault_name → skip creation.
        # Do NOT append the injection span to events — it is only used
        # for metadata extraction, not as an agent event.
        if fault_name in self.active_faults:
            logger.info(
                f"Fault bucket '{fault_name}' already active, "
                f"skipping duplicate injection span."
            )
            return

        # Also check active_faults by fault_name (fault_id may differ)
        for fid, bucket in self.active_faults.items():
            if bucket.fault_name == fault_name:
                logger.info(
                    f"Fault bucket '{fid}' (name={fault_name}) already "
                    f"active, skipping duplicate injection span."
                )
                return

        # Determine unique fault_id when closed bucket(s) exist
        fault_id = fault_name
        counter = 1
        while fault_id in self.closed_faults:
            counter += 1
            fault_id = f"{fault_name}_{counter}"

        # Parse metadata attributes
        metadata = self._extract_metadata_dict(event)
        attributes = metadata.get("attributes", {})

        target_pod = attributes.get("fault.target_label")
        namespace = attributes.get("fault.target_namespace")
        fault_status = attributes.get("fault.status", "").lower()

        # Extract ground truth from metadata or input
        ground_truth = self._extract_ground_truth_from_metadata(event)

        # Extract SLA, ideal course of action, and ideal tool usage
        # trajectory from within the ground truth dict
        sla = None
        ideal_course_of_action = None
        ideal_tool_usage_trajectory = None
        if ground_truth:
            sla = ground_truth.get("sla")
            ideal_course_of_action = ground_truth.get("ideal_course_of_action")
            ideal_tool_usage_trajectory = ground_truth.get("ideal_tool_usage_trajectory")

        # Create new bucket.  The injection span is NOT included in
        # events — it is used only for metadata.  The bucket stays
        # active; only the LLM classifier should close it when a real
        # mitigation event is identified.
        bucket = FaultBucket(
            fault_id=fault_id,
            fault_name=fault_name,
            target_pod=target_pod,
            namespace=namespace,
            events=[],
            status="active",
            injection_timestamp=event.get("startTime"),
            ground_truth=ground_truth,
            sla=sla,
            ideal_course_of_action=ideal_course_of_action,
            ideal_tool_usage_trajectory=ideal_tool_usage_trajectory,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
            agent_version=self.agent_version,
            experiment_id=self.experiment_id,
            run_id=self.run_id,
        )

        self.active_faults[fault_id] = bucket
        logger.info(
            f"Fault bucket created: {fault_id} "
            f"(target={target_pod}, namespace={namespace})"
        )

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
    # Record fault detection / localization
    # ------------------------------------------------------------------

    def _record_fault_detection(
        self,
        classification: EventClassification,
        detection_ts: Optional[str] = None,
    ) -> None:
        """Update a fault bucket with detection metadata from the LLM classifier.

        When the classifier identifies an event as the agent's first
        recognition of a fault, this method records the detection
        timestamp and associated metadata (severity, target pod,
        namespace, signals) on the matching bucket.

        If the detected fault matches an existing bucket (by fault name
        or ID), the bucket is updated only if it hasn't already been
        marked as detected by the agent.  If no matching bucket exists,
        the detection is recorded in ``self.other_detected_faults`` for
        output as a separate JSON file.
        """
        detected_name = classification.fault_detected
        if not detected_name:
            return

        all_buckets = {**self.active_faults, **self.closed_faults}

        # Try to find a matching bucket by fault_id or fault_name
        target_bucket: Optional[FaultBucket] = None
        target_key: Optional[str] = None

        if detected_name in all_buckets:
            target_bucket = all_buckets[detected_name]
            target_key = detected_name
        else:
            for fid, bucket in all_buckets.items():
                if bucket.fault_name == detected_name:
                    target_bucket = bucket
                    target_key = fid
                    break

        if target_bucket is not None:
            # Only update detection metadata if not already recorded
            if target_bucket.detected_at is None or target_bucket.detected_at == target_bucket.injection_timestamp:
                target_bucket.detected_at = detection_ts
            if classification.detected_fault_severity and not target_bucket.severity:
                target_bucket.severity = classification.detected_fault_severity
            if classification.detected_fault_target_pod and not target_bucket.target_pod:
                target_bucket.target_pod = classification.detected_fault_target_pod
            if classification.detected_fault_namespace and not target_bucket.namespace:
                target_bucket.namespace = classification.detected_fault_namespace
            if classification.detected_fault_signals and not target_bucket.detection_signals:
                target_bucket.detection_signals = classification.detected_fault_signals
            logger.info(
                f"Fault detection recorded for bucket '{target_key}' "
                f"at {detection_ts}"
            )
        else:
            # New fault discovered by the agent that doesn't match any
            # existing bucket — record it separately without creating
            # a full fault bucket.
            self.other_detected_faults.append({
                "fault_name": detected_name,
                "detected_at": detection_ts,
                "severity": classification.detected_fault_severity,
                "target_pod": classification.detected_fault_target_pod,
                "namespace": classification.detected_fault_namespace,
                "detection_signals": classification.detected_fault_signals or [],
                "event_id": classification.event_id,
            })
            logger.info(
                f"Other fault detected by agent (no matching bucket): "
                f"'{detected_name}' at {detection_ts}"
            )

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

        Fault buckets are created **deterministically** from spans whose
        name matches the ``fault: *`` pattern (e.g. ``fault: pod-delete``).
        No LLM is used for bucket creation.

        Deduplication:
        - If an active bucket with the same fault name exists, the event
          is added to it instead of creating a duplicate.
        - If all previous buckets with that name are closed, a new bucket
          is created (with a numeric suffix for uniqueness).

        Remaining (non-fault) events are assigned to fault buckets via
        the LLM classifier, which determines the related fault(s) for
        each event and identifies fault mitigations.

        Returns:
            Dictionary of fault_id → FaultBucket (all buckets, active + closed).
        """
        # Load and sort

        try:
            raw_events = self._load_trace()
            sorted_events = self._sort_events_chronologically(raw_events)

            # ----------------------------------------------------------
            # Extract agent metadata from the first trace event
            # ----------------------------------------------------------
            self._extract_agent_metadata(sorted_events)

            # ----------------------------------------------------------
            # Pass 1: Create fault buckets from "fault: *" spans
            #         (deterministic — no LLM)
            # ----------------------------------------------------------
            remaining_events: List[Dict[str, Any]] = []

            for event in sorted_events:
                if self._is_fault_name_span(event):
                    self._create_fault_bucket_from_span(event)
                else:
                    remaining_events.append(event)

            logger.info(
                f"Deterministic bucketing: {len(self.active_faults)} active, "
                f"{len(self.closed_faults)} closed fault bucket(s) created "
                f"from 'fault: *' spans."
            )

            if not remaining_events:
                self._write_output()
                return {**self.active_faults, **self.closed_faults}

            # ----------------------------------------------------------
            # Pass 2: Assign remaining events to fault buckets via LLM
            #         classifier in chronological batches.
            # ----------------------------------------------------------
            logger.info(
                f"Processing {len(remaining_events)} remaining events via LLM "
                f"(batch_size={self.batch_size})"
            )
            batches = self._create_event_batches(
                remaining_events, self.batch_size
            )

            for batch_idx, batch in enumerate(batches):
                # Provide all known fault buckets (active + closed) as context
                known_faults = {**self.active_faults, **self.closed_faults}

                try:
                    classifications = await self._classifier.classify_batch(
                        batch, known_faults
                    )
                except MyCustomError as exc:
                    logger.error(
                        f"Batch {batch_idx + 1}/{len(batches)} classification failed "
                        f"(custom error): {exc}. Marking {len(batch)} events as unclassified."
                    )
                    self.unclassified_events.extend(batch)
                    continue
                except Exception as exc:
                    logger.error(
                        f"Batch {batch_idx + 1}/{len(batches)} classification failed: {exc}. "
                        f"Marking {len(batch)} events as unclassified.",
                        exc_info=True,
                    )
                    self.unclassified_events.extend(batch)
                    continue

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

                    # --- Handle fault detection/localization identified by LLM ---
                    if classification.fault_detected:
                        detection_ts = (
                            event.get("startTime") or event.get("endTime")
                        )
                        self._record_fault_detection(
                            classification, detection_ts
                        )

                    # --- Assign event to related fault bucket(s) ---
                    self._place_event_in_buckets(event, classification)

                    # --- Handle fault mitigation identified by LLM ---
                    if classification.fault_mitigated:
                        mid = classification.fault_mitigated
                        mitigation_ts = (
                            event.get("endTime") or event.get("startTime")
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
                    "No 'fault: *' spans found. "
                    "Treating as single-fault trace (one bucket)."
                )
                single_bucket = FaultBucket(
                    fault_id="single_fault",
                    fault_name="unknown",
                    events=sorted_events,
                    status="closed",
                    detected_at=(
                        sorted_events[0].get("startTime")
                        if sorted_events
                        else None
                    ),
                    mitigated_at=(
                        sorted_events[-1].get("endTime")
                        if sorted_events
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
                f"Bucketing complete: {len(all_buckets)} buckets, "
                f"{total_events} events assigned, "
                f"{len(self.unclassified_events)} unclassified, "
                f"LLM tokens used: {self.total_input_tokens + self.total_output_tokens}"
            )

            self._write_output()
            self._write_ground_truth()
            return all_buckets
        except MyCustomError:
            raise
        except Exception as exc:
            raise FaultBucketingError(
                "Fault bucketing pipeline failed",
                original_exception=exc,
            ) from exc    

    # ------------------------------------------------------------------
    # Ground truth writer
    # ------------------------------------------------------------------

    def _write_ground_truth(self) -> None:
        """Write per-fault ground truth files into a ``ground_truth`` subfolder.

        One file per fault per experiment.  If a file already exists for
        the same fault + experiment combination it is overwritten.

        Filename pattern: ``<experiment_id>_<fault_name>_ground_truth.json``
        (falls back to ``unknown_experiment`` when experiment_id is absent).
        """
        all_buckets = {**self.active_faults, **self.closed_faults}

        # Only proceed if at least one bucket has ground truth
        if not any(b.ground_truth for b in all_buckets.values()):
            return

        gt_dir = self.output_dir.parent / "ground_truth"
        gt_dir.mkdir(parents=True, exist_ok=True)

        for fault_id, bucket in all_buckets.items():
            if not bucket.ground_truth:
                continue

            safe_fault = bucket.fault_name.replace("/", "_").replace(" ", "_")
            exp_id = bucket.experiment_id or self.experiment_id or "unknown_experiment"
            safe_exp = str(exp_id).replace("/", "_").replace(" ", "_")

            gt_filename = f"{safe_exp}_{safe_fault}_ground_truth.json"
            gt_path = gt_dir / gt_filename

            gt_output = {
                "fault_id": fault_id,
                "fault_name": bucket.fault_name,
                "experiment_id": exp_id,
                "ground_truth": bucket.ground_truth,
            }

            try:
                with open(gt_path, "w", encoding="utf-8") as f:
                    json.dump(gt_output, f, indent=2, default=str)
                logger.info(f"Wrote ground truth: {gt_filename}")
            except (OSError, TypeError) as exc:
                logger.error(
                    f"Failed to write ground truth file {gt_filename}: {exc}",
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Output writer
    # ------------------------------------------------------------------

    def _write_output(self) -> None:
        """Write per-fault bucket JSON files and a summary manifest."""
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FaultBucketingError(
                f"Failed to create output directory: {self.output_dir}",
                original_exception=exc,
            ) from exc

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

            try:
                with open(bucket_path, "w", encoding="utf-8") as f:
                    json.dump(bucket_output, f, indent=2, default=str)
            except (OSError, TypeError) as exc:
                raise FaultBucketingError(
                    f"Failed to write bucket file: {bucket_filename}",
                    original_exception=exc,
                ) from exc

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
                "sla": bucket.sla,
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

        # Write other detected faults (agent-discovered faults with no matching bucket)
        if self.other_detected_faults:
            other_faults_filename = f"{short_stem}_other_detected_faults.json"
            other_faults_path = self.output_dir / other_faults_filename
            try:
                with open(other_faults_path, "w", encoding="utf-8") as f:
                    json.dump(self.other_detected_faults, f, indent=2, default=str)
            except (OSError, TypeError) as exc:
                raise FaultBucketingError(
                    f"Failed to write other-detected-faults file: {other_faults_path}",
                    original_exception=exc,
                ) from exc
            logger.info(
                f"Wrote other detected faults: {other_faults_filename} "
                f"({len(self.other_detected_faults)} faults)"
            )

        # Write unclassified events if any
        if self.unclassified_events:
            unclassified_filename = f"{short_stem}_unclassified.json"
            unclassified_path = self.output_dir / unclassified_filename
            try:
                with open(unclassified_path, "w", encoding="utf-8") as f:
                    json.dump(self.unclassified_events, f, indent=2, default=str)
            except (OSError, TypeError) as exc:
                raise FaultBucketingError(
                    f"Failed to write unclassified events file: {unclassified_path}",
                    original_exception=exc,
                ) from exc
            logger.info(
                f"Wrote unclassified events: {unclassified_filename} "
                f"({len(self.unclassified_events)} events)"
            )

        # Write manifest
        manifest_filename = f"{short_stem}_bucketing_manifest.json"
        manifest_path = self.output_dir / manifest_filename

        manifest = {
            "trace_file": self.trace_file_path.name,
            "total_faults": len(all_buckets),
            "total_events_assigned": sum(
                len(b.events) for b in all_buckets.values()
            ),
            "other_detected_faults_count": len(self.other_detected_faults),
            "unclassified_event_count": len(self.unclassified_events),
            "llm_tokens_used": {
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "total_tokens": self.total_input_tokens + self.total_output_tokens,
            },
            "buckets": manifest_entries,
        }

        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, default=str)
        except (OSError, TypeError) as exc:
            raise FaultBucketingError(
                f"Failed to write manifest: {manifest_path}",
                original_exception=exc,
            ) from exc

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

    try:
        pipeline = FaultBucketingPipeline(
            trace_file_path=args.trace_file,
            output_dir=args.output_dir,
            config=config,
            batch_size=args.batch_size,
        )
        result = asyncio.run(pipeline.run())
    except MyCustomError as exc:
        logger.error(f"Fault bucketing failed: {exc}")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Unexpected error in fault bucketing: {exc}", exc_info=True)
        sys.exit(1)

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
