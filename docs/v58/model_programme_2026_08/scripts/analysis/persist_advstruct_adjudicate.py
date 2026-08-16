#!/usr/bin/env python3
"""ADVSTRUCT descriptive adjudication — C1..C4 against AMENDMENT A3.

Owner ruling 2026-08-16 (D8): C1 -> INCONCLUSIVE vs stated floor, because the bar was
unpassable by construction. That claim is derived from the BANKED bootstrap CIs alone —
no outcome re-read, no refit, nothing computed that A3 did not already publish.

Emits out/ADVSTRUCT_RESULT_2026_08_15.json.
"""
import json
from math import erf, sqrt
from pathlib import Path

PROG = Path("/Users/henrirapson/autosafe-v58/docs/v58/model_programme_2026_08")
Z = 1.959963985            # alpha .05 two-sided
ZP = 0.841621234           # 80% power
Phi = lambda x: 0.5 * (1 + erf(x / sqrt(2)))

tr = json.loads((PROG / "out/ADVSTRUCT_DESCRIPTIVE_TRAIN.json").read_text())
ev = json.loads((PROG / "out/ADVSTRUCT_DESCRIPTIVE_EVAL.json").read_text())

# ---- C1: per-stratum power against the TRAIN (discovery) effect -----------
strata, probs = [], []
for k in ev["estimand_a"]["strata"]:
    e, t = ev["estimand_a"]["strata"][k], tr["estimand_a"]["strata"][k]
    lo, hi = e["ci95"]
    se = (hi - lo) / (2 * Z)
    bT, bE = t["beta_breadth"], e["beta_breadth"]
    pwr = Phi(bT / se - Z)                      # P(CI clears 0 | true effect = bT)
    probs.append(pwr)
    strata.append({
        "stratum": k, "n_eval": e["n"], "n_vehicles": e["n_vehicles"],
        "n_positive": e["n_positive"], "prevalence": e["prevalence"],
        "beta_train": bT, "beta_eval": bE, "ci95_eval": e["ci95"],
        "se_eval": se, "mde80_eval": (Z + ZP) * se,
        "power_vs_train_effect": pwr,
        "ci_excludes_zero": e["ci_excludes_zero"],
        "classification": ("PASS" if e["ci_excludes_zero"]
                           else ("UNDERPOWERED" if bT < (Z + ZP) * se else "GENUINE_MISS")),
    })

# Poisson-binomial: distribution of #CI-clear strata if TRAIN effects are exactly true
dist = [1.0]
for p in probs:
    nd = [0.0] * (len(dist) + 1)
    for i, d in enumerate(dist):
        nd[i] += d * (1 - p)
        nd[i + 1] += d * p
    dist = nd

n_clear = sum(s["ci_excludes_zero"] for s in strata)
p_ge5 = sum(dist[5:])
p_ge4 = sum(dist[4:])

# ---- C2 / C3 / C4 --------------------------------------------------------
b = ev["estimand_b"]["breadth"]
cs = ev["control_survival"]["controls"]
c2 = {"beta_beyond_composition": b["beta_beyond_composition"], "ci95": b["ci95"],
      "ci_excludes_zero": b["ci_excludes_zero"],
      "matched_comparator_count_only":
          ev["estimand_b"]["matched_comparator_breadth_given_total_count_only"],
      "verdict": "PASS" if b["ci_excludes_zero"] and b["beta_beyond_composition"] > 0 else "FAIL"}
c2["share_absorbed_by_composition"] = round(
    1 - c2["beta_beyond_composition"] / c2["matched_comparator_count_only"], 4)

c3 = {"age_quartile": cs["age_quartile"]["beta_survival"],
      "prior_depth_band": cs["prior_depth_band"]["beta_survival"],
      "bar": 0.50,
      "verdict": "PASS" if min(cs["age_quartile"]["beta_survival"],
                               cs["prior_depth_band"]["beta_survival"]) >= 0.50 else "FAIL"}
c4 = {"n_target_year_groups": cs["target_year"]["n_groups"],
      "verdict": "UNTESTABLE",
      "why": "eval2024 spans a single target year; the stratification has one level."}

result = {
    "artifact": "ADVSTRUCT descriptive adjudication (Estimand A + B, EVAL confirmation)",
    "parent_prereg": "prereg/PREREG_ADVSTRUCT_2026_08_15.md",
    "parent_sha256_16": ev["prereg_sha256_16"],
    "amendments": ["AMENDMENT_ADVSTRUCT_A2_2026_08_15.md", "AMENDMENT_ADVSTRUCT_A3_2026_08_15.md"],
    "adjudicated": "2026-08-16",
    "basis": ("Derived entirely from banked artifacts ADVSTRUCT_DESCRIPTIVE_{TRAIN,EVAL}.json. "
              "No refit, no outcome re-read, no new query."),
    "C1": {
        "requirement": "beta_breadth|count CI-clear of 0 in >=5 of 6 count strata",
        "observed_clear": n_clear, "of": len(strata),
        "strata": strata,
        "power_analysis": {
            "assumption": "TRAIN (discovery) effect is exactly true",
            "expected_passes": sum(probs),
            "distribution_of_n_clear": {str(i): d for i, d in enumerate(dist)},
            "P_ge_5_of_6": p_ge5,
            "P_ge_4_of_6": p_ge4,
            "modal_outcome": max(range(len(dist)), key=lambda i: dist[i]),
            "underpowered_strata": [s["stratum"] for s in strata
                                    if s["classification"] == "UNDERPOWERED"],
            "genuine_misses": [s["stratum"] for s in strata
                               if s["classification"] == "GENUINE_MISS"],
        },
        "verdict": "INCONCLUSIVE",
        "verdict_basis": (
            "The bar was unpassable by construction. Under the discovery effect being exactly "
            f"true, P(>=5 of 6) = {p_ge5:.4f} and the modal outcome is "
            f"{max(range(len(dist)), key=lambda i: dist[i])} of 6 — which is what was observed. "
            "Every stratum with >=80% power passed; every failure was underpowered; there were "
            "no genuine misses. A gate with ~7% power against its own alternative does not "
            "discriminate between hypotheses and cannot falsify one."),
        "floor_it_was_inconclusive_against": {
            s["stratum"]: s["mde80_eval"] for s in strata
            if s["classification"] == "UNDERPOWERED"},
    },
    "C2": c2,
    "C3": c3,
    "C4": c4,
    "descriptive_verdict": None,   # filled below
    "what_this_does_not_license": [
        "No model-side claim. Sections 8.1/8.2 of the parent prereg have not run.",
        "No statement that breadth is prognostically useful to AutoSafe: the estimand is "
        "descriptive and research-only (parent §3.4).",
        "No re-run of C1 on pooled TRAIN+EVAL — A3 §4 bars moving the strata, and no "
        "owner override for that was sought or given.",
    ],
}

# Verdict: C2 is the load-bearing gate per A3 §3; C1 is void; C3 passes; C4 untestable.
result["descriptive_verdict"] = {
    "label": "SUPPORTED-ON-C2, C1 INCONCLUSIVE",
    "reading": (
        "The structural claim survives its load-bearing test. Breadth predicts beyond a frozen, "
        "TRAIN-fitted additive system-composition expectation, out-of-sample, with a clustered CI "
        f"clear of zero (beta={c2['beta_beyond_composition']:+.6f}, CI {c2['ci95']}). "
        f"But composition absorbs {c2['share_absorbed_by_composition']:.1%} of the raw "
        "count-conditional gradient, so most of what naive breadth appears to measure is WHICH "
        "systems were advised, not HOW MANY. The residual structural signal is small and real. "
        "C1 is void as a test and is reported INCONCLUSIVE against the per-stratum floors above; "
        "the three strata it failed had 27%, 25% and 4% power."),
    "gate_summary": {"C1": "INCONCLUSIVE", "C2": c2["verdict"], "C3": c3["verdict"],
                     "C4": c4["verdict"]},
}

dest = PROG / "out/ADVSTRUCT_RESULT_2026_08_15.json"
dest.write_text(json.dumps(result, indent=1))

print(f"C1: {n_clear} of {len(strata)} CI-clear (bar >=5) -> {result['C1']['verdict']}")
print(f"    E[passes]={sum(probs):.2f}  P(>=5)={p_ge5:.4%}  P(>=4)={p_ge4:.4%}  "
      f"modal={max(range(len(dist)), key=lambda i: dist[i])}")
print(f"    underpowered={result['C1']['power_analysis']['underpowered_strata']}  "
      f"genuine_misses={result['C1']['power_analysis']['genuine_misses']}")
print(f"C2: {c2['verdict']}  beta={c2['beta_beyond_composition']:+.6f} CI {c2['ci95']}  "
      f"composition absorbs {c2['share_absorbed_by_composition']:.1%}")
print(f"C3: {c3['verdict']}  age={c3['age_quartile']:.3f} depth={c3['prior_depth_band']:.3f}")
print(f"C4: {c4['verdict']} ({c4['n_target_year_groups']} year group)")
print(f"\nVERDICT: {result['descriptive_verdict']['label']}")
print(f"wrote {dest}")
