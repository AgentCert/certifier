"""
Step 1 — Verify Langfuse trace fetch.

Fetches observations for a given experiment + run from Langfuse, prints a
summary, and writes the result to a JSON file you can inspect.

Usage:
    python test_langfuse_fetch.py --experiment-id <id> --run-id <id> [--out trace.json]

Required env vars:
    LANGFUSE_HOST        e.g. https://cloud.langfuse.com
    LANGFUSE_PUBLIC_KEY
    LANGFUSE_SECRET_KEY
"""

import argparse
import json
import os
import sys

# Allow running from the certifier/ root without installing the package
sys.path.insert(0, os.path.dirname(__file__))

from main.services.trace_service import _fetch_langfuse_observations, TraceIngestionError


def main():
    parser = argparse.ArgumentParser(description="Fetch a Langfuse trace and dump it to JSON.")
    parser.add_argument("--experiment-id", required=True, help="Langfuse experiment ID")
    parser.add_argument("--run-id", required=True, help="Langfuse run ID")
    parser.add_argument("--out", default="fetched_trace.json", help="Output file path (default: fetched_trace.json)")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=10)
    args = parser.parse_args()

    # Check env vars early for a clear error message
    missing = [v for v in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") if not os.environ.get(v)]
    if missing:
        print(f"[ERROR] Missing environment variable(s): {', '.join(missing)}")
        sys.exit(1)

    print(f"Connecting to {os.environ['LANGFUSE_HOST']} ...")
    print(f"  experiment_id = {args.experiment_id}")
    print(f"  run_id        = {args.run_id}")

    try:
        observations = _fetch_langfuse_observations(
            base_url=os.environ["LANGFUSE_HOST"],
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            experiment_id=args.experiment_id,
            run_id=args.run_id,
            page_size=args.page_size,
            max_pages=args.max_pages,
            include_observations=True,
        )
    except TraceIngestionError as e:
        print(f"[ERROR] {e.error_code}: {e}")
        sys.exit(1)

    print(f"\nFetched {len(observations)} observations.")

    # Print a quick summary of what we got
    types = {}
    for obs in observations:
        t = obs.get("type", "unknown")
        types[t] = types.get(t, 0) + 1
    print("  Observation types:", dict(sorted(types.items())))

    if observations:
        first = observations[0]
        print(f"  First span : id={first.get('id')} name={first.get('name')!r} start={first.get('startTime')}")
        last = observations[-1]
        print(f"  Last span  : id={last.get('id')} name={last.get('name')!r} start={last.get('startTime')}")

        # Check that the fields TraceMetricsExtractor expects are present
        required_fields = ["id", "startTime", "input", "output", "usage"]
        missing_fields = [f for f in required_fields if f not in first]
        if missing_fields:
            print(f"\n[WARNING] First observation is missing fields: {missing_fields}")
            print("  This may cause issues in Phase 1 extraction.")
        else:
            print("\n  All required fields present (id, startTime, input, output, usage).")

    out_path = args.out
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(observations, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {out_path}  ({os.path.getsize(out_path) // 1024} KB)")
    print("\nNext step: inspect the file, then run test_extract.py on it.")


if __name__ == "__main__":
    main()
