"""v57 in-window history recompute — serving-frame features from the lake.

Rebuilds every history-derived training feature inside the observation
window [WINDOW_START, target_test_date) using the SERVING definitions from
``feature_engineering_v55`` (v57 Stage-1 rule: serving FE is canonical).
The serving path computes these same quantities from the capped DVSA API
history (see feature_engineering_v57); this module mirrors it over:

- ``cycle_first_with_history.parquet``  — per-test events (outcome,
  lagged advisory counts), 2019+,
- ``validation_samples/sampled_*.parquet`` — vehicle-panel spine
  (per-test odometer + result), used for prior-test odometers and as a
  secondary event source,
- ``cycle_history_extension_2024.parquet`` — OOT rows' own prev-cycle data,
- ``component_failures_all_years.parquet`` — per-test component-failure
  flags (brakes / tyres / suspension).

Serving frames replicated exactly (fe_v55 line references):
- prev_cycle_outcome_band       = latest prior's outcome      (:220-229)
- days_since_last_test/gap_band = gap BETWEEN the two latest priors (:232-238)
- test_mileage / anomaly flags  = latest prior's odometer, fallback to the
  second prior on >50k mi/yr anomaly (:241-270)
- annualized_mileage_v2 / usage_band_hybrid (:544-568)
- advisory totals               = sum over ALL in-window priors (:299)
- advisory_trend                = latest two priors' advisory counts (:302-312)
- V32 advisory/failure recency  = 0-based prior index, 999 sentinel (:327-431)
- temporal features             = latest prior's date, else target date (:205-212)
- days_late                     = target date vs latest prior's expiry,
  expiry approximated as prior date + 365 (:571-575)
- derived/negligence/mech-decay/severity formulas (:438-790)

Known Stage-1 residuals (documented, accepted by the directive):
- advisories at spine-only prior events are unknown -> treated as 0 with
  the lake's COALESCE convention (results-lake re-ingest lands in v58),
- lake 2024 indexing is partial (~9.2M rows), so late-2024 OOT priors may
  be missed; coverage is measured and written to the training manifest,
- lake events are cycle-first tests (no retests), so observed counts are
  cycle counts while serving counts raw API tests.
"""

from datetime import date
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from feature_engineering_v55 import get_gap_band, get_usage_band
from model_bundle import FIRST_MOT_AGE_YEARS, WINDOW_START

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
LAKE = PROJECT_ROOT / "cycle_first_with_history.parquet"
SPINE_GLOB = PROJECT_ROOT / "validation_samples" / "sampled_*.parquet"
HISTORY_EXTENSION_2024 = Path.home() / "autosafe_work/cycle_history_extension_2024.parquet"
COMPONENT_FAILURES = PROJECT_ROOT / "component_failures_all_years.parquet"

COMPONENTS = ("brakes", "tyres", "suspension")
_NEVER = 999  # serving sentinel: no prior advisory/failure observed


def _events_sql() -> str:
    """Unified, deduplicated per-test event table for the prior scan.

    Source priority lake > spine > extension (lake rows carry the lagged
    advisory columns; spine rows carry a real outcome; extension rows are
    OOT targets themselves and only matter as chain anchors).
    """
    return f"""
    SELECT * EXCLUDE (src_rank) FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY test_id ORDER BY src_rank) AS rn
        FROM (
            SELECT test_id, vehicle_id, CAST(test_date AS DATE) AS test_date,
                   outcome,
                   prev_advisory_total, prev_advisory_known,
                   prev_advisory_brakes, prev_advisory_tyres, prev_advisory_suspension,
                   1 AS src_rank
            FROM read_parquet('{LAKE}')
            WHERE test_date >= DATE '{WINDOW_START.isoformat()}'
            UNION ALL
            SELECT test_id, vehicle_id, CAST(test_date AS DATE) AS test_date,
                   CASE WHEN is_failure THEN 'FAIL' ELSE 'PASS' END AS outcome,
                   NULL, 0, NULL, NULL, NULL,
                   2 AS src_rank
            FROM read_parquet('{SPINE_GLOB}')
            WHERE test_date >= DATE '{WINDOW_START.isoformat()}'
            UNION ALL
            SELECT test_id, vehicle_id, CAST(test_date AS DATE) AS test_date,
                   NULL AS outcome,
                   prev_advisory_total, prev_advisory_known,
                   prev_advisory_brakes, prev_advisory_tyres, prev_advisory_suspension,
                   3 AS src_rank
            FROM read_parquet('{HISTORY_EXTENSION_2024}')
            WHERE test_date >= DATE '{WINDOW_START.isoformat()}'
        )
    ) WHERE rn = 1
    """


def _ranked_priors_sql(targets_table: str) -> str:
    """One row per (target, in-window prior), newest prior first (rnk=1).

    advisory counts AT each prior come from the lagged-column chain:
    advisories_at(prior rnk=k) = prev_advisory_* of the chain element one
    step newer (rnk=k-1), seeded with the target's own prev_advisory_*
    (supplied by the targets table from its h/e-join).
    """
    return f"""
    WITH events AS ({_events_sql()}),
    veh_events AS (
        SELECT e.* FROM events e
        SEMI JOIN {targets_table} t ON e.vehicle_id = t.vehicle_id
    ),
    ranked AS (
        SELECT
            t.test_id AS target_id,
            t.test_date AS target_date,
            e.test_id AS prior_test_id,
            e.test_date AS prior_date,
            e.outcome AS prior_outcome,
            e.prev_advisory_total AS prior_lag_adv_total,
            e.prev_advisory_known AS prior_lag_adv_known,
            e.prev_advisory_brakes AS prior_lag_adv_brakes,
            e.prev_advisory_tyres AS prior_lag_adv_tyres,
            e.prev_advisory_suspension AS prior_lag_adv_suspension,
            cf.fail_brakes, cf.fail_tyres, cf.fail_suspension,
            sp.test_mileage AS prior_odometer,
            ROW_NUMBER() OVER (
                PARTITION BY t.test_id ORDER BY e.test_date DESC, e.test_id DESC
            ) AS rnk
        FROM {targets_table} t
        JOIN veh_events e
          ON e.vehicle_id = t.vehicle_id
         AND e.test_date < CAST(t.test_date AS DATE)
        LEFT JOIN read_parquet('{COMPONENT_FAILURES}') cf ON cf.test_id = e.test_id
        LEFT JOIN (
            SELECT test_id, MAX(test_mileage) AS test_mileage
            FROM read_parquet('{SPINE_GLOB}') GROUP BY test_id
        ) sp ON sp.test_id = e.test_id
    )
    SELECT r.*,
        -- advisories AT this prior = lagged columns of the next-newer chain
        -- element; rnk=1 is seeded from the target's own prev_advisory_*
        LAG(prior_lag_adv_total)      OVER w AS adv_total_at_chain,
        LAG(prior_lag_adv_known)      OVER w AS adv_known_at_chain,
        LAG(prior_lag_adv_brakes)     OVER w AS adv_brakes_at_chain,
        LAG(prior_lag_adv_tyres)      OVER w AS adv_tyres_at_chain,
        LAG(prior_lag_adv_suspension) OVER w AS adv_suspension_at_chain
    FROM ranked r
    WINDOW w AS (PARTITION BY target_id ORDER BY rnk)
    """


def build_history_features(
    conn,
    targets: pd.DataFrame,
    *,
    label: str,
    window_start: date = WINDOW_START,
) -> pd.DataFrame:
    """Recompute serving-frame history features for every target row.

    ``targets`` must contain: test_id, vehicle_id, test_date,
    first_use_date, prev_advisory_total/known/brakes/tyres/suspension
    (the target's own lagged advisory columns from its dataset/h-join).

    Returns one row per test_id carrying the RENAMED v57 feature columns
    plus the five coverage features and a few aux columns (prefixed
    ``aux_``) used by downstream trainer blocks.
    """
    assert window_start == WINDOW_START, "window must match the bundle directive"
    need = {"test_id", "vehicle_id", "test_date", "first_use_date",
            "prev_advisory_total", "prev_advisory_known", "prev_advisory_brakes",
            "prev_advisory_tyres", "prev_advisory_suspension"}
    missing = need - set(targets.columns)
    if missing:
        raise ValueError(f"targets missing required columns: {sorted(missing)}")

    print(f"  [{label}] recompute: registering {len(targets):,} targets...")
    conn.register("v57_targets_df", targets[sorted(need)])
    conn.execute("CREATE OR REPLACE TEMP TABLE v57_targets AS SELECT * FROM v57_targets_df")

    ranked = conn.execute(_ranked_priors_sql("v57_targets")).fetchdf()
    print(f"  [{label}] recompute: {len(ranked):,} (target, prior) pairs")

    out = _aggregate(targets, ranked)
    coverage = {
        "targets": int(len(targets)),
        "targets_with_priors": int((out["n_prior_tests_observed"] > 0).sum()),
        "prior_pairs": int(len(ranked)),
        "priors_with_known_outcome": int(ranked["prior_outcome"].notna().sum()),
        "priors_with_odometer": int(ranked["prior_odometer"].notna().sum()),
    }
    print(f"  [{label}] recompute coverage: {coverage}")
    out.attrs["coverage"] = coverage
    return out


def _seed_chain(g_first_mask: np.ndarray, chain: np.ndarray, seed: np.ndarray) -> np.ndarray:
    """rnk=1 rows take the target's own lagged advisory value as 'at' value."""
    return np.where(g_first_mask, seed, chain)


def _aggregate(targets: pd.DataFrame, ranked: pd.DataFrame) -> pd.DataFrame:
    t = targets.set_index("test_id")
    t_date = pd.to_datetime(t["test_date"])

    if len(ranked):
        r = ranked.copy()
        r["prior_date"] = pd.to_datetime(r["prior_date"])
        r["target_date"] = pd.to_datetime(r["target_date"])
        first = r["rnk"] == 1
        seed_map = {
            "adv_total_at": "prev_advisory_total",
            "adv_known_at": "prev_advisory_known",
            "adv_brakes_at": "prev_advisory_brakes",
            "adv_tyres_at": "prev_advisory_tyres",
            "adv_suspension_at": "prev_advisory_suspension",
        }
        for out_col, seed_col in seed_map.items():
            seed = r["target_id"].map(t[seed_col])
            r[out_col] = _seed_chain(
                first.to_numpy(), r[f"{out_col}_chain"].to_numpy(), seed.to_numpy()
            )
            r[out_col] = pd.to_numeric(r[out_col], errors="coerce")
        # unknown advisory data -> lake COALESCE convention (0), Stage-1 residual
        adv_known = pd.to_numeric(r["adv_known_at"], errors="coerce").fillna(0) > 0
        for c in ("adv_total_at", "adv_brakes_at", "adv_tyres_at", "adv_suspension_at"):
            r[c] = np.where(adv_known, r[c].fillna(0), 0.0)
        for c in ("fail_brakes", "fail_tyres", "fail_suspension"):
            r[c] = pd.to_numeric(r[c], errors="coerce").fillna(0).astype(int)
        r["is_fail"] = (r["prior_outcome"] == "FAIL").astype(int)
        r["days_before_target"] = (r["target_date"] - r["prior_date"]).dt.days
        r["fail_365"] = (r["is_fail"].astype(bool) & (r["days_before_target"] <= 365)).astype(int)
        r["fail_730"] = (r["is_fail"].astype(bool) & (r["days_before_target"] <= 730)).astype(int)

        g = r.groupby("target_id", sort=False)
        agg = pd.DataFrame({
            "n_prior_tests_observed": g.size(),
            "n_prior_fails_observed": g["is_fail"].sum(),
            "fails_last_365d_observed": g["fail_365"].sum(),
            "fails_last_730d_observed": g["fail_730"].sum(),
            "prior_advisory_count_observed": g["adv_total_at"].sum(),
            "aux_prev_adv_brakes": g["adv_brakes_at"].sum(),
            "aux_prev_adv_tyres": g["adv_tyres_at"].sum(),
            "aux_prev_adv_suspension": g["adv_suspension_at"].sum(),
        })

        p1 = r[r["rnk"] == 1].set_index("target_id")
        p2 = r[r["rnk"] == 2].set_index("target_id")
        agg["p1_date"] = p1["prior_date"]
        agg["p1_outcome"] = p1["prior_outcome"]
        agg["p1_adv"] = p1["adv_total_at"]
        agg["p1_odo"] = p1["prior_odometer"]
        agg["p2_date"] = p2["prior_date"]
        agg["p2_adv"] = p2["adv_total_at"]
        agg["p2_odo"] = p2["prior_odometer"]

        # V32 recency families (serving frames, 0-based prior index)
        for comp in COMPONENTS:
            adv_pos = r[f"adv_{comp}_at"] > 0
            fail_pos = r[f"fail_{comp}"] > 0
            for kind, pos in (("advisory", adv_pos), ("failure", fail_pos)):
                rr = r[["target_id", "rnk"]].assign(hit=pos.to_numpy())
                gg = rr.groupby("target_id", sort=False)
                hit_rnk = rr[rr["hit"]].groupby("target_id")["rnk"].min()
                agg[f"aux_{kind}_first_hit_rnk_{comp}"] = hit_rnk
                # streak: consecutive hits from the newest prior down
                miss_rnk = rr[~rr["hit"]].groupby("target_id")["rnk"].min()
                n_all = gg.size()
                streak = (miss_rnk - 1).reindex(n_all.index)
                agg[f"aux_{kind}_streak_{comp}"] = streak.fillna(n_all).astype(int)
                agg[f"aux_{kind}_in_last_1_{comp}"] = (
                    rr[rr["rnk"] <= 1].groupby("target_id")["hit"].max().astype(int))
                agg[f"aux_{kind}_in_last_2_{comp}"] = (
                    rr[rr["rnk"] <= 2].groupby("target_id")["hit"].max().astype(int))

        # fail_rate_trend (serving fe:387-393): needs >=4 priors
        recent2 = r[r["rnk"] <= 2].groupby("target_id")["is_fail"].sum()
        older2 = r[(r["rnk"] >= 3) & (r["rnk"] <= 4)].groupby("target_id")["is_fail"].sum()
        agg["aux_fail_rate_trend"] = (recent2 - older2).where(
            agg["n_prior_tests_observed"] >= 4, 0)
    else:
        agg = pd.DataFrame(index=pd.Index([], name="target_id"))

    out = pd.DataFrame(index=t.index)
    out = out.join(agg)

    n = out["n_prior_tests_observed"] = (
        out.get("n_prior_tests_observed", pd.Series(0, index=out.index)).fillna(0).astype(int))
    out["n_prior_fails_observed"] = out.get(
        "n_prior_fails_observed", pd.Series(0, index=out.index)).fillna(0).astype(int)
    for c in ("fails_last_365d_observed", "fails_last_730d_observed",
              "prior_advisory_count_observed", "aux_prev_adv_brakes",
              "aux_prev_adv_tyres", "aux_prev_adv_suspension", "aux_fail_rate_trend"):
        out[c] = out.get(c, pd.Series(0, index=out.index)).fillna(0)

    # serving: raw ratio, 0.0 when no observed priors (fe:376-380)
    out["prior_fail_rate_observed"] = np.where(
        n > 0, out["n_prior_fails_observed"] / n.replace(0, 1), 0.0)

    # --- latest-prior / prior-pair frames -----------------------------------
    p1_date = pd.to_datetime(out.get("p1_date"))
    p2_date = pd.to_datetime(out.get("p2_date"))
    has_p1 = p1_date.notna()
    has_p2 = p2_date.notna()

    out["prev_cycle_outcome_band"] = np.select(
        [~has_p1,
         out.get("p1_outcome", pd.Series(index=out.index, dtype=object)) == "PASS",
         out.get("p1_outcome", pd.Series(index=out.index, dtype=object)) == "FAIL"],
        ["first_test", "pass", "fail"],
        default="unknown",
    )

    pair_gap = (p1_date - p2_date).dt.days
    out["days_since_last_test"] = np.where(has_p2, pair_gap.fillna(0), 0).astype(float)
    out["gap_band"] = [
        get_gap_band(int(d)) if h2 else "first_test"
        for d, h2 in zip(out["days_since_last_test"].fillna(0), has_p2)
    ]
    out["days_since_pass_ratio"] = out["days_since_last_test"] / 365.0

    # days_late: target date vs latest prior's expiry (~prior date + 365).
    # Serving reads the API expiry, which only exists when the latest prior
    # PASSED (failed/abandoned tests carry no expiry -> serving emits 0).
    own_gap = (t_date - p1_date).dt.days
    p1_passed = out.get("p1_outcome", pd.Series(index=out.index, dtype=object)) == "PASS"
    out["days_late"] = np.where(
        has_p1 & p1_passed, np.maximum(0, own_gap.fillna(0) - 365), 0).astype(float)
    out["mdps_score"] = np.where(out["days_late"] > 0, out["days_late"] / 365.0, 0.0)

    # advisory_trend (serving fe:302-312): latest two priors' advisory counts
    p1_adv = pd.to_numeric(out.get("p1_adv"), errors="coerce")
    p2_adv = pd.to_numeric(out.get("p2_adv"), errors="coerce")
    out["advisory_trend"] = np.select(
        [~has_p2, p1_adv > p2_adv, p1_adv < p2_adv],
        ["unknown", "increasing", "decreasing"],
        default="stable",
    )

    # temporal features (serving fe:205-212): latest prior, else target date
    eff_date = p1_date.fillna(t_date)
    out["test_month"] = eff_date.dt.month.astype(int)
    out["day_of_week"] = eff_date.dt.dayofweek.astype(int)
    out["is_winter_test"] = eff_date.dt.month.isin([10, 11, 12, 1, 2, 3]).astype(int)

    # --- mileage block (serving fe:241-270, :544-568) ------------------------
    odo1 = pd.to_numeric(out.get("p1_odo"), errors="coerce")
    odo2 = pd.to_numeric(out.get("p2_odo"), errors="coerce")
    diff = odo1 - odo2
    days = pair_gap
    annualized_pair = np.where(
        (days > 0) & (diff > 0), diff / days.replace(0, np.nan) * 365, np.nan)
    is_anomaly = has_p2 & odo1.notna() & odo2.notna() & (annualized_pair > 50000)
    out["mileage_anomaly_flag"] = is_anomaly.astype(int)
    out["test_mileage"] = np.select(
        [is_anomaly, odo1.notna()], [odo2.fillna(0), odo1.fillna(0)], default=0.0)
    out["has_prev_mileage"] = odo1.notna().astype(int)
    out["mileage_plausible_flag"] = np.where(
        odo1.notna(), (~is_anomaly).astype(int), 0)

    valid_pair = (~is_anomaly) & has_p2 & odo1.notna() & odo2.notna() & (days > 0) & (diff > 0)
    out["annualized_mileage_v2"] = np.where(valid_pair, annualized_pair, 10000.0)
    out["usage_band_hybrid"] = [
        get_usage_band(float(a)) if v else "average"
        for a, v in zip(out["annualized_mileage_v2"].fillna(10000.0), valid_pair)
    ]

    # --- V32 renamed recency features (serving fe:327-431) -------------------
    for comp in COMPONENTS:
        adv_cnt = out[f"aux_prev_adv_{comp}"]
        out[f"has_prior_advisory_{comp}_observed"] = (adv_cnt > 0).astype(int)
        hit = out.get(f"aux_advisory_first_hit_rnk_{comp}")
        out[f"tests_since_last_advisory_{comp}_observed"] = np.where(
            adv_cnt > 0, (hit - 1).fillna(_NEVER), _NEVER).astype(int)
        out[f"advisory_in_last_1_{comp}_observed"] = out.get(
            f"aux_advisory_in_last_1_{comp}", pd.Series(0, index=out.index)).fillna(0).astype(int)
        out[f"advisory_in_last_2_{comp}_observed"] = out.get(
            f"aux_advisory_in_last_2_{comp}", pd.Series(0, index=out.index)).fillna(0).astype(int)
        out[f"advisory_streak_len_{comp}_observed"] = np.where(
            adv_cnt > 0,
            out.get(f"aux_advisory_streak_{comp}", pd.Series(0, index=out.index)).fillna(0),
            0).astype(int)

        fail_hit = out.get(f"aux_failure_first_hit_rnk_{comp}")
        has_fail = fail_hit.notna() if fail_hit is not None else pd.Series(False, index=out.index)
        out[f"has_prior_failure_{comp}_observed"] = has_fail.astype(int)
        out[f"has_observed_failure_{comp}"] = has_fail.astype(int)
        out[f"tests_since_last_failure_{comp}_observed"] = np.where(
            has_fail, (fail_hit - 1).fillna(_NEVER) if fail_hit is not None else _NEVER,
            _NEVER).astype(int)
        out[f"failure_streak_len_{comp}_observed"] = np.where(
            has_fail,
            out.get(f"aux_failure_streak_{comp}", pd.Series(0, index=out.index)).fillna(0),
            0).astype(int)

    # miles_since_last_advisory estimate (serving fe:357-367)
    for comp in ("tyres", "suspension"):
        has_adv = out[f"has_prior_advisory_{comp}_observed"] == 1
        ok = has_adv & (out["has_prev_mileage"] == 1) & (out["test_mileage"] > 0)
        est = np.minimum(8000, out["test_mileage"] // np.maximum(n, 1))
        out[f"miles_since_last_advisory_{comp}_observed"] = np.where(ok, est, 0).astype(float)

    # --- derived serving formulas --------------------------------------------
    out["fail_rate_trend"] = out["aux_fail_rate_trend"].astype(float)
    out["recent_fail_intensity"] = out["fails_last_365d_observed"].astype(float)
    comp_counts = {c: out[f"aux_prev_adv_{c}"] for c in COMPONENTS}
    # steering has no lake column; serving counts it from defect text. The
    # lake only splits brakes/tyres/suspension -> steering contributes 0
    # (Stage-1 residual, lake re-ingest in v58).
    out["aux_prev_adv_steering"] = 0.0
    out["multi_system_advisory_count"] = sum(
        (cnt > 0).astype(int) for cnt in comp_counts.values())
    out["front_end_advisory_intensity"] = (
        out["aux_prev_adv_steering"] + out["aux_prev_adv_suspension"] + out["aux_prev_adv_tyres"])
    age_factor = np.log1p(n) / 3.0
    out["brake_system_stress"] = out["aux_prev_adv_brakes"] + age_factor
    out["commercial_wear_proxy"] = (
        np.log1p(out["annualized_mileage_v2"] / 10000)
        + np.maximum(0, out["days_since_last_test"] - 365) / 365.0
        + age_factor)

    out["has_advisory_history"] = (out["prior_advisory_count_observed"] > 0).astype(int)
    out["max_severity_score"] = np.select(
        [out["n_prior_fails_observed"] > 0, out["prior_advisory_count_observed"] > 0],
        [2, 1], default=0)
    out["severity_escalation_flag"] = (out["fail_rate_trend"] > 0).astype(int)

    # mech decay (serving fe:491-528); structure/steering have no lake split
    decay = {}
    for comp, col in (("brake", "aux_prev_adv_brakes"),
                      ("suspension", "aux_prev_adv_suspension")):
        decay[comp] = np.minimum(out[col] * 0.2, 1.0)
    decay["structure"] = pd.Series(0.0, index=out.index)
    decay["steering"] = pd.Series(0.0, index=out.index)
    out["mech_decay_brake"] = decay["brake"]
    out["mech_decay_suspension"] = decay["suspension"]
    out["mech_decay_structure"] = decay["structure"]
    out["mech_decay_steering"] = decay["steering"]
    out["mech_decay_index"] = np.maximum.reduce([d.to_numpy(dtype=float) for d in decay.values()])
    out["mech_decay_index_normalized"] = out["mech_decay_index"]  # serving fallback path
    out["mech_risk_driver"] = np.select(
        [out["mech_decay_index"] == 0,
         out["mech_decay_index"] == out["mech_decay_brake"],
         out["mech_decay_index"] == out["mech_decay_suspension"],
         out["mech_decay_index"] == out["mech_decay_steering"]],
        ["CLEAN", "BRAKE", "SUSP", "STEER"], default="STRUCT")

    # negligence (serving fe:765-783)
    ratio = np.where(
        (n > 0) & (out["prior_advisory_count_observed"] > 0),
        np.minimum(out["prior_advisory_count_observed"] / n.replace(0, 1), 1.0), 0.0)
    out["historic_negligence_ratio_smoothed"] = ratio
    out["negligence_band"] = np.select(
        [ratio == 0, ratio < 0.3, ratio < 0.6], ["clean", "low", "high"], default="chronic")
    out["raw_behavioral_count"] = out["prior_advisory_count_observed"]

    # --- coverage features (model_bundle definitions) ------------------------
    window_start_ts = pd.Timestamp(WINDOW_START)
    out["window_days_available"] = np.maximum(0, (t_date - window_start_ts).dt.days)
    first_observed = pd.to_datetime(
        ranked.groupby("target_id")["prior_date"].min()) if len(ranked) else pd.Series(dtype="datetime64[ns]")
    fo = out.index.to_series().map(first_observed)
    out["history_years_observed"] = np.where(
        fo.notna(), (t_date - pd.to_datetime(fo)).dt.days / 365.25, 0.0)
    out["has_prior_test_observed"] = (n > 0).astype(int)
    first_use = pd.to_datetime(t["first_use_date"], errors="coerce")
    first_mot_due = first_use + pd.DateOffset(years=FIRST_MOT_AGE_YEARS)
    out["has_left_truncated_history"] = (
        first_mot_due.notna() & (first_mot_due < window_start_ts)).astype(int)
    out["first_observed_test_is_not_true_first"] = (
        (out["has_prior_test_observed"] == 1) & (out["has_left_truncated_history"] == 1)
    ).astype(int)

    # rename the aux component-count columns to the contract names
    out["prev_adv_brakes"] = out["aux_prev_adv_brakes"].astype(float)
    out["prev_adv_tyres"] = out["aux_prev_adv_tyres"].astype(float)
    out["prev_adv_suspension"] = out["aux_prev_adv_suspension"].astype(float)
    out["prev_adv_steering"] = out["aux_prev_adv_steering"].astype(float)

    out.index.name = "test_id"
    return out.reset_index()
