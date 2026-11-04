"""Briefing engine: unit tests for every rule, the PRS event rule, the cutoff, wording and versioning.

Pure-function tests (no database). The same engine is exercised on the 50 real-shape fixtures in
test_briefing_fixtures.py and through the API in test_briefing_api.py.
"""
import random
import re
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

from garage.api import briefing as B
from garage.api.dvsa import DvsaHistory, DvsaTest
from .contract_check import CONTRACT

CUT = "2026-10-26"


def D(typ, text="Front brake disc worn"):
    return {"type": typ, "text": text, "dangerous": typ == "DANGEROUS"}


def T(day, result="PASSED", odo=None, unit="MI", defects=(), at="10:00:00"):
    return DvsaTest(completed_at=datetime.fromisoformat(f"{day}T{at}+00:00"), result=result,
                    odometer_value=odo, odometer_unit=unit if odo is not None else None, defects=tuple(defects))


def H(*tests):
    return DvsaHistory(registration="ZZ99TST", tests=tuple(tests))


def run(*tests, cutoff=CUT, history=None):
    h = history if history is not None else H(*tests)
    return B.evaluate(h, "ok", date.fromisoformat(cutoff), datetime.fromisoformat(cutoff + "T09:00:00+00:00"))


def gaps(out):
    return [g["rule_id"] for g in out["evidence_gaps"]]


def qs(out):
    return [q["rule_id"] for q in out["follow_up_questions"]]


# ------------------------------------------------------------------ evidence gaps
def test_G01_vehicle_not_found():
    out = B.evaluate(None, "not_found", date(2026, 10, 26), datetime(2026, 10, 26, 9, tzinfo=timezone.utc))
    assert gaps(out) == ["G01_vehicle_not_found"] and out["source_records"] == [] and qs(out) == []
    assert out["summary"].startswith("No dated MOT records were available")


def test_G02_no_prior_test_when_empty_or_only_later_tests():
    assert gaps(run()) == ["G02_no_prior_test"]
    later = run(T("2026-10-26", odo=1000), T("2026-11-30", odo=1100))      # on and after the cutoff date
    assert gaps(later) == ["G02_no_prior_test"] and later["source_records"] == []


def test_G03_short_history_only_for_exactly_one_event():
    assert "G03_short_history" in gaps(run(T("2026-05-01", odo=10)))
    assert "G03_short_history" not in gaps(run(T("2025-05-01", odo=10), T("2026-05-01", odo=20)))
    assert "G03_short_history" not in gaps(run())                                # zero is G02, not G03


@pytest.mark.parametrize("last,stale", [("2025-07-26", False), ("2025-07-25", True), ("2026-09-01", False),
                                        ("2024-01-10", True)])
def test_G04_stale_after_15_months(last, stale):
    out = run(T("2023-01-01", odo=1), T(last, odo=2))
    assert ("G04_stale_history" in gaps(out)) is stale
    if stale:
        txt = next(g["text"] for g in out["evidence_gaps"] if g["rule_id"] == "G04_stale_history")
        assert "more than 15 months earlier" in txt and B.fmt_date(date.fromisoformat(last)) in txt


def test_G05_missing_odometer_lists_dates_newest_first_and_only_inside_the_window():
    out = run(T("2026-05-01", odo=300), T("2025-05-01"), T("2024-05-01", odo=100), T("2023-05-01"))
    g = [x for x in out["evidence_gaps"] if x["rule_id"] == "G05_missing_odometer"]
    assert len(g) == 1 and g[0]["text"] == \
        "No odometer reading was recorded at the test on 1 May 2025, 1 May 2023."
    # six events: the oldest (no reading) falls outside the last five and is not reported
    out = run(T("2021-05-01"), *[T(f"{y}-05-01", odo=y) for y in range(2022, 2027)])
    assert "G05_missing_odometer" not in gaps(out)


def test_G06_odometer_drop():
    out = run(T("2025-05-01", odo=50000), T("2026-05-01", odo=40000))
    g = [x for x in out["evidence_gaps"] if x["rule_id"] == "G06_odometer_drop"]
    assert [x["text"] for x in g] == ["The odometer reading recorded at the test on 1 May 2026 (40,000 miles) was "
                                      "lower than the reading recorded at the test on 1 May 2025 (50,000 miles)."]
    assert "G06_odometer_drop" not in gaps(run(T("2025-05-01", odo=50000), T("2026-05-01", odo=50000)))
    assert "G06_odometer_drop" in gaps(run(T("2025-05-01", odo=50000), T("2026-05-01", odo=49999)))   # any fall
    # a missing reading between two present ones does not hide or invent a drop
    assert "G06_odometer_drop" not in gaps(run(T("2024-05-01", odo=10), T("2025-05-01"), T("2026-05-01", odo=20)))
    # outside the last five events: not reported
    old = [T("2019-05-01", odo=90000), T("2020-05-01", odo=10000)] + [T(f"{y}-05-01", odo=20000 + y) for y in range(2021, 2026)]
    assert "G06_odometer_drop" not in gaps(run(*old))


def test_G06_units_are_converted_not_compared_raw():
    # 100,000 km is 62,137 miles: NOT a drop from 50,000 miles; 80,000 miles to 100,000 km IS
    assert "G06_odometer_drop" not in gaps(run(T("2025-05-01", odo=50000, unit="MI"), T("2026-05-01", odo=100000, unit="KM")))
    assert "G06_odometer_drop" in gaps(run(T("2025-05-01", odo=80000, unit="MI"), T("2026-05-01", odo=100000, unit="KM")))


def test_G06_at_most_two_drops_are_reported():
    out = run(T("2022-05-01", odo=5000), T("2023-05-01", odo=4000), T("2024-05-01", odo=3000),
              T("2025-05-01", odo=2000), T("2026-05-01", odo=1000))
    assert gaps(out).count("G06_odometer_drop") == B.MAX_ODOMETER_DROP_GAPS == 2


def test_G07_and_Q01_fail_with_no_later_pass():
    out = run(T("2025-05-01", odo=10), T("2026-09-01", "FAILED", 20, defects=[D("MAJOR")]))
    assert "G07_recent_fail_no_pass" in gaps(out) and qs(out)[0] == "Q01_unresolved_fail"
    assert out["follow_up_questions"][0]["text"] == (
        "The test on 1 September 2026 was recorded as a fail and no later pass was recorded. "
        "Ask what has been repaired since and whether a retest took place.")
    ok = run(T("2026-09-01", "FAILED", 20, defects=[D("MAJOR")]), T("2026-09-10", odo=21))
    assert "G07_recent_fail_no_pass" not in gaps(ok) and "Q01_unresolved_fail" not in qs(ok)


def test_G08_prs_fail_without_an_immediate_pass_is_shown_as_a_fail():
    f = T("2026-05-01", "FAILED", 10, defects=[D("PRS", "Tyre damaged"), D("ADVISORY", "Seat belt worn")], at="10:00:00")
    for later in (None, T("2026-05-01", "PASSED", 10, at="10:00:03")):         # none, or 3 s later (> 2 s)
        out = run(T("2025-05-01", odo=5), f, *( [later] if later else [] ))
        assert "G08_prs_without_pass" in gaps(out)
    out = run(T("2025-05-01", odo=5), f)
    assert out["source_records"][0]["result"] == "FAILED"
    assert "G07_recent_fail_no_pass" in gaps(out)


# ------------------------------------------------------------------ the PRS event rule (T1b)
def prs_pair(gap_s=1, passed_defects=(D("ADVISORY", "Seat belt worn"),)):
    f = T("2026-05-01", "FAILED", 10, defects=[D("PRS", "Tyre damaged"), D("PRS", "Wiper blade"),
                                               D("ADVISORY", "Seat belt worn")], at="10:00:00")
    p = T("2026-05-01", "PASSED", 10, defects=passed_defects, at=f"10:00:{gap_s:02d}")
    return f, p


@pytest.mark.parametrize("gap_s,paired", [(0, True), (1, True), (2, True), (3, False), (35, False)])
def test_prs_fail_then_pass_within_two_seconds_is_one_rectified_event(gap_s, paired):
    f, p = prs_pair(gap_s)
    events = B.build_events((f, p))
    assert [e.kind for e in events] == (["RECTIFIED"] if paired else ["FAILED", "PASSED"])
    if paired:
        e = events[0]
        types = [d["type"] for d in e.defects]
        assert types.count("PRS") == 2 and types.count("ADVISORY") == 1       # the copied advisory is kept once
        assert e.day == date(2026, 5, 1) and e.odometer_value == 10


def test_prs_fail_with_a_hard_defect_is_not_a_rectified_event():
    f = T("2026-05-01", "FAILED", 10, defects=[D("PRS", "Tyre damaged"), D("MAJOR", "Brake pipe corroded")])
    p = T("2026-05-01", "PASSED", 10, at="10:00:01")
    assert [e.kind for e in B.build_events((f, p))] == ["FAILED", "PASSED"]


def test_rectified_event_in_the_briefing_has_one_record_and_dated_prs_items():
    f, p = prs_pair(1)
    out = run(T("2025-04-20", odo=5), f, p)
    assert [r["result"] for r in out["source_records"]] == ["RECTIFIED", "PASSED"]
    rec = out["source_records"][0]
    prs = [d for d in rec["defects"] if d["type"] == "PRS"]
    assert [d["label"] for d in prs] == ["Defect rectified at the test, recorded at test on 1 May 2026"] * 2
    assert qs(out)[0] == "Q03_rectified_at_last_test"
    assert out["follow_up_questions"][0]["text"] == (
        "The test on 1 May 2026 recorded 2 items as rectified at the test. Ask what was repaired and who did the work.")
    assert "G07_recent_fail_no_pass" not in gaps(out)           # a rectified event is not an open fail
    assert "Q01_unresolved_fail" not in qs(out) and "Q02_fail_then_pass" not in qs(out)


def test_q03_only_when_the_rectified_event_is_the_latest():
    f, p = prs_pair(1)
    out = run(f, p, T("2026-09-01", odo=30))
    assert "Q03_rectified_at_last_test" not in qs(out)


def test_cut_off_comes_before_pairing_so_a_split_pair_is_not_merged():
    f, p = prs_pair(1)
    h = H(T("2025-04-20", odo=5), f, p)
    out = B.evaluate(h, "ok", date(2026, 10, 26), datetime(2026, 5, 1, 10, 0, 0, 500000, tzinfo=timezone.utc))
    # cutoff instant falls between the fail (10:00:00) and the pass (10:00:01): only the fail is visible
    # (and the cutoff DATE already excludes 1 May 2026 itself when it is the cutoff date)
    assert [r["result"] for r in out["source_records"]] == ["FAILED", "PASSED"]
    assert "G08_prs_without_pass" in gaps(out)


# ------------------------------------------------------------------ follow-up questions
def test_Q02_fail_then_pass_within_60_days():
    f = T("2026-06-01", "FAILED", 10, defects=[D("MAJOR", "Brake pipe corroded"), D("DANGEROUS", "Tyre cord exposed"),
                                               D("ADVISORY", "Corrosion")])
    out = run(T("2025-06-01", odo=5), f, T("2026-07-15", odo=11))
    q = next(x for x in out["follow_up_questions"] if x["rule_id"] == "Q02_fail_then_pass")
    assert q["text"] == ("The test on 1 June 2026 was recorded as a fail with 2 major or dangerous items, and a pass "
                         "was recorded on 15 July 2026. Ask what repairs were made in between.")
    assert q["source_test_date"] == "2026-06-01"
    late = run(f, T("2026-08-01", odo=11))                    # 61 days later: outside the window
    assert "Q02_fail_then_pass" not in qs(late)
    one = run(T("2026-06-01", "FAILED", 10, defects=[D("MAJOR")]), T("2026-06-02", odo=11))
    assert next(x for x in one["follow_up_questions"] if x["rule_id"] == "Q02_fail_then_pass")["text"].count("1 major or dangerous item,") == 1


def test_Q04_repeat_advisory_across_two_tests_matches_the_spec_wording():
    txt = "Front Service brake fluctuating, but not excessively (1.2.1 (e))"
    out = run(T("2024-05-01", odo=1, defects=[D("ADVISORY", txt)]), T("2025-05-01", odo=2),
              T("2026-05-01", odo=3, defects=[D("ADVISORY", "  front service BRAKE fluctuating, but not excessively (1.2.1 (e)) ")]))
    q = [x for x in out["follow_up_questions"] if x["rule_id"] == "Q04_repeat_advisory"]
    assert len(q) == 1
    newest = "front service BRAKE fluctuating, but not excessively (1.2.1 (e))"      # the newest record's wording
    assert q[0]["text"] == (f"Ask whether the “{newest}” advisory from 1 May 2026 has been addressed. "
                            "It was recorded at 2 tests (1 May 2026, 1 May 2024).")
    assert q[0]["source_test_date"] == "2026-05-01"
    assert "Q05_last_test_advisory" not in qs(out)            # already covered by the repeat question


def test_Q04_counts_distinct_days_not_a_same_day_fail_and_retest_copy():
    adv = D("ADVISORY", "Corrosion on the sill")
    out = run(T("2025-05-01", odo=1), T("2026-05-01", "FAILED", 2, defects=[D("MAJOR"), adv], at="09:00:00"),
              T("2026-05-01", "PASSED", 2, defects=[adv], at="15:00:00"))
    assert "Q04_repeat_advisory" not in qs(out)
    assert "Q05_last_test_advisory" in qs(out)


def test_Q04_ignores_repeats_outside_the_last_five_events_and_non_advisories():
    adv = D("ADVISORY", "Corrosion on the sill")
    old = [T("2019-05-01", odo=1, defects=[adv]), T("2020-05-01", odo=2, defects=[adv])]
    new = [T(f"{y}-05-01", odo=10 + y) for y in range(2021, 2026)]
    assert "Q04_repeat_advisory" not in qs(run(*old, *new))
    minor = D("MINOR", "Corrosion on the sill")
    assert "Q04_repeat_advisory" not in qs(run(T("2025-05-01", odo=1, defects=[minor]), T("2026-05-01", odo=2, defects=[minor])))


def test_Q04_and_Q05_are_capped_at_three_each():
    adv = lambda n: D("ADVISORY", f"Item number {n} worn")
    out = run(T("2025-05-01", odo=1, defects=[adv(i) for i in range(5)]), T("2026-05-01", odo=2, defects=[adv(i) for i in range(5)]))
    assert qs(out).count("Q04_repeat_advisory") == B.MAX_ADVISORY_QUESTIONS == 3
    out = run(T("2026-05-01", odo=2, defects=[adv(i) for i in range(5)]))
    assert qs(out).count("Q05_last_test_advisory") == 3


def test_Q05_last_test_advisory_text_and_dedupe():
    a = D("ADVISORY", "Offside Play in steering rack inner joint(s)")
    out = run(T("2025-05-01", odo=1), T("2026-05-01", odo=2, defects=[a, dict(a)]))
    q = [x for x in out["follow_up_questions"] if x["rule_id"] == "Q05_last_test_advisory"]
    assert [x["text"] for x in q] == ["Ask whether the “Offside Play in steering rack inner joint(s)” advisory "
                                      "from 1 May 2026 has been addressed."]


def test_question_priority_order_and_stable_ids():
    adv = D("ADVISORY", "Corrosion on the sill")
    out = run(T("2025-05-01", odo=1, defects=[adv]),
              T("2026-05-01", "FAILED", 2, defects=[D("MAJOR"), adv]))
    assert qs(out) == ["Q01_unresolved_fail", "Q04_repeat_advisory"]
    again = run(T("2025-05-01", odo=1, defects=[adv]), T("2026-05-01", "FAILED", 2, defects=[D("MAJOR"), adv]))
    assert [q["id"] for q in out["follow_up_questions"]] == [q["id"] for q in again["follow_up_questions"]]
    assert len({q["id"] for q in out["follow_up_questions"]}) == 2


# ------------------------------------------------------------------ summary, records, cutoff
def test_summary_templates():
    out = run(T("2025-05-01", odo=41200), T("2026-05-01", odo=45800))
    assert out["summary"] == ("Recorded before 26 October 2026: 2 MOT test results. The most recent, on 1 May 2026, "
                              "was a pass at 45,800 miles. Listed below: no evidence gaps and no follow-up questions, "
                              "generated by fixed rules over the recorded history.")
    one = run(T("2026-05-01", "FAILED", 100, "KM", defects=[D("MAJOR")]))
    assert "1 MOT test result." in one["summary"] and "was a fail at 100 km." in one["summary"]
    assert "2 evidence gaps and 1 follow-up question," in one["summary"]
    f, p = prs_pair(1)
    assert "was a fail with items rectified at the test" in run(f, p)["summary"]
    assert run()["summary"] == "No dated MOT records were available for this booking. Listed below: 1 evidence gap."


def test_records_newest_first_last_five_with_dated_defect_labels():
    tests = [T(f"{y}-03-0{y - 2018}", odo=1000 * y, defects=[D("ADVISORY", f"Item {y}"), D("MAJOR", "Brake") if y == 2022 else D("MINOR", "m")],
               result="FAILED" if y == 2022 else "PASSED") for y in range(2019, 2026)]
    out = run(*tests, T("2026-03-09", odo=9))
    recs = out["source_records"]
    assert len(recs) == B.RECORD_LIMIT == 5
    assert [r["test_date"] for r in recs] == sorted((r["test_date"] for r in recs), reverse=True)
    assert recs[0]["test_date"] == "2026-03-09"
    for r in recs:
        for d in r["defects"]:
            assert d["recorded_on"] == r["test_date"]
            assert d["label"].endswith(f"recorded at test on {B.fmt_date(date.fromisoformat(r['test_date']))}")
    labels = {d["type"]: d["label"].split(",")[0] for r in recs for d in r["defects"]}
    assert labels["ADVISORY"] == "Advisory" and labels["MINOR"] == "Minor defect"


def test_odometer_fields_and_unreadable_reading():
    out = run(T("2026-05-01", odo=None), T("2025-05-01", odo=7, unit="KM"))
    r = out["source_records"]
    assert (r[0]["odometer_value"], r[0]["odometer_unit"]) == (None, None)
    assert (r[1]["odometer_value"], r[1]["odometer_unit"]) == (7, "KM")


def test_cutoff_hides_tests_on_or_after_it_before_any_rule_runs():
    tests = [T("2025-05-01", odo=10), T("2026-05-01", odo=20), T("2026-10-26", "FAILED", 5, defects=[D("MAJOR")]),
             T("2026-11-02", odo=3)]
    out = run(*tests)
    assert [r["test_date"] for r in out["source_records"]] == ["2026-05-01", "2025-05-01"]
    assert "G07_recent_fail_no_pass" not in gaps(out) and "G06_odometer_drop" not in gaps(out)
    early = run(*tests, cutoff="2026-05-01")
    assert [r["test_date"] for r in early["source_records"]] == ["2025-05-01"]


def test_cutoff_instant_as_well_as_date():
    t = T("2026-05-01", odo=20, at="12:00:00")
    out = B.evaluate(H(T("2025-05-01", odo=10), t), "ok", date(2026, 5, 2), datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc))
    assert [r["test_date"] for r in out["source_records"]] == ["2025-05-01"]       # same London day, later instant


def test_dates_are_london_dates_not_utc():
    late = T("2026-06-30", odo=5, at="23:30:00")            # 00:30 on 1 July in London (BST)
    out = run(late, cutoff="2026-07-01")
    assert out["source_records"] == []                       # test_date 1 July is not before the 1 July cutoff
    assert [r["test_date"] for r in run(late, cutoff="2026-07-02")["source_records"]] == ["2026-07-01"]


def test_input_order_does_not_matter():
    base = [T(f"{y}-05-01", odo=y, defects=[D("ADVISORY", "Worn")], at="08:00:00") for y in range(2020, 2027)]
    f, p = prs_pair(1)
    base += [f, p]
    ref = run(*base)
    for seed in range(5):
        shuf = base[:]
        random.Random(seed).shuffle(shuf)
        assert run(*shuf) == ref


def test_same_snapshot_same_rules_same_briefing_and_version_identity():
    h = H(T("2025-05-01", odo=1), T("2026-05-01", odo=2))
    a = B.build(h.to_json(), "ok", "ab" * 32, date(2026, 10, 26), datetime(2026, 10, 26, 9, tzinfo=timezone.utc))
    b = B.build(h.to_json(), "ok", "ab" * 32, date(2026, 10, 26), datetime(2026, 10, 26, 9, tzinfo=timezone.utc))
    assert a == b and a["briefing_version"] == "abababababababab.briefing-rules-1" and a["rule_table_version"] == B.RULE_TABLE_VERSION
    c = B.build(h.to_json(), "ok", "cd" * 32, date(2026, 10, 26), datetime(2026, 10, 26, 9, tzinfo=timezone.utc))
    assert c["briefing_version"] != a["briefing_version"]


# ------------------------------------------------------------------ the rule table
def test_rule_table_shape_and_contract_enums():
    ids = [r.id for r in B.RULES]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[GQS]\d\d_[a-z_]+", i) for i in ids)
    assert all(r.condition and r.text for r in B.RULES)
    schemas = yaml.safe_load(CONTRACT.read_text())["components"]["schemas"]
    assert tuple(schemas["BriefingGapRuleId"]["enum"]) == B.GAP_RULE_IDS
    assert tuple(schemas["BriefingQuestionRuleId"]["enum"]) == B.QUESTION_RULE_IDS
    assert [r.id for r in B.RULES if r.kind == "summary"] == ["S01_summary", "S02_summary_empty"]


# Changing ANY rule, template, threshold or constant changes this digest. When it does: bump RULE_TABLE_VERSION
# (briefings are versioned by snapshot + rule-table version), then update both pins together.
PINNED = {"version": "briefing-rules-1", "digest": "bd29ccdc8e58e111dd5891d8d4af697bb56b03a581e0c99f0d21bd0af02551e8"}


def test_rule_table_digest_is_pinned_to_the_version():
    assert (B.RULE_TABLE_VERSION, B.rule_table_digest()) == (PINNED["version"], PINNED["digest"])


# ------------------------------------------------------------------ wording: history is never presented as current
FORBIDDEN = re.compile(r"\b(is|are|has|have|needs?|requires?|remains?|currently|now|still|present|exists?|today)\b", re.I)
ALLOWED_PHRASES = ("has been addressed", "what has been repaired since")


def _strip_allowed(t):
    for a in ALLOWED_PHRASES:
        t = t.replace(a, "")
    return t


def test_no_template_uses_the_present_tense_about_the_record():
    for r in B.RULES:
        bad = FORBIDDEN.findall(_strip_allowed(r.text))
        assert bad == [], (r.id, bad, r.text)


def test_templates_that_name_a_defect_or_item_carry_a_date_and_a_past_tense_verb():
    for r in B.RULES:
        if r.kind == "summary":
            continue
        names_item = any(k in r.text for k in ("{item}", "{items}", "advisory", "fail", "repair", "rectified", "{n} "))
        if names_item and r.id not in ("Q05_last_test_advisory", "Q04_repeat_advisory"):
            assert re.search(r"\{(date|fail_date|pass_date|dates|date_after)\}", r.text), r.id
        if names_item:
            assert re.search(r"\b(was|were|recorded|rectified|advised|repaired)\b|from \{date\}", r.text), r.id
        # defect wording from the source is always quoted, never spliced into our own sentence
        if "{item}" in r.text:
            assert "“{item}”" in r.text


def test_defect_labels_have_one_dated_past_tense_shape():
    f, p = prs_pair(1)
    out = run(T("2025-05-01", odo=1, defects=[D("ADVISORY"), D("MINOR"), D("MAJOR"), D("DANGEROUS"), D("FAIL")]), f, p)
    pat = re.compile(r"^(Advisory|Minor defect|Major defect|Dangerous defect|Defect rectified at the test|Failure item), "
                     r"recorded at test on \d{1,2} [A-Z][a-z]+ \d{4}$")
    labels = [d["label"] for r in out["source_records"] for d in r["defects"]]
    assert labels and all(pat.match(x) for x in labels), labels


def strip_quotes(t):
    return re.sub(r"“[^”]*”", "", t)


def test_rendered_texts_outside_quotations_use_no_present_tense_for_any_rule():
    """Every rule fired at least once on crafted data; everything we wrote (outside quoted source text) is past tense."""
    f, p = prs_pair(1)
    adv = D("ADVISORY", "Corrosion is present on the sill and the brake needs attention")
    outs = [
        run(T("2024-05-01", odo=5000, defects=[adv]), T("2025-05-01", odo=4000, defects=[adv]),
            T("2026-02-01", "FAILED", 4500, defects=[D("MAJOR", "Brake is worn")]), T("2026-02-20", odo=None, defects=[adv])),
        run(T("2023-01-01", odo=9), f, p),
        run(T("2026-09-01", "FAILED", None, defects=[D("PRS", "Tyre needs replacing"), D("ADVISORY", "Is worn")])),
        run(), run(T("2020-01-01", odo=1)),
        B.evaluate(None, "not_found", date(2026, 10, 26), datetime(2026, 10, 26, 9, tzinfo=timezone.utc)),
    ]
    fired = set()
    for o in outs:
        texts = [g["text"] for g in o["evidence_gaps"]] + [q["text"] for q in o["follow_up_questions"]] + [o["summary"]]
        fired |= {g["rule_id"] for g in o["evidence_gaps"]} | {q["rule_id"] for q in o["follow_up_questions"]}
        for t in texts:
            assert FORBIDDEN.findall(_strip_allowed(strip_quotes(t))) == [], t
    missing = (set(B.GAP_RULE_IDS) | set(B.QUESTION_RULE_IDS)) - fired
    assert missing <= {"Q02_fail_then_pass", "Q05_last_test_advisory"}, missing      # those two are asserted in their own tests


def test_briefing_never_mentions_or_explains_a_model_score():
    f, p = prs_pair(1)
    o = run(T("2024-05-01", odo=5, defects=[D("ADVISORY", "Worn")]), T("2025-05-01", "FAILED", 4, defects=[D("MAJOR")]), f, p)
    blob = " ".join([o["summary"]] + [g["text"] for g in o["evidence_gaps"]] + [q["text"] for q in o["follow_up_questions"]])
    assert not re.search(r"%|estimate|score|risk|probab|model|because|driven|contribut|likel", blob, re.I)
    assert not ({"score", "percent", "band", "probability", "estimate", "explanation"} & set(o))
