"""Locks the train/serve parity invariants (audit item #2).

Runs the GF-17 parity harness in-process against the served v55 feature
contract. Requires numpy (pulled by feature_engineering_v55) — available in CI;
skipped automatically where it is not. The catboost model.cbm schema check is
exercised only when catboost is importable.
"""

import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

np = pytest.importorskip("numpy")  # harness imports feature_engineering_v55 → numpy

from gf17_train_serve_parity import (  # noqa: E402
    KNOWN_DEAD_VOCAB,
    MODEL_DIR,
    build_fixtures,
    check_model_binary,
    run_parity,
)
from feature_engineering_v55 import FEATURE_NAMES  # noqa: E402
from model_bundle import RC3_DROPPED_FEATURES  # noqa: E402


def test_fixtures_cover_distinct_cohorts():
    names = [n for n, _, _ in build_fixtures()]
    assert "no_history" in names  # first-test / no-prior cohort present
    assert len(names) == len(set(names))
    assert len(names) >= 8


def test_serving_emits_every_contract_feature():
    rep = run_parity(model_path=None)
    assert rep.missing_from_serving == [], (
        f"serving does not emit: {rep.missing_from_serving}"
    )
    assert rep.emitted_feature_count >= len(FEATURE_NAMES)


def test_rc3_features_confirmed_served_as_constant():
    # The four RC-3 features are trained live but served as hardcoded constants.
    # This pins that known defect: if a v55 boundary fix makes one dynamic, this
    # test fails and the baseline must be consciously updated.
    rep = run_parity(model_path=None)
    assert set(rep.rc3_constant_confirmed) == set(RC3_DROPPED_FEATURES)


def test_default_mode_passes_for_known_v55_state():
    # v55 audit mode: only the documented known-broken set is tolerated, so a
    # NEW parity regression (e.g. an out-of-vocab categorical on a feature that
    # is supposed to be live) would flip this to a failure.
    rep = run_parity(model_path=None)
    assert rep.ok, f"unexpected parity violations: {rep.violations}"


def test_shimmed_categoricals_are_in_training_vocab():
    # prev_cycle_outcome_band / advisory_trend must land in the deployed vocab
    # after the shim — any OOV here would be a real (non-known-dead) regression.
    rep = run_parity(model_path=None)
    new_oov = [v for v in rep.vocab_violations if v[0] not in KNOWN_DEAD_VOCAB]
    assert new_oov == [], f"new out-of-vocab categorical emissions: {new_oov}"


def test_expect_fixed_fails_against_v55():
    # v57 gate semantics: against the v55 serving contract (RC-3 still served
    # constant, dead vocab still present) --expect-fixed MUST fail.
    rep = run_parity(model_path=None, expect_fixed=True)
    assert not rep.ok
    assert any("rc3" in v for v in rep.violations)


def test_model_binary_schema_matches_serving_when_catboost_available():
    pytest.importorskip("catboost")
    if not (MODEL_DIR / "model.cbm").exists():
        pytest.skip("model.cbm not present")
    mc = check_model_binary(list(FEATURE_NAMES), MODEL_DIR / "model.cbm")
    # The deployed binary embeds exactly the 104 serving names in order
    # (work/legacy_v55/README.md). A regression here is the 104-vs-107 class.
    assert mc["status"] == "ok", mc
