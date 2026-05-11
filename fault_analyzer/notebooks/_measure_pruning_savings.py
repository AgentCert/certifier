"""Quick deterministic token-count comparison: VERBOSE vs PRUNED classifier context.

Runs against span 23 of the aarya parallel trace (the same target the
fault_bucket_compression_analyzer notebook uses). No LLM calls are made.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tiktoken

from fault_analyzer.scripts.fault_bucketing import FaultBucketingPipeline
from fault_analyzer.schema.data_models import parse_iso_timestamp

enc = tiktoken.encoding_for_model("gpt-4o")


def n_tokens(text: str) -> int:
    return len(enc.encode(text or ""))


TRACE_FILE = (
    REPO_ROOT
    / "data" / "input" / "08-05-26-aarya" / "1960bc89"
    / "56fe3250-parallel" / "raw_trace_parallel.json"
)
OUTPUT_DIR = Path(__file__).resolve().parent / "compression_analyzer_output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TARGET_SPAN_INDEX = 23


def main():
    pipeline = FaultBucketingPipeline(
        trace_file_path=str(TRACE_FILE),
        output_dir=str(OUTPUT_DIR),
        config={},
        batch_size=1,
        debug=False,
    )

    raw_events = pipeline._load_trace()
    sorted_events = pipeline._sort_events_chronologically(raw_events)
    pipeline._extract_agent_metadata(sorted_events)
    for evt in sorted_events[:TARGET_SPAN_INDEX]:
        if pipeline._is_fault_name_span(evt):
            pipeline._create_fault_bucket_from_span(evt)

    target_span = sorted_events[TARGET_SPAN_INDEX]
    target_ts = parse_iso_timestamp(target_span.get("startTime"))
    all_known = {**pipeline.active_faults, **pipeline.closed_faults}
    eligible = pipeline._temporally_active_faults(all_known, target_ts)

    print(f"Target span [{TARGET_SPAN_INDEX}]: {target_span.get('name')}")
    print(f"  startTime  : {target_span.get('startTime')}")
    print(f"  active fts : {list(eligible.keys())}")
    print()

    # Flip the toggle to render verbose, then back for the pruned render.
    pipeline._classifier.fault_pruning = False
    verbose_block = pipeline._classifier.build_known_faults_block(eligible)
    pipeline._classifier.fault_pruning = True
    pruned_block = pipeline._classifier.build_known_faults_block(eligible)

    v_tokens = n_tokens(verbose_block)
    p_tokens = n_tokens(pruned_block)
    saved = v_tokens - p_tokens
    pct = (saved / v_tokens) * 100 if v_tokens else 0

    print("## Known Faults block (3 active faults at span 23)")
    print(f"  VERBOSE (fault_pruning=False) : {v_tokens:>5d} tokens")
    print(f"  PRUNED  (fault_pruning=True)  : {p_tokens:>5d} tokens")
    print(f"  Saved                         : {saved:>5d} tokens   ({pct:.1f}% smaller)")
    print()

    # Per-fault breakdown.
    print("Per-fault breakdown:")
    print(f"  {'fault_id':40s} {'verbose':>9s} {'pruned':>8s} {'saved':>8s}")
    for fid, b in eligible.items():
        v = n_tokens(json.dumps(
            pipeline._classifier._verbose_fault_context(fid, b), indent=2, default=str,
        ))
        p = n_tokens(json.dumps(
            pipeline._classifier._compact_fault_context(fid, b), indent=2, default=str,
        ))
        print(f"  {fid:40s} {v:>9d} {p:>8d} {v - p:>8d}")
    print()

    # Full user-message totals (system prompt unchanged).
    eligible_by_event = {target_span.get("id"): list(eligible.keys())}
    pruned_user = pipeline._classifier.build_user_message(
        batch=[target_span], known_faults=eligible, eligible_by_event=eligible_by_event,
    )
    pruned_user_tokens = n_tokens(pruned_user)
    sys_tokens = n_tokens(pipeline._classifier._system_prompt or "")

    pipeline._classifier.fault_pruning = False
    verbose_user = pipeline._classifier.build_user_message(
        batch=[target_span], known_faults=eligible, eligible_by_event=eligible_by_event,
    )
    pipeline._classifier.fault_pruning = True
    verbose_user_tokens = n_tokens(verbose_user)

    print("Full prompt totals (system + user):")
    print(f"  system prompt              : {sys_tokens:>5d} tokens (unchanged)")
    print(f"  VERBOSE user message       : {verbose_user_tokens:>5d} tokens")
    print(f"  PRUNED  user message       : {pruned_user_tokens:>5d} tokens")
    print(f"  Per-call savings           : {verbose_user_tokens - pruned_user_tokens:>5d} tokens")
    print(f"  Total (sys+usr) verbose    : {sys_tokens + verbose_user_tokens:>5d}")
    print(f"  Total (sys+usr) pruned     : {sys_tokens + pruned_user_tokens:>5d}")


if __name__ == "__main__":
    main()
