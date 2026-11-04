"""G7 evaluator and band-monotonicity tests: synthetic matched outcomes only."""
import random
from datetime import datetime, timedelta, timezone

import pytest

from garage.api.monitoring import (G7Params, band_for_probability, evaluate_bands, evaluate_g7,
                                   f_or_prs_from_candidates, settled_outcome)
from garage.api.monitoring._stats import MonitoringInputError

T0 = datetime(2027, 3, 1, tzinfo=timezone.utc)


def params(**kw):
    base = dict(workshop_window=100, min_band_n=20, baseline_auc=None, clarification_approved=True)
    base.update(kw)
    return G7Params(**base)


def rec(i, *, p, y, band="medium", ws="w1", baseline=None, t=None, vehicle=None):
    d = {"booking_id": f"b{i}", "vehicle_id": vehicle or f"v{i}", "workshop_id": ws, "predicted": p, "y": y,
         "band": band, "observed_utc": (t or T0 + timedelta(hours=i)).isoformat()}
    if baseline is not None:
        d["baseline_score"] = baseline
    return d


def flat(n, *, p, n_pos, ws="w1", start=0, band="medium"):
    """n records, constant prediction p, exactly n_pos positives (spread through time)."""
    ys = [1] * n_pos + [0] * (n - n_pos)
    random.Random(7).shuffle(ys)
    return [rec(start + i, p=p, y=ys[i], ws=ws, band=band, baseline=0.5) for i in range(n)]


def three_bands(n_each, ks, start=0):
    out, i = [], start
    for band, k, p in zip(("low", "medium", "high"), ks, (0.1, 0.3, 0.6)):
        ys = [1] * k + [0] * (n_each - k)
        for y in ys:
            out.append(rec(i, p=p, y=y, band=band, baseline=0.5))
            i += 1
    return out


# ---------------------------------------------------------------- params
def test_open_decisions_are_required_arguments():
    with pytest.raises(TypeError):
        G7Params()
    with pytest.raises(TypeError):
        G7Params(workshop_window=100, min_band_n=10, baseline_auc=None)       # clarification flag missing
    with pytest.raises(MonitoringInputError):
        evaluate_g7([], params(workshop_window=50))                            # window below the 100 minimum
    with pytest.raises(MonitoringInputError):
        evaluate_g7([], params(baseline_auc=1.5))
    p = params()
    assert (p.pooled_window, p.min_workshop_n, p.bias_bound_pp, p.auc_margin) == (300, 100, 5.0, 0.02)


# ---------------------------------------------------------------- pooled bias, known answers
@pytest.mark.parametrize("n_pos,breach,bias", [(75, False, 5.0), (74, True, 5.3333), (76, False, 4.6667)])
def test_pooled_bias_boundary(n_pos, breach, bias):
    r = evaluate_g7(flat(300, p=0.30, n_pos=n_pos), params(), n_boot=50)
    b = r["pooled_bias"]
    assert b["n"] == 300 and b["bias_pp"] == pytest.approx(bias, abs=1e-3)
    assert b["breach"] is breach                                              # strictly greater than 5 pp
    trig = [t["trigger"] for t in r["triggers"]]
    assert ("pooled_bias" in trig) is breach


def test_negative_bias_also_breaches_and_interval_is_reported():
    r = evaluate_g7(flat(300, p=0.30, n_pos=120), params(), n_boot=300)       # observed 40% -> bias -10 pp
    b = r["pooled_bias"]
    assert b["bias_pp"] == pytest.approx(-10.0) and b["breach"]
    assert b["ci_lo_pp"] < b["bias_pp"] < b["ci_hi_pp"]                      # two-sided 95% interval reported
    assert b["bootstrap"]["sides"] == "two" and b["bootstrap"]["level"] == 0.95


def test_breach_is_point_based_even_when_interval_straddles_bound():
    r = evaluate_g7(flat(300, p=0.30, n_pos=70), params(), n_boot=300)        # bias 6.67 pp
    b = r["pooled_bias"]
    assert b["breach"] and b["ci_lo_pp"] < 5.0                                 # no waiting for significance
    assert b["ci_lo_pp"] < b["bias_pp"] < b["ci_hi_pp"]


def test_latest_300_by_time_not_input_order():
    old_bad = flat(50, p=0.30, n_pos=40, start=0)                              # old outcomes, huge bias
    good = flat(300, p=0.30, n_pos=90, start=1000)                             # latest 300: 30% -> bias 0
    allr = old_bad + good
    random.Random(3).shuffle(allr)
    r = evaluate_g7(allr, params(), n_boot=20)
    assert r["n_matched_total"] == 350 and r["pooled_bias"]["n"] == 300
    assert r["pooled_bias"]["bias_pp"] == pytest.approx(0.0, abs=1e-9) and not r["pooled_bias"]["breach"]
    assert r["window"]["first_utc"] == (T0 + timedelta(hours=1000)).isoformat()


def test_fewer_than_300_is_insufficient():
    recs = [x for w in range(4) for x in flat(75, p=0.3, n_pos=10, ws=f"w{w}", start=w * 100)]
    recs = recs[:299]
    r = evaluate_g7(recs, params(), n_boot=10)
    assert r["status"] == "insufficient_data" and r["pooled_bias"] is None and r["triggers"] == []


# ---------------------------------------------------------------- per-workshop
def test_workshop_checks_need_100_and_report_bias_only():
    recs = flat(120, p=0.30, n_pos=48, ws="A") + flat(99, p=0.30, n_pos=60, ws="B", start=1000)   # A: -10 pp
    r = evaluate_g7(recs, params(workshop_window=120), n_boot=50)
    ws = {w["workshop_id"]: w for w in r["workshops"]}
    assert ws["A"]["evaluated"] and ws["A"]["breach"] and ws["A"]["bias_pp"] == pytest.approx(-10.0)
    assert ws["B"]["evaluated"] is False and ws["B"]["n_total"] == 99
    assert r["pooled_bias"] is None                                               # 219 < 300
    assert [t["trigger"] for t in r["triggers"]] == ["workshop_bias"] and r["status"] == "breach"


def test_workshop_window_uses_latest_outcomes():
    early = flat(100, p=0.30, n_pos=60, ws="A", start=0)                           # +/- bad early
    late = flat(100, p=0.30, n_pos=30, ws="A", start=500)                          # fine late
    r = evaluate_g7(early + late, params(workshop_window=100), n_boot=0)
    w = r["workshops"][0]
    assert w["n"] == 100 and not w["breach"] and w["bias_pp"] == pytest.approx(0.0, abs=1e-9)
    r2 = evaluate_g7(early + late, params(workshop_window=200), n_boot=0)
    assert r2["workshops"][0]["n"] == 200 and r2["workshops"][0]["bias_pp"] == pytest.approx(-15.0)


# ---------------------------------------------------------------- AUC
def auc_pattern(reps):
    """4-case pattern with AUC exactly 0.75, replicated (cross-replica pairs keep AUC at 0.75)."""
    out = []
    for r in range(reps):
        for j, (s, y) in enumerate(((0.1, 0), (0.4, 0), (0.35, 1), (0.8, 1))):
            out.append(rec(r * 4 + j, p=s, y=y))
    return out


def test_auc_boundary_is_strict():
    recs = auc_pattern(75)                                                        # 300 records, AUC 0.75
    ok = evaluate_g7(recs, params(baseline_auc=0.77), n_boot=0)["auc"]
    assert ok["auc"] == pytest.approx(0.75) and ok["breach"] is False             # 0.75 == 0.77 - 0.02
    bad = evaluate_g7(recs, params(baseline_auc=0.78), n_boot=0)
    assert bad["auc"]["breach"] is True
    assert "auc_below_baseline_minus_margin" in [t["trigger"] for t in bad["triggers"]]


def test_auc_per_record_baseline_and_ci():
    rng = random.Random(5)
    recs = []
    for i in range(300):
        y = 1 if rng.random() < 0.3 else 0
        good = min(1, max(0, 0.3 + 0.3 * (y - 0.3) + rng.gauss(0, .1)))
        base = rng.random()                                                       # uninformative baseline
        recs.append(rec(i, p=good, y=y, baseline=base))
    r = evaluate_g7(recs, params(), n_boot=200)["auc"]
    assert r["baseline_source"] == "per_record_score" and r["auc"] > r["baseline_auc"] and not r["breach"]
    assert r["ci_lo"] > 0 and r["ci_hi"] >= r["ci_lo"]
    recs[0].pop("baseline_score")
    with pytest.raises(MonitoringInputError, match="baseline_score"):
        evaluate_g7(recs, params(), n_boot=0)


def test_auc_unassessable_with_one_class():
    r = evaluate_g7(flat(300, p=0.3, n_pos=0), params(), n_boot=0)["auc"]
    assert r["assessable"] is False and r["breach"] is None


# ---------------------------------------------------------------- bands inside G7
def test_monotone_bands_no_trigger():
    r = evaluate_g7(three_bands(100, (10, 30, 60)), params(baseline_auc=0.5, min_band_n=50), n_boot=100)
    assert r["bands"]["monotone_nondecreasing"] is True
    assert "band_nonmonotone" not in [t["trigger"] for t in r["triggers"]]


def test_nonmonotone_bands_trigger_and_empty_band_withholds():
    r = evaluate_g7(three_bands(100, (30, 20, 60)), params(baseline_auc=0.0, min_band_n=50), n_boot=0)
    assert r["bands"]["monotone_nondecreasing"] is False
    assert "band_nonmonotone" in [t["trigger"] for t in r["triggers"]]
    recs = three_bands(100, (10, 30, 60))[:200] + [dict(x, band="medium", booking_id=f"x{i}")
                                                   for i, x in enumerate(three_bands(100, (10, 30, 60))[200:])]
    r2 = evaluate_g7(recs, params(baseline_auc=0.0, min_band_n=50), n_boot=0)    # no 'high' outcomes at all
    assert r2["bands"]["empty_bands"] == ["high"] and r2["bands"]["monotone_nondecreasing"] is None
    assert "band_unassessable_percentages_withheld" in [t["trigger"] for t in r2["triggers"]]


def test_spec_status_records_clarification():
    r = evaluate_g7(flat(300, p=.3, n_pos=90), params(clarification_approved=False), n_boot=0)
    assert r["spec_status"] == "PROPOSED_NOT_APPROVED"
    assert evaluate_g7([], params())["spec_status"] == "clarification_approved"
    assert "no retraining" in r["action_on_trigger"]


def test_record_validation():
    bad = [rec(1, p=1.5, y=0)]
    with pytest.raises(MonitoringInputError):
        evaluate_g7(bad, params())
    with pytest.raises(MonitoringInputError, match="duplicated"):
        evaluate_g7([rec(1, p=.1, y=0), rec(1, p=.2, y=1)], params())
    with pytest.raises(MonitoringInputError):
        evaluate_g7([rec(1, p=.1, y=2)], params())
    with pytest.raises(MonitoringInputError):
        evaluate_g7([dict(rec(1, p=.1, y=0), band="none")], params())
    with pytest.raises(MonitoringInputError, match="timezone"):
        evaluate_g7([dict(rec(1, p=.1, y=0), observed_utc="2027-03-01T00:00:00")], params())


# ---------------------------------------------------------------- band monotonicity (standalone)
def bands_recs(ns, ks):
    out, i = [], 0
    for b, n, k in zip(("low", "medium", "high"), ns, ks):
        for j in range(n):
            out.append({"booking_id": i, "band": b, "y": 1 if j < k else 0, "vehicle_id": f"v{i}"})
            i += 1
    return out


def test_bands_known_answers_and_bands_only_qualification():
    r = evaluate_bands(bands_recs((300, 300, 300), (30, 60, 120)), min_n_assessable=100, n_boot=300)
    assert [r["bands"][b]["rate"] for b in ("low", "medium", "high")] == [0.1, 0.2, 0.4]
    assert r["monotone_nondecreasing"] and r["strictly_increasing"] and r["assessable"]
    assert r["bands"]["low"]["wilson_lo"] < 0.1 < r["bands"]["low"]["wilson_hi"]
    assert all(a["ci_lo"] > 0 for a in r["adjacent"]) and r["bands_only_qualified"] and r["bands_only_unmet"] == []


def test_bands_ties_are_monotone_but_not_bands_only():
    r = evaluate_bands(bands_recs((300, 300, 300), (60, 60, 120)), min_n_assessable=100, n_boot=100)
    assert r["monotone_nondecreasing"] is True and r["strictly_increasing"] is False
    assert not r["bands_only_qualified"] and "not strictly increasing" in " ".join(r["bands_only_unmet"])


def test_bands_small_band_blocks_bands_only_but_not_monotonicity():
    r = evaluate_bands(bands_recs((150, 300, 300), (15, 60, 120)), min_n_assessable=100, n_boot=100)
    assert r["monotone_nondecreasing"] is True and not r["bands_only_qualified"]
    assert "n < 200" in " ".join(r["bands_only_unmet"])
    r2 = evaluate_bands(bands_recs((150, 300, 300), (15, 60, 120)), min_n_assessable=200, n_boot=0)
    assert r2["assessable"] is False and r2["thin_bands"] == ["low"] and r2["monotone_nondecreasing"] is None


def test_bands_ci_must_clear_zero_for_bands_only():
    # point rates strictly increase but the difference is within noise -> not qualified
    r = evaluate_bands(bands_recs((200, 200, 200), (40, 41, 42)), min_n_assessable=100, n_boot=300)
    assert r["strictly_increasing"] is True and not r["bands_only_qualified"]
    assert r["adjacent"][0]["ci_lo"] < 0


def test_nonmonotone_violation_flags_and_empty_band():
    r = evaluate_bands(bands_recs((300, 300, 300), (90, 30, 120)), min_n_assessable=100, n_boot=300)
    assert r["monotone_nondecreasing"] is False
    low_med = r["adjacent"][0]
    assert low_med["violation"] is True and low_med["violation_ci_excludes_zero"] is True
    e = evaluate_bands(bands_recs((300, 0, 300), (30, 0, 120)), min_n_assessable=1, n_boot=50)
    assert e["empty_bands"] == ["medium"] and e["assessable"] is False and e["monotone_nondecreasing"] is None
    assert e["bands"]["medium"]["rate"] is None
    with pytest.raises(MonitoringInputError):
        evaluate_bands(bands_recs((1, 1, 1), (0, 0, 0)), min_n_assessable=0)


# ---------------------------------------------------------------- band edges and outcome helpers
@pytest.mark.parametrize("p,band", [(.1749, "low"), (.175, "medium"), (.1751, "medium"), (.18, "medium"),
                                    (.3249, "medium"), (.325, "medium"), (.3251, "high"), (0.0, "low"), (1.0, "high")])
def test_band_for_probability_rev3_boundary_cases(p, band):
    # rev3 s6 / AMENDMENT_DRAFT.md:55: round to 5 pp with ties-to-even, then 20/35 edges
    assert band_for_probability(p) == band


def test_band_for_probability_rejects_bad_input():
    for bad in (-0.1, 1.1, True, "0.2"):
        with pytest.raises(MonitoringInputError):
            band_for_probability(bad)


def test_f_or_prs_from_candidates_counts_prs_pair_as_positive():
    fail_pass = [{"result": "FAILED"}, {"result": "PASSED"}]        # the PRS pair that the app labels 'ambiguous'
    assert f_or_prs_from_candidates(fail_pass) == 1
    assert f_or_prs_from_candidates([{"result": "PASSED"}]) == 0
    assert f_or_prs_from_candidates([{"result": "PASSED"}, {"result": "FAILED"}]) == 0
    assert f_or_prs_from_candidates([]) is None and f_or_prs_from_candidates(None) is None


def test_settled_outcome_picks_day_14_with_candidates_only():
    rows = [{"check_day": 1, "status": "no_subsequent_test"}, {"check_day": 7, "status": "matched"},
            {"check_day": 14, "status": "ambiguous"}]
    assert settled_outcome(rows)["check_day"] == 14
    assert settled_outcome(rows[:2]) is None
    assert settled_outcome([{"check_day": 14, "status": "no_subsequent_test"}]) is None
