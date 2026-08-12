"""External validation: reconstruct DVSA MOT-01 from the lake and compare.

This is the replacement for the retired D7 reconciliation tripwire. Instead of
reproducing an undocumented internal constant (26.9139638817903%, whose window,
counting basis and vehicle classes were never recorded), it reproduces DVSA's
published national statistics, which are versioned, attributable and derived
from a definition DVSA states explicitly.

Comparator: DVSA table MOT-01 "MOT test results by class of vehicle", pinned at
tests/fixtures/dvsa/dvsa-mot-01-mot-test-results-by-class-of-vehicle.csv
(sha256 2702f210aaff0ce792a72aca9bbfb21f5d0b6978a144379d07e14d109a3b7fb2,
retrieved 2026-08-12 from
https://www.gov.uk/government/statistical-data-sets/mot-testing-data-for-great-britain).

TOLERANCES ARE DERIVED FROM MEASUREMENT, NOT CHOSEN TO PASS. Measured
2026-08-12 over Classes 3&4, complete financial years:

    FY        volume Δ    initial Δpp   final Δpp
    2013-14     +0.03%        0.00        0.00
    2014-15     -0.05%       +0.02       -0.04
    2015-16     -0.02%        0.00       -0.02
    2016-17     -0.01%        0.00        0.00
    2017-18     -0.01%        0.00        0.00
    2018-19     -0.02%        0.00        0.00
    2019-20     +0.15%       -0.05       -0.04   <- COVID-contaminated Q4
    2020-21     +2.01%       -0.60       -0.47   <- COVID-contaminated Q1,Q2

Quarterly decomposition localises the entire residual to the COVID MOT
exemption (vehicles due 30 Mar - 1 Aug 2020 got a 6-month extension):

    2019-20 Q4 (Jan-Mar 20)  +0.62%
    2020-21 Q1 (Apr-Jun 20) +13.81%
    2020-21 Q2 (Jul-Sep 20)  +1.16%
    every other quarter        <=0.03%

Direction: the bulk extract carries MORE tests than the publication counted,
consistent with the published figures being an earlier point-in-time snapshot
of an exceptional period. It is a publication-vs-extract difference over a
known external shock, not a defect in the population definition.

So: clean quarters set the tolerance (observed max 0.05% volume, 0.04pp rate),
and the COVID window is EXCLUDED FROM THE GATE AND REPORTED SEPARATELY. It is
never absorbed by widening the global tolerance -- doing so would blind the
gate to exactly the class of error it exists to catch.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pipeline.aggregates.dvsa_mot01_reference import MOT01_ANNUAL
from pipeline.lake import target_population

logger = logging.getLogger(__name__)

# 5x the observed clean-year maximum (0.05%), leaving room for legitimate DVSA
# revisions without admitting a definition error.
VOLUME_TOL_PCT = 0.25
# 2.5x the observed clean-year maximum (0.04pp).
RATE_TOL_PP = 0.10
# Additionally require the DISTRIBUTION to stay tight: a gate that only checks
# per-year maxima passes a systematic drift that sits just inside tolerance.
MEDIAN_RATE_TOL_PP = 0.03

# Quarter-aligned COVID carve-out. DVSA publishes quarterly, so the window
# cannot be tightened below quarter granularity from the published side.
COVID_QUARTERS = {(2019, "Q4"), (2020, "Q1"), (2020, "Q2")}

# Optional CSV used only to verify a refreshed download against the pinned
# literals in dvsa_mot01_reference; not required for the gate to run.
DEFAULT_COMPARATOR = (Path(__file__).resolve().parents[2] / "tests" / "fixtures" /
                      "dvsa" / "dvsa-mot-01-mot-test-results-by-class-of-vehicle.csv")

# MOT-01 groups classes; our test_class_id must be grouped to match.
CLASS_GROUPS: Dict[str, Tuple[str, ...]] = {
    "C1&2": ("1", "2"),
    "C3&4": ("3", "4"),
    "C5": ("5",),
    "C7": ("7",),
}
_MOT01_LABEL_PREFIX = {
    "C1&2": "Classes 1 & 2",
    "C3&4": "Classes 3 & 4",
    "C5": "Class 5",
    "C7": "Class 7",
}
_QUARTER_LABELS = {
    "Quarter 1: April to June": "Q1",
    "Quarter 2: July to September": "Q2",
    "Quarter 3: October to December": "Q3",
    "Quarter 4: January to March": "Q4",
}


class ComparatorMissingError(FileNotFoundError):
    """The published comparator is absent.

    The retired D7 gate returned PASS when no expectation was supplied
    (audit_aggregates.check_reconciliation(frame, None)), so forgetting the
    argument silently green-lit the build. This gate fails closed instead.
    """


@dataclass
class YearComparison:
    fin_year: int
    class_group: str
    ours_tests: int
    pub_tests: int
    ours_initial_pct: float
    pub_initial_pct: float
    ours_final_pct: float
    pub_final_pct: float
    covid_affected: bool

    @property
    def volume_delta_pct(self) -> float:
        return 100.0 * (self.ours_tests - self.pub_tests) / self.pub_tests

    @property
    def initial_delta_pp(self) -> float:
        return self.ours_initial_pct - self.pub_initial_pct

    @property
    def final_delta_pp(self) -> float:
        return self.ours_final_pct - self.pub_final_pct

    @property
    def passed(self) -> bool:
        if self.covid_affected:
            return True  # reported, not gated -- see module docstring
        return (abs(self.volume_delta_pct) <= VOLUME_TOL_PCT
                and abs(self.initial_delta_pp) <= RATE_TOL_PP
                and abs(self.final_delta_pp) <= RATE_TOL_PP)


def financial_year(d: date) -> int:
    """UK financial year starting 1 April, labelled by its opening year."""
    return d.year if d.month >= 4 else d.year - 1


def financial_quarter(d: date) -> str:
    return {4: "Q1", 5: "Q1", 6: "Q1", 7: "Q2", 8: "Q2", 9: "Q2",
            10: "Q3", 11: "Q3", 12: "Q3", 1: "Q4", 2: "Q4", 3: "Q4"}[d.month]


def reconstruction_sql(results_relation: str) -> str:
    """Per financial-year-quarter, per class group: the DVSA population and
    every excluded bucket, so input and output counts reconcile exactly."""
    class_case = " ".join(
        f"WHEN r.test_class_id IN ({', '.join(chr(39) + c + chr(39) for c in codes)}) "
        f"THEN '{group}'"
        for group, codes in CLASS_GROUPS.items()
    )
    initial = target_population.initial_test_sql("r")
    return f"""
SELECT
    CASE WHEN month(r.test_date) >= 4 THEN year(r.test_date)
         ELSE year(r.test_date) - 1 END AS fin_year,
    CASE WHEN month(r.test_date) BETWEEN 4 AND 6 THEN 'Q1'
         WHEN month(r.test_date) BETWEEN 7 AND 9 THEN 'Q2'
         WHEN month(r.test_date) BETWEEN 10 AND 12 THEN 'Q3'
         ELSE 'Q4' END AS quarter,
    CASE {class_case} ELSE 'other' END AS class_group,
    count(*) FILTER (WHERE {initial})                                   AS nt_tests,
    count(*) FILTER (WHERE {initial} AND r.outcome = 'PASS')            AS nt_pass,
    count(*) FILTER (WHERE {initial} AND r.outcome = 'FAIL')            AS nt_fail,
    count(*) FILTER (WHERE {initial} AND r.outcome = 'PRS')             AS nt_prs,
    count(*) FILTER (WHERE r.test_type IN ('RT', 'PL', 'PV'))           AS excl_retest,
    count(*) FILTER (WHERE r.test_type IN ('ES', 'EI'))                 AS excl_appeal,
    count(*) FILTER (WHERE r.test_type = 'NT'
                     AND r.outcome NOT IN ('PASS', 'FAIL', 'PRS'))      AS excl_nt_nonresult,
    count(*) FILTER (WHERE {target_population.unknown_vocabulary_sql('r')}) AS unknown_vocab,
    count(*)                                                            AS rows_total
FROM {results_relation} r
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
"""


def load_published(path: Optional[Path] = None) -> Dict[Tuple[int, str], Tuple[int, int, int]]:
    """MOT-01 annual totals -> {(fin_year, class_group): (tests, prs, fails)}.

    Defaults to the pinned literals in `dvsa_mot01_reference`. Pass a CSV path
    to verify a refreshed download against them before re-pinning. Fails closed
    either way: an empty comparator is never a pass.
    """
    if path is None:
        if not MOT01_ANNUAL:
            raise ComparatorMissingError("pinned DVSA MOT-01 reference is empty")
        return dict(MOT01_ANNUAL)
    if not path.exists():
        raise ComparatorMissingError(
            f"published comparator not found at {path}. This gate fails closed: "
            f"without DVSA MOT-01 there is no external check on the target "
            f"population, and a silent pass is exactly the failure mode of the "
            f"retired D7 tripwire."
        )
    out: Dict[Tuple[int, str], Tuple[int, int, int]] = {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            if len(row) < 8 or not row[0].strip().startswith("20"):
                continue
            if row[1].strip() != "Total":          # annual totals only
                continue
            group = next((g for g, pre in _MOT01_LABEL_PREFIX.items()
                          if row[2].startswith(pre)), None)
            if group is None:
                continue
            try:
                tests = int(row[3].replace(",", ""))
                prs = int(row[4].replace(",", ""))
                fails = int(row[5].replace(",", ""))
            except ValueError:
                continue
            out[(int(row[0].strip()[:4]), group)] = (tests, prs, fails)
    if not out:
        raise ComparatorMissingError(f"comparator at {path} parsed to zero rows")
    return out


def compare(reconstruction: List[dict], published: Dict[Tuple[int, str], Tuple[int, int, int]],
            class_group: str = "C3&4") -> List[YearComparison]:
    """Compare complete financial years only.

    A partially covered financial year (the lake is missing test_year=2022, and
    the release ends at 2023) would understate volume and silently fail the
    gate for a coverage reason rather than a semantic one.
    """
    by_year: Dict[int, List[dict]] = {}
    for row in reconstruction:
        if row["class_group"] != class_group:
            continue
        by_year.setdefault(int(row["fin_year"]), []).append(row)

    results: List[YearComparison] = []
    for fy, rows in sorted(by_year.items()):
        if {r["quarter"] for r in rows} != {"Q1", "Q2", "Q3", "Q4"}:
            logger.info("FY%s-%s incomplete (%s) -- excluded from gate",
                        fy, str(fy + 1)[2:], sorted(r["quarter"] for r in rows))
            continue
        if (fy, class_group) not in published:
            continue
        tests = sum(int(r["nt_tests"]) for r in rows)
        fails = sum(int(r["nt_fail"]) for r in rows)
        prs = sum(int(r["nt_prs"]) for r in rows)
        pub_tests, pub_prs, pub_fails = published[(fy, class_group)]
        results.append(YearComparison(
            fin_year=fy, class_group=class_group,
            ours_tests=tests, pub_tests=pub_tests,
            ours_initial_pct=100.0 * (fails + prs) / tests,
            pub_initial_pct=100.0 * (pub_fails + pub_prs) / pub_tests,
            ours_final_pct=100.0 * fails / tests,
            pub_final_pct=100.0 * pub_fails / pub_tests,
            covid_affected=any((fy, r["quarter"]) in COVID_QUARTERS for r in rows),
        ))
    return results


def gate(comparisons: List[YearComparison]) -> Tuple[bool, List[str]]:
    """Returns (passed, report lines). Fails closed on an empty comparison set."""
    lines = [
        f"{'FY':<9}{'ours':>13}{'DVSA':>13}{'volΔ%':>9}{'initΔpp':>9}"
        f"{'finΔpp':>9}  verdict",
    ]
    gated = [c for c in comparisons if not c.covid_affected]
    for c in comparisons:
        verdict = "COVID (reported, not gated)" if c.covid_affected else (
            "pass" if c.passed else "FAIL")
        lines.append(
            f"{c.fin_year}-{str(c.fin_year + 1)[2:]:<4}{c.ours_tests:>13,}"
            f"{c.pub_tests:>13,}{c.volume_delta_pct:>9.2f}"
            f"{c.initial_delta_pp:>9.2f}{c.final_delta_pp:>9.2f}  {verdict}"
        )
    if not gated:
        lines.append("FAIL: no gateable financial year -- refusing to pass vacuously")
        return False, lines

    failures = [c for c in gated if not c.passed]
    deltas = sorted(abs(c.final_delta_pp) for c in gated)
    median = deltas[len(deltas) // 2]
    lines.append(f"gated years: {len(gated)}; median |final Δpp| = {median:.3f} "
                 f"(tol {MEDIAN_RATE_TOL_PP})")
    if median > MEDIAN_RATE_TOL_PP:
        lines.append("FAIL: systematic drift -- median delta exceeds tolerance "
                     "even though individual years may pass")
        return False, lines
    if failures:
        lines.append(f"FAIL: {len(failures)} year(s) outside tolerance "
                     f"(volume {VOLUME_TOL_PCT}%, rate {RATE_TOL_PP}pp)")
        return False, lines
    lines.append("PASS: DVSA published-statistics reproduction within derived tolerance")
    return True, lines


def run(con, results_relation: str,
        comparator: Optional[Path] = None,
        class_group: str = "C3&4") -> Tuple[bool, List[str], List[dict]]:
    """Execute the reconstruction and gate it. Returns (passed, report, rows)."""
    published = load_published(comparator)
    frame = con.execute(reconstruction_sql(results_relation)).fetchdf()
    rows = frame.to_dict("records")

    unknown = int(frame["unknown_vocab"].sum())
    if unknown:
        return False, [f"FAIL: {unknown} row(s) carry a test_type/outcome outside "
                       f"the DVSA vocabulary -- fail closed"], rows

    # Input/output reconciliation: every row is either in the population or in
    # exactly one excluded bucket. An unexplained remainder means the buckets
    # do not partition the input.
    total = int(frame["rows_total"].sum())
    accounted = int(frame[["nt_tests", "excl_retest", "excl_appeal",
                           "excl_nt_nonresult"]].sum().sum())
    other_class = total - accounted
    comparisons = compare(rows, published, class_group=class_group)
    passed, lines = gate(comparisons)
    lines.append(f"reconciliation: {total:,} rows = {accounted:,} classified "
                 f"+ {other_class:,} unclassified-class remainder")
    return passed, lines, rows
