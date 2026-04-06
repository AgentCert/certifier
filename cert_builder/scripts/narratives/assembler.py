"""
Narrative Assembler — runs all 6 LLM calls, merges into one dict.

Calls 1-5 run concurrently via asyncio.to_thread + gather.
Call 6 runs after Call 5 completes (depends on limitations output).

Output keys: scope_narrative, key_findings, qualitative_findings,
             fault_category_analysis, limitations_enriched,
             recommendations_enriched, fallbacks_used, errors
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from cert_builder.scripts.narratives.scope_narrative_builder import build_scope_narrative
from cert_builder.scripts.narratives.key_findings_builder import build_key_findings
from cert_builder.scripts.narratives.qualitative_builder import build_qualitative_findings
from cert_builder.scripts.narratives.fault_analysis_builder import build_fault_analysis
from cert_builder.scripts.narratives.limitation_builder import build_limitations
from cert_builder.scripts.narratives.recommendation_builder import build_recommendations


async def _safe_call(phase_id: str, fn, *args) -> tuple[str, dict | None, dict | None]:
    """Run a sync builder in a thread, capturing errors."""
    try:
        output = await asyncio.to_thread(fn, *args)
        return phase_id, output, None
    except Exception as exc:
        print(f"[narrative-assembler] {phase_id} failed: {exc}")
        return phase_id, None, {"phase": phase_id, "error": str(exc)}


class NarrativeAssembler:
    """Assembles all Phase 3 LLM narrative outputs into one dict.

    Args:
        phase1_path: path to phase1 parsed context JSON.
        phase2_path: path to phase2 computed content JSON.
    """

    def __init__(self, phase1_path, phase2_path):
        self.phase1_path = Path(phase1_path)
        self.phase2_path = Path(phase2_path)

    async def assemble(self):
        """Run all 6 LLM calls and merge into a single dict.

        Returns:
            Combined dict with narrative outputs + metadata.
        """
        t0 = time.time()

        phase1 = json.loads(self.phase1_path.read_text(encoding="utf-8"))
        phase2 = json.loads(self.phase2_path.read_text(encoding="utf-8"))

        results = {}
        errors = []

        # Concurrent: Calls 1-5
        outcomes = await asyncio.gather(
            _safe_call("scope_narrative", build_scope_narrative, phase1),
            _safe_call("key_findings", build_key_findings, phase1, phase2),
            _safe_call("qualitative", build_qualitative_findings, phase1, phase2),
            _safe_call("fault_analysis", build_fault_analysis, phase1, phase2),
            _safe_call("limitations", build_limitations, phase1, phase2),
        )

        for phase_id, output, error in outcomes:
            if error:
                errors.append(error)
            elif output:
                results[phase_id] = output

        # Sequential: Call 6 (depends on limitations output)
        result_limitations = results.get("limitations", {})
        limitations_enriched = result_limitations.get("limitations_enriched", {})

        phase_id, output, error = await _safe_call(
            "recommendations", build_recommendations, phase1, phase2, limitations_enriched
        )
        if error:
            errors.append(error)
        elif output:
            results["recommendations"] = output

        # Merge
        merged = {}
        for phase_id, output in results.items():
            merged.update(output)

        # Detect fallback usage
        fallbacks_used = any(
            v.get("source") == "fallback"
            for v in merged.values()
            if isinstance(v, dict) and "source" in v
        )

        merged["fallbacks_used"] = fallbacks_used
        merged["errors"] = errors

        elapsed = time.time() - t0
        print(f"[narrative-assembler] Done in {elapsed:.1f}s")
        return merged
