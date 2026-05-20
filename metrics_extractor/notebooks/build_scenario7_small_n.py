"""Small-n scenario (n=3) where LEGACY mean wins over NEW median.

Demonstration of case E from stress_test_legacy_wins.py:
  - 3 runs, 1 fault each
  - Variant A: all 3 good (0.3x, 0.4x, 0.5x SLA)
  - Variant B: swap the 3rd good obs for a breach (0.3x, 0.4x, 1.5x SLA)

Legacy mean responds smoothly. New median ignores the change because
at n=3 the median is fixed by the middle obs (0.4x SLA).
"""
import json
import statistics
from pathlib import Path

import numpy as np

ROOT = Path("c:/Users/meemankgupta/Music/Project/infosys/certifier/metrics_extractor/notebooks")
SCEN_DIR = ROOT / "data/scenarios"
OUT_JSON_A = SCEN_DIR / "scenario7a_small_n_baseline.json"
OUT_JSON_B = SCEN_DIR / "scenario7b_small_n_blind_median.json"
OUT_HTML   = ROOT / "scenario7_small_n_comparison.html"

SLA_FAULT = "pod-network-loss"
SLA_VAL   = 180.0
SLA       = {SLA_FAULT: SLA_VAL}

def build(ttd_multipliers):
    runs = {}
    for i, m in enumerate(ttd_multipliers):
        runs[f"run-{i+1}"] = {"network_fault": {SLA_FAULT: round(m * SLA_VAL, 2)}}
    return {"sla": SLA, "runs": runs}

payload_a = build([0.3, 0.4, 0.5])
payload_b = build([0.3, 0.4, 1.5])
OUT_JSON_A.write_text(json.dumps(payload_a, indent=2))
OUT_JSON_B.write_text(json.dumps(payload_b, indent=2))

# ---- pipelines (mirror prior scripts) ----
def normalize(v, sla):
    if v is None or v == 0 or sla in (None, 0): return 0.0
    r = v / sla
    if r <= 1.0: return 1 - 0.85 * r
    return max(0.0, 0.15 - 0.3 * (r - 1.0))

def legacy(data):
    sla = data["sla"]
    per_cat = {}
    for cats in data["runs"].values():
        for cat, faults in cats.items():
            scores = [normalize(t, sla[f]) for f, t in faults.items() if sla.get(f) is not None]
            if scores:
                per_cat.setdefault(cat, []).append(sum(scores) / len(scores))
    return {c: {
        "mean":   round(sum(s)/len(s), 3),
        "median": round(statistics.median(s), 3),
        "min":    round(min(s), 3),
        "max":    round(max(s), 3),
        "runs":   len(s),
    } for c, s in per_cat.items()}

SKEW, IMBAL = 0.15, 0.15
def new_pipeline(data):
    sla = data["sla"]
    pool = []
    for cats in data["runs"].values():
        for cat, faults in cats.items():
            for f, ttd in faults.items():
                s = sla.get(f)
                if s is None: pool.append((cat, "NO_SLA", None, None))
                elif ttd is None: pool.append((cat, "MISSING", 0.0, False))
                elif ttd <= 0: pool.append((cat, "INVALID_ZERO", 0.0, False))
                else: pool.append((cat, "VALID", normalize(ttd, s), ttd <= s))
    in_pool = [p for p in pool if p[1] != "NO_SLA"]
    cat_out = {}
    for cat in sorted({p[0] for p in in_pool}):
        rows = [p for p in in_pool if p[0] == cat]
        scores = np.array([r[2] for r in rows])
        n_v = sum(1 for r in rows if r[1] == "VALID")
        n_c = sum(1 for r in rows if r[3])
        cat_out[cat] = {
            "category_score": round(float(np.median(scores)), 3),
            "category_mean":  round(float(scores.mean()), 3),
            "detection_rate": round(n_v / len(rows), 3),
            "sla_compliance": round(n_c / len(rows), 3),
            "n": len(rows),
        }
    scores = np.array([p[2] for p in in_pool])
    median = float(np.median(scores)); mean = float(scores.mean())
    mean_cats = float(np.mean([c["category_score"] for c in cat_out.values()]))
    flags = []
    if abs(mean - median) > SKEW: flags.append("skewed_distribution")
    if abs(mean_cats - median) > IMBAL: flags.append("mixed_category_health")
    if len(in_pool) < 20: flags.append("low_sample_size")
    return {
        "category": cat_out,
        "cumulative": {
            "cumulative_score":     round(median, 3),
            "cumulative_mean":      round(mean, 3),
            "detection_rate":       round(sum(1 for p in in_pool if p[1] == "VALID") / len(in_pool), 3),
            "sla_compliance":       round(sum(1 for p in in_pool if p[3]) / len(in_pool), 3),
            "n_attempted":          len(in_pool),
            "quality_flags":        flags or ["none"],
        },
    }

lg_a, nw_a = legacy(payload_a), new_pipeline(payload_a)
lg_b, nw_b = legacy(payload_b), new_pipeline(payload_b)

def delta(a, b):
    try: return round(b - a, 3)
    except: return None

# ---- console ----
print("Variant A — all good (TTD multipliers 0.3, 0.4, 0.5 of SLA)")
print(f"  legacy mean = {lg_a['network_fault']['mean']}   new median = {nw_a['category']['network_fault']['category_score']}   new mean = {nw_a['category']['network_fault']['category_mean']}")
print("Variant B — last obs becomes a breach (0.3, 0.4, 1.5)")
print(f"  legacy mean = {lg_b['network_fault']['mean']}   new median = {nw_b['category']['network_fault']['category_score']}   new mean = {nw_b['category']['network_fault']['category_mean']}")
print(f"  Δ legacy     = {delta(lg_a['network_fault']['mean'], lg_b['network_fault']['mean'])}")
print(f"  Δ new median = {delta(nw_a['category']['network_fault']['category_score'], nw_b['category']['network_fault']['category_score'])}")
print(f"  Δ new mean   = {delta(nw_a['category']['network_fault']['category_mean'], nw_b['category']['network_fault']['category_mean'])}")

# ---- HTML ----
def row(label, a, b):
    d = delta(a, b)
    cls = "neg" if d is not None and d < 0 else ("pos" if d is not None and d > 0 else "zer")
    d_str = f"{'+' if d and d > 0 else ''}{d}" if d is not None else "—"
    return f"<tr><th>{label}</th><td>{a}</td><td>{b}</td><td class='{cls}'>{d_str}</td></tr>"

la = lg_a["network_fault"]["mean"]; lb = lg_b["network_fault"]["mean"]
na = nw_a["category"]["network_fault"]["category_score"]; nb = nw_b["category"]["network_fault"]["category_score"]
nma = nw_a["category"]["network_fault"]["category_mean"]; nmb = nw_b["category"]["network_fault"]["category_mean"]
fa = " ".join(f"<span class='flag'>{f}</span>" for f in nw_a["cumulative"]["quality_flags"])
fb = " ".join(f"<span class='flag'>{f}</span>" for f in nw_b["cumulative"]["quality_flags"])

html = f"""<!doctype html>
<html><head><meta charset="utf-8"/>
<title>Small-n Scenario (n=3) — Legacy wins on smoothness</title>
<style>
  :root {{ --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --bg:#fafafa; --card:#fff;
    --legacy:#b45309; --new:#2563eb; --legacy-bg:#fffbeb; --new-bg:#eff6ff;
    --mono:'SFMono-Regular',Consolas,Menlo,monospace; }}
  *{{box-sizing:border-box}}
  body{{margin:0;font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}}
  .wrap{{max-width:1080px;margin:36px auto;padding:0 24px}}
  h1{{font-size:24px;margin:0 0 6px}}
  .sub{{color:var(--muted);margin-bottom:24px}}
  .setup{{background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:12px 16px;margin-bottom:18px;font-size:13px}}
  .setup b{{color:var(--legacy)}}
  .scenario{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:18px 22px;margin:0 0 18px}}
  table{{width:100%;border-collapse:collapse;margin:6px 0;font-size:13px;background:#fff}}
  th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line)}}
  th{{font-weight:600;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.3px}}
  code{{font:12px var(--mono);background:#f3f4f6;padding:1px 5px;border-radius:3px}}
  td.pos{{color:#16a34a;font-weight:600}} td.neg{{color:#dc2626;font-weight:600}} td.zer{{color:var(--muted)}}
  .flag{{display:inline-block;background:#fef3c7;color:#92400e;font-size:11px;padding:1px 6px;border-radius:8px;margin-right:3px}}
  .verdict{{margin-top:14px;padding:12px 14px;border-radius:6px;font-size:13px;line-height:1.6;background:#fef2f2;border:1px solid #fecaca}}
  .verdict h4{{margin:0 0 8px;color:#991b1b}}
  .badge{{display:inline-block;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;padding:2px 8px;border-radius:10px;background:#dc2626;color:#fff;margin-right:8px}}
  .foot{{color:var(--muted);font-size:12px;margin-top:32px;text-align:center}}
</style></head>
<body><div class="wrap">

<h1>Small-n Scenario (n=3) — Legacy wins on smoothness</h1>
<p class="sub">A controlled case (from stress-test E) where the new pipeline's pooled-median
fails to register a real change because of where the new observation lands in the sort order.</p>

<div class="setup">
  <b>Setup:</b> 3 runs &middot; 1 fault each (<code>{SLA_FAULT}</code>, SLA = {SLA_VAL}s) &middot;
  Variant A: TTDs = 0.3, 0.4, 0.5 &times; SLA (all good) &middot;
  Variant B: TTDs = 0.3, 0.4, 1.5 &times; SLA (last one breaches) &middot;
  files: <code>{OUT_JSON_A.name}</code>, <code>{OUT_JSON_B.name}</code>
</div>

<section class="scenario">
  <h2 style="margin:0 0 12px">network_fault score — Variant A vs Variant B</h2>
  <table>
    <tr><th>metric</th><th>Variant A (all good)</th><th>Variant B (last breaches)</th><th>Δ (B − A)</th></tr>
    {row("Legacy — per-run mean → mean", la, lb)}
    {row("New — pool median", na, nb)}
    {row("New — pool mean (reference)", nma, nmb)}
  </table>

  <h4 style="margin:18px 0 6px;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.4px">New-pipeline cumulative flags</h4>
  <table>
    <tr><th></th><th>Variant A</th><th>Variant B</th></tr>
    <tr><th>quality_flags</th><td>{fa}</td><td>{fb}</td></tr>
    <tr><th>n_attempted</th><td>{nw_a['cumulative']['n_attempted']}</td><td>{nw_b['cumulative']['n_attempted']}</td></tr>
  </table>

  <div class="verdict">
    <h4><span class="badge">Legacy pipeline wins</span> Median is blind at n=3</h4>
    <p><b>What happened:</b> swapping the 3rd obs (0.5&times;SLA, score 0.575) for a breach
    (1.5&times;SLA, score 0.0) is a <em>real degradation</em>. Legacy mean drops by
    <b>{abs(delta(la, lb))}</b>. New median moves by <b>{abs(delta(na, nb)) or 0}</b> — it sits
    at the middle obs (0.4&times;SLA, score 0.66), which didn't change.</p>
    <p><b>Why:</b> at n=3, the median is fully determined by the middle element. The largest
    and smallest can move arbitrarily and the median stays put. Mean uses every data point,
    so it always responds.</p>
    <p><b>Mitigations the new pipeline does (or should) provide:</b></p>
    <ul style="margin:6px 0 0 18px;padding:0">
      <li>Pool <em>mean</em> dropped from {nma} to {nmb} (Δ {delta(nma, nmb)}) — same magnitude
      as legacy, surfaced as a secondary signal.</li>
      <li><code>sla_compliance</code> drops from
      <b>{nw_a['category']['network_fault']['sla_compliance']}</b> to
      <b>{nw_b['category']['network_fault']['sla_compliance']}</b> — the breach is visible there.</li>
      <li><code>low_sample_size</code> flag fires (n &lt; 20) — the customer is warned the
      headline is built on thin data either way.</li>
    </ul>
    <p><b>Takeaway:</b> at very small n, median's robustness becomes blindness. Either widen
    the window (combine weeks), or have the cert-builder lean on
    <code>sla_compliance</code> + <code>cumulative_mean</code> when
    <code>low_sample_size</code> is set.</p>
  </div>
</section>

<p class="foot">Generated by <code>build_scenario7_small_n.py</code></p>
</div></body></html>
"""
OUT_HTML.write_text(html, encoding="utf-8")
print(f"\nWrote {OUT_HTML}")
