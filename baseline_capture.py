"""
baseline_capture.py — Phase 1 regression baseline (local raw-trace mode).

Runs FaultBucketingPipeline (Phase 0) exactly ONCE per trace file and caches
the resulting bucket dicts permanently at:

    <output-dir>/buckets/<trace_id>__<fault_id>.json

Then runs extract_metrics_from_trace_dict N times on each fixed, cached bucket.

Phase 0 is skipped on resume if bucket cache files already exist for that trace.
Phase 1 is skipped for a (trace_id, fault_id) pair if its stability report
already exists.  Rerun the same command to pick up where you left off.

To force a fresh Phase 0 for a trace, delete its bucket files from
<output-dir>/buckets/ before rerunning.

Usage:
    /srv/projects/intern/.venv/bin/python3 baseline_capture.py \\
        [TRACE_FILE ...]          positional: paths to raw trace JSON files
        [--runs N]                Phase 1 repetitions per fault  (default 5)
        [--output-dir DIR]        root output dir                (default ./baseline_output)
        [--trace-list FILE]       text file, one trace-file path per line
        [--log-file FILE]         default <output-dir>/baseline.log

If no trace files are given, the five known local files are used.
"""

import argparse
import asyncio
import json
import logging
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Bootstrap: add certifier/ to sys.path and load .env
# ---------------------------------------------------------------------------

_CERTIFIER_DIR = Path(__file__).resolve().parent
if str(_CERTIFIER_DIR) not in sys.path:
    sys.path.insert(0, str(_CERTIFIER_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(_CERTIFIER_DIR / ".env")
except ImportError:
    pass  # env vars must already be set

from fault_analyzer import FaultBucketingPipeline
from metrics_extractor.scripts.metrics_extractor_from_trace import (
    extract_metrics_from_trace_dict,
)
from metrics_extractor.scripts.metric_groups import run_extraction as _mg_run_extraction
from metrics_extractor.schema.data_models import ExtractionResult
from utils.load_config import ConfigLoader

# ---------------------------------------------------------------------------
# Default trace file list
# ---------------------------------------------------------------------------

DEFAULT_TRACE_FILES: List[str] = [
    "/srv/projects/intern/raw_trace.json",
    "/srv/projects/intern/ace-monorepo/certifier/trace_dump/raw_trace.json",
    "/srv/projects/intern/cyril/ace-monorepo/certifier/data/input/08-05-26-ujjwal/raw_trace-sequential.json",
    "/srv/projects/intern/cyril/ace-monorepo/certifier/data/input/08-05-26-aarya/1960bc89/f6152c39-sequential/raw_trace_sequential.json",
    "/srv/projects/intern/cyril/ace-monorepo/certifier/data/input/08-05-26-aarya/1960bc89/9f53f493-single/raw_trace_single.json",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(log_path: Path) -> logging.Logger:
    log = logging.getLogger("baseline")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(ch)
    return log

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _read_trace_id(trace_file: Path) -> str:
    with open(trace_file, encoding="utf-8") as f:
        data = json.load(f)
    for event in data:
        tid = event.get("traceId") or event.get("trace_id")
        if tid:
            return tid
    raise ValueError(f"No traceId field found in {trace_file}")


def _safe_name(fault_id: str) -> str:
    """Sanitise a fault_id for use in file names (replaces / and space)."""
    return fault_id.replace("/", "_").replace(" ", "_")


def _serialisable(v: Any) -> Any:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v

# ---------------------------------------------------------------------------
# Phase 0: run once per trace, cache permanently
# ---------------------------------------------------------------------------

async def _phase0_coroutine(
    trace_file: Path,
    scratch_dir: Path,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Run FaultBucketingPipeline and return {fault_id: bucket_dict}."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    pipeline = FaultBucketingPipeline(
        trace_file_path=str(trace_file),
        output_dir=str(scratch_dir),
        config=config,
    )
    raw_buckets = await pipeline.run()
    return {fid: bucket.to_dict() for fid, bucket in raw_buckets.items()}


def _load_or_run_phase0(
    trace_file: Path,
    trace_id: str,
    bucket_cache_dir: Path,
    config: Dict[str, Any],
    log: logging.Logger,
) -> Dict[str, Any]:
    """
    Return cached bucket dicts if present, otherwise run Phase 0 and cache them.

    Cache convention: <bucket_cache_dir>/<trace_id>__<fault_id_safe>.json
    These files are permanent — never overwritten unless explicitly deleted.
    """
    existing = sorted(bucket_cache_dir.glob(f"{trace_id}__*.json"))
    if existing:
        log.info(
            "Phase 0 CACHE HIT for %s — loading %d bucket(s), skipping Phase 0",
            trace_id, len(existing),
        )
        buckets: Dict[str, Any] = {}
        for cache_path in existing:
            # stem = "<trace_id>__<fault_id_safe>"
            fault_id_safe = cache_path.stem[len(trace_id) + 2:]
            with open(cache_path, encoding="utf-8") as f:
                buckets[fault_id_safe] = json.load(f)
        return buckets

    log.info("Phase 0 running for %s …", trace_id)
    scratch_dir = bucket_cache_dir.parent / "phase0_scratch" / trace_id
    buckets = asyncio.run(
        _phase0_coroutine(trace_file, scratch_dir, config)
    )

    if not buckets:
        log.warning("Phase 0 returned no buckets for %s", trace_id)
        return {}

    bucket_cache_dir.mkdir(parents=True, exist_ok=True)
    for fault_id, bucket_dict in buckets.items():
        safe = _safe_name(fault_id)
        cache_path = bucket_cache_dir / f"{trace_id}__{safe}.json"
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(bucket_dict, f, indent=2, default=_serialisable)
        log.info(
            "  bucket cached → buckets/%s  (%d events)",
            cache_path.name, len(bucket_dict.get("events", [])),
        )

    # Return using safe keys (matching how they'd be loaded from cache on resume)
    return {_safe_name(fid): bd for fid, bd in buckets.items()}

# ---------------------------------------------------------------------------
# Flatten ExtractionResult → flat metric dict
# ---------------------------------------------------------------------------

def _flatten_result(result: ExtractionResult) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    q = result.quantitative.model_dump(exclude_none=False, mode="json")
    for k, v in q.items():
        if k == "tool_calls":
            flat["tool_call_count"] = len(v) if isinstance(v, list) else 0
        else:
            flat[f"q.{k}"] = v
    ql = result.qualitative.model_dump(exclude_none=False, mode="json")
    for k, v in ql.items():
        flat[f"ql.{k}"] = v
    flat["_token_input"] = result.token_usage.input_tokens
    flat["_token_output"] = result.token_usage.output_tokens
    flat["_token_total"] = result.token_usage.total_tokens
    return flat

# ---------------------------------------------------------------------------
# Stability analysis
# ---------------------------------------------------------------------------

def _analyse_metric(name: str, values: List[Any]) -> Dict[str, Any]:
    non_none = [v for v in values if v is not None]
    none_count = len(values) - len(non_none)
    if not non_none:
        return {"stable": None, "note": "all_none", "none_count": len(values)}
    str_vals = [str(v) for v in non_none]
    unique = set(str_vals)
    base: Dict[str, Any] = {"none_count": none_count}
    if len(unique) == 1:
        base["stable"] = True
        base["value"] = non_none[0]
        return base
    base["stable"] = False
    if all(isinstance(v, (int, float)) for v in non_none):
        floats = [float(v) for v in non_none]
        base["values"] = floats
        base["mean"] = round(statistics.mean(floats), 6)
        base["stdev"] = round(statistics.stdev(floats), 6) if len(floats) > 1 else 0.0
        base["min"] = min(floats)
        base["max"] = max(floats)
        base["range"] = round(max(floats) - min(floats), 6)
    else:
        counts: Dict[str, int] = {}
        for sv in str_vals:
            counts[sv] = counts.get(sv, 0) + 1
        base["unique_values"] = sorted(unique)
        base["value_counts"] = counts
    return base


def _stability_report(all_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not all_runs:
        return {}
    all_keys = {k for run in all_runs for k in run}
    metrics = {
        key: _analyse_metric(key, [run.get(key) for run in all_runs])
        for key in sorted(all_keys)
    }
    return {
        "total_metrics": len(metrics),
        "stable": sum(1 for m in metrics.values() if m.get("stable") is True),
        "unstable": sum(1 for m in metrics.values() if m.get("stable") is False),
        "all_none": sum(1 for m in metrics.values() if m.get("stable") is None),
        "metrics": metrics,
    }

# ---------------------------------------------------------------------------
# Phase 1: N runs on a fixed bucket
# ---------------------------------------------------------------------------

def _run_phase1_for_fault(
    trace_id: str,
    fault_id: str,
    bucket: Dict[str, Any],
    n_runs: int,
    out_dir: Path,
    log: logging.Logger,
    use_metric_groups: bool = False,
) -> Dict[str, Any]:
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    n_events = len(bucket.get("events", []))
    log.info(
        "[%s / %s]  Phase 1 × %d  (%d events in bucket)",
        trace_id, fault_id, n_runs, n_events,
    )

    all_runs: List[Dict[str, Any]] = []
    run_meta: List[Dict[str, Any]] = []

    for run_idx in range(1, n_runs + 1):
        log.info(
            "[%s / %s]  run %d/%d …",
            trace_id, fault_id, run_idx, n_runs,
        )
        t0 = time.monotonic()
        try:
            if use_metric_groups:
                result: ExtractionResult = asyncio.run(_mg_run_extraction(bucket))
            else:
                result: ExtractionResult = extract_metrics_from_trace_dict(bucket)
        except Exception as exc:
            log.error(
                "[%s / %s]  run %d FAILED: %s",
                trace_id, fault_id, run_idx, exc, exc_info=True,
            )
            run_meta.append({
                "run": run_idx,
                "status": "error",
                "error": str(exc),
                "elapsed_s": round(time.monotonic() - t0, 2),
            })
            continue

        elapsed = round(time.monotonic() - t0, 2)
        log.info(
            "[%s / %s]  run %d done  %.1fs  in=%d out=%d",
            trace_id, fault_id, run_idx, elapsed,
            result.token_usage.input_tokens,
            result.token_usage.output_tokens,
        )

        flat = _flatten_result(result)
        raw_path = raw_dir / f"{trace_id}__{fault_id}__run{run_idx:02d}.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "trace_id": trace_id,
                    "fault_id": fault_id,
                    "run": run_idx,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "elapsed_s": elapsed,
                    "token_usage": result.token_usage.to_dict(),
                    "quantitative": result.quantitative.model_dump(
                        exclude_none=False, mode="json"
                    ),
                    "qualitative": result.qualitative.model_dump(
                        exclude_none=False, mode="json"
                    ),
                },
                f, indent=2, default=_serialisable,
            )
        all_runs.append(flat)
        run_meta.append({
            "run": run_idx,
            "status": "ok",
            "elapsed_s": elapsed,
            "tokens": result.token_usage.to_dict(),
        })

    report = _stability_report(all_runs)
    report["trace_id"] = trace_id
    report["fault_id"] = fault_id
    report["n_runs_attempted"] = n_runs
    report["n_runs_ok"] = len(all_runs)
    report["run_metadata"] = run_meta
    report["n_events_in_bucket"] = n_events

    report_path = out_dir / f"{trace_id}__{fault_id}__stability.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=_serialisable)

    log.info(
        "[%s / %s]  stability: %d stable  %d unstable → %s",
        trace_id, fault_id,
        report.get("stable", 0), report.get("unstable", 0),
        report_path.name,
    )
    return report

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 1 regression baseline. "
            "Phase 0 runs once per trace (result cached permanently). "
            "Phase 1 runs N times per fault bucket against that fixed cache."
        )
    )
    parser.add_argument(
        "trace_files",
        nargs="*",
        metavar="TRACE_FILE",
        help=(
            "Paths to raw trace JSON files. "
            "Defaults to the five known local files if omitted."
        ),
    )
    parser.add_argument(
        "--trace-list",
        metavar="FILE",
        help="Text file with one raw trace-file path per line.",
    )
    parser.add_argument(
        "--runs", type=int, default=5, metavar="N",
        help="Phase 1 repetitions per (trace, fault) pair (default 5).",
    )
    parser.add_argument(
        "--output-dir", default="baseline_output", metavar="DIR",
        help="Root output directory (default: ./baseline_output).",
    )
    parser.add_argument(
        "--log-file", default=None, metavar="FILE",
        help="Log file path (default: <output-dir>/baseline.log).",
    )
    parser.add_argument(
        "--use-metric-groups",
        action="store_true",
        help=(
            "Use the MetricGroup abstraction (run_extraction) instead of "
            "extract_metrics_from_trace_dict. Output-dir defaults to "
            "baseline_output_metricgroups/ and bucket cache defaults to "
            "baseline_output/buckets/ (the existing Phase 0 cache is shared, "
            "never re-run)."
        ),
    )
    parser.add_argument(
        "--bucket-cache-dir",
        default=None,
        metavar="DIR",
        help=(
            "Override the bucket cache directory. "
            "Default: <output-dir>/buckets, or baseline_output/buckets/ "
            "when --use-metric-groups is set."
        ),
    )
    args = parser.parse_args()

    # Resolve trace file list
    if args.trace_list:
        with open(args.trace_list, encoding="utf-8") as f:
            trace_files = [
                Path(line.strip())
                for line in f
                if line.strip() and not line.startswith("#")
            ]
    elif args.trace_files:
        trace_files = [Path(p) for p in args.trace_files]
    else:
        trace_files = [Path(p) for p in DEFAULT_TRACE_FILES]

    # When --use-metric-groups is set, default to a separate output dir so the
    # two baselines sit side by side and neither overwrites the other.
    if args.use_metric_groups and args.output_dir == "baseline_output":
        args.output_dir = "baseline_output_metricgroups"

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log_file) if args.log_file else out_dir / "baseline.log"
    log = _setup_logging(log_path)

    # Bucket cache directory: shared with the original baseline when using
    # metric groups (all 14 Phase 0 results are already cached there).
    if args.bucket_cache_dir:
        bucket_cache_dir = Path(args.bucket_cache_dir)
    elif args.use_metric_groups:
        bucket_cache_dir = _CERTIFIER_DIR / "baseline_output" / "buckets"
    else:
        bucket_cache_dir = out_dir / "buckets"
    bucket_cache_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 70)
    log.info("baseline_capture.py  started")
    log.info("trace files : %d", len(trace_files))
    log.info("runs/fault  : %d", args.runs)
    log.info("output dir  : %s", out_dir.resolve())
    log.info("bucket cache: %s  (permanent — never overwritten on resume)", bucket_cache_dir.resolve())
    log.info("=" * 70)

    try:
        config = ConfigLoader.load_config()
    except Exception as exc:
        log.warning("ConfigLoader failed (%s) — using empty config", exc)
        config = {}

    summary: List[Dict[str, Any]] = []

    for trace_file in trace_files:
        trace_file = trace_file.resolve()
        if not trace_file.exists():
            log.error("File not found: %s — skipping", trace_file)
            summary.append({"trace_file": str(trace_file), "status": "file_not_found"})
            continue

        log.info("─" * 60)
        log.info("TRACE FILE: %s", trace_file.name)

        try:
            trace_id = _read_trace_id(trace_file)
        except Exception as exc:
            log.error("Cannot read trace_id from %s: %s", trace_file, exc)
            summary.append({
                "trace_file": str(trace_file),
                "status": "error_read_trace_id",
                "error": str(exc),
            })
            continue

        log.info("trace_id   : %s", trace_id)

        # --- Phase 0: once per trace, cached permanently ---
        try:
            buckets = _load_or_run_phase0(
                trace_file, trace_id, bucket_cache_dir, config, log
            )
        except Exception as exc:
            log.error("Phase 0 failed for %s: %s", trace_id, exc, exc_info=True)
            summary.append({
                "trace_id": trace_id,
                "status": "error_phase0",
                "error": str(exc),
            })
            continue

        if not buckets:
            log.warning("No fault buckets produced for %s — skipping", trace_id)
            summary.append({"trace_id": trace_id, "status": "no_buckets"})
            continue

        log.info("%d fault bucket(s): %s", len(buckets), list(buckets.keys()))

        # --- Phase 1: N runs per fixed bucket ---
        for fault_id, bucket_dict in buckets.items():
            stability_path = out_dir / f"{trace_id}__{fault_id}__stability.json"
            if stability_path.exists():
                log.info(
                    "[%s / %s]  stability report exists — skipped (already done)",
                    trace_id, fault_id,
                )
                with open(stability_path, encoding="utf-8") as f:
                    done = json.load(f)
                summary.append({
                    "trace_id": trace_id,
                    "fault_id": fault_id,
                    "status": "skipped_already_done",
                    "n_runs_ok": done.get("n_runs_ok"),
                    "stable_metrics": done.get("stable"),
                    "unstable_metrics": done.get("unstable"),
                })
                continue

            try:
                report = _run_phase1_for_fault(
                    trace_id, fault_id, bucket_dict, args.runs, out_dir, log,
                    use_metric_groups=args.use_metric_groups,
                )
                summary.append({
                    "trace_id": trace_id,
                    "fault_id": fault_id,
                    "status": "ok",
                    "n_runs_ok": report.get("n_runs_ok"),
                    "stable_metrics": report.get("stable"),
                    "unstable_metrics": report.get("unstable"),
                })
            except Exception as exc:
                log.error(
                    "Phase 1 failed for %s / %s: %s",
                    trace_id, fault_id, exc, exc_info=True,
                )
                summary.append({
                    "trace_id": trace_id,
                    "fault_id": fault_id,
                    "status": "error_phase1",
                    "error": str(exc),
                })

    summary_path = out_dir / "baseline_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "runs_per_fault": args.runs,
                "entries": summary,
            },
            f, indent=2,
        )
    log.info("Summary → %s", summary_path)

    total_fault_pairs = sum(1 for e in summary if "fault_id" in e)
    ok_pairs = sum(
        1 for e in summary
        if e.get("status") in ("ok", "skipped_already_done")
    )
    log.info(
        "Done. %d/%d (trace, fault) pairs completed.",
        ok_pairs, total_fault_pairs,
    )


if __name__ == "__main__":
    main()
