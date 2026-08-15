"""ADVSTRUCT descriptive analysis -- Estimand A and Estimand B.

PREREG_ADVSTRUCT_2026_08_15.md sha 35ee4828c47f4b88, sections 7.2-7.5.
AMENDMENT_ADVSTRUCT_A2_2026_08_15.md sha 3579ed437b6674dc (binning).

Separated from advstruct_build.py so the statistic cannot be chosen after seeing it.
This module reads the prior-side table and the banked labels and computes nothing else.

ESTIMAND A -- does risk rise with breadth, within strata of equal advisory count?
ESTIMAND B -- the system-composition falsifier (prereg 7.3). A 4-system history may
  simply contain intrinsically higher-risk systems than a 1-system history. An additive
  per-system expectation is fitted on TRAIN and FROZEN; breadth is then tested as an
  increment over that expectation used as an offset. Estimand B, not A, is load-bearing:
  a positive A with a null B means composition, not structure.

Uncertainty is vehicle-clustered throughout -- a vehicle contributes several targets and
they are not independent.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent.parent.parent))

from scripts.analysis.advstruct_sections import SYSTEMS  # noqa: E402

PREREG_SHA16 = "35ee4828c47f4b88"
AMEND_A2_SHA16 = "3579ed437b6674dc"

#: Frozen by AMENDMENT A2.2. c>=9 excluded (below the 500-row floor); 7 and 8 pooled.
STRATA = [("c=2", 2, 2), ("c=3", 3, 3), ("c=4", 4, 4),
          ("c=5", 5, 5), ("c=6", 6, 6), ("c=7-8", 7, 8)]
MIN_CELL_N = 500        # A2.4: cells below this are shown and flagged, never pooled
MIN_STRATUM_POS = 50    # parent 7.5
N_BOOT = 2000
BOOT_SEED = 20260815
PRIMARY = "y_b3"        # parent 3.2: sole confirmatory target for the descriptive rule

FRAMES = {
    "train_flat4y": ("out/advstruct/prior_train.parquet", "out/TRAIN_SEVERITY_LABELS.parquet"),
    "eval2024": ("out/advstruct/prior_eval.parquet", "out/TARGET_SEVERITY_LABELS.parquet"),
}


# ------------------------------------------------------------------ logistic

def _fit_logit(X: np.ndarray, y: np.ndarray, offset: np.ndarray | None = None,
               max_iter: int = 60, tol: float = 1e-9) -> np.ndarray:
    """IRLS logistic with an optional fixed offset. Returns [intercept, *betas].

    Written out rather than delegated because sklearn has no offset parameter, and
    Estimand B is defined by its offset -- the whole point is that the composition
    expectation is FROZEN from TRAIN and not re-estimated.
    """
    n, p = X.shape
    Xd = np.column_stack([np.ones(n), X])
    off = np.zeros(n) if offset is None else offset
    beta = np.zeros(p + 1)
    for _ in range(max_iter):
        eta = Xd @ beta + off
        mu = 1.0 / (1.0 + np.exp(-np.clip(eta, -35, 35)))
        w = np.clip(mu * (1 - mu), 1e-10, None)
        z = eta - off + (y - mu) / w
        XtW = Xd.T * w
        try:
            new = np.linalg.solve(XtW @ Xd + 1e-10 * np.eye(p + 1), XtW @ z)
        except np.linalg.LinAlgError:
            return np.full(p + 1, np.nan)
        if np.max(np.abs(new - beta)) < tol:
            return new
        beta = new
    return beta


def _fit_logit_grouped(levels: np.ndarray, n_vec: np.ndarray, k_vec: np.ndarray,
                       max_iter: int = 60, tol: float = 1e-10) -> float:
    """Binomial IRLS on a collapsed (level, trials, successes) table. Returns beta.

    EXACTLY equivalent to a row-level logistic of y on a single predictor: the
    Bernoulli likelihood over N rows factorises into a binomial likelihood over the
    distinct predictor values. Within a count stratum `b` takes 2-6 values, so this
    turns a 104k-row solve into a 6-row solve with identical output. Used only where
    that equivalence holds -- never where an offset varies within a level.
    """
    keep = n_vec > 0
    x, n, k = levels[keep].astype(np.float64), n_vec[keep].astype(np.float64), k_vec[keep]
    if x.size < 2:
        return float("nan")
    Xd = np.column_stack([np.ones(x.size), x])
    beta = np.zeros(2)
    for _ in range(max_iter):
        eta = Xd @ beta
        mu = 1.0 / (1.0 + np.exp(-np.clip(eta, -35, 35)))
        w = np.clip(n * mu * (1 - mu), 1e-12, None)
        z = eta + (k - n * mu) / w
        XtW = Xd.T * w
        try:
            new = np.linalg.solve(XtW @ Xd + 1e-12 * np.eye(2), XtW @ z)
        except np.linalg.LinAlgError:
            return float("nan")
        if np.max(np.abs(new - beta)) < tol:
            return float(new[1])
        beta = new
    return float(beta[1])


def _clusters(vehicle_id: np.ndarray):
    """Sorted-vehicle index structure for clustered resampling (NY cohort method)."""
    order = np.argsort(vehicle_id, kind="mergesort")
    vs = vehicle_id[order]
    starts = np.flatnonzero(np.concatenate(([True], vs[1:] != vs[:-1])))
    counts = np.diff(np.concatenate((starts, [len(vs)])))
    return order, starts, counts


def _boot_idx(order, starts, counts, rng):
    s = rng.integers(0, starts.size, starts.size)
    c = counts[s]
    tot = int(c.sum())
    off = np.repeat(starts[s], c) + (np.arange(tot) - np.repeat(np.cumsum(c) - c, c))
    return order[off]


def _clustered_ci(fn, vehicle_id, rng, reps=N_BOOT):
    order, starts, counts = _clusters(vehicle_id)
    vals = np.empty(reps)
    for b in range(reps):
        vals[b] = fn(_boot_idx(order, starts, counts, rng))
    vals = vals[np.isfinite(vals)]
    if vals.size < reps * 0.5:
        return (float("nan"), float("nan"), int(vals.size))
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)), int(vals.size))


# ------------------------------------------------------------------ estimands

def estimand_a(df, rng) -> dict:
    """Within-count beta on continuous breadth, plus the flagged cell rate table."""
    y = df[PRIMARY].to_numpy(np.float64)
    veh = df["vehicle_id"].to_numpy()
    c = df["adv_n_last"].to_numpy()
    b = df["adv_breadth_last"].to_numpy(np.float64)

    out = {"strata": {}, "cells": {}}
    for name, lo, hi in STRATA:
        m = (c >= lo) & (c <= hi)
        ys, bs, vs = y[m], b[m], veh[m]
        n, pos = int(m.sum()), int(ys.sum())
        rec = {"n": n, "n_positive": pos, "n_vehicles": int(np.unique(vs).size),
               "prevalence": round(float(ys.mean()), 6) if n else None,
               "distinct_breadth_levels": int(np.unique(bs).size)}
        if pos < MIN_STRATUM_POS or rec["distinct_breadth_levels"] < 2:
            rec["verdict_eligible"] = False
            rec["reason"] = ("below_50_positives" if pos < MIN_STRATUM_POS
                             else "no_breadth_contrast")
            out["strata"][name] = rec
            continue
        levels = np.unique(bs)
        li_all = np.searchsorted(levels, bs)
        L = levels.size

        def _grouped(idx):
            li = li_all[idx]
            return _fit_logit_grouped(
                levels,
                np.bincount(li, minlength=L),
                np.bincount(li, weights=ys[idx], minlength=L))

        beta = _grouped(np.arange(ys.size))
        lo_ci, hi_ci, ok = _clustered_ci(_grouped, vs, rng)
        rec.update(beta_breadth=round(float(beta), 6),
                   ci95=[round(lo_ci, 6), round(hi_ci, 6)],
                   boot_ok=ok, ci_excludes_zero=bool(lo_ci > 0 or hi_ci < 0),
                   verdict_eligible=True)
        out["strata"][name] = rec

        for bv in sorted(np.unique(bs)):
            cm = bs == bv
            cn, cp = int(cm.sum()), int(ys[cm].sum())
            out["cells"][f"{name}_b{int(bv)}"] = {
                "n": cn, "n_positive": cp,
                "rate": round(float(ys[cm].mean()), 6),
                "thin": cn < MIN_CELL_N}
    return out


def _eligible(df) -> np.ndarray:
    """Rows inside the frozen count strata (A2.2). c=0 and c=1 admit no breadth
    contrast -- breadth is a deterministic function of count there -- so including
    them would dilute every coefficient below and make it non-comparable to the
    within-count estimates."""
    c = df["adv_n_last"].to_numpy()
    m = np.zeros(len(df), bool)
    for _, lo, hi in STRATA:
        m |= (c >= lo) & (c <= hi)
    return m


def estimand_b(train_df, test_df, rng) -> dict:
    """System-composition falsifier. Additive per-system expectation FROZEN on TRAIN.

    Conditioning on the full 9-vector of per-system counts is STRICTLY STRONGER than
    conditioning on the total, because the total is their sum. So the coefficient
    reported here is 'breadth given exactly which systems were advised and how often'.

    Both estimated on the ELIGIBLE population only, and reported next to a matched
    count-only comparator on the same rows, so the share of the raw gradient that
    composition absorbs is readable rather than inferred across two populations.
    """
    cols = [f"n_sys_{s}" for s in SYSTEMS]
    mtr, mte = _eligible(train_df), _eligible(test_df)
    train_df, test_df = train_df[mtr], test_df[mte]

    Xtr = train_df[cols].to_numpy(np.float64)
    ytr = train_df[PRIMARY].to_numpy(np.float64)
    coef = _fit_logit(Xtr, ytr)

    Xte = test_df[cols].to_numpy(np.float64)
    yte = test_df[PRIMARY].to_numpy(np.float64)
    veh = test_df["vehicle_id"].to_numpy()
    offset = coef[0] + Xte @ coef[1:]

    bte = test_df["adv_breadth_last"].to_numpy(np.float64)
    hhi = test_df["adv_items_per_system"].to_numpy(np.float64)
    hhi = np.nan_to_num(hhi, nan=0.0)

    # MATCHED COMPARATOR: breadth given TOTAL COUNT ONLY, same rows, same estimator.
    # The only difference from the line below is what is conditioned on, so the
    # ratio of the two betas is the share of the gradient composition absorbs.
    cte = test_df["adv_n_last"].to_numpy(np.float64)
    bte0 = test_df["adv_breadth_last"].to_numpy(np.float64)
    beta_count_only = _fit_logit(np.column_stack([bte0, cte]), yte)[1]

    res = {
        "population": "eligible count strata only (A2.2); c=0 and c=1 excluded",
        "additive_fit_on": "train_flat4y",
        "coefficients": {"intercept": round(float(coef[0]), 6),
                         **{s: round(float(v), 6) for s, v in zip(SYSTEMS, coef[1:])}},
        "tested_on": "held-out frame, coefficients frozen",
        "n": int(yte.size),
        "matched_comparator_breadth_given_total_count_only":
            round(float(beta_count_only), 6),
    }
    for label, x in (("breadth", bte), ("items_per_system", hhi)):
        beta = _fit_logit(x.reshape(-1, 1), yte, offset=offset)[1]
        lo, hi, ok = _clustered_ci(
            lambda idx: _fit_logit(x[idx].reshape(-1, 1), yte[idx], offset=offset[idx])[1],
            veh, rng)
        res[label] = {"beta_beyond_composition": round(float(beta), 6),
                      "ci95": [round(lo, 6), round(hi, 6)], "boot_ok": ok,
                      "ci_excludes_zero": bool(lo > 0 or hi < 0)}

    combos = {}
    c = test_df["adv_n_last"].to_numpy()
    sysmat = Xte > 0
    for name, lo_c, hi_c in STRATA[:4]:
        m = (c >= lo_c) & (c <= hi_c)
        if m.sum() < MIN_CELL_N:
            continue
        keys = ["+".join(s for s, on in zip(SYSTEMS, row) if on) or "(none)"
                for row in sysmat[m]]
        ys = yte[m]
        agg = {}
        for k, yy in zip(keys, ys):
            a = agg.setdefault(k, [0, 0])
            a[0] += 1
            a[1] += int(yy)
        top = sorted(agg.items(), key=lambda kv: -kv[1][0])[:8]
        combos[name] = {k: {"n": v[0], "rate": round(v[1] / v[0], 6)} for k, v in top}
    res["common_combinations"] = combos
    return res


def control_survival(df, rng) -> dict:
    """beta_survival = beta_stratified / beta_pooled, per parent 7.5."""
    y = df[PRIMARY].to_numpy(np.float64)
    c = df["adv_n_last"].to_numpy()
    b = df["adv_breadth_last"].to_numpy(np.float64)
    elig = np.zeros(len(df), bool)
    for _, lo, hi in STRATA:
        elig |= (c >= lo) & (c <= hi)

    def pooled_beta(mask):
        acc, wsum = 0.0, 0.0
        for _, lo, hi in STRATA:
            m = mask & (c >= lo) & (c <= hi)
            if m.sum() < MIN_CELL_N or np.unique(b[m]).size < 2 or y[m].sum() < MIN_STRATUM_POS:
                continue
            acc += _fit_logit(b[m].reshape(-1, 1), y[m])[1] * m.sum()
            wsum += m.sum()
        return acc / wsum if wsum else float("nan")

    base = pooled_beta(elig)
    out = {"beta_pooled": round(float(base), 6), "controls": {}}

    age = df["age_years"].to_numpy(np.float64)
    depth = df["n_priors"].to_numpy(np.float64)
    year = np.array([d.year for d in df["tgt_date"]])
    pc = df["tgt_pc"].fillna("__na__").to_numpy()
    make = df["tgt_make"].fillna("__na__").to_numpy()

    def strat_beta(groups):
        acc, wsum = 0.0, 0.0
        for g in np.unique(groups[elig]):
            m = elig & (groups == g)
            bb = pooled_beta(m)
            if np.isfinite(bb):
                acc += bb * m.sum()
                wsum += m.sum()
        return acc / wsum if wsum else float("nan")

    aq = np.digitize(age, np.nanpercentile(age[elig], [25, 50, 75]))
    dq = np.digitize(depth, [3, 6])
    for label, groups in (("age_quartile", aq), ("prior_depth_band", dq),
                          ("target_year", year), ("postcode_area", pc),
                          ("make", make)):
        bs = strat_beta(groups)
        out["controls"][label] = {
            "beta_stratified": round(float(bs), 6),
            "beta_survival": round(float(bs / base), 6) if np.isfinite(bs) else None,
            "n_groups": int(np.unique(groups[elig]).size)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True, choices=list(FRAMES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=N_BOOT)
    a = ap.parse_args()

    import pandas as pd
    os.chdir(ROOT)
    globals()["N_BOOT"] = a.reps
    rng = np.random.default_rng(BOOT_SEED)

    prior_path, label_path = FRAMES[a.frame]
    prior = pd.read_parquet(prior_path)
    lab = pd.read_parquet(label_path, columns=[
        "test_id", "n_major_or_dangerous", "n_sections_with_md", "n_dangerous", "y_final"])
    lab["y_b3"] = (lab.n_major_or_dangerous >= 3).astype(np.int8)
    df = prior.merge(lab[["test_id", "y_b3"]], left_on="tgt_id", right_on="test_id",
                     how="inner", validate="one_to_one")
    df = df[df.adv_state.isin(["observable", "observable_zero"])].reset_index(drop=True)

    out = {"frame": a.frame, "prereg_sha256_16": PREREG_SHA16,
           "amendment_a2_sha256_16": AMEND_A2_SHA16, "primary_target": PRIMARY,
           "n_analysed": int(len(df)), "boot_reps": N_BOOT,
           "estimand_a": estimand_a(df, rng)}

    tr = pd.read_parquet(FRAMES["train_flat4y"][0])
    tl = pd.read_parquet(FRAMES["train_flat4y"][1], columns=["test_id", "n_major_or_dangerous"])
    tl["y_b3"] = (tl.n_major_or_dangerous >= 3).astype(np.int8)
    tr = tr.merge(tl[["test_id", "y_b3"]], left_on="tgt_id", right_on="test_id", how="inner")
    tr = tr[tr.adv_state.isin(["observable", "observable_zero"])].reset_index(drop=True)
    out["estimand_b"] = estimand_b(tr, df, rng)
    out["control_survival"] = control_survival(df, rng)

    tied = df[df.tied_prior_day == False]  # noqa: E712
    out["tied_day_sensitivity"] = {
        "n_untied": int(len(tied)),
        "estimand_a": estimand_a(tied.reset_index(drop=True), rng)["strata"]}

    Path(a.out).write_text(json.dumps(out, indent=1))
    print(f"wrote {a.out}  n={len(df):,}")
    for k, v in out["estimand_a"]["strata"].items():
        if v.get("verdict_eligible"):
            print(f"  {k:<7} n={v['n']:>7,} beta={v['beta_breadth']:+.4f} "
                  f"CI[{v['ci95'][0]:+.4f},{v['ci95'][1]:+.4f}] "
                  f"{'*' if v['ci_excludes_zero'] else ' '}")
        else:
            print(f"  {k:<7} n={v['n']:>7,} INELIGIBLE ({v.get('reason')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
