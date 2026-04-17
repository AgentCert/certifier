"""
Code-based numeric aggregation for trace metrics extraction.
All mathematical operations (sums, averages, ratios) are performed here
instead of relying on LLM for computation accuracy.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from utils.setup_logging import logger
except ImportError:
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)


class QuantitativeAggregator:
    """Aggregates numeric quantitative fields from partial batch metrics in code."""

    @staticmethod
    def _parse_timestamp(ts: str) -> Optional[datetime]:
        """Parse an ISO format timestamp string.

        Always returns a timezone-naive datetime in UTC to avoid
        TypeError when subtracting offset-aware and offset-naive datetimes.
        """
        if not ts:
            return None
        try:
            ts_clean = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_clean)
            if dt.tzinfo is not None:
                from datetime import timezone
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def extract_from_bucket_metadata(bucket_metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract quantitative fields directly from the fault bucket metadata.

        The bucket metadata comes from the trace bucket JSON file produced
        by the fault bucketing pipeline.  It contains top-level keys such as
        ``injection_timestamp``, ``detected_at``, ``mitigated_at``,
        ``fault_name``, ``namespace``, ``target_pod``, etc.

        Returns deterministic field values without LLM dependency.
        """
        if not bucket_metadata:
            return {}

        result: Dict[str, Any] = {}

        if bucket_metadata.get("agent_name"):
            result["agent_name"] = bucket_metadata["agent_name"]
        if bucket_metadata.get("agent_id"):
            result["agent_id"] = bucket_metadata["agent_id"]
        if bucket_metadata.get("agent_version"):
            result["agent_version"] = bucket_metadata["agent_version"]

        if bucket_metadata.get("experiment_id"):
            result["experiment_id"] = bucket_metadata["experiment_id"]
        if bucket_metadata.get("run_id"):
            result["run_id"] = bucket_metadata["run_id"]
        if bucket_metadata.get("injection_timestamp"):
            result["fault_injection_time"] = bucket_metadata["injection_timestamp"]
        if bucket_metadata.get("fault_name"):
            result["injected_fault_name"] = bucket_metadata["fault_name"]
        if bucket_metadata.get("severity"):
            result["injected_fault_category"] = bucket_metadata["severity"]
        if bucket_metadata.get("target_pod"):
            result["fault_target_service"] = bucket_metadata["target_pod"]
        if bucket_metadata.get("namespace"):
            result["fault_namespace"] = bucket_metadata["namespace"]

        # Bucket-level detection and mitigation timestamps
        if bucket_metadata.get("detected_at"):
            result["bucket_detected_at"] = bucket_metadata["detected_at"]
        if bucket_metadata.get("mitigated_at"):
            result["bucket_mitigated_at"] = bucket_metadata["mitigated_at"]

        return result

    @staticmethod
    def find_events_by_timestamp(
        timestamp: str,
        events: List[Dict[str, Any]],
        time_field: str,
    ) -> List[Dict[str, Any]]:
        """Return all events whose *time_field* matches *timestamp*.

        Args:
            timestamp: The timestamp string to look for.
            events: The list of trace event / span dicts.
            time_field: The event key to compare against
                        (``'startTime'`` or ``'endTime'``).

        Returns:
            List of matching event dicts (may be empty).
        """
        if not timestamp or not events:
            return []
        return [e for e in events if e.get(time_field) == timestamp]

    def aggregate(
        self,
        partial_metrics: List[Dict[str, Any]],
        total_spans: int,
        span_times: Optional[Dict[str, Optional[str]]],
        bucket_metadata: Optional[Dict[str, Any]] = None,
        validated_bucket_timestamps: Optional[Dict[str, Optional[str]]] = None,
    ) -> Dict[str, Any]:
        """
        Aggregate all numeric quantitative fields in code. No LLM math.

        Args:
            partial_metrics: List of partial metrics dicts from each batch.
            total_spans: Total number of spans in the trace.
            span_times: Detection/mitigation timestamps identified by LLM from spans.
            bucket_metadata: Optional bucket metadata for deterministic fields.
            validated_bucket_timestamps: Optional dict with pre-validated bucket
                timestamps. Keys are ``'agent_fault_detection_time'`` and
                ``'agent_fault_mitigation_time'``. A value of ``None`` means
                the bucket timestamp failed LLM content validation.

        Returns:
            Dict with all aggregated quantitative values.
        """
        aggregated: Dict[str, Any] = {}

        # Extract fields directly from bucket metadata (deterministic)
        bucket_fields = self.extract_from_bucket_metadata(bucket_metadata)
        aggregated.update(bucket_fields)

        # Resolve detection/mitigation timestamps:
        # Use LLM-validated bucket timestamps as primary.  If bucket
        # timestamps were not validated (absent or failed content check),
        # fall back to LLM-identified span timestamps from the trace.
        bucket_detected = aggregated.pop("bucket_detected_at", None)
        bucket_mitigated = aggregated.pop("bucket_mitigated_at", None)
        llm_detected = (span_times or {}).get("agent_fault_detection_time")
        llm_mitigated = (span_times or {}).get("agent_fault_mitigation_time")

        validated = validated_bucket_timestamps or {}
        validated_detected = validated.get("agent_fault_detection_time")
        validated_mitigated = validated.get("agent_fault_mitigation_time")

        if validated_detected:
            aggregated["agent_fault_detection_time"] = validated_detected
            logger.info(
                "Using LLM-validated bucket detected_at: %s",
                validated_detected,
            )
        elif bucket_detected and not validated_bucket_timestamps:
            # No validation was performed (no events passed) — use bucket as-is
            aggregated["agent_fault_detection_time"] = bucket_detected
        elif llm_detected:
            aggregated["agent_fault_detection_time"] = llm_detected
            if bucket_detected:
                logger.warning(
                    "Bucket detected_at (%s) failed content validation. "
                    "Falling back to LLM-identified detection time (%s).",
                    bucket_detected,
                    llm_detected,
                )

        if validated_mitigated:
            aggregated["agent_fault_mitigation_time"] = validated_mitigated
            logger.info(
                "Using LLM-validated bucket mitigated_at: %s",
                validated_mitigated,
            )
        elif bucket_mitigated and not validated_bucket_timestamps:
            aggregated["agent_fault_mitigation_time"] = bucket_mitigated
        elif llm_mitigated:
            aggregated["agent_fault_mitigation_time"] = llm_mitigated
            if bucket_mitigated:
                logger.warning(
                    "Bucket mitigated_at (%s) failed content validation. "
                    "Falling back to LLM-identified mitigation time (%s).",
                    bucket_mitigated,
                    llm_mitigated,
                )

        # First non-null text/timestamp selections from LLM batch output (fallback)
        first_non_null_fields = ["experiment_id", "run_id"]
        for fname in [
            "injected_fault_name",
            "injected_fault_category",
            "detected_fault_type",
            "fault_target_service",
            "fault_namespace",
            "fault_injection_time",
            "agent_fault_detection_time",
            "agent_fault_mitigation_time",
        ]:
            if fname not in aggregated:
                first_non_null_fields.append(fname)

        for fname in first_non_null_fields:
            if fname in aggregated:
                continue
            for batch in partial_metrics:
                val = batch.get(fname)
                if val is not None:
                    aggregated[fname] = val
                    break

        # fault_detected: pick the most detailed description (longest non-trivial)
        fault_descriptions = [
            batch.get("fault_detected", "")
            for batch in partial_metrics
            if batch.get("fault_detected")
            and batch.get("fault_detected") != "Unknown"
        ]
        aggregated["fault_detected"] = (
            max(fault_descriptions, key=len) if fault_descriptions else "Unknown"
        )

        # Summable numeric fields
        sum_fields = [
            "input_tokens",
            "output_tokens",
            "number_of_pii_instances_detected",
            "malicious_prompts_detected",
        ]
        for fname in sum_fields:
            total = 0
            found = False
            for batch in partial_metrics:
                val = batch.get(fname)
                if val is not None:
                    try:
                        total += int(val)
                        found = True
                    except (ValueError, TypeError):
                        logger.warning(f"Non-numeric value for {fname}: {val}")
            if found:
                aggregated[fname] = total

        aggregated["trajectory_steps"] = total_spans

        # Boolean OR fields
        for fname in ["pii_detection"]:
            for batch in partial_metrics:
                if batch.get(fname) is True:
                    aggregated[fname] = True
                    break
            else:
                aggregated[fname] = False

        # Merge tool_calls lists
        all_tool_calls: List[Dict[str, Any]] = []
        for batch in partial_metrics:
            calls = batch.get("tool_calls", [])
            if isinstance(calls, list):
                all_tool_calls.extend(calls)
        aggregated["tool_calls"] = all_tool_calls

        # Ratio fields: sum numerators and denominators, compute ratio in code
        ratio_configs = {
            "tool_selection_accuracy": (
                "correct_tool_selections",
                "total_tool_selections",
                False,
            ),
        }
        for ratio_field, (num_field, den_field, as_percentage) in ratio_configs.items():
            total_num = 0
            total_den = 0
            found = False
            for batch in partial_metrics:
                num = batch.get(num_field)
                den = batch.get(den_field)
                if num is not None and den is not None:
                    try:
                        total_num += float(num)
                        total_den += float(den)
                        found = True
                    except (ValueError, TypeError):
                        logger.warning(
                            f"Non-numeric values for {ratio_field}: {num_field}={num}, {den_field}={den}"
                        )
            if found and total_den > 0:
                ratio = total_num / total_den
                if as_percentage:
                    aggregated[ratio_field] = round(ratio * 100, 2)
                else:
                    aggregated[ratio_field] = round(ratio, 4)

        # Compute time_to_detect and time_to_mitigate from timestamps
        fit = aggregated.get("fault_injection_time")
        fdt = aggregated.get("agent_fault_detection_time")
        fmt = aggregated.get("agent_fault_mitigation_time")

        if fit and fdt:
            dt_inject = self._parse_timestamp(str(fit))
            dt_detect = self._parse_timestamp(str(fdt))
            if dt_inject and dt_detect:
                ttd = round(abs((dt_detect - dt_inject).total_seconds()), 2)
                if ttd == 0.0:
                    logger.warning(
                        "time_to_detect is 0: fault_injection_time and "
                        "agent_fault_detection_time are identical (%s). "
                        "This likely means the injection timestamp was not "
                        "available and detection time was used as fallback.",
                        fit,
                    )
                aggregated["time_to_detect"] = ttd

        if fit and fmt:
            dt_inject = self._parse_timestamp(str(fit))
            dt_mitigate = self._parse_timestamp(str(fmt))
            if dt_inject and dt_mitigate:
                aggregated["time_to_mitigate"] = round(
                    abs((dt_mitigate - dt_inject).total_seconds()), 2
                )

        return aggregated


class QualitativeAggregator:
    """Aggregates numeric qualitative fields from partial batch observations in code."""

    def aggregate(
        self,
        partial_observations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Aggregate numeric qualitative fields in code. No LLM math.

        Args:
            partial_observations: List of observation dicts from each batch.

        Returns:
            Dict with code-computed numeric values to override LLM output.
        """
        aggregated: Dict[str, Any] = {}

        # Average numeric scores across batches
        avg_fields = [
            "reasoning_quality_score",
        ]
        for fname in avg_fields:
            values: List[float] = []
            for batch in partial_observations:
                val = batch.get(fname)
                if val is not None:
                    try:
                        values.append(float(val))
                    except (ValueError, TypeError):
                        logger.warning(f"Non-numeric value for {fname}: {val}")
            if values:
                aggregated[fname] = round(sum(values) / len(values), 2)

        # hallucination_score: compute from raw counts across batches
        total_hallucination_count = 0
        total_response_count = 0
        for batch in partial_observations:
            h_count = batch.get("hallucination_count")
            r_count = batch.get("total_response_count")
            if h_count is not None:
                try:
                    total_hallucination_count += int(h_count)
                except (ValueError, TypeError):
                    logger.warning(f"Non-numeric hallucination_count: {h_count}")
            if r_count is not None:
                try:
                    total_response_count += int(r_count)
                except (ValueError, TypeError):
                    logger.warning(f"Non-numeric total_response_count: {r_count}")
        if total_response_count > 0:
            aggregated["hallucination_score"] = round(
                total_hallucination_count / total_response_count, 2
            )

        return aggregated
