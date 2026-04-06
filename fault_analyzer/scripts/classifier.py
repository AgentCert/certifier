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
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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
        self._model_name = classifier_config.get("model_name", "extraction_model")
        self._temperature = classifier_config.get("temperature", 0.1)
        self._max_tokens = classifier_config.get("max_tokens", 4000)
        self._fallback_confidence = classifier_config.get("fallback_confidence", 0.3)
        self._system_prompt = _load_prompt()

    def _get_llm_client(self) -> Any:
        """Get or create the AzureLLMClient singleton."""
        if self._llm_client is None:
            if AzureLLMClient is None:
                raise RuntimeError(
                    "AzureLLMClient is not available. "
                    "Install the required dependencies."
                )
            self._llm_client = AzureLLMClient(config=self.config)
        return self._llm_client

    def build_user_message(
        self,
        batch: List[Dict[str, Any]],
        known_faults: Dict[str, FaultBucket],
        injected_faults: Dict[str, FaultBucket],
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

        # Injected faults context (ground truth from chaos engineering)
        injected_context = []
        for fid, bucket in injected_faults.items():
            injected_context.append({
                "fault_id": fid,
                "fault_name": bucket.fault_name,
                "ground_truth": bucket.ground_truth,
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

        message = "## Known Faults\n\n"
        if faults_context:
            message += f"```json\n{json.dumps(faults_context, indent=2)}\n```\n\n"
        else:
            message += "No faults have been identified yet. Look for fault detection events in this batch.\n\n"

        if injected_context:
            message += (
                "## Injected Faults (Ground Truth)\n\n"
                f"```json\n{json.dumps(injected_context, indent=2)}\n```\n\n"
                "These faults were injected by the chaos engineering platform. "
                "The agent should detect and remediate them during its investigation.\n\n"
            )

        message += (
            "## Event Batch\n\n"
            f"```json\n{json.dumps(events_for_llm, indent=2)}\n```\n\n"
            "Classify each event. Identify any events that represent new fault "
            "detections or fault mitigations. "
            "Return a JSON object with a 'classifications' array."
        )
        return message

    async def classify_batch(
        self,
        batch: List[Dict[str, Any]],
        known_faults: Dict[str, FaultBucket],
        injected_faults: Dict[str, FaultBucket],
    ) -> List[EventClassification]:
        """Send a batch of events to the LLM for classification.

        Falls back to assigning all events to every known fault on failure.
        """
        try:
            client = self._get_llm_client()
            user_message = self.build_user_message(
                batch, known_faults, injected_faults
            )

            result, usage = await client.with_structured_output(
                model_name=self._model_name,
                messages=[{"role": "user", "content": user_message}],
                output_format=BatchClassificationResult,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                system_prompt=self._system_prompt,
            )

            # Track tokens
            if isinstance(usage, dict):
                self.total_input_tokens += usage.get("input_tokens", 0)
                self.total_output_tokens += usage.get("output_tokens", 0)

            if isinstance(result, BatchClassificationResult):
                return result.classifications
            elif isinstance(result, dict) and "classifications" in result:
                return [
                    EventClassification.model_validate(c)
                    for c in result["classifications"]
                ]
            else:
                logger.warning(
                    "LLM returned unexpected format, using fallback classification"
                )
                return self.fallback_classify(batch, known_faults)

        except Exception as e:
            logger.error(f"LLM classification failed: {e}. Using fallback.")
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
