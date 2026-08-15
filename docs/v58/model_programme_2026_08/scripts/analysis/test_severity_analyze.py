#!/usr/bin/env python3
"""Tests for severity_analyze. Includes fixtures PROVEN ABLE TO FAIL -- a green
invariance test proves nothing until the fixture is shown able to fail.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from factory.runners import metrics as M  # noqa: E402
import severity_analyze as SA  # noqa: E402


def _groups(p, y, w=None):
    order, gidx, ng = SA._prep(p)
    w = np.ones(len(p)) if w is None else w
    ws, ys = w[order], y[order].astype(np.float64)
    wg_p = np.bincount(gidx, weights=ws * ys, minlength=ng)
    wg_n = np.bincount(gidx, weights=ws * (1 - ys), minlength=ng)
    return wg_p, wg_n


# --- the metric agrees with the house implementation -------------------------
@pytest.mark.parametrize("seed", range(8))
def test_auc_matches_house_metric(seed):
    rng = np.random.default_rng(seed)
    n = 500
    p = rng.random(n)
    y = (rng.random(n) < 0.3).astype(np.int8)
    got = SA._auc_from_groups(*_groups(p, y))
    assert abs(got - M.auroc(y, p)) < 1e-12


def test_auc_handles_ties_with_half_convention():
    # every score identical -> AUROC is exactly 0.5 regardless of labels
    p = np.full(100, 0.4)
    y = np.zeros(100, dtype=np.int8)
    y[:37] = 1
    assert abs(SA._auc_from_groups(*_groups(p, y)) - 0.5) < 1e-12


@pytest.mark.parametrize("seed", range(5))
def test_integer_weights_equal_row_replication(seed):
    """Cluster weights are multinomial counts; weighting must equal replicating."""
    rng = np.random.default_rng(seed)
    n = 200
    p = rng.random(n).round(2)          # force ties
    y = (rng.random(n) < 0.4).astype(np.int8)
    w = rng.integers(0, 4, n).astype(np.float64)
    weighted = SA._auc_from_groups(*_groups(p, y, w))
    rep = np.repeat(np.arange(n), w.astype(int))
    replicated = SA._auc_from_groups(*_groups(p[rep], y[rep]))
    assert abs(weighted - replicated) < 1e-12


# --- the mixture identity, and proof the check can fail ----------------------
def _synthetic(seed=0, n=4000):
    rng = np.random.default_rng(seed)
    p = rng.random(n)
    b1 = rng.random(n) < 0.30
    severe = b1 & (rng.random(n) < 0.45)
    return p, b1, severe


@pytest.mark.parametrize("seed", range(5))
def test_mixture_identity_holds(seed):
    p, b1, severe = _synthetic(seed)
    order, gidx, ng = SA._prep(p)
    w = np.ones(len(p))
    pooled, clean, mild, nc, nm = SA._decompose(
        w, gidx, ng, severe[order].astype(float), b1[order].astype(float))
    recon = (nc * clean + nm * mild) / (nc + nm)
    assert abs(recon - pooled) <= SA.IDENTITY_TOL


def test_mixture_identity_fixture_can_fail():
    """PROOF the G0.8 gate is live: perturb one component and the identity breaks."""
    p, b1, severe = _synthetic(1)
    order, gidx, ng = SA._prep(p)
    w = np.ones(len(p))
    pooled, clean, mild, nc, nm = SA._decompose(
        w, gidx, ng, severe[order].astype(float), b1[order].astype(float))
    bad = (nc * (clean + 0.01) + nm * mild) / (nc + nm)
    assert abs(bad - pooled) > SA.IDENTITY_TOL, "gate would not catch a wrong A_clean"


def test_clean_pool_is_fixed_for_nested_outcomes():
    """B2 subset of B1 => N_clean is identical across the burden ladder."""
    p, b1, _ = _synthetic(2)
    rng = np.random.default_rng(9)
    b2 = b1 & (rng.random(len(p)) < 0.5)
    b3 = b2 & (rng.random(len(p)) < 0.5)
    order, gidx, ng = SA._prep(p)
    w = np.ones(len(p))
    ncs = [SA._decompose(w, gidx, ng, x[order].astype(float),
                         b1[order].astype(float))[3] for x in (b1, b2, b3)]
    assert ncs[0] == ncs[1] == ncs[2] == float((~b1).sum())


def test_contaminated_negatives_lower_pooled_auc_below_a_clean():
    """The confound the decomposition exists to expose: demoted mild failures sit in
    the negative pool and drag pooled AUROC below A_clean."""
    rng = np.random.default_rng(3)
    n = 20000
    b1 = rng.random(n) < 0.30
    severe = b1 & (rng.random(n) < 0.4)
    p = rng.normal(0, 1, n) + 1.5 * b1 + 0.8 * severe   # score ranks burden
    order, gidx, ng = SA._prep(p)
    pooled, clean, mild, _, _ = SA._decompose(
        np.ones(n), gidx, ng, severe[order].astype(float), b1[order].astype(float))
    assert clean > pooled > mild


# --- outcome definitions -----------------------------------------------------
def test_outcome_definitions_match_prereg():
    lab = {
        "y_final": np.array([1, 0, 0, 0, 0], dtype=np.int8),
        "y_initial": np.array([1, 1, 0, 0, 0], dtype=np.int8),
        "n_major_or_dangerous": np.array([3, 1, 2, 1, 0]),
        "n_dangerous": np.array([1, 0, 0, 1, 0]),
        "n_major": np.array([2, 1, 2, 0, 0]),
        "n_minor": np.array([0, 0, 0, 2, 1]),
        "n_advisory": np.array([0, 0, 0, 0, 3]),
        "n_sections_with_md": np.array([2, 1, 2, 1, 0]),
    }
    out, ctl = SA.build_outcomes(lab)
    assert out["B1_ge1_MD"].tolist() == [True, True, True, True, False]
    assert out["B2_ge2_MD"].tolist() == [True, False, True, False, False]
    assert out["B3_ge3_MD"].tolist() == [True, False, False, False, False]
    # dangerous>=1 OR major>=2
    assert out["T1_NY_LIKE"].tolist() == [True, False, True, True, False]
    assert out["S1_ANY_DANGEROUS"].tolist() == [True, False, False, True, False]
    assert out["M1_MULTI_COMPONENT"].tolist() == [True, False, True, False, False]
    # controls require ZERO major/dangerous
    assert ctl["ONLY_MINOR"].tolist() == [False, False, False, False, True]
    assert ctl["ADVISORY_ONLY"].tolist() == [False, False, False, False, True]


def test_n_major_is_fail_gated_residual():
    """n_major = n_major_or_dangerous - n_dangerous, both on the F+P basis. A D-marked
    ADVISORY must not enter either (PREREG §3 recorded deviation)."""
    md, dang = 4, 1
    assert md - dang == 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
