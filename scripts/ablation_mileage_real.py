"""Real-data mileage ablation (replaces the synthetic ablation_mileage_honest.py
evidence). Computes the point-in-time-honest mileage representations on the ACTUAL
V55 spine and measures discriminative power (univariate AUC) and marginal-over-age
AUC on the held-out OOT, with cohort-rate means fit on DEV only.

Reps (per target test T):
  contemporaneous   = test_mileage                      (LEAKY: odo read AT T; reference only)
  honest_level      = test_mileage - miles_since_last_test  (= odo at T's prior test)
  leaky_rate        = miles_since*365/days_since         (= annualized_mileage_v2, training-leaky)
  honest_rate       = as-of-prior rate, 2-hop T->prev->prevprev (= serving's rate)
  cohort_norm_rate  = honest_rate / DEV cohort-mean(model_id, age_band)

Decisive question: does cohort_norm_rate win marginal-over-age on REAL data (the
HONEST_MILEAGE.md claim, currently shown only on a synthetic generator)?
"""
from __future__ import annotations
import numpy as np
import duckdb
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

IC = "/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe"
DEV = f"{IC}/stratified_samples/dev_set_with_advisory.parquet"
OOT = f"{IC}/stratified_samples/oot_set_with_advisory.parquet"
HIST = f"{IC}/cycle_first_with_history.parquet"
SPINE = f"{IC}/validation_samples/sampled_*.parquet"

con = duckdb.connect()
con.execute("PRAGMA memory_limit='5GB'"); con.execute("PRAGMA threads=4")
con.execute("SET preserve_insertion_order=false")


def build(path: str, label: str) -> "pd.DataFrame":
    print(f"[{label}] building honest reps (2-hop)...", flush=True)
    sql = f"""
    WITH tgt AS (
        SELECT test_id, CAST(test_mileage AS DOUBLE) test_mileage,
               CAST(miles_since_last_test AS DOUBLE) miles_since,
               CAST(days_since_last_test AS DOUBLE) days_since,
               age_band_ordinal, model_id, CAST(is_failure AS INT) y
        FROM read_parquet('{path}') WHERE test_mileage IS NOT NULL
    ),
    l1 AS (SELECT test_id, prev_cycle_test_id prev_id
           FROM read_parquet('{HIST}') WHERE test_id IN (SELECT test_id FROM tgt)),
    l2 AS (SELECT test_id prev_id, prev_cycle_test_id prevprev_id,
                  CAST(days_since_prev_cycle AS DOUBLE) days_prev
           FROM read_parquet('{HIST}')
           WHERE test_id IN (SELECT prev_id FROM l1 WHERE prev_id IS NOT NULL)),
    mpp AS (SELECT test_id prevprev_id, CAST(test_mileage AS DOUBLE) odo_prevprev
            FROM read_parquet('{SPINE}')
            WHERE test_id IN (SELECT prevprev_id FROM l2 WHERE prevprev_id IS NOT NULL)
              AND test_mileage IS NOT NULL)
    SELECT tgt.*, l2.days_prev, mpp.odo_prevprev
    FROM tgt LEFT JOIN l1 ON tgt.test_id = l1.test_id
             LEFT JOIN l2 ON l1.prev_id = l2.prev_id
             LEFT JOIN mpp ON l2.prevprev_id = mpp.prevprev_id
    """
    df = con.execute(sql).df()

    def clip_rate(r):
        return np.where(np.isfinite(r) & (r >= 0) & (r <= 50000), r, np.nan)

    df["honest_level"] = df["test_mileage"] - df["miles_since"]
    df["leaky_rate"] = clip_rate(df["miles_since"] * 365.0 / df["days_since"].replace(0, np.nan))
    df["honest_rate"] = clip_rate((df["honest_level"] - df["odo_prevprev"]) * 365.0
                                  / df["days_prev"].replace(0, np.nan))
    print(f"[{label}] n={len(df):,}  honest_rate defined: "
          f"{df['honest_rate'].notna().mean()*100:.1f}%", flush=True)
    return df


dev = build(DEV, "DEV")
oot = build(OOT, "OOT")

# cohort-mean honest rate fit on DEV only (>=10 per cohort), then normalise OOT
cm = (dev.dropna(subset=["honest_rate"]).groupby(["model_id", "age_band_ordinal"])
      .agg(cmean=("honest_rate", "mean"), n=("honest_rate", "size")).reset_index())
cm = cm[cm["n"] >= 10][["model_id", "age_band_ordinal", "cmean"]]
global_rate = float(dev["honest_rate"].mean())
oot = oot.merge(cm, on=["model_id", "age_band_ordinal"], how="left")
denom = oot["cmean"].fillna(global_rate).replace(0, np.nan)
oot["cohort_norm_rate"] = oot["honest_rate"] / denom

REPS = ["contemporaneous", "honest_level", "leaky_rate", "honest_rate", "cohort_norm_rate"]
oot["contemporaneous"] = oot["test_mileage"]

# fair comparison: common subset where every rep (incl. the 2-hop honest rate) is defined
common = oot.dropna(subset=REPS + ["age_band_ordinal", "y"]).copy()
y = common["y"].to_numpy()
age = common["age_band_ordinal"].to_numpy().astype(float).reshape(-1, 1)
print(f"\n=== OOT ablation | common-subset n={len(common):,} "
      f"({len(common)/len(oot)*100:.1f}% of OOT) | fail={y.mean():.4f} ===")
print(f"{'representation':22s} {'univ_AUC':>9s} {'AUC|age':>9s}")


def std(a):
    a = a.astype(float); s = a.std()
    return (a - a.mean()) / (s if s > 0 else 1.0)


age_auc = roc_auc_score(y, LogisticRegression(max_iter=200).fit(age, y).predict_proba(age)[:, 1])
for r in REPS:
    x = common[r].to_numpy().astype(float)
    uni = roc_auc_score(y, x); uni = max(uni, 1 - uni)
    X = np.column_stack([std(age.ravel()), std(x)])
    m = roc_auc_score(y, LogisticRegression(max_iter=200).fit(X, y).predict_proba(X)[:, 1])
    print(f"{r:22s} {uni:9.4f} {m:9.4f}")
print(f"{'age only (reference)':22s} {'-':>9s} {age_auc:9.4f}")
