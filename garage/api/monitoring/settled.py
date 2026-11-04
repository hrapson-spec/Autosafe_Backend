"""Settled-outcome rows -> G7 / band-order inputs, through the PRS-aware classifier.

`settled_rows` come from the database (see cli.G7_SQL): one row per booking with its settled outcome row, the
score (the policy's named column) and the stored vehicle history of the outcome snapshot. Each row is classified by
`classify.classify_booking_outcome` (NOT by outcomes.f_or_prs_from_candidates and NOT by status == 'matched'),
rows the classifier cannot resolve are excluded and counted, and the rest feed `evaluate_g7` and
`evaluate_bands`. The stored status is cross-checked against the classifier's own candidate count (matched = 1
test in the window, ambiguous = 2 or more) and a disagreement excludes the row (fail closed, counted).
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ._stats import MonitoringInputError
from .bands import evaluate_bands
from .classify import LABELS, OutcomePolicy, classify_booking_outcome
from .g7 import G7Params, evaluate_g7
from .outcomes import band_for_probability

_REQUIRED = ("booking_id", "workshop_id", "vehicle_id", "status", "schedule_date", "observed_utc",
             "score", "tests")


def build_records(settled_rows: Sequence[Mapping[str, Any]], outcome_policy: OutcomePolicy, *,
                  band_edges: Tuple[int, int], rounding_pp: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    outcome_policy.validate()
    records: List[Dict[str, Any]] = []
    labels: Counter = Counter()
    excluded = Counter()
    for r in settled_rows:
        missing = [k for k in _REQUIRED if k not in r]
        if missing:
            raise MonitoringInputError(f"settled row lacks {missing}")
        tests = [dict(t, vehicle_id=r["vehicle_id"]) for t in (r["tests"] or [])]
        c = classify_booking_outcome(tests, r["schedule_date"], outcome_policy)
        labels[c.label] += 1
        if r["status"] == "matched" and c.n_tests_in_window != 1 or \
                r["status"] == "ambiguous" and c.n_tests_in_window < 2:
            excluded["status_disagrees_with_history"] += 1
            continue
        if c.y is None:
            excluded["unresolved_" + c.label] += 1
            continue
        p = float(r["score"])
        records.append({"booking_id": r["booking_id"], "vehicle_id": r["vehicle_id"],
                        "workshop_id": r["workshop_id"], "predicted": p, "y": c.y,
                        "band": band_for_probability(p, band_edges, rounding_pp),
                        "observed_utc": r["observed_utc"], "outcome_label": c.label})
    summary = {"n_settled_rows": len(settled_rows), "n_used": len(records),
               "n_excluded": len(settled_rows) - len(records),
               "classifier_labels": {k: labels.get(k, 0) for k in LABELS},
               "excluded_by_reason": dict(sorted(excluded.items())),
               "n_used_by_label": dict(sorted(Counter(x["outcome_label"] for x in records).items())),
               "n_positive": sum(x["y"] for x in records)}
    return records, summary


def evaluate_settled(settled_rows: Sequence[Mapping[str, Any]], g7_params: G7Params, outcome_policy: OutcomePolicy, *,
                     band_edges: Tuple[int, int], rounding_pp: int, bands_only_min_n: int, n_boot: int,
                     seed: int) -> Dict[str, Any]:
    """Every argument is required. Returns {g7, bands_cumulative, outcome_classification, outcome_policy}."""
    records, summary = build_records(settled_rows, outcome_policy, band_edges=band_edges, rounding_pp=rounding_pp)
    return {"g7": evaluate_g7(records, g7_params, n_boot=n_boot, seed=seed),
            "bands_cumulative": evaluate_bands(records, min_n_assessable=g7_params.min_band_n,
                                               bands_only_min_n=bands_only_min_n, n_boot=n_boot, seed=seed),
            "outcome_classification": summary, "outcome_policy": outcome_policy.as_dict()}
