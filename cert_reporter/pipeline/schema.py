"""
pipeline/schema.py
~~~~~~~~~~~~~~~~~~
Normalises incoming certification JSON through a caller-supplied Pydantic
schema class.

The pipeline never imports a schema directly — the schema class (or None) is
passed in through GraphState["schema_class"].  This decouples the pipeline
from any specific package layout or import path.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _is_framework_format(raw: dict[str, Any]) -> bool:
    """Detect whether raw JSON uses the canonical framework format."""
    return "meta" in raw and "sections" in raw


def normalise_document(raw: dict[str, Any], schema_class: Any = None) -> dict[str, Any]:
    """
    Validate raw JSON through schema_class (a Pydantic model class) and
    return a plain dict.

    If schema_class is None, or validation fails, the raw dict is returned
    unchanged so the pipeline never hard-crashes.
    """
    if not _is_framework_format(raw):
        log.info("normalise_document: non-canonical format detected, passing through")
        return raw

    if schema_class is None:
        return raw

    try:
        doc = schema_class.model_validate(raw)
        return doc.model_dump(mode="python")
    except Exception as exc:
        log.warning("Schema validation failed (%s), falling back to raw dict", exc)
        return raw
