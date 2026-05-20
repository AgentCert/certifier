"""
Certification report pipeline.

Takes an aggregated scorecard JSON (produced by ``run_aggregation_pipeline.py``)
and runs the certification report builder (Phase 3).

Usage
-----
::

    python run_certification_pipeline.py \
        --scorecard <path_to_aggregated_scorecard.json> \
        --output-dir <output_dir> \
        [--debug]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from utils.setup_logging import logger
except ImportError:
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

from utils.custom_errors import MyCustomError, OrchestratorError

from cert_builder.scripts.error_report_builder import build_error_report
from cert_builder.scripts.certification_pipeline import CertificationPipeline


def _save_json(data: dict, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=4, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
    except MyCustomError:
        raise
    except Exception as exc:
        raise OrchestratorError(
            f"Failed to write JSON to '{path}'",
            original_exception=exc,
        ) from exc


async def run_pipeline(
    scorecard_path: str,
    output_dir: str,
    debug: bool = False,
) -> Dict[str, Any]:
    """Run certification (Phase 3) from a pre-built aggregated scorecard.

    Returns the final certification report dict.
    """
    scorecard_file = Path(scorecard_path)
    if not scorecard_file.exists():
        logger.error(f"Scorecard file not found: {scorecard_file}")
        return {}

    scorecard = json.loads(scorecard_file.read_text(encoding="utf-8"))
    agent_id = scorecard.get("agent_id", "unknown")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Early exit: metrics validation failed → error report
    if scorecard.get("metrics_validation_failed", False):
        logger.warning("Metrics validation failed. Generating hardcoded error report.")
        try:
            report = build_error_report(scorecard)
            report_path = output_path / f"certification_report_{agent_id}.json"
            _save_json(report, report_path)
            logger.info(f"Hardcoded error report written to {report_path}")
            return report
        except Exception as exc:
            raise OrchestratorError(
                "Failed to build error report",
                original_exception=exc,
            ) from exc

    # Normal certification
    logger.info("=" * 60)
    logger.info("Certification Report Builder")
    logger.info("=" * 60)

    report_path = output_path / f"certification_report_{agent_id}.json"
    try:
        cert_pipeline = CertificationPipeline(
            input_path=scorecard_file,
            output_path=report_path,
            debug=debug,
        )
        report = await cert_pipeline.run()
    except MyCustomError:
        raise
    except Exception as exc:
        logger.error(f"Certification step failed: {exc}", exc_info=True)
        raise OrchestratorError(
            "Certification step failed",
            original_exception=exc,
        ) from exc

    logger.info(f"Certification report written to {report_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Certification report pipeline. Consumes an aggregated scorecard "
            "JSON and produces the final certification report."
        )
    )
    parser.add_argument("--scorecard", required=True,
                        help="Path to the aggregated scorecard JSON file.")
    parser.add_argument("--output-dir", required=True,
                        help="Directory for the certification report output.")
    parser.add_argument("--debug", action="store_true",
                        help="Persist intermediate outputs / verbose logging.")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        report = asyncio.run(
            run_pipeline(
                scorecard_path=args.scorecard,
                output_dir=args.output_dir,
                debug=args.debug,
            )
        )
    except MyCustomError as exc:
        logger.error(f"Pipeline aborted: {exc}")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Unexpected pipeline error: {exc}", exc_info=True)
        sys.exit(1)

    if report:
        print("\nCertification Pipeline Complete")
        print("=" * 50)
        print("  Certification report generated successfully.")
        print(f"  Output: {args.output_dir}")
    else:
        print("\nPipeline failed. Check logs for details.")


if __name__ == "__main__":
    main()
