"""arm0_harness.py — the segmented scorer (the referee's measurement core). PROTECTED.

Dev-grade Arm 0: per-slice AUC + ECE over a scored frame, with deltas vs a base
score, and the PROMOTION verdict. It resolves ONLY the slices computable from the
current v57.1 frame columns and reports the rest as 'unavailable' — it never
fabricates a slice. The golden-tested-to-GF-8 version (reproduce the 0.61-0.67 cell
AUCs to <=1e-6) is the tracked upgrade; this is enough to make a candidate's verdict
real today.
"""
from __future__ import annotations

import numpy as np
from sklearn.metrics import roc_auc_score

try:
    from referee_config import SLICES
except ImportError:  # standalone import
    SLICES = []


def ece(y, p, n_bins: int = 10) -> float:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    e = 0.0
    for b in range(n_bins):
        m = idx == b
        if m.any():
            e += abs(p[m].mean() - y[m].mean()) * (m.mean())
    return float(e)


def _slice_masks(df) -> dict:
    """Defensive: build {slice: boolean mask} only for slices the frame supports.
    Unknown column names simply yield no mask for that slice (logged upstream)."""
    masks = {}
    c = set(df.columns)
    # The v57.1 frame has NO raw age / age_band (age enters only via EB rate
    # features). Age slices are therefore built from vehicle_age_years when an age
    # candidate injects it — comparing base-vs-candidate within narrow age bands is
    # the within-band-resolution test (does raw age beat the EB gradient), not circular.
    if "vehicle_age_years" in c:
        a = df["vehicle_age_years"]
        ok = a >= 0  # exclude the MISSING sentinel (-1)
        masks["age_le_3"] = ok & (a <= 3)
        masks["age_4_7"] = ok & (a > 3) & (a <= 7)
        masks["age_8_12"] = ok & (a > 7) & (a <= 12)
        masks["age_13_19"] = ok & (a > 12) & (a <= 19)
        masks["age_ge_20"] = ok & (a >= 20)
        masks["age_10_14"] = ok & (a >= 10) & (a <= 14)
        masks["age_14_plus"] = ok & (a >= 14)
    if "n_prior_tests_observed" in c:
        masks["no_or_low_history"] = df["n_prior_tests_observed"] <= 1
    if "first_observed_test_is_not_true_first" in c:
        masks["first_observed_mot"] = df["first_observed_test_is_not_true_first"] == 0
    adv = [col for col in df.columns
           if col.startswith("has_prior_advisory_") and col.endswith("_observed")]
    if adv:
        masks["prior_advisory_present"] = df[adv].sum(axis=1) > 0
    mcol = next((m for m in ("annualized_mileage_v2", "annualized_mileage") if m in c), None)
    if mcol:
        q = df[mcol].quantile([0.25, 0.75])
        masks["low_annual_miles"] = df[mcol] <= q.iloc[0]
        masks["high_annual_miles"] = df[mcol] >= q.iloc[1]
    return masks


def segmented_report(df, y, p_cand, p_base=None, min_n: int = 200) -> dict:
    y = np.asarray(y)
    p_cand = np.asarray(p_cand)
    out = {"pooled": {"n": int(len(y)),
                      "auc_cand": float(roc_auc_score(y, p_cand)),
                      "ece_cand": round(ece(y, p_cand), 4)}}
    if p_base is not None:
        p_base = np.asarray(p_base)
        out["pooled"]["auc_base"] = float(roc_auc_score(y, p_base))
        out["pooled"]["d_auc_pp"] = round(
            (out["pooled"]["auc_cand"] - out["pooled"]["auc_base"]) * 100, 3)
    masks = _slice_masks(df)
    out["slices"] = {}
    for s in (SLICES or list(masks)):
        if s not in masks:
            out["slices"][s] = {"status": "unavailable_on_frame"}
            continue
        m = np.asarray(masks[s])
        ys = y[m]
        if m.sum() < min_n or len(set(ys.tolist())) < 2:
            out["slices"][s] = {"status": "too_small", "n": int(m.sum())}
            continue
        rec = {"n": int(m.sum()), "auc_cand": round(float(roc_auc_score(ys, p_cand[m])), 4),
               "ece_cand": round(ece(ys, p_cand[m]), 4)}
        if p_base is not None:
            rec["auc_base"] = round(float(roc_auc_score(ys, p_base[m])), 4)
            rec["d_auc_pp"] = round((rec["auc_cand"] - rec["auc_base"]) * 100, 3)
            rec["d_ece"] = round(rec["ece_cand"] - ece(ys, p_base[m]), 4)
        out["slices"][s] = rec
    out["slices_unavailable"] = [s for s in SLICES if s not in masks]
    return out


def verdict(report: dict, promotion: dict) -> tuple[bool, dict]:
    """Apply referee_config.PROMOTION to a segmented_report. (Seed-stability is
    enforced by the caller, which passes a seed-mean report only if stable.)"""
    scored = {k: v for k, v in report.get("slices", {}).items() if "d_auc_pp" in v}
    wins = sorted(k for k, v in scored.items() if v["d_auc_pp"] > 0)
    ece_breaches = sorted(k for k, v in scored.items()
                          if v.get("d_ece", 0.0) > promotion["ece_worsen_max_per_slice"])
    within = len(wins) >= promotion["within_segment_min_slices"]
    pooled = report.get("pooled", {})
    pooled_ok = pooled.get("d_auc_pp", 0.0) >= promotion["pooled_d_auc_pp_min"]
    keep = (within or pooled_ok) and not ece_breaches
    why = {"decision_basis": "within_segment" if within else ("pooled" if pooled_ok else "none"),
           "within_segment_wins": wins, "n_scored_slices": len(scored),
           "ece_breaches": ece_breaches, "pooled_d_auc_pp": pooled.get("d_auc_pp")}
    return bool(keep), why
