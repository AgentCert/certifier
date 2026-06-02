"""
Metrics extractor from Langfuse trace files.
Extracts LLMQuantitativeExtraction and LLMQualitativeExtraction metrics.
Uses LLM to interpret trace data generically - works with traces having similar keys
but different value terminologies.

Uses batch processing to handle large traces without truncation.
Integrates fault bucket metadata for ground-truth comparison and timestamp baselines.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from utils.custom_errors import MetricsExtractorError, ConfigLoaderError

import yaml

from metrics_extractor.schema.metrics_model import (
    LLMQualitativeExtraction,
    LLMQuantitativeExtraction,
)

from metrics_extractor.scripts.span_aggregator import (
    QualitativeAggregator,
    QuantitativeAggregator,
)
from metrics_extractor.scripts.hallucination_validator import judge_trace
from metrics_extractor.scripts.reasoning_judge import judge_reasoning
from metrics_extractor.schema.data_models import (
    ExtractionResult,
    TokenUsage,
)

# Optional imports - gracefully handle if not available
try:
    from utils.azure_openai_util import AzureLLMClient
    from utils.load_config import ConfigLoader
    from utils.mongodb_util import MongoDBClient, MongoDBConfig
    from utils.setup_logging import logger
except ImportError:
    # Fallback for standalone usage
    AzureLLMClient = None
    ConfigLoader = None
    MongoDBClient = None
    MongoDBConfig = None
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _MODULE_DIR / "prompt" / "prompts.yml"
_CONFIG_PATH = _MODULE_DIR / "config" / "metric_extraction_config.json"


def _load_module_config() -> Dict[str, Any]:
    """Load the metric extraction module configuration from JSON."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise ConfigLoaderError(
            f"Metrics extractor config not found: {_CONFIG_PATH}"
        ) from e
    except json.JSONDecodeError as e:
        raise ConfigLoaderError(
            f"Metrics extractor config is not valid JSON ({_CONFIG_PATH}): {e}"
        ) from e
    except OSError as e:
        raise ConfigLoaderError(
            f"Cannot read metrics extractor config ({_CONFIG_PATH}): {e}"
        ) from e


def _load_prompts() -> Dict[str, str]:
    """Load prompt templates from prompts.yml."""
    try:
        with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError as e:
        raise ConfigLoaderError(
            f"Metrics extractor prompts file not found: {_PROMPT_PATH}"
        ) from e
    except yaml.YAMLError as e:
        raise ConfigLoaderError(
            f"Metrics extractor prompts file is not valid YAML ({_PROMPT_PATH}): {e}"
        ) from e
    except OSError as e:
        raise ConfigLoaderError(
            f"Cannot read metrics extractor prompts ({_PROMPT_PATH}): {e}"
        ) from e


PROMPTS = _load_prompts()
MODULE_CONFIG = _load_module_config()


class TraceMetricsExtractor:
    """
    Extracts metrics from Langfuse trace files using LLM.

    This extractor is generic and works with traces having similar key structures
    but different value terminologies. It uses an LLM to interpret the trace data
    and extract meaningful metrics.

    Uses batch processing to handle large traces without content truncation.
    Integrates fault bucket metadata for ground-truth comparison.
    """

    BATCH_SIZE = MODULE_CONFIG.get("extractor", {}).get("batch_size", 15)

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        bucket_metadata: Optional[Dict[str, Any]] = None,
        output_dir: Optional[Path] = None,
        debug_metrics: bool = False,
    ):
        if config:
            self.config = config
        elif ConfigLoader:
            self.config = ConfigLoader.load_config()
        else:
            self.config = {}
        self.llm_client = None
        self.token_usage = TokenUsage()
        self.mongodb_client: Optional[Any] = None
        self.bucket_metadata: Optional[Dict[str, Any]] = bucket_metadata
        self.output_dir = output_dir
        self.debug_metrics = debug_metrics
        self.quant_aggregator = QuantitativeAggregator()
        self.qual_aggregator = QualitativeAggregator()

    def _get_ground_truth(self) -> Optional[Dict[str, Any]]:
        if not self.bucket_metadata:
            return None
        ground_truth = self.bucket_metadata.get("ground_truth") or {}
        # Merge top-level ideal_course_of_action / ideal_tool_usage_trajectory
        # into ground_truth so prompts can access them.
        ideal_course = self.bucket_metadata.get("ideal_course_of_action")
        ideal_trajectory = self.bucket_metadata.get("ideal_tool_usage_trajectory")
        if ideal_course is not None:
            ground_truth["ideal_course_of_action"] = ideal_course
        if ideal_trajectory is not None:
            ground_truth["ideal_tool_usage_trajectory"] = ideal_trajectory
        return ground_truth if ground_truth else None

    def _build_fault_context(self) -> str:
        """Build fault context from bucket_metadata for use in LLM prompts.
        
        Includes infrastructure metadata and expected behavioral responses (ground truth).
        
        Returns:
            Formatted fault context string with injection timestamp, fault name, 
            expected agent responses, and tool usage trajectory.
        """
        bucket_context = ""
        if self.bucket_metadata:
            injection_ts = self.bucket_metadata.get("injection_timestamp")
            fault_name = self.bucket_metadata.get("fault_name")
            context_parts = ["## Fault Context"]
            if injection_ts:
                context_parts.append(
                    f"- **Fault injection timestamp**: {injection_ts}"
                )
            if fault_name:
                context_parts.append(
                    f"- **Fault name/type**: {fault_name}"
                )
            target_ns = self.bucket_metadata.get("namespace")
            target_svc = self.bucket_metadata.get("target_pod")
            if target_ns:
                context_parts.append(f"- **Target namespace**: {target_ns}")
            if target_svc:
                context_parts.append(f"- **Target service**: {target_svc}")
            
            # Add ground truth data (fault description, symptoms, remediation)
            ground_truth = self._get_ground_truth()
            if ground_truth:
                # Add fault description, symptoms, and remediation guidance
                fault_desc = ground_truth.get("fault_description_goal_remediation", {})
                if fault_desc:
                    symptoms = fault_desc.get("symptoms")
                    if symptoms:
                        symptoms_str = ", ".join(symptoms) if isinstance(symptoms, list) else str(symptoms)
                        context_parts.append(f"- **Expected symptoms**: {symptoms_str}")
                    remediation = fault_desc.get("remediation")
                    if remediation:
                        context_parts.append(f"- **Remediation approach**: {remediation.strip()}")
            
            bucket_context = "\n".join(context_parts)
        return bucket_context

    def _build_quantitative_batch_prompt(
        self, batch_number: int, total_batches: int
    ) -> str:
        """Build the quantitative batch extraction prompt with ground truth context."""
        ground_truth = self._get_ground_truth()
        if ground_truth:
            ideal_course = ground_truth.get("ideal_course_of_action", [])
            ideal_tools = ground_truth.get("ideal_tool_usage_trajectory", [])
            gt_instructions = PROMPTS["ground_truth_with_config"].format(
                ideal_course_of_action=json.dumps(ideal_course, indent=2),
                ideal_tool_usage_trajectory=json.dumps(ideal_tools, indent=2),
            )
        else:
            gt_instructions = PROMPTS["ground_truth_without_config"]

        bucket_context = ""
        if self.bucket_metadata:
            injection_ts = self.bucket_metadata.get("injection_timestamp")
            fault_name = self.bucket_metadata.get("fault_name")
            context_parts = ["## Fault Bucket Context"]
            if injection_ts:
                context_parts.append(
                    f"- **Fault injection timestamp**: {injection_ts} — use this as the authoritative fault_injection_time if the trace does not contain an explicit experiment_start timestamp."
                )
            if fault_name:
                context_parts.append(
                    f"- **Fault name/type**: {fault_name}"
                )
            target_ns = self.bucket_metadata.get("namespace")
            target_svc = self.bucket_metadata.get("target_pod")
            if target_ns:
                context_parts.append(f"- **Target namespace**: {target_ns}")
            if target_svc:
                context_parts.append(f"- **Target service**: {target_svc}")
            bucket_context = "\n".join(context_parts)

        prompt = PROMPTS["quantitative_batch_extraction"].replace(
            "{{batch_number}}", str(batch_number)
        ).replace("{{total_batches}}", str(total_batches))

        return prompt.format(
            ground_truth_instructions=gt_instructions,
            fault_config_context=bucket_context,
        )

    def _build_qualitative_batch_prompt(
        self, batch_number: int, total_batches: int
    ) -> str:
        """Build the qualitative batch extraction prompt with ground truth context."""
        ground_truth = self._get_ground_truth()
        if ground_truth:
            ideal_course = ground_truth.get("ideal_course_of_action", [])
            behavioural_instructions = PROMPTS["behavioural_with_config"].format(
                ideal_course_of_action=json.dumps(ideal_course, indent=2),
            )
        else:
            behavioural_instructions = PROMPTS["behavioural_without_config"]

        prompt = PROMPTS["qualitative_batch_extraction"].replace(
            "{{batch_number}}", str(batch_number)
        ).replace("{{total_batches}}", str(total_batches))

        return prompt.format(
            behavioural_assessment_instructions=behavioural_instructions,
        )

    def _init_llm_client(self):
        """Initialize LLM client lazily."""
        if self.llm_client is None:
            if AzureLLMClient is None:
                raise RuntimeError(
                    "AzureLLMClient is not available. Please ensure utils.azure_openai_util is importable."
                )
            self.llm_client = AzureLLMClient(self.config)

    def _init_mongodb_client(self):
        """Initialize MongoDB client lazily."""
        if self.mongodb_client is None:
            if MongoDBClient is None or MongoDBConfig is None:
                raise RuntimeError(
                    "MongoDBClient is not available. Please ensure utils.mongodb_util is importable."
                )
            mongo_config = MongoDBConfig(self.config)
            self.mongodb_client = MongoDBClient(mongo_config)

    def store_metrics_to_mongodb(
        self,
        quantitative: LLMQuantitativeExtraction,
        qualitative: LLMQualitativeExtraction,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store extracted metrics to MongoDB using sync client."""
        self._init_mongodb_client()

        try:
            doc_id = self.mongodb_client.insert_metrics(
                quantitative=quantitative,
                qualitative=qualitative,
                metadata=metadata,
            )
            logger.info(f"Stored metrics to MongoDB with document ID: {doc_id}")
            return doc_id
        finally:
            self.mongodb_client.close()
            self.mongodb_client = None

    def load_trace_file(self, file_path: str) -> List[Dict[str, Any]]:
        path = Path(file_path)
        if not path.exists():
            raise MetricsExtractorError(f"Trace file not found: {file_path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise MetricsExtractorError(
                f"Trace file is not valid JSON ({file_path}): {e}"
            ) from e
        except OSError as e:
            raise MetricsExtractorError(
                f"Cannot read trace file ({file_path}): {e}"
            ) from e

        try:
            if isinstance(data, dict) and "events" in data:
                events = data["events"]
                if not isinstance(events, list):
                    raise MetricsExtractorError(
                        f"Expected 'events' to be a list, got {type(events).__name__}"
                    )
                if events and not all(isinstance(e, dict) for e in events):
                    raise MetricsExtractorError(
                        "Expected all items in 'events' to be dicts (span objects)"
                    )
                if self.bucket_metadata is None:
                    self.bucket_metadata = {
                        k: v for k, v in data.items() if k != "events"
                    }
                    logger.info(
                        f"Loaded bucket metadata from trace file: "
                        f"fault_id={self.bucket_metadata.get('fault_id')}, "
                        f"fault_name={self.bucket_metadata.get('fault_name')}"
                    )
                return events
            elif isinstance(data, list):
                return data
            else:
                raise MetricsExtractorError(
                    "Unsupported trace file format: expected a list of spans "
                    "or a dict with an 'events' key."
                )
        except MetricsExtractorError:
            raise
        except Exception as e:
            raise MetricsExtractorError(
                f"Failed to parse trace file structure ({file_path}): {e}"
            ) from e
    
    def _create_batches(
        self, spans: List[Dict[str, Any]]
    ) -> List[List[Dict[str, Any]]]:
        """Split spans into batches for processing."""
        sorted_spans = sorted(spans, key=lambda x: x.get("startTime", ""))
        batches = []
        for i in range(0, len(sorted_spans), self.BATCH_SIZE):
            batch = sorted_spans[i: i + self.BATCH_SIZE]
            batches.append(batch)
        return batches

    @staticmethod
    def _prepare_span_for_llm(span: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare a single span for LLM consumption."""
        return {
            "id": span.get("id", ""),
            "type": span.get("type", ""),
            "name": span.get("name", ""),
            "startTime": span.get("startTime", ""),
            "endTime": span.get("endTime"),
            "input": span.get("input", ""),
            "output": span.get("output", ""),
            "metadata": span.get("metadata", ""),
            "usage": span.get("usage", ""),
        }

    async def _score_detection_spans(
        self,
        spans: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Score each span for detection likelihood (debug mode only).
        
        Returns:
            Dict mapping span_id → {detection_score, detection_reason}
        """
        if not self.debug_metrics:
            return {}

        sorted_spans = sorted(spans, key=lambda x: x.get("startTime", ""))
        # Only score first 10 spans to reduce tokens
        first_10_spans = sorted_spans[:10]
        full_spans = [self._prepare_span_for_llm(span) for span in first_10_spans]
        
        span_ids = [span.get("id", "") for span in first_10_spans]
        
        user_message = (
            f"Analyze these {len(full_spans)} trace spans and for EACH span, "
            f"score how strongly it indicates the agent DETECTING or CONFIRMING a fault.\n\n"
            f"For each span, provide:\n"
            f"- detection_score (0-1): likelihood this span represents fault detection\n"
            f"- detection_reason (brief): why or why not this indicates detection\n\n"
            f"Spans:\n```json\n{json.dumps(full_spans, indent=2)}\n```\n\n"
            f'Return a JSON object: {{"span_scores": [{{"span_id": "...", "detection_score": 0.X, "detection_reason": "..."}}]}}'
        )
        
        try:
            result, token_usage = await self.llm_client.call_llm(
                model_name="gpt-4o",
                messages=user_message,
                max_tokens=2000,
                system_prompt=(
                    "You are an expert IT-Ops analyst. Evaluate each span for detection indicators. "
                    "Be concise in your reasoning (1-2 sentences per span)."
                ),
            )
            self.token_usage.add(token_usage)
            
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    result = {}
            
            if not isinstance(result, dict):
                logger.warning("Unexpected span scoring result type")
                return {}
            
            # Build map: span_id → {score, reason}
            span_scores = {}
            for item in result.get("span_scores", []):
                if isinstance(item, dict):
                    span_id = item.get("span_id", "")
                    score = item.get("detection_score", 0.0)
                    reason = item.get("detection_reason", "")
                    if span_id in span_ids:
                        span_scores[span_id] = {
                            "detection_score": score,
                            "detection_reason": reason,
                        }
            
            logger.info(f"Scored {len(span_scores)} spans for detection")
            return span_scores
            
        except Exception as e:
            logger.error(f"Error scoring detection spans: {e}")
            return {}

    async def _identify_detection_mitigation_spans(
        self,
        spans: List[Dict[str, Any]],
    ) -> Dict[str, Optional[str]]:
        """Use LLM to identify the first detection and final mitigation spans.
        
        Uses batch size logic to limit spans sent to LLM. If detection_span_limit
        is configured, only processes the first N spans for identification.
        """
        self._init_llm_client()

        sorted_spans = sorted(spans, key=lambda x: x.get("startTime", ""))

        # Apply span limit if configured (useful for large traces)
        detection_span_limit = MODULE_CONFIG.get("extractor", {}).get("detection_span_limit")
        spans_to_process = sorted_spans
        if detection_span_limit and detection_span_limit > 0:
            spans_to_process = sorted_spans[:detection_span_limit]
            if len(sorted_spans) > detection_span_limit:
                logger.info(
                    f"Detection span limit applied: processing first {detection_span_limit} "
                    f"of {len(sorted_spans)} spans"
                )

        span_start_times: Dict[str, str] = {}
        span_end_times: Dict[str, str] = {}
        full_spans = []
        for span in spans_to_process:
            span_id = span.get("id", "")
            span_start_times[span_id] = span.get("startTime", "")
            span_end_times[span_id] = span.get("endTime", "")
            # Prepare span for LLM, but exclude usage, metadata, and input to reduce token size
            prepared = self._prepare_span_for_llm(span)
            prepared.pop("usage", None)  # Remove usage field
            prepared.pop("metadata", None)  # Remove metadata field
            prepared.pop("input", None)  # Remove input field (mostly repetitive noise)
            full_spans.append(prepared)

        # Build fault context for improved detection identification
        fault_context = self._build_fault_context()
        
        user_message = (
            f"{fault_context}\n\n"
            f"Analyze these {len(full_spans)} trace spans (chronologically ordered) "
            f"and identify:\n"
            f"1. The span where the agent FIRST detected/confirmed the fault\n"
            f"2. The span where the agent completed the FINAL remediation/mitigation\n\n"
            f"Spans:\n```json\n{json.dumps(full_spans, indent=2)}\n```\n\n"
            f'Return a JSON object with: "detection_span_id", "detection_reason" (why this span indicates detection), '
            f'"detection_confidence" (0-1 confidence score), "mitigation_span_id", "mitigation_reason" (why this span indicates mitigation), '
            f'and "mitigation_confidence" (0-1 confidence score).'
        )

        try:
            result, token_usage = await self.llm_client.call_llm(
                model_name="gpt-4o",
                messages=user_message,
                max_tokens=800,
                system_prompt=PROMPTS["span_identification"],
            )
            self.token_usage.add(token_usage)

            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    pass

            if not isinstance(result, dict):
                logger.warning(
                    f"Unexpected span identification result type: {type(result)}"
                )
                return {}

            detection_id = result.get("detection_span_id")
            mitigation_id = result.get("mitigation_span_id")

            times: Dict[str, Optional[str]] = {}
            if detection_id and detection_id in span_start_times:
                times["agent_fault_detection_time"] = span_start_times[detection_id]
                
                logger.info(
                    f"LLM identified detection span: {detection_id} "
                    f"at {span_start_times[detection_id]} "
                    f"(reason: {result.get('detection_reason', '')})"
                )
            elif detection_id:
                logger.warning(
                    f"Detection span ID '{detection_id}' not found in trace spans"
                )

            if mitigation_id and mitigation_id in span_end_times and span_end_times[mitigation_id]:
                times["agent_fault_mitigation_time"] = span_end_times[mitigation_id]
                logger.info(
                    f"LLM identified mitigation span: {mitigation_id} "
                    f"endTime={span_end_times[mitigation_id]}"
                )
            elif mitigation_id and mitigation_id in span_start_times:
                times["agent_fault_mitigation_time"] = span_start_times[mitigation_id]
                logger.warning(
                    f"Mitigation span '{mitigation_id}' has no endTime, "
                    f"falling back to startTime={span_start_times[mitigation_id]}"
                )
            elif mitigation_id:
                logger.warning(
                    f"Mitigation span ID '{mitigation_id}' not found in trace spans"
                )

            # Create debug metrics file if enabled (includes per-span detection scoring on first 10 spans)
            if self.debug_metrics and self.output_dir:
                # Pass only first 10 spans for scoring
                first_10_spans = sorted_spans[:10]
                detection_scores = await self._score_detection_spans(first_10_spans)
                await self._create_debug_detection_analysis(
                    full_spans, result, detection_id, mitigation_id, detection_scores,
                    llm_user_message=user_message
                )

            return times

        except Exception as e:
            logger.error(f"Error identifying detection/mitigation spans: {e}")
            return {}

    async def _create_debug_detection_analysis(
        self,
        spans: List[Dict[str, Any]],
        llm_result: Dict[str, Any],
        detection_id: Optional[str],
        mitigation_id: Optional[str],
        detection_scores: Optional[Dict[str, Dict[str, Any]]] = None,
        llm_user_message: Optional[str] = None,
    ) -> None:
        """Create debug JSON file documenting detection analysis.
        
        File is generated even if detection_id is null. Mitigation analysis is omitted.
        Only first 10 spans are included in per-span scoring analysis.
        
        Args:
            spans: List of prepared spans for LLM
            llm_result: Main LLM result (detection_span_id, reason, confidence)
            detection_id: Selected detection span ID (can be None)
            mitigation_id: Selected mitigation span ID (omitted from debug)
            detection_scores: Optional dict of span_id → {detection_score, detection_reason}
            llm_user_message: Complete user message sent to LLM
        """
        try:
            fault_name = self.bucket_metadata.get("fault_name", "unknown_fault") if self.bucket_metadata else "unknown_fault"
            
            debug_data = {
                "fault_name": fault_name,
                "llm_input": llm_user_message or "(no user message captured)",
                "detection_analysis": {
                    "selected_span_id": detection_id,
                    "selection_reason": llm_result.get("detection_reason", ""),
                    "llm_confidence": llm_result.get("detection_confidence", 0.9),
                    "span_analysis": []
                }
            }
            
            # Analyze each span for detection (with per-span scoring if available)
            for span in spans:
                span_id = span.get("id", "")
                span_name = span.get("name", "")
                is_detection = span_id == detection_id
                
                span_entry = {
                    "span_id": span_id,
                    "span_name": span_name,
                    "was_selected": is_detection,
                    "timestamp": span.get("startTime", ""),
                }
                
                # Include per-span detection scoring if available (only first 10 spans have scores)
                if detection_scores and span_id in detection_scores:
                    score_info = detection_scores[span_id]
                    span_entry["detection_score"] = score_info.get("detection_score")
                    span_entry["detection_reason"] = score_info.get("detection_reason", "")
                
                debug_data["detection_analysis"]["span_analysis"].append(span_entry)
            
            # Write debug file
            debug_file = self.output_dir / f"debug_detection_analysis_{fault_name}.json"
            with open(debug_file, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, indent=2)
            
            logger.info(f"Debug detection analysis written to {debug_file}")
            
        except Exception as e:
            logger.error(f"Error creating debug detection analysis file: {e}")

    async def _validate_bucket_timestamps_with_llm(
        self,
        spans: List[Dict[str, Any]],
    ) -> Dict[str, Optional[str]]:
        """Validate bucket detected_at / mitigated_at by checking whether
        the events at those timestamps actually contain detection or
        mitigation content.

        For detection: finds events whose ``startTime`` matches
        ``bucket_metadata['detected_at']`` and asks the LLM whether
        those events represent a fault detection.

        For mitigation: finds events whose ``endTime`` matches
        ``bucket_metadata['mitigated_at']`` and asks the LLM whether
        those events represent a fault mitigation/remediation.

        Returns:
            Dict with ``'agent_fault_detection_time'`` and/or
            ``'agent_fault_mitigation_time'`` set to the validated
            timestamp string, or ``None`` if validation failed.
        """
        if not self.bucket_metadata:
            return {}

        self._init_llm_client()

        detected_at = self.bucket_metadata.get("detected_at")
        mitigated_at = self.bucket_metadata.get("mitigated_at")
        result: Dict[str, Optional[str]] = {}

        # --- Detection validation ---
        if detected_at:
            matching = QuantitativeAggregator.find_events_by_timestamp(
                detected_at, spans, "startTime"
            )
            if matching:
                prepared = [self._prepare_span_for_llm(e) for e in matching]
                user_msg = (
                    f"The following trace event(s) have startTime={detected_at}.\n"
                    f"Do any of these events represent the agent DETECTING or "
                    f"CONFIRMING a fault/anomaly?\n\n"
                    f"Events:\n```json\n{json.dumps(prepared, indent=2)}\n```\n\n"
                    f'Return a JSON object: {{"is_detection_event": true/false, '
                    f'"reason": "brief explanation"}}'
                )
                try:
                    llm_resp, token_usage = await self.llm_client.call_llm(
                        model_name="gpt-4o",
                        messages=user_msg,
                        max_tokens=300,
                        system_prompt=(
                            "You are an expert IT-Ops analyst. Determine whether "
                            "the given trace event(s) represent the agent detecting "
                            "or confirming a fault. Respond ONLY with the requested "
                            "JSON object."
                        ),
                    )
                    self.token_usage.add(token_usage)
                    if isinstance(llm_resp, str):
                        try:
                            llm_resp = json.loads(llm_resp)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if isinstance(llm_resp, dict) and llm_resp.get("is_detection_event"):
                        result["agent_fault_detection_time"] = detected_at
                        logger.info(
                            "Bucket detected_at (%s) validated by LLM as a "
                            "detection event: %s",
                            detected_at,
                            llm_resp.get("reason", ""),
                        )
                    else:
                        result["agent_fault_detection_time"] = None
                        reason = llm_resp.get("reason", "") if isinstance(llm_resp, dict) else ""
                        logger.warning(
                            "Bucket detected_at (%s) rejected by LLM — matching "
                            "event(s) do not represent a detection event: %s",
                            detected_at,
                            reason,
                        )
                except Exception as e:
                    logger.error("LLM detection validation failed: %s", e)
                    result["agent_fault_detection_time"] = None
            else:
                logger.warning(
                    "Bucket detected_at (%s) has no matching event startTime.",
                    detected_at,
                )
                result["agent_fault_detection_time"] = None

        # --- Mitigation validation ---
        if mitigated_at:
            matching = QuantitativeAggregator.find_events_by_timestamp(
                mitigated_at, spans, "endTime"
            )
            if matching:
                prepared = [self._prepare_span_for_llm(e) for e in matching]
                user_msg = (
                    f"The following trace event(s) have endTime={mitigated_at}.\n"
                    f"Do any of these events represent the agent completing a "
                    f"MITIGATION, REMEDIATION, or RECOVERY action?\n\n"
                    f"Events:\n```json\n{json.dumps(prepared, indent=2)}\n```\n\n"
                    f'Return a JSON object: {{"is_mitigation_event": true/false, '
                    f'"reason": "brief explanation"}}'
                )
                try:
                    llm_resp, token_usage = await self.llm_client.call_llm(
                        model_name="gpt-4o",
                        messages=user_msg,
                        max_tokens=300,
                        system_prompt=(
                            "You are an expert IT-Ops analyst. Determine whether "
                            "the given trace event(s) represent the agent completing "
                            "a mitigation, remediation, or recovery action. Respond "
                            "ONLY with the requested JSON object."
                        ),
                    )
                    self.token_usage.add(token_usage)
                    if isinstance(llm_resp, str):
                        try:
                            llm_resp = json.loads(llm_resp)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if isinstance(llm_resp, dict) and llm_resp.get("is_mitigation_event"):
                        result["agent_fault_mitigation_time"] = mitigated_at
                        logger.info(
                            "Bucket mitigated_at (%s) validated by LLM as a "
                            "mitigation event: %s",
                            mitigated_at,
                            llm_resp.get("reason", ""),
                        )
                    else:
                        result["agent_fault_mitigation_time"] = None
                        reason = llm_resp.get("reason", "") if isinstance(llm_resp, dict) else ""
                        logger.warning(
                            "Bucket mitigated_at (%s) rejected by LLM — matching "
                            "event(s) do not represent a mitigation event: %s",
                            mitigated_at,
                            reason,
                        )
                except Exception as e:
                    logger.error("LLM mitigation validation failed: %s", e)
                    result["agent_fault_mitigation_time"] = None
            else:
                logger.warning(
                    "Bucket mitigated_at (%s) has no matching event endTime.",
                    mitigated_at,
                )
                result["agent_fault_mitigation_time"] = None

        return result

    async def _extract_batch_quantitative(
        self,
        batch: List[Dict[str, Any]],
        batch_number: int,
        total_batches: int,
    ) -> Dict[str, Any]:
        """Extract partial quantitative metrics from a single batch."""
        prepared_spans = [self._prepare_span_for_llm(span) for span in batch]

        user_message = f"""Analyze batch {batch_number} of {total_batches} and extract quantitative metrics.

Remember: 
- Each span's `input`, `output`, and `metadata` fields are JSON strings that must be parsed to access nested fields like `action`, `tokens_consumed`, `detected_at`, `experiment_type`, `pod`, `recovery_time_seconds`, etc.
- Each span may have a `usage` field (JSON string) containing token counts. For GENERATION spans, parse the usage field to extract `input` (input tokens) and `output` (output tokens) values.

Trace spans:
```json
{json.dumps(prepared_spans, indent=2)}
```

Extract all quantitative metrics from this batch as a JSON object. Parse every span's input, output, metadata, and usage JSON strings to find timestamps, token counts, tool calls, and fault information."""

        prompt = self._build_quantitative_batch_prompt(batch_number, total_batches)

        try:
            result, token_usage = await self.llm_client.with_structured_output(
                model_name="gpt-4o",
                messages=user_message,
                output_format=LLMQuantitativeExtraction,
                max_tokens=3000,
                system_prompt=prompt,
            )
            self.token_usage.add(token_usage)

            if isinstance(result, LLMQuantitativeExtraction):
                return result.model_dump(exclude_none=True, mode="json")
            elif isinstance(result, dict):
                return result
            return {"response": str(result)}

        except Exception as e:
            logger.warning(f"Error extracting batch {batch_number}: {e}")
            return {}

    async def _aggregate_quantitative_metrics(
        self,
        partial_metrics: List[Dict[str, Any]],
        total_spans: int,
        spans: List[Dict[str, Any]],
    ) -> LLMQuantitativeExtraction:
        """Aggregate partial metrics from all batches into final quantitative metrics."""
        # Step 0: Identify detection/mitigation spans using LLM
        logger.info("Identifying detection and mitigation spans using LLM...")
        span_times = await self._identify_detection_mitigation_spans(spans)

        # Step 0b: Validate bucket timestamps
        logger.info("Validating bucket timestamps...")
        validated_timestamps = await self._validate_bucket_timestamps_with_llm(spans)
        span_times.update(validated_timestamps)

        # Step 1: Aggregate all numeric fields in code
        prescan = self.quant_aggregator.prescan_spans_for_sensitive_data(spans)
        logger.info(
            "PII pre-scan: detected=%s, count=%d",
            prescan["pii_detected"],
            prescan["pii_instance_count"],
        )

        span_metrics = self.quant_aggregator.extract_token_and_tool_metrics(spans)
        logger.info(
            "Code-extracted metrics: input_tokens=%d output_tokens=%d tool_calls=%d",
            span_metrics["input_tokens"],
            span_metrics["output_tokens"],
            len(span_metrics["tool_calls"]),
        )

        try:
            self.quant_aggregator._prescan_result = prescan
            self.quant_aggregator._span_metrics = span_metrics
            code_aggregated = self.quant_aggregator.aggregate(
                partial_metrics, total_spans, span_times, self.bucket_metadata,
            )
        except MetricsExtractorError:
            raise
        except Exception as e:
            logger.error(f"Code-level quantitative aggregation failed: {e}", exc_info=True)
            raise MetricsExtractorError(
                f"Quantitative code aggregation failed: {e}"
            ) from e

        # Step 2: Use LLM only for text field consolidation
        user_message = f"""Consolidate text fields from these partial metrics from {len(partial_metrics)} batches.
ONLY consolidate descriptive/text fields (fault_detected, injected_fault_name, injected_fault_category, detected_fault_type, fault_target_service, fault_namespace, experiment_id).
Do NOT compute any numeric values — all numbers are handled by code.

Partial data from batches:
```json
{json.dumps(partial_metrics, indent=2)}
```

Total spans in trace: {total_spans}"""

        try:
            result, token_usage = await self.llm_client.with_structured_output(
                model_name="gpt-4o",
                messages=user_message,
                output_format=LLMQuantitativeExtraction,
                max_tokens=1500,
                system_prompt=PROMPTS["quantitative_aggregation"],
            )
            self.token_usage.add(token_usage)

            if isinstance(result, LLMQuantitativeExtraction):
                llm_result = result
            elif isinstance(result, dict):
                llm_result = LLMQuantitativeExtraction.model_validate(result)
            else:
                logger.warning(f"Unexpected aggregation result type: {type(result)}")
                llm_result = self._create_default_quantitative(total_spans)

        except Exception as e:
            logger.error(f"Error in LLM text consolidation: {e}")
            llm_result = self._create_default_quantitative(total_spans)

        # Step 3: Override ALL numeric and computed fields with code-aggregated values
        for field_name, value in code_aggregated.items():
            if hasattr(llm_result, field_name) and value is not None:
                setattr(llm_result, field_name, value)

        return llm_result

    async def extract_quantitative_metrics(
        self, spans: List[Dict[str, Any]]
    ) -> LLMQuantitativeExtraction:
        """Extract quantitative metrics from spans using batched LLM processing."""
        self._init_llm_client()

        batches = self._create_batches(spans)
        total_batches = len(batches)

        logger.info(f"Processing {len(spans)} spans in {total_batches} batches")

        partial_metrics = []
        for i, batch in enumerate(batches, 1):
            logger.info(f"Processing quantitative batch {i}/{total_batches}")
            batch_metrics = await self._extract_batch_quantitative(
                batch, i, total_batches
            )
            partial_metrics.append(batch_metrics)

        logger.info("Aggregating quantitative metrics from all batches")
        return await self._aggregate_quantitative_metrics(partial_metrics, len(spans), spans)

    async def _extract_batch_qualitative(
        self,
        batch: List[Dict[str, Any]],
        batch_number: int,
        total_batches: int,
    ) -> Dict[str, Any]:
        """Extract partial qualitative observations from a single batch."""
        prepared_spans = [self._prepare_span_for_llm(span) for span in batch]

        user_message = f"""Analyze batch {batch_number} of {total_batches} and extract qualitative observations:

```json
{json.dumps(prepared_spans, indent=2)}
```

Extract any qualitative observations you can make from this batch."""

        prompt = self._build_qualitative_batch_prompt(batch_number, total_batches)

        try:
            result, token_usage = await self.llm_client.with_structured_output(
                model_name="gpt-4o",
                messages=user_message,
                output_format=LLMQualitativeExtraction,
                max_tokens=10000,
                system_prompt=prompt,
            )
            self.token_usage.add(token_usage)

            if isinstance(result, LLMQualitativeExtraction):
                return result.model_dump(exclude_none=True, mode="json")
            elif isinstance(result, dict):
                return result
            return {"response": str(result)}

        except Exception as e:
            logger.warning(f"Error extracting qualitative batch {batch_number}: {e}")
            return {}

    async def _aggregate_qualitative_metrics(
        self,
        partial_observations: List[Dict[str, Any]],
        total_spans: int,
        spans: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMQualitativeExtraction:
        """Aggregate partial observations from all batches into final qualitative metrics."""
        # Step 1: Pre-compute numeric values in code
        try:
            code_aggregated = self.qual_aggregator.aggregate(partial_observations)
        except MetricsExtractorError:
            raise
        except Exception as e:
            logger.error(f"Code-level qualitative aggregation failed: {e}", exc_info=True)
            raise MetricsExtractorError(
                f"Qualitative code aggregation failed: {e}"
            ) from e

        # Step 1b: override hallucination signal with per-step claim-grounding judge.
        # The bulk LLM count (rules 4(a)-(d)) is replaced with a deterministic,
        # evidence-anchored validator. Output shape (count + total + score) is unchanged
        # so the rest of the pipeline (Phase 2 aggregator, Phase 3 cert builder) is unaffected.
        if spans:
            try:
                self._init_llm_client()
                trace_dict = {"events": spans}
                h_count, r_count, h_notes = await judge_trace(
                    self.llm_client, trace_dict, model="gpt-4o"
                )
                if r_count > 0:
                    code_aggregated["hallucination_count"] = h_count
                    code_aggregated["total_response_count"] = r_count
                    code_aggregated["hallucination_score"] = round(h_count / r_count, 2)
                    if h_notes:
                        code_aggregated["hallucination_notes"] = h_notes
                    logger.info(
                        f"Hallucination validator: {h_count}/{r_count} claims ungrounded "
                        f"(score={code_aggregated['hallucination_score']})"
                    )
                else:
                    logger.info("Hallucination validator: no reasoning steps found, retaining bulk count")
            except Exception as e:
                logger.warning(f"Hallucination validator failed, falling back to bulk count: {e}")

        # Step 1c: override reasoning_quality_score with per-step multi-dimensional judge.
        # Replaces the old LLM-averaged batch score with a structured, evidence-anchored
        # four-dimension assessment (coherence, depth, tool relevance, clarity).
        if spans:
            try:
                self._init_llm_client()
                trace_dict = {"events": spans}
                rj = await judge_reasoning(self.llm_client, trace_dict, model="gpt-4o")
                if rj.mean_composite > 0:
                    code_aggregated["reasoning_quality_score"] = rj.mean_composite
                    code_aggregated["reasoning_logical_coherence"] = rj.mean_logical_coherence
                    code_aggregated["reasoning_diagnostic_depth"] = rj.mean_diagnostic_depth
                    code_aggregated["reasoning_tool_usage_relevance"] = rj.mean_tool_usage_relevance
                    code_aggregated["reasoning_explanation_clarity"] = rj.mean_explanation_clarity
                    if rj.overall_notes:
                        code_aggregated["reasoning_quality_notes"] = rj.overall_notes
            except Exception as e:
                logger.warning(f"Reasoning judge failed, LLM batch score will be used: {e}")

        # Step 2: Use LLM only for text/narrative synthesis
        user_message = f"""Synthesize text and narrative fields from these observations from {len(partial_observations)} batches.
ONLY synthesize text/narrative fields. Do NOT compute any numeric scores or averages — all numbers are handled by code.

Observations from batches:
```json
{json.dumps(partial_observations, indent=2)}
```

Total spans analyzed: {total_spans}

Create a comprehensive qualitative assessment by combining the narrative observations."""

        try:
            result, token_usage = await self.llm_client.with_structured_output(
                model_name="gpt-4o",
                messages=user_message,
                output_format=LLMQualitativeExtraction,
                max_tokens=10000,
                system_prompt=PROMPTS["qualitative_aggregation"],
            )
            self.token_usage.add(token_usage)

            if isinstance(result, LLMQualitativeExtraction):
                llm_result = result
            elif isinstance(result, dict):
                llm_result = LLMQualitativeExtraction.model_validate(result)
            else:
                logger.warning(
                    f"Unexpected qualitative aggregation result type: {type(result)}"
                )
                llm_result = self._create_default_qualitative()

        except Exception as e:
            logger.error(f"Error aggregating qualitative metrics: {e}")
            llm_result = self._create_default_qualitative()

        # Step 3: Override numeric fields with code-computed values
        for field_name, value in code_aggregated.items():
            if hasattr(llm_result, field_name) and value is not None:
                setattr(llm_result, field_name, value)

        return llm_result

    async def extract_qualitative_metrics(
        self, spans: List[Dict[str, Any]]
    ) -> LLMQualitativeExtraction:
        """Extract qualitative metrics from spans using batched LLM processing."""
        self._init_llm_client()

        batches = self._create_batches(spans)
        total_batches = len(batches)

        logger.info(
            f"Processing {len(spans)} spans in {total_batches} batches for qualitative analysis"
        )

        partial_observations = []
        for i, batch in enumerate(batches, 1):
            logger.info(f"Processing qualitative batch {i}/{total_batches}")
            batch_observations = await self._extract_batch_qualitative(
                batch, i, total_batches
            )
            partial_observations.append(batch_observations)

        logger.info("Aggregating qualitative observations from all batches")
        return await self._aggregate_qualitative_metrics(
            partial_observations, len(spans), spans=spans
        )

    @staticmethod
    def _create_default_quantitative(total_spans: int) -> LLMQuantitativeExtraction:
        """Create a default quantitative extraction when LLM fails."""
        return LLMQuantitativeExtraction(
            trajectory_steps=total_spans,
            fault_detected="Unknown - extraction failed",
            detection_success=0,
            input_tokens=0,
            output_tokens=0,
            tool_calls=[],
        )

    @staticmethod
    def _create_default_qualitative() -> LLMQualitativeExtraction:
        """Create a default qualitative extraction when LLM fails."""
        return LLMQualitativeExtraction(
            rai_check_status="Not Evaluated",
            security_compliance_status="Not Evaluated",
            agent_summary="Extraction failed - unable to analyze trace",
        )

    async def extract_metrics_async(
        self, file_path: str, store_to_mongodb: bool = False
    ) -> ExtractionResult:
        """
        Main async extraction method - extracts both quantitative and qualitative metrics.

        Uses batch processing to handle large traces without truncation.
        Tracks and returns token usage from all LLM calls.
        When bucket metadata is available (either provided at init or extracted from
        a bucket-format trace file), ground truth context is injected into LLM prompts
        and bucket metadata fields are used for timestamp baselines and ground-truth
        comparison.
        """
        try:
            self.token_usage = TokenUsage()

            logger.info(f"Loading trace file: {file_path}")
            spans = self.load_trace_file(file_path)
            logger.info(f"Loaded {len(spans)} spans")

            if self.bucket_metadata:
                logger.info(
                    f"Using bucket metadata: fault_id={self.bucket_metadata.get('fault_id')}, "
                    f"fault_name={self.bucket_metadata.get('fault_name')}, "
                    f"injection_timestamp={self.bucket_metadata.get('injection_timestamp')}"
                )
            else:
                logger.info(
                    "No bucket metadata loaded. Proceeding without ground truth context."
                )

            logger.info("Extracting quantitative metrics using batched LLM processing...")
            quantitative = await self.extract_quantitative_metrics(spans)

            logger.info("Extracting qualitative metrics using batched LLM processing...")
            qualitative = await self.extract_qualitative_metrics(spans)

            logger.info(
                f"Extraction complete. Token usage - Input: {self.token_usage.input_tokens}, "
                f"Output: {self.token_usage.output_tokens}, Total: {self.token_usage.total_tokens}"
            )

            mongodb_document_id = None
            if store_to_mongodb:
                metadata = {
                    "trace_file": str(Path(file_path).name),
                    "total_spans": len(spans),
                    "extraction_token_usage": self.token_usage.to_dict(),
                }
                if self.bucket_metadata:
                    metadata["bucket_metadata"] = {
                        "fault_id": self.bucket_metadata.get("fault_id"),
                        "fault_name": self.bucket_metadata.get("fault_name"),
                        "severity": self.bucket_metadata.get("severity"),
                        "injection_timestamp": self.bucket_metadata.get("injection_timestamp"),
                    }
                try:
                    mongodb_document_id = self.store_metrics_to_mongodb(
                        quantitative=quantitative,
                        qualitative=qualitative,
                        metadata=metadata,
                    )
                except Exception as e:
                    logger.error(f"Failed to store metrics to MongoDB: {e}")

            return ExtractionResult(
                quantitative=quantitative,
                qualitative=qualitative,
                token_usage=self.token_usage,
                mongodb_document_id=mongodb_document_id,
            )
        except MetricsExtractorError:
            raise
        except Exception as e:
            logger.error(f"extract_metrics_async failed for {file_path}: {e}", exc_info=True)
            raise MetricsExtractorError(
                f"Metrics extraction failed for {file_path}: {e}"
            ) from e

    def extract_metrics(
        self, file_path: str, store_to_mongodb: bool = False
    ) -> ExtractionResult:
        """Synchronous wrapper for extract_metrics_async."""
        return asyncio.run(self.extract_metrics_async(file_path, store_to_mongodb))


async def extract_metrics_from_trace_async(
    trace_file_path: str,
    config: Optional[Dict[str, Any]] = None,
    bucket_metadata: Optional[Dict[str, Any]] = None,
    store_to_mongodb: bool = False,
) -> ExtractionResult:
    """
    Async convenience function to extract metrics from a trace file using LLM.

    Args:
        trace_file_path: Path to the trace bucket JSON file (or plain span list).
        config: Optional config dictionary.
        bucket_metadata: Optional bucket metadata dict. If the trace file is a
            bucket JSON with an ``events`` key, metadata is extracted automatically.
        store_to_mongodb: If True, store extracted metrics to MongoDB.

    Returns:
        ExtractionResult containing quantitative, qualitative metrics and token usage.
    """
    extractor = TraceMetricsExtractor(config, bucket_metadata=bucket_metadata)
    return await extractor.extract_metrics_async(trace_file_path, store_to_mongodb)


def extract_metrics_from_trace(
    trace_file_path: str,
    config: Optional[Dict[str, Any]] = None,
    bucket_metadata: Optional[Dict[str, Any]] = None,
    store_to_mongodb: bool = False,
) -> ExtractionResult:
    """
    Convenience function to extract metrics from a trace file using LLM.

    Args:
        trace_file_path: Path to the trace bucket JSON file (or plain span list).
        config: Optional config dictionary.
        bucket_metadata: Optional bucket metadata dict. If the trace file is a
            bucket JSON with an ``events`` key, metadata is extracted automatically.
        store_to_mongodb: If True, store extracted metrics to MongoDB.

    Returns:
        ExtractionResult containing quantitative, qualitative metrics and token usage.
    """
    extractor = TraceMetricsExtractor(config, bucket_metadata=bucket_metadata)
    return extractor.extract_metrics(trace_file_path, store_to_mongodb)


def main(file_path: str, store=True):
    result = extract_metrics_from_trace(file_path, store_to_mongodb=store)

    print("\n=== Quantitative Metrics ===")
    print(result.quantitative.model_dump_json(indent=2))

    print("\n=== Qualitative Metrics ===")
    print(result.qualitative.model_dump_json(indent=2))

    print("\n=== Token Usage for Extraction ===")
    print(json.dumps(result.token_usage.to_dict(), indent=2))

    if result.mongodb_document_id:
        print(f"\n=== Stored to MongoDB ===")
        print(f"Document ID: {result.mongodb_document_id}")


# Example usage
if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Extract metrics from fault bucket trace files"
    )
    parser.add_argument(
        "--trace-file-name",
        type=str,
        help="Name of the trace bucket file",
        default=None,
    )
    parser.add_argument(
        "--trace-directory",
        type=str,
        help="Directory containing trace bucket files",
        default=None,
    )
    parser.add_argument(
        "--store",
        action="store_true",
        help="Store extracted metrics to MongoDB",
    )

    args = parser.parse_args()

    if len(sys.argv) < 2:
        print(
            "Usage: python metrics_extractor_from_trace.py "
            "--trace-file-name <trace_bucket.json> [--store]"
        )
        sys.exit(1)

    trace_path = args.trace_file_name or None
    trace_dir = args.trace_directory or None
    store_flag = args.store or False

    try:
        if trace_path:
            main(trace_path, store=store_flag)
        elif trace_dir:
            for file_name in os.listdir(trace_dir):
                file_path = os.path.join(trace_dir, file_name)
                if os.path.isfile(file_path):
                    main(file_path, store=store_flag)
        else:
            print("Error: No trace file or directory specified")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        sys.exit(1)
