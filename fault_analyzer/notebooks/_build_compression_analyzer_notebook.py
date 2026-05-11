"""Build fault_bucket_compression_analyzer.ipynb."""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fault_bucket_compression_analyzer.ipynb"

cells = []

def md(*lines):
    cells.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": [l if l.endswith("\n") else l + "\n" for l in lines][:-1] + [lines[-1]],
    })

def code(src):
    cells.append({
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [l + "\n" for l in src.rstrip("\n").split("\n")],
    })

# ----------------------------------------------------------------------
md(
    "# Fault Bucket Compression Analyzer\n",
    "\n",
    "**Goal:** show that we can compress the per-fault context block sent to the classifier\n",
    "without losing classification accuracy on a single, multi-fault event.\n",
    "\n",
    "**Trace:** `data/input/08-05-26-aarya/1960bc89/56fe3250-parallel/raw_trace_parallel.json`  \n",
    "**Target span:** index `23` (a `litellm-acompletion` span where 3 faults are active).\n",
    "\n",
    "**Three cells of work:**\n",
    "1. Classify span 23 with the **full** fault context using `prompt/v2/prompt.yml` — same path as `fault_bucketing.py`.\n",
    "2. Analyze the fault bucket, design an LLM **compression prompt**, compress each active fault, and compare token counts.\n",
    "3. Re-classify span 23 with the **compressed** fault context using the same `prompt/v2/prompt.yml`.\n"
)

# ----------------------------------------------------------------------
md("## Setup")

code("""\
import os
import sys
from pathlib import Path

REPO_ROOT = Path.cwd().resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ENV_FILE = REPO_ROOT / '.env'
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE, override=False)
    print('Loaded .env via python-dotenv:', ENV_FILE if ENV_FILE.exists() else '(not found)')
except ImportError:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        print('Loaded .env via manual parser:', ENV_FILE)

print('Repo root:', REPO_ROOT)
""")

code("""\
%load_ext autoreload
%autoreload 2

import asyncio
import json
import copy
from typing import Any, Dict, List

import fault_analyzer.scripts.fault_bucketing as _fb_mod
import fault_analyzer.scripts.classifier as _fc_mod
import importlib
importlib.reload(_fb_mod)
importlib.reload(_fc_mod)

from fault_analyzer.scripts.fault_bucketing import FaultBucketingPipeline
from fault_analyzer.schema.data_models import FaultBucket, EventClassification

try:
    from utils.load_config import ConfigLoader
    config = ConfigLoader.load_config()
except Exception as e:
    print(f'ConfigLoader unavailable ({e}); falling back to empty config.')
    config = {}

try:
    import tiktoken
    _enc = tiktoken.encoding_for_model('gpt-4o')
    def n_tokens(text: str) -> int:
        return len(_enc.encode(text or ''))
    print('Token counting via tiktoken (gpt-4o)')
except Exception:
    def n_tokens(text: str) -> int:
        return max(1, len((text or '').split()) * 4 // 3)
    print('tiktoken not available; using word-based fallback (~4/3 ratio)')
""")

# ----------------------------------------------------------------------
md(
    "## Inputs\n",
    "\n",
    "Trace file is the parallel run from `08-05-26-aarya`. Target span is index 23.\n"
)

code("""\
TRACE_FILE = (
    REPO_ROOT
    / 'data' / 'input' / '08-05-26-aarya' / '1960bc89'
    / '56fe3250-parallel' / 'raw_trace_parallel.json'
)
OUTPUT_DIR = Path.cwd() / 'compression_analyzer_output'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TARGET_SPAN_INDEX = 23

assert TRACE_FILE.exists(), f'Trace file not found: {TRACE_FILE}'
print('Trace      :', TRACE_FILE)
print('Output dir :', OUTPUT_DIR)
print('Target span: index', TARGET_SPAN_INDEX)
""")

# ----------------------------------------------------------------------
md(
    "## Build pipeline + replay events 0..TARGET-1 to populate `active_faults`\n",
    "\n",
    "We reuse `FaultBucketingPipeline` (with `batch_size=1`) so we get the same `prompt/v2`\n",
    "system prompt, the same `build_user_message` rendering, and the same\n",
    "`_create_fault_bucket_from_span` deterministic bucket creation as production.\n",
    "\n",
    "Then we *only* walk through the events that come before the target span and call\n",
    "`_create_fault_bucket_from_span` for any `fault: *` span — that populates\n",
    "`pipeline.active_faults` so the classifier sees the right context for span 23.\n",
    "No LLM calls happen in this cell.\n"
)

code("""\
pipeline = FaultBucketingPipeline(
    trace_file_path=str(TRACE_FILE),
    output_dir=str(OUTPUT_DIR),
    config=config,
    batch_size=1,
    debug=True,
)

# Pick the right Azure deployment, same logic as the existing single-span notebook.
_standard_dep = os.environ.get('AZURE_OPENAI_CHAT_DEPLOYMENT_NAME')
_reasoning_dep = os.environ.get('AZURE_OPENAI_GPT5_CHAT_DEPLOYMENT_NAME')
if _standard_dep:
    pipeline._classifier._model_name = 'gpt-4o'
elif _reasoning_dep:
    pipeline._classifier._model_name = 'gpt-5.2'
else:
    raise RuntimeError(
        'Set AZURE_OPENAI_CHAT_DEPLOYMENT_NAME or AZURE_OPENAI_GPT5_CHAT_DEPLOYMENT_NAME in .env'
    )

if pipeline._classifier._max_tokens < 8000:
    pipeline._classifier._max_tokens = 8000

print('Classifier model :', pipeline._classifier._model_name)
print('Deployment       :', _standard_dep or _reasoning_dep)
print('Temperature      :', pipeline._classifier._temperature)
print('Max tokens       :', pipeline._classifier._max_tokens)
print('Prompt path      :', 'fault_analyzer/prompt/v2/prompt.yml (default in classifier config)')

raw_events = pipeline._load_trace()
sorted_events = pipeline._sort_events_chronologically(raw_events)
pipeline._extract_agent_metadata(sorted_events)

# Replay the deterministic-bucket creation up to (but not including) the target span.
for evt in sorted_events[:TARGET_SPAN_INDEX]:
    if pipeline._is_fault_name_span(evt):
        pipeline._create_fault_bucket_from_span(evt)

print()
print(f'Loaded {len(sorted_events)} events.')
print(f'Active faults at time of span {TARGET_SPAN_INDEX}:')
for fid, b in pipeline.active_faults.items():
    print(f'  - {fid:35s}  inj={b.injection_timestamp}  end={b.injection_end_timestamp}')
""")

# ----------------------------------------------------------------------
md(
    "---\n",
    "## Cell 1 — Classify span 23 with the (now PRUNED) fault context (baseline)\n",
    "\n",
    "`classifier.py` now exposes a `fault_pruning` toggle on `FaultEventClassifier`\n",
    "(default **True**, sourced from `fault_bucketing_config.json:classifier.fault_pruning`,\n",
    "overridable via `FaultBucketingPipeline(fault_pruning=...)` or by setting\n",
    "`pipeline._classifier.fault_pruning` directly).\n",
    "\n",
    "When True, `build_known_faults_block` emits the compact per-fault dict (only\n",
    "`fault_id`, `fault_name`, `injection_metadata.target`, `ground_truth.{symptoms,\n",
    "remediation}`, action names, command strings, `detection_signals`).  When False,\n",
    "the legacy verbose payload is emitted — used here to measure the savings.\n",
    "\n",
    "The next sub-cell flips the toggle off, builds the verbose block once for the\n",
    "comparison table, then flips it back on for the actual classifier call.\n"
)

code("""\
def _render_verbose_block(known):
    '''Build the pre-pruning ## Known Faults block by flipping the classifier toggle.'''
    prev = pipeline._classifier.fault_pruning
    pipeline._classifier.fault_pruning = False
    try:
        return pipeline._classifier.build_known_faults_block(known)
    finally:
        pipeline._classifier.fault_pruning = prev
""")

code("""\
from fault_analyzer.schema.data_models import parse_iso_timestamp

target_span = sorted_events[TARGET_SPAN_INDEX]
target_span_id = target_span.get('id')
target_ts = parse_iso_timestamp(target_span.get('startTime'))

print(f'Target span [{TARGET_SPAN_INDEX}]')
print(f'  id        : {target_span_id}')
print(f'  name      : {target_span.get("name")}')
print(f'  type      : {target_span.get("type")}')
print(f'  startTime : {target_span.get("startTime")}')

# Same temporal filter the production pipeline uses.
all_known = {**pipeline.active_faults, **pipeline.closed_faults}
eligible_known = pipeline._temporally_active_faults(all_known, target_ts)
eligible_by_event = {target_span_id: list(eligible_known.keys())}

print()
print('Eligible faults at this span:')
for fid in eligible_known:
    print('  -', fid)
""")

code("""\
# Build the user message exactly like classify_batch would.
baseline_user_msg = pipeline._classifier.build_user_message(
    batch=[target_span],
    known_faults=eligible_known,
    eligible_by_event=eligible_by_event,
)

baseline_known_faults_block = pipeline._classifier.build_known_faults_block(eligible_known)
baseline_system_prompt = pipeline._classifier._system_prompt

baseline_user_tokens   = n_tokens(baseline_user_msg)
baseline_faults_tokens = n_tokens(baseline_known_faults_block)
baseline_system_tokens = n_tokens(baseline_system_prompt)

# Pre-pruning verbose shape for comparison only.
verbose_block = _render_verbose_block(eligible_known)
verbose_faults_tokens = n_tokens(verbose_block)
# Approx verbose user message: same envelope, swap in the verbose block.
verbose_user_msg = baseline_user_msg.replace(
    baseline_known_faults_block.rstrip('\\n'), verbose_block.rstrip('\\n'),
)
verbose_user_tokens = n_tokens(verbose_user_msg)

print(f'{"":40s}{"VERBOSE":>10s}{"PRUNED":>10s}{"saved":>10s}')
print('-' * 70)
print(f'{"## Known Faults block tokens":40s}{verbose_faults_tokens:>10d}'
      f'{baseline_faults_tokens:>10d}{verbose_faults_tokens - baseline_faults_tokens:>+10d}')
print(f'{"full user message tokens":40s}{verbose_user_tokens:>10d}'
      f'{baseline_user_tokens:>10d}{verbose_user_tokens - baseline_user_tokens:>+10d}')
print(f'{"system prompt tokens (unchanged)":40s}{baseline_system_tokens:>10d}'
      f'{baseline_system_tokens:>10d}{0:>+10d}')
print()
pct_block = (1 - baseline_faults_tokens / max(verbose_faults_tokens, 1)) * 100
print(f'Pruning saved {verbose_faults_tokens - baseline_faults_tokens} tokens '
      f'on the fault context ({pct_block:.1f}% smaller).')
""")

code("""\
# Actually invoke the classifier — same call the pipeline makes per batch.
tokens_in_before  = pipeline._classifier.total_input_tokens
tokens_out_before = pipeline._classifier.total_output_tokens

baseline_classifications = await pipeline._classifier.classify_batch(
    [target_span], eligible_known, eligible_by_event,
)

baseline_call_in  = pipeline._classifier.total_input_tokens  - tokens_in_before
baseline_call_out = pipeline._classifier.total_output_tokens - tokens_out_before

baseline_cls = next(
    (c for c in baseline_classifications if c.event_id == target_span_id),
    None,
)

print(f'LLM call tokens : in={baseline_call_in}  out={baseline_call_out}')
print()
if baseline_cls is None:
    print('LLM omitted the event from its response.')
else:
    print('Baseline classification:')
    print(f'  related_faults     : {baseline_cls.related_faults}')
    print(f'  fault_detected     : {baseline_cls.fault_detected}')
    print(f'  fault_mitigated    : {baseline_cls.fault_mitigated}')
    print(f'  confidence         : {baseline_cls.confidence}')
    if baseline_cls.fault_reasoning:
        print('  fault_reasoning    :')
        for fid, rsn in baseline_cls.fault_reasoning.items():
            print(f'    - {fid}: {rsn[:200]}{"..." if len(rsn) > 200 else ""}')
    if baseline_cls.unclassified_reason:
        print(f'  unclassified_reason: {baseline_cls.unclassified_reason}')
""")

# ----------------------------------------------------------------------
md(
    "---\n",
    "## Cell 2 — LLM-compress each fault bucket (additional savings on top of pruning)\n",
    "\n",
    "Pruning (Cell 1) already drops everything the classifier doesn't use.\n",
    "Cell 2 layers an LLM-driven compression on top to test how far the\n",
    "*content* can be tightened: the classifier only needs `fault_id`, target\n",
    "identity, the symptom signature, and recovery signals.\n",
    "\n",
    "`render_full_fault` below builds the **pre-pruning verbose** payload as the\n",
    "compressor's input — that's the worst case and gives the LLM the most\n",
    "context to summarise from.\n"
)

code("""\
# What one fault currently costs.
sample_fid, sample_bucket = next(iter(eligible_known.items()))
sample_block = pipeline._classifier.build_known_faults_block({sample_fid: sample_bucket})
print(f'Single fault block ({sample_fid}) -> {n_tokens(sample_block)} tokens')
print()
print('--- first 60 lines ---')
print('\\n'.join(sample_block.splitlines()[:60]))
""")

code("""\
# Compression prompt. The schema below is the contract the classifier needs:
# - fault_id / fault_name : required to assign tags
# - target               : namespace + workload + label, for target disambiguation
# - injection_window     : start/end timestamps for the temporal filter (already done
#                          deterministically, but we keep them so the LLM can sanity-check)
# - symptoms             : 3-7 short strings the agent might say or observe
# - diagnostic_signals   : 2-5 tool calls / commands the agent typically runs
# - mitigation_signals   : 2-4 strings that confirm the fix landed
COMPRESSION_SYSTEM_PROMPT = '''You compress a single Kubernetes chaos-fault definition
into a compact JSON digest used as classifier context. Preserve everything an event
classifier needs to (a) recognise the fault from agent reasoning, (b) confirm it on
the right target, and (c) detect mitigation. Drop everything else.

Return ONLY a JSON object with this exact shape:

{
  "fault_id": str,
  "fault_name": str,
  "target": {"namespace": str|null, "workload_ref": str|null, "label": str|null},
  "injection_window": {"start": str|null, "end": str|null},
  "symptoms": [str, ...],            // 3-7 short fragments (<=10 words each)
  "diagnostic_signals": [str, ...],  // 2-5 tool-call / command names
  "mitigation_signals": [str, ...]   // 2-4 confirmation phrases
}

Rules:
- Keep IDs and names verbatim.
- Symptoms must be observable in agent output (e.g. "high pod CPU", "OOMKilled",
  "packet loss to service X"). Do NOT include explanations or Kubernetes theory.
- Diagnostic signals are short tool / command names (e.g. "pods_top",
  "kubectl describe pod", "events_list"). Include only ones that appear in
  ideal_course_of_action or ideal_tool_usage_trajectory.
- Mitigation signals are short phrases that, if seen in agent output, confirm
  the fault was resolved (e.g. "memory back to baseline", "no more restarts",
  "latency recovered").
- Output JSON only, no markdown fences, no commentary.'''

def render_full_fault(bucket: FaultBucket) -> str:
    '''Render the full bucket the way the classifier currently sees it.'''
    return json.dumps({
        'fault_id': bucket.fault_id,
        'fault_name': bucket.fault_name,
        'injection_timestamp': bucket.injection_timestamp,
        'injection_end_timestamp': bucket.injection_end_timestamp,
        'injection_metadata': bucket.injection_metadata,
        'severity': bucket.severity,
        'target_pod': bucket.target_pod,
        'namespace': bucket.namespace,
        'detection_signals': bucket.detection_signals,
        'ground_truth': bucket.ground_truth,
        'ideal_course_of_action': bucket.ideal_course_of_action,
        'ideal_tool_usage_trajectory': bucket.ideal_tool_usage_trajectory,
        'sla': bucket.sla,
    }, indent=2, default=str)

async def compress_fault(bucket: FaultBucket) -> dict:
    '''Ask the LLM to compress one fault bucket into the digest schema.'''
    client = pipeline._classifier._get_llm_client()
    user = (
        'Compress this fault definition into the digest JSON described in the '
        'system prompt. Source fault:\\n\\n```json\\n'
        + render_full_fault(bucket)
        + '\\n```'
    )
    result, usage = await client.with_structured_output(
        model_name=pipeline._classifier._model_name,
        messages=[{'role': 'user', 'content': user}],
        output_format=None,           # free-form JSON; we parse below
        temperature=0,
        max_tokens=600,
        system_prompt=COMPRESSION_SYSTEM_PROMPT,
    )
    # Some clients return a string, some a dict.
    if isinstance(result, str):
        text = result.strip()
        if text.startswith('```'):
            text = text.strip('`').lstrip('json').strip()
        digest = json.loads(text)
    elif isinstance(result, dict):
        digest = result
    else:
        digest = json.loads(str(result))
    digest['_usage'] = usage if isinstance(usage, dict) else {}
    return digest
""")

code("""\
# Compress every eligible fault.
compressed_digests: Dict[str, dict] = {}
compression_token_cost = {'in': 0, 'out': 0}

for fid, bucket in eligible_known.items():
    digest = await compress_fault(bucket)
    usage = digest.pop('_usage', {})
    compression_token_cost['in']  += int(usage.get('input_tokens',  0) or 0)
    compression_token_cost['out'] += int(usage.get('output_tokens', 0) or 0)
    compressed_digests[fid] = digest
    print(f'\\n=== {fid} ===')
    print(json.dumps(digest, indent=2, default=str))

print()
print(f'\\nCompression LLM total: in={compression_token_cost["in"]}  out={compression_token_cost["out"]}')
print('(One-time cost — same digest is reused for every classification call.)')
""")

code("""\
# Render the compressed `## Known Faults` block in the same shape build_known_faults_block uses.
def build_compressed_known_faults_block(digests: Dict[str, dict]) -> str:
    block = '## Known Faults (ordered by injection_timestamp)\\n\\n'
    if not digests:
        return block + 'No faults have been identified yet.\\n'
    ordered = sorted(
        digests.values(),
        key=lambda d: (d.get('injection_window', {}) or {}).get('start') or '',
    )
    return block + '```json\\n' + json.dumps(ordered, indent=2, default=str) + '\\n```\\n'

compressed_block = build_compressed_known_faults_block(compressed_digests)
compressed_block_tokens = n_tokens(compressed_block)

# Three-way comparison.
print(f'{"":34s}{"tokens":>10s}{"vs verbose":>12s}{"vs pruned":>12s}')
print('-' * 68)
print(f'{"VERBOSE   ## Known Faults block":34s}{verbose_faults_tokens:>10d}'
      f'{"":>12s}{"":>12s}')
print(f'{"PRUNED    ## Known Faults block":34s}{baseline_faults_tokens:>10d}'
      f'{(baseline_faults_tokens - verbose_faults_tokens) / max(verbose_faults_tokens,1) * 100:>11.1f}%'
      f'{"":>12s}')
print(f'{"COMPRESSED ## Known Faults block":34s}{compressed_block_tokens:>10d}'
      f'{(compressed_block_tokens - verbose_faults_tokens) / max(verbose_faults_tokens,1) * 100:>11.1f}%'
      f'{(compressed_block_tokens - baseline_faults_tokens) / max(baseline_faults_tokens,1) * 100:>11.1f}%')
print()
print(f'Pruning savings (already in main):    '
      f'-{verbose_faults_tokens - baseline_faults_tokens} tokens '
      f'(-{(1 - baseline_faults_tokens / max(verbose_faults_tokens,1)) * 100:.1f}%)')
print(f'Additional LLM-compression savings:   '
      f'-{baseline_faults_tokens - compressed_block_tokens} tokens '
      f'(-{(1 - compressed_block_tokens / max(baseline_faults_tokens,1)) * 100:.1f}% on top of pruned)')
""")

# ----------------------------------------------------------------------
md(
    "---\n",
    "## Cell 3 — Re-classify span 23 with the COMPRESSED fault context (prompt/v2 unchanged)\n",
    "\n",
    "We monkey-patch `build_known_faults_block` on this pipeline's classifier so it returns\n",
    "the compressed block. Everything else — system prompt (`prompt/v2/prompt.yml`),\n",
    "user-message envelope, `classify_batch`, structured-output schema — is unchanged.\n"
)

code("""\
_original_build_known_faults_block = pipeline._classifier.build_known_faults_block

def _patched_block(known_faults: Dict[str, FaultBucket]) -> str:
    digests_for_call = {fid: compressed_digests[fid] for fid in known_faults if fid in compressed_digests}
    if len(digests_for_call) != len(known_faults):
        # Fall back to original for any fault we did not compress.
        return _original_build_known_faults_block(known_faults)
    return build_compressed_known_faults_block(digests_for_call)

pipeline._classifier.build_known_faults_block = _patched_block

# Sanity check: compute the new full user message and prompt tokens.
compressed_user_msg = pipeline._classifier.build_user_message(
    batch=[target_span],
    known_faults=eligible_known,
    eligible_by_event=eligible_by_event,
)
compressed_user_tokens = n_tokens(compressed_user_msg)

print(f'{"":34s}{"tokens":>10s}{"delta":>12s}')
print('-' * 56)
print(f'{"system prompt (unchanged)":34s}{baseline_system_tokens:>10d}{"":>12s}')
print(f'{"FULL  user message":34s}{baseline_user_tokens:>10d}{"":>12s}')
print(f'{"COMPRESSED user message":34s}{compressed_user_tokens:>10d}'
      f'{(compressed_user_tokens - baseline_user_tokens):>+12d}')
""")

code("""\
# Actually call the classifier with the compressed context.
tokens_in_before  = pipeline._classifier.total_input_tokens
tokens_out_before = pipeline._classifier.total_output_tokens

compressed_classifications = await pipeline._classifier.classify_batch(
    [target_span], eligible_known, eligible_by_event,
)

compressed_call_in  = pipeline._classifier.total_input_tokens  - tokens_in_before
compressed_call_out = pipeline._classifier.total_output_tokens - tokens_out_before

compressed_cls = next(
    (c for c in compressed_classifications if c.event_id == target_span_id),
    None,
)

print(f'LLM call tokens : in={compressed_call_in}  out={compressed_call_out}')
print()
if compressed_cls is None:
    print('LLM omitted the event from its response.')
else:
    print('Compressed-context classification:')
    print(f'  related_faults     : {compressed_cls.related_faults}')
    print(f'  fault_detected     : {compressed_cls.fault_detected}')
    print(f'  fault_mitigated    : {compressed_cls.fault_mitigated}')
    print(f'  confidence         : {compressed_cls.confidence}')
    if compressed_cls.fault_reasoning:
        print('  fault_reasoning    :')
        for fid, rsn in compressed_cls.fault_reasoning.items():
            print(f'    - {fid}: {rsn[:200]}{"..." if len(rsn) > 200 else ""}')
    if compressed_cls.unclassified_reason:
        print(f'  unclassified_reason: {compressed_cls.unclassified_reason}')

# Restore the original method so the pipeline is clean for any further use.
pipeline._classifier.build_known_faults_block = _original_build_known_faults_block
""")

# ----------------------------------------------------------------------
md(
    "---\n",
    "## Comparison summary\n"
)

code("""\
def _setify(cls):
    return set(cls.related_faults) if cls is not None else set()

base_set = _setify(baseline_cls)
comp_set = _setify(compressed_cls)
agree    = base_set == comp_set

print(f'{"":40s}{"VERBOSE":>10s}{"PRUNED":>10s}{"COMPRESSED":>14s}')
print('-' * 80)
print(f'{"system prompt tokens":40s}{baseline_system_tokens:>10d}'
      f'{baseline_system_tokens:>10d}{baseline_system_tokens:>14d}')
print(f'{"## Known Faults block tokens":40s}{verbose_faults_tokens:>10d}'
      f'{baseline_faults_tokens:>10d}{compressed_block_tokens:>14d}')
print(f'{"full user message tokens":40s}{verbose_user_tokens:>10d}'
      f'{baseline_user_tokens:>10d}{compressed_user_tokens:>14d}')
print(f'{"LLM call input tokens (measured)":40s}{"n/a":>10s}'
      f'{baseline_call_in:>10d}{compressed_call_in:>14d}')
print(f'{"LLM call output tokens (measured)":40s}{"n/a":>10s}'
      f'{baseline_call_out:>10d}{compressed_call_out:>14d}')
print()
print(f'related_faults agree (PRUNED vs COMPRESSED)? : {agree}')
print(f'  PRUNED     -> {sorted(base_set)}')
print(f'  COMPRESSED -> {sorted(comp_set)}')
print()
print(f'Pruning savings vs verbose (already in main):')
print(f'  block:  -{verbose_faults_tokens - baseline_faults_tokens} tokens '
      f'(-{(1 - baseline_faults_tokens / max(verbose_faults_tokens,1)) * 100:.1f}%)')
print(f'  user :  -{verbose_user_tokens - baseline_user_tokens} tokens '
      f'(-{(1 - baseline_user_tokens / max(verbose_user_tokens,1)) * 100:.1f}%)')
print()
print(f'One-time compression cost (amortised across every future classification call):')
print(f'  in={compression_token_cost["in"]}  out={compression_token_cost["out"]}')
addl = baseline_faults_tokens - compressed_block_tokens
if addl > 0:
    print(f'Break-even after ~{compression_token_cost["in"] // addl} '
          f'classification calls reusing the same digests.')
else:
    print('Compression did not reduce tokens further on top of pruning.')
""")

# ----------------------------------------------------------------------
notebook = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.11"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
print(f"Wrote {OUT}")
