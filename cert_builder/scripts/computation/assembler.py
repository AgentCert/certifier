"""
Computation Assembler — merges all 6 builder outputs into one dict.

Runs each builder, respects the dependency graph
(charts depend on scorecard dimensions), and returns the
combined computed content validated against ComputedContent.

Output keys: scorecard, findings, tables, charts, assessments, hardcoded, cards
"""

from pathlib import Path

from cert_builder.schema.intermediate import ComputedContent

from cert_builder.scripts.computation.scorecard_builder import build_from_file as build_scorecard_from_file
from cert_builder.scripts.computation.table_builder import build_from_file as build_tables_from_file
from cert_builder.scripts.computation.chart_builder import build_from_file as build_charts_from_file
from cert_builder.scripts.computation.assessment_formatter import build_from_file as build_assessments_from_file
from cert_builder.scripts.computation.hardcoded_loader import load_all as load_hardcoded
from cert_builder.scripts.computation.card_builder import build_from_file as build_cards_from_file


class ComputationAssembler:
    """Assembles all Phase 2 computation outputs into one dict.

    Args:
        phase1_path: path to phase1 parsed context JSON.
        render_charts: if True, render chart images to disk.
        chart_output_dir: directory for rendered chart images.
    """

    def __init__(self, phase1_path,
                 render_charts=False, chart_output_dir=None):
        self.phase1_path = Path(phase1_path)
        self.render_charts = render_charts
        self.chart_output_dir = chart_output_dir

    def assemble(self) -> dict:
        """Run all 6 builders and merge into a single validated dict.

        Each builder validates its own output via Pydantic.
        The merged result is validated against ComputedContent.

        Returns:
            Combined dict with 7 top-level keys.
        """
        # Scorecard & Findings (no dependencies)
        result_scorecard = build_scorecard_from_file(self.phase1_path)

        # Tables (no dependencies)
        result_tables = build_tables_from_file(self.phase1_path)

        # Charts (depends on scorecard dimensions)
        scorecard_dims = result_scorecard["scorecard"]["dimensions"]
        result_charts = build_charts_from_file(
            self.phase1_path, scorecard_dims,
            render=self.render_charts, output_dir=self.chart_output_dir,
        )

        # Assessment Blocks (no dependencies)
        result_assessments = build_assessments_from_file(self.phase1_path)

        # Hardcoded Content (reads its own YAML)
        result_hardcoded = load_hardcoded()

        # Cards (no dependencies)
        result_cards = build_cards_from_file(self.phase1_path)

        # Merge — each builder returns a dict with distinct top-level keys
        merged = {
            **result_scorecard,    # scorecard, findings
            **result_tables,       # tables
            **result_charts,       # charts
            **result_assessments,  # assessments
            **result_hardcoded,    # hardcoded
            **result_cards,        # cards
        }

        # Validate merged output
        validated = ComputedContent.model_validate(merged)
        return validated.model_dump(mode="json")
