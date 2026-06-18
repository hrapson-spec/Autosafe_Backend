"""Ablation: which mileage representation gives the best *honest* ranking?

Compares the candidate prediction-time mileage signals on discriminative power
(ROC-AUC) under a point-in-time-honest protocol, and measures the train/serve
skew cost of the contemporaneous odometer.

The production row-level spine (the sampled MOT parquets) is not part of this
repo clone, so this harness uses a synthetic generator whose causal structure
mirrors the real problem:

  * failure is driven by latent wear = f(age, usage-rate-vs-cohort) + noise;
  * the odometer LEVEL is ~ age * rate, so it is strongly collinear with age
    (which the model already has) and adds little *marginal* honest signal;
  * the usage RATE is largely age-orthogonal -> genuine marginal signal;
  * the *contemporaneous* odometer read at the scored test shares same-event
    noise with the outcome, so it looks strongest but is not available before
    the test (and at serving collapses to the previous reading -> skew).

The point is to demonstrate the *direction and mechanism* reproducibly, not to
reproduce production AUCs. Run:  python scripts/ablation_mileage_honest.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mileage_honest import cohort_ratio, projected_odometer

RNG = np.random.default_rng(20260618)
N = 60_000


def synth():
    """Generate a point-in-time-honest dataset with a known causal structure."""
    # A handful of model cohorts with genuinely different typical usage rates
    # (e.g. small hatch ~7k/yr vs diesel estate ~16k/yr).
    cohort_rate_means = np.array([6500, 9000, 12000, 16000], dtype=float)
    cohort = RNG.integers(0, len(cohort_rate_means), N)
    cohort_mean_rate = cohort_rate_means[cohort]

    age_years = RNG.uniform(3.0, 18.0, N)               # age at the TARGET test
    gap_days = RNG.uniform(330, 400, N)                 # last completed -> target
    age_prev = age_years - gap_days / 365.0

    # True usage rate for the vehicle (varies around its cohort mean).
    rate = np.clip(RNG.lognormal(np.log(cohort_mean_rate), 0.35), 1000, 60000)

    # Honest, point-in-time readings.
    prior_odo = np.clip(rate * age_prev + RNG.normal(0, 2500, N), 0, None)
    # Honest usage rate observed from completed tests (the serving estimand).
    obs_rate = np.clip(rate + RNG.normal(0, 1200, N), 500, None)

    # Latent wear -> failure. Driven by age and by how hard the car is driven
    # *relative to its cohort* (age-orthogonal usage intensity), plus noise.
    rate_vs_cohort = np.log(rate / cohort_mean_rate)
    noise = RNG.normal(0, 1.0, N)
    wear = 0.045 * age_years + 1.30 * rate_vs_cohort + 0.55 * noise
    logit = -2.0 + wear
    p = 1.0 / (1.0 + np.exp(-logit))
    y = (RNG.uniform(0, 1, N) < p).astype(int)

    # Contemporaneous odometer read AT the target test: prior + this interval's
    # miles, PLUS a component correlated with the outcome noise (same-event
    # leakage the tester's reading carries). Not available before the test.
    contemporaneous_odo = prior_odo + obs_rate * (gap_days / 365.0) + 1800.0 * noise

    # Projected (honest) odometer: carry the prior reading forward at obs_rate.
    proj = np.array(
        [projected_odometer(prior_odo[i], obs_rate[i], gap_days[i]) for i in range(N)]
    )

    # Cohort-normalised honest rate (denominator fit on the cohort means).
    rate_cohort_ratio = np.array(
        [cohort_ratio(obs_rate[i], cohort_mean_rate[i]) for i in range(N)]
    )

    return {
        "y": y,
        "age": age_years,
        "contemporaneous_odo": contemporaneous_odo,
        "last_completed_odo": prior_odo,
        "projected_odo": proj,
        "annualized_rate": obs_rate,
        "rate_cohort_ratio": rate_cohort_ratio,
    }


def univariate_auc(x, y):
    x = np.asarray(x, float)
    # Orient so higher score = higher risk for a fair |AUC| comparison.
    auc = roc_auc_score(y, x)
    return max(auc, 1.0 - auc)


def marginal_auc_over_age(x, age, y):
    """AUC of a logistic model on [age, x] — the honest *marginal* lift a
    representation gives once age is already in the model."""
    X = np.column_stack([age, np.asarray(x, float)])
    X = (X - X.mean(0)) / (X.std(0) + 1e-9)
    m = LogisticRegression(max_iter=500).fit(X, y)
    return roc_auc_score(y, m.predict_proba(X)[:, 1])


def skew_cost(d):
    """Quantify the train/serve skew of the contemporaneous odometer inside a
    realistic multi-feature model [age, rate, <level>].

    A single-feature AUC is invariant to monotonic rescaling, so the skew only
    shows up once the model learns a *weight* for the level: a weight fitted on
    the leaky contemporaneous level is misapplied when production feeds the
    honest last-completed level.
    """
    y = d["y"]
    n = len(y)
    tr, te = slice(0, n // 2), slice(n // 2, n)

    def fit_eval(level_train, level_serve):
        Xtr = np.column_stack([d["age"][tr], d["annualized_rate"][tr], d[level_train][tr]])
        mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
        m = LogisticRegression(max_iter=500).fit((Xtr - mu) / sd, y[tr])
        Xte = np.column_stack([d["age"][te], d["annualized_rate"][te], d[level_serve][te]])
        return roc_auc_score(y[te], m.predict_proba((Xte - mu) / sd)[:, 1])

    matched = fit_eval("contemporaneous_odo", "contemporaneous_odo")
    skewed = fit_eval("contemporaneous_odo", "last_completed_odo")
    honest = fit_eval("last_completed_odo", "last_completed_odo")
    return matched, skewed, honest


def main():
    d = synth()
    y = d["y"]
    print(f"Synthetic honest-ranking ablation  (n={len(y):,}, fail rate={y.mean():.3f})\n")

    reps = [
        ("contemporaneous odo  (test_mileage, LEAKY/unavailable)", "contemporaneous_odo"),
        ("last-completed odo    (honest level)", "last_completed_odo"),
        ("projected odo         (honest level, forward)", "projected_odo"),
        ("annualised rate       (honest, age-orthogonal)", "annualized_rate"),
        ("cohort-normalised rate (honest, decontaminated)", "rate_cohort_ratio"),
        ("age only              (reference)", "age"),
    ]

    print(f"{'representation':<55}{'univ AUC':>10}{'AUC | age':>12}")
    print("-" * 77)
    for label, key in reps:
        ua = univariate_auc(d[key], y)
        ma = marginal_auc_over_age(d[key], d["age"], y) if key != "age" else univariate_auc(d["age"], y)
        print(f"{label:<55}{ua:>10.4f}{ma:>12.4f}")

    print("\nTrain/serve skew cost of the contemporaneous odometer")
    print("-" * 77)
    matched, skewed, honest = skew_cost(d)
    print(f"  [age,rate,level=contemporaneous] train+serve   (matched, dishonest): {matched:.4f}")
    print(f"  [age,rate,level=contemporaneous] train / serve honest (real skew)  : {skewed:.4f}")
    print(f"  [age,rate,level=last-completed]  train+serve   (honest)            : {honest:.4f}")
    print(f"  -> leak premium you cannot keep: {matched - honest:+.4f} AUC "
          f"(contemporaneous looks better than it can ever serve)")
    print(f"  -> residual skew gap (honest - skewed): {honest - skewed:+.4f} AUC "
          f"(second-order here; the dominant effect is the unkeepable leak premium)")

    print("\nReadout")
    print("-" * 77)
    print("  * The contemporaneous odometer tops univariate AUC but most of its")
    print("    edge vanishes once age is in the model, and it is unavailable")
    print("    before the test (serving it the honest level costs AUC).")
    print("  * The usage RATE keeps its lift *after* conditioning on age — it is")
    print("    the genuine marginal honest signal; cohort-normalising it isolates")
    print("    'driven hard for what it is'. That is the strongest VALID signal.")


if __name__ == "__main__":
    main()
