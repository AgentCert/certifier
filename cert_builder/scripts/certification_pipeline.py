"""
Certification Pipeline — main entry point.

Chains: ingestion → computation → narratives → assembly.
Takes input_path, output_path, debug flag.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path

from cert_builder.scripts.ingestion.ingestor import ingest_from_file, save_context
from cert_builder.scripts.computation.assembler import ComputationAssembler
from cert_builder.scripts.narratives.assembler import NarrativeAssembler
from cert_builder.scripts.report_assembler import ReportAssembler


def _save_json(data, path):
    """Write dict to JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )


class CertificationPipeline:
    """Main entry point for certification report generation.

    Chains: ingestion → computation → narratives → assembly.

    Args:
        input_path: path to aggregated_scorecard_output.json.
        output_path: path for the final certification_report.json.
        debug: if True, persist intermediate outputs to data/intermediate/.
    """

    def __init__(self, input_path, output_path, debug=False):
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.debug = debug
        self._data_dir = self.input_path.resolve().parent.parent  # data/

    async def run(self) -> dict:
        """Execute the full 4-phase pipeline.

        Uses a temp directory for phase-to-phase data passing.
        When debug=True, copies intermediates to data/intermediate/.
        Final report always goes to output_path.
        """
        t0 = time.time()
        print("=== Certification Pipeline Started ===")

        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)

            # ── Phase 1: Ingestion ────────────────────────────────
            print("[pipeline] Phase 1: Ingestion")
            parsed_context = ingest_from_file(self.input_path)
            phase1_path = workdir / "parsed_context.json"
            save_context(parsed_context, phase1_path)

            # ── Phase 2: Computation ──────────────────────────────
            print("[pipeline] Phase 2: Computation")
            computation = ComputationAssembler(phase1_path)
            computed_content = computation.assemble()
            phase2_path = workdir / "computed_content.json"
            _save_json(computed_content, phase2_path)

            # ── Phase 3: Narratives ───────────────────────────────
            print("[pipeline] Phase 3: Narratives")
            narrative = NarrativeAssembler(phase1_path, phase2_path)
            narratives = await narrative.assemble()
            phase3_path = workdir / "narratives.json"
            _save_json(narratives, phase3_path)

            # ── Phase 4: Assembly ─────────────────────────────────
            print("[pipeline] Phase 4: Assembly")
            assembly = ReportAssembler(phase1_path, phase2_path, phase3_path)
            report = assembly.assemble()

            # Persist intermediates only when debugging
            if self.debug:
                intermediate = self._data_dir / "intermediate"
                intermediate.mkdir(parents=True, exist_ok=True)
                for f in workdir.glob("*.json"):
                    shutil.copy2(f, intermediate / f.name)
                print(f"[pipeline] Intermediates saved to {intermediate}")

        # Final report always saved
        _save_json(report, self.output_path)

        elapsed = time.time() - t0
        print(f"=== Pipeline Completed in {elapsed:.1f}s ===")
        print(f"Output: {self.output_path}")
        return report

    def run_sync(self) -> dict:
        """Synchronous wrapper for run()."""
        return asyncio.run(self.run())
