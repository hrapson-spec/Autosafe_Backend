"""
AutoSafe Experiment Harness
===========================

Runs one experiment against the V55 prepared data with a fixed, disciplined
scoring protocol, and appends the result to experiments/results.jsonl.

An "experiment" is a Python file in experiments/ that defines:

    NAME = "exp_001_short_name"
    HYPOTHESIS = "One sentence: what change, and why it should help."

    def apply(ctx):
        # Modify the training context and return it. ctx keys:
        #   X_train, y_train, w_train  - training rows (pre-2023 DEV)
        #   X_val, y_val               - validation rows (2023 DEV holdout)
        #   params                     - CatBoost params dict (may edit)
        #   cat_features               - list of categorical column names
        #                                (append to it if you add a cat column)
        # RULES (see experiments/RESEARCH.md):
        #   - Any column transform must be applied to BOTH X_train and X_val.
        #   - Never fit anything on X_val/y_val (no target stats from val!).
        #   - New features may only be derived from existing columns.
        return ctx

Scoring: trains N seeds (default 3) with early stopping on the validation
set, reports the seed-ensemble validation AUC (mean of the seeds'
predicted probabilities — matches how production should serve). The OOT
set is never touched here.

Usage (on the machine with ~/autosafe_work):
    python experiments/harness.py experiments/exp_000_baseline.py
    python experiments/harness.py experiments/exp_001_smoother_year_weights.py

Keep --sample/--val-sample/--seeds identical across a campaign so scores
are comparable; the row subsample is drawn with a fixed RNG seed, so every
experiment sees the same data.
"""

import argparse
import importlib.util
import json
import pickle
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score, brier_score_loss
from catboost import CatBoostClassifier, Pool

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_FILE = Path(__file__).resolve().parent / "results.jsonl"
DATA_FILE = Path.home() / "autosafe_work/v55_prepared_data.pkl"
EARLY_STOPPING_ROUNDS = 150

# Production params (train_catboost_production_v55.py, set at V17).
# If Optuna tuning (tune_catboost_v55.py) finds better ones, update here
# and re-run exp_000_baseline so future deltas measure against the new floor.
DEFAULT_PARAMS = {
    'learning_rate': 0.02,
    'depth': 6,
    'l2_leaf_reg': 4.0,
    'border_count': 128,
    'random_strength': 1.0,
    'bagging_temperature': 0.5,
}

# An experiment must beat the baseline by at least this much (ensemble val
# AUC) to be a KEEP candidate. Guards against keeping seed/sampling noise.
KEEP_THRESHOLD = 0.001


def load_experiment(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for attr in ('NAME', 'HYPOTHESIS', 'apply'):
        if not hasattr(mod, attr):
            sys.exit(f"{path} must define {attr}")
    return mod


def build_context(args, rng):
    if not args.data.exists():
        sys.exit(f"Prepared data not found: {args.data}\n"
                 "Run train_catboost_production_v55.py once first (step [9b] "
                 "saves the matrices this harness needs).")
    with open(args.data, 'rb') as f:
        data = pickle.load(f)

    X, y, w = data['X_train'], data['y_train'], np.asarray(data['train_weights'])

    val_mask = w == 20.0  # weight 20.0 marks the 2023 DEV rows
    if not val_mask.any():
        sys.exit("No weight-20.0 (2023) rows found in prepared data — "
                 "cannot build the temporal validation split.")
    tr_idx, val_idx = np.where(~val_mask)[0], np.where(val_mask)[0]

    if args.sample and args.sample < len(tr_idx):
        tr_idx = rng.choice(tr_idx, size=args.sample, replace=False)
    if args.val_sample and args.val_sample < len(val_idx):
        val_idx = rng.choice(val_idx, size=args.val_sample, replace=False)

    return {
        'X_train': X.iloc[tr_idx].copy(),
        'y_train': y.iloc[tr_idx].copy(),
        'w_train': w[tr_idx],
        'X_val': X.iloc[val_idx].copy(),
        'y_val': y.iloc[val_idx].copy(),
        'params': dict(DEFAULT_PARAMS),
        'cat_features': list(data['cat_features']),
    }


def train_and_score(ctx, args):
    cols = list(ctx['X_train'].columns)
    if list(ctx['X_val'].columns) != cols:
        sys.exit("Experiment broke the rules: X_train and X_val have "
                 "different columns. Apply every transform to both.")
    cat_idx = [cols.index(c) for c in ctx['cat_features'] if c in cols]

    train_pool = Pool(ctx['X_train'], ctx['y_train'],
                      cat_features=cat_idx, weight=ctx['w_train'])
    val_pool = Pool(ctx['X_val'], ctx['y_val'], cat_features=cat_idx)

    seed_aucs, val_preds = [], []
    for seed in range(args.seeds):
        model = CatBoostClassifier(
            iterations=args.max_iterations,
            eval_metric='AUC',
            cat_features=cat_idx,
            random_seed=seed,
            verbose=0,
            thread_count=-1,
            use_best_model=True,
            **ctx['params'],
        )
        model.fit(train_pool, eval_set=val_pool,
                  early_stopping_rounds=EARLY_STOPPING_ROUNDS, verbose=0)
        pred = model.predict_proba(ctx['X_val'])[:, 1]
        auc = roc_auc_score(ctx['y_val'], pred)
        seed_aucs.append(auc)
        val_preds.append(pred)
        print(f"  seed {seed}: val AUC {auc:.5f} "
              f"({model.get_best_iteration()} trees)")

    ens_pred = np.mean(val_preds, axis=0)
    return {
        'ensemble_auc': float(roc_auc_score(ctx['y_val'], ens_pred)),
        'ensemble_brier': float(brier_score_loss(ctx['y_val'], ens_pred)),
        'seed_aucs': [float(a) for a in seed_aucs],
        'mean_seed_auc': float(np.mean(seed_aucs)),
        'n_features': len(cols),
    }


def config_fingerprint(args):
    return {'sample': args.sample, 'val_sample': args.val_sample,
            'seeds': args.seeds, 'max_iterations': args.max_iterations}


def latest_baseline(fingerprint):
    if not RESULTS_FILE.exists():
        return None
    baseline = None
    for line in RESULTS_FILE.read_text().splitlines():
        rec = json.loads(line)
        if rec['name'].startswith('exp_000') and rec['config'] == fingerprint:
            baseline = rec
    return baseline


def git_commit_hash():
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                              capture_output=True, text=True,
                              cwd=REPO_ROOT, check=True).stdout.strip()
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('experiment', type=Path, help='Path to experiment .py file')
    p.add_argument('--sample', type=int, default=300_000,
                   help='Training rows (0 = all pre-2023 DEV; default 300k)')
    p.add_argument('--val-sample', type=int, default=150_000,
                   help='Validation rows (0 = all 2023 DEV; default 150k)')
    p.add_argument('--seeds', type=int, default=3,
                   help='Seeds per experiment (default 3)')
    p.add_argument('--max-iterations', type=int, default=4000,
                   help='Iteration ceiling; early stopping picks the count')
    p.add_argument('--data', type=Path, default=DATA_FILE)
    args = p.parse_args()

    exp = load_experiment(args.experiment)
    print(f"\n=== {exp.NAME} ===")
    print(f"Hypothesis: {exp.HYPOTHESIS}\n")

    rng = np.random.default_rng(42)  # fixed: every experiment sees the same rows
    ctx = build_context(args, rng)
    print(f"Train: {len(ctx['X_train']):,} rows  "
          f"Valid: {len(ctx['X_val']):,} rows (2023 DEV holdout)")

    ctx = exp.apply(ctx)
    scores = train_and_score(ctx, args)

    fingerprint = config_fingerprint(args)
    baseline = latest_baseline(fingerprint)
    is_baseline = exp.NAME.startswith('exp_000')

    record = {
        'timestamp': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'name': exp.NAME,
        'hypothesis': exp.HYPOTHESIS,
        'config': fingerprint,
        'params': ctx['params'],
        'git': git_commit_hash(),
        **scores,
    }

    print(f"\nEnsemble val AUC:   {scores['ensemble_auc']:.5f}")
    print(f"Ensemble val Brier: {scores['ensemble_brier']:.5f}")
    print(f"Per-seed AUC:       {scores['mean_seed_auc']:.5f} "
          f"(range {min(scores['seed_aucs']):.5f}-{max(scores['seed_aucs']):.5f})")

    if is_baseline:
        record['verdict'] = 'BASELINE'
        print("\nVerdict: BASELINE recorded.")
    elif baseline is None:
        record['verdict'] = 'NO_BASELINE'
        print("\nWARNING: no baseline with this config in results.jsonl — "
              "run exp_000_baseline.py with the same flags first.")
    else:
        delta = scores['ensemble_auc'] - baseline['ensemble_auc']
        record['delta_vs_baseline'] = round(delta, 6)
        record['verdict'] = 'KEEP' if delta >= KEEP_THRESHOLD else 'REVERT'
        print(f"\nDelta vs baseline ({baseline['ensemble_auc']:.5f}): "
              f"{delta:+.5f}  ->  {record['verdict']}"
              f"  (threshold {KEEP_THRESHOLD:+.4f})")

    with open(RESULTS_FILE, 'a') as f:
        f.write(json.dumps(record) + '\n')
    print(f"Logged to {RESULTS_FILE.relative_to(REPO_ROOT)}")


if __name__ == '__main__':
    main()
