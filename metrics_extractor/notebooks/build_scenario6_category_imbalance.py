"""Create a hybrid scenario: network_fault = null-heavy (s2), resource_fault = low-variance (s3).
Mimics a realistic agent: consistent on resource faults, frequently misses network faults.
Then run BOTH pipelines and compare.
"""
import json
import random
import statistics
import uuid
from pathlib import Path

import numpy as np

ROOT = Path("c:/Users/meemankgupta/Music/Project/infosys/certifier/metrics_extractor/notebooks")
SCEN_DIR = ROOT / "data/scenarios"
OUT_JSON = SCEN_DIR / "scenario6_category_imbalance.json"
OUT_HTML = ROOT / "scenario6_category_imbalance_comparison.html"

SLA = json.loads((SCEN_DIR / "scenario1_normal.json").read_text())["sla"]

FAULTS = {
    "resource_fault": ["pod-cpu-hog", "pod-memory-hog"],  # low-variance (s3)
    "network_fault":  ["pod-network-loss"],               # null-heavy (s2)
}
N_RUNS = 30
SEED = 23

# --- samplers ---
def s2_null_heavy(rng, fault):
    if rng.random() < 0.70:
        return None
    return round(rng.uniform(SLA[fault] * 0.20, SLA[fault] * 0.80), 2)

def s3_low_variance(rng, fault):
    return round(rng.gauss(SLA[fault] * 0.40, SLA[fault] * 0.03), 2)

SAMPLER_BY_CAT = {
    "resource_fault": s3_low_variance,
    "network_fault":  s2_null_heavy,
}

rng = random.Random(SEED)
runs = {}
for _ in range(N_RUNS):
    rid = str(uuid.uuid4())
    runs[rid] = {}
    for cat, faults in FAULTS.items():
        runs[rid][cat] = {}
        sampler = SAMPLER_BY_CAT[cat]
        for f in faults:
            runs[rid][cat][f] = sampler(rng, f)

payload = {"sla": SLA, "runs": runs}
OUT_JSON.write_text(json.dumps(payload, indent=2))

# quick stats
vals = [v for r in runs.values() for c in r.values() for v in c.values()]
print(f"Wrote {OUT_JSON.name}: {N_RUNS} runs, {len(vals)} obs, "
      f"{sum(1 for v in vals if v is None)} null, "
      f"range {min((v for v in vals if v is not None)):.2f}-"
      f"{max((v for v in vals if v is not None)):.2f}\n")

# ==================== PIPELINE IMPLEMENTATIONS ====================
def normalize(v, sla):
    if v is None or v == 0 or sla in (None, 0): return 0.0
    r = v / sla
    if r <= 1.0: return 1 - 0.85 * r
    return max(0.0, 0.15 - 0.3 * (r - 1.0))

# LEGACY
def legacy(data):
    sla = data["sla"]
    per_run = {}
    for rid, cats in data["runs"].items():
        for cat, faults in cats.items():
            scores = []
            for f, ttd in faults.items():
                if sla.get(f) is None: continue
                scores.append(normalize(ttd, sla[f]))
            if scores:
                per_run.setdefault(cat, []).append(sum(scores) / len(scores))
    out = {}
    for cat, s in per_run.items():
        out[cat] = {
            "mean":   round(sum(s) / len(s), 3),
            "median": round(statistics.median(s), 3),
            "stdev":  round(statistics.stdev(s) if len(s) > 1 else 0.0, 3),
            "min":    round(min(s), 3),
            "max":    round(max(s), 3),
            "runs":   len(s),
        }
    return out

# NEW
SKEW, IMBAL = 0.15, 0.15
def new_pipeline(data):
    sla = data["sla"]
    pool = []
    for cats in data["runs"].values():
        for cat, faults in cats.items():
            for f, ttd in faults.items():
                s = sla.get(f)
                if s is None:
                    pool.append((cat, "NO_SLA", None, None))
                elif ttd is None:
                    pool.append((cat, "MISSING", 0.0, False))
                elif ttd <= 0:
                    pool.append((cat, "INVALID_ZERO", 0.0, False))
                else:
                    pool.append((cat, "VALID", normalize(ttd, s), ttd <= s))
    in_pool = [p for p in pool if p[1] != "NO_SLA"]
    cat_out = {}
    for cat in sorted({p[0] for p in in_pool}):
        rows = [p for p in in_pool if p[0] == cat]
        scores = np.array([r[2] for r in rows])
        n_v = sum(1 for r in rows if r[1] == "VALID")
        n_c = sum(1 for r in rows if r[3])
        cat_out[cat] = {
            "category_score": round(float(np.median(scores)), 3),
            "detection_rate": round(n_v / len(rows), 3),
            "sla_compliance": round(n_c / len(rows), 3),
            "n": len(rows),
        }
    scores = np.array([p[2] for p in in_pool])
    median = float(np.median(scores)); mean = float(scores.mean())
    mean_cats = float(np.mean([c["category_score"] for c in cat_out.values()]))
    n_v = sum(1 for p in in_pool if p[1] == "VALID")
    n_c = sum(1 for p in in_pool if p[3])
    flags = []
    if abs(mean - median) > SKEW: flags.append("skewed_distribution")
    if abs(mean_cats - median) > IMBAL: flags.append("mixed_category_health")
    return {
        "category": cat_out,
        "cumulative": {
            "cumulative_score": round(median, 3),
            "detection_rate": round(n_v / len(in_pool), 3),
            "sla_compliance": round(n_c / len(in_pool), 3),
            "n_attempted": len(in_pool),
            "quality_flags": flags or ["none"],
        },
    }

lg = legacy(payload)
nw = new_pipeline(payload)

# ==================== CONSOLE SUMMARY ====================
print("LEGACY (per-run mean → mean across runs):")
for c, s in sorted(lg.items()):
    print(f"  {c:18}  mean={s['mean']:.3f}  median={s['median']:.3f}  stdev={s['stdev']:.3f}  range={s['min']}-{s['max']}  runs={s['runs']}")

print("\nNEW (pool-median + flags):")
for c, s in nw["category"].items():
    print(f"  {c:18}  score={s['category_score']:.3f}  detect={s['detection_rate']:.3f}  sla_compl={s['sla_compliance']:.3f}  n={s['n']}")
cum = nw["cumulative"]
print(f"  cumulative={cum['cumulative_score']}  detect={cum['detection_rate']}  sla_compl={cum['sla_compliance']}  flags={cum['quality_flags']}")

# ==================== HTML COMPARISON ====================
def fmt_legacy(cats):
    rows = "".join(
        f"<tr><td><code>{c}</code></td><td><b>{s['mean']}</b></td><td>{s['median']}</td>"
        f"<td>{s['stdev']}</td><td>{s['min']} - {s['max']}</td><td>{s['runs']}</td></tr>"
        for c, s in sorted(cats.items())
    )
    return ("<table><tr><th>category</th><th>mean</th><th>median</th><th>stdev</th>"
            "<th>range</th><th>runs</th></tr>" + rows + "</table>")

def fmt_new(res):
    cats = res["category"]; cum = res["cumulative"]
    cat_rows = "".join(
        f"<tr><td><code>{c}</code></td><td><b>{s['category_score']}</b></td>"
        f"<td>{s['detection_rate']}</td><td>{s['sla_compliance']}</td><td>{s['n']}</td></tr>"
        for c, s in cats.items()
    )
    cat_tbl = ("<table><tr><th>category</th><th>score</th><th>detect</th>"
               "<th>sla_compl</th><th>n</th></tr>" + cat_rows + "</table>")
    flags = " ".join(f"<span class='flag'>{f}</span>" for f in cum["quality_flags"])
    cum_tbl = (
        "<table class='cum'>"
        f"<tr><th>cumulative_score</th><td><b>{cum['cumulative_score']}</b></td></tr>"
        f"<tr><th>detection_rate</th><td>{cum['detection_rate']}</td></tr>"
        f"<tr><th>sla_compliance</th><td>{cum['sla_compliance']}</td></tr>"
        f"<tr><th>n_attempted</th><td>{cum['n_attempted']}</td></tr>"
        f"<tr><th>quality_flags</th><td>{flags}</td></tr></table>"
    )
    return cat_tbl + cum_tbl

# delta
delta_rows = ""
for c in sorted(set(lg.keys()) | set(nw["category"].keys())):
    l = lg.get(c, {}).get("mean", "-")
    n = nw["category"].get(c, {}).get("category_score", "-")
    try:
        d = round(float(n) - float(l), 3)
        d_str = f"{'+' if d > 0 else ''}{d}"
        cls = "pos" if d > 0 else ("neg" if d < 0 else "zer")
    except: d_str, cls = "-", "zer"
    delta_rows += f"<tr><td><code>{c}</code></td><td>{l}</td><td>{n}</td><td class='{cls}'>{d_str}</td></tr>"

html = f"""<!doctype html>
<html><head><meta charset="utf-8"/>
<title>Hybrid Scenario (s2+s3) - Legacy vs New</title>
<style>
  :root {{
    --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --bg:#fafafa; --card:#fff;
    --legacy:#b45309; --new:#2563eb; --legacy-bg:#fffbeb; --new-bg:#eff6ff;
    --mono:'SFMono-Regular',Consolas,Menlo,monospace;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}}
  .wrap{{max-width:1180px;margin:36px auto;padding:0 24px}}
  h1{{font-size:24px;margin:0 0 6px}}
  .sub{{color:var(--muted);margin-bottom:24px}}
  .setup{{background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:12px 16px;margin-bottom:18px;font-size:13px}}
  .setup b{{color:var(--new)}}
  .scenario{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:18px 22px;margin:0 0 18px}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
  .col{{padding:14px;border-radius:6px}}
  .col.legacy{{background:var(--legacy-bg);border:1px solid #fde68a}}
  .col.new{{background:var(--new-bg);border:1px solid #bfdbfe}}
  .col.legacy h3{{color:var(--legacy)}} .col.new h3{{color:var(--new)}}
  h3{{margin:0 0 6px;font-size:13.5px}}
  h4{{margin:14px 0 6px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}}
  .hint{{margin:0 0 10px;font-size:12px;color:var(--muted)}}
  table{{width:100%;border-collapse:collapse;margin:6px 0;font-size:12.5px;background:#fff}}
  th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}}
  th{{font-weight:600;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.3px}}
  code{{font:12px var(--mono);background:#f3f4f6;padding:1px 5px;border-radius:3px}}
  table.cum{{margin-top:8px}} table.cum th{{width:46%}}
  .flag{{display:inline-block;background:#fef3c7;color:#92400e;font-size:11px;padding:1px 6px;border-radius:8px;margin-right:3px}}
  .delta-block{{margin-top:14px;padding:10px 14px;background:#f9fafb;border-radius:6px;border:1px solid var(--line)}}
  table.delta td.pos{{color:#16a34a;font-weight:600}}
  table.delta td.neg{{color:#dc2626;font-weight:600}}
  .verdict{{margin-top:14px;padding:12px 14px;border-radius:6px;font-size:13px;line-height:1.6;background:#ecfdf5;border:1px solid #a7f3d0}}
  .verdict h4{{margin:0 0 8px;color:#065f46}}
  .foot{{color:var(--muted);font-size:12px;margin-top:32px;text-align:center}}
</style></head>
<body><div class="wrap">

<h1>Hybrid Scenario — Null-heavy network + Low-variance resource</h1>
<p class="sub">Combines scenario 2 (null-heavy) on <code>network_fault</code> with
scenario 3 (low-variance) on <code>resource_fault</code> — modelling a realistic agent
that detects resource faults reliably but frequently misses network faults.</p>

<div class="setup">
  <b>Setup:</b> 30 runs &middot; each run injects 3 faults (2 resource + 1 network) &middot; 90 obs total &middot;
  <code>resource_fault</code> uses s3 sampler (gauss around 0.4&times;SLA, stdev 0.03&times;SLA) &middot;
  <code>network_fault</code> uses s2 sampler (70% null, else uniform 0.2-0.8&times;SLA) &middot;
  source: <code>{OUT_JSON.name}</code>
</div>

<section class="scenario">
  <h2 style="margin:0 0 14px">Side-by-side output</h2>
  <div class="grid">
    <div class="col legacy">
      <h3>Legacy — validate_ttd_logic</h3>
      <p class="hint">Per-run mean of fault scores → mean/median/stdev across runs.</p>
      {fmt_legacy(lg)}
    </div>
    <div class="col new">
      <h3>New — metric_scorecard_pipeline</h3>
      <p class="hint">Pool every obs (misses=0). Category = median of pool. Cumulative + flags.</p>
      {fmt_new(nw)}
    </div>
  </div>

  <div class="delta-block">
    <h4>Delta — legacy mean vs new median per category</h4>
    <table class="delta"><tr><th>category</th><th>legacy mean</th><th>new median</th>
        <th>Δ (new − legacy)</th></tr>
      {delta_rows}
    </table>
  </div>

  <div class="verdict">
    <h4>What the hybrid scenario reveals</h4>
    <p><b>Resource fault:</b> both pipelines agree (~0.66). Low-variance data is the regime where
    mean ≈ median — neither tool has an edge here.</p>
    <p><b>Network fault:</b> divergence is dramatic. Legacy reports a fractional score (~0.07)
    that <em>no single run actually produced</em>; new reports <b>0.0</b> because more than half
    the network attempts were misses. The new pipeline backs this with
    <code>detection_rate</code> ≈ 0.30 — the customer can see exactly what's broken.</p>
    <p><b>Cumulative:</b> the new pipeline's <code>mixed_category_health</code> flag (if it fires)
    is the entire point of this hybrid — one category is healthy, the other is failing, and
    a single headline number that doesn't surface that imbalance is misleading by construction.
    Legacy has no equivalent signal.</p>
  </div>
</section>

<p class="foot">Generated by <code>build_hybrid_scenario.py</code></p>
</div></body></html>
"""
OUT_HTML.write_text(html, encoding="utf-8")
print(f"\nWrote {OUT_HTML}")
