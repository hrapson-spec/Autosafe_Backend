"""PRS-aware outcome classifier: known-answer tests on synthetic tests only. The parameter values below are TEST
values (they mirror the display constants in briefing.py:39 and :181 for realism, they are not adopted policy)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from garage.api.monitoring import OutcomePolicy, PrsPairingRule, classify_booking_outcome
from garage.api.monitoring._stats import MonitoringInputError

APPT = date(2027, 3, 10)
HARD = ("MAJOR", "DANGEROUS", "FAIL")


def rule(**kw):
    base = dict(min_gap_seconds=0, max_gap_seconds=2, defect_rule="prs_only_fail_required",
                hard_defect_types=HARD, vehicle_rule="ignore", station_rule="ignore")
    base.update(kw)
    return PrsPairingRule(**base)


def policy(endpoint="fail_or_prs", multi="exclude_unresolved", window=14, **rk):
    return OutcomePolicy(endpoint=endpoint, window_days=window, multi_event_rule=multi, prs_rule=rule(**rk))


def T(day, hh=10, mm=0, ss=0, res="PASSED", defects=None, **kw):
    """day: 'YYYY-MM-DD' or offset in days from APPT. London == UTC in March before the 28th."""
    d = APPT + timedelta(days=day) if isinstance(day, int) else date.fromisoformat(day)
    t = {"completed_at": datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=timezone.utc).isoformat(),
         "result": res}
    if defects is not None:
        t["defects"] = defects
    t.update(kw)
    return t


PRS = [{"type": "PRS", "text": "x"}]
MAJOR = [{"type": "MAJOR", "text": "y"}]


def pair(day, gap=1, **kw):
    f = T(day, 10, 0, 0, "FAILED", PRS, **kw)
    p = T(day, 10, 0, gap, "PASSED", **kw)
    return [f, p]


def cls(tests, pol=None, appt=APPT):
    return classify_booking_outcome(tests, appt, pol or policy())


# ---- the six required cases ------------------------------------------------------------------------------
def test_pure_fail_is_positive_for_both_endpoints():
    for ep in ("fail_only", "fail_or_prs"):
        c = cls([T(1, res="FAILED", defects=MAJOR)], policy(ep))
        assert (c.label, c.y, c.n_tests_in_window, c.n_events) == ("fail", 1, 1, 1)


def test_prs_pair_positive_only_for_fail_or_prs():
    c = cls(pair(1), policy("fail_or_prs"))
    assert (c.label, c.y, c.n_tests_in_window, c.n_events, c.pair_gap_seconds) == ("prs_pair", 1, 2, 1, 1.0)
    c = cls(pair(1), policy("fail_only"))
    assert (c.label, c.y) == ("prs_pair", 0)


def test_pass_is_negative():
    for ep in ("fail_only", "fail_or_prs"):
        c = cls([T(2, res="PASSED")], policy(ep))
        assert (c.label, c.y, c.n_tests_in_window) == ("pass", 0, 1)


def test_ambiguous_non_prs_is_unresolved_under_exclude_and_first_event_decides_otherwise():
    # a real fail then a retest pass 3 days later: not a PRS pair
    tests = [T(1, res="FAILED", defects=MAJOR), T(4, res="PASSED")]
    c = cls(tests, policy(multi="exclude_unresolved"))
    assert (c.label, c.y, c.n_events, c.n_tests_in_window) == ("ambiguous_non_prs", None, 2, 2)
    c = cls(tests, policy(multi="first_event"))
    assert (c.label, c.y, c.n_events) == ("fail", 1, 2)
    # pass followed by a later fail
    tests = [T(1, res="PASSED"), T(5, res="FAILED", defects=MAJOR)]
    assert cls(tests, policy(multi="exclude_unresolved")).y is None
    assert cls(tests, policy(multi="first_event")).y == 0


def test_several_tests_on_one_day():
    # fail 09:00 (PRS-only defects), pass 15:00 the same day: gap far above the window -> two events, not a pair
    tests = [T(1, 9, res="FAILED", defects=PRS), T(1, 15, res="PASSED")]
    assert cls(tests, policy(multi="exclude_unresolved")).label == "ambiguous_non_prs"
    c = cls(tests, policy(multi="first_event"))
    assert (c.label, c.y) == ("fail", 1)
    # a PRS pair plus another test on the same day: still two events
    tests = pair(1) + [T(1, 16, res="FAILED", defects=MAJOR)]
    c = cls(tests, policy(multi="exclude_unresolved"))
    assert (c.label, c.y, c.n_events, c.n_tests_in_window) == ("ambiguous_non_prs", None, 2, 3)
    c = cls(tests, policy(multi="first_event"))
    assert (c.label, c.y) == ("prs_pair", 1)
    # input order never matters
    assert cls(list(reversed(tests)), policy(multi="first_event")) == c


def test_pairing_gap_boundary_is_inclusive_both_ends():
    assert cls(pair(1, gap=2)).label == "prs_pair"                       # == max_gap_seconds
    assert cls(pair(1, gap=3)).label == "ambiguous_non_prs"              # just above
    assert cls(pair(1, gap=0)).label == "prs_pair"                       # == min_gap_seconds (0)
    assert cls(pair(1, gap=1), policy(min_gap_seconds=1.5, max_gap_seconds=2)).label == "ambiguous_non_prs"
    assert cls(pair(1, gap=2), policy(min_gap_seconds=1.5, max_gap_seconds=2)).label == "prs_pair"


def test_window_boundary_london_date_inclusive():
    last = APPT + timedelta(days=14)
    assert cls([T(14, res="PASSED")]).label == "pass"                    # appointment + 14 days: in
    assert cls([T(15, res="PASSED")]).label == "no_test"                 # one day later: out
    assert cls([T(0, res="PASSED")]).label == "pass"                     # appointment day: in
    assert cls([T(-1, res="PASSED")]).label == "no_test"                 # day before: out
    assert cls([T(15, res="PASSED")], policy(window=15)).label == "pass"
    # a pair whose fail is the last in-window instant and whose pass is after midnight: one event, anchored at the fail
    f = T(14, 23, 59, 59, "FAILED", PRS)
    p = {"completed_at": (datetime(last.year, last.month, last.day, 23, 59, 59, tzinfo=timezone.utc)
                          + timedelta(seconds=1)).isoformat(), "result": "PASSED"}
    c = cls([f, p])
    assert (c.label, c.y, c.n_tests_in_window) == ("prs_pair", 1, 2)
    # a pair anchored before the window start is not an in-window event (its pass is not a separate test)
    f = T(-1, 23, 59, 59, "FAILED", PRS)
    p = {"completed_at": (datetime(APPT.year, APPT.month, APPT.day, tzinfo=timezone.utc)).isoformat(),
         "result": "PASSED"}
    assert cls([f, p]).label == "no_test"
    # London calendar day, not UTC: 23:30 UTC on 2027-07-09 is 00:30 BST on 2027-07-10
    jul = {"completed_at": "2027-07-09T23:30:00+00:00", "result": "PASSED"}
    assert classify_booking_outcome([jul], date(2027, 7, 10), policy(window=0)).label == "pass"
    assert classify_booking_outcome([jul], date(2027, 7, 9), policy(window=0)).label == "no_test"


# ---- rule parameters --------------------------------------------------------------------------------------
def test_defect_rule():
    no_prs = [T(1, 10, 0, 0, "FAILED", MAJOR), T(1, 10, 0, 1, "PASSED")]
    assert cls(no_prs).label == "ambiguous_non_prs"                       # hard defects: not a PRS pair
    assert cls(no_prs, policy(defect_rule="ignore_defects")).label == "prs_pair"
    mixed = [T(1, 10, 0, 0, "FAILED", PRS + MAJOR), T(1, 10, 0, 1, "PASSED")]
    assert cls(mixed).label == "ambiguous_non_prs"
    nodef = [T(1, 10, 0, 0, "FAILED"), T(1, 10, 0, 1, "PASSED")]
    assert cls(nodef).label == "ambiguous_non_prs"


def test_vehicle_and_station_rules_fail_closed():
    a = pair(1, vehicle_id="v1", station_id="s1")
    assert cls(a, policy(vehicle_rule="same_required", station_rule="same_required")).label == "prs_pair"
    b = pair(1, vehicle_id="v1", station_id="s1")
    b[1]["station_id"] = "s2"
    assert cls(b, policy(station_rule="same_required")).label == "ambiguous_non_prs"
    assert cls(b, policy(station_rule="ignore")).label == "prs_pair"
    c = pair(1, vehicle_id="v1")
    c[1]["vehicle_id"] = "v2"
    assert cls(c, policy(vehicle_rule="same_required")).label == "ambiguous_non_prs"
    with pytest.raises(MonitoringInputError):                              # station not carried, rule requires it
        cls(pair(1), policy(station_rule="same_required"))


def test_empty_history_and_other_tests_outside_window():
    assert cls([]).label == "no_test"
    c = cls([T(-400, res="PASSED"), T(1, res="FAILED", defects=MAJOR)])
    assert (c.label, c.n_tests_in_window) == ("fail", 1)


def test_policy_has_no_defaults_and_validates():
    with pytest.raises(TypeError):
        PrsPairingRule(min_gap_seconds=0, max_gap_seconds=2)               # type: ignore[call-arg]
    with pytest.raises(TypeError):
        OutcomePolicy(endpoint="fail_only", window_days=14)                # type: ignore[call-arg]
    for bad in (policy(endpoint="hard_fail"), policy(multi="guess"), policy(window=-1),
                policy(defect_rule="x"), policy(vehicle_rule="maybe"), policy(max_gap_seconds=-1),
                policy(min_gap_seconds=3, max_gap_seconds=2), policy(hard_defect_types=())):
        with pytest.raises(MonitoringInputError):
            cls([T(1)], bad)
    with pytest.raises(MonitoringInputError):
        cls([{"completed_at": "2027-03-11T10:00:00", "result": "PASSED"}])  # naive time
    with pytest.raises(MonitoringInputError):
        cls([T(1, res="WEIRD")])
    with pytest.raises(MonitoringInputError):
        classify_booking_outcome([], "not-a-date", policy())


def test_matching_py_is_unchanged_by_this_module():
    """The product matcher still calls the PRS pair 'ambiguous' (display behaviour), the classifier does not."""
    from garage.api import matching
    from garage.api.dvsa import DvsaTest
    f = DvsaTest(completed_at=datetime(2027, 3, 11, 10, tzinfo=timezone.utc), result="FAILED", defects=tuple(PRS))
    p = DvsaTest(completed_at=datetime(2027, 3, 11, 10, 0, 1, tzinfo=timezone.utc), result="PASSED")
    st, matched, cands, detail = matching.match_outcome([f, p], APPT, "mot")
    assert st == "ambiguous" and matched is None and detail == "fail_then_later_pass"
    assert cls([f.to_json(), p.to_json()], policy("fail_or_prs")).y == 1
