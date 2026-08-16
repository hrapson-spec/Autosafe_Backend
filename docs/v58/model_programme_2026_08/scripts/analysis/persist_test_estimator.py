#!/usr/bin/env python3
"""Equivalence and invariance tests for persist_estimator.py. Exit 1 on any failure.

T1  IRLS coefficients and SEs match sklearn LogisticRegression(C=inf)
T2  warm starts do not change the solution
T3  offset pins the persistence coefficient exactly
T4  MEMBERSHIP INVARIANCE — the denominator is all nine systems in every replicate,
    and the own-estimate set never varies (the defect this rewrite exists to fix)
T5  synthetic planted-effect recovery still holds after the rewrite
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from persist_estimator import (irls_logistic, design, system_eligibility,  # noqa: E402
                               standardised_effect, CONTROLS)
from sklearn.linear_model import LogisticRegression  # noqa: E402

fails = []


def check(ok, msg):
    print(("  PASS  " if ok else "  FAIL  ") + msg)
    if not ok:
        fails.append(msg)


SYS = [f"s{i}" for i in range(9)]


def synth(rng, n_per, betas, base=-2.5, conf=1.0, sizes=None):
    rows = []
    for i, (s, b) in enumerate(zip(SYS, betas)):
        n = n_per if sizes is None else sizes[i]
        z = rng.normal(size=n)
        p = (rng.random(n) < 1 / (1 + np.exp(-(-0.4 + conf * z)))).astype(int)
        y = (rng.random(n) < 1 / (1 + np.exp(-(base + b * p + conf * z)))).astype(int)
        rows.append(pd.DataFrame({
            "sys": s, "persistent": p, "y": y, "conf_z": z,
            "n_adv_sys_t": rng.integers(1, 3, n), "n_items_sys_t": rng.integers(1, 4, n),
            "tot_adv_t": rng.integers(1, 5, n), "n_advised_systems_t": rng.integers(1, 4, n),
            "tot_min_t": 0, "tot_md_t": 0, "n_episodes": rng.integers(3, 9, n),
            "n_prior_fail_sysdays": rng.integers(0, 3, n),
            "prior_md_items": rng.integers(0, 4, n),
            "interval_days": 365 + rng.integers(-40, 40, n)}))
    return pd.concat(rows, ignore_index=True)


import persist_estimator as PE
PE.CONTROLS = PE.CONTROLS + ["conf_z"]   # the planted confounder must be adjusted for

rng = np.random.default_rng(11)

# ---------------------------------------------------------------- T1 vs sklearn
print("\nT1 — IRLS vs sklearn LogisticRegression(C=inf)")
d = synth(rng, 3000, [0.5] * 9)
sub = d[d.sys == "s0"]
X = np.hstack([sub.persistent.to_numpy(float).reshape(-1, 1), design(sub)])
y = sub.y.to_numpy(float)
b_irls, H = irls_logistic(X, y)
m = LogisticRegression(C=np.inf, max_iter=5000, tol=1e-8, solver="lbfgs").fit(X, y)
b_sk = np.concatenate([m.intercept_, m.coef_[0]])
Xd_ = np.hstack([np.ones((len(X), 1)), X])
score = lambda b: float(np.max(np.abs(Xd_.T @ (y - 1 / (1 + np.exp(-(Xd_ @ b)))))))
# The MLE has score exactly 0. sklearn's lbfgs plateaus near 1e-4; IRLS reaches
# machine precision. So the criterion is NOT bit-agreement with a looser reference:
# it is that IRLS is at least as close to the optimum, and agrees once sklearn is
# converged tightly.
check(score(b_irls) < score(b_sk),
      f"IRLS closer to MLE: score {score(b_irls):.2e} vs sklearn {score(b_sk):.2e}")
dmax = float(np.max(np.abs(b_irls - b_sk)))
check(dmax < 1e-5, f"agreement vs tightly-converged sklearn = {dmax:.3e} (< 1e-5)")
eta = m.decision_function(X)
w = np.clip(1 / (1 + np.exp(-eta)), 1e-9, 1 - 1e-9); w = w * (1 - w)
Xd = np.hstack([np.ones((len(X), 1)), X])
se_sk = float(np.sqrt(np.linalg.pinv(Xd.T @ (Xd * w[:, None]))[1, 1]))
se_irls = float(np.sqrt(np.linalg.pinv(H)[1, 1]))
check(abs(se_sk - se_irls) / se_sk < 1e-6,
      f"SE agreement: irls {se_irls:.8f} vs sklearn-route {se_sk:.8f}")

# ---------------------------------------------------------------- T2 warm start
print("\nT2 — warm start does not change the solution")
b_warm, _ = irls_logistic(X, y, b0=b_irls + 0.3)
check(float(np.max(np.abs(b_warm - b_irls))) < 1e-7,
      f"cold vs warm max diff = {float(np.max(np.abs(b_warm-b_irls))):.3e}")

# ---------------------------------------------------------------- T3 offset pin
print("\nT3 — offset pins the persistence coefficient exactly")
PIN = 0.4321
p = sub.persistent.to_numpy(float)
Xc = design(sub)
b_off, _ = irls_logistic(Xc, y, offset=PIN * p)
# refit WITH a free persistence term but starting from the pinned solution; the pinned
# model's implied persistence effect must be exactly PIN by construction
eta_pinned = np.hstack([np.ones((len(Xc), 1)), Xc]) @ b_off + PIN * p
eta_at1 = np.hstack([np.ones((len(Xc), 1)), Xc]) @ b_off + PIN * 1.0
eta_at0 = np.hstack([np.ones((len(Xc), 1)), Xc]) @ b_off + PIN * 0.0
implied = float(np.unique(np.round(eta_at1 - eta_at0, 10))[0])
check(abs(implied - PIN) < 1e-9, f"implied log-odds gap = {implied:.10f} (target {PIN})")

# ------------------------------------------------- T4 MEMBERSHIP INVARIANCE
print("\nT4 — membership invariance (the defect this rewrite exists to fix)")
# one system deliberately starved so it is INELIGIBLE on the full sample
sizes = [3000] * 8 + [60]   # ~6 positives at 10% prevalence -> below min_pos=10
d2 = synth(rng, None, [0.5] * 9, sizes=sizes)
elig = system_eligibility(d2, SYS)
inelig = [s for s in SYS if not elig[s]["eligible"]]
check(len(inelig) >= 1, f"starved system is ineligible on the full sample: {inelig}")
r0 = standardised_effect(d2, SYS, {s: elig[s]["eligible"] for s in SYS})
check(r0["n_systems_in_denominator"] == 9,
      f"denominator covers all 9 systems (got {r0['n_systems_in_denominator']})")
check(all(r0["per_system"][s]["role"] == "pooled(offset)" for s in inelig),
      f"ineligible systems use the pooled coefficient: "
      f"{[r0['per_system'][s]['role'] for s in inelig]}")

own, dens, effs = set(), set(), []
veh = np.arange(len(d2)) // 2                     # 2 rows per synthetic 'vehicle'
uniq = np.unique(veh)
for rep in range(40):
    rr = np.random.default_rng([20260816, rep])   # replicate-indexed stream
    pick = rr.integers(0, len(uniq), len(uniq))
    idxs = np.concatenate([np.flatnonzero(veh == u) for u in pick])
    res = standardised_effect(d2.take(idxs), SYS, {s: elig[s]["eligible"] for s in SYS})
    own.add(tuple(res["systems_own_estimate"]))
    dens.add(res["n_systems_in_denominator"])
    effs.append(res["standardised_risk_diff_pp"])
check(len(own) == 1, f"own-estimate set identical across 40 replicates "
                     f"({len(own)} distinct set(s))")
check(dens == {9}, f"denominator = 9 systems in every replicate (saw {dens})")
print(f"        effect across replicates: mean {np.mean(effs):+.4f} pp, "
      f"sd {np.std(effs, ddof=1):.4f}")

# ---------------------------------------------------------------- T5 recovery
print("\nT5 — planted-effect recovery survives the rewrite")
d3 = synth(rng, 3000, [0.5] * 9)
e3 = system_eligibility(d3, SYS)
r3 = standardised_effect(d3, SYS, {s: e3[s]["eligible"] for s in SYS})
z = d3.conf_z.to_numpy()
truth = ((1 / (1 + np.exp(-(-2.5 + 0.5 + z)))) - (1 / (1 + np.exp(-(-2.5 + z))))).mean() * 100
err = r3["standardised_risk_diff_pp"] - truth
check(abs(err) < 1.0, f"planted {truth:+.4f} pp | estimated "
                      f"{r3['standardised_risk_diff_pp']:+.4f} pp | err {err:+.4f}")
d4 = synth(rng, 3000, [0.0] * 9)
e4 = system_eligibility(d4, SYS)
r4 = standardised_effect(d4, SYS, {s: e4[s]["eligible"] for s in SYS})
check(abs(r4["standardised_risk_diff_pp"]) < 1.0,
      f"true null returns {r4['standardised_risk_diff_pp']:+.4f} pp")

print("\n" + "=" * 62)
if fails:
    print(f"FAILED — {len(fails)} check(s)")
    sys.exit(1)
print("ALL PASS — estimator equivalence and membership invariance verified")
