"""Build side-by-side HTML comparing:
  - LEGACY: loaded from validate_ttd_logic_scenarios_output.json (notebook output)
  - NEW:    computed by running metric_scorecard_pipeline logic against same scenarios
Per-scenario blocks, no extra commentary - just the numbers as both notebooks produce them.
"""
import json
from pathlib import Path
import numpy as np

ROOT = Path("c:/Users/meemankgupta/Music/Project/infosys/certifier/metrics_extractor/notebooks")
SCEN_DIR = ROOT / "data/scenarios"
LEGACY_JSON = ROOT / "validate_ttd_logic_scenarios_output.json"
OUT_HTML = ROOT / "scenarios_side_by_side.html"

# ---------- new-pipeline logic (mirrors metric_scorecard_pipeline.ipynb) ----------
# Detection-weighted: central tendency over the DETECTED subset, multiplied by
# detection_rate. Misses contribute via the multiplier, not as 0s injected into
# the central-tendency input.
SKEW, IMBAL = 0.15, 0.15

def normalize(value, sla):
    if value is None or value == 0 or sla in (None, 0):
        return 0.0
    r = value / sla
    if r <= 1.0:
        return 1 - 0.85 * r
    return max(0.0, 0.15 - 0.3 * (r - 1.0))

def _central(detected, n_total):
    """Confidence-tiered central tendency on detected scores only."""
    if n_total < 3:
        return None
    if len(detected) == 0:
        return 0.0
    if n_total < 5:
        return float(detected.mean())
    if n_total < 20:
        return float(0.7 * np.median(detected) + 0.3 * np.percentile(detected, 5))
    return float(0.5 * np.median(detected)
                 + 0.3 * np.percentile(detected, 5)
                 + 0.2 * np.percentile(detected, 1))

def _weighted(detected_list, n_total):
    central = _central(np.array(detected_list), n_total)
    if central is None:
        return None, 0.0
    det_rate = len(detected_list) / n_total if n_total else 0.0
    return central * det_rate, det_rate

def run_new(data):
    sla = data["sla"]
    # Tuple shape: (run_id, cat, sub_fault, status, score, compliant)
    pool = []
    for run_id, cats in data["runs"].items():
        for cat, faults in cats.items():
            for f, ttd in faults.items():
                s = sla.get(f)
                if s is None:
                    pool.append((run_id, cat, f, "NO_SLA", None, None))
                elif ttd is None:
                    pool.append((run_id, cat, f, "MISSING", 0.0, False))
                elif ttd <= 0:
                    pool.append((run_id, cat, f, "INVALID_ZERO", 0.0, False))
                else:
                    pool.append((run_id, cat, f, "VALID", normalize(ttd, s), ttd <= s))

    in_pool = [p for p in pool if p[3] != "NO_SLA"]
    if not in_pool:
        return {"category": {}, "cumulative": {}}

    cat_out = {}
    for cat in sorted({p[1] for p in in_pool}):
        rows = [p for p in in_pool if p[1] == cat]
        detected = [r[4] for r in rows if r[3] == "VALID"]
        n_compl = sum(1 for r in rows if r[5])
        n_runs_cat = len({r[0] for r in rows})
        if detected:
            cat_central = float(np.median(np.array(detected)))
            det_rate = len(detected) / len(rows)
            score = cat_central * det_rate
        else:
            score, det_rate = 0.0, 0.0
        cat_out[cat] = {
            "category_score": round(score, 3),
            "detection_rate": round(det_rate, 3),
            "sla_compliance": round(n_compl / len(rows), 3),
            "n": n_runs_cat,
        }

    detected_all = [p[4] for p in in_pool if p[3] == "VALID"]
    n_obs = len(in_pool)
    n_runs = len({p[0] for p in in_pool})
    n_valid = len(detected_all)
    n_compl = sum(1 for p in in_pool if p[5])
    n_no_sla = sum(1 for p in pool if p[3] == "NO_SLA")

    if n_valid:
        d_median = float(np.median(detected_all))
        det_rate = n_valid / n_obs
        headline = d_median * det_rate
    else:
        d_median = 0.0
        det_rate = 0.0
        headline = 0.0

    flags = []
    if n_valid >= 2:
        det_arr = np.array(detected_all)
        if abs(float(det_arr.mean()) - d_median) > SKEW:
            flags.append("skewed_distribution")
    if len(cat_out) >= 2:
        scores = [c["category_score"] for c in cat_out.values()]
        if max(scores) - min(scores) > IMBAL:
            flags.append("mixed_category_health")
    if n_no_sla:
        flags.append(f"{n_no_sla}_obs_excluded_no_sla")

    return {
        "category": cat_out,
        "cumulative": {
            "cumulative_score": round(headline, 3),
            "detection_rate": round(det_rate, 3),
            "sla_compliance": round(n_compl / n_obs, 3),
            "n_attempted": n_runs,
            "quality_flags": flags or ["none"],
        },
    }

# ---------- load both sides ----------
legacy_all = json.loads(LEGACY_JSON.read_text())

scenarios = []
for name, payload in legacy_all.items():
    data = json.loads((SCEN_DIR / payload["file"]).read_text())
    scenarios.append({
        "key": name,
        "label": name.replace("scenario", "Scenario ").replace("_", " "),
        "file": payload["file"],
        "n_runs": payload["n_runs"],
        "n_valid": payload["n_valid_obs"],
        "n_null": payload["n_null_obs"],
        "legacy_cats": payload["category_overall"],
        "new": run_new(data),
    })

# ---------- HTML ----------
def legacy_table(cats):
    rows = "".join(
        f"<tr><td><code>{c}</code></td>"
        f"<td><b>{s['mean']}</b></td><td>{s['median']}</td>"
        f"<td>{s['stdev']}</td><td>{s['min']} – {s['max']}</td>"
        f"<td>{s['count_runs']}</td></tr>"
        for c, s in sorted(cats.items())
    )
    return ("<table><tr><th>category</th><th>mean</th><th>median</th>"
            "<th>stdev</th><th>range</th><th>runs</th></tr>" + rows + "</table>")

def new_tables(res):
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

# delta row: legacy mean vs new median per category, plus cumulative
def delta_table(s):
    new_cats = s["new"]["category"]
    rows = ""
    for c in sorted(set(list(s["legacy_cats"].keys()) + list(new_cats.keys()))):
        lg = s["legacy_cats"].get(c, {}).get("mean", "—")
        nw = new_cats.get(c, {}).get("category_score", "—")
        try:
            d = round(float(nw) - float(lg), 3)
            d_str = f"{'+' if d > 0 else ''}{d}"
            cls = "pos" if d > 0 else ("neg" if d < 0 else "zer")
        except Exception:
            d_str, cls = "—", "zer"
        rows += (f"<tr><td><code>{c}</code></td><td>{lg}</td><td>{nw}</td>"
                 f"<td class='{cls}'>{d_str}</td></tr>")
    return ("<table class='delta'><tr><th>category</th><th>legacy mean</th>"
            "<th>new median</th><th>Δ (new − legacy)</th></tr>" + rows + "</table>")

# Per-scenario verdict: which side gives the more honest customer-facing answer
VERDICTS = {
    "scenario1_normal": {
        "winner": "tie",
        "text": "Both pipelines converge (mean ≈ median when the distribution is dense and "
                "well-behaved). The new pipeline is no <em>more</em> accurate here, but it adds "
                "<code>detection_rate</code> and <code>quality_flags</code> at no cost. "
                "Tie on the headline, edge to new on diagnostics.",
    },
    "scenario2_null_heavy": {
        "winner": "new",
        "text": "Legacy reports 0.16 / 0.21 for an agent that misses 67% of faults — a number "
                "no single run actually produced. New reports a <b>detection-weighted</b> headline: "
                "<code>median(detected) × detection_rate</code>. The headline is non-zero (the "
                "successful tail scored ~0.4 on average), but is heavily discounted by the 33% "
                "detection rate. Customer sees both signals fused into one honest number, plus "
                "<code>detection_rate</code> reported alongside for clarity.",
    },
    "scenario3_low_variance": {
        "winner": "tie",
        "text": "Tight cluster around 40% of SLA → mean and median are nearly identical. Both "
                "pipelines agree. As with Scenario 1, the new pipeline adds trust signals for "
                "free but produces the same headline.",
    },
    "scenario4_sla_breach": {
        "winner": "new",
        "text": "80% of detects breach SLA. Legacy mean (0.12 / 0.14) is inflated by the 20% "
                "within-SLA tail — a customer reads it as 'weakly okay'. New <code>median(detected) "
                "× detection_rate</code> lands very low (breach band × high detection); "
                "<code>sla_compliance</code> shows the rest of the story: agent detects fine but "
                "is consistently too slow.",
    },
    "scenario5_small_sample": {
        "winner": "new-with-caveat",
        "text": "Both pipelines are fragile at n=5 runs. New is structurally better — every obs "
                "gets one vote instead of being halved inside per-run means — but it currently "
                "does <em>not</em> flag low sample size. Recommended fix: add "
                "<code>low_sample_size</code> to <code>quality_flags</code> when "
                "<code>n_attempted &lt; 20</code>. Without that, neither pipeline warns the "
                "customer that the headline is built on thin data.",
    },
    "scenario6_category_imbalance": {
        "winner": "new",
        "text": "Two categories, very different health: resource_fault healthy (~0.65), "
                "network_fault failing (0.0 with 33% detection). Legacy reports each category "
                "separately but provides no single headline that surfaces the imbalance — the "
                "consumer has to spot it by eye. New pipeline emits a detection-weighted "
                "<code>cumulative_score</code> that's discounted by the failing category, "
                "plus a <code>mixed_category_health</code> flag that says explicitly: "
                "<em>the categories disagree, don't trust the headline alone</em>. Failing "
                "network category drops to ~0.26 (still non-zero — surfaces the successful "
                "detect-tail) rather than flat 0.",
    },
    "scenario7a_small_n_baseline": {
        "winner": "tie",
        "text": "Baseline for the n=3 pair. All three obs within SLA (0.3/0.4/0.5 &times; SLA). "
                "Legacy mean, new median, new pool-mean all converge on <b>0.66</b>. Sets the "
                "reference point against which Scenario 7b's blind-median pathology can be measured.",
    },
    "scenario7b_small_n_blind_median": {
        "winner": "legacy",
        "text": "<b>One of the few cases where legacy genuinely wins.</b> Swap the 3rd good obs "
                "for a breach (1.5 &times; SLA, normalized 0.0). Legacy mean drops from 0.66 to "
                "<b>0.468</b> (Δ −0.192). New median stays at <b>0.66</b> — at n=3 the median is "
                "pinned to the middle obs, and the changed value lands at position 3, so the "
                "headline ignores a real degradation. Mitigations the new pipeline still provides: "
                "<code>sla_compliance</code> drops 1.00 → 0.67, pool mean drops by the same "
                "magnitude as legacy, and a <code>low_sample_size</code> flag (if added at n &lt; 20) "
                "would warn the consumer. Lesson: at very small n, median's robustness becomes "
                "blindness — widen the window or lean on <code>sla_compliance</code>.",
    },
}

WINNER_BADGE = {
    "new":              ("New pipeline wins",                "badge-new"),
    "new-with-caveat":  ("New pipeline wins (with caveat)",  "badge-new-caveat"),
    "legacy":           ("Legacy pipeline wins",             "badge-legacy"),
    "tie":              ("Tie — both agree",                 "badge-tie"),
}

def verdict_block(scen_key):
    v = VERDICTS.get(scen_key)
    if not v:
        return ""
    label, cls = WINNER_BADGE[v["winner"]]
    return (f"<div class='verdict v-{v['winner']}'>"
            f"<span class='badge {cls}'>{label}</span> "
            f"<span class='verdict-text'>{v['text']}</span></div>")

# Overall summary table
SUMMARY_ROWS = [
    ("Normal",              "Tie",                  "Distribution is dense → mean ≈ median. Both fine."),
    ("Null-heavy",          "New",                  "Legacy hides 67% miss rate behind a fractional mean. New surfaces it with score 0 + detection_rate + flag."),
    ("Low variance",        "Tie",                  "Tight cluster → mean ≈ median. Both fine."),
    ("SLA breach",          "New",                  "Legacy mean inflated by within-SLA tail. New median sits in the breach band where typical attempt lives."),
    ("Small sample",        "New (with caveat)",    "New treats each obs as one vote (more honest). But neither flags low sample size — fix recommended."),
    ("Category imbalance",  "New",                  "Two categories with very different health. New surfaces it via mixed_category_health flag; legacy has no equivalent."),
    ("Small-n baseline",    "Tie",                  "n=3, all good. Both converge on 0.66 as expected."),
    ("Small-n blind median","Legacy",               "n=3 with 1 breach. Median is pinned to middle obs → ignores the breach. Legacy mean responds. sla_compliance still catches it."),
]

def summary_html():
    rows = "".join(
        f"<tr><td><b>{name}</b></td><td>{winner}</td><td>{why}</td></tr>"
        for name, winner, why in SUMMARY_ROWS
    )
    return ("<div class='summary'><h2 style='margin:0 0 10px;font-size:16px'>"
            "Verdict summary — which pipeline tells the truth?</h2>"
            "<table><tr><th>Scenario</th><th>Winner</th><th>Why</th></tr>"
            + rows + "</table>"
            "<p class='summary-foot'>Bottom line: legacy and new agree on benign distributions. "
            "On the failure modes that <em>actually matter</em> at production scale (lots of misses, "
            "lots of breaches, bimodal data, mixed-health categories), legacy systematically "
            "over-reports because mean is pulled by tails — new pipeline's pooled median + "
            "<code>quality_flags</code> + <code>detection_rate</code> together produce a "
            "customer-honest summary. The one place legacy still wins is very small n "
            "(Scenario 7b): the median can be pinned to a middle obs and miss real changes at "
            "the tails. Mitigation: pool over longer windows or lean on "
            "<code>sla_compliance</code> + <code>cumulative_mean</code> when n is small.</p></div>")

blocks = ""
for s in scenarios:
    blocks += f"""
    <section class="scenario">
      <h2>{s['label']} <span class="meta">·
          {s['n_runs']} runs · {s['n_valid']} valid obs · {s['n_null']} nulls ·
          <code>{s['file']}</code></span></h2>

      <div class="grid">
        <div class="col legacy">
          <h3>Legacy — <code>validate_ttd_logic_scenarios.ipynb</code></h3>
          <p class="hint">Per-run mean of fault scores → mean/median/stdev across runs.
             Stops at category. NO_SLA silently dropped.</p>
          {legacy_table(s['legacy_cats'])}
        </div>
        <div class="col new">
          <h3>New — <code>metric_scorecard_pipeline.ipynb</code></h3>
          <p class="hint">Detection-weighted: pool every obs, separate detected vs missed,
             score = <code>detection_rate × median(detected)</code>. NO_SLA flagged.</p>
          {new_tables(s['new'])}
        </div>
      </div>

      <div class="delta-block">
        <h4>Side-by-side delta</h4>
        {delta_table(s)}
      </div>

      {verdict_block(s['key'])}
    </section>
    """

html = f"""<!doctype html>
<html><head><meta charset="utf-8"/>
<title>TTD Scenarios — Legacy vs New, Side by Side</title>
<style>
  :root {{
    --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb;
    --bg:#fafafa; --card:#fff; --legacy:#b45309; --new:#2563eb;
    --legacy-bg:#fffbeb; --new-bg:#eff6ff;
    --mono:'SFMono-Regular',Consolas,Menlo,monospace;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}}
  .wrap{{max-width:1180px;margin:36px auto;padding:0 24px}}
  h1{{font-size:24px;margin:0 0 6px}}
  .sub{{color:var(--muted);margin-bottom:28px}}
  .scenario{{background:var(--card);border:1px solid var(--line);border-radius:8px;
            padding:18px 22px;margin:0 0 18px}}
  .scenario h2{{font-size:17px;margin:0 0 14px}}
  .scenario .meta{{color:var(--muted);font-weight:400;font-size:12.5px}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
  .col{{padding:14px;border-radius:6px}}
  .col.legacy{{background:var(--legacy-bg);border:1px solid #fde68a}}
  .col.new{{background:var(--new-bg);border:1px solid #bfdbfe}}
  .col.legacy h3{{color:var(--legacy)}}
  .col.new h3{{color:var(--new)}}
  h3{{margin:0 0 6px;font-size:13.5px}}
  h4{{margin:14px 0 6px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}}
  .hint{{margin:0 0 10px;font-size:12px;color:var(--muted)}}
  table{{width:100%;border-collapse:collapse;margin:6px 0;font-size:12.5px;background:#fff}}
  th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}}
  th{{font-weight:600;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.3px}}
  code{{font:12px var(--mono);background:#f3f4f6;padding:1px 5px;border-radius:3px}}
  table.cum{{margin-top:8px}}
  table.cum th{{width:46%}}
  .flag{{display:inline-block;background:#fef3c7;color:#92400e;font-size:11px;
        padding:1px 6px;border-radius:8px;margin-right:3px}}
  .delta-block{{margin-top:14px;padding:10px 14px;background:#f9fafb;border-radius:6px;border:1px solid var(--line)}}
  table.delta td.pos{{color:#16a34a;font-weight:600}}
  table.delta td.neg{{color:#dc2626;font-weight:600}}
  table.delta td.zer{{color:var(--muted)}}
  .verdict{{margin-top:14px;padding:12px 14px;border-radius:6px;font-size:13px;line-height:1.55}}
  .verdict.v-new{{background:#ecfdf5;border:1px solid #a7f3d0}}
  .verdict.v-new-with-caveat{{background:#fefce8;border:1px solid #fde68a}}
  .verdict.v-legacy{{background:#fef2f2;border:1px solid #fecaca}}
  .verdict.v-tie{{background:#f3f4f6;border:1px solid var(--line)}}
  .verdict-text{{color:var(--ink)}}
  .badge{{display:inline-block;font-size:11px;font-weight:700;text-transform:uppercase;
         letter-spacing:.4px;padding:2px 8px;border-radius:10px;margin-right:8px}}
  .badge-new{{background:#16a34a;color:#fff}}
  .badge-new-caveat{{background:#d97706;color:#fff}}
  .badge-legacy{{background:#dc2626;color:#fff}}
  .badge-tie{{background:#6b7280;color:#fff}}
  .summary{{background:var(--card);border:1px solid var(--line);border-radius:8px;
           padding:18px 22px;margin:0 0 24px}}
  .summary table th,.summary table td{{padding:7px 10px;vertical-align:top}}
  .summary-foot{{margin:14px 0 0;font-size:13px;color:var(--ink);
                background:#eff6ff;border-left:3px solid var(--new);padding:10px 14px;border-radius:4px}}
  .foot{{color:var(--muted);font-size:12px;margin-top:32px;text-align:center}}
</style>
</head><body><div class="wrap">

<h1>TTD Scenarios — Legacy vs New, Side by Side</h1>
<p class="sub">Legacy numbers loaded from
<code>validate_ttd_logic_scenarios_output.json</code> (notebook output).
New numbers computed by running the <code>metric_scorecard_pipeline</code> logic against the
same 5 scenario JSONs in <code>data/scenarios/</code>.</p>

{summary_html()}

{blocks}

<p class="foot">Generated by <code>build_scenarios_side_by_side.py</code></p>
</div></body></html>
"""

OUT_HTML.write_text(html, encoding="utf-8")
print(f"Wrote {OUT_HTML}\n")
for s in scenarios:
    cum = s["new"]["cumulative"]
    print(f"{s['label']:30}  cumulative={cum.get('cumulative_score', '—')}  "
          f"detect={cum.get('detection_rate', '—')}  flags={cum.get('quality_flags', ['—'])}")
