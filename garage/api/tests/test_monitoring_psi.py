"""G6 evaluator tests: synthetic data only (no real MOT data, labels, 2024+ rows or network)."""
import hashlib
import json
import math
import random

import pytest

from garage.api.monitoring import (ReferenceArtefact, build_reference_dict, dumps_canonical, evaluate_g6,
                                   psi_from_counts, simulate_null)
from garage.api.monitoring._stats import MonitoringInputError, auc, quantile, wilson_interval
from garage.api.monitoring.psi import expected_null_psi
from garage.api.monitoring.reference import Binner, ReferenceArtefactError, normalise_category

KINDS = {"age": {"kind": "continuous"}, "odometer": {"kind": "continuous"}, "diesel": {"kind": "binary"},
         "make": {"kind": "categorical", "top_n": 3}}
MAKES = ["FORD", "VAUXHALL", "VW", "BMW", "AUDI", "TOYOTA"]
MAKE_W = [30, 25, 20, 12, 8, 5]


def synth_row(rng, i, *, age_shift=0.0, make_override=None, prefix="r"):
    age = rng.gauss(8 + age_shift, 3)
    odo = max(0.0, rng.gauss(70000 + age * 1000, 20000))
    row = {"booking_id": f"{prefix}{i}", "vehicle_id": f"{prefix}v{i}", "workshop_id": f"w{i % 3}",
           "supported": True,
           "features": {"age": age, "odometer": odo, "diesel": rng.random() < 0.4,
                        "make": make_override(rng) if make_override else rng.choices(MAKES, MAKE_W)[0]},
           "score": min(0.99, max(0.01, 0.2 + 0.02 * (age - 8) + rng.gauss(0, 0.05)))}
    return row


def synth(n, seed, **kw):
    rng = random.Random(seed)
    return [synth_row(rng, i, **kw) for i in range(n)]


def dev_rows_flat(rows):
    return [{**r["features"], "score": r["score"]} for r in rows]


@pytest.fixture(scope="module")
def dev():
    return synth(5000, 1, prefix="d")


@pytest.fixture(scope="module")
def ref(dev):
    d = build_reference_dict(dev_rows_flat(dev), KINDS, artefact_id="ref-test-1", reference_source="fixture",
                             bins_frozen_utc="2026-10-30T00:00:00Z")
    return d


def make_artefact(d, source=None):
    d = json.loads(json.dumps(d))
    if source:
        d["reference_source"] = source
    raw = dumps_canonical(d)
    return ReferenceArtefact.from_bytes(raw, hashlib.sha256(raw).hexdigest())


@pytest.fixture(scope="module")
def art(ref):
    return make_artefact(ref, "development_<=2023")


# ------------------------------------------------------------------ helpers with known answers
def test_psi_known_answers():
    # no smoothing: (.5-.3)ln(.5/.3) + (.5-.7)ln(.5/.7) = 0.169459...
    assert psi_from_counts([50, 50], [30, 70], smoothing=0) == pytest.approx(0.1694589, abs=1e-6)
    assert psi_from_counts([10, 20, 30], [10, 20, 30]) == 0.0
    # with +0.5: (20/11) ln 21
    assert psi_from_counts([0, 10], [10, 0]) == pytest.approx(20 / 11 * math.log(21), abs=1e-9)
    with pytest.raises(MonitoringInputError):
        psi_from_counts([1], [1, 2])


def test_expected_null_psi_formula():
    assert expected_null_psi(500, 5000, 11) == pytest.approx(10 * (1 / 500 + 1 / 5000))


def test_wilson_auc_quantile_known_answers():
    lo, hi = wilson_interval(50, 100)
    assert (round(lo, 4), round(hi, 4)) == (0.4038, 0.5962)
    assert wilson_interval(0, 0) == (None, None)
    assert auc([0.1, 0.4, 0.35, 0.8], [0, 0, 1, 1]) == pytest.approx(0.75)
    assert auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) == 0.5
    assert auc([0.1, 0.2], [1, 1]) is None
    assert quantile([1, 2, 3, 4], 0.5) == 2.5


# ------------------------------------------------------------------ binning rules
def test_continuous_bins_right_open_missing_and_unparseable():
    b = Binner("x", {"kind": "continuous", "edges": [1.0, 2.0]})
    assert b.labels == ["bin0", "bin1", "bin2", "MISSING"]
    assert [b.classify(v)[0] for v in (-5, 0.999, 1.0, 1.5, 2.0, 99, float("inf"))] == [0, 0, 1, 1, 2, 2, 2]
    assert b.classify(None) == (3, None) and b.classify(float("nan")) == (3, None)
    assert b.classify("7") == (3, "unparseable") and b.classify(True) == (3, "unparseable")


def test_binary_bins():
    b = Binner("x", {"kind": "binary"})
    assert [b.classify(v)[0] for v in (0, 1, False, True, 0.0, 1.0)] == [0, 1, 0, 1, 0, 1]
    assert b.classify(None) == (2, None) and b.classify(2) == (2, "unparseable")


def test_categorical_bins_other_unknown_unseen():
    b = Binner("make", {"kind": "categorical", "categories": ["FORD", "VW"], "known_values": ["FORD", "VW", "KIA"]})
    assert b.labels == ["FORD", "VW", "OTHER", "UNKNOWN_MISSING"]
    assert b.classify(" ford ") == (0, None)
    assert b.classify("KIA") == (2, "other")             # known in development but rare -> OTHER
    assert b.classify("TESLA") == (2, "unseen")          # never seen in development
    for v in (None, "", "  ", "unknown", "Missing", float("nan")):
        assert b.classify(v) == (3, None)
    assert normalise_category(" a b ") == "A B"


def test_builder_rules_deciles_collapse_topn_and_1pct():
    rows = [{"x": v, "c": c, "score": 0.5} for v, c in
            zip([0] * 50 + list(range(1, 51)), ["A"] * 40 + ["B"] * 30 + ["C"] * 20 + ["D"] * 9 + ["E"] * 1)]
    d = build_reference_dict(rows, {"x": {"kind": "continuous"}, "c": {"kind": "categorical", "top_n": 2}},
                             artefact_id="t", reference_source="fixture", bins_frozen_utc="2026-10-30T00:00:00Z")
    e = d["inputs"]["x"]["edges"]
    assert e == sorted(set(e)) and 0.0 in e                      # deciles of a half-zero sample collapse
    assert len(e) < 9                                            # at least one identical edge collapsed
    # top-2 (A, B) plus every value with frequency >= 1%: C 20%, D 9%, E 1% -> all kept (E is exactly 1%)
    assert d["inputs"]["c"]["categories"] == ["A", "B", "C", "D", "E"]
    d2 = build_reference_dict(rows, {"x": {"kind": "continuous"}, "c": {"kind": "categorical", "top_n": 2}},
                              artefact_id="t", reference_source="fixture", bins_frozen_utc="2026-10-30T00:00:00Z",
                              min_freq=0.10)
    assert d2["inputs"]["c"]["categories"] == ["A", "B", "C"]
    assert d2["inputs"]["c"]["ref_counts"] == [40, 30, 20, 10, 0]   # D+E -> OTHER
    assert sum(d["inputs"]["x"]["ref_counts"]) == 100


# ------------------------------------------------------------------ artefact pinning
def test_artefact_requires_matching_pin(ref):
    raw = dumps_canonical(ref)
    good = hashlib.sha256(raw).hexdigest()
    assert ReferenceArtefact.from_bytes(raw, good).sha256 == good
    with pytest.raises(ReferenceArtefactError, match="mismatch"):
        ReferenceArtefact.from_bytes(raw, "0" * 64)
    with pytest.raises(ReferenceArtefactError, match="64 lowercase hex"):
        ReferenceArtefact.from_bytes(raw, good.upper())
    with pytest.raises(TypeError):
        ReferenceArtefact.from_bytes(raw)                       # the pin is not optional
    tampered = raw.replace(b'"reference_n": 5000', b'"reference_n": 5001')
    assert tampered != raw
    with pytest.raises(ReferenceArtefactError, match="mismatch"):
        ReferenceArtefact.from_bytes(tampered, good)


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d.update(schema="x"), "schema"),
    (lambda d: d.update(smoothing=1.0), "smoothing"),
    (lambda d: d.update(reference_source="whatever"), "reference_source"),
    (lambda d: d["inputs"]["age"].update(edges=[2.0, 1.0]), "strictly increasing"),
    (lambda d: d["inputs"]["age"]["ref_counts"].__setitem__(0, d["inputs"]["age"]["ref_counts"][0] + 1), "sum"),
    (lambda d: d["inputs"]["make"].update(categories=["FORD", "FORD"]), "duplicate"),
    (lambda d: d["inputs"]["make"].update(categories=["ford"]), "normalised"),
    (lambda d: d["inputs"].update(score=d["score"]), "score"),
])
def test_artefact_validation(ref, mutate, msg):
    d = json.loads(json.dumps(ref))
    mutate(d)
    raw = dumps_canonical(d)
    with pytest.raises(ReferenceArtefactError, match=msg):
        ReferenceArtefact.from_bytes(raw, hashlib.sha256(raw).hexdigest())


# ------------------------------------------------------------------ G6 evaluator
def run(rows, art, **kw):
    kw.setdefault("outcome_access_start_utc", "2026-12-01T00:00:00Z")
    kw.setdefault("population", "prospective_pilot")
    kw.setdefault("n_boot", 100)
    return evaluate_g6(rows, art, **kw)


def test_same_distribution_passes_and_reports_everything(art):
    r = run(synth(600, 2), art)
    assert r["status"] == "pass" and r["criteria_met"] and r["n_supported"] == 600
    assert r["max_psi"] < 0.10 and set(r["psi"]) == {"age", "odometer", "diesel", "make", "score"}
    assert r["n_tests_multiplicity"] == 5
    e = r["psi"]["age"]
    assert e["ci_simultaneous_lo"] <= e["ci_lo"] <= e["ci_hi"] <= e["ci_simultaneous_hi"]
    assert e["expected_null_psi"] == pytest.approx(expected_null_psi(600, 5000, len(art.inputs["age"]["ref_counts"])))
    assert r["psi"]["make"]["unseen_rate"] == 0.0
    assert r["bootstrap"]["n_boot"] == 100 and "Bonferroni" in r["bootstrap"]["simultaneous"]
    assert any("Multiplicity" in n for n in r["notes"])
    json.dumps(r)                                         # JSON-serialisable


def test_shifted_input_fails_and_is_named(art):
    r = run(synth(600, 3, age_shift=4.0), art)
    assert r["status"] == "fail" and not r["criteria_met"]
    assert r["psi"]["age"]["breach"] and r["max_psi_input"] in ("age", "score", "odometer")
    assert r["psi"]["diesel"]["breach"] is False


def test_psi_known_answer_through_evaluator():
    # one binary input, reference 50/50, current 60/40 (no missing): smoothing adds .5 to each of 3 cells
    rows_ref = [{"b": i % 2, "score": 0.1 * (i % 10)} for i in range(1000)]
    d = build_reference_dict(rows_ref, {"b": {"kind": "binary"}}, artefact_id="k", reference_source="fixture",
                             bins_frozen_utc="2026-10-30T00:00:00Z")
    a = make_artefact(d)
    cur = [{"booking_id": i, "supported": True, "features": {"b": 1 if i < 300 else 0}, "score": 0.1 * (i % 10)}
           for i in range(500)]
    r = evaluate_g6(cur, a, outcome_access_start_utc="NOT_STARTED", population="fixture", n_boot=0)
    exp = psi_from_counts([200, 300, 0], [500, 500, 0])
    assert r["psi"]["b"]["psi"] == pytest.approx(exp)
    assert 0.0 < exp < 0.10 + 0.05
    assert r["psi"]["score"]["psi"] < 0.01


def test_minimum_count_rule_counts_supported_only(art):
    rows = synth(600, 4)
    for r in rows[:101]:                                  # 499 supported remain
        r["supported"] = False
    r = run(rows, art)
    assert r["n_supported"] == 499 and r["status"] == "insufficient_n" and r["n_unsupported"] == 101
    assert r["unsupported_share"] == pytest.approx(101 / 600) and r["unsupported_guard_ok"] is True
    rows[100]["supported"] = True                          # 500 supported
    assert run(rows, art)["status"] == "pass"


def test_unsupported_guard_reported_separately(art):
    rows = synth(1000, 5)
    for r in rows[:350]:
        r["supported"] = False
    r = run(rows, art)
    assert r["unsupported_guard_ok"] is False and r["status"] == "pass"       # guard not folded into status


def test_fixture_population_or_reference_can_never_pass(art, ref):
    rows = synth(600, 6)
    r = run(rows, art, population="fixture")
    assert r["criteria_met"] and r["status"] == "fixture_only"
    r2 = run(rows, make_artefact(ref))                      # reference_source == 'fixture'
    assert r2["status"] == "fixture_only"
    with pytest.raises(MonitoringInputError):
        run(rows, art, population="pilot")


def test_freeze_must_precede_outcome_access(art):
    rows = synth(500, 7)
    with pytest.raises(MonitoringInputError, match="before outcome access"):
        run(rows, art, outcome_access_start_utc="2026-10-29T00:00:00Z")
    assert run(rows, art, outcome_access_start_utc="NOT_STARTED")["freeze_precedes_outcome_access"] is True
    with pytest.raises(MonitoringInputError):
        run(rows, art, outcome_access_start_utc="2026-12-01T00:00:00")       # naive timestamp refused
    with pytest.raises(TypeError):
        evaluate_g6(rows, art, population="fixture")                          # pin of the timing is required


def test_reference_must_be_an_artefact(ref):
    with pytest.raises(MonitoringInputError):
        run(synth(10, 1), ref)


def test_input_validation(art):
    rows = synth(600, 8)
    dup = rows + [dict(rows[0])]
    with pytest.raises(MonitoringInputError, match="duplicate"):
        run(dup, art)
    gone = synth(600, 9)
    for r in gone:
        del r["features"]["odometer"]
    with pytest.raises(MonitoringInputError, match="absent from every row"):
        run(gone, art)


def test_rare_and_unseen_categories(art):
    rng_make = lambda rng: "TESLA" if rng.random() < 0.15 else rng.choices(MAKES, MAKE_W)[0]   # noqa: E731
    r = run(synth(600, 10, make_override=rng_make), art)
    m = r["psi"]["make"]
    assert 0.08 < m["unseen_rate"] < 0.22 and m["other_rate"] >= m["unseen_rate"]
    assert m["psi"] > 0.10 and r["status"] == "fail"
    nulls = lambda rng: rng.choice([None, "", "unknown"])                                          # noqa: E731
    r2 = run(synth(600, 11, make_override=nulls), art)
    assert r2["psi"]["make"]["unseen_rate"] == 0.0 and r2["psi"]["make"]["missing_rate"] == 1.0


def test_unparseable_values_are_counted_not_hidden(art):
    rows = synth(600, 12)
    for r in rows[:30]:
        r["features"]["odometer"] = "12,345"                 # string where a number is required
    r = run(rows, art)
    assert r["psi"]["odometer"]["n_unparseable"] == 30 and r["psi"]["odometer"]["missing_rate"] >= 0.05


def test_deterministic_and_vehicle_clustered(art):
    rows = synth(600, 13)
    a, b = run(rows, art), run(rows, art)
    assert a == b
    for i, r in enumerate(rows):                              # 3 bookings per vehicle
        r["vehicle_id"] = f"veh{i // 3}"
    c = run(rows, art)
    assert c["bootstrap"]["n_clusters"] == 200
    w_unclustered = a["psi"]["age"]["ci_hi"] - a["psi"]["age"]["ci_lo"]
    assert c["psi"]["age"]["ci_hi"] - c["psi"]["age"]["ci_lo"] > 0 and w_unclustered > 0


def test_no_supported_rows(art):
    rows = synth(5, 14)
    for r in rows:
        r["supported"] = False
    r = run(rows, art)
    assert r["status"] == "insufficient_n" and r["max_psi"] is None


def test_null_simulation(dev, art):
    sub = [{"vehicle_id": r["vehicle_id"], "features": r["features"], "score": r["score"]} for r in dev[:700]]
    d = build_reference_dict(dev_rows_flat(dev[:700]), KINDS, artefact_id="n", reference_source="fixture",
                             bins_frozen_utc="2026-10-30T00:00:00Z")
    a = make_artefact(d)
    res = simulate_null(sub, a, n_vehicles=500, reps=60, seed=1)
    assert res["reps"] == 60 and set(res["per_input"]) == {"age", "odometer", "diesel", "make", "score"}
    assert 0.0 <= res["false_alarm_rate_any_psi_above_threshold"] <= 1.0
    assert res["max_over_inputs"]["max"] >= res["per_input"]["age"]["mean"] >= 0
    assert "make" in res["other_rate"] and "not replace" in res["note"]
    assert simulate_null(sub, a, n_vehicles=500, reps=60, seed=1) == res
    # drawing the whole development set reproduces the reference exactly -> PSI is exactly zero
    full = simulate_null(sub[:500], make_artefact(build_reference_dict(
        dev_rows_flat(dev[:500]), KINDS, artefact_id="n2", reference_source="fixture",
        bins_frozen_utc="2026-10-30T00:00:00Z")), n_vehicles=500, reps=5, seed=2)
    assert full["max_over_inputs"]["max"] == 0.0 and full["false_alarm_rate_any_psi_above_threshold"] == 0.0
    with pytest.raises(MonitoringInputError):
        simulate_null(sub[:10], a, n_vehicles=500, reps=2)
