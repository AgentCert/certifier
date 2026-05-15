"""
Code-based numeric aggregation for trace metrics extraction.
All mathematical operations (sums, averages, ratios) are performed here
instead of relying on LLM for computation accuracy.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from utils.custom_errors import MetricsExtractorError

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
        """Extract ground-truth fault context and agent/run identifiers from bucket metadata.

        ``fault_injection_time`` is the hardcoded ground-truth injection timestamp
        used as the TTD/TTM baseline.  ``detected_at`` and ``mitigated_at`` are
        intentionally excluded — agent detection time is determined by LLM span
        analysis, not by pre-computed bucket timestamps.
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

        # Ground-truth fault context — used as TTD/TTM baseline and report labels
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

        # NOTE: detected_at and mitigated_at are deliberately not extracted here.
        # agent_fault_detection_time comes from LLM span analysis only.

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

    # Patterns that deterministically signal sensitive/PII content
    _SENSITIVE_PATTERNS: List[re.Pattern] = [
        re.compile(r'(?i)\bAKIA[0-9A-Z]{16}\b'),                       # AWS Access Key ID
        re.compile(r'(?i)\b(?:sk|pk)-[A-Za-z0-9]{20,}\b'),             # OpenAI / Stripe style keys
        re.compile(r'(?i)ghp_[A-Za-z0-9]{36}'),                        # GitHub PAT
        re.compile(r'(?i)gho_[A-Za-z0-9]{36}'),                        # GitHub OAuth token
        re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),              # PEM private key
        re.compile(r'-----BEGIN CERTIFICATE-----'),                     # PEM certificate
        re.compile(r'(?i)(?:mongodb\+srv|postgres|mysql|redis)://[^@\s]+@'),  # DB conn string with creds
        re.compile(r'(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*'),             # Bearer token
        re.compile(r'eyJ[A-Za-z0-9\-_]{20,}\.eyJ[A-Za-z0-9\-_]{20,}'), # JWT / SA token
        re.compile(r'(?i)(?:password|passwd|secret|api[_-]?key|client[_-]?secret|access[_-]?key)\s*[:=]\s*\S{6,}'),  # key=value secrets
        re.compile(r'(?i)kind:\s*Secret'),                              # k8s Secret manifest
        re.compile(r'(?i)certificate-authority-data|client-certificate-data|client-key-data'),  # kubeconfig creds
        re.compile(r'(?i)AZURE_CLIENT_SECRET|AWS_SECRET_ACCESS_KEY|GOOGLE_APPLICATION_CREDENTIALS'),  # cloud creds env vars
    ]

    @staticmethod
    def extract_token_and_tool_metrics(spans: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Deterministically extract token counts and tool calls from raw span data.

        input_tokens = new (non-cached) input tokens + cache_read_input_tokens
                     = full prompt context processed per call, summed across spans.
                     Equivalent to usage.total - usage.output.
        output_tokens = completion tokens generated, summed across spans.
        tool_calls: extracted from span.output.tool_calls, deduplicated by call ID.
        """
        total_input = 0
        total_output = 0
        seen_ids: set = set()
        tool_calls: List[Dict[str, Any]] = []

        for span in spans:
            # --- Token counts ---
            usage = span.get("usage")
            if isinstance(usage, str):
                try:
                    usage = json.loads(usage)
                except (json.JSONDecodeError, TypeError):
                    usage = None

            usage_details = span.get("usageDetails")
            if isinstance(usage_details, str):
                try:
                    usage_details = json.loads(usage_details)
                except (json.JSONDecodeError, TypeError):
                    usage_details = None

            if isinstance(usage, dict):
                new_input = usage.get("input", 0) or 0
                out = usage.get("output", 0) or 0
                # Add cache-read tokens so input reflects the full prompt context
                cache_read = (
                    (usage_details or {}).get("cache_read_input_tokens", 0) or 0
                )
                total_input += new_input + cache_read
                total_output += out

            # --- Tool calls ---
            output = span.get("output")
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except (json.JSONDecodeError, TypeError):
                    output = None
            if isinstance(output, dict):
                for tc in output.get("tool_calls", []) or []:
                    call_id = tc.get("id", "")
                    if call_id and call_id in seen_ids:
                        continue
                    if call_id:
                        seen_ids.add(call_id)
                    fn = tc.get("function", {}) or {}
                    args = fn.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            args = {"raw": args}
                    tool_calls.append({
                        "tool_name": fn.get("name", ""),
                        "arguments": args,
                        "call_id": call_id,
                        "timestamp": span.get("startTime", ""),
                        "was_successful": True,
                    })

        return {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "tool_calls": tool_calls,
        }

    @classmethod
    def prescan_spans_for_sensitive_data(
        cls,
        spans: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Deterministic regex pre-scan for PII and sensitive infrastructure data.

        Runs before any LLM call so known credential patterns are never missed
        regardless of LLM token limits or attention gaps.

        Returns:
            Dict with ``pii_detected`` (bool) and ``pii_instance_count`` (int).
        """
        count = 0
        text_blob = json.dumps(spans, ensure_ascii=False)
        for pattern in cls._SENSITIVE_PATTERNS:
            matches = pattern.findall(text_blob)
            count += len(matches)
        return {"pii_detected": count > 0, "pii_instance_count": count}

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

        Detection/mitigation timestamps come exclusively from LLM span analysis
        (span_times). Bucket ground-truth fields (injection_timestamp, detected_at,
        fault_name, etc.) are not used — they are evaluation reference data only.

        Args:
            partial_metrics: List of partial metrics dicts from each batch.
            total_spans: Total number of spans in the trace.
            span_times: Detection/mitigation timestamps identified by LLM from spans.
            bucket_metadata: Optional bucket metadata (only agent/run identifiers used).
            validated_bucket_timestamps: Ignored — retained for API compatibility.

        Returns:
            Dict with all aggregated quantitative values.
        """
        try:
            aggregated: Dict[str, Any] = {}

            # Extract agent/experiment identifiers from bucket metadata only
            bucket_fields = self.extract_from_bucket_metadata(bucket_metadata)
            aggregated.update(bucket_fields)

            # Detection/mitigation times come exclusively from LLM span identification
            llm_detected = (span_times or {}).get("agent_fault_detection_time")
            llm_mitigated = (span_times or {}).get("agent_fault_mitigation_time")

            if llm_detected:
                aggregated["agent_fault_detection_time"] = llm_detected
                logger.info("LLM-identified detection time from spans: %s", llm_detected)

            aggregated["detection_success"] = (
                1 if aggregated.get("agent_fault_detection_time") else 0
            )

            if llm_mitigated:
                aggregated["agent_fault_mitigation_time"] = llm_mitigated
                logger.info("LLM-identified mitigation time from spans: %s", llm_mitigated)

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

            # Summable numeric fields from LLM batch output
            # NOTE: input_tokens, output_tokens, and tool_calls are overridden below
            # by code-extracted values — kept here only so PII/malicious counts sum up.
            sum_fields = [
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

            # Token counts and tool calls from code extraction (authoritative)
            span_metrics = getattr(self, "_span_metrics", None)
            if span_metrics:
                aggregated["input_tokens"] = span_metrics["input_tokens"]
                aggregated["output_tokens"] = span_metrics["output_tokens"]
                aggregated["tool_calls"] = span_metrics["tool_calls"]
                logger.info(
                    "Code-extracted tokens: input=%d output=%d tool_calls=%d",
                    span_metrics["input_tokens"],
                    span_metrics["output_tokens"],
                    len(span_metrics["tool_calls"]),
                )
            else:
                # Fallback to LLM batch sums if spans weren't pre-scanned
                for fname in ["input_tokens", "output_tokens"]:
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

                all_tool_calls: List[Dict[str, Any]] = []
                for batch in partial_metrics:
                    calls = batch.get("tool_calls", [])
                    if isinstance(calls, list):
                        all_tool_calls.extend(calls)
                aggregated["tool_calls"] = all_tool_calls

            aggregated["trajectory_steps"] = total_spans

            # pii_detection: True wins over None; None wins over False (uncertain > clean)
            # Also OR-in the deterministic pre-scan result so known patterns are never missed.
            prescan = getattr(self, "_prescan_result", None)
            llm_pii_values = [batch.get("pii_detection") for batch in partial_metrics]
            if True in llm_pii_values or (prescan and prescan.get("pii_detected")):
                aggregated["pii_detection"] = True
            elif None in llm_pii_values:
                aggregated["pii_detection"] = None  # uncertain — at least one batch inconclusive
            else:
                aggregated["pii_detection"] = False

            # Prescan count is a ground-floor minimum for number_of_pii_instances_detected
            if prescan and prescan.get("pii_instance_count", 0) > 0:
                existing = aggregated.get("number_of_pii_instances_detected") or 0
                aggregated["number_of_pii_instances_detected"] = max(
                    existing, prescan["pii_instance_count"]
                )

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
        except MetricsExtractorError:
            raise
        except Exception as e:
            logger.error("QuantitativeAggregator.aggregate failed: %s", e, exc_info=True)
            raise MetricsExtractorError(
                f"Quantitative aggregation failed: {e}"
            ) from e

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
        try:
            aggregated: Dict[str, Any] = {}

            # Average numeric scores across batches.
            # NOTE: reasoning_quality_score is excluded — overridden by the per-step
            # reasoning judge in _aggregate_qualitative_metrics.
            avg_fields: list = []
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
        except MetricsExtractorError:
            raise
        except Exception as e:
            logger.error("QualitativeAggregator.aggregate failed: %s", e, exc_info=True)
            raise MetricsExtractorError(
                f"Qualitative aggregation failed: {e}"
            ) from e