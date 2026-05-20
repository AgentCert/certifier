"""Extract TTM per (run, category, sub_fault) + SLAs into a single JSON."""
import json
from pathlib import Path
import yaml

ROOT = Path("c:/Users/meemankgupta/Music/Project/infosys/certifier")
BASE_DIR = ROOT / "data/input/12-05-26-sequential-aarya-30run/fault-bucketing"
FAULT_CATEGORIES_CONFIG = ROOT / "configs/fault_categories.json"
GROUNDTRUTH_DIR = ROOT / "hypothesis_framework/data/groundtruth/kubernetes"
OUTPUT_FILE = ROOT / "metrics_extractor/notebooks/data/ttm_by_run_category_fault.json"


def load_fault_categories(path):
    with open(path, "r") as f:
        config = json.load(f)
    mapping = {}
    for category, faults in config.get("categories", {}).items():
        for fault_name in faults:
            mapping[fault_name] = category
    return mapping


def load_ttm_slas(groundtruth_dir, fault_names):
    slas = {}
    for fault_name in fault_names:
        gt_file = groundtruth_dir / fault_name / "ground_truth.yaml"
        if not gt_file.exists():
            slas[fault_name] = None
            continue
        try:
            with open(gt_file, "r") as f:
                data = yaml.safe_load(f)
            ttm = (
                data.get("ground_truth", {})
                .get("sla", {})
                .get("time_to_mitigate", {})
                .get("threshold")
            )
            slas[fault_name] = ttm
        except Exception as e:
            print(f"  ! Failed to read SLA for {fault_name}: {e}")
            slas[fault_name] = None
    return slas


def extract_ttm_by_run(base_dir, fault_category_map):
    runs = {}
    metrics_files = list(base_dir.glob("**/*_metrics.json"))
    print(f"Found {len(metrics_files)} metrics files")

    for f in metrics_files:
        try:
            with open(f, "r") as fp:
                m = json.load(fp)
        except Exception as e:
            print(f"  ! Skip {f.name}: {e}")
            continue

        run_id = m.get("run_id")
        fault_name = m.get("fault_name")
        if not run_id or not fault_name:
            continue

        ttm = m.get("quantitative", {}).get("time_to_mitigate")
        category = fault_category_map.get(fault_name, "Unknown")

        runs.setdefault(run_id, {}).setdefault(category, {})[fault_name] = ttm

    return runs


def main():
    print(f"Loading fault categories from {FAULT_CATEGORIES_CONFIG}")
    fault_category_map = load_fault_categories(FAULT_CATEGORIES_CONFIG)
    print(f"  Loaded {len(fault_category_map)} fault-to-category mappings\n")

    print(f"Loading TTM SLAs from {GROUNDTRUTH_DIR}")
    slas = load_ttm_slas(GROUNDTRUTH_DIR, fault_category_map.keys())
    for fault, ttm in sorted(slas.items()):
        print(f"  {fault:30} TTM_SLA = {ttm}s")
    print()

    print(f"Extracting TTM from {BASE_DIR}")
    runs = extract_ttm_by_run(BASE_DIR, fault_category_map)
    print(f"  Extracted {len(runs)} runs\n")

    # Sort runs deterministically
    runs_sorted = {rid: runs[rid] for rid in sorted(runs.keys())}

    output = {
        "sla": slas,
        "runs": runs_sorted,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    n_obs = sum(len(faults) for run in runs.values() for faults in run.values())
    n_null = sum(
        1
        for run in runs.values()
        for faults in run.values()
        for v in faults.values()
        if v is None
    )
    print(f"Wrote {OUTPUT_FILE}")
    print(f"  runs: {len(runs)}, observations: {n_obs}, nulls: {n_null}")


if __name__ == "__main__":
    main()
