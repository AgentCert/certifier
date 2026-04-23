"""
LLM-based event classifier for the Fault Bucketing pipeline.

Sends batches of trace events to Azure OpenAI for classification into
per-fault buckets: detects new faults, identifies mitigations, and assigns
events to known faults.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from utils.custom_errors import MyCustomError, FaultClassifierError

import yaml

from fault_analyzer.schema.data_models import (
    BatchClassificationResult,
    EventClassification,
    FaultBucket,
)

# Optional imports — gracefully handle if not available
try:
    from utils.azure_openai_util import AzureLLMClient
    from utils.setup_logging import logger
except ImportError:
    AzureLLMClient = None
    logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _MODULE_DIR / "prompt" / "prompt.yml"
_CONFIG_PATH = _MODULE_DIR / "config" / "fault_bucketing_config.json"


def _load_prompt() -> str:
    """Load the fault classifier system prompt from the YAML file."""
    with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
        prompts = yaml.safe_load(f)
    return prompts["fault_classifier"]["system_prompt"]


def _load_module_config() -> Dict[str, Any]:
    """Load the fault bucketing module configuration from JSON."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError as exc:
        raise FaultClassifierError(
            f"Could not read classifier module config: {_CONFIG_PATH}",
            original_exception=exc,
        ) from exc
    except json.JSONDecodeError as exc:
        raise FaultClassifierError(
            f"Classifier module config is not valid JSON: {_CONFIG_PATH}",
            original_exception=exc,
        ) from exc


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class FaultEventClassifier:
    """Classifies trace events into fault buckets using an LLM."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._llm_client: Optional[Any] = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        # Load module-level settings
        module_config = _load_module_config()
        classifier_config = module_config.get("classifier", {})
        self._model_name = classifier_config.get("model_name", "gpt-4o")
        self._temperature = classifier_config.get("temperature", 0.1)
        self._max_tokens = classifier_config.get("max_tokens", 4000)
        self._fallback_confidence = classifier_config.get("fallback_confidence", 0.3)
        self._system_prompt = _load_prompt()

    def _get_llm_client(self) -> Any:
        """Get or create the AzureLLMClient singleton."""
        if self._llm_client is None:
            if AzureLLMClient is None:
                raise FaultClassifierError(
                    "AzureLLMClient is not available. Install the required dependencies."
                )
            try:
                self._llm_client = AzureLLMClient(config=self.config)
            except MyCustomError:
                raise
            except Exception as exc:
                raise FaultClassifierError(
                    "Failed to initialize AzureLLMClient",
                    original_exception=exc,
                ) from exc
        return self._llm_client

    def build_user_message(
        self,
        batch: List[Dict[str, Any]],
        known_faults: Dict[str, FaultBucket],
    ) -> str:
        """Build the user message for the LLM classifier."""

        # Known faults context (active + closed)
        faults_context = []
        for fid, bucket in known_faults.items():
            faults_context.append({
                "fault_id": fid,
                "fault_name": bucket.fault_name,
                "severity": bucket.severity,
                "target_pod": bucket.target_pod,
                "namespace": bucket.namespace,
                "detection_signals": bucket.detection_signals,
            })

        # Compact event representation for the batch
        events_for_llm = []
        for evt in batch:
            events_for_llm.append({
                "event_id": evt.get("id"),
                "type": evt.get("type"),
                "name": evt.get("name"),
                "startTime": evt.get("startTime"),
                "endTime": evt.get("endTime"),
                "parentObservationId": evt.get("parentObservationId"),
                "input": evt.get("input"),
                "output": evt.get("output"),
                "metadata": evt.get("metadata"),
            })

        try:
            faults_context_json = json.dumps(faults_context, indent=2, default=str)
            events_json = json.dumps(events_for_llm, indent=2, default=str)
        except (TypeError, ValueError) as exc:
            raise FaultClassifierError(
                "Failed to serialize batch or known_faults context to JSON",
                original_exception=exc,
            ) from exc

        message = "## Known Faults\n\n"
        if faults_context:
            message += f"```json\n{faults_context_json}\n```\n\n"
        else:
            message += "No faults have been identified yet. Look for fault detection events in this batch.\n\n"

        message += (
            "## Event Batch\n\n"
            f"```json\n{events_json}\n```\n\n"
            "Classify each event. Identify any events that represent new fault "
            "detections or fault mitigations. "
            "Return a JSON object with a 'classifications' array."
        )
        return message

    async def classify_batch(
        self,
        batch: List[Dict[str, Any]],
        known_faults: Dict[str, FaultBucket],
    ) -> List[EventClassification]:
        """Send a batch of events to the LLM for classification.

        Error-handling policy:
        - Config / serialization bugs in setup → raise ``FaultClassifierError``
          (these indicate bugs that the fallback would mask).
        - Transient LLM / network / API errors → log + fallback_classify.
        - Unexpected LLM output or parse errors → log + fallback_classify.
        """
        # ---- Stage 1: setup (client + message) ----
        # Config / serialization bugs here MUST NOT be hidden by the fallback.
        try:
            client = self._get_llm_client()
            user_message = self.build_user_message(batch, known_faults)
        except MyCustomError:
            raise
        except Exception as exc:
            raise FaultClassifierError(
                "Failed to prepare classification request",
                original_exception=exc,
            ) from exc

        # ---- Stage 2: LLM call (transient failures → fallback) ----
        try:
            result, usage = await client.with_structured_output(
                model_name=self._model_name,
                messages=[{"role": "user", "content": user_message}],
                output_format=BatchClassificationResult,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                system_prompt=self._system_prompt,
            )
        except MyCustomError as exc:
            logger.error(
                f"LLM classification failed (custom error): {exc}. Using fallback."
            )
            return self.fallback_classify(batch, known_faults)
        except Exception as exc:
            logger.error(
                f"LLM classification failed: {exc}. Using fallback.",
                exc_info=True,
            )
            return self.fallback_classify(batch, known_faults)

        # ---- Stage 3: parse result + track tokens (failures → fallback) ----
        try:
            if isinstance(usage, dict):
                self.total_input_tokens += int(usage.get("input_tokens", 0) or 0)
                self.total_output_tokens += int(usage.get("output_tokens", 0) or 0)

            if isinstance(result, BatchClassificationResult):
                return result.classifications

            if isinstance(result, dict) and "classifications" in result:
                return [
                    EventClassification.model_validate(c)
                    for c in result["classifications"]
                ]

            logger.warning(
                "LLM returned unexpected format, using fallback classification"
            )
            return self.fallback_classify(batch, known_faults)

        except MyCustomError as exc:
            logger.error(
                f"Parsing LLM output failed (custom error): {exc}. Using fallback."
            )
            return self.fallback_classify(batch, known_faults)
        except Exception as exc:
            logger.error(
                f"Failed to parse LLM classification output or track tokens: {exc}. "
                f"Using fallback.",
                exc_info=True,
            )
            return self.fallback_classify(batch, known_faults)


    def fallback_classify(
        self,
        batch: List[Dict[str, Any]],
        known_faults: Dict[str, FaultBucket],
    ) -> List[EventClassification]:
        """Assign every event to ALL known faults as a conservative fallback."""
        all_fault_ids = list(known_faults.keys())
        return [
            EventClassification(
                event_id=evt.get("id", "unknown"),
                related_faults=all_fault_ids,
                confidence=self._fallback_confidence,
            )
            for evt in batch
        ]
