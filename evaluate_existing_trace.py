"""
Evaluate an existing Langfuse trace with the LLM-as-judge evaluator.

Usage — from a Langfuse trace ID (fetches observations from Langfuse):
    python evaluate_existing_trace.py --trace-id <trace_id> [--evaluator-name <name>]

Usage — from a local JSON file (already-dumped observations):
    python evaluate_existing_trace.py --trace-file traces_april1.json --trace-id <trace_id> [--evaluator-name <name>]

    --trace-id is still required in file mode: the file is missing the traceId
    field on each observation, so the pipeline can't create Langfuse scores or
    trigger the evaluator without it.

Steps performed:
  1. Fetch or load observations → normalised JSON file
  2. Patch traceId on every observation (needed for Langfuse score creation)
  3. Run FaultBucketingPipeline — automatically injects known_faults_context
     metadata onto ambiguous observations via _duplicate_to_langfuse_evaluator
  4. Trigger the configured Langfuse LLM-judge evaluator on every enriched
     GENERATION observation
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("evaluate_existing_trace")


# ---------------------------------------------------------------------------
# Step 1a — Load local file + patch traceId
# ---------------------------------------------------------------------------

def load_local_file_to_dest(trace_file: Path, trace_id: str, dest: Path) -> int:
    """Copy a local JSON observation file to dest, patching traceId on every record.

    The file is expected to be a JSON array of observation dicts (same format
    as the pipeline's raw_trace.json).  traceId is injected if missing so that
    _duplicate_to_langfuse_evaluator can create Langfuse scores.
    """
    with open(trace_file, "r", encoding="utf-8") as f:
        observations = json.load(f)

    if not isinstance(observations, list):
        raise ValueError(f"{trace_file} must be a JSON array")

    for obs in observations:
        if not obs.get("traceId"):
            obs["traceId"] = trace_id

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(observations, f, indent=2)

    logger.info(f"Loaded {len(observations)} observations from {trace_file.name} → {dest}")
    return len(observations)


# ---------------------------------------------------------------------------
# Step 1b — Fetch & normalise trace from Langfuse
# ---------------------------------------------------------------------------

def fetch_trace_to_file(trace_id: str, dest: Path) -> int:
    """Fetch a trace by ID from Langfuse and write it as a normalised JSON array.

    Returns the number of observations written.
    """
    from langfuse import Langfuse
    from main.services.trace_service import _format_observations

    base_url = os.environ.get("LANGFUSE_HOST", "").strip()
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()

    missing = [n for n, v in (
        ("LANGFUSE_HOST", base_url),
        ("LANGFUSE_PUBLIC_KEY", public_key),
        ("LANGFUSE_SECRET_KEY", secret_key),
    ) if not v]
    if missing:
        raise EnvironmentError(f"Missing env vars: {', '.join(missing)}")

    client = Langfuse(public_key=public_key, secret_key=secret_key, host=base_url, timeout=120)

    logger.info(f"Fetching trace {trace_id} from Langfuse …")
    full = client.api.trace.get(trace_id)

    raw_obs = [
        (o.model_dump() if hasattr(o, "model_dump") else o.dict())
        for o in (full.observations or [])
    ]

    if not raw_obs:
        raise ValueError(f"No observations found for trace_id={trace_id!r}")

    normalised = _format_observations(raw_obs)

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(normalised, f, indent=2)

    logger.info(f"Wrote {len(normalised)} observations → {dest}")
    return len(normalised)


# ---------------------------------------------------------------------------
# Step 2 — Extract fault metadata (deterministic, no LLM)
# ---------------------------------------------------------------------------

def extract_fault_metadata(trace_file: Path) -> dict:
    """Scan the trace for ``fault: *`` spans and return minimal FaultBucket
    objects (metadata only, no event assignment, no LLM call).

    Independent of the main pipeline — works without Azure OpenAI.
    """
    from fault_analyzer.scripts.langfuse_bucketing import extract_fault_metadata as _extract
    return _extract(trace_file)


# ---------------------------------------------------------------------------
# Step 2b — Inject known_faults_context on ALL GENERATION observations
#
# _duplicate_to_langfuse_evaluator only enriches observations in the overlap
# region (>1 fault in flight). Observations assigned deterministically (single
# fault in flight) are skipped. But the Langfuse evaluator needs the full
# fault context on every GENERATION observation to judge the agent's response.
# This step builds the context from all bucketed faults and patches every
# GENERATION observation that is still missing it.
# ---------------------------------------------------------------------------

def inject_context_all_generations(trace_id: str, buckets: dict) -> int:
    """Inject known_faults_context on every GENERATION observation lacking it.

    Builds one shared context block from all fault buckets (active + closed),
    then sends a single ingestion batch to Langfuse for the missing observations.

    Returns the number of observations patched.
    """
    from langfuse import get_client, Langfuse
    from langfuse.api import IngestionEvent_ObservationUpdate, ObservationBody
    from fault_analyzer.scripts.classifier import FaultEventClassifier
    import uuid
    from datetime import datetime, timezone
    import os

    lf_client = get_client()

    # Build the full fault context from all buckets
    try:
        from utils.load_config import ConfigLoader
        config = ConfigLoader.load_config()
    except Exception:
        config = {}

    classifier = FaultEventClassifier(config=config)
    known_faults_context = classifier.build_known_faults_block(buckets)

    # Fetch observations and find those missing the context
    base_url = os.environ.get("LANGFUSE_HOST", "").strip()
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    lf = Langfuse(public_key=public_key, secret_key=secret_key, host=base_url, timeout=120)

    full = lf.api.trace.get(trace_id)
    missing = [
        o for o in (full.observations or [])
        if o.type == "GENERATION"
        and not (isinstance(o.metadata, dict) and "known_faults_context" in o.metadata)
    ]

    if not missing:
        logger.info("All GENERATION observations already have known_faults_context.")
        return 0

    ingestion_events = [
        IngestionEvent_ObservationUpdate(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            body=ObservationBody(
                id=o.id,
                type="GENERATION",
                metadata={
                    "known_faults_context": known_faults_context,
                    "needs_fault_classification": "true",
                },
            ),
        )
        for o in missing
    ]

    lf_client.api.ingestion.batch(batch=ingestion_events)
    logger.info(f"Injected known_faults_context on {len(missing)} GENERATION observations.")
    return len(missing)


# ---------------------------------------------------------------------------
# Step 3 — Trigger the Langfuse LLM-judge evaluator on enriched observations
# ---------------------------------------------------------------------------

def trigger_evaluator(
    trace_id: str,
    evaluator_name: str,
    output_dir: Optional[Path] = None,
    buckets: Optional[dict] = None,
) -> int:
    """Run the LLM-as-judge evaluator directly and push scores to Langfuse.

    Langfuse v3 provides no public API to trigger evaluators on demand: the
    evaluation rule only fires at first ingestion, not on ObservationUpdate
    events. This function replicates the Langfuse evaluator behaviour directly:
      1. Fetches the evaluator prompt from /api/public/unstable/evaluators
      2. Calls AzureLLMClient for each GENERATION observation, building the
         known_faults_context from buckets (when provided) to avoid a race
         condition with Langfuse's async ingestion processing
      3. Pushes a score (value=confidence, comment=reasoning) per observation

    buckets: FaultBucket dict from extract_fault_metadata. When provided, the
    known_faults_context is built locally (no dependency on Langfuse having
    processed the ObservationUpdate batch from inject_context_all_generations).
    When None, falls back to reading known_faults_context from observation
    metadata (requires the ingestion batch to have been processed).

    Returns the number of observations scored.
    """
    import asyncio
    import json as _json
    import re
    import httpx
    from langfuse import Langfuse

    base_url = os.environ.get("LANGFUSE_HOST", "").strip()
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()

    # 1. Fetch evaluator prompt from the unstable evaluators API.
    resp = httpx.get(
        f"{base_url}/api/public/unstable/evaluators",
        auth=(public_key, secret_key),
        timeout=30,
    )
    resp.raise_for_status()
    evaluators = resp.json().get("data", [])
    evaluator = next((e for e in evaluators if e["name"] == evaluator_name), None)
    if evaluator is None:
        available = [e["name"] for e in evaluators]
        raise ValueError(
            f"Evaluator {evaluator_name!r} not found in Langfuse. "
            f"Available: {available}"
        )
    logger.info(f"Found evaluator: {evaluator_name} (id={evaluator['id']})")

    # Langfuse stores the prompt as a JSON-encoded string (with wrapping quotes).
    raw_prompt = evaluator.get("prompt", "")
    try:
        prompt_template = _json.loads(raw_prompt)
    except (_json.JSONDecodeError, TypeError):
        prompt_template = raw_prompt

    client = Langfuse(public_key=public_key, secret_key=secret_key, host=base_url, timeout=120)

    # 3. Set up LLM client (same Azure deployment as the main pipeline).
    try:
        from utils.load_config import ConfigLoader
        config = ConfigLoader.load_config()
    except Exception:
        config = {}
    from utils.azure_openai_util import AzureLLMClient
    from fault_analyzer.scripts.classifier import _load_module_config, FaultEventClassifier
    model_name = _load_module_config().get("classifier", {}).get("model_name", "gpt-4o")
    # Strip models with empty endpoints to avoid AzureLLMClient init failures
    # (e.g. embedding_model when AZURE_EMBEDDING_ENDPOINT is not set).
    valid_models = {
        k: v for k, v in config.get("models", {}).items() if v.get("endpoint")
    }
    llm = AzureLLMClient(config={**config, "models": valid_models})

    # Build known_faults_context locally when buckets are provided, to avoid
    # depending on Langfuse having processed the async ObservationUpdate batch.
    local_meta_str: Optional[str] = None
    if buckets is not None:
        classifier = FaultEventClassifier(config=config)
        known_faults_context = classifier.build_known_faults_block(buckets)
        local_meta_str = _json.dumps(
            {"known_faults_context": known_faults_context}, ensure_ascii=False
        )

    # 2. Fetch GENERATION observations.
    # When local context is available, evaluate all GENERATION observations.
    # Otherwise, fall back to only those that already have known_faults_context
    # in their Langfuse metadata (requires the ingestion batch to be processed).
    full = client.api.trace.get(trace_id)
    if local_meta_str is not None:
        generations = [o for o in (full.observations or []) if o.type == "GENERATION"]
    else:
        generations = [
            o for o in (full.observations or [])
            if o.type == "GENERATION"
            and isinstance(o.metadata, dict)
            and "known_faults_context" in o.metadata
        ]

    if not generations:
        logger.warning(
            "No GENERATION observations found. "
            "Run step 2b (inject_context_all_generations) first."
        )
        return 0

    logger.info(f"Running evaluator on {len(generations)} GENERATION observations ...")

    async def _score_one(obs) -> dict:
        meta_str = local_meta_str if local_meta_str is not None else _json.dumps(obs.metadata, ensure_ascii=False)
        input_str = (
            obs.input if isinstance(obs.input, str)
            else _json.dumps(obs.input or "", ensure_ascii=False)
        )
        output_str = (
            obs.output if isinstance(obs.output, str)
            else _json.dumps(obs.output or "", ensure_ascii=False)
        )
        prompt = (
            prompt_template
            .replace("{{metadata}}", meta_str)
            .replace("{{input}}", input_str)
            .replace("{{output}}", output_str)
        )
        response, usage = await llm.call_llm(
            model_name=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=500,
        )
        # call_llm already strips fences and parses JSON; fall back to regex.
        if isinstance(response, dict) and "related_faults" in response:
            parsed = response
        else:
            raw_text = response.get("response", "") if isinstance(response, dict) else str(response)
            m = re.search(r"\{.*\}", raw_text, re.DOTALL)
            parsed = _json.loads(m.group()) if m else {}

        return {
            "obs_id": obs.id,
            "obs_name": getattr(obs, "name", "") or "",
            "confidence": float(parsed.get("confidence", 0.0)),
            "reasoning": str(parsed.get("reasoning", "")),
            "related_faults": parsed.get("related_faults", []),
            "tokens_in": int((usage or {}).get("input_tokens", 0)),
            "tokens_out": int((usage or {}).get("output_tokens", 0)),
        }

    async def _run_all():
        sem = asyncio.Semaphore(5)

        async def _guarded(obs):
            async with sem:
                return await _score_one(obs)

        return await asyncio.gather(*[_guarded(o) for o in generations], return_exceptions=True)

    results = asyncio.run(_run_all())

    # 4. Push one score per observation to Langfuse; collect trace entries.
    count = 0
    trace_entries = []
    for obs, result in zip(generations, results):
        if isinstance(result, Exception):
            logger.warning(f"  Skipped {obs.id[:24]}: {result}")
            continue
        try:
            client.create_score(
                trace_id=trace_id,
                observation_id=result["obs_id"],
                name=evaluator_name,
                value=result["confidence"],
                comment=result["reasoning"],
            )
            count += 1
            logger.info(
                f"  Scored {obs.id[:24]}… faults={result['related_faults']} "
                f"conf={result['confidence']}"
            )
            trace_entries.append({
                "event_id": result["obs_id"],
                "name": result["obs_name"],
                "classification": {
                    "related_faults": result["related_faults"],
                    "confidence": result["confidence"],
                },
                "tokens_in": result["tokens_in"],
                "tokens_out": result["tokens_out"],
                "source": "llm",
                "deterministic_assignment": False,
            })
        except Exception as exc:
            logger.warning(f"  Failed to create score for {obs.id}: {exc}")

    logger.info(f"Scores pushed: {count}/{len(generations)}")

    if output_dir is not None and trace_entries:
        output_dir.mkdir(parents=True, exist_ok=True)
        trace_path = output_dir / "batch_classification_trace.json"
        with open(trace_path, "w", encoding="utf-8") as f:
            _json.dump(trace_entries, f, indent=2, ensure_ascii=False)
        logger.info(f"Wrote classification trace: {trace_path} ({len(trace_entries)} entries)")

    return count


# ---------------------------------------------------------------------------
# Resolve trace_id from experiment_id + experiment_run_id
# ---------------------------------------------------------------------------

def resolve_trace_id(experiment_id: str, experiment_run_id: str) -> str:
    """Return the first Langfuse trace ID matching this experiment run.

    Reuses _list_traces from trace_service which queries both chaos/OTel
    (experiment.id / experiment.run_id) and LiteLLM (experiment_id /
    experiment_run_id) metadata filters.

    Raises ValueError if no trace is found.
    """
    from langfuse import Langfuse
    from main.services.trace_service import _list_traces

    base_url = os.environ.get("LANGFUSE_HOST", "").strip()
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()

    client = Langfuse(public_key=public_key, secret_key=secret_key, host=base_url, timeout=120)
    traces = _list_traces(client, experiment_id, experiment_run_id, page_size=50, max_pages=5)

    if not traces:
        raise ValueError(
            f"No Langfuse trace found for experiment_id={experiment_id!r} "
            f"run_id={experiment_run_id!r}"
        )

    if len(traces) > 1:
        ids = [t.id for t in traces]
        logger.info(f"Multiple traces found: {ids} — using the first one")

    trace_id = traces[0].id
    logger.info(f"Resolved trace_id: {trace_id}")
    return trace_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Evaluate an existing Langfuse trace with the LLM-as-judge evaluator."
    )

    id_group = parser.add_mutually_exclusive_group(required=True)
    id_group.add_argument("--trace-id", help="Langfuse trace ID (direct)")
    id_group.add_argument(
        "--experiment-id",
        help="experiment_id metadata value — resolves trace_id automatically. "
             "Must be combined with --experiment-run-id.",
    )

    parser.add_argument(
        "--experiment-run-id",
        default=None,
        help="experiment_run_id metadata value (required with --experiment-id).",
    )
    parser.add_argument(
        "--trace-file",
        default=None,
        help="Path to a local JSON observation file. If provided, skips the "
             "Langfuse fetch step. A trace_id (direct or resolved) is still "
             "required to patch traceId on each observation.",
    )
    parser.add_argument(
        "--evaluator-name",
        default="fault-event-classifier-lf",
        help="Name of the Langfuse score config (LLM judge) to trigger "
             "(default: fault-event-classifier-lf). Pass an empty string to "
             "stop after metadata injection without triggering the evaluator.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for bucketing output files (default: tmp dir).",
    )
    args = parser.parse_args()

    if args.experiment_id and not args.experiment_run_id:
        parser.error("--experiment-run-id is required when using --experiment-id")

    # Resolve trace_id
    if args.trace_id:
        trace_id = args.trace_id
    else:
        try:
            trace_id = resolve_trace_id(args.experiment_id, args.experiment_run_id)
        except Exception as exc:
            logger.error(f"Could not resolve trace_id: {exc}")
            sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(args.output_dir) if args.output_dir else Path(tmp) / "output"
        trace_file = output_dir / "raw_trace.json"

        # Step 1: load local file or fetch from Langfuse
        try:
            if args.trace_file:
                n = load_local_file_to_dest(Path(args.trace_file), trace_id, trace_file)
            else:
                n = fetch_trace_to_file(trace_id, trace_file)
        except Exception as exc:
            logger.error(f"Trace acquisition failed: {exc}")
            sys.exit(1)

        # Step 2: extract fault metadata (deterministic, no LLM)
        try:
            buckets = extract_fault_metadata(trace_file)
            logger.info(f"Fault metadata extraction complete: {len(buckets)} bucket(s)")
        except Exception as exc:
            logger.error(f"Fault metadata extraction failed: {exc}", exc_info=True)
            sys.exit(1)

        # Step 2b: inject known_faults_context on ALL remaining GENERATION observations
        try:
            patched = inject_context_all_generations(trace_id, buckets)
            logger.info(f"Step 2b: {patched} additional observations patched.")
        except Exception as exc:
            logger.warning(f"Step 2b injection failed (non-fatal): {exc}", exc_info=True)

        # Step 3: trigger evaluator (optional)
        if args.evaluator_name:
            try:
                trigger_evaluator(trace_id, args.evaluator_name, output_dir=output_dir, buckets=buckets)
            except Exception as exc:
                logger.error(f"Evaluator trigger failed: {exc}", exc_info=True)
                sys.exit(1)
        else:
            logger.info(
                "Empty --evaluator-name — metadata injected, evaluator not triggered. "
                "Re-run without --evaluator-name (defaults to fault-event-classifier-lf) "
                "or trigger from the Langfuse UI."
            )


if __name__ == "__main__":
    main()
