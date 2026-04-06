"""
LLM Council for textual metric aggregation.

Implements the k-judge + meta-reconciliation pattern for synthesizing
narrative metrics across multiple experiment runs.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from utils.azure_openai_util import AzureLLMClient
from utils.setup_logging import logger

# ---------------------------------------------------------------------------
# Module-level paths & config
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent.parent
_PROMPTS_PATH = _MODULE_DIR / "prompt" / "prompt.yml"
_CONFIG_PATH = _MODULE_DIR / "config" / "aggregation_config.json"


def _load_prompts() -> Dict[str, Any]:
    """Load prompt templates from prompt/prompt.yml."""
    with open(_PROMPTS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_module_config() -> Dict[str, Any]:
    """Load module-specific configuration from config/aggregation_config.json."""
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_PROMPTS: Dict[str, Any] = {}
_MODULE_CONFIG: Dict[str, Any] = {}


def _get_prompts() -> Dict[str, Any]:
    global _PROMPTS
    if not _PROMPTS:
        _PROMPTS = _load_prompts()
    return _PROMPTS


def _get_config() -> Dict[str, Any]:
    global _MODULE_CONFIG
    if not _MODULE_CONFIG:
        _MODULE_CONFIG = _load_module_config()
    return _MODULE_CONFIG


# ---------------------------------------------------------------------------
# Council internals
# ---------------------------------------------------------------------------


class LLMCouncil:
    """Runs k independent LLM judges and a meta-judge to produce consensus."""

    def __init__(
        self,
        llm_client: AzureLLMClient,
        council_size: Optional[int] = None,
        council_members: Optional[List[str]] = None,
        meta_judge_model: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        config = _get_config().get("llm_council", {})
        self.llm_client = llm_client
        self.council_size = council_size or config.get("council_size", 3)
        self._config = config

        # Resolve council member models.
        # Priority: explicit council_members > config council_members > legacy model_name
        if council_members:
            self.council_members = list(council_members)
        elif config.get("council_members"):
            self.council_members = list(config["council_members"])
        else:
            fallback = model_name or config.get("model_name", "extraction_model")
            self.council_members = [fallback] * self.council_size

        # Meta-judge / scorecard synthesis model
        self.meta_judge_model = (
            meta_judge_model
            or config.get("meta_judge_model")
            or self.council_members[0]
        )

        # Backward-compat: keep model_name pointing to the first member
        self.model_name = self.council_members[0]

    # ---- single judge ----------------------------------------------------

    def _model_for_judge(self, judge_index: int) -> str:
        """Return the model name for the given judge index (cycles if needed)."""
        return self.council_members[judge_index % len(self.council_members)]

    async def _run_single_judge(
        self,
        prompt: str,
        system_prompt: str,
        judge_index: int,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        """Run a single LLM judge and return (parsed_response, token_usage)."""
        config = self._config
        judge_model = self._model_for_judge(judge_index)
        response, usage = await self.llm_client.call_llm(
            model_name=judge_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=config.get("judge_temperature", 0.3),
            max_tokens=config.get("judge_max_tokens", 1500),
            system_prompt=system_prompt,
        )
        logger.info(f"Judge {judge_index + 1} (model={judge_model}) completed.")

        if isinstance(response, dict):
            return response, usage
        return {
            "consensus_summary": str(response),
            "severity_label": "Adequate",
            "confidence": "Medium",
        }, usage

    # ---- meta-judge ------------------------------------------------------

    async def _run_meta_judge(
        self,
        judge_outputs: List[Dict[str, Any]],
        metric_name: str,
        fault_category: str,
        n_runs: int,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        """Run the meta-reconciliation judge."""
        config = self._config
        prompts = _get_prompts()
        formatted_outputs = "\n\n".join(
            f"--- Judge {i + 1} ---\n{json.dumps(j, indent=2)}"
            for i, j in enumerate(judge_outputs)
        )

        prompt = prompts["meta_judge"]["reconciliation"].format(
            k=len(judge_outputs),
            metric_name=metric_name,
            fault_category=fault_category,
            n=n_runs,
            judge_outputs=formatted_outputs,
        )

        response, usage = await self.llm_client.call_llm(
            model_name=self.meta_judge_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=config.get("meta_judge_temperature", 0.1),
            max_tokens=config.get("meta_judge_max_tokens", 2000),
            system_prompt=prompts["meta_judge"]["system_prompt"],
        )

        if isinstance(response, dict):
            return response, usage
        return {
            "consensus_summary": str(response),
            "severity_label": "Adequate",
            "confidence": "Medium",
            "inter_judge_agreement": 0.5,
        }, usage

    # ---- public: narrative metric ----------------------------------------

    async def synthesize_textual_metric(
        self,
        narratives: List[str],
        metric_name: str,
        fault_category: str,
        prompt_template: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        """
        Full LLM Council pipeline for a single textual metric.

        1. Present all narratives to k independent judges.
        2. Run a meta-judge to reconcile.
        3. Return aggregated result dict + total token usage.
        """
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        n_runs = len(narratives)

        if n_runs == 0:
            return {}, total_usage

        prompts = _get_prompts()
        template = prompt_template or prompts["judge"]["narrative"]
        system_prompt = prompts["judge"]["system_prompt"]

        formatted = "\n".join(
            f"  Run {i + 1}: {n}" for i, n in enumerate(narratives)
        )

        prompt = template.format(
            metric_name=metric_name,
            fault_category=fault_category,
            n=n_runs,
            narratives=formatted,
        )

        # Step 1: Run k judges concurrently
        judge_tasks = [
            self._run_single_judge(prompt, system_prompt, i)
            for i in range(self.council_size)
        ]
        judge_results = await asyncio.gather(*judge_tasks, return_exceptions=True)

        judge_outputs: List[Dict[str, Any]] = []
        for result in judge_results:
            if isinstance(result, Exception):
                logger.error(f"Judge failed: {result}")
                judge_outputs.append({
                    "consensus_summary": "Judge evaluation failed.",
                    "severity_label": "Weak",
                    "confidence": "Low",
                })
            else:
                resp, usage = result
                judge_outputs.append(resp)
                for k in total_usage:
                    total_usage[k] += usage.get(k, 0)

        # Step 2: Meta-reconciliation
        meta_response, meta_usage = await self._run_meta_judge(
            judge_outputs, metric_name, fault_category, n_runs
        )
        for k in total_usage:
            total_usage[k] += meta_usage.get(k, 0)

        return meta_response, total_usage

    # ---- public: list metric ---------------------------------------------

    async def synthesize_list_metric(
        self,
        all_items: List[str],
        metric_name: str,
        fault_category: str,
        prompt_template: str,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        """
        LLM Council pipeline for list-based metrics (known_limitations, recommendations).

        Picks the judge output with the most items as the best result.
        """
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        if not all_items:
            return {}, total_usage

        prompts = _get_prompts()
        system_prompt = prompts["judge"]["system_prompt"]
        formatted_items = "\n".join(f"  - {item}" for item in all_items)

        prompt = prompt_template.format(
            metric_name=metric_name,
            fault_category=fault_category,
            n=len(all_items),
            narratives=formatted_items,
        )

        judge_tasks = [
            self._run_single_judge(prompt, system_prompt, i)
            for i in range(self.council_size)
        ]
        judge_results = await asyncio.gather(*judge_tasks, return_exceptions=True)

        best_result: Dict[str, Any] = {}
        best_item_count = 0

        for result in judge_results:
            if isinstance(result, Exception):
                logger.error(f"List judge failed: {result}")
                continue
            resp, usage = result
            for k in total_usage:
                total_usage[k] += usage.get(k, 0)

            items_key = "ranked_items" if "ranked_items" in resp else "prioritized_items"
            items = resp.get(items_key, [])
            if len(items) > best_item_count:
                best_item_count = len(items)
                best_result = resp

        return best_result, total_usage

    # ---- public: scorecard synthesis -------------------------------------

    async def synthesize_limitations_and_recommendations(
        self,
        fault_category: str,
        faults_tested: List[str],
        total_runs: int,
        numeric_aggs: Dict[str, Dict[str, Any]],
        derived_rates: Dict[str, Optional[float]],
        boolean_aggs: Dict[str, Any],
        textual_aggs: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        """
        Synthesize known_limitations and recommendations from already-computed
        aggregated metrics for a fault category.
        """
        config = self._config
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        prompts = _get_prompts()

        summaries_for_prompt: Dict[str, Any] = {}
        for key, val in textual_aggs.items():
            if key in ("known_limitations", "recommendations"):
                continue
            if isinstance(val, dict) and "consensus_summary" in val:
                summaries_for_prompt[key] = val["consensus_summary"]
            else:
                summaries_for_prompt[key] = val

        prompt = prompts["scorecard_synthesis"]["prompt"].format(
            fault_category=fault_category,
            total_runs=total_runs,
            faults_tested=", ".join(faults_tested) if faults_tested else "N/A",
            numeric_metrics=json.dumps(numeric_aggs, indent=2, default=str),
            derived_rates=json.dumps(derived_rates, indent=2, default=str),
            boolean_metrics=json.dumps(boolean_aggs, indent=2, default=str),
            textual_summaries=json.dumps(summaries_for_prompt, indent=2, default=str),
        )

        response, usage = await self.llm_client.call_llm(
            model_name=self.meta_judge_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=config.get("scorecard_synthesis_temperature", 0.2),
            max_tokens=config.get("scorecard_synthesis_max_tokens", 2000),
            system_prompt=prompts["scorecard_synthesis"]["system_prompt"],
        )
        for k in total_usage:
            total_usage[k] += usage.get(k, 0)

        result: Dict[str, Any] = {}
        if isinstance(response, dict):
            result["known_limitations"] = response.get("known_limitations", {})
            result["recommendations"] = response.get("recommendations", {})
        else:
            logger.warning("Scorecard synthesis returned non-dict response; skipping.")

        return result, total_usage

    # ---- public: all textual metrics for a category ----------------------

    async def compute_textual_aggregates(
        self,
        docs: List[Dict[str, Any]],
        fault_category: str,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        """
        Synthesize all textual/narrative metrics via LLM Council.

        Produces:
        - rai_check_summary
        - overall_response_and_reasoning_quality
        - security_compliance_summary
        - agent_summary
        """
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        results: Dict[str, Any] = {}

        metric_mappings = [
            ("rai_check_summary", "qualitative", "rai_check_notes",
             ["consensus_summary", "severity_label", "confidence", "inter_judge_agreement"]),
            ("overall_response_and_reasoning_quality", "qualitative", "reasoning_quality_notes",
             ["consensus_summary", "severity_label", "confidence", "inter_judge_agreement"]),
            ("security_compliance_summary", "qualitative", "security_compliance_notes",
             ["consensus_summary", "severity_label", "confidence", "inter_judge_agreement"]),
            ("agent_summary", "qualitative", "agent_summary",
             ["consensus_summary", "confidence", "inter_judge_agreement"]),
        ]

        for output_key, section, field_name, output_fields in metric_mappings:
            narratives = _collect_narratives(docs, section, field_name)
            if not narratives:
                continue

            agg, usage = await self.synthesize_textual_metric(
                narratives, output_key, fault_category
            )

            results[output_key] = {
                f: agg.get(f, "" if f == "consensus_summary" else None)
                for f in output_fields
            }
            for k in total_usage:
                total_usage[k] += usage.get(k, 0)

        return results, total_usage


# ---------------------------------------------------------------------------
# Narrative helpers (used by LLMCouncil.compute_textual_aggregates)
# ---------------------------------------------------------------------------

def _collect_narratives(
    docs: List[Dict[str, Any]], section: str, field_name: str
) -> List[str]:
    """Collect non-empty string values from docs[section][field_name]."""
    narratives: List[str] = []
    for doc in docs:
        val = doc.get(section, {}).get(field_name)
        if val and isinstance(val, str) and val.strip():
            narratives.append(val.strip())
    return narratives
