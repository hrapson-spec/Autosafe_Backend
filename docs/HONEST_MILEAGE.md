# Honest point-in-time mileage representation

## The question

Mileage is one of the model's most important feature families (in the V40/V55
`results.json`, `test_mileage` and `annualized_mileage_v2` are the two single
largest features, with `usage_band_hybrid` and `mileage_cohort_ratio` close
behind). The question this change answers is *not* "does serving match
training?" but: **which prediction-time mileage signal gives the best _honest_
ranking** — last-MOT mileage, projected mileage, user/current mileage,
annualised mileage, or cohort-normalised mileage.

## The leak (correct, and the reason for the change)

The raw odometer **level** (`test_mileage`) is the strongest-*looking* signal
but is not point-in-time honest in training:

- In **training**, `test_mileage` is `d.test_mileage` — the odometer recorded
  **at the scored test**, contemporaneous with the label. The training
  `annualized_mileage_v2` also differences that same scored-test reading
  (`(d.test_mileage - m_prev.test_mileage) * 365 / days`), so it reads the
  target test too.
- In **serving**, the upcoming test has not happened, so the same names resolve
  to the most recent **completed** test (`feature_engineering_v55.py` uses
  `tests[0]`). The trained feature and the served feature are therefore
  different estimands (train/serve skew), and the trained one violates the v57
  contract's point-in-time rule.

The honest LEVEL is `test_mileage - miles_since_last_test`: this **algebraically
cancels** the scored-test reading down to the previous test's odometer, which at
serving equals `tests[0]` — so train and serve become the same estimand.

## The finding (real-data ablation overturns the rate hypothesis)

The original hypothesis was that, since `level ≈ age × usage_rate` and age is
already dominant, the **cohort-normalised usage rate** would be the strongest
*valid* signal. A synthetic generator supported that. **It does not hold on real
MOT data.** `scripts/ablation_mileage_honest.py` (synthetic) was re-run on the
actual V55 spine — DEV-fit cohort means, evaluated on held-out OOT, with the
honest rate computed correctly as the as-of-prior (2-hop) rate:

| representation (real OOT, common subset n=28,121) | univariate AUC | AUC given age |
|---|---|---|
| contemporaneous odo (`test_mileage`, leaky/unavailable) | 0.642 | 0.652 |
| **last-completed odo (honest level)** | **0.642** | **0.647** |
| leaky annualised rate (`annualized_mileage_v2`) | 0.547 | 0.639 |
| honest as-of-prior rate | 0.500 | 0.614 |
| cohort-normalised honest rate | 0.500 | 0.614 |
| age only (reference) | — | 0.613 |

Two results:

1. **The honest LEVEL ≈ the leaky contemporaneous level** (0.647 vs 0.652 over
   age): de-leaking the level removes the train/serve skew at essentially no AUC
   cost. This is a clean correctness win and is what ships.
2. **The cohort-normalised RATE carries no real signal**: univariate AUC 0.500,
   `corr_with_y = +0.006`, and +0.001 over age (vs age-only 0.613). The synthetic
   0.612 was an artifact of the generator (it *set* a rate→failure coefficient).
   Separately, the honest rate needs a resolved 2-hop chain whose prev-prev
   odometer lands in the **10% test-hash spine** only ~4.7% of the time, so it is
   not even learnable offline. **The rate feature was therefore dropped.**

## What changed (level-only)

`mileage_honest.py` is the single source of truth for the leakage-free level,
imported by both the trainer and serving so they cannot drift:

- `prior_odometer` — honest LEVEL: `test_mileage - miles_since_last_test`.
- `cohort_ratio` — safe-divide normaliser for `mileage_cohort_ratio`.

**Serving** (`feature_engineering_v55.py`) emits `last_completed_odometer`
(= `test_mileage`, which at serving already IS the last completed reading).
Additive — `predict_risk` selects only `FEATURE_NAMES`, so the **deployed model
is unaffected** until the next retrain.

**Training** (`train_catboost_production_v55.py`): `add_honest_level_features`
(after the V36 mileage block) adds `last_completed_odometer` and re-bases
`mileage_cohort_ratio` onto the honest level. `FEATURE_COLS` uses
`last_completed_odometer` **instead of** the contemporaneous `test_mileage`.
This **defines the next model**.

The cohort-normalised rate, `compute_cohort_rate_stats`, `add_cohort_rate_features`,
`projected_odometer`, and the serving rate emission were all removed.

## To deploy (must run in the training environment)

Level-only swap; expected AUC-neutral (the ablation shows honest ≈ contemporaneous),
with the train/serve level skew removed.

1. Run `train_catboost_production_v55.py` against the spine → new `model.cbm` on
   the honest feature set (the `cohort_stats.pkl` is unchanged in shape — no rate
   table is added).
2. Regenerate serving's `FEATURE_NAMES` to match the new model's feature order
   (`model_bundle.emit_contract` / `validate_feature_columns` gates this);
   `tests/test_model_bundle.py` pins the served count (104) — re-pin with the
   version when the honest contract lands.
3. Confirm OOT AUC ≥ current and Gate-0 capture not worse (the level swap should
   be neutral); verify train/serve parity on `last_completed_odometer`.
