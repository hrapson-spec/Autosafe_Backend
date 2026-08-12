"""Lake acceptance checks. Every gate returns a CheckResult; the runner
exits non-zero on any failure -- gates are enforced, never merely printed
(the v55 trainer's printed-not-enforced gates are a recorded defect class).

The single most important gate is check_vehicle_continuity: the whole
full-depth premise rests on the downloaded publication carrying ONE
consistent vehicle_id space across 2005->present. If vehicle identities
reset per source file, cross-year history accumulation is impossible and
the program's window must be renegotiated BEFORE any further work
(riskiest-assumption #1 in the approved plan). Run this on day 1.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .normalize import TAXONOMY_SWITCH_DATE


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def _result(name: str, passed: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=bool(passed), detail=detail)


def check_vehicle_continuity(con, results_relation: str,
                             sample_size: int = 10_000,
                             min_multiyear_share: float = 0.20,
                             max_first_use_conflict_share: float = 0.01,
                             min_median_gap_days: int = 200,
                             max_median_gap_days: int = 800) -> CheckResult:
    """Day-1 gate: is vehicle_id one consistent space across the full depth?

    Three signals over a sample of multi-test vehicles:
    - multiyear_share: share whose tests span >=2 calendar years. Annual
      MOTs mean a genuine multi-test vehicle almost always spans years; if
      IDs reset per source file this collapses toward zero. THE tripwire.
    - first_use_conflict_share: share carrying >1 distinct non-null
      first_use_date (an identity attribute; conflicts mean ID collisions).
    - median inter-test gap: annual test discipline puts the median around
      365 days; a mixed/reset ID space distorts it wildly.
    """
    # NOTE (2026-08-11 workstation): USING SAMPLE binds to the input scan in
    # DuckDB, so placing it inside the grouped SELECT sampled RAW ROWS before
    # aggregation — on a full-depth lake no vehicle has >=3 tests within a
    # 10k-row sample of 450M, and the gate could only ever fail (verified
    # live; unit fixtures were too small to expose it). Sampling now applies
    # to the grouped multi-test-vehicle set, the documented intent. The three
    # PASS thresholds are unchanged.
    sampled = f"""
    WITH multi AS (
        SELECT vehicle_id FROM (
            SELECT vehicle_id
            FROM {results_relation}
            GROUP BY vehicle_id
            HAVING count(*) >= 3
        ) USING SAMPLE reservoir({sample_size} ROWS) REPEATABLE (42)
    )
    """
    row = con.execute(sampled + f"""
    , per_vehicle AS (
        SELECT r.vehicle_id,
               count(DISTINCT year(r.test_date)) AS n_years,
               count(DISTINCT r.first_use_date) FILTER (WHERE r.first_use_date IS NOT NULL)
                   AS n_first_use,
               median(gap) AS median_gap
        FROM (
            SELECT vehicle_id, test_date, first_use_date,
                   -- test_id here is a determinism tiebreak, not chronology
                   -- (D13): lag(test_date) is invariant under any reordering
                   -- of same-day ties, since the date multiset is unchanged.
                   date_diff('day', lag(test_date) OVER (PARTITION BY vehicle_id
                                                          ORDER BY test_date, test_id),
                             test_date) AS gap
            FROM {results_relation}
            WHERE vehicle_id IN (SELECT vehicle_id FROM multi)
        ) r
        GROUP BY r.vehicle_id
    )
    SELECT count(*)                                            AS n_vehicles,
           avg(CASE WHEN n_years >= 2 THEN 1.0 ELSE 0.0 END)   AS multiyear_share,
           avg(CASE WHEN n_first_use > 1 THEN 1.0 ELSE 0.0 END) AS first_use_conflict_share,
           median(median_gap)                                  AS overall_median_gap
    FROM per_vehicle
    """).fetchone()
    n_vehicles, multiyear_share, conflict_share, median_gap = row
    if not n_vehicles:
        return _result("vehicle_continuity", False, "no multi-test vehicles found in sample")
    passed = (
        multiyear_share is not None
        and multiyear_share >= min_multiyear_share
        and conflict_share is not None
        and conflict_share <= max_first_use_conflict_share
        and median_gap is not None
        and min_median_gap_days <= median_gap <= max_median_gap_days
    )
    return _result(
        "vehicle_continuity", passed,
        f"sample={n_vehicles} multiyear_share={multiyear_share:.3f} "
        f"(min {min_multiyear_share}) first_use_conflict_share={conflict_share:.4f} "
        f"(max {max_first_use_conflict_share}) median_gap_days={median_gap} "
        f"(range {min_median_gap_days}-{max_median_gap_days})",
    )


def check_year_volumes(con, results_relation: str,
                       expected: Optional[Dict[int, tuple]] = None,
                       default_bounds: tuple = (15_000_000, 50_000_000),
                       ramp_years: int = 2) -> CheckResult:
    """Per-year class-4 test volumes inside plausible bounds.

    GB class-4 volume runs roughly 25-40M/year in the modern era; the 2005/06
    computerisation ramp years are exempt from the lower bound. `expected`
    overrides bounds per year (workstation config can pin the published DVSA
    statistics); the defaults only catch catastrophic ingest loss, not
    percent-level drift.
    """
    rows = con.execute(f"""
        SELECT year(test_date) AS y, count(*) AS n
        FROM {results_relation}
        WHERE test_class_id = '4'
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    if not rows:
        return _result("year_volumes", False, "no class-4 rows in lake")
    expected = expected or {}
    min_year = min(y for y, _ in rows)
    failures: List[str] = []
    for year, count in rows:
        lo, hi = expected.get(year, default_bounds)
        if year <= min_year + ramp_years - 1 and year not in expected:
            lo = 0  # computerisation ramp: only the upper bound applies
        if not (lo <= count <= hi):
            failures.append(f"{year}: {count:,} outside [{lo:,}, {hi:,}]")
    table = ", ".join(f"{y}={n:,}" for y, n in rows)
    if failures:
        return _result("year_volumes", False, "; ".join(failures) + f" | volumes: {table}")
    return _result("year_volumes", True, f"volumes: {table}")


def check_taxonomy_step(con, items_relation: str, results_relation: str) -> CheckResult:
    """The May-2018 switch must be visible in the item-class vocabulary:
    dangerous/major/minor codes only after the switch date, never before."""
    switch = TAXONOMY_SWITCH_DATE.isoformat()
    pre_bad, post_missing = con.execute(f"""
        WITH joined AS (
            SELECT i.rfr_type_code, r.test_date
            FROM {items_relation} i
            JOIN {results_relation} r USING (test_id)
        )
        SELECT
            (SELECT count(*) FROM joined
             WHERE test_date < DATE '{switch}'
               AND rfr_type_code IN ('D', 'M', 'm')) AS pre_bad,
            (SELECT count(*) FROM joined
             WHERE test_date >= DATE '{switch}'
               AND rfr_type_code IN ('D', 'M', 'm')) = 0 AS post_missing
    """).fetchone()
    passed = (pre_bad == 0) and (not post_missing)
    return _result(
        "taxonomy_step", passed,
        f"pre-switch dangerous/major/minor rows={pre_bad} (must be 0); "
        f"post-switch has D/M/m codes={'no' if post_missing else 'yes'} (must be yes)",
    )


def check_no_unknown_outcomes(con, results_relation: str,
                              max_share: float = 0.001) -> CheckResult:
    total, unknown = con.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE outcome = 'UNKNOWN')
        FROM {results_relation}
    """).fetchone()
    share = (unknown / total) if total else 1.0
    return _result(
        "no_unknown_outcomes", share <= max_share,
        f"unknown_outcome_share={share:.5f} (max {max_share}) over {total:,} rows",
    )


def check_rfr_type_coverage(con, items_relation: str,
                            max_unclassified_share: float = 0.001) -> CheckResult:
    """Items whose rfr_type_code fell outside the era tables (ingest stores
    them with is_fail_item NULL rather than guessing). Any real volume here
    means RFR_TYPE_BY_ERA needs a deliberate extension."""
    total, unclassified = con.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE is_fail_item IS NULL)
        FROM {items_relation}
    """).fetchone()
    if not total:
        return _result("rfr_type_coverage", False, "no item rows in lake")
    share = unclassified / total
    detail = f"unclassified_share={share:.5f} (max {max_unclassified_share}) over {total:,} items"
    if share > max_unclassified_share:
        top = con.execute(f"""
            SELECT rfr_type_code, count(*) AS n FROM {items_relation}
            WHERE is_fail_item IS NULL GROUP BY 1 ORDER BY n DESC LIMIT 5
        """).fetchall()
        detail += "; top unregistered codes: " + ", ".join(f"{c!r}×{n:,}" for c, n in top)
    return _result("rfr_type_coverage", share <= max_unclassified_share, detail)


def check_category_coverage(con, items_relation: str,
                            min_categorized_share_of_failures: float = 0.5) -> CheckResult:
    """Of failing items, how many project into the 7 comparison categories?
    Many legitimately do not (exhaust, seatbelts...), so this is a floor
    against a broken mapping, plus a volume report of what stayed unmapped
    so _SECTION_TO_CATEGORY gets extended deliberately."""
    total_fail, categorized = con.execute(f"""
        SELECT count(*) FILTER (WHERE is_fail_item),
               count(*) FILTER (WHERE is_fail_item AND component_category IS NOT NULL)
        FROM {items_relation}
    """).fetchone()
    if not total_fail:
        return _result("category_coverage", False, "no failing items in lake")
    share = categorized / total_fail
    return _result(
        "category_coverage", share >= min_categorized_share_of_failures,
        f"categorized_failure_share={share:.3f} "
        f"(min {min_categorized_share_of_failures}) over {total_fail:,} failing items",
    )


def check_class_mix(con, results_relation: str,
                    min_class4_share: float = 0.5,
                    max_class4_share: float = 0.98) -> CheckResult:
    total, class4 = con.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE test_class_id = '4')
        FROM {results_relation}
    """).fetchone()
    share = (class4 / total) if total else 0.0
    return _result(
        "class_mix", min_class4_share <= share <= max_class4_share,
        f"class4_share={share:.3f} (expected {min_class4_share}-{max_class4_share}) "
        f"over {total:,} rows",
    )


def run_results_checks(con, results_relation: str,
                       config: Optional[Dict[str, Any]] = None) -> List[CheckResult]:
    config = config or {}
    return [
        check_vehicle_continuity(con, results_relation,
                                 **config.get("vehicle_continuity", {})),
        check_year_volumes(con, results_relation, **config.get("year_volumes", {})),
        check_no_unknown_outcomes(con, results_relation,
                                  **config.get("no_unknown_outcomes", {})),
        check_class_mix(con, results_relation, **config.get("class_mix", {})),
    ]


def run_items_checks(con, items_relation: str, results_relation: str,
                     config: Optional[Dict[str, Any]] = None) -> List[CheckResult]:
    config = config or {}
    return [
        check_taxonomy_step(con, items_relation, results_relation),
        check_rfr_type_coverage(con, items_relation,
                                **config.get("rfr_type_coverage", {})),
        check_category_coverage(con, items_relation,
                                **config.get("category_coverage", {})),
    ]
