import json, uuid

nb_path = 'c:/Users/meemankgupta/Music/Project/infosys/certifier/fault_analyzer/notebooks/per_span_sweep_v2.ipynb'
nb = json.load(open(nb_path, encoding='utf-8'))


def cell(cell_type, source, cid=None):
    return {
        "cell_type": cell_type,
        "id": cid or uuid.uuid4().hex[:8],
        "metadata": {},
        "source": source if isinstance(source, list) else [source],
        **({"outputs": [], "execution_count": None} if cell_type == "code" else {}),
    }


AUDIT_MD = """\
## Fault Context Token Audit

Measure exactly how many tokens the `## Known Faults` block occupies
before any compression. This is the block sent in **every** LLM call —
47 calls \u00d7 fault-block size \u2248 the bulk of total cost.

| abbrev | field |
|---|---|
| `meta` | `injection_metadata` (target, timing, probes, workflow) |
| `gt` | `ground_truth` (symptoms, goal, remediation) |
| `ica` | `ideal_course_of_action` |
| `iut` | `ideal_tool_usage_trajectory` |\
"""

AUDIT_CODE = """\
ordered = sorted(known_faults.items(), key=lambda kv: (kv[1].injection_timestamp or ''))
fctx_raw = [{
    'fault_id': fid, 'fault_name': b.fault_name,
    'injection_timestamp': b.injection_timestamp,
    'injection_end_timestamp': b.injection_end_timestamp,
    'injection_metadata': b.injection_metadata,
    'severity': b.severity, 'target_pod': b.target_pod, 'namespace': b.namespace,
    'detection_signals': b.detection_signals, 'ground_truth': b.ground_truth,
    'ideal_course_of_action': b.ideal_course_of_action,
    'ideal_tool_usage_trajectory': b.ideal_tool_usage_trajectory,
    'sla': b.sla,
} for fid, b in ordered]

fault_block_str = (
    '## Known Faults (ordered by injection_timestamp)\\n\\n'
    + '```json\\n' + json.dumps(fctx_raw, indent=2, default=str) + '\\n```\\n'
)
fault_block_tok = tok(fault_block_str)

print(f'Fault block characters : {len(fault_block_str):,}')
print(f'Fault block tokens     : {fault_block_tok:,}')
print(f'Tokens per fault (avg) : {fault_block_tok // len(fctx_raw):,}')
print(f'47 calls x fault block : {fault_block_tok * 47:,} tokens')
print()
print(f'{"fault_id":<22} {"total":>7} {"meta":>7} {"gt":>7} {"ica":>7} {"iut":>7}')
print('-' * 60)
for row in fctx_raw:
    fid  = row['fault_id']
    t    = tok(json.dumps(row, default=str))
    meta = tok(json.dumps(row.get('injection_metadata') or {}, default=str))
    gt   = tok(json.dumps(row.get('ground_truth') or {}, default=str))
    ica  = tok(json.dumps(row.get('ideal_course_of_action') or [], default=str))
    iut  = tok(json.dumps(row.get('ideal_tool_usage_trajectory') or [], default=str))
    print(f'{fid:<22} {t:>7,} {meta:>7,} {gt:>7,} {ica:>7,} {iut:>7,}')
print()
sample_msg = build_user_message([non_fault[10]], known_faults, include_input=True)
sample_tok  = tok(sample_msg)
pct = 100 * fault_block_tok / sample_tok
print(f'Sample full user-msg (event 10)  : {sample_tok:,} tokens')
print(f'  of which fault block           : {fault_block_tok:,} tokens  ({pct:.0f}%)')
print(f'  of which event payload + instr : {sample_tok - fault_block_tok:,} tokens  ({100-pct:.0f}%)')\
"""

COMPRESS_MD = """\
## LLM Fault Compression

Ask the LLM to distil each fault into the smallest text that still lets
the classifier disambiguate targets.

Target format per fault:
```
id | name | ns/<ns> label/<label> workload/<workload> | end:<ts> ramp:<N>s | symptoms: <s1>; <s2>; <s3>
```
Target: **\u2264 80 tokens per fault** (down from ~3\u202f875 today).

Compression runs once offline; the digest is reused for all 47 per-span calls.\
"""

COMPRESS_CODE = r"""COMPRESS_SYSTEM = (
    "You are a token-budget optimizer for LLM prompts. "
    "Your output is used verbatim as fault context in a downstream classifier."
)

COMPRESS_USER_TMPL = (
    "Compress the following fault metadata into a single line of <= 80 tokens.\n\n"
    "Keep exactly:\n"
    "  fault_id | fault_name | ns/<namespace> label/<label> workload/<workload_ref>"
    " | end:<injection_end_timestamp> ramp:<ramp_time_sec>s | symptoms: <s1>; <s2>; <s3>\n\n"
    "Rules:\n"
    "- Preserve fault_id verbatim.\n"
    "- Keep top-3 shortest symptoms from ground_truth.fault_description_goal_remediation.symptoms.\n"
    "- Drop everything else (ideal_course_of_action, ideal_tool_usage_trajectory, probes,\n"
    "  workflow, injection.verdict, severity, sla, detection_signals).\n"
    "- Output ONLY the compressed line — no commentary, no JSON, no markdown fences.\n\n"
    "Fault metadata:\n{fault_json}"
)

async def compress_one(fid, bucket):
    fault_payload = {
        'fault_id': fid,
        'fault_name': bucket.fault_name,
        'injection_end_timestamp': bucket.injection_end_timestamp,
        'injection_metadata': {
            'target': (bucket.injection_metadata or {}).get('target', {}),
            'timing': (bucket.injection_metadata or {}).get('timing', {}),
        },
        'ground_truth': {
            'fault_description_goal_remediation': (
                (bucket.ground_truth or {})
                .get('fault_description_goal_remediation', {})
            ),
        },
    }
    user_msg = COMPRESS_USER_TMPL.format(
        fault_json=json.dumps(fault_payload, indent=2, default=str)
    )
    result, usage = await client.with_structured_output(
        model_name=classifier._model_name,
        messages=[{'role': 'user', 'content': user_msg}],
        output_format=None,
        temperature=0.0,
        max_tokens=120,
        system_prompt=COMPRESS_SYSTEM,
    )
    raw = result.get('response', '') if isinstance(result, dict) else str(result)
    compressed = raw.strip().strip('`').strip()
    return compressed, usage

compressed_faults = {}
print('Compressing faults via LLM...')
for fid, bucket in known_faults.items():
    digest, usage = await compress_one(fid, bucket)
    compressed_faults[fid] = digest
    t = tok(digest)
    print(f'  {fid:<22}  {t:3d} tok  |  {digest[:110]}')
    print(f'               LLM: in={usage.get("input_tokens",0)} out={usage.get("output_tokens",0)}')"""

COMPARE_MD = "## Comparison — raw block vs LLM-compressed digest"

COMPARE_CODE = """\
def build_compressed_fault_block(kf, digests):
    ordered = sorted(kf.items(), key=lambda kv: (kv[1].injection_timestamp or ''))
    header = '## Known Faults\\n'
    lines  = [digests.get(fid, f'{fid} (no digest)') for fid, _ in ordered]
    return header + '\\n'.join(lines) + '\\n'

compressed_block = build_compressed_fault_block(known_faults, compressed_faults)
comp_tok = tok(compressed_block)

print('=== Compressed block ===')
print(compressed_block)
print()
fmt_w = 30
print(f'{"Format":<{fmt_w}} {"Tokens":>8} {"Tok/fault":>10} {"vs raw":>10}')
print('-' * (fmt_w + 32))
print(f'{"Raw (full JSON)":<{fmt_w}} {fault_block_tok:>8,} {fault_block_tok//len(known_faults):>10,} {"---":>10}')
reduction = 100 * (1 - comp_tok / fault_block_tok)
print(f'{"LLM digest":<{fmt_w}} {comp_tok:>8,} {comp_tok//len(known_faults):>10,} {f"-{reduction:.0f}%":>10}')
print()
print('Per-fault token counts (target <= 80):')
for fid in known_faults:
    d = compressed_faults.get(fid, '')
    t = tok(d)
    bar = '#' * min(t, 80) + ('!' * (t - 80) if t > 80 else '.' * (80 - t))
    flag = ' ** OVER 80 **' if t > 80 else ''
    print(f'  {fid:<22}  {t:3d}/80  [{bar[:50]}]{flag}')
print()
digest_path = OUT_DIR / 'fault_digests.json'
digest_path.write_text(json.dumps(compressed_faults, indent=2), encoding='utf-8')
print('Saved ->', digest_path)\
"""

new_cells = [
    cell("markdown", AUDIT_MD),
    cell("code",     AUDIT_CODE),
    cell("markdown", COMPRESS_MD),
    cell("code",     COMPRESS_CODE),
    cell("markdown", COMPARE_MD),
    cell("code",     COMPARE_CODE),
]

# insert after index 7 (build_user_message code cell, 0-indexed)
nb['cells'] = nb['cells'][:8] + new_cells + nb['cells'][8:]

with open(nb_path, 'w', encoding='utf-8') as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
print(f'Written — total cells: {len(nb["cells"])}')
