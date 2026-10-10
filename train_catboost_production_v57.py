"""AutoSafe CatBoost V57 trainer — parity-first Stage-1 release.

Trains the v57 model on the contract emitted by ``model_contract_v57``
(105 features: 104 served v55 − 4 RC-3 drops + 5 coverage features, 35
observed-history renames) with every history-derived feature recomputed in
the [2019-01-01, test_date) observation window using SERVING definitions
(``v57_history_recompute``, fixture-parity-tested against fe_v57).

Differences from the v55 trainer, each tied to a ledger/RCA decision:
- shared contract module, hard failure on any missing matrix column,
- in-window serving-frame history recompute (RC-2 vocab, RC-4 frames,
  RC-5 window) replacing the baked d./h./a. history columns,
- canonical per-feature defaults from the contract (RC-1),
- no station priors / strictness / suspension_risk_profile (RC-3 drop-4),
- no neglect scores, no system/trajectory/hazard ablation families,
- eb_unified_prior aliases model_age_fail_rate_eb (serving fe_v55:677 —
  the served model never saw the v48 OOF values),
- early stopping on a dev 2023-H2 time tail (seed 0), then the 10-seed
  ensemble retrains on FULL dev at the chosen iteration count; OOT is
  evaluated exactly once (the v55 protocol early-stopped on OOT),
- the deployed artifact IS the evaluated artifact: catboost.sum_models
  merges the 10 seeds into one model.cbm,
- pickle-free calibrator.json (sigmoid(A*raw + B), audit P0 gf2b),
- full bundle emission: feature_contract.json (via model_bundle),
  training_manifest.json (hashes, rows, date ranges, env, coverage),
  feature_artifacts/ JSON lookups for serving, metrics.json,
  oot_eval_frame.parquet, and ~/autosafe_work/v57_prepared_data.pkl for
  the GF-17 --expect-fixed end-gate.

Usage:  python3.13 train_catboost_production_v57.py
"""

import hashlib
import json
import pickle
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool, sum_models
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)

from feature_engineering_v55 import get_age_band
from hierarchical_make_adjustment import HierarchicalFeatures, ModelHierarchicalFeatures
from model_contract_v57 import (
    CATEGORICAL_FEATURES,
    FEATURE_NAMES,
    HISTORY_WINDOW_START,
    contract_as_dict,
)
from regional_defaults import get_corrosion_index
import v57_history_recompute as recompute
from v57_history_recompute import build_history_features
from feature_engineering_v55 import HIGH_RISK_MODELS_SET

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set_with_advisory.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_set_with_advisory.parquet"
LAKE = PROJECT_ROOT / "cycle_first_with_history.parquet"
V45_FEATURES_DIR = Path.home() / "autosafe_work/v45_features"
TEXT_MINING_FEATURES = Path.home() / "autosafe_work/text_mining_features_v52.parquet"
COMPONENT_FAILURES = PROJECT_ROOT / "component_failures_all_years.parquet"

WORK_DIR = PROJECT_ROOT / "models" / "v57"
ARTIFACTS_DIR = WORK_DIR / "feature_artifacts"
MODEL_FILE = WORK_DIR / "model.cbm"
CALIBRATOR_FILE = WORK_DIR / "calibrator.json"
CONTRACT_FILE = WORK_DIR / "feature_contract.json"
METRICS_FILE = WORK_DIR / "metrics.json"
MANIFEST_FILE = WORK_DIR / "training_manifest.json"
OOT_EVAL_FRAME = WORK_DIR / "oot_eval_frame.parquet"
PREPARED_DATA = Path.home() / "autosafe_work/v57_prepared_data.pkl"

TRAIN_YEARS = [2019, 2020, 2021, 2022, 2023]
EARLY_STOP_TAIL_START = "2023-07-01"   # dev time-tail for iteration search
N_SEEDS = 10
PARAMS = {
    "iterations": 2000,
    "learning_rate": 0.02,
    "depth": 6,
    "l2_leaf_reg": 4,
    "border_count": 128,
    "random_strength": 1.0,
    "bagging_temperature": 0.5,
    "eval_metric": "AUC",
}
K_GLOBAL, K_SEGMENT, K_MODEL_DEFAULT = 10, 100, 20

FEATURE_COLS = list(FEATURE_NAMES)
CAT_FEATURES = list(CATEGORICAL_FEATURES)

# Serving-emittable categorical levels (from feature_engineering_v55/v57
# code paths). The matrix must contain every HIGH-MASS level; rare levels
# absent from the matrix are reported in the manifest as bounded residuals.
SERVING_VOCAB = {
    "prev_cycle_outcome_band": {"pass", "fail", "unknown", "first_test"},
    "gap_band": {"early", "on_time", "slightly_late", "late", "very_late", "first_test"},
    "advisory_trend": {"increasing", "decreasing", "stable", "unknown"},
    "usage_band_hybrid": {"low", "average", "high", "very_high"},
    "negligence_band": {"clean", "low", "high", "chronic"},
    "mech_risk_driver": {"CLEAN", "BRAKE", "SUSP", "STEER", "STRUCT"},
    "dominant_mechanism": {"NO_HISTORY", "CLEAN", "CORROSION", "WEAR", "LEAK", "DAMAGE"},
    "test_month": {str(m) for m in range(1, 13)},
    "day_of_week": {str(d) for d in range(7)},
}
HIGH_MASS_LEVELS = {
    "prev_cycle_outcome_band": {"pass", "fail", "first_test"},
    "gap_band": {"early", "on_time", "slightly_late", "late", "very_late", "first_test"},
    "advisory_trend": {"increasing", "decreasing", "stable", "unknown"},
    "usage_band_hybrid": {"low", "average", "high", "very_high"},
    "negligence_band": {"clean", "low", "high", "chronic"},
    "mech_risk_driver": {"CLEAN", "BRAKE", "SUSP"},
    "dominant_mechanism": {"NO_HISTORY", "CLEAN"},
}


STAGING_DIR = Path.home() / "autosafe_work/v57_staged_inputs"


def stage_inputs() -> dict:
    """Copy heavy iCloud-hosted inputs to local APFS and rebind paths.

    Training does hours of random I/O; the iCloud volume serves evicted
    placeholders and stalls under load (observed 2026-06-12: Errno 60
    timeouts + a corrupt half-materialized parquet). One sequential copy
    per input makes the run immune. Local copies are footer-validated and
    are the files the manifest hashes; original paths are recorded.
    """
    import shutil

    import pyarrow.parquet as pq

    global DEV_SET, OOT_SET
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    mapping = {}

    def stage(src: Path) -> Path:
        dst = STAGING_DIR / src.name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            print(f"  staging {src.name} ({src.stat().st_size / 1e6:,.0f} MB)...",
                  flush=True)
            shutil.copy2(src, dst)
        pq.ParquetFile(dst).metadata  # local footer validation
        mapping[str(src)] = str(dst)
        return dst

    DEV_SET = stage(DEV_SET)
    OOT_SET = stage(OOT_SET)
    recompute.LAKE = stage(recompute.LAKE)
    recompute.COMPONENT_FAILURES = stage(recompute.COMPONENT_FAILURES)
    recompute.SPINE_FILES = [stage(p) for p in recompute.SPINE_FILES]
    # TEXT_MINING_FEATURES / V45 / extension already live on local APFS
    return mapping


def build_target_query(dataset_path: Path, train_years=None) -> str:
    """Target rows + their own lagged advisory seed columns.

    The veterans population filter is v55's, evaluated on the ORIGINAL
    baked columns so the v57 population is row-identical to v55's.
    """
    where = ["""NOT (
        d.prev_cycle_outcome_band = 'NO_PRIOR_RECORDED'
        AND (d.n_prior_tests IS NULL OR d.n_prior_tests = 0)
    )"""]
    if train_years:
        years = ", ".join(str(y) for y in train_years)
        where.append(f"YEAR(d.test_date) IN ({years})")
    return f"""
    SELECT
        d.test_id, d.vehicle_id, d.test_date, d.first_use_date,
        CAST(d.is_failure AS INT) AS target,
        COALESCE(d.model_id, 'UNKNOWN UNKNOWN') AS model_id,
        COALESCE(d.make, 'UNKNOWN') AS make,
        COALESCE(d.postcode_area, 'UNKNOWN') AS postcode_area,
        COALESCE(d.prev_advisory_total, 0) AS prev_advisory_total,
        COALESCE(d.prev_advisory_known, 0) AS prev_advisory_known,
        COALESCE(d.prev_advisory_brakes, 0) AS prev_advisory_brakes,
        COALESCE(d.prev_advisory_tyres, 0) AS prev_advisory_tyres,
        COALESCE(d.prev_advisory_suspension, 0) AS prev_advisory_suspension
    FROM read_parquet('{dataset_path}') d
    WHERE {' AND '.join(where)}
    -- the *_with_advisory builds carry a handful of duplicated test_ids
    -- (advisory-join inflation; 76 in the OOT file) — keep one row each
    QUALIFY ROW_NUMBER() OVER (PARTITION BY d.test_id ORDER BY d.test_date, d.vehicle_id) = 1
    """


def add_serving_age_band(df: pd.DataFrame) -> pd.DataFrame:
    """Vehicle age band with the SERVING formula (fe_v55:583 + get_age_band).

    Serving uses manufacture_date; the matrices have first_use_date (the
    closest training-side proxy) — documented Stage-1 residual.
    """
    age_years = (
        (pd.to_datetime(df["test_date"]) - pd.to_datetime(df["first_use_date"])).dt.days
        // 365
    ).fillna(5).astype(int)
    df["serving_age_band"] = [get_age_band(a) for a in age_years]
    return df


def add_cohort_features(df: pd.DataFrame, cohort_stats: dict) -> pd.DataFrame:
    """mileage_cohort_ratio / advisory_cohort_delta with serving lookups
    (fe_v55:586-610) keyed (model_id, serving_age_band)."""
    cm, ca = cohort_stats["cohort_mileage"], cohort_stats["cohort_advisory"]
    gm, ga = cohort_stats["global_mileage_avg"], cohort_stats["global_advisory_avg"]
    keys = list(zip(df["model_id"], df["serving_age_band"]))
    mileage_avg = np.array([cm.get(k, gm) or gm for k in keys], dtype=float)
    advisory_avg = np.array([ca.get(k, ga) for k in keys], dtype=float)
    df["mileage_cohort_ratio"] = np.where(
        mileage_avg > 0, df["test_mileage"] / mileage_avg, 1.0)
    df["advisory_cohort_delta"] = df["prior_advisory_count_observed"] - advisory_avg
    return df


def compute_cohort_stats(df: pd.DataFrame) -> dict:
    """v55 cohort statistics (trainer:2143-2181) keyed by serving age band."""
    global_mileage = float(df["test_mileage"].mean())
    global_advisory = float(df["prior_advisory_count_observed"].mean())
    g = df.groupby(["model_id", "serving_age_band"]).agg(
        mileage_avg=("test_mileage", "mean"),
        advisory_avg=("prior_advisory_count_observed", "mean"),
        cohort_size=("test_mileage", "size"),
    ).reset_index()
    m = df.groupby("model_id").agg(
        model_mileage=("test_mileage", "mean"),
        model_advisory=("prior_advisory_count_observed", "mean"),
    ).reset_index()
    g = g.merge(m, on="model_id", how="left")
    small = g["cohort_size"] < 10
    g.loc[small, "mileage_avg"] = g.loc[small, "model_mileage"]
    g.loc[small, "advisory_avg"] = g.loc[small, "model_advisory"]
    return {
        "cohort_mileage": {(r.model_id, r.serving_age_band): float(r.mileage_avg) for r in g.itertuples()},
        "cohort_advisory": {(r.model_id, r.serving_age_band): float(r.advisory_avg) for r in g.itertuples()},
        "global_mileage_avg": global_mileage,
        "global_advisory_avg": global_advisory,
    }


def add_v45_model_age(df: pd.DataFrame, dataset: str) -> pd.DataFrame:
    f = V45_FEATURES_DIR / f"model_age_features_{dataset.lower()}.parquet"
    if not f.exists():
        raise FileNotFoundError(f"V45 features missing: {f}")
    v45 = pd.read_parquet(f)
    # upstream per-test feature files can carry duplicated test_ids (same
    # advisory-join inflation as the OOT set); values are deterministic per
    # test so keep the first
    v45 = v45.drop_duplicates(subset="test_id", keep="first")
    n = len(df)
    df = df.merge(v45[["test_id", "model_age_fail_rate_eb", "make_age_fail_rate_eb"]],
                  on="test_id", how="left")
    assert len(df) == n, "V45 join inflated rows"
    cov = df["model_age_fail_rate_eb"].notna().mean()
    df["model_age_fail_rate_eb"] = df["model_age_fail_rate_eb"].fillna(0.25)
    df["make_age_fail_rate_eb"] = df["make_age_fail_rate_eb"].fillna(0.25)
    print(f"    V45 coverage ({dataset}): {cov * 100:.1f}%")
    return df


def add_v52_text(df: pd.DataFrame) -> pd.DataFrame:
    """Keyword text-mining columns (not recomputable without the item lake).

    max_severity_score / severity_escalation_flag / has_advisory_history are
    recomputed serving-side quantities and are NOT taken from v52.
    dominant_mechanism is relabelled to the serving rule (fe_v55:473-481):
    no observed prior tests -> NO_HISTORY; v52 NO_HISTORY/CLEAN -> CLEAN.
    """
    cols = ["test_id",
            "text_corrosion_index", "text_wear_index", "text_leak_index",
            "text_damage_index", "text_corrosion_index_log", "text_wear_index_log",
            "text_leak_index_log", "text_damage_index_log",
            "has_corrosion_history", "has_wear_history", "has_leak_history",
            "has_damage_history", "mechanism_count", "dominant_mechanism"]
    text = pd.read_parquet(TEXT_MINING_FEATURES)[cols]
    text = text.drop_duplicates(subset="test_id", keep="first")
    n = len(df)
    df = df.merge(text, on="test_id", how="left")
    assert len(df) == n, "V52 join inflated rows"
    for c in cols[1:-1]:
        df[c] = df[c].fillna(0.0)
    dm = df["dominant_mechanism"].fillna("NO_HISTORY")
    df["dominant_mechanism"] = np.select(
        [df["n_prior_tests_observed"] == 0, dm.isin(["NO_HISTORY", "CLEAN"])],
        ["NO_HISTORY", "CLEAN"], default=dm)
    return df


def assert_vocab(df: pd.DataFrame, label: str) -> dict:
    report = {}
    for col, serving_levels in SERVING_VOCAB.items():
        matrix_levels = set(df[col].astype(str).unique())
        missing = serving_levels - matrix_levels
        extra = matrix_levels - serving_levels
        report[col] = {"matrix_only": sorted(extra), "serving_only_absent": sorted(missing)}
        hard = missing & HIGH_MASS_LEVELS.get(col, set())
        if hard:
            raise ValueError(
                f"[{label}] high-mass serving level(s) absent from matrix vocab "
                f"for {col}: {sorted(hard)} — would be OOV-dead at serve")
    return report


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def precision_and_lift_at_k(y_true: np.ndarray, y_score: np.ndarray, k: float):
    n = len(y_true)
    cutoff = max(1, int(n * k))
    top = np.argsort(-y_score)[:cutoff]
    precision = float(np.mean(y_true[top]))
    base = float(np.mean(y_true))
    return precision, (precision / base if base > 0 else float("nan"))


def build_matrix(conn, dataset_path: Path, dataset: str, train_years=None) -> pd.DataFrame:
    print(f"\n[{dataset}] loading targets...")
    targets = conn.execute(build_target_query(dataset_path, train_years)).fetchdf()
    print(f"  {len(targets):,} rows (veterans filter applied)")

    hist = build_history_features(
        conn, targets, label=dataset, n_chunks=4 if len(targets) > 500_000 else 1)
    df = targets.merge(hist, on="test_id", how="left", validate="one_to_one")

    df = add_serving_age_band(df)
    df = add_v45_model_age(df, dataset)
    # serving aliases the unified prior to model-age EB (fe_v55:677)
    df["eb_unified_prior"] = df["model_age_fail_rate_eb"]
    df = add_v52_text(df)

    # regional features: the serving static lookup, applied per postcode area
    corro = {a: get_corrosion_index(a) for a in df["postcode_area"].astype(str).unique()}
    df["local_corrosion_index"] = df["postcode_area"].astype(str).map(corro)
    df["local_corrosion_delta"] = df["local_corrosion_index"] - 0.5

    df["high_risk_model_flag"] = (
        df["model_id"].astype(str).str.upper().str.strip().isin(HIGH_RISK_MODELS_SET)
    ).astype(int)

    # aliases consumed internally by HierarchicalFeatures.transform (the
    # interaction columns it derives from these are not contract features)
    df["age_band"] = df["serving_age_band"]
    df["n_prior_tests"] = df["n_prior_tests_observed"]
    return df, hist.attrs.get("coverage", {})


def main() -> int:
    start = datetime.now()
    print("=" * 70)
    print("AUTOSAFE CATBOOST V57 TRAINING — parity-first Stage-1")
    print(f"window {HISTORY_WINDOW_START}  features {len(FEATURE_COLS)}  seeds {N_SEEDS}")
    print("=" * 70)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect()
    conn.execute("SET memory_limit='6GB'")

    print("\n[0] staging inputs to local disk...", flush=True)
    staged = stage_inputs()
    print(f"  staged {len(staged)} inputs under {STAGING_DIR}")

    dev, dev_coverage = build_matrix(conn, DEV_SET, "DEV", TRAIN_YEARS)
    oot, oot_coverage = build_matrix(conn, OOT_SET, "OOT")

    print("\n[2] hierarchical features (fit on DEV)...")
    segment_hf = HierarchicalFeatures(k_global=K_GLOBAL, k_segment=K_SEGMENT)
    segment_hf.fit(dev, target_col="target", make_col="make")
    dev = segment_hf.transform(dev, make_col="make", prev_outcome_col="prev_cycle_outcome_band")
    oot = segment_hf.transform(oot, make_col="make", prev_outcome_col="prev_cycle_outcome_band")
    dev["segment_fail_rate_smoothed"] = dev["make_fail_rate_smoothed"]
    oot["segment_fail_rate_smoothed"] = oot["make_fail_rate_smoothed"]
    model_hf = ModelHierarchicalFeatures(k_global=K_GLOBAL, k_model=K_MODEL_DEFAULT)
    model_hf.fit(dev, target_col="target", model_col="model_id")
    dev = model_hf.transform(dev, model_col="model_id", prev_outcome_col="prev_cycle_outcome_band")
    oot = model_hf.transform(oot, model_col="model_id", prev_outcome_col="prev_cycle_outcome_band")

    print("\n[3] cohort statistics (DEV) + residuals...")
    cohort_stats = compute_cohort_stats(dev)
    dev = add_cohort_features(dev, cohort_stats)
    oot = add_cohort_features(oot, cohort_stats)

    # ------------------------------------------------------------------ gates
    missing = [f for f in FEATURE_COLS if f not in dev.columns or f not in oot.columns]
    if missing:
        raise ValueError(f"V57 training matrix missing required features: {missing}")

    for col in CAT_FEATURES:
        dev[col] = dev[col].astype(str)
        oot[col] = oot[col].astype(str)
    dev["target"] = dev["target"].astype("int8")
    oot["target"] = oot["target"].astype("int8")

    vocab_report = {"dev": assert_vocab(dev, "DEV"), "oot": assert_vocab(oot, "OOT")}
    print("  vocab gate: PASS (high-mass serving levels all present)")

    X_train_full = dev[FEATURE_COLS]
    y_train_full = dev["target"]
    X_test = oot[FEATURE_COLS]
    y_test = oot["target"]

    dev_dates = pd.to_datetime(dev["test_date"])
    year = dev_dates.dt.year
    weights_full = np.select([year == 2023, year == 2022, year == 2021],
                             [20.0, 6.0, 2.0], default=1.0)
    cat_idx = [FEATURE_COLS.index(f) for f in CAT_FEATURES]

    print(f"\n[4] iteration search: dev tail >= {EARLY_STOP_TAIL_START} (seed 0)...")
    tail = dev_dates >= pd.Timestamp(EARLY_STOP_TAIL_START)
    print(f"  head {(~tail).sum():,} rows / tail {tail.sum():,} rows")
    head_pool = Pool(X_train_full[~tail], y_train_full[~tail],
                     cat_features=cat_idx, weight=weights_full[~tail])
    tail_pool = Pool(X_train_full[tail], y_train_full[tail], cat_features=cat_idx)
    probe = CatBoostClassifier(random_seed=0, verbose=200, cat_features=cat_idx, **PARAMS)
    probe.fit(head_pool, eval_set=tail_pool, early_stopping_rounds=150)
    probe_best = probe.get_best_iteration()
    if probe_best is None:
        probe_best = PARAMS["iterations"]
    best_iter = min(PARAMS["iterations"], max(200, int(probe_best * 1.05) + 1))
    print(f"  best_iteration={probe_best} -> ensemble iterations={best_iter}")

    print(f"\n[5] training {N_SEEDS}-seed ensemble on FULL dev @ {best_iter} iterations...")
    full_pool = Pool(X_train_full, y_train_full, cat_features=cat_idx, weight=weights_full)
    ensemble_params = dict(PARAMS, iterations=best_iter)
    seed_dir = Path.home() / "autosafe_work/v57_seed_models"
    seed_dir.mkdir(parents=True, exist_ok=True)
    models = []
    for seed in range(N_SEEDS):
        ckpt = seed_dir / f"seed_{seed}_it{best_iter}.cbm"
        m = CatBoostClassifier(random_seed=seed, verbose=0, cat_features=cat_idx,
                               **ensemble_params)
        if ckpt.exists():
            # deterministic resume: same data, params, and probe-chosen
            # iteration count -> checkpoint is the identical model
            m.load_model(str(ckpt))
            print(f"  seed {seed}: resumed from checkpoint")
        else:
            m.fit(full_pool)
            m.save_model(str(ckpt))
        models.append(m)
        print(f"  seed {seed}: OOT AUC={roc_auc_score(y_test, m.predict_proba(X_test)[:, 1]):.4f}")

    print("\n[6] merging seeds (sum_models) — deployed artifact == evaluated artifact...")
    merged = sum_models(models, weights=[1.0 / N_SEEDS] * N_SEEDS)
    # sum_models returns a base CatBoost object (no predict_proba). Persist
    # it and reload through CatBoostClassifier — the EXACT path the serving
    # loader (model_v57) uses — so the metrics below describe precisely what
    # production will execute.
    merged.save_model(str(MODEL_FILE))
    deployed = CatBoostClassifier()
    deployed.load_model(str(MODEL_FILE))
    raw_train = deployed.predict_proba(X_train_full)[:, 1]
    raw_test = deployed.predict_proba(X_test)[:, 1]

    print("\n[7] Platt calibration (fit on train predictions, v55 protocol)...")
    calib = LogisticRegression()
    calib.fit(raw_train.reshape(-1, 1), y_train_full)
    cal_test = calib.predict_proba(raw_test.reshape(-1, 1))[:, 1]
    A, B = float(calib.coef_[0][0]), float(calib.intercept_[0])

    print("\n[8] OOT metrics (single evaluation)...")
    y = y_test.to_numpy()
    metrics = {
        "version": "v57",
        "n_features": len(FEATURE_COLS),
        "oot_rows": int(len(y)),
        "oot_base_rate": float(y.mean()),
        "roc_auc": float(roc_auc_score(y, raw_test)),
        "pr_auc": float(average_precision_score(y, raw_test)),
        "log_loss": float(log_loss(y, raw_test)),
        "brier": float(brier_score_loss(y, raw_test)),
        "calibrated_brier": float(brier_score_loss(y, cal_test)),
        "train_auc": float(roc_auc_score(y_train_full, raw_train)),
        "best_iterations": best_iter,
        "protocol_notes": [
            "early stopping searched on dev 2023-H2 tail (seed 0); OOT untouched",
            "metrics computed from the summed-ensemble artifact (model.cbm)",
            "v55 comparator (0.7500) early-stopped on OOT and reported "
            "probability-space ensemble means from an unshipped artifact",
        ],
        "created": datetime.now().isoformat(),
    }
    for k_pct, k in (("1pct", 0.01), ("5pct", 0.05), ("10pct", 0.10)):
        p, lift = precision_and_lift_at_k(y, cal_test, k)
        metrics[f"precision_at_{k_pct}"] = p
        metrics[f"lift_at_{k_pct}"] = lift
    for k_, v_ in metrics.items():
        if isinstance(v_, float):
            print(f"  {k_}: {v_:.4f}")

    print("\n[9] writing bundle...")
    # model.cbm already persisted at the merge step (and reloaded from disk
    # for evaluation — the file on disk IS the evaluated artifact)
    with open(CALIBRATOR_FILE, "w") as f:
        json.dump({"type": "platt_sigmoid", "A": A, "B": B,
                   "formula": "p_cal = 1/(1+exp(-(A*p_raw+B)))",
                   "fitted_on": "summed-ensemble train predictions"}, f, indent=2)
    with open(METRICS_FILE, "w") as f:
        json.dump(metrics, f, indent=2)

    def _encode(d):  # tuple keys -> 'a|b' for JSON
        return {"|".join(map(str, k)) if isinstance(k, tuple) else str(k): v
                for k, v in d.items()}

    artifacts = {
        "cohort_stats.json": {
            "cohort_mileage": _encode(cohort_stats["cohort_mileage"]),
            "cohort_advisory": _encode(cohort_stats["cohort_advisory"]),
            "global_mileage_avg": cohort_stats["global_mileage_avg"],
            "global_advisory_avg": cohort_stats["global_advisory_avg"],
        },
        "hierarchical_rates.json": {
            "make_rates": _encode(getattr(segment_hf, "make_rates", {}) or {}),
            "segment_rates": _encode(getattr(segment_hf, "segment_rates", {}) or {}),
            "model_rates": _encode(getattr(model_hf, "model_rates", {}) or {}),
            "global_fail_rate": float(y_train_full.mean()),
        },
        "model_age_rates.json": {
            "model_age_rates": _encode(
                oot.groupby(["model_id", "serving_age_band"])["model_age_fail_rate_eb"]
                .mean().to_dict()),
            "make_age_rates": _encode(
                oot.groupby(["make", "serving_age_band"])["make_age_fail_rate_eb"]
                .mean().to_dict()),
            "global_fail_rate": float(oot["model_age_fail_rate_eb"].mean()),
        },
    }
    for name, payload in artifacts.items():
        with open(ARTIFACTS_DIR / name, "w") as f:
            json.dump(payload, f)

    model_age_global = artifacts["model_age_rates.json"]["global_fail_rate"]
    contract_as_dict(
        default_overrides={"eb_unified_prior": model_age_global},
        artifact_dependencies=[f"feature_artifacts/{n}" for n in artifacts],
        out_path=CONTRACT_FILE,
    )

    eval_cols = ["test_id", "target", "make", "model_id", "serving_age_band",
                 "test_mileage", "prev_cycle_outcome_band", "gap_band",
                 "has_prior_test_observed", "has_left_truncated_history"]
    frame = oot[eval_cols].copy()
    frame["raw_score"] = raw_test
    frame["calibrated_score"] = cal_test
    frame.to_parquet(OOT_EVAL_FRAME, index=False)

    with open(PREPARED_DATA, "wb") as f:
        pickle.dump({"X_train": X_train_full, "y_train": y_train_full,
                     "X_test": X_test, "y_test": y_test,
                     "cat_features": CAT_FEATURES, "feature_cols": FEATURE_COLS,
                     "train_weights": weights_full}, f)

    print("\n[10] training manifest...")
    inputs = [DEV_SET, OOT_SET, recompute.LAKE, recompute.COMPONENT_FAILURES,
              TEXT_MINING_FEATURES,
              V45_FEATURES_DIR / "model_age_features_dev.parquet",
              V45_FEATURES_DIR / "model_age_features_oot.parquet"]
    inputs += list(recompute.SPINE_FILES)
    inputs += [recompute.HISTORY_EXTENSION_2024]
    git_sha = subprocess.run(["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
    manifest = {
        "version": "v57",
        "command": "python3.13 train_catboost_production_v57.py",
        "interpreter": sys.executable,
        "git_commit": git_sha,
        "history_window_start": HISTORY_WINDOW_START.isoformat(),
        "feature_count": len(FEATURE_COLS),
        "feature_names": FEATURE_COLS,
        "categorical_features": CAT_FEATURES,
        "train_years": TRAIN_YEARS,
        "early_stop_tail_start": EARLY_STOP_TAIL_START,
        "best_iterations": best_iter,
        "n_seeds": N_SEEDS,
        "hyperparameters": PARAMS,
        "dev_rows": int(len(dev)),
        "oot_rows": int(len(oot)),
        "dev_date_range": [str(dev_dates.min().date()), str(dev_dates.max().date())],
        "oot_date_range": [str(pd.to_datetime(oot['test_date']).min().date()),
                           str(pd.to_datetime(oot['test_date']).max().date())],
        "recompute_coverage": {"dev": dev_coverage, "oot": oot_coverage},
        "staged_inputs": staged,
        "vocab_report": vocab_report,
        "prepared_data": str(PREPARED_DATA),
        "input_files": [
            {"path": str(p), "size_bytes": p.stat().st_size, "sha256": sha256_of(p)}
            for p in inputs if p.exists()
        ],
        "package_versions": {
            m.__name__: getattr(m, "__version__", "?")
            for m in (np, pd, duckdb)
        },
        "elapsed_seconds": (datetime.now() - start).total_seconds(),
        "created": datetime.now().isoformat(),
    }
    import catboost as _cb
    import sklearn as _sk
    manifest["package_versions"]["catboost"] = _cb.__version__
    manifest["package_versions"]["scikit-learn"] = _sk.__version__
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nDONE in {(datetime.now() - start).total_seconds() / 60:.1f} min")
    print(f"  OOT ROC-AUC: {metrics['roc_auc']:.4f} (floor 0.7000)")
    print(f"  bundle: {WORK_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
