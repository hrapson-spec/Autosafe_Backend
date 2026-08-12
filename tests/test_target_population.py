"""Regression protection for the DVSA target population (decision D7, revised).

These tests encode the invariants that the retired cycle reconstruction
violated. Each maps to a measured defect:

- non-monotone test_id must not alter membership  -> Defect #18 (same-day
  retest sorted before its own originating failure in 43,403/43,791 cases);
- shuffled physical input order must not alter membership -> the population
  must not depend on any ordering at all;
- fail-closed on unknown vocabulary -> the retired gate passed silently when
  no expectation was supplied.
"""
import random
from datetime import date

import pytest

from pipeline.lake import target_population as tp

pytest.importorskip("duckdb")
import duckdb  # noqa: E402


# --- vocabulary and rule of record ------------------------------------------

def test_dvsa_lookup_vocabulary_is_complete_and_partitioned():
    """The six DVSA test types, each classified exactly once."""
    assert tp.KNOWN_TEST_TYPES == {"NT", "RT", "PL", "PV", "ES", "EI"}
    assert tp.RETEST_TEST_TYPES & tp.APPEAL_TEST_TYPES == set()
    assert tp.INITIAL_TEST_TYPE not in tp.RETEST_TEST_TYPES | tp.APPEAL_TEST_TYPES
    assert tp.RESULT_OUTCOMES & tp.NON_RESULT_OUTCOMES == set()


@pytest.mark.parametrize("test_type,outcome,expected", [
    ("NT", "PASS", True), ("NT", "FAIL", True), ("NT", "PRS", True),
    ("NT", "ABANDONED", False), ("NT", "ABORTED", False),
    ("NT", "ABORTED_VE", False), ("NT", "REFUSED", False),
    ("RT", "PASS", False), ("RT", "FAIL", False), ("RT", "PRS", False),
    ("PL", "PASS", False), ("PV", "PASS", False),
    ("ES", "FAIL", False), ("EI", "PASS", False),
])
def test_initial_test_membership(test_type, outcome, expected):
    assert tp.is_initial_test(test_type, outcome) is expected


def test_retests_and_aborts_can_never_enter_the_population():
    for tt in tp.RETEST_TEST_TYPES | tp.APPEAL_TEST_TYPES:
        for oc in tp.KNOWN_OUTCOMES:
            assert not tp.is_initial_test(tt, oc)
    for oc in tp.NON_RESULT_OUTCOMES:
        assert not tp.is_initial_test("NT", oc)


def test_label_is_final_basis_and_prs_is_a_pass():
    """Historical AutoSafe label semantics, preserved exactly."""
    assert tp.is_final_failure("NT", "FAIL") is True
    assert tp.is_final_failure("NT", "PRS") is False   # PRS is a pass
    assert tp.is_initial_failure("NT", "PRS") is True  # DVSA initial basis differs
    assert tp.is_final_failure("RT", "FAIL") is False  # retest never labelled


def test_unknown_vocabulary_fails_closed():
    with pytest.raises(tp.UnknownTestTypeError):
        tp.is_initial_test("XX", "PASS")
    with pytest.raises(tp.UnknownOutcomeError):
        tp.is_initial_test("NT", "UNKNOWN")
    with pytest.raises(tp.UnknownTestTypeError):
        tp.assert_known_test_types(["NT", "RT", "ZZ"])
    with pytest.raises(tp.UnknownOutcomeError):
        tp.assert_known_outcomes(["PASS", "UNKNOWN"])


# --- SQL twin equivalence ----------------------------------------------------

@pytest.fixture()
def con():
    c = duckdb.connect()
    c.execute("CREATE TABLE r (test_id BIGINT, test_type VARCHAR, outcome VARCHAR)")
    return c


def test_sql_twin_matches_rule_of_record(con):
    rows = [(i, tt, oc) for i, (tt, oc) in enumerate(
        (tt, oc) for tt in sorted(tp.KNOWN_TEST_TYPES) for oc in sorted(tp.KNOWN_OUTCOMES))]
    con.executemany("INSERT INTO r VALUES (?, ?, ?)", rows)
    sql_ids = {r[0] for r in con.execute(
        f"SELECT test_id FROM r WHERE {tp.initial_test_sql('r')}").fetchall()}
    py_ids = {i for i, tt, oc in rows if tp.is_initial_test(tt, oc)}
    assert sql_ids == py_ids
    assert py_ids, "fixture must contain at least one initial test"


def test_unknown_vocabulary_sql_flags_exactly_the_bad_rows(con):
    con.executemany("INSERT INTO r VALUES (?, ?, ?)", [
        (1, "NT", "PASS"), (2, "RT", "FAIL"),
        (3, "XX", "PASS"), (4, "NT", "UNKNOWN"), (5, None, "PASS"), (6, "NT", None),
    ])
    flagged = {r[0] for r in con.execute(
        f"SELECT test_id FROM r WHERE {tp.unknown_vocabulary_sql('r')}").fetchall()}
    assert flagged == {3, 4, 5, 6}


# --- the invariants the cycle reconstruction violated ------------------------

def _population(con, rows):
    con.execute("DELETE FROM r")
    con.executemany("INSERT INTO r VALUES (?, ?, ?)", rows)
    return {x[0] for x in con.execute(
        f"SELECT test_id FROM r WHERE {tp.initial_test_sql('r')}").fetchall()}


def test_non_monotone_test_id_cannot_alter_membership(con):
    """Defect #18 shape: a same-day retest carrying a LOWER test_id than the
    NT that caused it. Under the cycle rule this inverted the sequence and
    promoted the retest to a cycle-first row; membership here is per-row, so
    the id ordering is irrelevant by construction.
    """
    inverted = [(100, "RT", "PASS"), (999, "NT", "FAIL")]   # retest has lower id
    natural = [(999, "RT", "PASS"), (1000, "NT", "FAIL")]   # retest has higher id
    assert _population(con, inverted) == {999}
    assert _population(con, natural) == {1000}


def test_shuffled_input_order_cannot_alter_membership(con):
    rows = [(1, "NT", "FAIL"), (2, "RT", "PASS"), (3, "NT", "PASS"),
            (4, "NT", "ABORTED"), (5, "ES", "FAIL"), (6, "NT", "PRS")]
    baseline = _population(con, rows)
    rng = random.Random(0)
    for _ in range(8):
        shuffled = rows[:]
        rng.shuffle(shuffled)
        assert _population(con, shuffled) == baseline
    assert baseline == {1, 3, 6}


def test_genuine_initial_tests_after_a_failure_are_retained(con):
    """A full NT 30 days after a failure is a genuine new presentation.

    The 45-day cycle rule deleted these (8,185 rows/1.22% of initial tests in
    the 2019 C3&4 sample sat 15-45 days after a failure, i.e. OUTSIDE the
    statutory 10-working-day retest entitlement, so they were necessarily new
    full-fee tests). DVSA records them as NT and so do we.
    """
    rows = [(1, "NT", "FAIL"), (2, "NT", "PASS")]  # second is 30 days later
    assert _population(con, rows) == {1, 2}


# --- published-statistics gate ----------------------------------------------

def test_gate_fails_closed_on_missing_comparator(tmp_path):
    from pipeline.aggregates import published_stats_gate as g
    with pytest.raises(g.ComparatorMissingError):
        g.load_published(tmp_path / "nope.csv")


def test_pinned_reference_matches_the_published_csv_when_present():
    """A refreshed download must be diffed against the pinned literals."""
    from pipeline.aggregates import published_stats_gate as g
    if not g.DEFAULT_COMPARATOR.exists():
        pytest.skip("comparator CSV not present (it is gitignored by *.csv)")
    from_csv = g.load_published(g.DEFAULT_COMPARATOR)
    pinned = g.load_published()
    shared = set(from_csv) & set(pinned)
    assert shared, "no overlap between CSV and pinned reference"
    mismatched = {k: (from_csv[k], pinned[k]) for k in shared if from_csv[k] != pinned[k]}
    assert not mismatched, f"pinned reference is stale: {mismatched}"


def test_gate_never_passes_vacuously():
    """Zero gateable years must FAIL, not pass by absence of evidence."""
    from pipeline.aggregates import published_stats_gate as g
    passed, lines = g.gate([])
    assert passed is False
    assert any("vacuously" in ln for ln in lines)


def test_covid_years_are_reported_but_not_gated():
    from pipeline.aggregates import published_stats_gate as g
    covid = g.YearComparison(2020, "C3&4", 30_854_134, 30_246_272,
                             29.63, 30.23, 23.11, 23.58, covid_affected=True)
    assert covid.passed          # excluded from the gate...
    assert abs(covid.volume_delta_pct) > g.VOLUME_TOL_PCT   # ...though far outside it
    passed, lines = g.gate([covid])
    assert passed is False       # ...and cannot carry the gate alone
    assert any("COVID" in ln for ln in lines)


def test_a_real_regression_fails_the_gate():
    from pipeline.aggregates import published_stats_gate as g
    good = g.YearComparison(2017, "C3&4", 28_874_533, 28_877_225,
                            34.48, 34.48, 26.22, 26.22, covid_affected=False)
    # a 1.3pp depression is the size the same-day ordering defect produced
    bad = g.YearComparison(2018, "C3&4", 29_556_303, 29_560_831,
                           33.60, 33.59, 24.90, 26.15, covid_affected=False)
    assert g.gate([good])[0] is True
    assert g.gate([good, bad])[0] is False
