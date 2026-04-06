"""
Sub-Phase 2E -- Hardcoded content loader.

Loads static definitions, methodology text, normalization formulas,
and section introductions from hardcoded_content.yaml.

Input:  hardcoded_content.yaml (no Phase 1 dependency)
Output: {"hardcoded": {definitions, normalization, statistics, section_intros, methodology_bullets}}
"""

from pathlib import Path

import yaml

from cert_builder.schema.intermediate import HardcodedResult

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "hardcoded_content.yaml"


def _load():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def load_all():
    """Load all hardcoded content from YAML.

    Returns:
        {"hardcoded": {"definitions": {...}, "normalization": {...}, ...}}
    """
    content = _load()
    result = HardcodedResult.model_validate({"hardcoded": content})
    return result.model_dump(mode="json")


def get_definitions():
    """Return metric definitions dict."""
    return _load()["definitions"]


def get_normalization():
    """Return normalization formulas and config."""
    return _load()["normalization"]


def get_section_intros():
    """Return section introduction texts."""
    return _load()["section_intros"]


def get_methodology_bullets():
    """Return list of methodology description bullets."""
    return _load()["methodology_bullets"]
