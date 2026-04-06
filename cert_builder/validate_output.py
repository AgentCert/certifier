"""
Structural key comparison: certification report output vs groundtruth.

Values may differ (LLM-generated text, dates, counts) but the
structural keys -- field names, block types, dict keys at every
level -- must match.

Usage:
    python validate_output.py <output_path> <groundtruth_path>
"""

import argparse
import json
import sys
from pathlib import Path

passed = 0
failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}")
        if detail:
            print(f"        {detail}")


def sorted_keys(d):
    return sorted(d.keys()) if isinstance(d, dict) else []


def compare_keys(label, out, gt):
    """Compare dict keys (not values)."""
    ok = sorted_keys(out) if isinstance(out, dict) else []
    gk = sorted_keys(gt) if isinstance(gt, dict) else []
    check(f"{label} keys", ok == gk,
          f"output={ok}\n        ground={gk}")


def compare_meta(out, gt):
    print("\n=== Meta ===")
    compare_keys("meta", out, gt)

    # categories: check key structure per category
    oc = out.get("categories", [])
    gc = gt.get("categories", [])
    check("meta.categories count", len(oc) == len(gc),
          f"output={len(oc)} ground={len(gc)}")
    if oc and gc:
        compare_keys("meta.categories[0]", oc[0], gc[0])


def compare_header(out, gt):
    print("\n=== Header ===")
    compare_keys("header", out, gt)

    # Scorecard: check item keys
    od = out.get("scorecard", [])
    gd = gt.get("scorecard", [])
    check("header.scorecard count", len(od) == len(gd),
          f"output={len(od)} ground={len(gd)}")
    if od and gd:
        compare_keys("header.scorecard[0]", od[0], gd[0])

    # Findings: check item keys
    of = out.get("findings", [])
    gf = gt.get("findings", [])
    if of and gf:
        compare_keys("header.findings[0]", of[0], gf[0])


def _block_keys(block):
    """Get the key set for a content block."""
    return sorted(block.keys())


def compare_sections(out_sections, gt_sections):
    print("\n=== Sections ===")
    check("section count", len(out_sections) == len(gt_sections),
          f"output={len(out_sections)} ground={len(gt_sections)}")

    for i, (os, gs) in enumerate(zip(out_sections, gt_sections)):
        print(f"\n--- Section {i+1} (id={gs.get('id','?')}) ---")

        # Section-level keys
        compare_keys(f"  section", os, gs)

        # Section id match
        check(f"  id", os.get("id") == gs.get("id"),
              f"output={os.get('id')!r} ground={gs.get('id')!r}")

        oc = os.get("content", [])
        gc = gs.get("content", [])
        check(f"  content block count", len(oc) == len(gc),
              f"output={len(oc)} ground={len(gc)}")

        # Block type sequence
        out_types = [b.get("type") for b in oc]
        gt_types = [b.get("type") for b in gc]
        check(f"  block type sequence", out_types == gt_types,
              f"output={out_types}\n        ground={gt_types}")

        # Per-block: compare keys only
        for j, (ob, gb) in enumerate(zip(oc, gc)):
            btype = gb.get("type", "?")
            chart_type = gb.get("chart_type", "")
            label = f"chart.{chart_type}" if chart_type else btype

            ob_keys = _block_keys(ob)
            gb_keys = _block_keys(gb)
            check(f"  block[{j}] {label} keys",
                  ob_keys == gb_keys,
                  f"output={ob_keys}\n        ground={gb_keys}")

            # For nested structures, check key shapes
            if btype == "table":
                # Check row count matches column count (structural)
                oh = ob.get("headers", [])
                gh = gb.get("headers", [])
                check(f"  block[{j}] {label} column count",
                      len(oh) == len(gh),
                      f"output={len(oh)} ground={len(gh)}")

            elif btype == "chart":
                check(f"  block[{j}] {label} chart_type",
                      ob.get("chart_type") == gb.get("chart_type"),
                      f"output={ob.get('chart_type')!r} ground={gb.get('chart_type')!r}")

            elif btype == "card":
                # Check item key structure
                oi = ob.get("items", [])
                gi = gb.get("items", [])
                if oi and gi:
                    compare_keys(f"  block[{j}] {label} items[0]", oi[0], gi[0])

            elif btype == "assessment":
                # Just check all required keys present (values can differ)
                for key in ("title", "confidence", "body"):
                    check(f"  block[{j}] {label} has '{key}'",
                          key in ob,
                          f"missing key '{key}'")

            elif btype == "findings":
                oi = ob.get("items", [])
                gi = gb.get("items", [])
                if oi and gi:
                    compare_keys(f"  block[{j}] {label} items[0]", oi[0], gi[0])


def main():
    parser = argparse.ArgumentParser(description="Structural key comparison: certification report vs groundtruth")
    parser.add_argument("output_path", type=Path, help="Path to generated certification_report.json")
    parser.add_argument("groundtruth_path", type=Path, help="Path to groundtruth certification_report.json")
    args = parser.parse_args()

    out = json.loads(args.output_path.read_text(encoding="utf-8"))
    gt = json.loads(args.groundtruth_path.read_text(encoding="utf-8"))

    print("Certification Report: Structural Key Comparison")
    print(f"Output: {args.output_path}")
    print(f"Ground: {args.groundtruth_path}")

    # Top-level keys
    print("\n=== Top-Level ===")
    compare_keys("report", out, gt)

    compare_meta(out["meta"], gt["meta"])
    compare_header(out["header"], gt["header"])
    compare_sections(out["sections"], gt["sections"])

    # Footer
    print("\n=== Footer ===")
    check("footer key present", "footer" in out and "footer" in gt)

    # Summary
    total = passed + failed
    print(f"\n{'='*60}")
    print(f"RESULTS: {passed}/{total} passed, {failed} failed")
    if failed == 0:
        print("ALL STRUCTURAL CHECKS PASSED")
    else:
        print(f"{failed} STRUCTURAL CHECKS FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
