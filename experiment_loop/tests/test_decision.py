"""Unit tests for decision.py — the seed-direction classification (F3 fix) and the
pure promotion decision. No harness, no data: synthetic deltas only (test pyramid L1).

F3 regression: `seed_stable = all(d>0) or all(d<0)` treated consistently-harmful as
"stable" and could KEEP a pooled-negative feature. The fix models direction as a
4-way classification; promotion requires `stable_positive`, and `stable_negative`
(consistently harmful) is preserved as its own class and never promotes.

GF-8 / gradient-gaming: promotion REQUIRES within-segment wins (an AND, not an OR with
pooled) — a pooled-only gain with flat within-segment slices must NOT promote.
"""
import math

import pytest

from decision import classify_seed_direction, decide

DZ = 0.05  # seed_dead_zone_pp
THRESH = {
    "pooled_d_auc_min_pp": 0.30,
    "median_seed_d_auc_min_pp": 0.10,
    "within_segment_min_slices": 2,
    "leakage_min_auc_drop_pp": 0.10,
}


def _decide(**kw):
    """Defaults represent a clean promote; tests override one field to probe a gate."""
    base = dict(seed_direction="stable_positive", pooled_d_auc_pp=0.4,
                median_seed_d_auc_pp=0.3, within_segment_wins=3, ece_breach=False,
                leakage_drop_pp=0.5, thresholds=THRESH)
    base.update(kw)
    return decide(**base)


# --- classify_seed_direction (deltas are per-seed ΔAUC in percentage points) ----
def test_all_positive_beyond_deadzone_is_stable_positive():
    assert classify_seed_direction([0.5, 0.3], DZ) == "stable_positive"


def test_all_negative_beyond_deadzone_is_stable_negative():
    # F3: consistently harmful is its OWN class (not "unstable"), and never promotes
    assert classify_seed_direction([-0.69, -0.25], DZ) == "stable_negative"


def test_mixed_signs_is_mixed_unstable():
    # the real 2026-06-14 30K run: seed0 -0.690pp, seed1 +0.097pp
    assert classify_seed_direction([-0.69, 0.097], DZ) == "mixed_unstable"


def test_all_within_deadzone_is_flat_or_noise():
    assert classify_seed_direction([0.01, -0.02], DZ) == "flat_or_noise"


def test_exactly_zero_is_flat_or_noise():
    assert classify_seed_direction([0.0, 0.0], DZ) == "flat_or_noise"


def test_exactly_at_deadzone_boundary_is_flat():
    # dead-zone is inclusive: |d| == dz counts as flat, not directional
    assert classify_seed_direction([0.05, 0.05], DZ) == "flat_or_noise"


def test_one_positive_rest_flat_is_mixed_unstable():
    assert classify_seed_direction([0.5, 0.01, 0.01, 0.01, 0.01], DZ) == "mixed_unstable"


def test_four_positive_one_flat_is_mixed_unstable():
    # strict (ratified): ALL seeds must clear the dead-zone for stable_positive
    assert classify_seed_direction([0.5, 0.5, 0.5, 0.5, 0.01], DZ) == "mixed_unstable"


def test_nan_delta_raises():
    with pytest.raises(ValueError):
        classify_seed_direction([0.5, math.nan], DZ)


def test_empty_deltas_raises():
    with pytest.raises(ValueError):
        classify_seed_direction([], DZ)


# --- decide (pure promotion decision over scalar summaries) ----------------------
def test_clean_stable_positive_promotes():
    d = _decide()
    assert d.promotable and d.verdict == "promote"


def test_stable_negative_never_promotes_and_is_dead_when_unused():
    # F3 regression guard: consistently harmful + ~0 leakage => dead, NOT keep
    d = _decide(seed_direction="stable_negative", pooled_d_auc_pp=-0.28,
                median_seed_d_auc_pp=-0.30, within_segment_wins=0, leakage_drop_pp=-0.01)
    assert not d.promotable and d.verdict == "dead"


def test_pooled_only_gain_with_no_within_segment_wins_does_not_promote():
    # GF-8 / gradient-gaming: pooled up, within-segment flat => REJECT
    d = _decide(pooled_d_auc_pp=0.6, within_segment_wins=0)
    assert not d.promotable


def test_stable_positive_but_pooled_below_min_does_not_promote():
    d = _decide(pooled_d_auc_pp=0.10)
    assert not d.promotable


def test_stable_positive_but_median_below_min_does_not_promote():
    d = _decide(median_seed_d_auc_pp=0.05)
    assert not d.promotable


def test_ece_breach_blocks_promotion_despite_auc_gain():
    d = _decide(ece_breach=True)
    assert not d.promotable


def test_not_promotable_with_real_leakage_drop_is_discard():
    d = _decide(seed_direction="mixed_unstable", pooled_d_auc_pp=0.0,
                median_seed_d_auc_pp=0.0, within_segment_wins=0, leakage_drop_pp=0.5)
    assert d.verdict == "discard"
