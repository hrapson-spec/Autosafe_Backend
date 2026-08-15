#!/usr/bin/env python3
"""Severity Stage-2 CONFIRMATION — k-seed paired verdict.

Every seed's B3 arm is compared against ITS OWN matched control at the same seed, so the
refit nuisance measured at k=1 (B3 label: -6.87e-04, CI excluding zero) is differenced out
rather than assumed away.

Reports per-seed deltas, the seed-mean paired delta with a vehicle-clustered bootstrap CI,
the ON-SURFACE sigma-hat for THIS contrast, and sign consistency. The B7 precedent failed
on sign consistency, not on the mean, so that is reported as a first-class gate.

The bootstrap CI is NOT a gate condition: it conditions on the fitted seeds and cannot see
refit variability (STAGE2_INFERENCE_RULE_FINAL_2026_08_15.md:227).
"""
import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
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


AB = _load("ablation_tables", ROOT / "scripts/analysis/ablation_tables.py")
MDE = _load("mde", ROOT / "scripts/analysis/mde.py")

TRIGGER = 0.005
Z_SUM = 2.8016


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--treat", default="sev.B3")
    ap.add_argument("--control", default="sev.CTRL")
    ap.add_argument("--seeds", default="101,202,303,404,505")
    ap.add_argument("--labels", default="out/TARGET_SEVERITY_LABELS.parquet")
    ap.add_argument("--label-expr", default="b3", choices=("b3", "m1"))
    ap.add_argument("--out", default="out/SEVERITY_STAGE2_CONFIRM.json")
    a = ap.parse_args()

    seeds = [int(s) for s in a.seeds.split(",")]
    lab = pq.read_table(ROOT / a.labels)
    L = {n: np.asarray(lab.column(n)) for n in lab.schema.names}
    y = ((L["n_major_or_dangerous"] >= 3) if a.label_expr == "b3"
         else (L["n_sections_with_md"] >= 2)).astype(np.int8)
    ids = [int(t) for t in L["test_id"]]

    def preds(cell, seed):
        p = ROOT / f"out/fits/sev/preds/{cell}.seed{seed}.parquet"
        if not p.exists():
            return None, None
        t = pq.read_table(p).to_pydict()
        ix = {int(v): i for i, v in enumerate(t["test_id"])}
        sel = np.array([ix[i] for i in ids])
        return (M.as_stored(np.asarray(t["p"], dtype=np.float64)[sel]),
                np.asarray(t["vehicle_id"])[sel])

    P_t, P_c, per_seed, veh = [], [], [], None
    missing = []
    for s in seeds:
        pt, vt = preds(a.treat, s)
        pc, vc = preds(a.control, s)
        if pt is None or pc is None:
            missing.append(s)
            continue
        if veh is None:
            veh = vt
        assert np.array_equal(vt, vc) and np.array_equal(vt, veh), "vehicle_id mismatch"
        at, ac = M.auroc(y, pt), M.auroc(y, pc)
        per_seed.append({"seed": s, "treat_auroc": at, "control_auroc": ac,
                         "delta": at - ac})
        P_t.append(pt)
        P_c.append(pc)

    if not P_t:
        print("no seed pairs available")
        return 2

    deltas = np.array([r["delta"] for r in per_seed])
    k = len(deltas)
    sigma_hat = float(deltas.std(ddof=1)) if k > 1 else float("nan")
    boot = AB.clustered_bootstrap_delta(y, veh, P_t, P_c)
    se_delta = MDE.se_delta_from_ci(boot["lo"], boot["hi"]) if k else float("nan")
    mde_k = (Z_SUM * math.sqrt(sigma_hat ** 2 / k + se_delta ** 2)
             if k > 1 and not math.isnan(sigma_hat) else float("nan"))

    all_pos = bool((deltas > 0).all())
    mean_d = float(deltas.mean())
    verdict = ("CONFIRMED" if (mean_d >= TRIGGER and all_pos) else
               "SIGN-INCONSISTENT-STOP" if mean_d >= TRIGGER else
               "NOT-CONFIRMED")

    payload = {"prereg": "prereg/PREREG_SEVERITY_STAGE2_2026_08_15.md",
               "label": a.label_expr, "k": k, "seeds_used": [r["seed"] for r in per_seed],
               "seeds_missing": missing, "per_seed": per_seed,
               "mean_delta": mean_d, "sigma_hat_paired": sigma_hat,
               "se_delta": se_delta, "mde_at_k": mde_k,
               "boot_point": boot["point"], "boot_ci": [boot["lo"], boot["hi"]],
               "reps_used": boot.get("reps_used"),
               "all_seeds_positive": all_pos, "trigger": TRIGGER,
               "ci_is_not_a_gate": True, "verdict": verdict}
    (ROOT / a.out).write_text(json.dumps(payload, indent=1, default=str))

    print(f"{'seed':>6}{'B3-trained':>13}{'control':>12}{'delta':>12}")
    for r in per_seed:
        print(f"{r['seed']:>6}{r['treat_auroc']:>13.6f}{r['control_auroc']:>12.6f}"
              f"{r['delta']:>+12.6f}")
    print(f"\nk = {k}   mean paired delta = {mean_d:+.6f}")
    print(f"on-surface sigma_hat (paired, this contrast) = {sigma_hat:.3e}")
    print(f"SE_delta from bootstrap = {se_delta:.3e}   MDE(k={k}) = {mde_k:.3e}")
    print(f"seed-mean bootstrap CI [{boot['lo']:+.6f}, {boot['hi']:+.6f}] (NOT a gate)")
    print(f"all seeds positive: {all_pos}")
    print(f"VERDICT: {verdict}")
    if missing:
        print(f"WARNING: seeds missing a pair, excluded: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
