"""
Run Phase 0 (fault bucketing) + Phase 1 (metrics extraction) on a Langfuse trace.

Usage:
    python run_eval.py <trace_id>

Reads credentials from certifier/.env (same directory as this script).
Pushes scores to Langfuse and prints a per-fault summary on completion.
"""

import logging
import sys
from pathlib import Path

# Configure root logger before any project imports so every logger that
# propagates to root (e.g. fault_bucketing, metrics_extractor sub-modules)
# prints to the console in real time.  The utils.setup_logging logger already
# has its own StreamHandler (propagate=False) so this does not duplicate those
# messages — it only activates the remaining loggers.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stdout,
)

# Load certifier/.env (the file that lives next to this script) so the script
# works standalone without manually exporting env vars beforehand.
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    pass  # python-dotenv not installed; caller must export env vars manually

# ---------------------------------------------------------------------------

def _usage():
    print("Usage: python run_eval.py <trace_id>", file=sys.stderr)
    sys.exit(1)


def _fmt(value, fmt=".2f"):
    return format(value, fmt) if value is not None else "N/A"


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        _usage()

    trace_id = sys.argv[1].strip()

    from metrics_extractor.scripts.langfuse_bridge import run_phase0_then_phase1

    print(f"\nRunning Phase 0+1 on trace: {trace_id}\n")
    results = run_phase0_then_phase1(trace_id)

    # Summary table
    col = {"fault": 24, "ttd": 9, "ttr": 9, "det": 5, "halluc": 8, "reason": 8}
    header = (
        f"{'Fault':<{col['fault']}} "
        f"{'TTD (s)':>{col['ttd']}} "
        f"{'TTR (s)':>{col['ttr']}} "
        f"{'Det':>{col['det']}} "
        f"{'Halluc':>{col['halluc']}} "
        f"{'Reason':>{col['reason']}}"
    )
    sep = "-" * len(header)

    print()
    print("=" * len(header))
    print("RESULTS")
    print("=" * len(header))
    print(header)
    print(sep)

    for fault_id, result in results.items():
        q = result.quantitative
        ql = result.qualitative
        print(
            f"{fault_id:<{col['fault']}} "
            f"{_fmt(q.time_to_detect):>{col['ttd']}} "
            f"{_fmt(q.time_to_mitigate):>{col['ttr']}} "
            f"{str(q.detection_success):>{col['det']}} "
            f"{_fmt(ql.hallucination_score, '.3f'):>{col['halluc']}} "
            f"{_fmt(ql.reasoning_quality_score, '.3f'):>{col['reason']}}"
        )

    print(sep)
    print(f"Faults processed: {len(results)}")
    print()


if __name__ == "__main__":
    main()
