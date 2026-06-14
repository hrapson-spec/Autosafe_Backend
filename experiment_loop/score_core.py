"""score_core.py — the ONE shared train/score path. PROTECTED referee module.

Extracted verbatim (behaviour-preserving) from evaluate.py's `train` / scoring loop /
`leakage_drop_pp`, but with dependency injection: params, cat_features and split are
passed in rather than imported from config. Both the dev-grade scorer (evaluate.py) and
the promotion CLI call THIS — there is exactly one implementation of training and
scoring, so the two grades can never silently diverge (review pt 13).

Deltas are returned in PERCENTAGE POINTS (ΔAUC × 100), the unit decision.py expects.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import roc_auc_score


@dataclass
class ScoreResult:
    deltas_pp: list          # per-seed (cand_auc - base_auc) * 100
    base_proba: np.ndarray   # seed-mean OOT base probability
    cand_proba: np.ndarray   # seed-mean OOT candidate probability
    last_model: object       # last candidate model (for the leakage ablation)
    full_cols: list


def train(dev, cols, seed, *, params, cat_features, split, label_col="y"):
    from catboost import CatBoostClassifier, Pool
    from sklearn.model_selection import train_test_split
    cat_idx = [cols.index(c) for c in cat_features if c in cols]
    tr, va = train_test_split(dev, test_size=split["test_size"],
                              random_state=split["random_state"], stratify=dev[label_col])
    m = CatBoostClassifier(**params, random_seed=seed, verbose=0,
                           cat_features=cat_idx, eval_metric="AUC")
    m.fit(Pool(tr[cols], tr[label_col], cat_features=cat_idx),
          eval_set=Pool(va[cols], va[label_col], cat_features=cat_idx),
          early_stopping_rounds=150)
    return m


def score_candidate(dev, oot, base_cols, cand_cols, *, seeds, params,
                    cat_features, split, label_col="y") -> ScoreResult:
    """Train base vs base+candidate for each seed; return per-seed ΔAUC (pp) and the
    seed-mean OOT probabilities."""
    full_cols = list(base_cols) + list(cand_cols)
    y_oot = oot[label_col].values
    base_p, cand_p, deltas = [], [], []
    last_model = None
    for s in seeds:
        mb = train(dev, base_cols, s, params=params, cat_features=cat_features,
                   split=split, label_col=label_col)
        mc = train(dev, full_cols, s, params=params, cat_features=cat_features,
                   split=split, label_col=label_col)
        pb = mb.predict_proba(oot[base_cols])[:, 1]
        pc = mc.predict_proba(oot[full_cols])[:, 1]
        base_p.append(pb)
        cand_p.append(pc)
        last_model = mc
        deltas.append((roc_auc_score(y_oot, pc) - roc_auc_score(y_oot, pb)) * 100.0)
    return ScoreResult(deltas_pp=deltas, base_proba=np.mean(base_p, axis=0),
                       cand_proba=np.mean(cand_p, axis=0), last_model=last_model,
                       full_cols=full_cols)


def leakage_drop_pp(model, oot, cols, cand_cols, seed, *, label_col="y") -> float:
    """AUC drop (pp) when the candidate columns are shuffled within OOT — a ~0 drop
    means the model does not actually use the candidate (→ 'dead')."""
    y = oot[label_col].values
    base = roc_auc_score(y, model.predict_proba(oot[cols])[:, 1])
    rng = np.random.default_rng(seed)
    shuf = oot.copy()
    for c in cand_cols:
        shuf[c] = rng.permutation(shuf[c].values)
    perm = roc_auc_score(y, model.predict_proba(shuf[cols])[:, 1])
    return (base - perm) * 100.0
