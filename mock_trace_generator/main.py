"""
CLI entry point for multi-fault trace generation.

Usage:
    python -m multi_fault_trace_generation \
        --faults-file faults.json --output-dir ../../../agentcert/data/langfuse_minio_traces

    python -m multi_fault_trace_generation --interactive

    python -m multi_fault_trace_generation \
        --fault "pod-delete:Deletes a running pod causing downtime" \
        --fault "pod-network-latency:Injects network latency into pod traffic" \
        --fault "disk-fill:Fills the disk on a node causing I/O pressure"
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import List

try:
    from utils.azure_openai_util import AzureLLMClient
    from utils.load_config import ConfigLoader
    from utils.setup_logging import logger
except ImportError:
    AzureLLMClient = None
    ConfigLoader = None
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

from mock_trace_generator import (
    FaultDefinition,
    MultiFaultTraceGenerator,
)


def _parse_fault_arg(value: str) -> FaultDefinition:
    """Parse a 'name:description' fault argument."""
    if ":" not in value:
        raise argparse.ArgumentTypeError(
            f"Invalid fault format '{value}'. Expected 'name:description'"
        )
    name, description = value.split(":", 1)
    return FaultDefinition(name=name.strip(), description=description.strip())


def _load_agent_metadata() -> dict:
    """Load agent defaults from the package config file."""
    config_path = (
        Path(__file__).resolve().parent / "config" / "multi_fault_config.json"
    )
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("agent_defaults", {})
    return {}


async def main():
    parser = argparse.ArgumentParser(
        description="Generate OTEL-compliant multi-fault traces for ITOps agent scenarios"
    )
    parser.add_argument(
        "--fault",
        type=_parse_fault_arg,
        action="append",
        dest="faults",
        help="Fault in 'name:description' format. Repeat for multiple faults. "
             "Example: --fault 'pod-delete:Deletes a running pod'",
    )
    parser.add_argument(
        "--faults-file",
        type=str,
        help="Path to JSON file with fault definitions: "
             '[{"name": "...", "description": "..."}, ...]',
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(
            Path(__file__).resolve().parent.parent.parent.parent
            / "data"
            / "langfuse_minio_traces"
        ),
        help="Output directory for trace JSON files",
    )
    parser.add_argument(
        "--num-cycles",
        type=int,
        default=3,
        help="Number of detection-verify cycles per fault (default: 3)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Interactive mode: prompt for faults one at a time",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="extraction_model",
        help="Model key from configs.json to use (default: extraction_model)",
    )
    parser.add_argument(
        "--num-traces",
        type=int,
        default=1,
        help="Number of trace files to generate (default: 1)",
    )
    parser.add_argument(
        "--agent-name",
        type=str,
        default=None,
        help="Override the agent name in the onboarding span",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default=None,
        help="Agent ID (constant for a given agent and version). "
             "If not provided, a random UUID is generated.",
    )
    parser.add_argument(
        "--agent-description",
        type=str,
        default=None,
        help="Override the agent description in the onboarding span",
    )

    args = parser.parse_args()

    # Collect faults from various sources
    all_faults: List[FaultDefinition] = []

    if args.faults:
        all_faults.extend(args.faults)

    if args.faults_file:
        faults_path = Path(args.faults_file)
        if not faults_path.exists():
            parser.error(f"Faults file not found: {args.faults_file}")
        with open(faults_path, "r", encoding="utf-8") as f:
            faults_data = json.load(f)
        for fd in faults_data:
            all_faults.append(FaultDefinition(**fd))

    if args.interactive:
        print("Enter faults one at a time. Type 'done' when finished.\n")
        while True:
            name = input("Fault name (or 'done'): ").strip()
            if name.lower() == "done":
                break
            description = input("Fault description: ").strip()
            if name and description:
                all_faults.append(FaultDefinition(name=name, description=description))
                print(f"  Added: {name}\n")

    if not all_faults:
        parser.error(
            "No faults provided. Use --fault, --faults-file, or --interactive."
        )

    if len(all_faults) < 2:
        parser.error(
            "Multi-fault generation requires at least 2 faults. "
            "For single-fault, use trace_data_gen.py instead."
        )

    # Initialize LLM client
    if ConfigLoader is None or AzureLLMClient is None:
        logger.error(
            "Required utilities not available. "
            "Run from the agentcert/ directory with the correct conda environment."
        )
        sys.exit(1)

    config = ConfigLoader.load_config()
    llm_client = AzureLLMClient(config=config)

    # Build agent metadata from config + CLI overrides
    agent_metadata = _load_agent_metadata()
    if args.agent_name:
        agent_metadata["agent_name"] = args.agent_name
    if args.agent_description:
        agent_metadata["agent_description"] = args.agent_description

    generator = MultiFaultTraceGenerator(
        llm_client=llm_client,
        model_name=args.model,
        agent_metadata=agent_metadata,
    )

    print(f"\nGenerating {args.num_traces} trace(s) for {len(all_faults)} faults: "
          f"{', '.join(f.name for f in all_faults)}\n")

    # Compute experiment_id (deterministic for the fault combination)
    experiment_id = MultiFaultTraceGenerator.generate_experiment_id(all_faults)
    # Use provided agent_id or generate one
    agent_id = args.agent_id or str(uuid.uuid4())

    for i in range(args.num_traces):
        if args.num_traces > 1:
            print(f"--- Trace {i + 1}/{args.num_traces} ---")

        # Each trace gets a unique run_id
        run_id = str(uuid.uuid4())

        filepath = await generator.generate_and_save(
            faults=all_faults,
            output_dir=args.output_dir,
            num_detection_cycles=args.num_cycles,
            agent_id=agent_id,
            experiment_id=experiment_id,
            run_id=run_id,
        )
        print(f"Trace generated: {filepath}")

    await llm_client.close()


if __name__ == "__main__":
    asyncio.run(main())
