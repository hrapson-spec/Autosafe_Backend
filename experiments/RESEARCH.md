# AutoSafe Experiment Loop — Instructions & Guardrails

Instructions for anyone — human, Claude Code session, or an
AutoResearch-style agent — running model experiments on AutoSafe.
**Read fully before running anything. The Hard Rules are not optional.**

## Mission

Improve MOT failure prediction over the V55 production baseline
(CatBoost, 104 features, ensemble val AUC recorded by
`exp_000_baseline`). The deliverable of every session is
`results.jsonl` entries + a short narrative in `LOG.md` — not just code.

## The loop

1. Pick a hypothesis (from the backlog below, or propose a new one).
2. Write `experiments/exp_NNN_short_name.py` defining `NAME`,
   `HYPOTHESIS`, and `apply(ctx)` (see `harness.py` docstring for the
   `ctx` contract; copy `exp_001` as a template).
3. Run: `python experiments/harness.py experiments/exp_NNN_....py`
   (defaults: 300k train rows, 150k val rows, 3 seeds — keep flags
   identical across a campaign).
4. The harness logs the result and prints a verdict:
   - `KEEP` (≥ +0.001 ensemble val AUC vs baseline): keep the file,
     add a LOG.md entry, commit as `exp_NNN: +0.00XX KEEP`.
   - `REVERT`: keep the experiment file and the log entry (negative
     results are knowledge), but do NOT carry the change forward.
5. Every experiment gets exactly ONE run. No re-running with different
   seeds until it passes. If you believe a result is noise, say so in
   LOG.md and move on.

Run `exp_000_baseline.py` first (and again whenever harness flags or
`DEFAULT_PARAMS` change) — deltas are measured against the latest
baseline with a matching config.

## Hard rules

1. **OOT is quarantined.** Nothing in this loop reads `X_test`/`y_test`
   (the OOT set). It is used at most ONCE per campaign, by a human, to
   confirm the final surviving combination. An agent that touches OOT
   has failed the task regardless of any AUC it reports.
2. **Never fit on validation.** No target statistics, encoders,
   scalers, or feature selections computed from `X_val`/`y_val`.
   Transforms are fit on train and applied to both.
3. **No future information.** Features may only be derived from the
   existing 104 columns (which are already point-in-time-safe). Do not
   reconstruct anything from raw MOT history without time-slicing
   review — this codebase has been burned before (see the V57 RC-3
   station-feature removal).
4. **Serve-parity.** Any new feature must be computable at serving time
   in `feature_engineering_v55.py` from DVSA history + postcode. If you
   cannot describe how it would be computed at serve time in one
   sentence in the LOG, do not build it.
5. **One change per experiment.** If you change weights AND depth,
   you learn nothing. Factor combined winners into separate follow-ups.
6. **Don't tune hyperparameters here.** Numeric knob search belongs to
   `tune_catboost_v55.py` (Optuna). Experiments may *set* params when
   the hypothesis requires it (e.g., monotone constraints), not sweep
   them.
7. **Log everything.** A result that isn't in `results.jsonl` + `LOG.md`
   didn't happen. Commit after every experiment, win or lose.

## Statistical honesty

- The KEEP threshold (+0.001) exists because ~30 experiments against a
  150k-row validation set WILL produce lucky flukes. Do not lower it.
- KEEPs accumulate against the same validation set, so a long campaign
  overfits it. After ~20-30 experiments, or before promoting anything
  to production, a human re-baselines: combine the KEEPs, run the
  one-time OOT confirmation, and start a fresh campaign.
- Expect most experiments to lose. That is the loop working.

## Hypothesis backlog (starter set, roughly by expected value)

1. **Serve the seed ensemble.** Production trains 10 seeds but serves
   only seed 0 (`train_catboost_production_v55.py:2623`). Merge with
   CatBoost `sum_models` and measure the gap. Known-direction win;
   mostly an engineering experiment.
2. **Drop the 4 parity-broken constant features**
   (`station_fail_rate_smoothed`, `station_x_prev_outcome_fail_rate`,
   `station_strictness_bias`, `suspension_risk_profile` — served as
   constants, see `feature_engineering_v55.py:536,790`). If val AUC is
   flat, deleting them is a free parity win.
3. **Calibration head-to-head:** Platt vs isotonic vs beta calibration,
   fit on train predictions of the validation-split models, scored by
   val Brier/log-loss. (Calibration is the product; see the 2026-06-12
   literature scan.)
4. **Monotone constraints** on physically-monotone features (e.g.,
   `mech_decay_*`, EB failure-rate priors) via CatBoost
   `monotone_constraints` — often small AUC cost/gain but better
   generalization and trustworthiness.
5. **Interaction features** from existing columns: vehicle-age band ×
   annualized mileage; advisory streak × usage band; EB prior ×
   negligence band.
6. **Recency weighting shape** (exp_001 starts this): exponential decay
   vs the 20/6/2/1 step function.
7. **Loss variants:** class-weighted Logloss; focal-style loss — check
   both AUC and Brier (calibration matters as much as ranking).
8. **Text-mining feature depth:** the 16 text features carry only 0.6%
   importance for 15% of feature count — try consolidating into 3-4
   composite indices (less noise for the trees to overfit).
9. **Larger architectural bets** (TabM / MLP-PLR challenger, survival
   framing) — out of scope for this harness; require human sign-off
   and their own infrastructure.

## Operational notes

- Data prerequisite: `~/autosafe_work/v55_prepared_data.pkl` (created by
  step [9b] of `train_catboost_production_v55.py`). The loop therefore
  runs on the machine that holds `~/autosafe_work` (currently the
  development Mac), not from a fresh clone.
- Runtime at defaults: roughly 5-15 min per experiment on a laptop CPU
  (3 seeds, early stopping). `--sample 0` for full-data confirmation of
  shortlisted winners only.
- To point an AutoResearch-style agent at this: give it this file as
  the instruction file, the repo as the workspace, and
  `python experiments/harness.py <exp file>` as the experiment command.
  Supervise the first few iterations before any unattended run.
