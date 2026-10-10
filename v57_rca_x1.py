"""X1 — is the advisory/condition substrate inert in the v57.0 training matrix?

Self-contained: loads ONLY the frozen v57.0 matrix
(~/autosafe_work/v57_prepared_data.pkl, 105 features incl.
window_days_available) + catboost. Imports NO v57 modules, so the in-flight
v57.1 working-tree edits cannot contaminate the v57.0 diagnosis.

Refits (1 seed @ the bundle's 1233 iters, same PARAMS/weights as the trainer):
  baseline           — all 105 features (must reproduce ~0.7133)
  X1a drop TDSL       — strict GF-17 TRAINED_DEAD_SERVED_LIVE members only
  X1b drop substrate  — full advisory + text + mech-decay condition channel

A null ΔAUC proves ONLY matrix-inertness (the model does not use these
columns), NOT that a live/re-ingested substrate would add signal — that is a
separate v58 hypothesis this cannot settle.

Outputs: merges a tier2.X1 block into models/v57/rca/rca_results.json.
Usage: python3.13 -u v57_rca_x1.py
"""

import json
import pickle
from pathlib import Path

import numpy as np
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import roc_auc_score

PKL = Path.home() / "autosafe_work/v57_prepared_data.pkl"
RESULTS = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe/models/v57/rca/rca_results.json")
ITERS = 1233
PARAMS = dict(iterations=ITERS, learning_rate=0.02, depth=6, l2_leaf_reg=4,
              border_count=128, random_strength=1.0, bagging_temperature=0.5,
              eval_metric="AUC")

# Strict GF-17 TRAINED_DEAD_SERVED_LIVE members (deg_train=True rows), mapped
# to v57 *_observed names. These are PROVEN near-constant in the v55/v57
# training matrices (ledger RC-5/b9).
TDSL = [
    "prev_adv_steering",
    "advisory_in_last_2_brakes_observed",
    "has_prior_advisory_suspension_observed",
    "tests_since_last_advisory_suspension_observed",
    "advisory_in_last_1_suspension_observed",
    "advisory_in_last_2_suspension_observed",
    "advisory_streak_len_suspension_observed",
    "miles_since_last_advisory_suspension_observed",
    "mech_decay_steering",
    "mech_decay_structure",
]

# Full condition substrate: advisory recency/counts + failure recency + text
# mining + mech decay. Pattern-matched, then intersected with the matrix.
SUBSTRATE_SUBSTR = (
    "advisory", "_failure_", "has_observed_failure", "prev_adv_",
    "text_", "mech_decay", "mech_risk_driver", "mechanism_count",
    "max_severity", "severity_escalation", "has_corrosion", "has_wear",
    "has_leak", "has_damage", "dominant_mechanism", "raw_behavioral_count",
    "historic_negligence", "negligence_band", "front_end_advisory_intensity",
    "brake_system_stress", "advisory_cohort_delta", "multi_system",
    "prior_advisory_count_observed",
)


def fit_auc(X_tr, y_tr, w, X_te, y_te, cats, label):
    cat_idx = [X_tr.columns.get_loc(c) for c in cats if c in X_tr.columns]
    m = CatBoostClassifier(random_seed=0, verbose=0, cat_features=cat_idx, **PARAMS)
    m.fit(Pool(X_tr, y_tr, cat_features=cat_idx, weight=w))
    auc = float(roc_auc_score(y_te, m.predict_proba(X_te)[:, 1]))
    print(f"  {label}: AUC={auc:.4f}  ({X_tr.shape[1]} feats)", flush=True)
    return auc, m


def main():
    d = pickle.load(open(PKL, "rb"))
    Xtr, ytr, Xte, yte = d["X_train"], d["y_train"], d["X_test"], d["y_test"]
    w, cats, cols = d["train_weights"], d["cat_features"], list(d["feature_cols"])
    print(f"matrix: train {Xtr.shape} / test {Xte.shape}; base rate {yte.mean():.4f}", flush=True)

    base_auc, base_model = fit_auc(Xtr, ytr, w, Xte, yte, cats, "baseline (105)")

    imp = dict(zip(base_model.feature_names_, base_model.get_feature_importance()))

    x1a = [c for c in TDSL if c in cols]
    x1b = [c for c in cols if any(s in c for s in SUBSTRATE_SUBSTR)]
    print(f"\n  X1a TDSL drop set ({len(x1a)}): {x1a}", flush=True)
    print(f"  X1b substrate drop set ({len(x1b)}) total importance "
          f"{sum(imp.get(c, 0) for c in x1b):.2f}%", flush=True)

    keep_a = [c for c in cols if c not in set(x1a)]
    keep_b = [c for c in cols if c not in set(x1b)]
    cats_a = [c for c in cats if c in keep_a]
    cats_b = [c for c in cats if c in keep_b]

    auc_a, _ = fit_auc(Xtr[keep_a], ytr, w, Xte[keep_a], yte, cats_a, f"X1a drop TDSL ({len(keep_a)})")
    auc_b, _ = fit_auc(Xtr[keep_b], ytr, w, Xte[keep_b], yte, cats_b, f"X1b drop substrate ({len(keep_b)})")

    block = {
        "baseline_auc": base_auc,
        "X1a_drop_tdsl": {
            "dropped": x1a, "n_dropped": len(x1a),
            "auc": auc_a, "delta": round(auc_a - base_auc, 4),
            "dropped_total_importance_pct": round(sum(imp.get(c, 0) for c in x1a), 3),
        },
        "X1b_drop_substrate": {
            "dropped": x1b, "n_dropped": len(x1b),
            "auc": auc_b, "delta": round(auc_b - base_auc, 4),
            "dropped_total_importance_pct": round(sum(imp.get(c, 0) for c in x1b), 3),
        },
        "substrate_member_importance": {c: round(imp.get(c, 0), 4) for c in x1b},
        "claim": ("null delta => substrate is INERT in the v57.0 training matrix "
                  "(model does not use it); does NOT establish that a live "
                  "substrate would add AUC (separate v58 hypothesis)."),
    }

    results = json.loads(RESULTS.read_text())
    results.setdefault("tier2", {})["X1_substrate_inertness"] = block
    RESULTS.write_text(json.dumps(results, indent=2))
    print(f"\nX1 done. baseline {base_auc:.4f} | X1a {auc_a:.4f} ({block['X1a_drop_tdsl']['delta']:+.4f}) "
          f"| X1b {auc_b:.4f} ({block['X1b_drop_substrate']['delta']:+.4f})")
    print(f"  substrate carried {block['X1b_drop_substrate']['dropped_total_importance_pct']}% importance")
    print(f"-> {RESULTS}")


if __name__ == "__main__":
    main()
