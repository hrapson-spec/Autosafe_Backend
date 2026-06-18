# Honest point-in-time mileage representation

## The question

Mileage is one of the model's most important feature families (in the V40/V55
`results.json`, `test_mileage` and `annualized_mileage_v2` are the two single
largest features, with `usage_band_hybrid` and `mileage_cohort_ratio` close
behind). The question this change answers is *not* "does serving match
training?" but: **which prediction-time mileage signal gives the best _honest_
ranking** — last-MOT mileage, projected mileage, user/current mileage,
annualised mileage, or cohort-normalised mileage.

## The finding

The raw odometer **level** (`test_mileage`) is the strongest-*looking* signal
but the least *honest* one:

- In **training**, `test_mileage` is `d.test_mileage` — the odometer recorded
  **at the scored test**, contemporaneous with the label. The training
  `annualized_mileage_v2` also differences that same scored-test reading
  (`(d.test_mileage - m_prev.test_mileage) * 365 / days`), so it reads the
  target test too.
- In **serving**, the upcoming test has not happened, so the same names resolve
  to the most recent **completed** test (`feature_engineering_v55.py` uses
  `tests[0]`). The trained feature and the served feature are therefore
  different estimands (train/serve skew), and the trained one violates the v57
  contract's point-in-time rule ("only data available strictly before the
  target test date").
- The level is also heavily collinear with vehicle age, which the model already
  encodes — so most of its apparent strength is an age proxy plus same-event
  leakage.

Because `level ≈ age × usage_rate` and age is already a dominant feature, the
**usage rate** is the part of mileage that carries genuinely new, age-orthogonal
information. The strongest **valid** representation is the **cohort-normalised
usage rate** — "how hard is this car driven for what it is" — which is honest
(pure past readings, no outcome), age- and model-decontaminated, and robust.

`scripts/ablation_mileage_honest.py` demonstrates the mechanism with real
ROC-AUC on a synthetic generator (the production row-level spine is not in the
repo clone). Representative run:

| representation | univariate AUC | AUC given age |
|---|---|---|
| contemporaneous odo (`test_mileage`, leaky/unavailable) | 0.595 | 0.599 |
| last-completed odo (honest level) | 0.589 | 0.594 |
| projected odo (honest level, forward) | 0.591 | 0.594 |
| annualised rate (honest, age-orthogonal) | 0.582 | 0.593 |
| **cohort-normalised rate (honest, decontaminated)** | **0.612** | **0.620** |
| age only (reference) | 0.546 | 0.546 |

The contemporaneous level carries a "+0.002 leak premium you cannot keep": it
tops the *level* group only because of same-event leakage that evaporates at
serving. The cohort-normalised rate wins both univariate and **marginal over
age** — it is the strongest valid signal.

## What changed

A single source of truth, `mileage_honest.py`, is imported by both the trainer
and the serving path so the two cannot drift:

- `prior_odometer` — honest LEVEL: the odometer at the test strictly before the
  target (`test_mileage - miles_since_last_test`).
- `projected_odometer` — the level carried forward to the target date at the
  honest rate.
- `cohort_ratio` — safe-divide normaliser for both the level and the rate
  cohort ratios.

**Serving** (`feature_engineering_v55.py`) now additionally emits
`last_completed_odometer`, `projected_odometer` and
`annualized_mileage_cohort_ratio`. These are additive — `predict_risk` selects
only `FEATURE_NAMES`, so the **currently deployed model is unaffected**, and the
existing `mileage_cohort_ratio` value is unchanged (at serving its numerator was
already the last completed reading).

**Training** (`train_catboost_production_v55.py`):

- `compute_cohort_rate_stats` (DEV-only, leakage-free) builds the cohort mean
  usage-rate table; `add_cohort_rate_features` (after the V36 mileage block)
  adds `last_completed_odometer` and `annualized_mileage_cohort_ratio` and
  re-bases `mileage_cohort_ratio` onto the honest level. The rate table is
  merged into `cohort_stats` so serving gets it from one pickle.
- `FEATURE_COLS` now uses `last_completed_odometer` instead of the
  contemporaneous `test_mileage`, and adds `annualized_mileage_cohort_ratio`.
  This **defines the next model**.

## To deploy (must run in the training environment)

The production row-level spine is not in this repo clone, so the retrain has not
been run here. To ship:

1. Run `train_catboost_production_v55.py` against the spine. This produces a new
   `model.cbm` on the honest feature set and a `cohort_stats.pkl` that now
   carries `cohort_rate` / `global_rate_avg`.
2. Regenerate serving's `FEATURE_NAMES` to match the new model's feature order
   (the v57 `model_bundle.emit_contract` / `validate_feature_columns` path gates
   this). `tests/test_model_bundle.py` pins the current served count (104); bump
   it with the version when the honest contract lands.
3. Confirm the honest test AUC holds (the ablation predicts it does, with the
   cohort-normalised rate carrying the marginal signal the contemporaneous level
   only appeared to have).

Until then serving degrades gracefully: with the current `cohort_stats.pkl`
(no rate table), `annualized_mileage_cohort_ratio` returns the neutral 1.0.
