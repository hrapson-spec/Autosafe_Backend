"""Feature blocks B1-B6 + the emitted-column registry.

Contract v2 removed B0: the 104 serving features come from the packets ->
feature_engineering_v55 module path (see packets.py), not from here. Everything
in this module is a pure function of `AsOfState` as it stands BEFORE the target
day is folded in, plus the target row's own vehicle attributes (make, model,
first_use_date, ...), which are observable at prediction time.

No column here reads the target day's own multiset, items or outcome. That is
what makes falsifier 4 (emit-before-update) and falsifier 1 (as-of) hold.

Every column carries: block, dtype, a one-line definition (emitted verbatim
into FEATURE_DICTIONARY.md), and an era-observability status. Its deployability
class is attached at build time from serve_view_classes.json (serve_view.py) --
never hardcoded here.
"""
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

from . import state as st
from .taxonomy import (CANONICAL_CATEGORY_KEYS, CATEGORY_KEYS, LATERAL_GROUPS,
                       LONGITUDINAL_GROUPS, VERTICAL_GROUPS)

#: Digital MOT records begin 2005-01-01: nothing before it is observable.
OBSERVABLE_FLOOR = date(2005, 1, 1)
#: A car's first MOT is due at 3 years, so the first 3 years of life offer no
#: test opportunities (opportunity-adjusted density denominator).
FIRST_MOT_AGE_YEARS = 3.0
DAYS_PER_YEAR = st.DAYS_PER_YEAR

MILEAGE_BANDS = (("0-30k", 0, 30_000), ("30k-60k", 30_000, 60_000),
                 ("60k-100k", 60_000, 100_000), ("100k+", 100_000, None))

ERA_ALL = "all_eras"
ERA_POST2018 = "post_2018_only"
ERA_CALENDAR = "calendar_derived"
ERA_RESEARCH = "research_only_input"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    block: str
    dtype: str
    definition: str
    era_observability: str = ERA_ALL


def _cat_label(key: str) -> str:
    return key.replace("_", " ")


# --- registry ---------------------------------------------------------------

META_COLUMNS: List[ColumnSpec] = [
    ColumnSpec("recipe", "meta", "VARCHAR", "Window-recipe name this row was emitted under.", ERA_CALENDAR),
    ColumnSpec("rung", "meta", "VARCHAR", "Nested-sample rung this shard belongs to.", ERA_CALENDAR),
    ColumnSpec("tgt_id", "meta", "BIGINT", "test_id of the prediction event (identifier; never ordered on)."),
    ColumnSpec("vehicle_id", "meta", "BIGINT", "Lake vehicle identifier; the history key and the sampling/bucketing key."),
    ColumnSpec("tgt_date", "meta", "DATE", "Target test date. All priors are strictly earlier calendar days."),
    ColumnSpec("tgt_year", "meta", "INTEGER", "Calendar year of tgt_date."),
    ColumnSpec("tgt_outcome", "meta", "VARCHAR", "Canonical lake outcome of the target row (PASS/FAIL/PRS)."),
    ColumnSpec("y_final", "meta", "BOOLEAN", "Label, final basis: outcome = 'FAIL' (the preserved AutoSafe label)."),
    ColumnSpec("y_initial", "meta", "BOOLEAN", "Label, initial basis: outcome IN ('FAIL','PRS') (DVSA initial-failure basis, D12)."),
    ColumnSpec("tgt_test_class_id", "meta", "VARCHAR", "DVSA test class of the target row."),
    ColumnSpec("tgt_test_type", "meta", "VARCHAR", "DVSA test type of the target row (always 'NT' by population rule)."),
    ColumnSpec("tgt_miles", "meta", "BIGINT", "Odometer reading recorded ON the target test. NOT a feature: unavailable at serving time; carried for audit/mileage-parity work only."),
    ColumnSpec("tgt_make", "meta", "VARCHAR", "Vehicle make as recorded on the target row."),
    ColumnSpec("tgt_model", "meta", "VARCHAR", "Vehicle model as recorded on the target row."),
    ColumnSpec("tgt_model_id", "meta", "VARCHAR", "Lake 'MAKE MODEL' key (normalize.build_model_id)."),
    ColumnSpec("tgt_fuel", "meta", "VARCHAR", "Fuel type as recorded on the target row."),
    ColumnSpec("tgt_colour", "meta", "VARCHAR", "Colour as recorded on the target row."),
    ColumnSpec("tgt_cc", "meta", "INTEGER", "Cylinder capacity; NULL is informative missingness (EV growth)."),
    ColumnSpec("tgt_fud", "meta", "DATE", "first_use_date, ingest-sanitised (pre-1900 and post-test values nulled)."),
    ColumnSpec("tgt_pc", "meta", "VARCHAR", "postcode_area of the testing station. Research-only input (not API-observable).", ERA_RESEARCH),
    ColumnSpec("tgt_age_at_test", "meta", "DOUBLE", "Lake age_at_test on the target row (years)."),
    ColumnSpec("tgt_taxonomy_era", "meta", "VARCHAR", "Taxonomy era of the target date: pre_2018 / post_2018."),
    ColumnSpec("sample_u", "meta", "DOUBLE", "Salted unit hash u = hash(vehicle_id || 'mp2026s1') / 2^64 -- the sample-membership coordinate."),
    ColumnSpec("sample_bucket", "meta", "INTEGER", "Memory-management bucket (separate salt 'mp2026bucket'); carries no sample meaning."),
    ColumnSpec("enrichment_stratum", "meta", "VARCHAR", "As-of enrichment stratum at tgt_date: dangerous_prior / recent_fail / deep_history / none."),
    ColumnSpec("inclusion_weight", "meta", "DOUBLE", "Horvitz-Thompson weight base/p(selected): 1.0 for base rows, <1 for enrichment-selected rows."),
]

B1_COLUMNS: List[ColumnSpec] = [
    ColumnSpec("b1_n_prior_test_days", "B1", "BIGINT", "Prior test-DAYS (strictly earlier calendar days with >=1 test). The D13-safe depth measure."),
    ColumnSpec("b1_n_prior_tests", "B1", "BIGINT", "Prior test RECORDS, all outcomes (retests included, undifferentiated)."),
    ColumnSpec("b1_n_prior_initials", "B1", "BIGINT", "Prior DVSA initial tests (test_type='NT' and a recorded result). Consumes test_type-in-history: research-only input.", ERA_RESEARCH),
    ColumnSpec("b1_n_prior_final_fails", "B1", "BIGINT", "Prior initial tests with outcome FAIL (final basis).", ERA_RESEARCH),
    ColumnSpec("b1_n_prior_initial_fails", "B1", "BIGINT", "Prior initial tests with outcome FAIL or PRS (initial basis).", ERA_RESEARCH),
    ColumnSpec("b1_first_prior_date", "B1", "DATE", "Earliest observed prior test-day; NULL when there are no priors."),
    ColumnSpec("b1_last_prior_date", "B1", "DATE", "Most recent prior test-day; NULL when there are no priors."),
    ColumnSpec("b1_history_years", "B1", "DOUBLE", "(tgt_date - b1_first_prior_date) / 365.25; NULL without priors. Observed span, not vehicle age."),
    ColumnSpec("b1_observable_years", "B1", "DOUBLE", "(tgt_date - max(first_use_date, 2005-01-01)) / 365.25: the window in which history COULD have been recorded."),
    ColumnSpec("b1_observable_years_status", "B1", "VARCHAR", "How b1_observable_years was derived: observed / left_censored_2005 / first_use_missing."),
    ColumnSpec("b1_history_coverage_grade", "B1", "VARCHAR", "none / left_censored / partial / full -- coverage of the observable window by recorded history."),
    ColumnSpec("b1_left_censor_flag", "B1", "BOOLEAN", "TRUE when first_use_date precedes the 2005 digital-records floor (history is truncated by the publication, not by the vehicle)."),
    ColumnSpec("b1_first_use_missing_flag", "B1", "BOOLEAN", "TRUE when first_use_date is NULL after ingest sanitisation."),
    ColumnSpec("b1_age_at_target_years", "B1", "DOUBLE", "(tgt_date - first_use_date) / 365.25; NULL when first_use_date is missing (never zero-filled)."),
    ColumnSpec("b1_density_per_observable_year", "B1", "DOUBLE", "b1_n_prior_test_days / b1_observable_years; NULL when the window is non-positive."),
    ColumnSpec("b1_opportunity_adjusted_density", "B1", "DOUBLE", "b1_n_prior_test_days / testable years, where testable years excludes the pre-first-MOT 3 years and the 2020 COVID extension overlap."),
    ColumnSpec("b1_n_prior_years_observed", "B1", "BIGINT", "Distinct calendar years carrying >=1 prior test-day."),
    ColumnSpec("b1_max_gap_days", "B1", "BIGINT", "Longest gap in days between consecutive prior test-days; NULL with <2 priors."),
    ColumnSpec("b1_mean_gap_days", "B1", "DOUBLE", "Mean gap in days between consecutive prior test-days; NULL with <2 priors."),
    ColumnSpec("b1_n_prior_nonresult_days", "B1", "BIGINT", "Prior test-days carrying no definitive outcome at all (abandoned/aborted/refused only). Distinct from b5_n_prior_nondefinitive_days, which is a CLUSTER-OUTCOME property."),
]
for _cap_years, _suffix in st.DEPTH_CAPS:
    _y = int(_cap_years)
    B1_COLUMNS += [
        ColumnSpec(f"b1_n_prior_test_days_{_suffix}", "B1", "BIGINT",
                   f"b1_n_prior_test_days restricted to the trailing {_y}-year window before tgt_date (strictly-earlier-day rule still applies inside it)."),
        ColumnSpec(f"b1_n_prior_initials_{_suffix}", "B1", "BIGINT",
                   f"b1_n_prior_initials restricted to the trailing {_y}-year window before tgt_date.", ERA_RESEARCH),
        ColumnSpec(f"b1_history_years_{_suffix}", "B1", "DOUBLE",
                   f"Observed span INSIDE the trailing {_y}-year window: (tgt_date - earliest in-window prior test-day) / 365.25; NULL when no prior falls in the window. NOT min(b1_history_years, {_y})."),
    ]

B2_COLUMNS: List[ColumnSpec] = []
for _k in CATEGORY_KEYS:
    B2_COLUMNS += [
        ColumnSpec(f"b2_{_k}_n_days", "B2", "BIGINT", f"Prior test-days carrying >=1 defect item in section-category '{_cat_label(_k)}' (any disposition)."),
        ColumnSpec(f"b2_{_k}_days_since", "B2", "BIGINT", f"Days since the most recent prior test-day with a '{_cat_label(_k)}' item; NULL when never observed."),
        ColumnSpec(f"b2_{_k}_max_run", "B2", "BIGINT", f"Longest run of CONSECUTIVE observed prior test-days carrying a '{_cat_label(_k)}' item (recurrence)."),
        ColumnSpec(f"b2_{_k}_persistence", "B2", "DOUBLE", f"b2_{_k}_n_days / b1_n_prior_test_days -- share of observed test-days carrying '{_cat_label(_k)}'; NULL without priors."),
    ]
B2_COLUMNS += [
    ColumnSpec("b2_breadth_categories", "B2", "BIGINT", "Distinct section-categories ever seen on an ITEM-OBSERVABLE prior test-day (breadth of defect history); NULL when no prior day is item-observable."),
    ColumnSpec("b2_last_day_n_categories", "B2", "BIGINT", "Distinct section-categories present on the most recent prior test-day; NULL without priors AND when that day's defect detail is unobservable (unknown, never 0)."),
    ColumnSpec("b2_n_items_total", "B2", "BIGINT", "Total prior defect items across ITEM-OBSERVABLE prior test-days (all dispositions); NULL when none is observable."),
    ColumnSpec("b2_n_catalogue_miss_items", "B2", "BIGINT", "Prior item rows whose rfr_id is absent from the class-4 catalogue (counted, never folded into 'other'); NULL when no prior day is item-observable."),
    # --- item-observability index (PREREG_CUBE_v2 §4) ------------------------
    # The denominators every item-derived column above was actually scored on.
    # Without them a NULL is uninterpretable and a 0 is unfalsifiable.
    ColumnSpec("b2_item_observability_status", "B2", "VARCHAR", "no_priors / none / partial / full -- item-observability across this vehicle's prior test-days. 'none' means EVERY item-derived column in B2/B3/B4 is NULL because the defect detail was unavailable, NOT because the vehicle was clean; 'no_priors' means there was nothing to observe and the zeros are certain."),
    ColumnSpec("b2_n_prior_days_items_observed", "B2", "BIGINT", "Prior test-days carrying >=1 test whose defect detail is observable -- the honest denominator for every b2_*_persistence and item count."),
    ColumnSpec("b2_n_prior_days_items_unobserved", "B2", "BIGINT", "Prior test-days carrying >=1 test whose defect detail is NOT observable. Overlaps b2_n_prior_days_items_observed on partially-dark days (both are 'days with >=1 such test')."),
    ColumnSpec("b2_n_prior_days_items_zero_defects", "B2", "BIGINT", "Item-observable prior test-days that carried NO defect items -- the honest-zero population (49.9-62.1% of passes, measured). Its complement within b2_n_prior_days_items_observed is the days that did carry items."),
    ColumnSpec("b2_n_prior_days_items_unavailable", "B2", "BIGINT", "Prior test-days wholly unobservable because the source/partition cell is declared structurally dark (publisher schema change)."),
    ColumnSpec("b2_n_prior_days_items_expected_missing", "B2", "BIGINT", "Prior test-days wholly unobservable although the evidence says items should exist (dark day inside a covered partition, or a fail-bearing test with zero items). Expectation only -- attribution is a per-cell field in the ledger/manifest, not a per-row claim."),
]
for _cap_years, _suffix in st.DEPTH_CAPS:
    _y = int(_cap_years)
    for _k in CANONICAL_CATEGORY_KEYS:
        B2_COLUMNS.append(ColumnSpec(
            f"b2_{_k}_n_days_{_suffix}", "B2", "BIGINT",
            f"b2_{_k}_n_days restricted to the trailing {_y}-year window before tgt_date."))

B3_COLUMNS: List[ColumnSpec] = [
    ColumnSpec("b3_n_days_fine_severity_observable", "B3", "BIGINT", "Prior test-days on/after 2018-05-20 -- the DENOMINATOR for every dangerous/major/minor count.", ERA_POST2018),
    ColumnSpec("b3_severity_observability_status", "B3", "VARCHAR", "none / partial / full: whether the fine severity ladder was observable across this vehicle's prior history.", ERA_POST2018),
    ColumnSpec("b3_n_dangerous_items", "B3", "BIGINT", "Prior items with dangerous_mark='D' (post-2018 only); NULL when no prior day is severity-observable.", ERA_POST2018),
    ColumnSpec("b3_n_dangerous_days", "B3", "BIGINT", "Prior test-days carrying >=1 dangerous item; NULL when unobservable.", ERA_POST2018),
    ColumnSpec("b3_days_since_dangerous", "B3", "BIGINT", "Days since the most recent prior dangerous item; NULL when never/unobservable.", ERA_POST2018),
    ColumnSpec("b3_n_major_items", "B3", "BIGINT", "Prior MAJOR items: disposition in {F,P} and NOT dangerous-marked (post-2018 only).", ERA_POST2018),
    ColumnSpec("b3_n_major_days", "B3", "BIGINT", "Prior test-days carrying >=1 major item; NULL when unobservable.", ERA_POST2018),
    ColumnSpec("b3_days_since_major", "B3", "BIGINT", "Days since the most recent prior major item; NULL when never/unobservable.", ERA_POST2018),
    ColumnSpec("b3_n_minor_items", "B3", "BIGINT", "Prior MINOR items (rfr_type_code 'M', F-22-corrected: minor does NOT fail the test); post-2018 only.", ERA_POST2018),
    ColumnSpec("b3_n_minor_days", "B3", "BIGINT", "Prior test-days carrying >=1 minor item; NULL when unobservable.", ERA_POST2018),
    ColumnSpec("b3_days_since_minor", "B3", "BIGINT", "Days since the most recent prior minor item; NULL when never/unobservable.", ERA_POST2018),
    ColumnSpec("b3_n_prs_items", "B3", "BIGINT", "Prior items with disposition 'P' -- fail-bearing, rectified at the station. Both eras."),
    ColumnSpec("b3_n_prs_item_days", "B3", "BIGINT", "Prior test-days carrying >=1 PRS-rectified item."),
    ColumnSpec("b3_days_since_prs_item", "B3", "BIGINT", "Days since the most recent prior PRS-rectified item; NULL when never."),
    ColumnSpec("b3_n_fail_items_initial", "B3", "BIGINT", "Prior fail-bearing items on the INITIAL basis (disposition F + P). Both eras."),
    ColumnSpec("b3_n_fail_items_final", "B3", "BIGINT", "Prior fail-bearing items on the FINAL-unrectified basis (disposition F only). Both eras."),
    ColumnSpec("b3_n_advisory_items", "B3", "BIGINT", "Prior advisory items (disposition A). Both eras; volumes are recording-regime-confounded pre-2015 (DATA_ASSESSMENT §4)."),
    ColumnSpec("b3_fail_item_rectified_share", "B3", "DOUBLE", "b3_n_prs_items / b3_n_fail_items_initial -- share of fail-bearing items rectified at the station; NULL when no fail-bearing items."),
]

B4_COLUMNS: List[ColumnSpec] = [
    ColumnSpec("b4_n_adv_to_fail_transitions", "B4", "BIGINT", "Count of (category, day) pairs where a fail-bearing item appeared in a category that had carried an ADVISORY on some strictly earlier day."),
    ColumnSpec("b4_adv_to_fail_categories", "B4", "BIGINT", "Distinct categories with >=1 advisory->fail transition."),
    ColumnSpec("b4_days_since_adv_to_fail", "B4", "BIGINT", "Days since the most recent advisory->fail transition; NULL when never."),
    ColumnSpec("b4_n_recurrence_after_repair", "B4", "BIGINT", "Count of (category, day) pairs where a category failed, a later day passed, and the category failed again."),
    ColumnSpec("b4_recurrence_categories", "B4", "BIGINT", "Distinct categories with >=1 recurrence-after-repair."),
    ColumnSpec("b4_days_since_recurrence", "B4", "BIGINT", "Days since the most recent recurrence-after-repair; NULL when never."),
    ColumnSpec("b4_burden_delta_1", "B4", "BIGINT", "n_items on the most recent prior test-day minus the one before it; NULL with <2 priors."),
    ColumnSpec("b4_burden_delta_2", "B4", "BIGINT", "n_items on the 2nd most recent prior test-day minus the 3rd; NULL with <3 priors."),
    ColumnSpec("b4_burden_mean_last3", "B4", "DOUBLE", "Mean n_items over the last up-to-3 prior test-days; NULL without priors."),
    ColumnSpec("b4_burden_x_age", "B4", "DOUBLE", "b4_burden_mean_last3 * b1_age_at_target_years; NULL when either input is NULL."),
    ColumnSpec("b4_mileage_band", "B4", "VARCHAR", "Band of the LAST TRUSTED prior odometer reading: 0-30k / 30k-60k / 60k-100k / 100k+ / unknown. Unit-robust: never mixes a km reading with a mi reading because only one reading is used."),
    ColumnSpec("b4_burden_x_mileage_band_ord", "B4", "DOUBLE", "b4_burden_mean_last3 * mileage-band ordinal (1..4); NULL when the band is unknown."),
    ColumnSpec("b4_deterioration_slope", "B4", "DOUBLE", "Least-squares slope of items-per-test-day against years since the first prior day (items/year); NULL with <2 priors or a degenerate span."),
    ColumnSpec("b4_deterioration_slope_n_days", "B4", "BIGINT", "Number of ITEM-OBSERVABLE prior test-days the slope was fitted on (its honest denominator). Dark days contribute neither a point nor a count -- previously they contributed a false 0 to the regression AND inflated this denominator."),
]

B5_COLUMNS: List[ColumnSpec] = [
    ColumnSpec("b5_n_prior_multi_test_days", "B5", "BIGINT", "Prior test-days carrying more than one test record (same-day retest pairs)."),
    ColumnSpec("b5_max_tests_on_a_prior_day", "B5", "BIGINT", "Largest number of test records on any single prior test-day."),
    ColumnSpec("b5_n_prior_days_pass_and_fail", "B5", "BIGINT", "Prior test-days carrying BOTH a PASS and a FAIL record (the tie-exposed population)."),
    ColumnSpec("b5_n_prior_nondefinitive_days", "B5", "BIGINT", "Prior test-days whose D13 cluster outcome is AMBIGUOUS under cycles.py semantics -- which is EITHER a same-stratum FAIL + definitive pass OR a day of mixed non-definitive outcomes (e.g. ABANDONED + ABORTED). Superset of b5_n_prior_ambiguous_days."),
    ColumnSpec("b5_n_prior_ambiguous_days", "B5", "BIGINT", "STRICT variant: prior test-days that are AMBIGUOUS *and* carry a definitive outcome -- same-stratum FAIL + definitive pass only. The sequence is unidentified and is never invented."),
    ColumnSpec("b5_n_prior_mileage_conflict_days", "B5", "BIGINT", "Prior test-days whose same-day odometer readings differ by more than 1%."),
    ColumnSpec("b5_last_day_n_tests", "B5", "BIGINT", "Test records on the most recent prior test-day; NULL without priors."),
    ColumnSpec("b5_last_day_n_distinct_outcomes", "B5", "BIGINT", "Distinct outcomes on the most recent prior test-day; NULL without priors."),
    ColumnSpec("b5_last_day_has_pass", "B5", "BOOLEAN", "Most recent prior test-day contained a PASS record; NULL without priors."),
    ColumnSpec("b5_last_day_has_fail", "B5", "BOOLEAN", "Most recent prior test-day contained a FAIL record; NULL without priors."),
    ColumnSpec("b5_last_day_has_prs", "B5", "BOOLEAN", "Most recent prior test-day contained a PRS record; NULL without priors. PRS has no confirmed live representation (SERVE_VIEW invariant 3).", ERA_RESEARCH),
    ColumnSpec("b5_last_day_has_nonresult", "B5", "BOOLEAN", "Most recent prior test-day contained an abandoned/aborted/refused record; NULL without priors."),
    ColumnSpec("b5_days_since_prior_day", "B5", "BIGINT", "tgt_date minus the most recent prior test-day, in days; NULL without priors."),
    ColumnSpec("b5_gap_annual_band_flag", "B5", "BOOLEAN", "b5_days_since_prior_day falls in the annual band [300, 430]; NULL without priors."),
    ColumnSpec("b5_covid_straddle_flag", "B5", "BOOLEAN", "The interval [last prior test-day, tgt_date] intersects the COVID MOT-extension window 2020-03-30..2020-08-01.", ERA_CALENDAR),
]

B6_COLUMNS: List[ColumnSpec] = [
    ColumnSpec(f"b6_lat_{_g}_n", "B6", "BIGINT",
               f"Prior defect items whose location_id has coarse LATERAL group '{_g}' (mdr_rfr_location.lateral); NULL when the location map is absent.",
               ERA_RESEARCH)
    for _g in LATERAL_GROUPS
] + [
    ColumnSpec(f"b6_long_{_g}_n", "B6", "BIGINT",
               f"Prior defect items whose location_id has coarse LONGITUDINAL group '{_g}' (mdr_rfr_location.longitudinal); NULL when the location map is absent.",
               ERA_RESEARCH)
    for _g in LONGITUDINAL_GROUPS
] + [
    ColumnSpec(f"b6_vert_{_g}_n", "B6", "BIGINT",
               f"Prior defect items whose location_id has coarse VERTICAL group '{_g}' (mdr_rfr_location.vertical); NULL when the location map is absent.",
               ERA_RESEARCH)
    for _g in VERTICAL_GROUPS
] + [
    ColumnSpec("b6_pos_n_total", "B6", "BIGINT", "Prior defect items whose location_id RESOLVED against mdr_rfr_location (the honest denominator for both axes); NULL when the location map is absent.", ERA_RESEARCH),
    ColumnSpec("b6_location_map_status", "B6", "VARCHAR", "present / absent -- whether mdr_rfr_location was supplied. Absent means the B6 counts are NULL, not zero.", ERA_RESEARCH),
]

BLOCK_COLUMNS: Dict[str, List[ColumnSpec]] = {
    "meta": META_COLUMNS, "B1": B1_COLUMNS, "B2": B2_COLUMNS,
    "B3": B3_COLUMNS, "B4": B4_COLUMNS, "B5": B5_COLUMNS, "B6": B6_COLUMNS,
}
NEW_BLOCKS = ("B1", "B2", "B3", "B4", "B5", "B6")
#: Contract cap: total NEW columns across B1-B6.
NEW_COLUMN_CAP = 150

ALL_COLUMNS: List[ColumnSpec] = [c for block in ("meta",) + NEW_BLOCKS
                                 for c in BLOCK_COLUMNS[block]]
COLUMN_NAMES: List[str] = [c.name for c in ALL_COLUMNS]


def n_new_columns() -> int:
    return sum(len(BLOCK_COLUMNS[b]) for b in NEW_BLOCKS)


# --- helpers ----------------------------------------------------------------

def _years_between(start: Optional[date], end: date) -> Optional[float]:
    return None if start is None else (end - start).days / DAYS_PER_YEAR


def _safe_div(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or den <= 0:
        return None
    return num / den


def mileage_band(mileage: Optional[int]) -> str:
    if mileage is None:
        return "unknown"
    for name, low, high in MILEAGE_BANDS:
        if mileage >= low and (high is None or mileage < high):
            return name
    return "unknown"


def mileage_band_ordinal(band: str) -> Optional[int]:
    for idx, (name, _lo, _hi) in enumerate(MILEAGE_BANDS, start=1):
        if name == band:
            return idx
    return None


def _covid_overlap_years(start: Optional[date], end: date) -> float:
    """Years of [start, end] that fall inside the COVID MOT-extension window."""
    if start is None:
        return 0.0
    lo = max(start, st.COVID_EXT_START)
    hi = min(end, st.COVID_EXT_END)
    return max(0, (hi - lo).days) / DAYS_PER_YEAR


# --- emitters ---------------------------------------------------------------

def emit_b1(state: st.AsOfState, tgt_date: date, first_use: Optional[date]) -> Dict[str, Any]:
    history_years = _years_between(state.first_date, tgt_date)
    if first_use is None:
        obs_start, obs_status = OBSERVABLE_FLOOR, "first_use_missing"
    elif first_use < OBSERVABLE_FLOOR:
        obs_start, obs_status = OBSERVABLE_FLOOR, "left_censored_2005"
    else:
        obs_start, obs_status = first_use, "observed"
    observable_years = max(0.0, (tgt_date - obs_start).days / DAYS_PER_YEAR)

    testable_start = obs_start
    if first_use is not None:
        first_mot = date.fromordinal(
            min(date.max.toordinal(),
                first_use.toordinal() + int(FIRST_MOT_AGE_YEARS * DAYS_PER_YEAR)))
        testable_start = max(obs_start, first_mot)
    testable_years = max(0.0, (tgt_date - testable_start).days / DAYS_PER_YEAR)
    testable_years = max(0.0, testable_years - _covid_overlap_years(testable_start, tgt_date))

    if state.n_days == 0:
        grade = "none"
    elif obs_status == "left_censored_2005":
        grade = "left_censored"
    elif history_years is not None and observable_years > 0 and history_years >= 0.6 * observable_years:
        grade = "full"
    else:
        grade = "partial"

    out = {
        "b1_n_prior_test_days": state.n_days,
        "b1_n_prior_tests": state.n_tests,
        "b1_n_prior_initials": state.n_initials,
        "b1_n_prior_final_fails": state.n_final_fails,
        "b1_n_prior_initial_fails": state.n_initial_fails,
        "b1_first_prior_date": state.first_date,
        "b1_last_prior_date": state.last_date,
        "b1_history_years": history_years,
        "b1_observable_years": observable_years,
        "b1_observable_years_status": obs_status,
        "b1_history_coverage_grade": grade,
        "b1_left_censor_flag": (first_use is not None and first_use < OBSERVABLE_FLOOR),
        "b1_first_use_missing_flag": first_use is None,
        "b1_age_at_target_years": _years_between(first_use, tgt_date),
        "b1_density_per_observable_year": _safe_div(float(state.n_days), observable_years),
        "b1_opportunity_adjusted_density": _safe_div(float(state.n_days), testable_years),
        "b1_n_prior_years_observed": len(state.years_seen),
        "b1_max_gap_days": state.max_gap_days,
        "b1_mean_gap_days": state.mean_gap_days(),
        "b1_n_prior_nonresult_days": state.n_nonresult_only_days,
    }
    for cap_years, suffix in st.DEPTH_CAPS:
        out[f"b1_n_prior_test_days_{suffix}"] = state.n_days_within(tgt_date, cap_years)
        out[f"b1_n_prior_initials_{suffix}"] = state.n_initials_within(tgt_date, cap_years)
        out[f"b1_history_years_{suffix}"] = state.history_years_within(tgt_date, cap_years)
    return out


def item_graded(state: st.AsOfState, value: Any) -> Any:
    """NULL when the vehicle HAS priors but none of them is item-observable.

    The item-side twin of `emit_b3.graded` (which does the same job for the
    pre-2018 severity era). PREREG_CUBE_v2 §4: an unobservable quantity is
    emitted as NULL plus a status, never as zero -- and the status is
    `b2_item_observability_status`, emitted alongside.

    THE DAMAGE-RATIO GUARD IS THE `has_priors` CLAUSE. A vehicle with NO prior
    test-days has no prior defect items either: that zero is a certainty, not an
    absence of evidence, and nulling it would destroy the whole zero-prior
    cohort for no repair at all. Only a vehicle whose priors EXIST but whose
    defect detail cannot be seen goes NULL.
    """
    if not state.has_priors:
        return value
    return value if state.has_item_observations else None


def emit_b2(state: st.AsOfState, tgt_date: date) -> Dict[str, Any]:
    # The denominator is item-observable prior DAYS, not all prior days: a dark
    # day used to inflate the persistence denominator only, biasing every
    # b2_*_persistence systematically downwards.
    observed_days = state.n_days_items_observed
    out: Dict[str, Any] = {}
    for key in CATEGORY_KEYS:
        n_days = state.cat_days[key]
        out[f"b2_{key}_n_days"] = item_graded(state, n_days)
        out[f"b2_{key}_days_since"] = state.days_since(state.cat_last_date[key], tgt_date)
        out[f"b2_{key}_max_run"] = item_graded(state, state.cat_max_run[key])
        out[f"b2_{key}_persistence"] = _safe_div(float(n_days), float(observed_days))
    out["b2_breadth_categories"] = item_graded(
        state, sum(1 for k in CATEGORY_KEYS if state.cat_days[k] > 0))
    out["b2_last_day_n_categories"] = (
        len(state.last_day_categories)
        if (state.has_priors and state.last_day_categories is not None) else None)
    out["b2_n_items_total"] = item_graded(state, state.n_items_total)
    out["b2_n_catalogue_miss_items"] = item_graded(state, state.n_catalogue_miss)
    for cap_years, suffix in st.DEPTH_CAPS:
        for key in CANONICAL_CATEGORY_KEYS:
            out[f"b2_{key}_n_days_{suffix}"] = item_graded(
                state, state.cat_n_days_within(key, tgt_date, cap_years))
    out["b2_item_observability_status"] = state.item_observability_status()
    out["b2_n_prior_days_items_observed"] = state.n_days_items_observed
    out["b2_n_prior_days_items_unobserved"] = state.n_days_items_unobserved
    out["b2_n_prior_days_items_zero_defects"] = state.n_days_items_zero_defects
    out["b2_n_prior_days_items_unavailable"] = state.n_days_items_unavailable
    out["b2_n_prior_days_items_expected_missing"] = state.n_days_items_expected_missing
    return out


def emit_b3(state: st.AsOfState, tgt_date: date) -> Dict[str, Any]:
    observable = state.n_severity_observable_days
    if observable == 0:
        status = "none"
    elif observable == state.n_days:
        status = "full"
    else:
        status = "partial"

    def graded(value: Any) -> Any:
        # pre-2018 severity is UNOBSERVABLE, never zero (contract).
        return value if observable > 0 else None

    return {
        # `observable` now requires post-2018 AND item-observable (state.py):
        # it is a real denominator for the first time. Before this repair a
        # post-2018 day with dark items counted as severity-observable.
        "b3_n_days_fine_severity_observable": observable,
        "b3_severity_observability_status": status,
        "b3_n_dangerous_items": graded(state.n_dangerous_items),
        "b3_n_dangerous_days": graded(state.n_dangerous_days),
        "b3_days_since_dangerous": state.days_since(state.last_dangerous_date, tgt_date),
        "b3_n_major_items": graded(state.n_major_items),
        "b3_n_major_days": graded(state.n_major_days),
        "b3_days_since_major": state.days_since(state.last_major_date, tgt_date),
        "b3_n_minor_items": graded(state.n_minor_items),
        "b3_n_minor_days": graded(state.n_minor_days),
        "b3_days_since_minor": state.days_since(state.last_minor_date, tgt_date),
        # both-era item counts: gated on ITEM observability, not on the era.
        "b3_n_prs_items": item_graded(state, state.n_prs_items),
        "b3_n_prs_item_days": item_graded(state, state.n_prs_item_days),
        "b3_days_since_prs_item": state.days_since(state.last_prs_item_date, tgt_date),
        "b3_n_fail_items_initial": item_graded(state, state.n_fail_items_initial),
        "b3_n_fail_items_final": item_graded(state, state.n_fail_items_final),
        "b3_n_advisory_items": item_graded(state, state.n_advisory_items),
        "b3_fail_item_rectified_share": _safe_div(float(state.n_prs_items),
                                                  float(state.n_fail_items_initial)),
    }


def emit_b4(state: st.AsOfState, tgt_date: date, age_years: Optional[float]) -> Dict[str, Any]:
    d1, d2 = state.burden_deltas()
    window = list(state.burden)
    burden_mean = (sum(window) / len(window)) if window else None
    band = mileage_band(state.last_valid_mileage)
    ordinal = mileage_band_ordinal(band)
    return {
        "b4_n_adv_to_fail_transitions": item_graded(
            state, sum(state.cat_adv_to_fail.values())),
        "b4_adv_to_fail_categories": item_graded(
            state, sum(1 for v in state.cat_adv_to_fail.values() if v > 0)),
        "b4_days_since_adv_to_fail": state.days_since(state.last_adv_to_fail_date, tgt_date),
        "b4_n_recurrence_after_repair": item_graded(
            state, sum(state.cat_recurrence.values())),
        "b4_recurrence_categories": item_graded(
            state, sum(1 for v in state.cat_recurrence.values() if v > 0)),
        "b4_days_since_recurrence": state.days_since(state.last_recurrence_date, tgt_date),
        "b4_burden_delta_1": d1,
        "b4_burden_delta_2": d2,
        "b4_burden_mean_last3": burden_mean,
        "b4_burden_x_age": (None if (burden_mean is None or age_years is None)
                            else burden_mean * age_years),
        "b4_mileage_band": band,
        "b4_burden_x_mileage_band_ord": (None if (burden_mean is None or ordinal is None)
                                         else burden_mean * ordinal),
        "b4_deterioration_slope": state.deterioration_slope(),
        "b4_deterioration_slope_n_days": state.slope_n,
    }


def emit_b5(state: st.AsOfState, tgt_date: date) -> Dict[str, Any]:
    gap = state.days_since(state.last_date, tgt_date)
    return {
        "b5_n_prior_multi_test_days": state.n_multi_test_days,
        "b5_max_tests_on_a_prior_day": state.max_tests_on_a_day if state.has_priors else None,
        "b5_n_prior_days_pass_and_fail": state.n_days_pass_and_fail,
        "b5_n_prior_nondefinitive_days": state.n_nondefinitive_days,
        "b5_n_prior_ambiguous_days": state.n_ambiguous_days,
        "b5_n_prior_mileage_conflict_days": state.n_mileage_conflict_days,
        "b5_last_day_n_tests": state.last_day_n_tests,
        "b5_last_day_n_distinct_outcomes": state.last_day_n_distinct_outcomes,
        "b5_last_day_has_pass": state.last_day_has_pass,
        "b5_last_day_has_fail": state.last_day_has_fail,
        "b5_last_day_has_prs": state.last_day_has_prs,
        "b5_last_day_has_nonresult": state.last_day_has_nonresult,
        "b5_days_since_prior_day": gap,
        "b5_gap_annual_band_flag": st.in_annual_band(gap),
        "b5_covid_straddle_flag": st.covid_straddle(state.last_date, tgt_date),
    }


def emit_b6(state: st.AsOfState, location_map_present: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {"b6_location_map_status":
                           "present" if location_map_present else "absent"}
    observed = location_map_present and state.pos_observed
    for prefix, groups in (("lat", LATERAL_GROUPS), ("long", LONGITUDINAL_GROUPS),
                           ("vert", VERTICAL_GROUPS)):
        for group in groups:
            out[f"b6_{prefix}_{group}_n"] = (
                int(state.pos_counts.get(f"pos_{prefix}_{group}_n") or 0)
                if observed else None)
    out["b6_pos_n_total"] = (int(state.pos_counts.get("pos_resolved_n") or 0)
                             if observed else None)
    return out


def emit_all(state: st.AsOfState, event: dict, location_map_present: bool) -> Dict[str, Any]:
    """All B1-B6 columns for one event, read BEFORE the target day's update."""
    tgt_date = event["tgt_date"]
    first_use = event.get("tgt_fud")
    row: Dict[str, Any] = {}
    row.update(emit_b1(state, tgt_date, first_use))
    row.update(emit_b2(state, tgt_date))
    row.update(emit_b3(state, tgt_date))
    row.update(emit_b4(state, tgt_date, row["b1_age_at_target_years"]))
    row.update(emit_b5(state, tgt_date))
    row.update(emit_b6(state, location_map_present))
    return row
