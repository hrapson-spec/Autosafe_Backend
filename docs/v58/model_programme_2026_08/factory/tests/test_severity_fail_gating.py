#!/usr/bin/env python3
"""Regression tests for the 2026-08-15 fail-gating repair in factory/severity.py.

THE BUG: classify_severity tested `dangerous_mark` BEFORE disposition, so a D-marked
ADVISORY graded `dangerous`. Measured post-2018 lake-wide: 73,814 such items, 0.248% of
everything the old rule called dangerous (F+D 28,216,146 · P+D 1,487,821 · A+D 73,814 ·
M+D 0). Under DVSA rules a dangerous defect is a failure, so only a fail-bearing
disposition can be graded dangerous.

These tests pin the repaired ordering, prove the fixture can catch a regression to the
old ordering, and assert the Python rule and its SQL twin never diverge.
"""
import itertools
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from factory import severity as sev  # noqa: E402

POST, PRE = sev.POST_2018, sev.PRE_2018
CODES_POST = ["A", "M", "F", "P"]
CODES_PRE = ["A", "F", "P"]
MARKS = [None, "", " ", "D", " D ", "X"]


# --- the regression itself ---------------------------------------------------
def test_d_marked_advisory_is_advisory_not_dangerous():
    assert sev.classify_severity("A", "D", POST) == sev.SEVERITY_ADVISORY


def test_d_marked_minor_is_minor_not_dangerous():
    assert sev.classify_severity("M", "D", POST) == sev.SEVERITY_MINOR


def test_old_ordering_would_be_caught():
    """PROOF the fixture is live: the pre-repair rule graded these dangerous."""
    def old_rule(code, mark, era):
        disp = sev.classify_disposition(code, era)
        if era == PRE:
            return sev.SEVERITY_UNGRADED
        if (mark or "").strip() == sev.DANGEROUS_MARK:
            return sev.SEVERITY_DANGEROUS
        if disp in sev.FAIL_BEARING:
            return sev.SEVERITY_MAJOR
        if disp == sev.MINOR:
            return sev.SEVERITY_MINOR
        return sev.SEVERITY_ADVISORY

    assert old_rule("A", "D", POST) == sev.SEVERITY_DANGEROUS
    assert sev.classify_severity("A", "D", POST) != old_rule("A", "D", POST)


# --- everything the repair must NOT change -----------------------------------
@pytest.mark.parametrize("code", ["F", "P"])
def test_fail_bearing_with_mark_is_dangerous(code):
    assert sev.classify_severity(code, "D", POST) == sev.SEVERITY_DANGEROUS


@pytest.mark.parametrize("code", ["F", "P"])
@pytest.mark.parametrize("mark", [None, "", " ", "X"])
def test_fail_bearing_without_mark_is_major(code, mark):
    assert sev.classify_severity(code, mark, POST) == sev.SEVERITY_MAJOR


def test_unmarked_advisory_and_minor_unchanged():
    assert sev.classify_severity("A", None, POST) == sev.SEVERITY_ADVISORY
    assert sev.classify_severity("M", None, POST) == sev.SEVERITY_MINOR


@pytest.mark.parametrize("code,mark", list(itertools.product(CODES_PRE, MARKS)))
def test_pre_2018_always_ungraded(code, mark):
    assert sev.classify_severity(code, mark, PRE) == sev.SEVERITY_UNGRADED


def test_whitespace_padded_mark_still_counts():
    assert sev.classify_severity("F", " D ", POST) == sev.SEVERITY_DANGEROUS


def test_post_2018_is_case_sensitive_and_raises():
    with pytest.raises(sev.UnknownDispositionCode):
        sev.classify_severity("m", "D", POST)
    with pytest.raises(sev.UnknownDispositionCode):
        sev.classify_severity("D", "D", POST)


# --- the anomaly stays countable ---------------------------------------------
@pytest.mark.parametrize("code", ["A", "M"])
def test_anomaly_flag_true_for_non_fail_d_marks(code):
    assert sev.is_anomalous_dangerous_mark(code, "D", POST) is True


@pytest.mark.parametrize("code", ["F", "P"])
def test_anomaly_flag_false_for_fail_bearing(code):
    assert sev.is_anomalous_dangerous_mark(code, "D", POST) is False


def test_anomaly_flag_false_without_mark_and_pre_2018():
    assert sev.is_anomalous_dangerous_mark("A", None, POST) is False
    assert sev.is_anomalous_dangerous_mark("A", "D", PRE) is False


# --- Python rule and SQL twin must never diverge -----------------------------
def _grid():
    rows = []
    for era, codes in ((POST, CODES_POST), (PRE, CODES_PRE)):
        for code, mark in itertools.product(codes, MARKS):
            rows.append((era, code, mark))
    return rows


def test_sql_twin_matches_python_exhaustively():
    rows = _grid()
    con = duckdb.connect()
    con.execute("CREATE TABLE g(era VARCHAR, code VARCHAR, mark VARCHAR)")
    con.executemany("INSERT INTO g VALUES (?,?,?)", rows)
    sev_sql = sev.severity_expr("code", "mark", "era")
    anom_sql = sev.anomalous_dangerous_mark_expr("code", "mark", "era")
    got = con.execute(
        f"SELECT era, code, mark, {sev_sql}, {anom_sql} FROM g").fetchall()
    con.close()
    assert len(got) == len(rows)
    for era, code, mark, sql_sev, sql_anom in got:
        assert sql_sev == sev.classify_severity(code, mark, era), (era, code, mark)
        assert bool(sql_anom) == sev.is_anomalous_dangerous_mark(code, mark, era), \
            (era, code, mark)


def test_sql_twin_grades_d_marked_advisory_as_advisory():
    """The SQL twin carried the same ordering bug; pin it independently."""
    con = duckdb.connect()
    expr = sev.severity_expr("'A'", "'D'", f"'{POST}'")
    assert con.execute(f"SELECT {expr}").fetchone()[0] == sev.SEVERITY_ADVISORY
    con.close()


def test_fail_bearing_basis_unaffected_by_the_repair():
    """n_major_or_dangerous is the F+P count and must not move."""
    for code in ("F", "P"):
        assert sev.is_fail_bearing(code, POST, sev.BASIS_INITIAL) is True
    assert sev.is_fail_bearing("F", POST, sev.BASIS_FINAL) is True
    assert sev.is_fail_bearing("P", POST, sev.BASIS_FINAL) is False
    for code in ("A", "M"):
        assert sev.is_fail_bearing(code, POST, sev.BASIS_INITIAL) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
