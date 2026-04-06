"""
Phase 1 — Ingestor: Parse raw scorecard JSON into structured context.

Input:   aggregated_scorecard_output.json
Output:  phase1_parsed_context.json
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CAT_LABELS = {
    "application_fault": "Application",
    "network_fault": "Network",
    "resource_fault": "Resource",
    "database_fault": "Database",
    "storage_fault": "Storage",
}

NUMERIC_FIELDS = [
    "time_to_detect", "time_to_mitigate", "action_correctness",
    "reasoning_score", "response_quality_score", "hallucination_score",
    "input_tokens", "output_tokens",
]

# Map raw field names to clean output keys
PII_FIELDS = {
    "number_of_pii_instances_detected": "pii_instances",
    "malicious_prompts_detected": "malicious_prompts",
}


@dataclass
class ParsedContext:
    meta: dict[str, Any]
    categories: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


def _compute_runs_per_fault(raw: dict) -> int:
    """Derive runs_per_fault from total_runs / total_faults_tested."""
    total = raw.get("total_runs", 0)
    faults = raw.get("total_faults_tested", 0)
    return total // faults if faults else 0


def ingest(raw: dict) -> ParsedContext:
    """Parse raw scorecard dict into structured context."""
    warnings = []
    scorecards = raw.get("fault_category_scorecards", [])

    # Parse date
    cert_date = raw.get("created_at", "")
    if "T" in cert_date:
        cert_date = cert_date.split("T")[0]

    meta = {
        "agent_name": raw.get("agent_name", ""),
        "agent_id": raw.get("agent_id", ""),
        "certification_run_id": raw.get("certification_run_id", ""),
        "certification_date": cert_date,
        "total_runs": raw.get("total_runs", 0),
        "total_faults_tested": raw.get("total_faults_tested", 0),
        "total_fault_categories": raw.get("total_fault_categories", 0),
        "runs_per_fault": _compute_runs_per_fault(raw),
        "categories_summary": [],
    }

    categories = []
    for sc in scorecards:
        cat_name = sc.get("fault_category", "")
        label = CAT_LABELS.get(cat_name, cat_name.replace("_", " ").title())
        raw_numeric = sc.get("numeric_metrics", {})

        # Extract numeric metrics
        numeric = {}
        for f in NUMERIC_FIELDS:
            val = raw_numeric.get(f)
            if not isinstance(val, dict):
                warnings.append(f"{label}: '{f}' missing")
                numeric[f] = {}
            else:
                numeric[f] = val

        # PII / malicious prompts
        for raw_key, clean_key in PII_FIELDS.items():
            val = raw_numeric.get(raw_key)
            numeric[clean_key] = val if isinstance(val, dict) else {"sum": 0.0, "mean": 0.0}

        categories.append({
            "fault_category": cat_name,
            "label": label,
            "faults_tested": sc.get("faults_tested", []),
            "total_runs": sc.get("total_runs", 0),
            "numeric": numeric,
            "derived": sc.get("derived_metrics", {}),
            "boolean": sc.get("boolean_status_metrics", {}),
            "textual": sc.get("textual_metrics", {}),
        })

        meta["categories_summary"].append({
            "name": label,
            "fault": ", ".join(sc.get("faults_tested", [])),
            "runs": sc.get("total_runs", 0),
        })

    return ParsedContext(meta=meta, categories=categories, warnings=warnings)


def ingest_from_file(path: str | Path) -> ParsedContext:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return ingest(raw)


def save_context(ctx: ParsedContext, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(asdict(ctx), indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent.parent
    inp = base / "data" / "phase1" / "input" / "aggregated_scorecard_output.json"
    out = base / "data" / "phase1" / "output" / "phase1_parsed_context.json"

    ctx = ingest_from_file(inp)
    save_context(ctx, out)

    print(f"Agent:      {ctx.meta['agent_name']}")
    print(f"Categories: {len(ctx.categories)}")
    print(f"Warnings:   {len(ctx.warnings)}")
    print(f"Output:     {out}")
