"""v57 OOT-AUC root-cause analysis — tiered, observation-first, one
mechanism per experiment (plan: goal-build-a-reproducible-binary-clarke,
approved 2026-06-12 evening).

Tier 0  slicing + window_days_available forensics (no refits)
Tier 1  single-mechanism ablation refits (seed 0 @ the bundle's iteration
        count, matrices rebuilt WITH test_ids; rebuilt baseline doubles as
        the equivalence check against the shipped bundle)

Outputs: models/v57/rca/rca_results.json + per-stage prints (unbuffered).
Usage:  python3.13 -u v57_auc_rca.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score

import train_catboost_production_v57 as T
from model_contract_v57 import CATEGORICAL_FEATURES, FEATURE_NAMES

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
BUNDLE = PROJECT_ROOT / "models/v57"
RCA_DIR = BUNDLE / "rca"
STAGED_OOT = Path.home() / "autosafe_work/v57_staged_inputs/oot_set_with_advisory.parquet"
V48_DIR = Path.home() / "autosafe_work/v48_features"
NEGLIGENCE = Path.home() / "autosafe_work/negligence_features.parquet"

RESULTS = {"created": datetime.now().isoformat(), "tier0": {}, "tier1": {}}


def auc(y, s):
    return float(roc_auc_score(y, s))


# ---------------------------------------------------------------------------
# Tier 0 — observation only
# ---------------------------------------------------------------------------
def tier0():
    print("\n=== TIER 0: slicing + window_days forensics ===", flush=True)
    frame = pd.read_parquet(BUNDLE / "oot_eval_frame.parquet")
    y = frame["target"].to_numpy()
    s = frame["raw_score"].to_numpy()

    o1 = {"pooled": {"auc": auc(y, s), "n": len(frame), "rate": float(y.mean())}}
    vet = (frame["has_prior_test_observed"] == 1).to_numpy()
    o1["veterans_observed"] = {"auc": auc(y[vet], s[vet]), "n": int(vet.sum()),
                               "rate": float(y[vet].mean())}
    o1["first_mot_like"] = {"auc": auc(y[~vet], s[~vet]), "n": int((~vet).sum()),
                            "rate": float(y[~vet].mean())}
    RESULTS["tier0"]["O1_decomposition"] = o1
    print(f"  O1 pooled {o1['pooled']['auc']:.4f} | veterans {o1['veterans_observed']['auc']:.4f} "
          f"(n={o1['veterans_observed']['n']:,}) | first-MOT-like {o1['first_mot_like']['auc']:.4f} "
          f"(n={o1['first_mot_like']['n']:,})", flush=True)

    # O2: window_days_available forensics on the SHIPPED model
    model = CatBoostClassifier()
    model.load_model(str(BUNDLE / "model.cbm"))
    con = duckdb.connect()
    oot = con.execute(f"""
        SELECT test_id, test_date FROM read_parquet('{STAGED_OOT}')
        QUALIFY ROW_NUMBER() OVER (PARTITION BY test_id ORDER BY test_date) = 1
    """).fetchdf()
    frame = frame.merge(oot, on="test_id", how="left")
    wda_oot = (pd.to_datetime(frame["test_date"]) - pd.Timestamp("2019-01-01")).dt.days
    train_max_wda = (pd.Timestamp("2023-12-31") - pd.Timestamp("2019-01-01")).days
    o2 = {
        "train_wda_max_possible": int(train_max_wda),
        "oot_wda_min": int(wda_oot.min()),
        "oot_wda_max": int(wda_oot.max()),
        "oot_share_beyond_train_max": float((wda_oot > train_max_wda).mean()),
    }
    # rightmost-bin proof: clamp every OOT wda to the train max and re-score;
    # if all OOT values already fall in the top bin, predictions are identical
    import model_v57
    assert model_v57.load_model()
    Xcols = model.feature_names_
    # rebuild the OOT design matrix cheaply from the eval frame is not
    # possible (only 10 cols kept) — perturbation proof runs in Tier 1 on
    # the rebuilt matrix instead; here we record the range facts.
    RESULTS["tier0"]["O2_window_days"] = o2
    print(f"  O2 wda: train max {train_max_wda}, OOT [{o2['oot_wda_min']}, {o2['oot_wda_max']}], "
          f"beyond-train share {o2['oot_share_beyond_train_max']:.3f}", flush=True)

    # O3: monthly OOT AUC
    months = pd.to_datetime(frame["test_date"]).dt.month
    o3 = {}
    for m in range(1, 13):
        mask = (months == m).to_numpy()
        if mask.sum() > 1000 and 0 < y[mask].sum() < mask.sum():
            o3[str(m)] = {"auc": auc(y[mask], s[mask]), "n": int(mask.sum())}
    RESULTS["tier0"]["O3_monthly"] = o3
    print("  O3 monthly AUC: " + ", ".join(f"{m}:{v['auc']:.3f}" for m, v in o3.items()),
          flush=True)


# ---------------------------------------------------------------------------
# Tier 1 — rebuilt matrices with IDs, then one-change refits
# ---------------------------------------------------------------------------
def rebuild_matrices():
    print("\n=== TIER 1: rebuilding matrices with test_ids ===", flush=True)
    conn = duckdb.connect()
    conn.execute("SET memory_limit='6GB'")
    T.stage_inputs()
    dev, _ = T.build_matrix(conn, T.DEV_SET, "DEV", T.TRAIN_YEARS)
    oot, _ = T.build_matrix(conn, T.OOT_SET, "OOT")

    from hierarchical_make_adjustment import HierarchicalFeatures, ModelHierarchicalFeatures
    seg = HierarchicalFeatures(k_global=T.K_GLOBAL, k_segment=T.K_SEGMENT)
    seg.fit(dev, target_col="target", make_col="make")
    dev = seg.transform(dev, make_col="make", prev_outcome_col="prev_cycle_outcome_band")
    oot = seg.transform(oot, make_col="make", prev_outcome_col="prev_cycle_outcome_band")
    dev["segment_fail_rate_smoothed"] = dev["make_fail_rate_smoothed"]
    oot["segment_fail_rate_smoothed"] = oot["make_fail_rate_smoothed"]
    mh = ModelHierarchicalFeatures(k_global=T.K_GLOBAL, k_model=T.K_MODEL_DEFAULT)
    mh.fit(dev, target_col="target", model_col="model_id")
    dev = mh.transform(dev, model_col="model_id", prev_outcome_col="prev_cycle_outcome_band")
    oot = mh.transform(oot, model_col="model_id", prev_outcome_col="prev_cycle_outcome_band")
    stats = T.compute_cohort_stats(dev)
    dev = T.add_cohort_features(dev, stats)
    oot = T.add_cohort_features(oot, stats)
    for c in CATEGORICAL_FEATURES:
        dev[c] = dev[c].astype(str)
        oot[c] = oot[c].astype(str)
    return dev, oot


def fit_and_score(dev, oot, feature_cols, label, iterations):
    from catboost import Pool
    cat_idx = [feature_cols.index(f) for f in CATEGORICAL_FEATURES if f in feature_cols]
    year = pd.to_datetime(dev["test_date"]).dt.year
    w = np.select([year == 2023, year == 2022, year == 2021], [20.0, 6.0, 2.0], default=1.0)
    pool = Pool(dev[feature_cols], dev["target"].astype("int8"),
                cat_features=cat_idx, weight=w)
    m = CatBoostClassifier(random_seed=0, verbose=0, cat_features=cat_idx,
                           **dict(T.PARAMS, iterations=iterations))
    m.fit(pool)
    a = auc(oot["target"].astype(int).to_numpy(),
            m.predict_proba(oot[feature_cols])[:, 1])
    print(f"  {label}: OOT AUC = {a:.4f}", flush=True)
    return a


def tier1():
    manifest = json.loads((BUNDLE / "training_manifest.json").read_text())
    iters = int(manifest["best_iterations"])
    dev, oot = rebuild_matrices()
    base_cols = list(FEATURE_NAMES)

    runs = {}
    runs["baseline_rebuilt"] = fit_and_score(dev, oot, base_cols,
                                             "BASELINE (rebuilt, seed0)", iters)

    # E1: drop window_days_available
    e1_cols = [c for c in base_cols if c != "window_days_available"]
    runs["E1_drop_window_days"] = fit_and_score(dev, oot, e1_cols, "E1 -window_days", iters)

    # E1b: clip wda to train max (only meaningful if E1 helped; cheap anyway)
    dev_c, oot_c = dev.copy(), oot.copy()
    cap = float(dev["window_days_available"].max())
    oot_c["window_days_available"] = np.minimum(oot_c["window_days_available"], cap)
    runs["E1b_clip_window_days"] = fit_and_score(dev_c, oot_c, base_cols,
                                                 "E1b clip wda", iters)

    # E2: real v48 OOF eb_unified_prior instead of the model_age alias
    v48d = pd.read_parquet(V48_DIR / "unified_prior_dev.parquet")[["test_id", "eb_unified_prior"]]
    v48o = pd.read_parquet(V48_DIR / "unified_prior_oot.parquet")[["test_id", "eb_unified_prior"]]
    for v in (v48d, v48o):
        v.drop_duplicates(subset="test_id", keep="first", inplace=True)
    dev_e2 = dev.drop(columns=["eb_unified_prior"]).merge(v48d, on="test_id", how="left")
    oot_e2 = oot.drop(columns=["eb_unified_prior"]).merge(v48o, on="test_id", how="left")
    dev_e2["eb_unified_prior"] = dev_e2["eb_unified_prior"].fillna(0.25)
    oot_e2["eb_unified_prior"] = oot_e2["eb_unified_prior"].fillna(0.25)
    cov = float(oot_e2["eb_unified_prior"].ne(0.25).mean())
    print(f"  E2 v48 OOT coverage: {cov:.3f}", flush=True)
    runs["E2_real_v48_oof"] = fit_and_score(dev_e2, oot_e2, base_cols, "E2 real v48 OOF", iters)

    # E3: v46 EB negligence trio instead of the crude serving formula
    neg = pd.read_parquet(NEGLIGENCE)[
        ["vehicle_id", "historic_negligence_ratio_smoothed", "negligence_band",
         "raw_behavioral_count"]].drop_duplicates(subset="vehicle_id", keep="first")
    def swap_neg(df):
        out = df.drop(columns=["historic_negligence_ratio_smoothed",
                               "negligence_band", "raw_behavioral_count"])
        out = out.merge(neg, on="vehicle_id", how="left")
        out["historic_negligence_ratio_smoothed"] = out["historic_negligence_ratio_smoothed"].fillna(0.05)
        out["negligence_band"] = out["negligence_band"].fillna("unknown").astype(str)
        out["raw_behavioral_count"] = out["raw_behavioral_count"].fillna(0)
        return out
    runs["E3_v46_negligence"] = fit_and_score(swap_neg(dev), swap_neg(oot), base_cols,
                                              "E3 v46 EB negligence", iters)

    # E4 (diagnostic only): own-frame test_mileage + days_since_last_test
    con = duckdb.connect()
    def own_frame(df, path):
        baked = con.execute(f"""
            SELECT test_id,
                   CAST(COALESCE(test_mileage, 0) AS DOUBLE) AS own_mileage,
                   CAST(COALESCE(days_since_last_test, 0) AS DOUBLE) AS own_days
            FROM read_parquet('{path}')
            QUALIFY ROW_NUMBER() OVER (PARTITION BY test_id ORDER BY test_date) = 1
        """).fetchdf()
        out = df.merge(baked, on="test_id", how="left")
        out["test_mileage"] = out["own_mileage"].fillna(0)
        out["days_since_last_test"] = out["own_days"].fillna(0)
        out["days_since_pass_ratio"] = out["days_since_last_test"] / 365.0
        return out
    runs["E4_own_frame_mileage_gap"] = fit_and_score(
        own_frame(dev, T.DEV_SET), own_frame(oot, T.OOT_SET), base_cols,
        "E4 own-frame mileage/gap (diagnostic)", iters)

    base = runs["baseline_rebuilt"]
    RESULTS["tier1"] = {
        "iterations": iters,
        "runs": runs,
        "deltas_vs_rebuilt_baseline": {k: round(v - base, 4) for k, v in runs.items()},
        "shipped_bundle_pooled_auc": 0.7135,
    }


def main():
    RCA_DIR.mkdir(parents=True, exist_ok=True)
    tier0()
    tier1()
    out = RCA_DIR / "rca_results.json"
    out.write_text(json.dumps(RESULTS, indent=2))
    print(f"\nRCA DONE -> {out}", flush=True)
    print(json.dumps(RESULTS["tier1"]["deltas_vs_rebuilt_baseline"], indent=2))


if __name__ == "__main__":
    sys.exit(main())
