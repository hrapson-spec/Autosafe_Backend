"""Unit tests for controls.py — the adversarial control battery (review pts 3, 4, 12).

CI-testable core: the control specs (kinds/status/fencing), the synthetic feature
generators' properties, the pass/fail classifier, and the battery aggregation
(required-green vs pending). The heavy on-frame run (inject -> score -> decide) lives in
validate_promotion_grade and runs on real frames.
"""
import numpy as np
from sklearn.metrics import roc_auc_score

from decision import Decision
from controls import (CONTROLS, get_control, synthetic_feature,
                      gradient_gaming_transform, classify_control_outcome, battery_summary)


def test_two_fenced_synthetic_positive_controls_exist():
    syn = {c.name for c in CONTROLS if c.kind == "positive_synthetic"}
    assert syn == {"positive_synthetic_obvious", "positive_synthetic_nearthreshold"}
    for name in syn:
        c = get_control(name)
        assert c.control_only and not c.deployable   # fenced: never a deployable candidate


def test_domain_positive_is_deployable_with_escape_hatch_states():
    c = get_control("positive_domain")
    assert c.expected == "promote" and c.deployable and not c.control_only
    assert set(c.resolution_states) == {
        "domain_control_invalid_redundant", "domain_control_replaced",
        "pipeline_injection_fault", "model_truth_age_redundant"}


def test_train_only_control_is_pending_not_enforced():
    assert get_control("negative_train_only").status == "pending_not_enforced"


def test_required_controls_have_required_status():
    for n in ["positive_synthetic_obvious", "negative_gradient_gaming", "noop", "replay"]:
        assert get_control(n).status == "required_pass"


def test_planted_obvious_signal_is_clear_but_calibratable_not_a_leak():
    # review pt 3: clearly positive, NOT absurdly strong. A near-perfect leak (AUC~1)
    # blows up calibration (ECE) and is correctly vetoed — that proves nothing. The
    # obvious control must be a clear lift the evaluator promotes through ALL gates.
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, size=4000)
    auc = roc_auc_score(y, synthetic_feature("positive_synthetic_obvious", y, rng))
    assert 0.72 < auc < 0.88


def test_planted_nearthreshold_is_weak_but_real_signal():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, size=6000)
    auc = roc_auc_score(y, synthetic_feature("positive_synthetic_nearthreshold", y, rng))
    assert 0.52 < auc < 0.72   # real lift, not a sledgehammer


def test_noop_feature_is_constant():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, size=200)
    assert len(set(np.asarray(synthetic_feature("noop", y, rng)).tolist())) == 1


def test_gradient_gaming_transform_is_monotone_no_new_ordering():
    vals = np.array([1.0, 5.0, 2.0, 9.0, 3.0])
    t = np.asarray(gradient_gaming_transform(vals))
    assert list(np.argsort(t)) == list(np.argsort(vals))   # rank-preserving (no new info)


def _dec(verdict, promotable):
    return Decision(verdict=verdict, promotable=promotable, seed_direction="x", reasons={})


def test_classify_control_outcome():
    assert classify_control_outcome(get_control("positive_domain"), _dec("promote", True)) == "pass"
    assert classify_control_outcome(get_control("positive_domain"), _dec("dead", False)) == "fail"
    assert classify_control_outcome(get_control("noop"), _dec("dead", False)) == "pass"
    assert classify_control_outcome(get_control("noop"), _dec("promote", True)) == "fail"
    # gradient-gaming must be rejected (not promotable); promoting it is a failure
    assert classify_control_outcome(get_control("negative_gradient_gaming"), _dec("promote", True)) == "fail"
    assert classify_control_outcome(get_control("negative_gradient_gaming"), _dec("discard", False)) == "pass"


def test_battery_summary_green_with_pending_listed_separately():
    results = {"positive_synthetic_obvious": "pass", "positive_synthetic_nearthreshold": "pass",
               "positive_domain": "pass", "negative_gradient_gaming": "pass", "noop": "pass",
               "replay": "pass", "negative_train_only": "pending"}
    s = battery_summary(results)
    assert s["required_control_battery_green"] is True
    assert s["pending_controls"] == ["negative_train_only"]


def test_battery_not_green_if_a_required_control_fails():
    results = {"positive_synthetic_obvious": "pass", "negative_gradient_gaming": "fail",
               "noop": "pass", "replay": "pass"}
    assert battery_summary(results)["required_control_battery_green"] is False


def test_battery_pending_control_does_not_block_green():
    # a scaffolded (pending) control is reported, never counted as a pass or a fail
    results = {"positive_synthetic_obvious": "pass", "positive_synthetic_nearthreshold": "pass",
               "positive_domain": "pass", "negative_gradient_gaming": "pass", "noop": "pass",
               "replay": "pass", "negative_train_only": "pending"}
    assert battery_summary(results)["required_control_battery_green"] is True
