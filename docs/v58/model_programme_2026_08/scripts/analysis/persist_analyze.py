#!/usr/bin/env python3
"""PERSIST Phase 2 — the confirmatory estimand and its cohort-A sensitivity.

Per PREREG_PERSIST_2026_08_16.md (sha 424dfdd4af84ea56...) §6.3, §9.1, §10.3.
Licensed only by out/PERSIST_CORRECTNESS_GATE.json all_pass == true; asserted at start.

Estimand (§6.3), NOT the mean of log-odds:
    population-standardised average persistence effect on the RISK scale,
    by g-computation over the observed system mix.

Partial pooling (§6.3, D4): system-specific baseline risk AND system-specific persistence
effects. Implemented in two transparent stages because statsmodels is unavailable:
  stage 1  per-system logistic with controls  -> beta_s, se_s
  stage 2  DerSimonian-Laird random effects   -> tau, shrunk beta_s
  stage 3  g-computation with the SHRUNK betas -> standardised risk difference

tau is estimated, reported, and propagated through the bootstrap. No closed-form SE
inflation is applied anywhere (§9.1).

Uncertainty: vehicle-clustered bootstrap, shared draws, whole pipeline refit per replicate.
Cohort A is NESTED in cohort B, so the §10.3 contrast D = dA - 0.5*dB is computed
jointly WITHIN each replicate, never from two independent intervals.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

PROG = Path(__file__).resolve().parents[2]
SYS_ORDER = None  # set from the ontology


# ----------------------------------------------------------------- frame construction
def build_frame(con, frame, labels, state, idx):
    """One row per (target, advised system) with controls and the same-system outcome.

    Prior-side state comes from the episode build. The outcome is the SAME-SYSTEM
    major/dangerous count at the target MOT, via the frozen ontology bridge.
    """
    bridge = idx["ontology_bridge"]["systems"]
    # same-system outcome expression per system (many-to-one: sums where required)
    cases = " ".join(
        f"WHEN '{s}' THEN " + " + ".join(c["column"] for c in cols)
        for s, cols in bridge.items())
    outcome_sql = f"CASE p.sys {cases} ELSE NULL END"
    # all-nine-systems M/D, so 8A's "failure OUTSIDE that system" = all - same.
    # Excluded buckets (identification, not_tested) are deliberately NOT in this sum:
    # they are not vehicle systems and §6.4 bars them from system-level outcomes.
    all_sys_cols = sorted({c["column"] for cols in bridge.values() for c in cols})
    all_sys_sql = " + ".join(all_sys_cols)

    con.execute(f"""
        CREATE OR REPLACE TABLE frame AS
        WITH s AS (
            SELECT tgt_id, ep_rank, ep_date, sys, state, graded_regime,
                   n_adv, n_min, n_md, n_items
            FROM read_parquet('{state}')
        ),
        pair AS (      -- t = ep_rank 1, t-1 = ep_rank 2, both graded (§ deviation 6)
            SELECT a.tgt_id, a.sys, a.ep_date AS t_date,
                   a.state AS s_t, b.state AS s_t1,
                   a.n_adv AS n_adv_sys_t, a.n_items AS n_items_sys_t
            FROM s a JOIN s b USING (tgt_id, sys)
            WHERE a.ep_rank = 1 AND b.ep_rank = 2
              AND a.graded_regime AND b.graded_regime
        ),
        burden AS (    -- whole-vehicle current condition at t, across all systems
            SELECT tgt_id,
                   sum(n_adv) AS tot_adv_t,
                   sum(CASE WHEN state='A' THEN 1 ELSE 0 END) AS n_advised_systems_t,
                   sum(n_min) AS tot_min_t,
                   sum(n_md)  AS tot_md_t
            FROM s WHERE ep_rank = 1 GROUP BY 1
        ),
        depth AS (
            SELECT tgt_id, max(ep_rank) AS n_episodes,
                   min(ep_date) AS first_ep_date
            FROM s GROUP BY 1
        ),
        hist AS (      -- history depth and prior failure burden, episodes 2+
            SELECT tgt_id,
                   sum(CASE WHEN state='F' THEN 1 ELSE 0 END) AS n_prior_fail_sysdays,
                   sum(n_md) AS prior_md_items
            FROM s WHERE ep_rank >= 2 GROUP BY 1
        )
        SELECT p.tgt_id, p.sys, p.s_t, p.s_t1,
               CASE WHEN p.s_t1 = 'A' THEN 1 ELSE 0 END AS persistent,
               p.n_adv_sys_t, p.n_items_sys_t,
               b.tot_adv_t, b.n_advised_systems_t, b.tot_min_t, b.tot_md_t,
               d.n_episodes, h.n_prior_fail_sysdays, h.prior_md_items,
               l.tgt_date, l.vehicle_id,
               date_diff('day', p.t_date, l.tgt_date) AS interval_days,
               ({outcome_sql}) AS y_same_system_md,
               ({all_sys_sql}) AS y_all_systems_md,
               ({all_sys_sql}) - ({outcome_sql}) AS y_other_system_md,
               l.n_major_or_dangerous AS y_md_total,
               CASE WHEN l.n_major_or_dangerous >= 3 THEN 1 ELSE 0 END AS y_b3
        FROM pair p
        JOIN read_parquet('{labels}') l ON l.test_id = p.tgt_id
        JOIN burden b USING (tgt_id)
        JOIN depth d  USING (tgt_id)
        LEFT JOIN hist h USING (tgt_id)
        WHERE p.s_t = 'A'                       -- the currently-advised population
          AND p.s_t1 IN ('A','C')               -- A->A vs genuinely new C->A
    """)
    n = con.execute("SELECT count(*) FROM frame").fetchone()[0]
    print(f"  cohort B frame rows: {n:,}", flush=True)
    return n


CONTROLS = ["n_adv_sys_t", "n_items_sys_t", "tot_adv_t", "n_advised_systems_t",
            "tot_min_t", "tot_md_t", "n_episodes", "n_prior_fail_sysdays",
            "prior_md_items", "interval_days"]


def design(df):
    """Controls + non-linear (binned, not assumed-linear) terms for interval and depth.

    ⚠ Zero-variance columns are DROPPED, not standardised to a column of zeros. A
    constant column makes the observed-information matrix singular, which made
    fit_system() return None and silently drop that system from the pooled estimate.
    Found by the synthetic planted-effect test, which is why that test exists.
    """
    X = df[[c for c in CONTROLS if c in df.columns]].astype(float).fillna(0.0).to_numpy()
    extra = []
    for col in ("interval_days", "n_episodes"):
        if col not in df.columns:
            continue
        v = df[col].astype(float).fillna(0.0).to_numpy()
        for q in np.unique(np.quantile(v, [.2, .4, .6, .8])):
            b = (v > q).astype(float)
            if 0 < b.sum() < len(b):          # skip degenerate splits
                extra.append(b)
    if extra:
        X = np.hstack([X, np.column_stack(extra)])
    sd = X.std(0)
    keep = sd > 1e-12                          # drop constants outright
    if not keep.any():
        return np.empty((len(X), 0))
    X = X[:, keep]
    return (X - X.mean(0)) / X.std(0)


def fit_system(df_s, min_n=40, min_pos=10):
    """Per-system logistic: returns (beta, se) for the persistence term, or None."""
    y = df_s["y"].to_numpy()
    if len(df_s) < min_n or y.sum() < min_pos or y.sum() == len(y):
        return None
    p = df_s["persistent"].to_numpy(float)
    if p.sum() < 5 or p.sum() > len(p) - 5:
        return None
    X = np.hstack([p.reshape(-1, 1), design(df_s)])
    m = LogisticRegression(C=np.inf, max_iter=400, solver="lbfgs")
    try:
        m.fit(X, y)
    except Exception:
        return None
    beta = float(m.coef_[0][0])
    # observed-information SE for the persistence coefficient. pinv, not inv: a
    # near-singular design must degrade to a wide SE (which shrinkage then handles),
    # never to a dropped system.
    eta = m.decision_function(X)
    w = np.clip(1 / (1 + np.exp(-eta)), 1e-9, 1 - 1e-9)
    w = w * (1 - w)
    Xd = np.hstack([np.ones((len(X), 1)), X])
    cov = np.linalg.pinv(Xd.T @ (Xd * w[:, None]))
    se = float(np.sqrt(max(cov[1, 1], 0.0)))
    if not np.isfinite(beta) or not np.isfinite(se) or se <= 0:
        return None
    return beta, se, m, X


def dersimonian_laird(betas, ses):
    """Random-effects pooling -> (pooled, tau2, shrunk betas)."""
    b, s = np.asarray(betas, float), np.asarray(ses, float)
    w = 1 / s**2
    fixed = (w * b).sum() / w.sum()
    Q = (w * (b - fixed) ** 2).sum()
    k = len(b)
    c = w.sum() - (w**2).sum() / w.sum()
    tau2 = max(0.0, (Q - (k - 1)) / c) if c > 0 else 0.0
    wr = 1 / (s**2 + tau2)
    pooled = (wr * b).sum() / wr.sum()
    shrink = tau2 / (tau2 + s**2)            # 1 = keep own estimate, 0 = full pooling
    shrunk = pooled + shrink * (b - pooled)
    return float(pooled), float(tau2), shrunk, shrink


def standardised_effect(df, systems):
    """Stage 1-3. Returns dict with the risk-scale standardised effect and tau."""
    fits, order = {}, []
    for s in systems:
        d = df[df.sys == s]
        r = fit_system(d)
        if r is not None:
            fits[s] = r
            order.append(s)
    if not order:
        return None
    betas = [fits[s][0] for s in order]
    ses = [fits[s][1] for s in order]
    pooled, tau2, shrunk, shrink = dersimonian_laird(betas, ses)

    # stage 3: g-computation with the SHRUNK persistence coefficient
    num, den = 0.0, 0
    per_sys = {}
    for s, bs in zip(order, shrunk):
        _, se_s, m, X = fits[s]
        b_raw = fits[s][0]
        Xa, Xc = X.copy(), X.copy()
        # substitute the shrunk coefficient: eta = eta_fitted + (b_shrunk - b_raw)*p
        eta = m.decision_function(X)
        p = X[:, 0]
        adj = eta + (bs - b_raw) * p
        eta_a = adj + (1 - p) * bs          # everyone persistent
        eta_c = adj - p * bs                # everyone newly advised
        pa = 1 / (1 + np.exp(-eta_a))
        pc = 1 / (1 + np.exp(-eta_c))
        num += float((pa - pc).sum())
        den += len(X)
        per_sys[s] = {"n": int(len(X)), "beta_raw": float(b_raw), "se": float(se_s),
                      "beta_shrunk": float(bs),
                      "shrinkage_weight": float(shrink[order.index(s)]),
                      "risk_diff_pp": float((pa - pc).mean() * 100)}
    return {"standardised_risk_diff_pp": num / den * 100,
            "tau": float(np.sqrt(tau2)), "tau2": float(tau2),
            "pooled_log_odds": pooled,
            "n_systems_fitted": len(order), "systems_fitted": order,
            "per_system": per_sys, "n_rows": int(den)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", default="train_flat4y")
    ap.add_argument("--reps", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260816)
    a = ap.parse_args()

    g = json.loads((PROG / "out/PERSIST_CORRECTNESS_GATE.json").read_text())
    if not g.get("all_pass"):
        print("FATAL: correctness gate has not passed; outcome computation is barred.",
              file=sys.stderr)
        sys.exit(2)

    tax = json.loads((PROG / "out/ADVSTRUCT_TAXONOMY.json").read_text())
    idx = json.loads((PROG / "out/PERSIST_SECT_INDEX.json").read_text())
    systems = tax["systems"]
    labels = {"eval2024": "out/TARGET_SEVERITY_LABELS.parquet",
              "train_flat4y": "out/TRAIN_SEVERITY_LABELS.parquet"}[a.frame]

    con = duckdb.connect()
    con.execute("SET memory_limit='3GB'"); con.execute("SET threads=4")
    print(f"=== {a.frame} ===", flush=True)
    build_frame(con, a.frame, str(PROG / labels),
                str(PROG / f"out/persist/state_{a.frame}.parquet"), idx)

    df = con.execute("SELECT * FROM frame").df()
    df["y"] = (df.y_same_system_md.fillna(0) > 0).astype(int)
    # Cohort A: clean room — exactly one advisory in total, that system, no minors, no M/D
    df["cohortA"] = ((df.tot_adv_t == 1) & (df.n_adv_sys_t == 1)
                     & (df.tot_min_t == 0) & (df.tot_md_t == 0)).astype(int)

    print(f"  cohort B rows {len(df):,} | cohort A rows {int(df.cohortA.sum()):,}", flush=True)
    print(f"  A->A {int(df.persistent.sum()):,} | C->A {int((1-df.persistent).sum()):,}",
          flush=True)

    t0 = time.time()
    B = standardised_effect(df, systems)
    A = standardised_effect(df[df.cohortA == 1], systems)
    print(f"  point estimates in {time.time()-t0:.1f}s", flush=True)
    if B is None:
        print("FATAL: cohort B did not fit", file=sys.stderr); sys.exit(2)
    print(f"  cohort B standardised risk diff = {B['standardised_risk_diff_pp']:+.4f} pp"
          f"  tau={B['tau']:.4f}  systems fitted {B['n_systems_fitted']}/9", flush=True)
    if A:
        print(f"  cohort A standardised risk diff = {A['standardised_risk_diff_pp']:+.4f} pp"
              f"  tau={A['tau']:.4f}  systems fitted {A['n_systems_fitted']}/9", flush=True)

    # ---- vehicle-clustered bootstrap, shared draws, joint A/B per replicate -----------
    # Project to ONLY the columns the estimator reads before resampling. The full frame
    # carries ~25 mixed-dtype columns (dates, strings, unused outcomes); slicing all of
    # them cost ~15s of each 20.4s replicate. Estimator maths is untouched — the point
    # estimate is asserted identical below.
    need = ["sys", "y", "persistent", "cohortA"] + [c for c in CONTROLS if c in df.columns]
    slim = df[need].copy()
    slim["sys"] = slim["sys"].astype("category")
    chk = standardised_effect(slim, systems)
    assert abs(chk["standardised_risk_diff_pp"] - B["standardised_risk_diff_pp"]) < 1e-9, (
        f"projection changed the estimate: {chk['standardised_risk_diff_pp']} "
        f"vs {B['standardised_risk_diff_pp']}")
    print(f"  column projection verified identical "
          f"({chk['standardised_risk_diff_pp']:+.6f} pp)", flush=True)

    rng = np.random.default_rng(a.seed)
    veh = df.vehicle_id.to_numpy()
    uniq, inv = np.unique(veh, return_inverse=True)
    order = np.argsort(inv, kind="stable")
    starts = np.searchsorted(inv[order], np.arange(len(uniq)))
    counts = np.diff(np.append(starts, len(order)))
    dB, dA, dD = [], [], []
    t0 = time.time()
    for r in range(a.reps):
        pick = rng.integers(0, len(uniq), len(uniq))
        idxs = order[np.repeat(starts[pick], counts[pick])
                     + np.arange(counts[pick].sum())
                     - np.repeat(np.cumsum(counts[pick]) - counts[pick], counts[pick])]
        d = slim.take(idxs)
        rb = standardised_effect(d, systems)
        ra = standardised_effect(d[d.cohortA == 1], systems)
        if rb is None:
            continue
        dB.append(rb["standardised_risk_diff_pp"])
        if ra is not None:
            dA.append(ra["standardised_risk_diff_pp"])
            dD.append(ra["standardised_risk_diff_pp"] - 0.5 * rb["standardised_risk_diff_pp"])
        if (r + 1) % 25 == 0:
            print(f"    boot {r+1}/{a.reps}  ({time.time()-t0:.0f}s)", flush=True)

    def ci(v):
        v = np.asarray(v, float)
        return [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] if len(v) else None

    out = {"artifact": f"PERSIST Phase 2 — {a.frame}",
           "prereg_sha256_16": "424dfdd4af84ea56",
           "licensed_by": "out/PERSIST_CORRECTNESS_GATE.json all_pass=true",
           "frame": a.frame, "n_rows_cohortB": int(len(df)),
           "n_rows_cohortA": int(df.cohortA.sum()),
           "n_AA": int(df.persistent.sum()), "n_CA": int((1 - df.persistent).sum()),
           "cohortB": B, "cohortA": A,
           "bootstrap": {"reps_requested": a.reps, "reps_ok_B": len(dB), "reps_ok_A": len(dA),
                         "seed": a.seed, "cluster": "vehicle_id", "shared_draws": True,
                         "ci95_cohortB_pp": ci(dB), "ci95_cohortA_pp": ci(dA),
                         "D_definition": "dA - 0.5*dB, computed JOINTLY within replicate",
                         "D_point": (A["standardised_risk_diff_pp"]
                                     - 0.5 * B["standardised_risk_diff_pp"]) if A else None,
                         "ci95_D_pp": ci(dD)}}
    dest = PROG / f"out/PERSIST_PHASE2_{a.frame}.json"
    dest.write_text(json.dumps(out, indent=1))
    print(f"\ncohort B {B['standardised_risk_diff_pp']:+.4f} pp  CI {ci(dB)}")
    if A:
        print(f"cohort A {A['standardised_risk_diff_pp']:+.4f} pp  CI {ci(dA)}")
        print(f"D = dA - 0.5*dB = {out['bootstrap']['D_point']:+.4f} pp  CI {ci(dD)}")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
