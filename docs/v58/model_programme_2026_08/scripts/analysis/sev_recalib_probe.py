#!/usr/bin/env python3
"""How much of the B3 probability improvement needs RETRAINING, and how much is just
recalibration of the frozen score?

Recalibration is a MONOTONE map, so it cannot change AUROC (Platt is strictly monotone;
isotonic is weakly monotone and can only create ties). Any AUROC gain therefore requires
retraining by construction. What is genuinely open is how much of the Brier/calibration
gain is available without it. That is what this measures.

Fitting a recalibrator on the same rows it is scored on is optimistic, so every map here
is OUT-OF-FOLD, with folds clustered by vehicle_id (a vehicle's rows never straddle a
fold, matching split_validation's rule).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

from factory.runners import metrics as M  # noqa: E402

N_FOLDS = 5
FOLD_SEED = 20260812
EPS = 1e-12


def _logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def platt_fit(y, p, iters=50):
    x = _logit(p)
    X = np.column_stack([np.ones_like(x), x])
    b = np.zeros(2)
    for _ in range(iters):
        mu = 1.0 / (1.0 + np.exp(-(X @ b)))
        w = np.clip(mu * (1 - mu), 1e-9, None)
        z = (X @ b) + (y - mu) / w
        WX = X * w[:, None]
        # ridge: isotonic output has flat regions, which can make the design matrix
        # singular on a fold and silently propagate NaN into the slope diagnostic.
        A = X.T @ WX + 1e-9 * np.eye(2)
        nb = np.linalg.solve(A, WX.T @ z)
        if np.max(np.abs(nb - b)) < 1e-11:
            return nb
        b = nb
    return b


def platt_apply(b, p):
    return 1.0 / (1.0 + np.exp(-(b[0] + b[1] * _logit(p))))


def isotonic_fit(y, p):
    """PAVA on (p, y) sorted by p. Returns knots for step-wise interpolation."""
    o = np.argsort(p, kind="mergesort")
    ps, ys = p[o], y[o].astype(np.float64)
    v = ys.copy()
    w = np.ones_like(v)
    idx = np.arange(v.size)
    i = 0
    while i < len(v) - 1:
        if v[i] <= v[i + 1] + 1e-15:
            i += 1
            continue
        nw = w[i] + w[i + 1]
        nv = (v[i] * w[i] + v[i + 1] * w[i + 1]) / nw
        v = np.delete(v, i + 1); w = np.delete(w, i + 1); idx = np.delete(idx, i + 1)
        v[i] = nv; w[i] = nw
        while i > 0 and v[i - 1] > v[i] + 1e-15:
            nw = w[i - 1] + w[i]
            nv = (v[i - 1] * w[i - 1] + v[i] * w[i]) / nw
            v = np.delete(v, i); w = np.delete(w, i); idx = np.delete(idx, i)
            i -= 1
            v[i] = nv; w[i] = nw
    return ps[idx], v


def isotonic_apply(knots, p):
    xs, vs = knots
    j = np.searchsorted(xs, p, side="right") - 1
    return vs[np.clip(j, 0, len(vs) - 1)]


def calib(y, p):
    """Isotonic saturates at exactly 0/1, where the logit is undefined and IRLS diverges.
    The diagnostic clips to [1e-6, 1-1e-6]; the saturation itself is reported separately
    because a probability of exactly 0 or 1 is a real hazard for anything quoting
    probabilities or scoring log-loss downstream."""
    b = platt_fit(y.astype(np.float64), np.clip(p, 1e-6, 1 - 1e-6))
    return float(b[0]), float(b[1])


def describe(y, p):
    y8 = y.astype(np.int8)
    a, s = calib(y, p)
    return {"auroc": M.auroc(y8, p), "auprc": M.auprc(y8, p),
            "brier": float(np.mean((p - y) ** 2)),
            "logloss": float(-np.mean(y * np.log(np.clip(p, EPS, 1)) +
                                      (1 - y) * np.log(np.clip(1 - p, EPS, 1)))),
            "calib_intercept": a, "calib_slope": s,
            "n_at_zero": int((p <= 0.0).sum()), "n_at_one": int((p >= 1.0).sum()),
            "n_distinct": int(np.unique(p).size)}


def oof(y, p, vehicle_id, kind):
    _, inv = np.unique(vehicle_id, return_inverse=True)
    rng = np.random.default_rng(FOLD_SEED)
    fold_of_vehicle = rng.integers(0, N_FOLDS, inv.max() + 1)
    folds = fold_of_vehicle[inv]
    out = np.empty_like(p)
    for f in range(N_FOLDS):
        te = folds == f
        tr = ~te
        if kind == "platt":
            out[te] = platt_apply(platt_fit(y[tr].astype(np.float64), p[tr]), p[te])
        else:
            out[te] = isotonic_apply(isotonic_fit(y[tr], p[tr]), p[te])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="out/TARGET_SEVERITY_LABELS.parquet")
    ap.add_argument("--frozen", default="out/fits/s2/preds/s2.D.cum.b0-6.seed101.parquet")
    ap.add_argument("--trained", default="out/fits/sev/preds/sev.B3.seed101.parquet")
    ap.add_argument("--out", default="out/SEV_RECALIB_PROBE.json")
    a = ap.parse_args()

    lab = pq.read_table(ROOT / a.labels)
    L = {n: np.asarray(lab.column(n)) for n in lab.schema.names}
    y = (L["n_major_or_dangerous"] >= 3).astype(np.float64)
    ids = [int(t) for t in L["test_id"]]

    def load(path):
        t = pq.read_table(ROOT / path).to_pydict()
        ix = {int(v): i for i, v in enumerate(t["test_id"])}
        sel = np.array([ix[i] for i in ids])
        return (M.as_stored(np.asarray(t["p"], dtype=np.float64)[sel]),
                np.asarray(t["vehicle_id"])[sel])

    p_frz, veh = load(a.frozen)
    p_new, veh2 = load(a.trained)
    assert np.array_equal(veh, veh2)

    res = {"n": int(y.size), "prevalence": float(y.mean()),
           "n_folds": N_FOLDS, "fold_seed": FOLD_SEED,
           "arms": {
               "frozen_raw": describe(y, p_frz),
               "frozen_platt_oof": describe(y, oof(y, p_frz, veh, "platt")),
               "frozen_isotonic_oof": describe(y, oof(y, p_frz, veh, "isotonic")),
               "b3_trained_raw": describe(y, p_new),
               "b3_trained_platt_oof": describe(y, oof(y, p_new, veh, "platt")),
           }}
    A = res["arms"]
    gap = A["frozen_raw"]["brier"] - A["b3_trained_raw"]["brier"]
    res["brier_gap_frozen_to_trained"] = gap
    res["recovered_by_recalibration"] = {
        k: (A["frozen_raw"]["brier"] - A[k]["brier"]) / gap
        for k in ("frozen_platt_oof", "frozen_isotonic_oof")}
    res["auroc_change_from_recalibration"] = {
        k: A[k]["auroc"] - A["frozen_raw"]["auroc"]
        for k in ("frozen_platt_oof", "frozen_isotonic_oof")}
    (ROOT / a.out).write_text(json.dumps(res, indent=1, default=str))

    print(f"{'arm':<24}{'AUROC':>10}{'Brier':>11}{'logloss':>10}{'cal_int':>10}{'cal_slope':>11}{'@0':>6}{'@1':>4}{'distinct':>9}")
    for k, v in A.items():
        print(f"{k:<24}{v['auroc']:>10.6f}{v['brier']:>11.6f}{v['logloss']:>10.6f}"
              f"{v['calib_intercept']:>10.4f}{v['calib_slope']:>11.4f}"
              f"{v['n_at_zero']:>6}{v['n_at_one']:>4}{v['n_distinct']:>9}")
    print(f"\nBrier gap frozen -> B3-trained : {gap:.6f}")
    for k, v in res["recovered_by_recalibration"].items():
        print(f"  recovered by {k:<22}: {v*100:6.2f}%")
    for k, v in res["auroc_change_from_recalibration"].items():
        print(f"  AUROC change {k:<22}: {v:+.3e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
