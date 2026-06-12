"""
AutoSafe CatBoost Hyperparameter Tuning (Optuna)
================================================

Finds better hyperparameters for the production CatBoost model than the
hand-set V17 values in train_catboost_production_v55.py (PARAMS).

How it works (beginner notes)
-----------------------------
Hyperparameters are the model's "knobs" (tree depth, learning rate, ...).
Tuning = trying many knob combinations and keeping the best one, where
"best" is measured on data the model did NOT train on (the validation set).

This script uses Optuna, a Bayesian optimizer: it starts with the current
production parameters as a baseline, then proposes new combinations,
learns which regions of the search space work, and focuses there. Every
trial is saved to a local SQLite file, so you can stop with Ctrl+C and
re-run the same command later to resume where you left off.

Validation protocol: the 2023 rows of the DEV set (identified by their
sample weight of 20.0) are held out as the validation set, and the model
trains on pre-2023 rows. That mimics the production protocol (train on
the past, predict the most recent year) without ever touching the OOT
set — the OOT set must stay untouched until one final check, otherwise
the tuning "cheats" by fitting to it.

Prerequisites
-------------
1. Run train_catboost_production_v55.py once (it saves the prepared
   feature matrices to ~/autosafe_work/v55_prepared_data.pkl at step [9b]).
2. pip install optuna

Usage (run on the machine that has ~/autosafe_work, e.g. your Mac)
------------------------------------------------------------------
  # Quick first pass (~minutes per trial on a laptop):
  python tune_catboost_v55.py --trials 40

  # Use the full DEV set instead of a subsample (much slower, more exact):
  python tune_catboost_v55.py --trials 40 --sample 0 --val-sample 0

  # After tuning: ONE final check of the best params against the OOT set:
  python tune_catboost_v55.py --final-check

When finished, copy the printed PARAMS block into
train_catboost_production_v55.py and retrain the full 10-seed model.
"""

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score
from catboost import CatBoostClassifier, Pool

DATA_FILE = Path.home() / "autosafe_work/v55_prepared_data.pkl"
STORAGE = "sqlite:///catboost_v55_tuning.db"
STUDY_NAME = "v55_catboost"
EARLY_STOPPING_ROUNDS = 150
MAX_ITERATIONS = 4000  # ceiling; early stopping picks the effective count

# Current production values (train_catboost_production_v55.py PARAMS, set at V17).
# Enqueued as trial 0 so every later trial shows its gain over production.
BASELINE_PARAMS = {
    'learning_rate': 0.02,
    'depth': 6,
    'l2_leaf_reg': 4.0,
    'border_count': 128,
    'random_strength': 1.0,
    'bagging_temperature': 0.5,
    'min_data_in_leaf': 1,
}


def load_data(path):
    if not path.exists():
        sys.exit(
            f"Prepared data not found: {path}\n"
            "Run train_catboost_production_v55.py once first — its step [9b] "
            "saves the feature matrices this script needs."
        )
    with open(path, 'rb') as f:
        data = pickle.load(f)
    return data


def split_train_valid(data, split, rng):
    """Return (X_tr, y_tr, w_tr, X_val, y_val) as positionally-aligned arrays."""
    X, y, w = data['X_train'], data['y_train'], np.asarray(data['train_weights'])

    if split == 'temporal':
        val_mask = w == 20.0  # weight 20.0 marks the 2023 DEV rows
        if not val_mask.any():
            print("  WARNING: no weight-20 (2023) rows found; falling back to random split")
            split = 'random'
        else:
            tr_idx = np.where(~val_mask)[0]
            val_idx = np.where(val_mask)[0]

    if split == 'random':
        idx = rng.permutation(len(X))
        n_val = int(len(X) * 0.15)
        val_idx, tr_idx = idx[:n_val], idx[n_val:]

    return (X.iloc[tr_idx], y.iloc[tr_idx], w[tr_idx],
            X.iloc[val_idx], y.iloc[val_idx])


def subsample(X, y, w, n, rng):
    if n and n < len(X):
        idx = rng.choice(len(X), size=n, replace=False)
        return X.iloc[idx], y.iloc[idx], (w[idx] if w is not None else None)
    return X, y, w


def make_objective(train_pool, X_val, y_val, cat_idx):
    import optuna  # local import so --help works without optuna installed

    def objective(trial):
        params = {
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.12, log=True),
            'depth': trial.suggest_int('depth', 4, 10),
            'l2_leaf_reg': trial.suggest_float('l2_leaf_reg', 1.0, 30.0, log=True),
            'border_count': trial.suggest_categorical('border_count', [64, 128, 254]),
            'random_strength': trial.suggest_float('random_strength', 0.0, 5.0),
            'bagging_temperature': trial.suggest_float('bagging_temperature', 0.0, 2.0),
            'min_data_in_leaf': trial.suggest_int('min_data_in_leaf', 1, 100, log=True),
        }
        model = CatBoostClassifier(
            iterations=MAX_ITERATIONS,
            eval_metric='AUC',
            cat_features=cat_idx,
            random_seed=0,
            verbose=0,
            thread_count=-1,
            use_best_model=True,
            **params,
        )
        val_pool = Pool(X_val, y_val, cat_features=cat_idx)
        model.fit(train_pool, eval_set=val_pool,
                  early_stopping_rounds=EARLY_STOPPING_ROUNDS, verbose=0)
        auc = roc_auc_score(y_val, model.predict_proba(X_val)[:, 1])
        trial.set_user_attr('best_iteration', model.get_best_iteration())
        print(f"  trial {trial.number:>3}: AUC={auc:.5f} "
              f"(trees={model.get_best_iteration()}, depth={params['depth']}, "
              f"lr={params['learning_rate']:.3f})")
        return auc

    return objective


def run_tuning(args):
    import optuna

    rng = np.random.default_rng(42)
    data = load_data(args.data)
    feature_cols = data['feature_cols']
    cat_idx = [feature_cols.index(c) for c in data['cat_features'] if c in feature_cols]

    X_tr, y_tr, w_tr, X_val, y_val = split_train_valid(data, args.split, rng)
    X_tr, y_tr, w_tr = subsample(X_tr, y_tr, w_tr, args.sample, rng)
    X_val, y_val, _ = subsample(X_val, y_val, None, args.val_sample, rng)

    print(f"Train: {len(X_tr):,} rows ({y_tr.mean()*100:.1f}% fail)  "
          f"Valid: {len(X_val):,} rows ({y_val.mean()*100:.1f}% fail)  "
          f"[{args.split} split]")

    train_pool = Pool(X_tr, y_tr, cat_features=cat_idx, weight=w_tr)

    study = optuna.create_study(
        study_name=STUDY_NAME, storage=STORAGE,
        direction='maximize', load_if_exists=True,
    )
    if not study.trials:  # first run: make production params trial 0
        study.enqueue_trial(BASELINE_PARAMS)

    study.optimize(make_objective(train_pool, X_val, y_val, cat_idx),
                   n_trials=args.trials)

    baseline = next((t for t in study.trials if t.params == BASELINE_PARAMS), None)
    best = study.best_trial
    print("\n" + "=" * 64)
    if baseline and baseline.value is not None:
        print(f"Baseline (production params): AUC {baseline.value:.5f}")
        print(f"Best found:                   AUC {best.value:.5f}  "
              f"(+{best.value - baseline.value:.5f})")
    else:
        print(f"Best found: AUC {best.value:.5f}")
    print(f"\nBest PARAMS (paste into train_catboost_production_v55.py):\n")
    print("PARAMS = {")
    print(f"    'iterations': {MAX_ITERATIONS},  # early stopping picked "
          f"{best.user_attrs.get('best_iteration', '?')} trees in tuning")
    for k, v in best.params.items():
        print(f"    '{k}': {v!r},")
    print("    'eval_metric': 'AUC',")
    print("}")
    print("\nNext: --final-check evaluates these params ONCE against the OOT set.")


def run_final_check(args):
    """Train one model on full DEV with the study's best params; score OOT once."""
    import optuna

    data = load_data(args.data)
    feature_cols = data['feature_cols']
    cat_idx = [feature_cols.index(c) for c in data['cat_features'] if c in feature_cols]

    study = optuna.load_study(study_name=STUDY_NAME, storage=STORAGE)
    params = study.best_trial.params
    print(f"Best trial: #{study.best_trial.number}, valid AUC {study.best_value:.5f}")
    print(f"Training single seed on full DEV ({len(data['X_train']):,} rows)...")

    rng = np.random.default_rng(42)
    _, _, _, X_val, y_val = split_train_valid(data, 'temporal', rng)
    train_pool = Pool(data['X_train'], data['y_train'],
                      cat_features=cat_idx, weight=np.asarray(data['train_weights']))
    model = CatBoostClassifier(
        iterations=MAX_ITERATIONS, eval_metric='AUC', cat_features=cat_idx,
        random_seed=0, verbose=200, thread_count=-1, use_best_model=True, **params,
    )
    # Early-stop on the 2023 DEV slice — NOT on OOT, so OOT stays a clean test
    model.fit(train_pool, eval_set=Pool(X_val, y_val, cat_features=cat_idx),
              early_stopping_rounds=EARLY_STOPPING_ROUNDS)

    oot_auc = roc_auc_score(data['y_test'],
                            model.predict_proba(data['X_test'])[:, 1])
    print("\n" + "=" * 64)
    print(f"OOT AUC with tuned params (single seed): {oot_auc:.5f}")
    print("Compare against a single seed of the current production params —")
    print("the per-seed AUCs printed by the V55 trainer — not the 0.7500")
    print("headline, which is a 10-seed ensemble figure.")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--trials', type=int, default=40, help='Optuna trials to run (default 40)')
    p.add_argument('--sample', type=int, default=300_000,
                   help='Subsample training rows for speed; 0 = use all (default 300k)')
    p.add_argument('--val-sample', type=int, default=150_000,
                   help='Subsample validation rows; 0 = use all (default 150k)')
    p.add_argument('--split', choices=['temporal', 'random'], default='temporal',
                   help='Validation split: temporal = hold out 2023 DEV rows (default)')
    p.add_argument('--data', type=Path, default=DATA_FILE,
                   help='Path to v55_prepared_data.pkl')
    p.add_argument('--final-check', action='store_true',
                   help='Train best params on full DEV, evaluate ONCE on OOT')
    args = p.parse_args()

    if args.final_check:
        run_final_check(args)
    else:
        run_tuning(args)


if __name__ == '__main__':
    main()
