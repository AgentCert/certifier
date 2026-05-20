"""Generate 5 synthetic TTD scenario JSONs (same shape as ttd_by_run_category_fault.json)."""
import json
import random
import uuid
from pathlib import Path

ROOT = Path("c:/Users/meemankgupta/Music/Project/infosys/certifier")
SOURCE = ROOT / "metrics_extractor/notebooks/data/ttd_by_run_category_fault.json"
OUT_DIR = ROOT / "metrics_extractor/notebooks/data/scenarios"
OUT_DIR.mkdir(parents=True, exist_ok=True)

with open(SOURCE) as f:
    base = json.load(f)
SLA = base["sla"]

# Use a representative subset of faults that appear in real data
FAULTS = {
    "resource_fault": ["pod-cpu-hog", "pod-memory-hog"],
    "network_fault":  ["pod-network-loss"],
}


def mkrun():
    return str(uuid.uuid4())


def build(n_runs, sampler, seed):
    """sampler(fault_name, sla) -> ttd value (float | None)"""
    rng = random.Random(seed)
    runs = {}
    for _ in range(n_runs):
        rid = mkrun()
        runs[rid] = {}
        for category, faults in FAULTS.items():
            runs[rid][category] = {}
            for fault in faults:
                runs[rid][category][fault] = sampler(rng, fault, SLA[fault])
    return {"sla": SLA, "runs": runs}


# -------- Scenario 1: NORMAL --------
# All detects, values comfortably within SLA (20% - 60% of SLA), mild variance.
def s1(rng, fault, sla):
    return round(rng.uniform(sla * 0.20, sla * 0.60), 2)


# -------- Scenario 2: NULL-HEAVY --------
# 70% null, 30% real (within SLA when present).
def s2(rng, fault, sla):
    if rng.random() < 0.70:
        return None
    return round(rng.uniform(sla * 0.20, sla * 0.80), 2)


# -------- Scenario 3: LOW VARIANCE --------
# Tight cluster around 40% of SLA, stdev ~ 3% of SLA.
def s3(rng, fault, sla):
    return round(rng.gauss(sla * 0.40, sla * 0.03), 2)


# -------- Scenario 4: SLA BREACH --------
# 80% of detects exceed SLA (mix of near-breach, breach, severe). 10% null.
def s4(rng, fault, sla):
    r = rng.random()
    if r < 0.10:
        return None
    if r < 0.30:
        return round(rng.uniform(sla * 0.50, sla * 1.00), 2)   # within SLA
    if r < 0.65:
        return round(rng.uniform(sla * 1.00, sla * 1.30), 2)   # near-breach / breach
    return round(rng.uniform(sla * 1.50, sla * 3.00), 2)        # severe


# -------- Scenario 5: SMALL SAMPLE (5 runs) --------
# Realistic mix: mostly detected, one null, mild variance.
def s5(rng, fault, sla):
    if rng.random() < 0.15:
        return None
    return round(rng.uniform(sla * 0.15, sla * 0.70), 2)


scenarios = [
    ("scenario1_normal.json",        30, s1, 1),
    ("scenario2_null_heavy.json",    30, s2, 2),
    ("scenario3_low_variance.json",  30, s3, 3),
    ("scenario4_sla_breach.json",    30, s4, 4),
    ("scenario5_small_sample.json",   5, s5, 5),
]

for name, n_runs, sampler, seed in scenarios:
    payload = build(n_runs, sampler, seed)
    out = OUT_DIR / name
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)

    # quick stats
    vals = [v for r in payload["runs"].values()
              for cat in r.values()
              for v in cat.values()]
    n_null = sum(1 for v in vals if v is None)
    nums = [v for v in vals if v is not None]
    rng_str = f"{min(nums):.2f}–{max(nums):.2f}" if nums else "n/a"
    print(f"{name:35} runs={n_runs:>2}  obs={len(vals):>3}  null={n_null:>3}  range={rng_str}")

print(f"\nAll scenarios written under {OUT_DIR}")
