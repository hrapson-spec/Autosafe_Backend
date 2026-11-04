"""Rollback decision tests: the function maps evaluator outputs to an action; thresholds are policy parameters."""
import pytest

from garage.api.monitoring import RollbackPolicy, decide
from garage.api.monitoring._stats import MonitoringInputError


def policy(**kw):
    base = dict(g7_clarification_approved=True, on_insufficient_g7_data="continue", workshop_bias_action="alert",
                psi_alert_threshold=0.10, psi_fallback_threshold=0.25, unsupported_share_action="alert")
    base.update(kw)
    return RollbackPolicy(**base)


def g7(*triggers, status=None, n=300):
    trig = [dict(t) if isinstance(t, dict) else {"trigger": t} for t in triggers]
    return {"status": status or ("breach" if trig else "ok"), "triggers": trig, "n_matched_total": n,
            "spec_status": "clarification_approved"}


def g6(**psis):
    return {"psi": {k: {"psi": v} for k, v in psis.items()}, "unsupported_guard_ok": True, "unsupported_share": 0.1}


GOOD_BANDS = {"bands_only_qualified": True, "bands_only_unmet": []}
BAD_BANDS = {"bands_only_qualified": False, "bands_only_unmet": ["a band has n < 200"]}


def run(pol=None, **kw):
    kw.setdefault("current_mode", "percentages")
    kw.setdefault("bundle_verified", True)
    return decide(pol or policy(), **kw)


def test_policy_has_no_defaults():
    with pytest.raises(TypeError):
        RollbackPolicy()
    with pytest.raises(TypeError):
        RollbackPolicy(g7_clarification_approved=True)


@pytest.mark.parametrize("kw", [dict(on_insufficient_g7_data="x"), dict(workshop_bias_action="x"),
                                dict(unsupported_share_action="x"), dict(psi_alert_threshold=-1),
                                dict(psi_alert_threshold=0.3, psi_fallback_threshold=0.2),
                                dict(g7_clarification_approved="yes")])
def test_policy_validation(kw):
    with pytest.raises(MonitoringInputError):
        run(policy(**kw))


def test_clean_state_continues():
    d = run(g7=g7(), g6=g6(age=0.02, score=0.01), bands_only=GOOD_BANDS)
    assert d["action"] == "continue" and d["target_mode"] == "percentages" and d["reasons"] == [] and d["alerts"] == []


@pytest.mark.parametrize("trigger", ["pooled_bias", "auc_below_baseline_minus_margin", "band_nonmonotone",
                                     "band_unassessable_percentages_withheld"])
def test_g7_trigger_hides_percentages_keeps_bands_if_qualified(trigger):
    d = run(g7=g7(trigger), bands_only=GOOD_BANDS)
    assert d["action"] == "fall_back_bands_only" and d["target_mode"] == "bands_only"
    assert d["reasons"][0]["rule"] == trigger
    assert "No retraining" in d["note"]


def test_g7_trigger_without_bands_only_qualification_goes_to_stub():
    for bands in (BAD_BANDS, None):
        d = run(g7=g7("pooled_bias"), bands_only=bands)
        assert d["action"] == "fall_back_stub" and d["target_mode"] == "stub"
        assert any(r["rule"] == "bands_not_qualified" or r["rule"] == "bands_only_not_qualified" for r in d["reasons"])


def test_unapproved_clarification_caps_g7_at_alert():
    d = run(policy(g7_clarification_approved=False), g7=g7("pooled_bias"), bands_only=GOOD_BANDS)
    assert d["action"] == "alert" and d["target_mode"] == "percentages" and d["reasons"] == []
    assert "not approved" in d["alerts"][0]["capped_to_alert"]


def test_workshop_bias_action_is_a_policy_choice():
    t = {"trigger": "workshop_bias", "workshop_id": "A", "value_pp": 9.0}
    assert run(g7=g7(t), bands_only=GOOD_BANDS)["action"] == "alert"
    d = run(policy(workshop_bias_action="hide_percentages"), g7=g7(t), bands_only=GOOD_BANDS)
    assert d["action"] == "fall_back_bands_only"


def test_insufficient_g7_data_policy():
    ins = g7(status="insufficient_data", n=120)
    assert run(g7=ins)["action"] == "continue"
    assert run(policy(on_insufficient_g7_data="alert"), g7=ins)["action"] == "alert"


def test_psi_thresholds_are_parameters():
    assert run(g6=g6(age=0.12))["action"] == "alert"
    assert run(policy(psi_alert_threshold=None, psi_fallback_threshold=None), g6=g6(age=0.9))["action"] == "continue"
    d = run(g6=g6(age=0.30, score=0.01), bands_only=GOOD_BANDS)
    assert d["action"] == "fall_back_bands_only" and d["reasons"][0]["input"] == "age"
    assert run(policy(psi_alert_threshold=0.5, psi_fallback_threshold=0.5), g6=g6(age=0.3))["action"] == "continue"


def test_unsupported_share_guard_policy():
    g = g6(age=0.0)
    g["unsupported_guard_ok"], g["unsupported_share"] = False, 0.4
    assert run(g6=g)["action"] == "alert"
    assert run(policy(unsupported_share_action="ignore"), g6=g)["action"] == "continue"


def test_unverified_bundle_forces_stub_in_any_active_mode():
    for mode in ("percentages", "bands_only"):
        d = run(current_mode=mode, bundle_verified=False, bands_only=GOOD_BANDS)
        assert d["action"] == "fall_back_stub" and d["reasons"][0]["rule"] == "bundle_not_verified"


def test_bands_only_mode_behaviour():
    d = run(current_mode="bands_only", g7=g7("pooled_bias"), bands_only=GOOD_BANDS)
    assert d["action"] == "continue" and d["target_mode"] == "bands_only"     # percentages already hidden
    d = run(current_mode="bands_only", g7=g7("pooled_bias"), bands_only=BAD_BANDS)
    assert d["action"] == "fall_back_stub"
    d = run(current_mode="bands_only", bands_only=BAD_BANDS)                  # bands lost their qualification
    assert d["action"] == "fall_back_stub" and d["reasons"][0]["rule"] == "bands_only_no_longer_qualified"
    assert run(current_mode="bands_only")["action"] == "continue"             # no evidence supplied: no change


def test_stub_mode_never_rolls_back_but_can_alert():
    d = run(current_mode="stub", g7=g7("pooled_bias"), bundle_verified=False)
    assert d["action"] == "alert" and d["target_mode"] == "stub" and d["reasons"] == []
    assert run(current_mode="stub", bundle_verified=False)["action"] == "continue"
    assert run(current_mode="stub", g6=g6(age=0.5))["action"] == "alert"


def test_most_severe_action_wins():
    d = run(g7=g7("pooled_bias"), g6=g6(age=0.12), bundle_verified=False, bands_only=GOOD_BANDS)
    assert d["action"] == "fall_back_stub" and d["alerts"]


def test_argument_validation():
    with pytest.raises(MonitoringInputError):
        run(current_mode="shadow")
    with pytest.raises(MonitoringInputError):
        run(bundle_verified="yes")
    with pytest.raises(TypeError):
        decide(policy(), current_mode="percentages")                           # bundle_verified is required


def test_end_to_end_with_real_evaluators():
    import random
    from datetime import datetime, timedelta, timezone
    from garage.api.monitoring import G7Params, evaluate_bands, evaluate_g7
    rng = random.Random(11)
    t0 = datetime(2027, 3, 1, tzinfo=timezone.utc)
    recs = []
    for i in range(900):
        band = ("low", "medium", "high")[i % 3]
        p = {"low": 0.10, "medium": 0.28, "high": 0.55}[band]
        recs.append({"booking_id": i, "vehicle_id": f"v{i}", "workshop_id": "w1", "predicted": p,
                     "y": 1 if rng.random() < p + 0.12 else 0, "band": band,           # model under-predicts by 12 pp
                     "observed_utc": (t0 + timedelta(hours=i)).isoformat()})
    g = evaluate_g7(recs, G7Params(workshop_window=300, min_band_n=50, baseline_auc=0.5, clarification_approved=True),
                    n_boot=100)
    b = evaluate_bands(recs, min_n_assessable=50, n_boot=200)
    assert g["pooled_bias"]["breach"]
    d = decide(policy(), current_mode="percentages", bundle_verified=True, g7=g, bands_only=b)
    assert d["action"] == "fall_back_bands_only" and b["bands_only_qualified"]
