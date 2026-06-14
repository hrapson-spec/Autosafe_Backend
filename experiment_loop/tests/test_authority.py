"""Tests for the gate-vs-authority decision (review pt 2).

`make test-evaluator-gates` (machinery) must pass for the PR; `validate-promotion-authority`
MAY lock safely if the domain positive is genuinely redundant. authority_decision encodes
that split: a synthetic-positive failure means the evaluator is broken; a domain failure
(with synthetics passing) locks pending a ratified resolution state, NOT "evaluator broken".
"""
from validate_promotion_grade import authority_decision


def _all_pass():
    return {"positive_synthetic_obvious": "pass", "positive_synthetic_nearthreshold": "pass",
            "positive_domain": "pass", "negative_gradient_gaming": "pass", "noop": "pass",
            "replay": "pass", "negative_train_only": "pending"}


def test_all_required_pass_activates_authority():
    assert authority_decision(_all_pass())["status"] == "activated"


def test_synthetic_positive_failure_is_evaluator_broken():
    d = authority_decision(dict(_all_pass(), positive_synthetic_obvious="fail"))
    assert d["status"] == "locked" and d["reason"] == "evaluator_broken"


def test_domain_failure_with_synthetics_passing_locks_pending_resolution():
    d = authority_decision(dict(_all_pass(), positive_domain="fail"))
    assert d["status"] == "locked" and d["reason"] == "domain_control_failed"
    assert "resolution_states" in d   # surfaces the ratifiable next steps


def test_negative_control_failure_locks_as_required_control_failed():
    d = authority_decision(dict(_all_pass(), negative_gradient_gaming="fail"))
    assert d["status"] == "locked" and d["reason"] == "required_control_failed"
    assert d["failed"] == ["negative_gradient_gaming"]


def test_pending_control_alone_does_not_block_activation():
    # negative_train_only is pending_not_enforced — must not prevent activation
    assert authority_decision(_all_pass())["status"] == "activated"
