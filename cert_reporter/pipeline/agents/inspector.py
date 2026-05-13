"""
pipeline/agents/inspector.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Rule-based JSON structure discovery. No LLM needed.

Updated for the canonical certification framework format.
Walks any JSON document and classifies every significant field into one of:
  scalar     — single numeric / string / bool value
  table      — list[dict] with at least 2 rows and >=1 numeric column
  narrative  — long text string (>=100 chars)
  kv_list    — list of {key,value} / [k, v] pairs
  array      — simple array of scalars
  nested     — dict of nested objects (sub-documents)
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


# ── models ────────────────────────────────────────────────────────────────────

class FieldInfo(BaseModel):
    path: str
    field_type: str          # scalar | table | narrative | kv_list | array | nested
    value_type: str = ""     # float | int | str | bool | mixed
    sample: Any = None       # first value or short preview
    row_count: int = 0
    columns: list[str] = Field(default_factory=list)
    numeric_columns: list[str] = Field(default_factory=list)
    text_length: int = 0


class DomainProfile(BaseModel):
    domain: str = "general"
    title: str = ""
    agent_name: str = ""
    cert_level: str = ""
    cert_score: float = 0.0
    fields: list[FieldInfo] = Field(default_factory=list)
    # quick-access collections
    scalars: dict[str, Any] = Field(default_factory=dict)
    narratives: dict[str, str] = Field(default_factory=dict)
    tables: dict[str, FieldInfo] = Field(default_factory=dict)


# ── helpers ───────────────────────────────────────────────────────────────────

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "cybersecurity": ["cve", "vulnerability", "threat", "exploit", "cwe", "cvss",
                      "penetration", "audit", "compliance", "firewall", "tls", "rbac"],
    "sre":           ["latency", "uptime", "slo", "sli", "incident", "mttr",
                      "k8s", "kubernetes", "pod", "fault", "chaos", "sre"],
    "ai_evaluation": ["hallucination", "accuracy", "benchmark", "eval", "score",
                      "certification", "llm", "reasoning", "detection", "mitigation"],
    "financial":     ["revenue", "profit", "roi", "cost", "budget", "expenditure",
                      "asset", "liability", "performance"],
    "compliance":    ["gdpr", "hipaa", "soc2", "iso27001", "pci", "policy",
                      "requirement", "control", "risk", "audit"],
}


def _infer_domain(text: str) -> str:
    blob = text.lower()
    best, best_score = "general", 0
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in blob)
        if score > best_score:
            best, best_score = domain, score
    return best


def _value_type(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, float):
        return "float"
    if isinstance(v, int):
        return "int"
    if isinstance(v, str):
        return "str"
    return "mixed"


def _is_table(lst: list) -> bool:
    if len(lst) < 2:
        return False
    dicts = [r for r in lst if isinstance(r, dict)]
    if len(dicts) < 2:
        return False
    keys = set(dicts[0].keys())
    shared = sum(1 for d in dicts[1:4] if set(d.keys()) & keys)
    return shared >= 1


def _is_kv_list(lst: list) -> bool:
    if not lst:
        return False
    sample = lst[0]
    if isinstance(sample, dict) and {"key", "value"} & set(sample.keys()):
        return True
    if isinstance(sample, dict) and {"label", "value"} & set(sample.keys()):
        return True
    if isinstance(sample, (list, tuple)) and len(sample) == 2:
        return True
    return False


def _table_info(path: str, lst: list) -> FieldInfo:
    dicts = [r for r in lst if isinstance(r, dict)]
    if not dicts:
        return FieldInfo(path=path, field_type="table", row_count=len(lst))
    cols = list(dicts[0].keys())
    num_cols = [c for c in cols
                if any(isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool)
                       for r in dicts[:5])]
    sample_row = {k: v for k, v in list(dicts[0].items())[:6]}
    return FieldInfo(
        path=path, field_type="table",
        row_count=len(dicts), columns=cols, numeric_columns=num_cols,
        sample=sample_row,
    )


_MAX_DEPTH = 6
_MAX_FIELDS = 200


def _walk(node: Any, path: str, fields: list[FieldInfo], depth: int = 0) -> None:
    if len(fields) >= _MAX_FIELDS or depth > _MAX_DEPTH:
        return

    if isinstance(node, dict):
        for k, v in node.items():
            child_path = f"{path}.{k}" if path else k
            _walk(v, child_path, fields, depth + 1)

    elif isinstance(node, list):
        if not node:
            return
        if _is_kv_list(node):
            fields.append(FieldInfo(path=path, field_type="kv_list",
                                    row_count=len(node), sample=node[0]))
        elif _is_table(node):
            fields.append(_table_info(path, node))
        elif all(isinstance(x, (str, int, float, bool)) for x in node[:10]):
            fields.append(FieldInfo(
                path=path, field_type="array",
                row_count=len(node),
                value_type=_value_type(node[0]),
                sample=node[:3],
            ))
        else:
            for i, item in enumerate(node[:3]):
                _walk(item, f"{path}[{i}]", fields, depth + 1)

    elif isinstance(node, str):
        length = len(node)
        if length >= 100:
            fields.append(FieldInfo(
                path=path, field_type="narrative",
                value_type="str", text_length=length,
                sample=node[:120] + ("…" if length > 120 else ""),
            ))

    elif isinstance(node, (int, float, bool)):
        fields.append(FieldInfo(
            path=path, field_type="scalar",
            value_type=_value_type(node), sample=node,
        ))


# ── public API ────────────────────────────────────────────────────────────────

def inspect_document(doc: dict[str, Any]) -> DomainProfile:
    """Walk *doc* and return a DomainProfile with a classified field inventory."""
    fields: list[FieldInfo] = []
    _walk(doc, "", fields, 0)

    text_blob = " ".join(str(k) for k in _flatten_keys(doc))
    domain = _infer_domain(text_blob)

    # Extract common header fields — support both canonical and legacy formats
    meta = doc.get("meta") or {}
    header = doc.get("header") or doc.get("report_header") or {}
    title = meta.get("subtitle") or doc.get("title") or ""
    agent = meta.get("agent_name") or doc.get("agent_name") or ""

    # Compute aggregate score from scorecard dimensions if available
    scorecard = header.get("scorecard") or []
    score = 0.0
    if scorecard:
        vals = []
        for d in scorecard:
            if isinstance(d, dict):
                vals.append(float(d.get("value", 0)))
            elif hasattr(d, "value"):
                vals.append(float(d.value))
        if vals:
            score = sum(vals) / len(vals) * 100  # 0-1 → 0-100

    scalars: dict[str, Any] = {}
    narratives: dict[str, str] = {}
    tables: dict[str, FieldInfo] = {}
    for f in fields:
        if f.field_type == "scalar":
            scalars[f.path] = f.sample
        elif f.field_type == "narrative":
            narratives[f.path] = str(f.sample or "")
        elif f.field_type == "table":
            tables[f.path] = f

    return DomainProfile(
        domain=domain,
        title=str(title),
        agent_name=str(agent),
        cert_level="",
        cert_score=score,
        fields=fields,
        scalars=scalars,
        narratives=narratives,
        tables=tables,
    )


def _flatten_keys(node: Any, depth: int = 0) -> list[str]:
    if depth > 4:
        return []
    if isinstance(node, dict):
        keys = list(node.keys())
        for v in node.values():
            keys.extend(_flatten_keys(v, depth + 1))
        return keys
    if isinstance(node, list):
        out = []
        for item in node[:3]:
            out.extend(_flatten_keys(item, depth + 1))
        return out
    if isinstance(node, str):
        return node.split()[:20]
    return []
