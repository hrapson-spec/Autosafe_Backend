"""X5 — of the 51 'no-unique-contribution' substrate features, how many are
REDUNDANT (signal covered by a correlated feature) vs INERT (no signal at all,
e.g. lake-truncated trained-dead)?

Permutation importance alone (X4) cannot tell these apart: both read ~0. Two
cheap fixed-model diagnostics that can:

  (1) near-constancy in the matrix — a trained-dead feature is ~constant
      (lake truncation made it near-zero everywhere) => INERT, no signal to
      be redundant with. A redundant-but-live feature VARIES but is covered.
  (2) grouped permutation of ALL 51 at once (no refit) — if jointly ~0, the
      51's signal is either absent OR fully held by the RETAINED features
      (priors/mileage/the 12 adders); if it drops, the 51 carry signal that
      is redundant only AMONG themselves.

Classification used:
  INERT      : modal_fraction >= 0.99 (effectively one value across the matrix)
  REDUNDANT  : varies (modal_fraction < 0.99) but individual perm imp ~0
  (cross-checked against the GF-17 trained-dead list).

Self-contained (committed model.cbm + frozen pkl). Appends tier2.X5.
Usage: python3.13 -u v57_rca_x5_redundant_vs_inert.py
"""

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score

REPO = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
PKL = Path.home() / "autosafe_work/v57_prepared_data.pkl"
MODEL = REPO / "models/v57/model.cbm"
RESULTS = REPO / "models/v57/rca/rca_results.json"
N_REPEATS = 3
RNG = np.random.default_rng(0)
CONST_THRESH = 0.99

# GF-17 trained-dead members (ledger b9 / RC-5 deg_train=True), v57 names.
KNOWN_TRAINED_DEAD = {
    "prev_adv_steering", "advisory_in_last_2_brakes_observed",
    "has_prior_advisory_suspension_observed",
    "tests_since_last_advisory_suspension_observed",
    "advisory_in_last_1_suspension_observed",
    "advisory_in_last_2_suspension_observed",
    "advisory_streak_len_suspension_observed",
    "miles_since_last_advisory_suspension_observed",
    "mech_decay_steering", "mech_decay_structure",
}


def modal_fraction(s: pd.Series) -> float:
    vc = s.value_counts(dropna=False)
    return float(vc.iloc[0] / len(s)) if len(vc) else 1.0


def main():
    d = pickle.load(open(PKL, "rb"))
    X, y, cols = d["X_test"].copy(), d["y_test"].to_numpy(), list(d["feature_cols"])
    X = X[cols]
    m = CatBoostClassifier()
    m.load_model(str(MODEL))
    base = float(roc_auc_score(y, m.predict_proba(X)[:, 1]))

    x4 = json.loads(RESULTS.read_text())["tier2"]["X4_substrate_permutation"]
    noise = x4["noise_band_auc"]
    zero_unique = [r["feature"] for r in x4["per_feature_sorted"]
                   if abs(r["perm_importance"]) <= noise]
    print(f"baseline {base:.4f} | {len(zero_unique)} no-unique-contribution features | noise ±{noise}", flush=True)

    # (1) near-constancy
    rows = []
    for f in zero_unique:
        mf = modal_fraction(X[f])
        rows.append({
            "feature": f, "modal_fraction": round(mf, 4),
            "n_distinct": int(X[f].nunique(dropna=False)),
            "classification": "inert_near_constant" if mf >= CONST_THRESH else "redundant_varies_but_covered",
            "in_gf17_trained_dead": f in KNOWN_TRAINED_DEAD,
        })
    inert = [r for r in rows if r["classification"].startswith("inert")]
    redundant = [r for r in rows if r["classification"].startswith("redundant")]

    # (2) grouped permutation of ALL the no-unique set at once
    grouped = []
    for _ in range(N_REPEATS):
        Xp = X.copy()
        for f in zero_unique:
            Xp[f] = RNG.permutation(X[f].to_numpy())
        grouped.append(float(roc_auc_score(y, m.predict_proba(Xp)[:, 1])))
    grouped_imp = round(base - float(np.mean(grouped)), 5)

    # grouped permutation of just the INERT subset and just the REDUNDANT subset
    def grouped_perm(feats):
        if not feats:
            return 0.0
        outs = []
        for _ in range(N_REPEATS):
            Xp = X.copy()
            for f in feats:
                Xp[f] = RNG.permutation(X[f].to_numpy())
            outs.append(float(roc_auc_score(y, m.predict_proba(Xp)[:, 1])))
        return round(base - float(np.mean(outs)), 5)

    inert_grp = grouped_perm([r["feature"] for r in inert])
    redun_grp = grouped_perm([r["feature"] for r in redundant])

    block = {
        "baseline_oot_auc": round(base, 5),
        "n_no_unique": len(zero_unique),
        "const_threshold": CONST_THRESH,
        "n_inert_near_constant": len(inert),
        "n_redundant_varies": len(redundant),
        "grouped_perm_all_no_unique": grouped_imp,
        "grouped_perm_inert_subset": inert_grp,
        "grouped_perm_redundant_subset": redun_grp,
        "inert_features": [r["feature"] for r in inert],
        "redundant_features": [r["feature"] for r in redundant],
        "detail": rows,
        "reading": ("grouped_perm_all ~0 => the no-unique set adds nothing even "
                    "jointly to the fixed model (signal absent or fully held by "
                    "retained features). near-constant members are INERT (no "
                    "signal); varying members are REDUNDANT (covered)."),
    }
    res = json.loads(RESULTS.read_text())
    res.setdefault("tier2", {})["X5_redundant_vs_inert"] = block
    RESULTS.write_text(json.dumps(res, indent=2))

    print(f"\nof {len(zero_unique)} no-unique-contribution features:")
    print(f"  INERT (near-constant, modal_frac>={CONST_THRESH}): {len(inert)}")
    print(f"  REDUNDANT (varies but covered):                    {len(redundant)}")
    print(f"  of the inert, {sum(r['in_gf17_trained_dead'] for r in inert)} are on the GF-17 trained-dead list")
    print(f"\ngrouped permutation (joint as-used contribution, no refit):")
    print(f"  all {len(zero_unique)} together: {grouped_imp:+.4f}")
    print(f"  inert subset:      {inert_grp:+.4f}")
    print(f"  redundant subset:  {redun_grp:+.4f}")
    print(f"\ninert (near-constant) examples:")
    for r in sorted(inert, key=lambda x: -x["modal_fraction"])[:12]:
        print(f"  {r['feature']:>44} modal_frac={r['modal_fraction']:.3f} distinct={r['n_distinct']}")
    print(f"\nredundant (varies) examples:")
    for r in sorted(redundant, key=lambda x: x["modal_fraction"])[:12]:
        print(f"  {r['feature']:>44} modal_frac={r['modal_fraction']:.3f} distinct={r['n_distinct']}")
    print(f"\n-> {RESULTS}")


if __name__ == "__main__":
    main()
