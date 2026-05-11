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
_CONFIG_PATH = _MODULE_DIR / "config" / "fault_bucketing_config.json"


def _load_prompt(prompt_path: Path) -> str:
    """Load the fault classifier system prompt from the YAML file."""
    with open(prompt_path, "r", encoding="utf-8") as f:
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

    def __init__(
        self,
        config: Dict[str, Any],
        prompt_path: Optional[str] = None,
        fault_pruning: Optional[bool] = None,
        cache_enabled: Optional[bool] = None,
        include_event_input: Optional[bool] = None,
    ):
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

        # ----- Three independent toggles, each: ctor arg > config > hardcoded default -----
        # Toggle 1: when True, build_known_faults_block emits the compact dict
        # (~84% smaller); when False, emits the legacy verbose payload.
        if fault_pruning is None:
            fault_pruning = classifier_config.get("fault_pruning", False)
        self.fault_pruning = bool(fault_pruning)

        # Toggle 2: when True, the v2 system prompt is sent in the *system*
        # role so Azure GPT-4o auto-caches the ≥1024-token prefix and rebates
        # ~50% on cached tokens for batches 2..N. When False, the system
        # prompt is inlined into the user message (system role left empty);
        # the long stable prefix collapses and auto-cache cannot hit, which
        # is useful when measuring un-cached worst-case token cost or when
        # debugging prompt instructions that you suspect are being cached
        # past a config change.
        if cache_enabled is None:
            cache_enabled = classifier_config.get("cache_enabled", False)
        self.cache_enabled = bool(cache_enabled)

        # Toggle 3: when True, each event's `input` (the full agent reasoning /
        # tool arguments) is rendered alongside `output` in the per-event
        # block sent to the LLM. False = output-only (cheaper but discards the
        # agent's stated intent). Default True so the LLM sees both sides of
        # each event by default.
        if include_event_input is None:
            include_event_input = classifier_config.get("include_event_input", True)
        self.include_event_input = bool(include_event_input)

        # Resolve prompt path: CLI arg > constructor arg > config > hardcoded default
        if prompt_path:
            resolved = Path(prompt_path)
        else:
            config_rel = classifier_config.get("prompt_path", "prompt/v1/prompt.yml")
            resolved = _MODULE_DIR / config_rel
        self._system_prompt = _load_prompt(resolved)

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

    @staticmethod
    def _verbose_fault_context(fid: str, b: FaultBucket) -> Dict[str, Any]:
        """Pre-pruning per-fault dict (kept for ``fault_pruning=False`` mode).

        This is the original payload ``build_known_faults_block`` used to emit
        before the prune. It carries every field on the FaultBucket — including
        injection_timestamp/end, severity, sla, the full injection_metadata
        envelope, and step-level paraphrasing in ``ideal_course_of_action`` and
        ``ideal_tool_usage_trajectory``.
        """
        return {
            "fault_id": fid,
            "fault_name": b.fault_name,
            "injection_timestamp": b.injection_timestamp,
            "injection_end_timestamp": b.injection_end_timestamp,
            "injection_metadata": b.injection_metadata,
            "severity": b.severity,
            "target_pod": b.target_pod,
            "namespace": b.namespace,
            "detection_signals": b.detection_signals,
            "ground_truth": b.ground_truth,
            "ideal_course_of_action": b.ideal_course_of_action,
            "ideal_tool_usage_trajectory": b.ideal_tool_usage_trajectory,
            "sla": b.sla,
        }

    @staticmethod
    def _compact_fault_context(fid: str, b: FaultBucket) -> Dict[str, Any]:
        """Build the minimal fault-context dict the classifier actually uses.

        Drops fields the LLM doesn't need:
        - injection_timestamp / injection_end_timestamp / timing — already
          consumed by the deterministic temporal filter in
          ``FaultBucketingPipeline._temporally_active_faults`` *before* the
          LLM is called.
        - severity / sla — used by metrics extraction, not classification.
        - injection_metadata.engine_name, injection.phase, injection.verdict,
          probes.results, workflow.cohort_faults, etc. — tell us *if* the
          fault landed, not what its symptoms look like.
        - ground_truth.fault_description_goal_remediation.goal — meta
          description, no symptom keywords.
        - ideal_course_of_action[].step / .detail — verbose paraphrase of action.
        - ideal_tool_usage_trajectory[].step / .purpose / .tool /
          .tool_available — explanation, not signal.
        - agent_id / experiment_id / run_id — bookkeeping.

        Keeps the outer field names ``injection_metadata.target``,
        ``ground_truth``, ``ideal_course_of_action``,
        ``ideal_tool_usage_trajectory``, ``detection_signals`` so the
        ``prompt/v2/prompt.yml`` rule references stay valid.
        """
        # injection_metadata: keep only the target subdict (used for
        # target-disambiguation rule).
        target: Dict[str, Any] = {}
        if b.injection_metadata:
            inj_target = b.injection_metadata.get("target") or {}
            for k in ("namespace", "label", "workload_ref", "kind"):
                v = inj_target.get(k)
                if v is not None:
                    target[k] = v
        # Fall back to bucket fields when injection_metadata.target is empty.
        if not target.get("namespace") and b.namespace:
            target["namespace"] = b.namespace
        if not target.get("label") and b.target_pod:
            target["label"] = b.target_pod

        # ground_truth: keep only symptoms + remediation, lifted out of the
        # nested ``fault_description_goal_remediation`` envelope.
        gt: Dict[str, Any] = {}
        if isinstance(b.ground_truth, dict):
            inner = b.ground_truth.get("fault_description_goal_remediation") or {}
            symptoms = inner.get("symptoms") or b.ground_truth.get("symptoms")
            remediation = inner.get("remediation") or b.ground_truth.get("remediation")
            if symptoms:
                gt["symptoms"] = symptoms
            if remediation:
                gt["remediation"] = remediation

        # ideal_course_of_action: flatten to a list of action strings only.
        actions: List[str] = []
        for step in (b.ideal_course_of_action or []):
            if isinstance(step, dict):
                a = step.get("action")
                if a:
                    actions.append(a)
            elif isinstance(step, str):
                actions.append(step)

        # ideal_tool_usage_trajectory: flatten to a list of command strings only.
        commands: List[str] = []
        for step in (b.ideal_tool_usage_trajectory or []):
            if isinstance(step, dict):
                c = step.get("command")
                if c:
                    commands.append(c)
            elif isinstance(step, str):
                commands.append(step)

        compact: Dict[str, Any] = {
            "fault_id": fid,
            "fault_name": b.fault_name,
        }
        if target:
            compact["injection_metadata"] = {"target": target}
        if gt:
            compact["ground_truth"] = gt
        if actions:
            compact["ideal_course_of_action"] = actions
        if commands:
            compact["ideal_tool_usage_trajectory"] = commands
        if b.detection_signals:
            compact["detection_signals"] = list(b.detection_signals)
        return compact

    def build_known_faults_block(
        self,
        known_faults: Dict[str, FaultBucket],
    ) -> str:
        """Render the ``## Known Faults`` section as a standalone string.

        Placed in the user message (not system prompt) so the stable system
        prefix stays constant across batches and GPT-4o auto-cache can hit it.

        When ``self.fault_pruning`` is True (default) only
        classification-relevant fields are rendered (see
        ``_compact_fault_context``). Bookkeeping, SLA, injection-result
        metadata, and step-level paraphrasing are stripped — yields ~84%
        token reduction on the fault context.

        When ``self.fault_pruning`` is False the legacy verbose payload is
        rendered for A/B comparison or migration debugging (see
        ``_verbose_fault_context``).
        """
        ordered_faults = sorted(
            known_faults.items(),
            key=lambda kv: (kv[1].injection_timestamp or ""),
        )
        render = self._compact_fault_context if self.fault_pruning else self._verbose_fault_context
        faults_context = [render(fid, b) for fid, b in ordered_faults]
        block = "## Known Faults (ordered by injection_timestamp)\n\n"
        if faults_context:
            block += f"```json\n{json.dumps(faults_context, indent=2, default=str)}\n```\n"
        else:
            block += "No faults have been identified yet.\n"
        return block

    def build_user_message(
        self,
        batch: List[Dict[str, Any]],
        known_faults: Dict[str, FaultBucket],
        eligible_by_event: Optional[Dict[str, List[str]]] = None,
    ) -> str:
        """Build the user message: known faults context + event batch + per-event candidate_fault_ids.

        The ``## Known Faults`` block is dynamic (grows each batch) so it lives
        here in the user message, keeping the system prompt prefix stable for
        GPT-4o auto-caching.
        """
        events_for_llm = []
        for evt in batch:
            evt_payload: Dict[str, Any] = {
                "event_id": evt.get("id"),
            }
            if self.include_event_input:
                evt_payload["input"] = evt.get("input")
            evt_payload["output"] = evt.get("output")
            if eligible_by_event is not None:
                evt_payload["candidate_fault_ids"] = eligible_by_event.get(
                    evt.get("id"), []
                )
            events_for_llm.append(evt_payload)

        return (
            self.build_known_faults_block(known_faults)
            + "\n"
            "## Event Batch\n\n"
            f"```json\n{json.dumps(events_for_llm, indent=2, default=str)}\n```\n\n"
            "Classify each event. If an event has a `candidate_fault_ids` "
            "list, restrict your assignments to ONLY those fault_ids — every "
            "other fault has been ruled out by a deterministic temporal "
            "filter and MUST NOT appear in `related_faults`. Otherwise, the "
            "candidate set is the cumulative list of injected faults whose "
            "`injection_timestamp` is at or before the event. Assign every "
            "candidate fault whose symptoms (per its `ground_truth` / "
            "`ideal_course_of_action` / `ideal_tool_usage_trajectory`) appear "
            "in the event's `output`. "
            "Use `injection_metadata.target` (namespace, label, workload_ref) as "
            "precision disambiguation signals — if the event explicitly references "
            "a specific workload or pod label that matches exactly one candidate's target, "
            "assign only that fault. "
            "Identify any events that represent new fault detections or mitigations. "
            "Return a JSON object with a 'classifications' array."
        )

    async def classify_batch(
        self,
        batch: List[Dict[str, Any]],
        known_faults: Dict[str, FaultBucket],
        eligible_by_event: Optional[Dict[str, List[str]]] = None,
        catch: bool = True,
    ) -> List[EventClassification]:
        """Send a batch of events to the LLM for classification.

        Args:
            catch: If True (default), transient LLM/parse errors are caught and
                   ``fallback_classify`` is returned. If False, all errors propagate
                   to the caller (useful for debugging or strict pipelines).

        Error-handling policy:
        - Config / serialization bugs in setup → always raise ``FaultClassifierError``.
        - Transient LLM / network / API errors → fallback if catch=True, else raise.
        - Unexpected LLM output or parse errors → fallback if catch=True, else raise.
        """
        # ---- Stage 1: setup (client + message) ----
        # Config / serialization bugs here MUST NOT be hidden by the fallback.
        try:
            client = self._get_llm_client()
            user_message = self.build_user_message(batch, known_faults, eligible_by_event)
            if self.cache_enabled:
                # Stable system prefix → Azure GPT-4o auto-cache hits.
                system_prompt = self._system_prompt
            else:
                # Inline the system prompt into the user message so the system
                # role is empty and there is no >=1024-token stable prefix to
                # cache. Used for measuring un-cached worst-case cost.
                system_prompt = ""
                user_message = (
                    f"{self._system_prompt}\n\n"
                    "---\n\n"
                    f"{user_message}"
                )
        except MyCustomError:
            raise
        except Exception as exc:
            raise FaultClassifierError(
                "Failed to prepare classification request",
                original_exception=exc,
            ) from exc

        # ---- Stage 2: LLM call ----
        try:
            result, usage = await client.with_structured_output(
                model_name=self._model_name,
                messages=[{"role": "user", "content": user_message}],
                output_format=BatchClassificationResult,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                system_prompt=system_prompt,
            )
        except MyCustomError as exc:
            if not catch:
                raise
            logger.error(
                f"LLM classification failed (custom error): {exc}. Using fallback."
            )
            return self.fallback_classify(batch, known_faults)
        except Exception as exc:
            if not catch:
                raise
            logger.error(
                f"LLM classification failed: {exc}. Using fallback.",
                exc_info=True,
            )
            return self.fallback_classify(batch, known_faults)

        # ---- Stage 3: parse result + track tokens ----
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

            if not catch:
                raise FaultClassifierError(
                    "LLM returned unexpected output format",
                )
            logger.warning(
                "LLM returned unexpected format, using fallback classification"
            )
            return self.fallback_classify(batch, known_faults)

        except MyCustomError as exc:
            if not catch:
                raise
            logger.error(
                f"Parsing LLM output failed (custom error): {exc}. Using fallback."
            )
            return self.fallback_classify(batch, known_faults)
        except Exception as exc:
            if not catch:
                raise
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
        fallback_reason = (
            "Fallback assignment: LLM classifier failed for this batch, "
            "so the event was conservatively associated with every active "
            "fault. No content-based evidence was evaluated."
        )
        fault_reasoning = {fid: fallback_reason for fid in all_fault_ids}
        return [
            EventClassification(
                event_id=evt.get("id", "unknown"),
                related_faults=all_fault_ids,
                confidence=self._fallback_confidence,
                fault_reasoning=fault_reasoning,
            )
            for evt in batch
        ]
