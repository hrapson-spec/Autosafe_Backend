"""Unit tests for the GF-17 Phase A remediation:

A2 — vocab_shim.apply_vocab_shim (serving categoricals -> training vocab at
     the model boundary), and
A1 — days_since_pass_ratio computed from days_since_last_test (was hardcoded
     1.0) in feature_engineering_v55.engineer_features.

CI-safe: no model artifacts or large data loaded.
"""
from datetime import datetime, timedelta

import pytest

from vocab_shim import VOCAB_MAP, TRAINING_VOCAB, apply_vocab_shim
from dvsa_client import MOTTest, VehicleHistory
from feature_engineering_v55 import engineer_features


# ---------------------------------------------------------------- A2: shim

def test_prev_cycle_outcome_band_mapping():
    for serve, train in [('pass', 'PASS'), ('fail', 'FAIL'),
                         ('first_test', 'FIRST_TEST'),
                         ('unknown', 'HISTORY_MISSING_LINK')]:
        out = apply_vocab_shim({'prev_cycle_outcome_band': serve})
        assert out['prev_cycle_outcome_band'] == train


def test_advisory_trend_mapping_direction():
    # increasing advisories = worsening condition; decreasing = improving.
    # Direction validated empirically against the training matrix in
    # gf17_phaseA_verification (fail rate WORSENING > IMPROVING).
    for serve, train in [('stable', 'STABLE'), ('unknown', 'UNKNOWN'),
                         ('increasing', 'WORSENING'),
                         ('decreasing', 'IMPROVING')]:
        out = apply_vocab_shim({'advisory_trend': serve})
        assert out['advisory_trend'] == train


def test_dominant_mechanism_deliberately_unmapped():
    # The CLEAN->NO_HISTORY mapping was tested and REJECTED on evidence
    # (gf17_phaseA_verification run 1: fresh-panel AUC 0.6876->0.6755,
    # ECE 0.0082->0.0121). dominant_mechanism stays out-of-vocabulary (dead)
    # until the v57 retrain; this test pins that decision.
    assert 'dominant_mechanism' not in VOCAB_MAP
    out = apply_vocab_shim({'dominant_mechanism': 'CLEAN',
                            'has_advisory_history': 0})
    assert out['dominant_mechanism'] == 'CLEAN'


def test_dominant_mechanism_real_mechanisms_pass_through():
    for v in ('WEAR', 'DAMAGE', 'CORROSION', 'LEAK', 'NO_HISTORY'):
        assert apply_vocab_shim({'dominant_mechanism': v})['dominant_mechanism'] == v


def test_unmapped_features_pass_through():
    # gap_band / usage_band_hybrid deliberately have NO shim (different
    # concepts/edges; 'high' is a false friend) — they must pass unchanged.
    f = {'gap_band': 'on_time', 'usage_band_hybrid': 'average',
         'make': 'FORD', 'test_month': '6', 'negligence_band': 'clean'}
    assert apply_vocab_shim(f) == f


def test_input_not_mutated_and_copy_returned():
    f = {'prev_cycle_outcome_band': 'pass'}
    out = apply_vocab_shim(f)
    assert f['prev_cycle_outcome_band'] == 'pass'
    assert out is not f


def test_idempotent():
    f = {'prev_cycle_outcome_band': 'pass', 'advisory_trend': 'increasing',
         'dominant_mechanism': 'CLEAN'}
    once = apply_vocab_shim(f)
    assert apply_vocab_shim(once) == once


def test_shimmed_output_within_training_vocab():
    # Every serving-reachable value (GF-17 probe battery + fresh panel) for
    # the two shimmed features must land inside the training vocabulary.
    serving_reachable = {
        'prev_cycle_outcome_band': ['pass', 'fail', 'first_test', 'unknown'],
        'advisory_trend': ['stable', 'increasing', 'decreasing', 'unknown'],
    }
    for feat, values in serving_reachable.items():
        for v in values:
            out = apply_vocab_shim({feat: v})
            assert out[feat] in TRAINING_VOCAB[feat], (feat, v, out[feat])


def test_vocab_map_targets_are_training_vocab():
    for feat, mapping in VOCAB_MAP.items():
        for target in mapping.values():
            assert target in TRAINING_VOCAB[feat], (feat, target)


# ---------------------------------------------------------------- A1: ratio

def _vehicle(tests):
    return VehicleHistory(
        registration='TESTA1', make='FORD', model='FIESTA',
        fuel_type='Petrol', colour='Blue',
        registration_date=datetime(2015, 3, 15),
        manufacture_date=datetime(2015, 3, 15),
        engine_size=1242, mot_tests=tests)


def _mot(test_date, odo):
    return MOTTest(test_date=test_date, test_result='PASSED',
                   expiry_date=test_date + timedelta(days=365),
                   odometer_value=odo, odometer_unit='mi',
                   test_number=f'T{odo}', defects=[])


@pytest.mark.parametrize('gap_days', [100, 300, 365, 500])
def test_days_since_pass_ratio_is_gap_over_365(gap_days):
    newest = datetime(2025, 6, 1)
    feats = engineer_features(
        _vehicle([_mot(newest, 45000),
                  _mot(newest - timedelta(days=gap_days), 40000)]),
        'SW1A 1AA', datetime(2026, 6, 10))
    assert feats['days_since_last_test'] == gap_days
    assert feats['days_since_pass_ratio'] == pytest.approx(gap_days / 365.0)


def test_days_since_pass_ratio_single_test_is_zero():
    # Residual default mismatch vs training fill (730/365=2.0) is documented
    # in the GF-17 default contract; closes with v57.
    feats = engineer_features(_vehicle([_mot(datetime(2025, 6, 1), 35000)]),
                              'SW1A 1AA', datetime(2026, 6, 10))
    assert feats['days_since_pass_ratio'] == 0.0
