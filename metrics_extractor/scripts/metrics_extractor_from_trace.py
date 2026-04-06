"""
Metrics extractor from Langfuse trace files.
Extracts LLMQuantitativeExtraction and LLMQualitativeExtraction metrics.
Uses LLM to interpret trace data generically - works with traces having similar keys
but different value terminologies.

Uses batch processing to handle large traces without truncation.
Integrates fault_configuration.json for ground-truth comparison and timestamp baselines.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from metrics_extractor.schema.metrics_model import (
    LLMQualitativeExtraction,
    LLMQuantitativeExtraction,
)

from metrics_extractor.scripts.span_aggregator import (
    QualitativeAggregator,
    QuantitativeAggregator,
)
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
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_prompts() -> Dict[str, str]:
    """Load prompt templates from prompts.yml."""
    with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


PROMPTS = _load_prompts()
MODULE_CONFIG = _load_module_config()


class TraceMetricsExtractor:
    """
    Extracts metrics from Langfuse trace files using LLM.

    This extractor is generic and works with traces having similar key structures
    but different value terminologies. It uses an LLM to interpret the trace data
    and extract meaningful metrics.

    Uses batch processing to handle large traces without content truncation.
    Integrates fault_configuration.json for ground-truth comparison.
    """

    BATCH_SIZE = MODULE_CONFIG.get("extractor", {}).get("batch_size", 15)

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        fault_config_path: Optional[str] = None,
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
        self.fault_config: Optional[Dict[str, Any]] = None
        self.quant_aggregator = QuantitativeAggregator()
        self.qual_aggregator = QualitativeAggregator()
        if fault_config_path:
            self.fault_config = self._load_fault_config(fault_config_path)

    @staticmethod
    def _load_fault_config(fault_config_path: str) -> Optional[Dict[str, Any]]:
        """Load and parse the fault configuration JSON file."""
        path = Path(fault_config_path)
        if not path.exists():
            logger.warning(
                f"Fault configuration file not found: {fault_config_path}. "
                "Proceeding without ground truth context."
            )
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)
            logger.info(
                f"Loaded fault configuration: fault_id={config.get('fault_id')}, "
                f"fault_name={config.get('fault_name')}"
            )
            return config
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                f"Failed to parse fault configuration file: {e}. "
                "Proceeding without ground truth context."
            )
            return None

    def _get_ground_truth(self) -> Optional[Dict[str, Any]]:
        if self.fault_config:
            return self.fault_config.get("ground_truth")
        return None

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

        fault_config_context = ""
        if self.fault_config:
            injection_ts = self.fault_config.get("injection_timestamp")
            fault_name = self.fault_config.get("fault_name")
            context_parts = ["## Fault Configuration Context (from fault_configuration.json)"]
            if injection_ts:
                context_parts.append(
                    f"- **Fault injection timestamp**: {injection_ts} — use this as the authoritative fault_injection_time if the trace does not contain an explicit experiment_start timestamp."
                )
            if fault_name:
                context_parts.append(
                    f"- **Fault name/type**: {fault_name}"
                )
            fault_config_section = self.fault_config.get("fault_configuration", {})
            target_ns = fault_config_section.get("target_namespace")
            target_svc = fault_config_section.get("target_service")
            if target_ns:
                context_parts.append(f"- **Target namespace**: {target_ns}")
            if target_svc:
                context_parts.append(f"- **Target service**: {target_svc}")
            fault_config_context = "\n".join(context_parts)

        prompt = PROMPTS["quantitative_batch_extraction"].replace(
            "{{batch_number}}", str(batch_number)
        ).replace("{{total_batches}}", str(total_batches))

        return prompt.format(
            ground_truth_instructions=gt_instructions,
            fault_config_context=fault_config_context,
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
        """Load and parse trace JSON file."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Trace file not found: {file_path}")

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

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
        }

    async def _identify_detection_mitigation_spans(
        self,
        spans: List[Dict[str, Any]],
    ) -> Dict[str, Optional[str]]:
        """Use LLM to identify the first detection and final mitigation spans."""
        self._init_llm_client()

        sorted_spans = sorted(spans, key=lambda x: x.get("startTime", ""))

        span_summaries = []
        span_start_times: Dict[str, str] = {}
        for span in sorted_spans:
            span_id = span.get("id", "")
            start_time = span.get("startTime", "")
            span_start_times[span_id] = start_time

            metadata_raw = span.get("metadata", "")
            try:
                metadata = (
                    json.loads(metadata_raw)
                    if isinstance(metadata_raw, str)
                    else (metadata_raw or {})
                )
            except (json.JSONDecodeError, TypeError):
                metadata = {}

            input_raw = span.get("input", "")
            try:
                input_data = (
                    json.loads(input_raw)
                    if isinstance(input_raw, str)
                    else (input_raw or {})
                )
            except (json.JSONDecodeError, TypeError):
                input_data = {}

            output_raw = span.get("output", "")
            output_summary = str(output_raw)[:300] if output_raw else ""

            span_summaries.append({
                "id": span_id,
                "name": span.get("name", ""),
                "type": span.get("type", ""),
                "startTime": start_time,
                "action": metadata.get("action", ""),
                "method": metadata.get("method", ""),
                "input_summary": str(input_data)[:400],
                "output_summary": output_summary,
            })

        user_message = (
            f"Analyze these {len(span_summaries)} trace spans (chronologically ordered) "
            f"and identify:\n"
            f"1. The span where the agent FIRST detected/confirmed the fault\n"
            f"2. The span where the agent completed the FINAL remediation/mitigation\n\n"
            f"Span summaries:\n```json\n{json.dumps(span_summaries, indent=2)}\n```\n\n"
            f'Return a JSON object with "detection_span_id" and "mitigation_span_id".'
        )

        try:
            result, token_usage = await self.llm_client.call_llm(
                model_name="extraction_model",
                messages=user_message,
                max_tokens=500,
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
                    f"at {span_start_times[detection_id]}"
                )
            elif detection_id:
                logger.warning(
                    f"Detection span ID '{detection_id}' not found in trace spans"
                )

            if mitigation_id and mitigation_id in span_start_times:
                times["agent_fault_mitigation_time"] = span_start_times[mitigation_id]
                logger.info(
                    f"LLM identified mitigation span: {mitigation_id} "
                    f"at {span_start_times[mitigation_id]}"
                )
            elif mitigation_id:
                logger.warning(
                    f"Mitigation span ID '{mitigation_id}' not found in trace spans"
                )

            return times

        except Exception as e:
            logger.error(f"Error identifying detection/mitigation spans: {e}")
            return {}

    async def _extract_batch_quantitative(
        self,
        batch: List[Dict[str, Any]],
        batch_number: int,
        total_batches: int,
    ) -> Dict[str, Any]:
        """Extract partial quantitative metrics from a single batch."""
        prepared_spans = [self._prepare_span_for_llm(span) for span in batch]

        user_message = f"""Analyze batch {batch_number} of {total_batches} and extract quantitative metrics.

Remember: each span's `input`, `output`, and `metadata` fields are JSON strings that must be parsed to access nested fields like `action`, `tokens_consumed`, `detected_at`, `experiment_type`, `pod`, `recovery_time_seconds`, etc.

Trace spans:
```json
{json.dumps(prepared_spans, indent=2)}
```

Extract all quantitative metrics from this batch as a JSON object. Parse every span's input, output, and metadata JSON strings to find timestamps, token counts, tool calls, and fault information."""

        prompt = self._build_quantitative_batch_prompt(batch_number, total_batches)

        try:
            result, token_usage = await self.llm_client.call_llm(
                model_name="extraction_model",
                messages=user_message,
                max_tokens=3000,
                system_prompt=prompt,
            )
            self.token_usage.add(token_usage)

            if isinstance(result, dict):
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

        # Step 1: Aggregate all numeric fields in code
        code_aggregated = self.quant_aggregator.aggregate(
            partial_metrics, total_spans, span_times, self.fault_config
        )

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
                model_name="extraction_model",
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
            result, token_usage = await self.llm_client.call_llm(
                model_name="extraction_model",
                messages=user_message,
                max_tokens=10000,
                system_prompt=prompt,
            )
            self.token_usage.add(token_usage)

            if isinstance(result, dict):
                return result
            return {"response": str(result)}

        except Exception as e:
            logger.warning(f"Error extracting qualitative batch {batch_number}: {e}")
            return {}

    async def _aggregate_qualitative_metrics(
        self,
        partial_observations: List[Dict[str, Any]],
        total_spans: int,
    ) -> LLMQualitativeExtraction:
        """Aggregate partial observations from all batches into final qualitative metrics."""
        # Step 1: Pre-compute numeric values in code
        code_aggregated = self.qual_aggregator.aggregate(partial_observations)

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
                model_name="extraction_model",
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
            partial_observations, len(spans)
        )

    @staticmethod
    def _create_default_quantitative(total_spans: int) -> LLMQuantitativeExtraction:
        """Create a default quantitative extraction when LLM fails."""
        return LLMQuantitativeExtraction(
            trajectory_steps=total_spans,
            fault_detected="Unknown - extraction failed",
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
        When a fault configuration is loaded, ground truth context is injected into
        LLM prompts and fault config fields override trace-extracted values.
        """
        self.token_usage = TokenUsage()

        logger.info(f"Loading trace file: {file_path}")
        spans = self.load_trace_file(file_path)
        logger.info(f"Loaded {len(spans)} spans")

        if self.fault_config:
            logger.info(
                f"Using fault configuration: fault_id={self.fault_config.get('fault_id')}, "
                f"fault_name={self.fault_config.get('fault_name')}, "
                f"injection_timestamp={self.fault_config.get('injection_timestamp')}"
            )
        else:
            logger.info(
                "No fault configuration loaded. Proceeding without ground truth context."
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
            if self.fault_config:
                metadata["fault_config"] = {
                    "fault_id": self.fault_config.get("fault_id"),
                    "fault_name": self.fault_config.get("fault_name"),
                    "fault_category": self.fault_config.get("fault_category"),
                    "injection_timestamp": self.fault_config.get("injection_timestamp"),
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

    def extract_metrics(
        self, file_path: str, store_to_mongodb: bool = False
    ) -> ExtractionResult:
        """Synchronous wrapper for extract_metrics_async."""
        return asyncio.run(self.extract_metrics_async(file_path, store_to_mongodb))


async def extract_metrics_from_trace_async(
    trace_file_path: str,
    config: Optional[Dict[str, Any]] = None,
    fault_config_path: Optional[str] = None,
    store_to_mongodb: bool = False,
) -> ExtractionResult:
    """
    Async convenience function to extract metrics from a trace file using LLM.

    Args:
        trace_file_path: Path to the Langfuse trace JSON file
        config: Optional config dictionary
        fault_config_path: Optional path to fault_configuration.json for ground truth
        store_to_mongodb: If True, store extracted metrics to MongoDB

    Returns:
        ExtractionResult containing quantitative, qualitative metrics and token usage
    """
    extractor = TraceMetricsExtractor(config, fault_config_path=fault_config_path)
    return await extractor.extract_metrics_async(trace_file_path, store_to_mongodb)


def extract_metrics_from_trace(
    trace_file_path: str,
    config: Optional[Dict[str, Any]] = None,
    fault_config_path: Optional[str] = None,
    store_to_mongodb: bool = False,
) -> ExtractionResult:
    """
    Convenience function to extract metrics from a trace file using LLM.

    Args:
        trace_file_path: Path to the Langfuse trace JSON file
        config: Optional config dictionary
        fault_config_path: Optional path to fault_configuration.json for ground truth
        store_to_mongodb: If True, store extracted metrics to MongoDB

    Returns:
        ExtractionResult containing quantitative, qualitative metrics and token usage
    """
    extractor = TraceMetricsExtractor(config, fault_config_path=fault_config_path)
    return extractor.extract_metrics(trace_file_path, store_to_mongodb)


def main(file_path: str, store=True, fault_config_path=None):
    result = extract_metrics_from_trace(file_path, store_to_mongodb=store, fault_config_path=fault_config_path)

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
        description="Generate OTEL-compliant mock traces for ITOps agent fault scenarios"
    )
    parser.add_argument(
        "--trace-file-name",
        type=str,
        help="Name of the trace file",
        default=None,
    )
    parser.add_argument(
        "--trace-directory",
        type=str,
        help="Directory containing trace files",
        default=None,
    )
    parser.add_argument(
        "--fault-config-path",
        type=str,
        help="Path to fault_configuration.json for ground truth",
    )
    parser.add_argument(
        "--store",
        action="store_true",
        help="Store extracted metrics to MongoDB",
    )

    args = parser.parse_args()

    if len(sys.argv) < 2:
        print(
            "Usage: python metrics_extractor_from_trace.py <trace_file_path> "
            "[--fault-config <fault_config.json>] [--store]"
        )
        sys.exit(1)

    trace_path = args.trace_file_name or None
    trace_dir = args.trace_directory or None
    store_flag = args.store or False
    fault_config_path = args.fault_config_path or None

    try:
        if trace_path:
            main(trace_path, store=store_flag, fault_config_path=fault_config_path)
        elif trace_dir:
            for file_name in os.listdir(trace_dir):
                file_path = os.path.join(trace_dir, file_name)
                if os.path.isfile(file_path):
                    main(file_path, store=store_flag, fault_config_path=fault_config_path)
        else:
            print("Error: No trace file or directory specified")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        sys.exit(1)
