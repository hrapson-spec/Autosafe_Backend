"""Briefing engine on 50 fully authored synthetic histories. Independent raw JSON PRS recount."""
import copy
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone

import jsonschema
import pytest

from garage.api import briefing as B
from garage.api import export_briefing_fixtures as X
from garage.api.dvsa import FIXTURE_DIR, FakeDvsaClient
from .contract_check import _convert, load
from .test_briefing_rules import ALLOWED_PHRASES, FORBIDDEN, _strip_allowed, strip_quotes

PLATES = X.fixture_plates()
FAKE = FakeDvsaClient()


def raw(plate):
    return json.loads((FIXTURE_DIR / f"{plate}.json").read_text())


def test_there_are_50_fixtures_with_synthetic_plates_only():
    assert len(PLATES) == 50 and all(re.fullmatch(r"ZZ\d\dFXT", p) for p in PLATES)
    for p in PLATES:
        d = raw(p)
        assert d["registration"] == p and all(t["registrationAtTimeOfTest"] == p for t in d["motTests"])
    nums = [t["motTestNumber"] for p in PLATES for t in raw(p)["motTests"]]
    assert len(nums) == len(set(nums)) == 405
    # no file name or content carries a hash of a real plate (T1b names were sha256(plate)[:16])
    assert not [f for f in FIXTURE_DIR.glob("*.json") if re.fullmatch(r"[0-9a-f]{16}", f.stem)]
    assert not (FIXTURE_DIR / "plate_map.json").exists()


def test_independent_recount_of_prs_events_from_raw_json_matches_synthetic_cases_and_the_engine():
    """Five authored PRS-only fail/pass pairs, each separated by one second."""
    n_pairs = n_prs_rows = n_tests = 0
    engine_rectified = engine_events = 0
    for p in PLATES:
        tests = sorted(raw(p)["motTests"], key=lambda t: t["completedDate"])
        n_tests += len(tests)
        pairs_here = 0
        for i, t in enumerate(tests):
            types = [d["type"] for d in t["defects"]]
            n_prs_rows += types.count("PRS")
            if t["testResult"] == "FAILED" and "PRS" in types and not {"MAJOR", "DANGEROUS"} & set(types):
                nxt = tests[i + 1]
                a = datetime.fromisoformat(t["completedDate"].replace("Z", "+00:00"))
                b = datetime.fromisoformat(nxt["completedDate"].replace("Z", "+00:00"))
                assert nxt["testResult"] == "PASSED" and (b - a) == timedelta(seconds=1)
                pairs_here += 1
        n_pairs += pairs_here
        events = B.build_events(FAKE.fetch_history(p).tests)
        engine_events += len(events)
        engine_rectified += sum(e.kind == "RECTIFIED" for e in events)
        assert not [e for e in events if e.unpaired_prs]
        assert pairs_here == sum(e.kind == "RECTIFIED" for e in events), p
        # no remaining FAILED event is PRS-only: every PRS-typed row sits in a RECTIFIED event
        for e in events:
            if e.kind == "FAILED":
                assert not any(d["type"] == "PRS" for d in e.defects), p
    assert (n_tests, n_pairs, n_prs_rows) == (405, 5, 5)
    assert engine_rectified == 5 and engine_events == 405 - 5


def briefing_for(p, cutoff=X.CUTOFF, at=X.CUTOFF_AT):
    h = FAKE.fetch_history(p).to_json()
    return B.build(h, "ok", "f" * 64, cutoff, at)


def test_every_fixture_yields_a_briefing_and_no_record_is_on_or_after_the_cutoff():
    for p in PLATES:
        full = FAKE.fetch_history(p)
        dates = sorted({t.test_date for t in full.tests})
        for cut in (dates[3], dates[-1], X.CUTOFF):
            out = briefing_for(p, cut, datetime.combine(cut, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12))
            assert all(date.fromisoformat(r["test_date"]) < cut for r in out["source_records"]), (p, cut)
            assert len(out["source_records"]) <= B.RECORD_LIMIT
            assert out["summary"] and out["history_cutoff_date"] == cut.isoformat()


def test_every_defect_in_every_record_is_dated_labelled_and_historical():
    pat = re.compile(r"^(Advisory|Minor defect|Major defect|Dangerous defect|Defect rectified at the test), "
                     r"recorded at test on \d{1,2} [A-Z][a-z]+ \d{4}$")
    n = 0
    for p in PLATES:
        for r in briefing_for(p)["source_records"]:
            for d in r["defects"]:
                n += 1
                assert pat.match(d["label"]) and d["recorded_on"] == r["test_date"], d
                assert d["label"].endswith(B.fmt_date(date.fromisoformat(r["test_date"])))
    assert n > 40


def test_rendered_wording_on_synthetic_data_outside_quotations_is_past_tense():
    seen_rules = set()
    for p in PLATES:
        dates = sorted({t.test_date for t in FAKE.fetch_history(p).tests})
        for cut in (dates[4], dates[-1], X.CUTOFF, date(2028, 1, 1)):       # the last one makes every history stale
            out = briefing_for(p, cut, datetime.combine(cut, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12))
            for t in [out["summary"]] + [g["text"] for g in out["evidence_gaps"]] + [q["text"] for q in out["follow_up_questions"]]:
                assert FORBIDDEN.findall(_strip_allowed(strip_quotes(t))) == [], t
            seen_rules |= {g["rule_id"] for g in out["evidence_gaps"]} | {q["rule_id"] for q in out["follow_up_questions"]}
    # synthetic data exercises the pairing, repeat/last-test advisory, fail->pass and rectified rules
    assert {"Q02_fail_then_pass", "Q03_rectified_at_last_test", "Q04_repeat_advisory", "Q05_last_test_advisory",
            "G04_stale_history"} <= seen_rules


def test_question_sources_are_real_dated_records_in_the_same_briefing():
    for p in PLATES:
        out = briefing_for(p)
        dates = {r["test_date"] for r in out["source_records"]}
        for q in out["follow_up_questions"]:
            assert q["source_test_date"] in dates, (p, q)


def test_input_order_permutation_gives_identical_briefings_on_synthetic_data():
    import random
    for p in PLATES[:10]:
        d = FAKE.fetch_history(p).to_json()
        want = B.build(d, "ok", "f" * 64, X.CUTOFF, X.CUTOFF_AT)
        shuf = copy.deepcopy(d)
        random.Random(7).shuffle(shuf["tests"])
        assert B.build(shuf, "ok", "f" * 64, X.CUTOFF, X.CUTOFF_AT) == want


def test_exported_web_fixtures_are_fresh_and_match_the_contract():
    assert json.loads(X.EVIDENCE_OUT.read_text()) == X.fixture_evidence()
    assert X.OUT.read_text() == X.render(), "run: python -m garage.api.export_briefing_fixtures"
    doc, comps, _ = load()
    schema = _convert(copy.deepcopy(doc["components"]["schemas"]["Briefing"]))
    v = jsonschema.Draft7Validator({"allOf": [schema], "components": comps})
    for plate, b in json.loads(X.OUT.read_text()).items():
        full = {**b, "booking_id": str(uuid.uuid4()), "status": "ready", "reason": None, "reason_text": None,
                "assessment_id": str(uuid.uuid4()), "assessment_version": 1, "feedback": None}
        v.validate(full)


def test_every_application_history_is_exactly_the_authored_synthetic_generator():
    from garage.api.generate_synthetic_fixtures import fixtures, render
    expected = fixtures()
    assert {p.stem for p in FIXTURE_DIR.glob("*.json")} == set(expected)
    for reg, value in expected.items():
        assert (FIXTURE_DIR / f"{reg}.json").read_text() == render(value)
