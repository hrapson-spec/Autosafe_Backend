"""X4 — which of the 63 condition-substrate features ADD vs SUBTRACT signal?

Permutation importance on the FROZEN v57.0 model (committed model.cbm) +
frozen matrix (prepared_data.pkl). No refit, so no seed noise: the only
randomness is the shuffle, controlled by N_REPEATS + reported std.

perm_imp(f) = baseline_OOT_AUC - mean_over_repeats(AUC with column f shuffled)
  > +noise : feature carries UNIQUE signal (breaking it lowers AUC) -> ADDS
  ~ 0      : redundant given the rest, OR irrelevant (perm cannot distinguish)
  < -noise : OOT AUC IMPROVES when broken -> the model overfits it -> SUBTRACTS

Caveat (stated in output): permutation attributes shared signal to NEITHER of
two correlated features, so a ~0 here means "no UNIQUE contribution given the
others", not "no signal". Net family value is the X1-style drop-refit, not this.

Self-contained (no v57 module imports). Usage: python3.13 -u v57_rca_x4_perm.py
Appends tier2.X4 to models/v57/rca/rca_results.json.
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

# Same substrate definition as X1b (the 63-feature condition channel).
SUBSTRATE_SUBSTR = (
    "advisory", "_failure_", "has_observed_failure", "prev_adv_",
    "text_", "mech_decay", "mech_risk_driver", "mechanism_count",
    "max_severity", "severity_escalation", "has_corrosion", "has_wear",
    "has_leak", "has_damage", "dominant_mechanism", "raw_behavioral_count",
    "historic_negligence", "negligence_band", "front_end_advisory_intensity",
    "brake_system_stress", "advisory_cohort_delta", "multi_system",
    "prior_advisory_count_observed",
)


def family_of(f):
    if "_failure_" in f or f.startswith("has_observed_failure"):
        return "failure_recency"
    if "advisory" in f and ("_observed" in f or f in ("prior_advisory_count_observed",)):
        return "advisory_recency"
    if f.startswith("prev_adv_") or f in ("multi_system_advisory_count",
                                          "front_end_advisory_intensity",
                                          "brake_system_stress", "advisory_cohort_delta"):
        return "advisory_counts"
    if f.startswith("text_") or f in ("has_corrosion_history", "has_wear_history",
                                      "has_leak_history", "has_damage_history",
                                      "mechanism_count", "max_severity_score",
                                      "severity_escalation_flag", "dominant_mechanism",
                                      "has_advisory_history"):
        return "text_mining"
    if f.startswith("mech_decay") or f == "mech_risk_driver":
        return "mech_decay"
    if f.startswith("historic_negligence") or f in ("negligence_band", "raw_behavioral_count"):
        return "negligence"
    return "other"


def main():
    d = pickle.load(open(PKL, "rb"))
    X, y, cols, cats = d["X_test"].copy(), d["y_test"].to_numpy(), list(d["feature_cols"]), set(d["cat_features"])
    X = X[cols]  # exact model order
    m = CatBoostClassifier()
    m.load_model(str(MODEL))

    base = float(roc_auc_score(y, m.predict_proba(X)[:, 1]))
    print(f"baseline OOT AUC = {base:.4f} (n={len(y):,})", flush=True)

    substrate = [c for c in cols if any(s in c for s in SUBSTRATE_SUBSTR)]
    print(f"substrate features: {len(substrate)}", flush=True)

    rows = []
    for i, f in enumerate(substrate):
        col = X[f].to_numpy(copy=True)
        aucs = []
        for r in range(N_REPEATS):
            Xp = X  # mutate one column in place, restore after
            saved = X[f].to_numpy(copy=True)
            X[f] = RNG.permutation(col)
            aucs.append(float(roc_auc_score(y, m.predict_proba(X)[:, 1])))
            X[f] = saved
        mean_auc = float(np.mean(aucs))
        rows.append({
            "feature": f, "family": family_of(f),
            "is_categorical": f in cats,
            "perm_importance": round(base - mean_auc, 5),
            "perm_auc_mean": round(mean_auc, 5),
            "perm_auc_std": round(float(np.std(aucs)), 5),
        })
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(substrate)} done", flush=True)

    rows.sort(key=lambda x: x["perm_importance"], reverse=True)
    # noise band ~ max observed per-repeat std (signed call must exceed it)
    noise = round(max(r["perm_auc_std"] for r in rows), 5)
    adds = [r for r in rows if r["perm_importance"] > noise]
    subtracts = [r for r in rows if r["perm_importance"] < -noise]
    inert = [r for r in rows if abs(r["perm_importance"]) <= noise]

    fam = {}
    for r in rows:
        fam.setdefault(r["family"], 0.0)
        fam[r["family"]] += r["perm_importance"]
    fam = {k: round(v, 5) for k, v in sorted(fam.items(), key=lambda x: -x[1])}

    block = {
        "baseline_oot_auc": round(base, 5),
        "n_repeats": N_REPEATS,
        "noise_band_auc": noise,
        "n_features": len(substrate),
        "n_adds": len(adds), "n_inert": len(inert), "n_subtracts": len(subtracts),
        "family_perm_importance_sum": fam,
        "adds_top": adds[:12],
        "subtracts": subtracts,
        "per_feature_sorted": rows,
        "caveat": ("permutation gives UNIQUE marginal contribution; correlated "
                   "features share signal so both can read ~0 (= redundant, not "
                   "signal-free). Net family value = X1-style drop-refit, not this. "
                   "perm_importance>noise=ADDS unique; <-noise=SUBTRACTS (overfit)."),
    }
    results = json.loads(RESULTS.read_text())
    results.setdefault("tier2", {})["X4_substrate_permutation"] = block
    RESULTS.write_text(json.dumps(results, indent=2))

    print(f"\nnoise band ±{noise}")
    print(f"ADDS unique signal: {len(adds)} | inert/redundant: {len(inert)} | SUBTRACTS: {len(subtracts)}")
    print("\nfamily perm-importance sums (signed):")
    for k, v in fam.items():
        print(f"  {k:>18}: {v:+.4f}")
    print("\ntop adders:")
    for r in adds[:10]:
        print(f"  {r['feature']:>42} {r['perm_importance']:+.4f}  [{r['family']}]")
    print("\nsubtractors (OOT improves when broken):")
    for r in subtracts:
        print(f"  {r['feature']:>42} {r['perm_importance']:+.4f}  [{r['family']}]")
    if not subtracts:
        print("  (none exceed the noise band)")
    print(f"\n-> {RESULTS}")


if __name__ == "__main__":
    main()
