"""Tests for score_core.py — the single shared train/score path (review pt 13).

Exercised on tiny synthetic data (fast, runs in CI since catboost is a prod dep) so
the base-vs-base+candidate delta computation is verified without the heavy v57 frames.
"""
import numpy as np
import pandas as pd

from score_core import score_candidate

TINY = {"iterations": 60, "depth": 3, "learning_rate": 0.2, "l2_leaf_reg": 2}
SPLIT = {"test_size": 0.25, "random_state": 42}


def _data(n=600, signal=True, seed=0):
    rng = np.random.default_rng(seed)
    xb = rng.normal(size=n)
    y = (xb + rng.normal(size=n) > 0).astype(int)
    cand = (y.astype(float) * 2 - 1) if signal else np.zeros(n)  # planted signal vs constant
    return pd.DataFrame({"x_base": xb, "x_cand": cand, "y": y})


def test_strong_signal_candidate_yields_clearly_positive_delta():
    dev, oot = _data(600, True, 0), _data(600, True, 1)
    r = score_candidate(dev, oot, ["x_base"], ["x_cand"], seeds=[0, 1],
                        params=TINY, cat_features=[], split=SPLIT)
    assert np.mean(r.deltas_pp) > 0.5
    assert len(r.deltas_pp) == 2


def test_constant_candidate_yields_near_zero_delta():
    dev, oot = _data(600, False, 0), _data(600, False, 1)
    r = score_candidate(dev, oot, ["x_base"], ["x_cand"], seeds=[0, 1],
                        params=TINY, cat_features=[], split=SPLIT)
    assert abs(np.mean(r.deltas_pp)) < 0.5


def test_score_result_exposes_seed_mean_probabilities_for_oot():
    dev, oot = _data(400, True, 0), _data(400, True, 1)
    r = score_candidate(dev, oot, ["x_base"], ["x_cand"], seeds=[0],
                        params=TINY, cat_features=[], split=SPLIT)
    assert r.base_proba.shape == (len(oot),)
    assert r.cand_proba.shape == (len(oot),)
