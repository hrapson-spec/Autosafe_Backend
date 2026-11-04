"""G7 evaluator: live calibration-bias, discrimination and band monitoring on matched outcomes.

Spec sources (ROOT = work/garage_mvp):
  PLAN_rev3_20261026.md:96         pooled latest 300 matched outcomes, per-workshop checks at >=100, bias bound
                                   +-5 pp, monotonic band rates, AUC no worse than baseline minus 0.02; action =
                                   hide percentages/report, no retraining; endpoint F-or-PRS
  protocol/AMENDMENT_DRAFT.md:57   PROPOSED clarification (Henri must approve): point |bias| > 5 pp is the
                                   immediate trigger; a two-sided 95% vehicle-bootstrap interval is reported as
                                   uncertainty without waiting for significance; also hide on non-monotone band
                                   rates or AUC < baseline - .02; empty band => unassessable, percentages
                                   withheld pending support; no automatic retraining/recentring.

IMPLEMENTED FROM THE SPEC: pooled window 300, workshop minimum 100, |bias| bound 5 pp (strict >), AUC margin
.02 (strict <), bootstrap interval for bias, monotonicity, empty-band handling, no retraining.
NOT SPECIFIED -> required arguments of `G7Params` (no defaults; see REPORT.md open decisions):
  workshop_window            how many of a workshop's latest outcomes the per-workshop check uses
  min_band_n                 minimum outcomes per band before monotonicity is assessable
  baseline_auc               frozen baseline AUC (float) or None to use per-record `baseline_score`
  clarification_approved     whether Henri has approved AMENDMENT_DRAFT.md:57; recorded in the result
Assumptions flagged (not fixed by the text): "latest" is ordered by `observed_utc` then booking_id;
bias = mean(predicted) - observed rate, with `predicted` the unrounded calibrated probability; the per-workshop
check covers bias only (AUC and band checks are pooled); band and AUC checks use the pooled window;
the two readings "pooled>=300 AND each workshop>=100" and "two separate checks" differ - the second is used.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ._stats import (DEFAULT_N_BOOT, DEFAULT_SEED, ClusterSampler, MonitoringInputError, auc, percentile_interval)
from .bands import BANDS, evaluate_bands
from .reference import parse_utc

POOLED_WINDOW = 300          # rev3:96; AMENDMENT:57
MIN_WORKSHOP_N = 100         # rev3:96; AMENDMENT:57
BIAS_BOUND_PP = 5.0          # rev3:96; AMENDMENT:57
AUC_MARGIN = 0.02            # rev3:96; AMENDMENT:57
_TOL = 1e-9                  # floating-point tolerance on the strict comparisons


@dataclass(frozen=True)
class G7Params:
    # --- required: not fixed by the spec text (open decisions) ---
    workshop_window: int
    min_band_n: int
    baseline_auc: Optional[float]
    clarification_approved: bool
    # --- specified ---
    pooled_window: int = POOLED_WINDOW
    min_workshop_n: int = MIN_WORKSHOP_N
    bias_bound_pp: float = BIAS_BOUND_PP
    auc_margin: float = AUC_MARGIN

    def validate(self) -> None:
        for name in ("workshop_window", "min_band_n", "pooled_window", "min_workshop_n"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise MonitoringInputError(f"{name} must be a positive integer")
        if self.workshop_window < self.min_workshop_n:
            raise MonitoringInputError("workshop_window must be >= min_workshop_n")
        if self.baseline_auc is not None and not (isinstance(self.baseline_auc, (int, float))
                                                  and 0.0 <= self.baseline_auc <= 1.0):
            raise MonitoringInputError("baseline_auc must be in [0, 1] or None")
        if not isinstance(self.clarification_approved, bool):
            raise MonitoringInputError("clarification_approved must be a bool")


def _prepare(records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in records:
        bid = r.get("booking_id")
        if bid is None or bid in seen:
            raise MonitoringInputError(f"booking_id missing or duplicated: {bid!r}")
        seen.add(bid)
        p, y = r.get("predicted"), r.get("y")
        if not (isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and 0.0 <= p <= 1.0):
            raise MonitoringInputError(f"{bid}: predicted must be a probability in [0, 1]")
        if y not in (0, 1):
            raise MonitoringInputError(f"{bid}: y must be 0 or 1 (F-or-PRS)")
        if r.get("band") not in BANDS:
            raise MonitoringInputError(f"{bid}: band must be one of {BANDS}")
        d = dict(r)
        d["_t"] = parse_utc(r.get("observed_utc"), f"{bid}.observed_utc")
        d["y"], d["predicted"] = int(y), float(p)
        out.append(d)
    out.sort(key=lambda d: (d["_t"], str(d["booking_id"])))
    return out


def _bias_block(window: Sequence[Mapping[str, Any]], bound: float, n_boot: int, seed: int) -> Dict[str, Any]:
    n = len(window)
    pred = [w["predicted"] for w in window]
    ys = [w["y"] for w in window]
    bias = (sum(pred) / n - sum(ys) / n) * 100.0
    blk: Dict[str, Any] = {"n": n, "mean_predicted": sum(pred) / n, "observed_rate": sum(ys) / n,
                           "bias_pp": bias, "bound_pp": bound, "breach": abs(bias) > bound + _TOL,
                           "ci_lo_pp": None, "ci_hi_pp": None}
    if n_boot > 0:
        sampler = ClusterSampler([w.get("vehicle_id") for w in window])
        rng = random.Random(seed)
        ds = []
        for _ in range(n_boot):
            idx = sampler.sample(rng)
            m = len(idx)
            ds.append((sum(pred[i] for i in idx) / m - sum(ys[i] for i in idx) / m) * 100.0)
        blk["ci_lo_pp"], blk["ci_hi_pp"] = percentile_interval(ds, 0.95)
        blk["bootstrap"] = {"n_boot": n_boot, "seed": seed, "level": 0.95, "sides": "two",
                            "cluster": "vehicle_id (row if absent)"}
    return blk


def _auc_block(window: Sequence[Mapping[str, Any]], params: G7Params, n_boot: int, seed: int) -> Dict[str, Any]:
    scores = [w["predicted"] for w in window]
    ys = [w["y"] for w in window]
    a = auc(scores, ys)
    per_record = params.baseline_auc is None
    base_scores = None
    if per_record:
        if any(w.get("baseline_score") is None for w in window):
            raise MonitoringInputError("baseline_auc is None, so every record needs a 'baseline_score'")
        base_scores = [float(w["baseline_score"]) for w in window]
        base = auc(base_scores, ys)
    else:
        base = float(params.baseline_auc)
    blk: Dict[str, Any] = {"n": len(window), "n_pos": sum(ys), "n_neg": len(ys) - sum(ys), "auc": a,
                           "baseline_auc": base, "baseline_source": "per_record_score" if per_record else "frozen_value",
                           "margin": params.auc_margin, "assessable": a is not None and base is not None}
    if not blk["assessable"]:
        blk.update(diff=None, breach=None, ci_lo=None, ci_hi=None)
        return blk
    blk["diff"] = a - base
    blk["breach"] = a < base - params.auc_margin - _TOL
    blk["ci_lo"] = blk["ci_hi"] = None
    if n_boot > 0:
        sampler = ClusterSampler([w.get("vehicle_id") for w in window])
        rng = random.Random(seed)
        ds = []
        for _ in range(n_boot):
            idx = sampler.sample(rng)
            ys_b = [ys[i] for i in idx]
            a_b = auc([scores[i] for i in idx], ys_b)
            if a_b is None:
                continue
            if per_record:
                b_b = auc([base_scores[i] for i in idx], ys_b)
                ds.append(a_b - b_b)
            else:
                ds.append(a_b - base)
        if ds:
            blk["ci_lo"], blk["ci_hi"] = percentile_interval(ds, 0.95)
        blk["ci_is_for"] = "auc - baseline_auc" + ("" if per_record else " (baseline treated as fixed)")
        blk["bootstrap"] = {"n_boot": n_boot, "seed": seed, "level": 0.95, "valid": len(ds)}
    return blk


def evaluate_g7(records: Sequence[Mapping[str, Any]], params: G7Params, *, n_boot: int = DEFAULT_N_BOOT,
                seed: int = DEFAULT_SEED) -> Dict[str, Any]:
    """records: matched outcomes [{booking_id, vehicle_id?, workshop_id, predicted, y, band, observed_utc,
    baseline_score?}]. Returns a JSON-serialisable dict; `triggers` lists every spec'd hide-percentages cause."""
    params.validate()
    recs = _prepare(records)
    out: Dict[str, Any] = {
        "spec": {"pooled_window": params.pooled_window, "min_workshop_n": params.min_workshop_n,
                 "bias_bound_pp": params.bias_bound_pp, "auc_margin": params.auc_margin,
                 "sources": ["PLAN_rev3_20261026.md:96", "protocol/AMENDMENT_DRAFT.md:57"]},
        "open_parameters": {"workshop_window": params.workshop_window, "min_band_n": params.min_band_n,
                            "baseline_auc": params.baseline_auc},
        "spec_status": "clarification_approved" if params.clarification_approved else "PROPOSED_NOT_APPROVED",
        "n_matched_total": len(recs), "triggers": [], "action_on_trigger": "hide_percentages_and_report; no retraining",
    }
    # pooled latest-N
    if len(recs) >= params.pooled_window:
        win = recs[-params.pooled_window:]
        out["window"] = {"first_utc": win[0]["_t"].isoformat(), "last_utc": win[-1]["_t"].isoformat(),
                         "n": len(win)}
        out["pooled_bias"] = _bias_block(win, params.bias_bound_pp, n_boot, seed)
        out["auc"] = _auc_block(win, params, n_boot, seed)
        out["bands"] = evaluate_bands(win, min_n_assessable=params.min_band_n, n_boot=n_boot, seed=seed)
        if out["pooled_bias"]["breach"]:
            out["triggers"].append({"trigger": "pooled_bias", "value_pp": out["pooled_bias"]["bias_pp"],
                                    "bound_pp": params.bias_bound_pp, "scope": "pooled"})
        if out["auc"]["breach"]:
            out["triggers"].append({"trigger": "auc_below_baseline_minus_margin", "auc": out["auc"]["auc"],
                                    "baseline_auc": out["auc"]["baseline_auc"], "margin": params.auc_margin,
                                    "scope": "pooled"})
        bands = out["bands"]
        if bands["monotone_nondecreasing"] is False:
            out["triggers"].append({"trigger": "band_nonmonotone", "scope": "pooled"})
        if not bands["assessable"]:
            out["triggers"].append({"trigger": "band_unassessable_percentages_withheld",
                                    "empty_bands": bands["empty_bands"], "thin_bands": bands["thin_bands"],
                                    "scope": "pooled"})
    else:
        out["pooled_bias"] = out["auc"] = out["bands"] = None
    # per workshop (bias only)
    by_ws: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for r in recs:
        by_ws[r.get("workshop_id")].append(r)
    ws_out = []
    for ws in sorted(by_ws, key=str):
        rs = by_ws[ws]
        if len(rs) < params.min_workshop_n:
            ws_out.append({"workshop_id": ws, "n_total": len(rs), "evaluated": False})
            continue
        blk = _bias_block(rs[-params.workshop_window:], params.bias_bound_pp, n_boot, seed)
        blk.update(workshop_id=ws, n_total=len(rs), evaluated=True)
        ws_out.append(blk)
        if blk["breach"]:
            out["triggers"].append({"trigger": "workshop_bias", "workshop_id": ws, "value_pp": blk["bias_pp"],
                                    "bound_pp": params.bias_bound_pp, "scope": "workshop"})
    out["workshops"] = ws_out
    evaluated_any = out["pooled_bias"] is not None or any(w["evaluated"] for w in ws_out)
    out["status"] = "insufficient_data" if not evaluated_any else ("breach" if out["triggers"] else "ok")
    return out
