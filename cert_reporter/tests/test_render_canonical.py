"""Acceptance suite for the three canonical framework samples.

Each sample is rendered to HTML and three properties are asserted:
  1. The pipeline reports no errors.
  2. The output contains zero `Unknown block:` markers.
  3. For every distinct block type present in the JSON sample, the
     rendered HTML emits the canonical CSS class associated with that
     block. This is the class-set check from REFACTORING_PLAN.md §6.1 —
     scoped per-sample to the blocks the JSON actually exercises, so a
     reference HTML that happens to cover other block types doesn't
     pollute the gate.

Reference HTMLs and sample JSONs live under
`local-personal-workspace/certification_template/`. The reference HTMLs
are kept around as a visual design spec; they are not used to drive the
class-set assertion (different samples cover different blocks).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipeline.graph import run_pipeline


REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLES_DIR = REPO_ROOT / "local-personal-workspace" / "certification_template"


SAMPLES = [
    pytest.param("certification_report_sre-agent-v2.1_s1.json", id="s1"),
    pytest.param("certification_report_sre-agent-v2.1_s2.json", id="s2"),
    pytest.param("certification_report_sre-agent-v2.1_s4.json", id="s4"),
]


# Canonical CSS class(es) each block type is expected to emit. Values may
# be a single class string or a tuple of acceptable alternatives (a block
# may render different markup based on its data shape — e.g. `card`
# switches between `kv-grid` for KPI numbers and `kv-list` for text rows).
# A block type passes if *any* of its acceptable classes appears in the
# rendered HTML.
BLOCK_CANONICAL_CLASS: dict[str, str | tuple[str, ...]] = {
    "identity_card":        "identity-card",
    "scope_stats":          "scope-grid",
    "scope_metrics":        "scope-grid",
    "fault_pills":          "fault-categories",
    "notice":               "notice",
    "part_banner":          "part-banner",
    "hypothesis_strip":     "hyp-strip",
    "interpretation_scale": "interpretation-scale",
    "category_panel":       "category-panel",
    "enumerated_item":      "enum-item",
    "taxonomy_table":       "taxonomy-table",
    # Pre-existing block types — kept here so the regression gate is one
    # consolidated check rather than two parallel suites.
    "heading":              "sub-section-title",
    "text":                 "narrative",
    "card":                 ("kv-grid", "kv-list"),
    "findings":             "findings-block",
    "table":                "data-table",
    "chart":                "chart-card",
}


_CLASS_RE = re.compile(r'class\s*=\s*"([^"]+)"')


def _collect_block_types(payload, into: set[str]) -> None:
    if isinstance(payload, dict):
        t = payload.get("type")
        if isinstance(t, str):
            into.add(t)
        for v in payload.values():
            _collect_block_types(v, into)
    elif isinstance(payload, list):
        for item in payload:
            _collect_block_types(item, into)


def _extract_class_set(html: str) -> set[str]:
    # Strip <style> blocks so embedded CSS rules don't false-positive
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S)
    classes: set[str] = set()
    for match in _CLASS_RE.findall(html):
        for token in match.split():
            token = token.strip()
            if token:
                classes.add(token)
    return classes


@pytest.mark.parametrize("input_name", SAMPLES)
def test_canonical_sample_renders(tmp_path, input_name):
    input_path = SAMPLES_DIR / input_name
    if not input_path.exists():
        pytest.skip(f"sample input not present: {input_path}")

    state = run_pipeline(
        input_path=str(input_path),
        output_dir=str(tmp_path),
        formats=["html"],
    )

    assert state.get("errors", []) == [], (
        f"pipeline produced errors: {state.get('errors')}"
    )

    html_path = state.get("html_path")
    assert html_path, "html_renderer_node did not set html_path"
    rendered = Path(html_path).read_text(encoding="utf-8")

    assert "Unknown block:" not in rendered, (
        "rendered HTML still contains an Unknown-block fallback — the "
        "dispatcher could not resolve a block.type for this sample"
    )

    block_types: set[str] = set()
    _collect_block_types(json.loads(input_path.read_text(encoding="utf-8")), block_types)

    rendered_classes = _extract_class_set(rendered)

    checked = 0
    missing: list[str] = []
    for t in block_types:
        canonical = BLOCK_CANONICAL_CLASS.get(t)
        if canonical is None:
            continue
        accepted = (canonical,) if isinstance(canonical, str) else canonical
        checked += 1
        if not any(c in rendered_classes for c in accepted):
            missing.append(f"{t}=>{'/'.join(accepted)}")

    coverage = (checked - len(missing)) / checked if checked else 1.0
    assert coverage >= 0.95, (
        f"class-set coverage {coverage:.0%} < 95% for {input_name}; "
        f"missing canonical class for block types: {missing}"
    )
