"""Pure-Python evaluator for fault-bucketing predictions.

The fault bucketing pipeline assigns each non-``fault: *`` trace event to
zero, one, or many fault buckets (``EventClassification.related_faults``).
For pipeline optimisation we need a small, reproducible harness that:

1. Joins manual ("ground truth") labels with predicted labels by ``event_id``.
2. Reports multi-label classification quality (exact-match, Jaccard,
   micro/macro precision/recall/F1, per-class breakdown).
3. Surfaces a per-event mismatch table — including the original event
   description — so prompt edits can be targeted.
4. Reports total LLM input/output tokens and an estimated USD cost so
   prompt / payload optimisations can be measured.
5. Appends each iteration's result to a CSV log so successive prompt
   versions, batch sizes and payload trims can be compared at a glance.

This module is intentionally framework-light: it only depends on the
standard library plus pandas (for tabular output).  No network or LLM
calls are made here — it consumes already-computed predictions.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

try:
    import pandas as pd  # type: ignore
except ImportError:  # pragma: no cover - pandas is optional for non-notebook use
    pd = None  # type: ignore


LabelLike = Union[str, Iterable[str], None]


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class LabelRecord:
    """Joined manual + predicted labels for a single event."""

    event_id: str
    manual_labels: Set[str] = field(default_factory=set)
    predicted_labels: Set[str] = field(default_factory=set)
    description: str = ""

    @property
    def exact_match(self) -> bool:
        return self.manual_labels == self.predicted_labels

    @property
    def jaccard(self) -> float:
        if not self.manual_labels and not self.predicted_labels:
            return 1.0
        union = self.manual_labels | self.predicted_labels
        if not union:
            return 1.0
        return len(self.manual_labels & self.predicted_labels) / len(union)

    @property
    def true_positives(self) -> Set[str]:
        return self.manual_labels & self.predicted_labels

    @property
    def false_positives(self) -> Set[str]:
        return self.predicted_labels - self.manual_labels

    @property
    def false_negatives(self) -> Set[str]:
        return self.manual_labels - self.predicted_labels


@dataclass
class EvaluationResult:
    """Aggregate evaluation output for one iteration."""

    n_events: int
    n_evaluated: int
    n_only_manual: int
    n_only_predicted: int
    exact_match_accuracy: float
    mean_jaccard: float
    micro_precision: float
    micro_recall: float
    micro_f1: float
    macro_precision: float
    macro_recall: float
    macro_f1: float
    per_class: Dict[str, Dict[str, float]]
    confusion: Dict[str, Dict[str, int]]
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    estimated_cost_usd: Optional[float]
    iteration_label: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _to_label_set(labels: LabelLike) -> Set[str]:
    """Accept str, Iterable[str], or None and return a clean set."""
    if labels is None:
        return set()
    if isinstance(labels, str):
        labels = [labels]
    out: Set[str] = set()
    for lbl in labels:
        if lbl is None:
            continue
        s = str(lbl).strip()
        if s:
            out.add(s)
    return out


def build_label_records(
    manual_labels: Mapping[str, LabelLike],
    predicted_labels: Mapping[str, LabelLike],
    descriptions: Optional[Mapping[str, str]] = None,
) -> Tuple[List[LabelRecord], List[str], List[str]]:
    """Join manual and predicted labels by ``event_id``.

    Returns
    -------
    records:
        Records for every ``event_id`` that appears in BOTH inputs.
    only_manual:
        ``event_id`` values present only in ``manual_labels`` (missing
        prediction — counted toward recall loss).
    only_predicted:
        ``event_id`` values present only in ``predicted_labels`` (extra
        prediction — counted toward precision loss).
    """
    descriptions = descriptions or {}

    manual_keys = set(manual_labels.keys())
    pred_keys = set(predicted_labels.keys())

    common = sorted(manual_keys & pred_keys)
    only_manual = sorted(manual_keys - pred_keys)
    only_predicted = sorted(pred_keys - manual_keys)

    records: List[LabelRecord] = []
    for eid in common:
        records.append(
            LabelRecord(
                event_id=eid,
                manual_labels=_to_label_set(manual_labels[eid]),
                predicted_labels=_to_label_set(predicted_labels[eid]),
                description=str(descriptions.get(eid, "")),
            )
        )
    return records, only_manual, only_predicted


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def _safe_div(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def _per_class_counts(records: Sequence[LabelRecord]) -> Dict[str, Dict[str, int]]:
    """Compute per-class TP/FP/FN counts across all records."""
    classes: Set[str] = set()
    for r in records:
        classes |= r.manual_labels | r.predicted_labels

    counts: Dict[str, Dict[str, int]] = {
        c: {"tp": 0, "fp": 0, "fn": 0} for c in classes
    }
    for r in records:
        for c in r.true_positives:
            counts[c]["tp"] += 1
        for c in r.false_positives:
            counts[c]["fp"] += 1
        for c in r.false_negatives:
            counts[c]["fn"] += 1
    return counts


def _per_class_metrics(counts: Mapping[str, Mapping[str, int]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for c, vals in counts.items():
        tp, fp, fn = vals["tp"], vals["fp"], vals["fn"]
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        f1 = _safe_div(2 * precision * recall, precision + recall)
        out[c] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "support": tp + fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    return out


def _confusion(records: Sequence[LabelRecord]) -> Dict[str, Dict[str, int]]:
    """Pairwise confusion table.

    For each manual label ``m`` and predicted label ``p`` co-occurring on
    the same event, increment ``confusion[m][p]``.  Manual labels with no
    matching prediction are counted under ``__missed__``; predicted
    labels with no matching manual are counted under ``__spurious__``.
    """
    table: Dict[str, Dict[str, int]] = {}
    for r in records:
        if not r.manual_labels and r.predicted_labels:
            row = table.setdefault("__none__", {})
            for p in r.predicted_labels:
                row[p] = row.get(p, 0) + 1
            continue
        for m in r.manual_labels:
            row = table.setdefault(m, {})
            if not r.predicted_labels:
                row["__missed__"] = row.get("__missed__", 0) + 1
                continue
            for p in r.predicted_labels:
                row[p] = row.get(p, 0) + 1
        for p in r.false_positives:
            row = table.setdefault("__spurious__", {})
            row[p] = row.get(p, 0) + 1
    return table


def compute_metrics(
    records: Sequence[LabelRecord],
    n_only_manual: int = 0,
    n_only_predicted: int = 0,
) -> Dict[str, Any]:
    """Compute the full metric bundle from joined ``records``."""
    n = len(records)
    if n == 0:
        return {
            "n_events": 0,
            "exact_match_accuracy": 0.0,
            "mean_jaccard": 0.0,
            "micro_precision": 0.0,
            "micro_recall": 0.0,
            "micro_f1": 0.0,
            "macro_precision": 0.0,
            "macro_recall": 0.0,
            "macro_f1": 0.0,
            "per_class": {},
            "confusion": {},
        }

    exact_match = sum(1 for r in records if r.exact_match) / n
    mean_jaccard = sum(r.jaccard for r in records) / n

    counts = _per_class_counts(records)
    per_class = _per_class_metrics(counts)

    total_tp = sum(v["tp"] for v in counts.values())
    total_fp = sum(v["fp"] for v in counts.values())
    total_fn = sum(v["fn"] for v in counts.values())

    micro_precision = _safe_div(total_tp, total_tp + total_fp)
    micro_recall = _safe_div(total_tp, total_tp + total_fn)
    micro_f1 = _safe_div(2 * micro_precision * micro_recall, micro_precision + micro_recall)

    if per_class:
        macro_precision = sum(v["precision"] for v in per_class.values()) / len(per_class)
        macro_recall = sum(v["recall"] for v in per_class.values()) / len(per_class)
        macro_f1 = sum(v["f1"] for v in per_class.values()) / len(per_class)
    else:
        macro_precision = macro_recall = macro_f1 = 0.0

    return {
        "n_events": n,
        "n_only_manual": n_only_manual,
        "n_only_predicted": n_only_predicted,
        "exact_match_accuracy": exact_match,
        "mean_jaccard": mean_jaccard,
        "micro_precision": micro_precision,
        "micro_recall": micro_recall,
        "micro_f1": micro_f1,
        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "macro_f1": macro_f1,
        "per_class": per_class,
        "confusion": _confusion(records),
    }


# ---------------------------------------------------------------------------
# Token / cost reporting
# ---------------------------------------------------------------------------

def token_cost_report(
    input_tokens: int,
    output_tokens: int,
    input_price_per_1k: Optional[float] = None,
    output_price_per_1k: Optional[float] = None,
) -> Dict[str, Any]:
    """Return a flat token/cost summary.

    Pricing arguments are optional — when omitted, ``estimated_cost_usd``
    is ``None`` and only token counts are reported.  Pass model-specific
    USD rates per 1 000 tokens (e.g. GPT-4o input ~ ``0.0025``).
    """
    input_tokens = int(input_tokens or 0)
    output_tokens = int(output_tokens or 0)
    cost: Optional[float] = None
    if input_price_per_1k is not None and output_price_per_1k is not None:
        cost = (
            input_tokens * float(input_price_per_1k) / 1000.0
            + output_tokens * float(output_price_per_1k) / 1000.0
        )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": cost,
    }


# ---------------------------------------------------------------------------
# Top-level evaluate()
# ---------------------------------------------------------------------------

def evaluate(
    manual_labels: Mapping[str, LabelLike],
    predicted_labels: Mapping[str, LabelLike],
    descriptions: Optional[Mapping[str, str]] = None,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    input_price_per_1k: Optional[float] = None,
    output_price_per_1k: Optional[float] = None,
    iteration_label: str = "",
    metadata: Optional[Mapping[str, Any]] = None,
) -> EvaluationResult:
    """End-to-end evaluation entrypoint used by the notebook."""
    records, only_manual, only_predicted = build_label_records(
        manual_labels, predicted_labels, descriptions
    )
    metrics = compute_metrics(records, len(only_manual), len(only_predicted))
    cost = token_cost_report(
        input_tokens, output_tokens, input_price_per_1k, output_price_per_1k
    )
    return EvaluationResult(
        n_events=len(set(manual_labels) | set(predicted_labels)),
        n_evaluated=metrics["n_events"],
        n_only_manual=len(only_manual),
        n_only_predicted=len(only_predicted),
        exact_match_accuracy=metrics["exact_match_accuracy"],
        mean_jaccard=metrics["mean_jaccard"],
        micro_precision=metrics["micro_precision"],
        micro_recall=metrics["micro_recall"],
        micro_f1=metrics["micro_f1"],
        macro_precision=metrics["macro_precision"],
        macro_recall=metrics["macro_recall"],
        macro_f1=metrics["macro_f1"],
        per_class=metrics["per_class"],
        confusion=metrics["confusion"],
        total_input_tokens=cost["input_tokens"],
        total_output_tokens=cost["output_tokens"],
        total_tokens=cost["total_tokens"],
        estimated_cost_usd=cost["estimated_cost_usd"],
        iteration_label=iteration_label,
        metadata=dict(metadata or {}),
    )


# ---------------------------------------------------------------------------
# Inspection helpers
# ---------------------------------------------------------------------------

def mismatch_table(
    manual_labels: Mapping[str, LabelLike],
    predicted_labels: Mapping[str, LabelLike],
    descriptions: Optional[Mapping[str, str]] = None,
    *,
    only_mismatches: bool = True,
):
    """Build a per-event inspection table.

    Returns a ``pandas.DataFrame`` if pandas is installed; otherwise a
    list of dicts.  Sorted by ascending Jaccard so the worst predictions
    surface first — ideal for prompt-refinement triage.
    """
    records, only_manual, only_predicted = build_label_records(
        manual_labels, predicted_labels, descriptions
    )

    rows: List[Dict[str, Any]] = []
    for r in records:
        if only_mismatches and r.exact_match:
            continue
        rows.append({
            "event_id": r.event_id,
            "manual": sorted(r.manual_labels),
            "predicted": sorted(r.predicted_labels),
            "missed": sorted(r.false_negatives),
            "spurious": sorted(r.false_positives),
            "exact_match": r.exact_match,
            "jaccard": round(r.jaccard, 4),
            "description": r.description,
        })

    descriptions = descriptions or {}
    for eid in only_manual:
        rows.append({
            "event_id": eid,
            "manual": sorted(_to_label_set(manual_labels[eid])),
            "predicted": [],
            "missed": sorted(_to_label_set(manual_labels[eid])),
            "spurious": [],
            "exact_match": False,
            "jaccard": 0.0,
            "description": str(descriptions.get(eid, "[NO PREDICTION]")),
        })
    for eid in only_predicted:
        rows.append({
            "event_id": eid,
            "manual": [],
            "predicted": sorted(_to_label_set(predicted_labels[eid])),
            "missed": [],
            "spurious": sorted(_to_label_set(predicted_labels[eid])),
            "exact_match": False,
            "jaccard": 0.0,
            "description": str(descriptions.get(eid, "[NO MANUAL LABEL]")),
        })

    rows.sort(key=lambda r: (r["jaccard"], r["event_id"]))

    if pd is None:  # pragma: no cover
        return rows
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Loaders for the canonical ground-truth + prediction file shapes
# ---------------------------------------------------------------------------

def _short_snippet(value: Any, limit: int = 200) -> str:
    """Render any value as a short single-line string for display."""
    if value is None:
        return ""
    if isinstance(value, str):
        s = value
    else:
        try:
            s = json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            s = str(value)
    s = " ".join(s.split())
    return s[:limit]


def load_ground_truth(
    path: Union[str, Path],
) -> Tuple[Dict[str, List[str]], Dict[str, str], Dict[str, Any]]:
    """Load a ``ground_truth_labels.json`` file.

    Expected shape::

        {
          "experiment_id": "...", "run_id": "...",
          "faults": { "pod-cpu-hog": {...}, ... },
          "labels": [
             { "event_id": "...", "name": "...",
               "related_faults": ["pod-cpu-hog", ...],
               "unclassified_reason": "...", "labeling_notes": "..." },
             ...
          ]
        }

    Returns
    -------
    manual_labels:
        ``{event_id: [fault_id, ...]}``
    descriptions:
        ``{event_id: "<name> | <labeling_notes or unclassified_reason>"}``
    metadata:
        ``{"experiment_id", "run_id", "faults", "n_labels"}``
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    labels = raw.get("labels", []) or []
    manual: Dict[str, List[str]] = {}
    descs: Dict[str, str] = {}
    for lbl in labels:
        eid = lbl.get("event_id")
        if not eid:
            continue
        manual[eid] = list(lbl.get("related_faults") or [])
        name = lbl.get("name", "")
        note = lbl.get("labeling_notes") or lbl.get("unclassified_reason") or ""
        descs[eid] = " | ".join(s for s in (name, note) if s).strip()

    meta = {
        "source_path": str(path),
        "experiment_id": raw.get("experiment_id"),
        "run_id": raw.get("run_id"),
        "faults": raw.get("faults", {}),
        "n_labels": len(labels),
    }
    return manual, descs, meta


def load_predictions(
    path: Union[str, Path],
) -> Tuple[Dict[str, List[str]], Dict[str, str], Dict[str, int], Dict[str, Any]]:
    """Load a ``batch_classification_trace.json`` file.

    Expected shape: a list of objects, each with ``event_id``, ``name``,
    ``span_name``, ``input``, ``output``, ``classification.related_faults``,
    ``tokens_in``, ``tokens_out``.

    Returns
    -------
    predicted_labels:
        ``{event_id: [fault_id, ...]}``
    descriptions:
        ``{event_id: "<name>: <input snippet>"}`` (fallback when GT does
        not provide one)
    tokens:
        ``{"input_tokens", "output_tokens", "total_tokens"}`` summed over
        all entries.
    metadata:
        ``{"source_path", "n_events", "n_llm_classified", "n_deterministic"}``
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(
            f"Expected a JSON list at top level of {path}, got {type(raw).__name__}"
        )

    preds: Dict[str, List[str]] = {}
    descs: Dict[str, str] = {}
    in_tok = out_tok = 0
    n_llm = n_det = 0

    for item in raw:
        if not isinstance(item, dict):
            continue
        eid = item.get("event_id")
        if not eid:
            continue
        cls = item.get("classification") or {}
        preds[eid] = list(cls.get("related_faults") or [])

        name = item.get("name") or item.get("span_name") or ""
        snippet = _short_snippet(item.get("input"))
        descs[eid] = (f"{name}: {snippet}" if snippet else name).strip(": ").strip()

        in_tok += int(item.get("tokens_in") or 0)
        out_tok += int(item.get("tokens_out") or 0)

        if item.get("deterministic_assignment"):
            n_det += 1
        elif item.get("source") == "llm":
            n_llm += 1

    tokens = {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
    }
    meta = {
        "source_path": str(path),
        "n_events": len(raw),
        "n_llm_classified": n_llm,
        "n_deterministic": n_det,
    }
    return preds, descs, tokens, meta


# ---------------------------------------------------------------------------
# Loader for the actual bucketing pipeline output
# ---------------------------------------------------------------------------

def load_labels_from_bucket_dir(
    bucket_dir: Union[str, Path],
    *,
    manifest_glob: str = "*_bucketing_manifest.json",
    bucket_glob: str = "*_bucket_*.json",
    unclassified_glob: str = "*_unclassified.json",
) -> Tuple[Dict[str, List[str]], Dict[str, str], Dict[str, int]]:
    """Reverse-engineer per-event predictions from a bucketing run.

    The pipeline writes one JSON per fault containing the events assigned
    to that fault.  This helper inverts that mapping into:

    ``{event_id: [fault_id, ...]}``

    so it can feed ``evaluate()`` directly.  Also returns short event
    descriptions (``"<name>: <input snippet>"``) and the token usage
    block from the manifest when present.
    """
    bucket_dir = Path(bucket_dir)
    if not bucket_dir.is_dir():
        raise FileNotFoundError(f"Bucket directory not found: {bucket_dir}")

    predictions: Dict[str, List[str]] = {}
    descriptions: Dict[str, str] = {}

    bucket_files = sorted(
        f for f in bucket_dir.glob(bucket_glob)
        if "_bucketing_manifest" not in f.name and "_unclassified" not in f.name
    )
    for bf in bucket_files:
        try:
            data = json.loads(bf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fault_id = data.get("fault_id") or data.get("fault_name") or bf.stem
        for evt in data.get("events", []) or []:
            eid = evt.get("id") or evt.get("event_id")
            if not eid:
                continue
            predictions.setdefault(eid, []).append(fault_id)
            if eid not in descriptions:
                name = evt.get("name", "")
                snippet = ""
                raw_in = evt.get("input")
                if isinstance(raw_in, str):
                    snippet = raw_in[:200]
                elif raw_in is not None:
                    try:
                        snippet = json.dumps(raw_in, default=str)[:200]
                    except (TypeError, ValueError):
                        snippet = str(raw_in)[:200]
                descriptions[eid] = f"{name}: {snippet}".strip(": ").strip()

    for uf in bucket_dir.glob(unclassified_glob):
        try:
            data = json.loads(uf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        events = data.get("events", data) if isinstance(data, dict) else data
        if not isinstance(events, list):
            continue
        for evt in events:
            if not isinstance(evt, dict):
                continue
            eid = evt.get("id") or evt.get("event_id")
            if not eid:
                continue
            predictions.setdefault(eid, [])
            if eid not in descriptions:
                descriptions[eid] = f"{evt.get('name', '')} [unclassified]".strip()

    tokens: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for mf in bucket_dir.glob(manifest_glob):
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        used = data.get("llm_tokens_used") or {}
        for k in tokens:
            tokens[k] += int(used.get(k, 0) or 0)
        break

    return predictions, descriptions, tokens


# ---------------------------------------------------------------------------
# Iteration log
# ---------------------------------------------------------------------------

_LOG_COLUMNS = [
    "timestamp",
    "iteration_label",
    "n_evaluated",
    "n_only_manual",
    "n_only_predicted",
    "exact_match_accuracy",
    "mean_jaccard",
    "micro_precision",
    "micro_recall",
    "micro_f1",
    "macro_precision",
    "macro_recall",
    "macro_f1",
    "total_input_tokens",
    "total_output_tokens",
    "total_tokens",
    "estimated_cost_usd",
    "metadata",
]


def log_iteration(
    result: EvaluationResult,
    log_path: Union[str, Path],
) -> Path:
    """Append ``result`` to a CSV iteration log (creates header if new)."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.exists()

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "iteration_label": result.iteration_label,
        "n_evaluated": result.n_evaluated,
        "n_only_manual": result.n_only_manual,
        "n_only_predicted": result.n_only_predicted,
        "exact_match_accuracy": round(result.exact_match_accuracy, 6),
        "mean_jaccard": round(result.mean_jaccard, 6),
        "micro_precision": round(result.micro_precision, 6),
        "micro_recall": round(result.micro_recall, 6),
        "micro_f1": round(result.micro_f1, 6),
        "macro_precision": round(result.macro_precision, 6),
        "macro_recall": round(result.macro_recall, 6),
        "macro_f1": round(result.macro_f1, 6),
        "total_input_tokens": result.total_input_tokens,
        "total_output_tokens": result.total_output_tokens,
        "total_tokens": result.total_tokens,
        "estimated_cost_usd": (
            round(result.estimated_cost_usd, 6)
            if result.estimated_cost_usd is not None else ""
        ),
        "metadata": json.dumps(result.metadata, default=str, ensure_ascii=False),
    }

    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_LOG_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
    return log_path
