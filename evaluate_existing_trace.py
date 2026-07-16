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
                    "needs_fault_classification": True,
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

def trigger_evaluator(trace_id: str, evaluator_name: str) -> int:
    """Trigger a named Langfuse evaluator on all GENERATION observations of the trace.

    Finds the score config whose name matches *evaluator_name*, then creates
    one evaluation log per GENERATION observation so the LLM judge runs on
    each enriched span.

    Returns the number of evaluation jobs created.
    """
    from langfuse import Langfuse

    base_url = os.environ.get("LANGFUSE_HOST", "").strip()
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()

    client = Langfuse(public_key=public_key, secret_key=secret_key, host=base_url, timeout=120)

    # Find the evaluator config by name
    configs = client.api.score_configs.get()
    config_id = next(
        (c.id for c in (configs.data or []) if c.name == evaluator_name),
        None,
    )
    if config_id is None:
        available = [c.name for c in (configs.data or [])]
        raise ValueError(
            f"Evaluator {evaluator_name!r} not found in Langfuse. "
            f"Available: {available}"
        )

    logger.info(f"Found evaluator config: {evaluator_name} (id={config_id})")

    # Fetch GENERATION observations for this trace
    full = client.api.trace.get(trace_id)
    generations = [
        o for o in (full.observations or [])
        if (o.type if isinstance(o.type, str) else getattr(o, "type", "")) == "GENERATION"
    ]

    logger.info(f"Triggering evaluator on {len(generations)} GENERATION observations …")
    count = 0
    for obs in generations:
        obs_id = obs.id if hasattr(obs, "id") else obs.get("id")
        try:
            client.api.evals.create_log(
                config_id=config_id,
                trace_id=trace_id,
                observation_id=obs_id,
            )
            count += 1
        except Exception as exc:
            logger.warning(f"  Skipped obs {obs_id}: {exc}")

    logger.info(f"Evaluation jobs created: {count}/{len(generations)}")
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
                trigger_evaluator(trace_id, args.evaluator_name)
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
