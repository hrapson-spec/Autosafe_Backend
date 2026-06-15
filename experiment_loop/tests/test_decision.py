"""Unit tests for decision.py — seed-direction diagnostic, the mean-ΔAUC CI statistic,
the promotion decision, and the safe report reduction. Synthetic inputs only (L1).

The promotion gate is a bootstrap CI on the MEAN per-seed ΔAUC: promote only when the CI
LOWER bound clears the bar. This replaces the old all-seeds `stable_positive` rule, which
got *harder* with more seeds and couldn't promote a marginal signal. The CI tightens with
more seeds/data (the power lever), and correctly rejects a chance-positive noise feature
whose CI is wide. `classify_seed_direction` is kept as a recorded diagnostic (F3: a
consistently-harmful feature is its own class) but is no longer the gate.
"""
import math

import pytest

from decision import (classify_seed_direction, mean_delta_ci, decide, summarize_report)

DZ = 0.05
THRESH = {
    "pooled_d_auc_min_pp": 0.30,
    "within_segment_min_slices": 2,
    "leakage_min_auc_drop_pp": 0.10,
    "ci_alpha": 0.05,
    "seed_dead_zone_pp": 0.05,
}


def _decide(**kw):
    base = dict(deltas_pp=[1.0, 1.1, 0.9, 1.05, 0.95], within_segment_wins=3,
                ece_breach=False, leakage_drop_pp=0.5, thresholds=THRESH)
    base.update(kw)
    return decide(**base)


# --- classify_seed_direction (diagnostic; F3 class preserved) --------------------
def test_all_positive_beyond_deadzone_is_stable_positive():
    assert classify_seed_direction([0.5, 0.3], DZ) == "stable_positive"


def test_all_negative_beyond_deadzone_is_stable_negative():
    assert classify_seed_direction([-0.69, -0.25], DZ) == "stable_negative"


def test_mixed_signs_is_mixed_unstable():
    assert classify_seed_direction([-0.69, 0.097], DZ) == "mixed_unstable"


def test_all_within_deadzone_is_flat_or_noise():
    assert classify_seed_direction([0.01, -0.02], DZ) == "flat_or_noise"


def test_exactly_at_deadzone_boundary_is_flat():
    assert classify_seed_direction([0.05, 0.05], DZ) == "flat_or_noise"


def test_nan_delta_raises():
    with pytest.raises(ValueError):
        classify_seed_direction([0.5, math.nan], DZ)


# --- mean_delta_ci ---------------------------------------------------------------
def test_ci_is_deterministic():
    assert mean_delta_ci([0.4, 0.5, 0.6], 0.05) == mean_delta_ci([0.4, 0.5, 0.6], 0.05)


def test_tight_deltas_give_a_narrow_ci_around_the_mean():
    lo, mean, hi = mean_delta_ci([1.0, 1.0, 1.0, 1.0, 1.0], 0.05)
    assert lo == mean == hi == 1.0


def test_wide_deltas_give_a_ci_that_can_drop_below_a_positive_mean():
    lo, mean, hi = mean_delta_ci([2.0, 0.1, 1.9, -0.3, 1.5], 0.05)
    assert lo < mean < hi and lo < 0.30


def test_chance_positive_noise_has_a_ci_lower_below_the_bar():
    # the real n=100K NOISE feature: mean +0.32 by luck, but wide -> CI lower below 0.30pp
    lo, mean, hi = mean_delta_ci([0.657, 0.342, -0.242, 0.611, 0.226], 0.05)
    assert mean > 0.30 and lo < 0.30


def test_ci_raises_on_empty_or_nan():
    with pytest.raises(ValueError):
        mean_delta_ci([], 0.05)
    with pytest.raises(ValueError):
        mean_delta_ci([0.5, math.nan], 0.05)


# --- decide (gate on CI-lower > bar, AND within-segment, AND no ECE) --------------
def test_clean_signal_well_above_bar_promotes():
    assert _decide().verdict == "promote" and _decide().promotable


def test_consistently_harmful_never_promotes_and_is_dead_when_unused():
    # F3 regression via the CI: all-negative -> CI lower far below the bar
    d = _decide(deltas_pp=[-0.5, -0.6, -0.4, -0.55, -0.45], within_segment_wins=0,
                leakage_drop_pp=-0.01)
    assert not d.promotable and d.verdict == "dead"
    assert d.seed_direction == "stable_negative"   # diagnostic still recorded


def test_chance_positive_noise_is_not_promoted():
    # the n=100K noise feature: positive mean but wide CI -> rejected (no false positive)
    d = _decide(deltas_pp=[0.657, 0.342, -0.242, 0.611, 0.226])
    assert not d.promotable


def test_high_but_noisy_mean_is_not_promoted_underpowered():
    d = _decide(deltas_pp=[2.0, 0.1, 1.9, -0.3, 1.5])   # mean ~1.0 but CI lower < bar
    assert not d.promotable


def test_pooled_only_gain_with_no_within_segment_wins_does_not_promote():
    assert not _decide(within_segment_wins=0).promotable        # GF-8 defense retained


def test_ece_breach_blocks_promotion():
    assert not _decide(ece_breach=True).promotable


def test_not_promotable_with_real_leakage_drop_is_discard():
    d = _decide(deltas_pp=[0.0, 0.0, 0.0, 0.0, 0.0], within_segment_wins=0, leakage_drop_pp=0.5)
    assert d.verdict == "discard"


# --- summarize_report (NaN/missing/flat-safe reduction) --------------------------
def test_summarize_ignores_unavailable_and_too_small_slices():
    report = {"slices": {"a": {"d_auc_pp": 0.5, "d_ece": 0.0},
                         "u": {"status": "unavailable_on_frame"},
                         "t": {"status": "too_small", "n": 10}},
              "pooled": {"d_auc_pp": 0.4}}
    s = summarize_report(report)
    assert s["within_wins"] == ["a"] and s["worst_d_ece"] == 0.0 and s["pooled_d_auc_pp"] == 0.4


def test_summarize_flat_slice_is_not_a_win():
    assert summarize_report({"slices": {"a": {"d_auc_pp": 0.0, "d_ece": 0.0}}, "pooled": {}})["within_wins"] == []


def test_summarize_nan_auc_delta_is_not_a_win():
    report = {"slices": {"a": {"d_auc_pp": float("nan"), "d_ece": 0.0},
                         "b": {"d_auc_pp": 0.3, "d_ece": 0.0}}, "pooled": {}}
    assert summarize_report(report)["within_wins"] == ["b"]


def test_summarize_nan_ece_forces_a_breach_fail_safe():
    report = {"slices": {"a": {"d_auc_pp": 0.5, "d_ece": float("nan")}}, "pooled": {}}
    assert summarize_report(report)["worst_d_ece"] == float("inf")


def test_summarize_missing_or_nan_pooled_is_zero():
    assert summarize_report({"slices": {}})["pooled_d_auc_pp"] == 0.0
