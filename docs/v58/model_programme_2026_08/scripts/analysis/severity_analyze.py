#!/usr/bin/env python3
"""Severity/burden outcome study -- ANALYSIS. Per PREREG_SEVERITY_2026_08_15.md.

Written and committed BEFORE the labels existed, so the statistic cannot be chosen after
seeing it. Gate 0 refuses to compute a single outcome number until the pooled AUROC
reproduces the banked s2.D.cum.b0-6 value to 1e-6 -- that proves the join, the label and
the metric implementation before anything is believed.

The score vector is held FIXED; only the label changes. There is therefore zero
retrain/seed noise in the score, and the house detectability floor 0.002052 (a seed
variance quantity) does not apply. Only sampling uncertainty over rows applies.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from factory.runners import metrics as M  # noqa: E402

LABELS = "out/TARGET_SEVERITY_LABELS.parquet"
PREDS = "out/fits/s2/preds/s2.D.cum.b0-6.seed{seed}.parquet"
TRAIN_IDS = "out/fits/s2/preds/s2.D.cum.b0-6.seed{seed}.train_ids.parquet"
FIT = "out/fits/s2/s2.D.cum.b0-6.seed{seed}.json"
PRIMARY_SEED = 101
PANEL_SEEDS = (202,)

BOOTSTRAP_REPS = 1000        # PREREG §5; house constant ablation_tables.py:71
BOOTSTRAP_SEED = 20260812    # PREREG §5; house constant ablation_tables.py:72
CI_LO_Q, CI_HI_Q = 0.025, 0.975
AUROC_REPRO_TOL = 1e-6       # G0.1
IDENTITY_TOL = 1e-9          # G0.8

STAGE2_DELTA_BAR = 0.02      # PREREG §7


# --- weighted AUROC from per-tie-group weights -------------------------------
def _auc_from_groups(wg_pos: np.ndarray, wg_neg: np.ndarray) -> float:
    """Weighted Mann-Whitney with the 0.5 tie convention, groups sorted by score."""
    wp, wn = wg_pos.sum(), wg_neg.sum()
    if wp <= 0 or wn <= 0:
        return float("nan")
    cum_below = np.cumsum(wg_neg) - wg_neg
    return float((wg_pos * (cum_below + 0.5 * wg_neg)).sum() / (wp * wn))


def _prep(p: np.ndarray):
    """Sort once; the score is fixed across every outcome and every replicate."""
    order = np.argsort(p, kind="mergesort")
    ps = p[order]
    new_group = np.empty(len(ps), dtype=bool)
    new_group[0] = True
    np.not_equal(ps[1:], ps[:-1], out=new_group[1:])
    gidx = np.cumsum(new_group) - 1
    return order, gidx, int(gidx[-1]) + 1


def _decompose(w, gidx, ng, yk, b1, wg_tot=None, wg_b1=None):
    """Pooled / clean / mild AUROC computed INDEPENDENTLY (so G0.8 is a real check).

    N_clean(O) = ~P_O & ~B1   N_mild(O) = ~P_O & B1   -- an exact partition of ~P_O.

    wg_tot/wg_b1 are outcome-independent; the bootstrap hoists them out of the outcome
    loop so each replicate costs 2 bincounts per outcome instead of 4.
    """
    # yk and b1 are 0/1 float indicators, so the product is their conjunction.
    wg_p = np.bincount(gidx, weights=w * yk, minlength=ng)
    wg_p_in = np.bincount(gidx, weights=w * (yk * b1), minlength=ng)
    if wg_tot is None:
        wg_tot = np.bincount(gidx, weights=w, minlength=ng)
    if wg_b1 is None:
        wg_b1 = np.bincount(gidx, weights=w * b1, minlength=ng)
    wg_clean = (wg_tot - wg_b1) - (wg_p - wg_p_in)
    wg_mild = wg_b1 - wg_p_in
    return (_auc_from_groups(wg_p, wg_clean + wg_mild),
            _auc_from_groups(wg_p, wg_clean),
            _auc_from_groups(wg_p, wg_mild),
            float(wg_clean.sum()), float(wg_mild.sum()))


def build_outcomes(t) -> dict:
    """PREREG §4. Fixed before performance was inspected."""
    md = t["n_major_or_dangerous"]
    dang = t["n_dangerous"]
    major = t["n_major"]
    out = {
        "T0_AUTOSAFE": t["y_final"].astype(bool),
        "T0_DVSA_INITIAL": t["y_initial"].astype(bool),
        "B1_ge1_MD": md >= 1,
        "B2_ge2_MD": md >= 2,
        "B3_ge3_MD": md >= 3,
        "T1_NY_LIKE": (dang >= 1) | (major >= 2),
        "S1_ANY_DANGEROUS": dang >= 1,
        "M1_MULTI_COMPONENT": t["n_sections_with_md"] >= 2,
    }
    controls = {
        "ONLY_MINOR": (t["n_minor"] >= 1) & (md == 0),
        "ADVISORY_ONLY": (t["n_advisory"] >= 1) & (md == 0),
    }
    return out, controls


def describe(y, p, b1, order, gidx, ng, prev_ref):
    n_pos = int(y.sum())
    prevalence = n_pos / len(y)
    yk = y[order].astype(np.float64)
    b1s = b1[order].astype(np.float64)
    w1 = np.ones(len(y), dtype=np.float64)
    pooled, clean, mild, w_clean, w_mild = _decompose(w1, gidx, ng, yk, b1s)
    auprc = M.auprc(y.astype(np.int8), p) if 0 < n_pos < len(y) else float("nan")
    return {
        "positive_n": n_pos,
        "prevalence": prevalence,
        "auroc": pooled,
        "a_clean": clean,
        "a_mild": mild,
        "n_clean_neg": int(round(w_clean)),
        "n_mild_neg": int(round(w_mild)),
        "auprc": auprc,
        "pr_lift": (auprc / prevalence) if prevalence > 0 else float("nan"),
        "share_of_dvsa_initial": (
            float((y & prev_ref["dvsa"]).sum() / prev_ref["dvsa"].sum())
            if prev_ref["dvsa"].sum() else float("nan")),
        "share_of_autosafe_pos": (
            float((y & prev_ref["autosafe"]).sum() / prev_ref["autosafe"].sum())
            if prev_ref["autosafe"].sum() else float("nan")),
    }


def bootstrap(outcomes, p, b1, vehicle_id, order, gidx, ng, reps, seed):
    """One SHARED vehicle-clustered resample per replicate across ALL outcomes, so
    paired deltas are correctly correlated (PREREG §5)."""
    _, inv = np.unique(vehicle_id, return_inverse=True)
    n_veh = inv.max() + 1
    inv_sorted = inv[order]
    keys = list(outcomes)
    ys = {k: outcomes[k][order].astype(np.float64) for k in keys}
    b1s = b1[order].astype(np.float64)
    acc = {k: {"auroc": np.empty(reps), "a_clean": np.empty(reps)} for k in keys}
    base = "T0_AUTOSAFE"
    dacc = {k: np.empty(reps) for k in keys}
    rng = np.random.default_rng(seed)
    pv = np.full(n_veh, 1.0 / n_veh)
    for r in range(reps):
        counts = rng.multinomial(n_veh, pv)
        w = counts[inv_sorted].astype(np.float64)
        wg_tot = np.bincount(gidx, weights=w, minlength=ng)
        wg_b1 = np.bincount(gidx, weights=w * b1s, minlength=ng)
        vals = {}
        for k in keys:
            pooled, clean, _, _, _ = _decompose(w, gidx, ng, ys[k], b1s, wg_tot, wg_b1)
            acc[k]["auroc"][r] = pooled
            acc[k]["a_clean"][r] = clean
            vals[k] = pooled
        for k in keys:
            dacc[k][r] = vals[k] - vals[base]
    out = {}
    for k in keys:
        out[k] = {
            "auroc_ci": [float(np.nanquantile(acc[k]["auroc"], CI_LO_Q)),
                         float(np.nanquantile(acc[k]["auroc"], CI_HI_Q))],
            "a_clean_ci": [float(np.nanquantile(acc[k]["a_clean"], CI_LO_Q)),
                           float(np.nanquantile(acc[k]["a_clean"], CI_HI_Q))],
            "delta_vs_T0_ci": [float(np.nanquantile(dacc[k], CI_LO_Q)),
                               float(np.nanquantile(dacc[k], CI_HI_Q))],
            "delta_vs_T0_mean": float(np.nanmean(dacc[k])),
        }
    return out


def run_seed(seed, lab, sect_cols, reps, gates_only=False):
    preds = pq.read_table(ROOT / PREDS.format(seed=seed)).to_pydict()
    fit = json.loads((ROOT / FIT.format(seed=seed)).read_text())
    pid = np.asarray(preds["test_id"])
    order_map = {int(v): i for i, v in enumerate(pid)}
    idx = np.array([order_map[int(v)] for v in lab["test_id"]])
    p = M.as_stored(np.asarray(preds["p"], dtype=np.float64)[idx])
    y_banked = np.asarray(preds["y"])[idx].astype(bool)
    vehicle_id = np.asarray(preds["vehicle_id"])[idx]

    gates = {}
    order, gidx, ng = _prep(p)
    ones = np.ones(len(p))
    wg_p = np.bincount(gidx, weights=ones * y_banked[order], minlength=ng)
    wg_n = np.bincount(gidx, weights=ones * (~y_banked[order]), minlength=ng)
    repro = _auc_from_groups(wg_p, wg_n)
    gates["G0.1_auroc_repro"] = {"computed": repro, "banked": fit["auroc"],
                                 "abs_diff": abs(repro - fit["auroc"]),
                                 "pass": abs(repro - fit["auroc"]) <= AUROC_REPRO_TOL}
    tids = set(pq.read_table(ROOT / TRAIN_IDS.format(seed=seed))
               .column("test_id").to_pylist())
    overlap = len(tids & set(int(v) for v in lab["test_id"]))
    gates["G0.2_train_eval_overlap"] = {"n": overlap, "pass": overlap == 0}
    gates["G0.label_agreement_y_final"] = {
        "n_disagree": int((y_banked != lab["y_final"].astype(bool)).sum()),
        "pass": bool((y_banked == lab["y_final"].astype(bool)).all())}

    if gates_only or not all(g.get("pass", True) for g in gates.values()):
        return {"seed": seed, "gates": gates}, None

    outcomes, controls = build_outcomes(lab)
    b1 = outcomes["B1_ge1_MD"]
    prev_ref = {"dvsa": outcomes["T0_DVSA_INITIAL"], "autosafe": outcomes["T0_AUTOSAFE"]}

    rows = {}
    for k, y in {**outcomes, **controls}.items():
        rows[k] = describe(y, p, b1, order, gidx, ng, prev_ref)

    # component outcomes: PRIMARY = DVSA section grain, thin sections preserved
    comp, comp_y = {}, {}
    for c in sect_cols:
        y = lab[c] >= 1
        if y.sum() == 0:
            continue
        comp[c] = describe(y, p, b1, order, gidx, ng, prev_ref)
        comp_y[c] = y

    # G0.8 mixture identity, computed on independently derived components
    ident = []
    for k, r in {**rows, **comp}.items():
        nc, nm = r["n_clean_neg"], r["n_mild_neg"]
        if nc + nm == 0 or np.isnan(r["a_clean"]) or np.isnan(r["a_mild"]):
            continue
        recon = (nc * r["a_clean"] + nm * r["a_mild"]) / (nc + nm)
        ident.append(abs(recon - r["auroc"]))
    gates["G0.8_mixture_identity"] = {"max_abs_err": float(max(ident)) if ident else 0.0,
                                      "n_checked": len(ident),
                                      "pass": (max(ident) if ident else 0.0) <= IDENTITY_TOL}

    # G0.10 reconciliation: results-table y_initial vs items-table B1
    yi = outcomes["T0_DVSA_INITIAL"]
    gates["G0.10_reconciliation"] = {
        "yi1_b1_1": int((yi & b1).sum()), "yi1_b1_0": int((yi & ~b1).sum()),
        "yi0_b1_1": int((~yi & b1).sum()), "yi0_b1_0": int((~yi & ~b1).sum())}

    # one shared resample covers primary outcomes, controls AND component labels
    boot = bootstrap({**outcomes, **controls, **comp_y}, p, b1, vehicle_id,
                     order, gidx, ng, reps, BOOTSTRAP_SEED)
    for k, v in boot.items():
        (rows if k in rows else comp)[k].update(v)
    return {"seed": seed, "gates": gates}, {"outcomes": rows, "components": comp}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=LABELS)
    ap.add_argument("--reps", type=int, default=BOOTSTRAP_REPS)
    ap.add_argument("--out", default="out/SEVERITY_RESULT.json")
    ap.add_argument("--gates-only", action="store_true")
    a = ap.parse_args()

    tbl = pq.read_table(ROOT / a.labels)
    lab = {n: np.asarray(tbl.column(n)) for n in tbl.schema.names}
    sect_cols = sorted(c for c in tbl.schema.names if c.startswith("sect_"))

    payload = {"prereg": "prereg/PREREG_SEVERITY_2026_08_15.md",
               "n_rows": len(lab["test_id"]), "reps": a.reps,
               "bootstrap_seed": BOOTSTRAP_SEED, "seeds": {}}

    g, res = run_seed(PRIMARY_SEED, lab, sect_cols, a.reps, a.gates_only)
    payload["seeds"][str(PRIMARY_SEED)] = {"gates": g["gates"], "result": res}
    print("VALIDITY FENCES:", json.dumps(g["gates"], indent=1, default=str))
    if not all(x.get("pass", True) for x in g["gates"].values()):
        print("GATE FAILURE -- refusing to report outcome numbers.")
        (ROOT / a.out).write_text(json.dumps(payload, indent=1, default=str))
        return 2
    if a.gates_only:
        return 0
    for s in PANEL_SEEDS:
        g2, r2 = run_seed(s, lab, sect_cols, a.reps)
        payload["seeds"][str(s)] = {"gates": g2["gates"], "result": r2}

    # k=2 seed mean on the primary estimands
    keys = payload["seeds"][str(PRIMARY_SEED)]["result"]["outcomes"].keys()
    payload["k2_mean"] = {
        k: {m: float(np.mean([payload["seeds"][str(s)]["result"]["outcomes"][k][m]
                              for s in (PRIMARY_SEED,) + PANEL_SEEDS]))
            for m in ("auroc", "a_clean", "auprc", "prevalence")} for k in keys}

    t0 = payload["k2_mean"]["T0_AUTOSAFE"]["auroc"]
    t1 = payload["k2_mean"]["T1_NY_LIKE"]["auroc"]
    ac = [payload["k2_mean"][k]["a_clean"] for k in ("B1_ge1_MD", "B2_ge2_MD", "B3_ge3_MD")]
    ci = payload["seeds"][str(PRIMARY_SEED)]["result"]["outcomes"]["T1_NY_LIKE"]["delta_vs_T0_ci"]
    payload["stage2_gate"] = {
        "rule": "monotone A_clean across B1->B2->B3 AND (AUROC(T1)-AUROC(T0)) >= "
                f"{STAGE2_DELTA_BAR} with paired CI excluding 0",
        "a_clean_ladder": ac,
        "monotone": bool(ac[0] <= ac[1] <= ac[2]),
        "delta_t1_vs_t0": t1 - t0,
        "delta_ci": ci,
        "ci_excludes_zero": bool(ci[0] > 0 or ci[1] < 0),
        "FIRES": bool(ac[0] <= ac[1] <= ac[2] and (t1 - t0) >= STAGE2_DELTA_BAR
                      and (ci[0] > 0 or ci[1] < 0)),
    }
    (ROOT / a.out).write_text(json.dumps(payload, indent=1, default=str))
    print(json.dumps(payload["stage2_gate"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
