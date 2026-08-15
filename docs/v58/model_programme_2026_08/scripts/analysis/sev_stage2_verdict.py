#!/usr/bin/env python3
"""Severity Stage 2 -- ANALYSIS + mechanical verdict. Per PREREG_SEVERITY_STAGE2_2026_08_15.md.

Written before any Stage-2 fit existed. Applies the pre-registered escalation rule without
discretion. Stage 1's severity_analyze.py is imported read-only and NEVER modified.

The CI is reported but is deliberately NOT a gate condition: it conditions on the fitted
seed and cannot see refit variability (STAGE2_INFERENCE_RULE_FINAL_2026_08_15.md:227).
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

from factory.runners import metrics as M  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SA = _load("severity_analyze", ROOT / "scripts/analysis/severity_analyze.py")
AB = _load("ablation_tables", ROOT / "scripts/analysis/ablation_tables.py")

BENCH = {"y_b3": ("B3_ge3_MD", 0.7913097252702295),
         "y_m1": ("M1_MULTI_COMPONENT", 0.783293)}
# exact banked frozen seed101 values (out/SEVERITY_RESULT.json)
FROZEN = {"y_b3": 0.7913097252702295, "y_m1": 0.7832926394979942}
TRIGGER = 0.005
MDE_K1 = 0.00242
HALT = 0.02


def logistic_recal(y, p, iters=25):
    """Y ~ a + b*logit(p) by IRLS. The programme has no ECE implementation; calibration
    is reported as intercept/slope, matching ny_cohorts_analyze."""
    eps = 1e-12
    x = np.log(np.clip(p, eps, 1 - eps) / (1 - np.clip(p, eps, 1 - eps)))
    X = np.column_stack([np.ones_like(x), x])
    b = np.zeros(2)
    for _ in range(iters):
        eta = X @ b
        mu = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(mu * (1 - mu), 1e-9, None)
        z = eta + (y - mu) / w
        WX = X * w[:, None]
        b_new = np.linalg.solve(X.T @ WX, WX.T @ z)
        if np.max(np.abs(b_new - b)) < 1e-10:
            b = b_new
            break
        b = b_new
    return float(b[0]), float(b[1])


def topk(y, p, k):
    m = max(1, int(round(k * y.size)))
    o = np.argsort(-p, kind="mergesort")[:m]
    return float(y[o].sum() / y.sum()) if y.sum() else float("nan")


def describe(y, p):
    y = y.astype(np.int8)
    prev = float(y.mean())
    auroc = M.auroc(y, p)
    auprc = M.auprc(y, p)
    a, b = logistic_recal(y.astype(float), p)
    caps = {f"top{int(k*100)}": topk(y, p, k) for k in (0.05, 0.10, 0.20)}
    lifts = {f"lift{int(k*100)}": (topk(y, p, k) / k) for k in (0.05, 0.10, 0.20)}
    return {"positive_n": int(y.sum()), "prevalence": prev, "auroc": auroc,
            "auprc": auprc, "pr_lift": auprc / prev if prev else float("nan"),
            "brier": float(np.mean((p - y) ** 2)),
            "calib_intercept": a, "calib_slope": b, **caps, **lifts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=sorted(BENCH))
    ap.add_argument("--new-preds", required=True)
    ap.add_argument("--frozen-preds",
                    default="out/fits/s2/preds/s2.D.cum.b0-6.seed101.parquet")
    ap.add_argument("--labels", default="out/TARGET_SEVERITY_LABELS.parquet")
    ap.add_argument("--reps", type=int, default=AB.BOOTSTRAP_REPS)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    lab = pq.read_table(ROOT / a.labels)
    L = {n: np.asarray(lab.column(n)) for n in lab.schema.names}
    outcomes, controls = SA.build_outcomes(L)
    key = BENCH[a.label][0]
    y_all = {**outcomes, **controls}

    new = pq.read_table(ROOT / a.new_preds).to_pydict()
    frz = pq.read_table(ROOT / a.frozen_preds).to_pydict()

    # --- identity fences (bootstrap_from_paired_parquet does NOT check these) ---
    fences = {}
    ln = {int(t): i for i, t in enumerate(new["test_id"])}
    lf = {int(t): i for i, t in enumerate(frz["test_id"])}
    ids = [int(t) for t in L["test_id"]]
    fences["rows_match"] = (len(ln) == len(lf) == len(ids))
    fences["id_sets_identical"] = (set(ln) == set(lf) == set(ids))
    if not all(fences.values()):
        print("FENCE FAIL:", json.dumps(fences))
        return 2
    inew = np.array([ln[i] for i in ids])
    ifrz = np.array([lf[i] for i in ids])
    p_new = M.as_stored(np.asarray(new["p"], dtype=np.float64)[inew])
    p_frz = M.as_stored(np.asarray(frz["p"], dtype=np.float64)[ifrz])
    veh = np.asarray(new["vehicle_id"])[inew]
    fences["vehicle_id_agrees"] = bool(
        np.array_equal(veh, np.asarray(frz["vehicle_id"])[ifrz]))
    y = y_all[key].astype(np.int8)

    # --- frozen benchmark reproduction --------------------------------------
    repro = M.auroc(y, p_frz)
    fences["frozen_benchmark_repro"] = {"computed": repro, "banked": FROZEN[a.label],
                                        "abs_diff": abs(repro - FROZEN[a.label])}

    rows = {"new": describe(y, p_new), "frozen": describe(y, p_frz)}
    delta = rows["new"]["auroc"] - rows["frozen"]["auroc"]

    # --- paired vehicle-clustered CI on the AUROC delta ---------------------
    tmp = ROOT / "out/_sev_paired_tmp.parquet"
    pq.write_table(pa.table({"test_id": pa.array(ids, pa.int64()),
                             "vehicle_id": pa.array(veh, pa.int64()),
                             "y": pa.array(y, pa.int8()),
                             "p_a": pa.array(p_new, pa.float64()),
                             "p_b": pa.array(p_frz, pa.float64())}), tmp)
    boot = AB.bootstrap_from_paired_parquet(str(tmp), reps=a.reps)
    tmp.unlink(missing_ok=True)

    # --- transfer table: the new score against every Stage-1 outcome --------
    order, gidx, ng = SA._prep(p_new)
    b1 = outcomes["B1_ge1_MD"]
    transfer = {}
    for k in ("B3_ge3_MD", "M1_MULTI_COMPONENT", "T0_AUTOSAFE", "S1_ANY_DANGEROUS"):
        yk = y_all[k].astype(np.int8)
        transfer[k] = {"new": M.auroc(yk, p_new), "frozen": M.auroc(yk, p_frz)}
        transfer[k]["delta"] = transfer[k]["new"] - transfer[k]["frozen"]

    verdict = ("HALT-LEAKAGE-AUDIT" if delta >= HALT else
               "ESCALATE" if delta >= TRIGGER else
               "DETECTABLE-SUB-TRIGGER" if delta >= MDE_K1 else "NULL")

    payload = {"prereg": "prereg/PREREG_SEVERITY_STAGE2_2026_08_15.md",
               "label": a.label, "outcome_key": key, "fences": fences,
               "rows": rows, "delta": delta, "delta_ci": [boot["lo"], boot["hi"]],
               "delta_point_boot": boot["point"], "reps": boot["reps_used"],
               "rule": {"trigger": TRIGGER, "mde_k1": MDE_K1, "halt": HALT,
                        "ci_is_not_a_gate": True},
               "verdict": verdict, "transfer": transfer}
    (ROOT / a.out).write_text(json.dumps(payload, indent=1, default=str))
    print(json.dumps({k: payload[k] for k in
                      ("label", "delta", "delta_ci", "verdict", "fences")},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
