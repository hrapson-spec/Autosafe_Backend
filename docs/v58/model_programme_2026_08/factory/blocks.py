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
from typing import Any, Dict, List, Optional, Tuple

from . import day_outcomes as do
from . import rates
from . import state as st
from .taxonomy import (CANONICAL_CATEGORY_KEYS, CATEGORY_KEYS, LATERAL_GROUPS,
                       LONGITUDINAL_GROUPS, VERTICAL_GROUPS)

#: Digital MOT records begin 2005-01-01. This is the ABSOLUTE lower bound on
#: what could ever have been recorded -- NOT the floor a given build should use.
OBSERVABLE_FLOOR = date(2005, 1, 1)


@dataclass(frozen=True)
class HistoryFloors:
    """Per-channel earliest date on which history COULD have been recorded.

    D-1: `OBSERVABLE_FLOOR` used to be applied unconditionally, so a build
    loading only 2015+ still reported `b1_observable_years` reaching back to
    2005. For a vehicle first used in 2008 with a 2023 target that claims 15.0
    observable years against 8 loadable ones -- deflating
    `b1_density_per_observable_year` by ~47%, worst for exactly the oldest,
    highest-exposure vehicles, and `b1_left_censor_flag` (which fires on
    first_use < 2005) does not mark the affected 2005-2015 cohort.

    Train/eval consistency does not fix this: both sides carry the same
    mis-specified denominator, so it is a feature-quality defect rather than a
    parity defect, and it lands on precisely the comparison the exposure-
    normalisation argument rests on.

    `result` bounds test history; `item` and `severity` bound their own
    channels, which start later and must not borrow the result window.
    """

    result: date = OBSERVABLE_FLOOR
    item: date = OBSERVABLE_FLOOR
    severity: date = OBSERVABLE_FLOOR
    #: How `result` was chosen. Recorded in BUILD_MANIFEST; NOT a feature.
    source: str = "digital_records_2005"

    @staticmethod
    def from_input_years(years, source: str = "build_year_list") -> "HistoryFloors":
        """Floor each channel at the earliest year the build actually loads."""
        if not years:
            raise ValueError("cannot derive history floors from an empty year list")
        floor = max(OBSERVABLE_FLOOR, date(int(min(years)), 1, 1))
        return HistoryFloors(result=floor, item=floor, severity=floor, source=source)

    def to_manifest(self):
        return {"result": self.result.isoformat(), "item": self.item.isoformat(),
                "severity": self.severity.isoformat(), "source": self.source}


DEFAULT_FLOORS = HistoryFloors()

# --- pinned categorical vocabularies ----------------------------------------
# A derived status column's REACHABLE level set depends on the data window: the
# D-1 floor correction made `full` reachable in b1_history_coverage_grade where
# it previously was not, and a rare level ('none', 2 rows in 239) can fall
# entirely into one side of a train/valid split. CatBoost then quantises the two
# Pools to different layouts and refuses the fit outright -- a hard failure, not
# a degradation.
#
# The fix is to declare each vocabulary ONCE and carry it on every frame, so the
# Pool's category set is a property of the CONTRACT rather than of whichever
# levels a particular split happened to observe.
#
# Validation is MEMBERSHIP, not coverage: every observed value must be in the
# vocabulary, and no frame is required to observe every level. Requiring
# coverage would make a legitimately homogeneous cohort un-scoreable.

HISTORY_COVERAGE_GRADES: Tuple[str, ...] = ("none", "left_censored", "partial", "full")
OBSERVABLE_YEARS_STATUSES: Tuple[str, ...] = ("observed", "left_censored_2005",
                                              "first_use_missing")

PINNED_VOCABULARIES: Dict[str, Tuple[str, ...]] = {
    "b1_history_coverage_grade": HISTORY_COVERAGE_GRADES,
    "b1_observable_years_status": OBSERVABLE_YEARS_STATUSES,
}

#: Which pinned vocabularies are ORDERED. An ordered grade is rendered to the
#: model as its vocabulary INDEX -- a numeric feature whose mapping is the
#: contract -- rather than as an unordered category. Two reasons:
#:
#:  1. Correctness. none < left_censored < partial < full is a real ordering,
#:     and a categorical encoding discards it.
#:  2. Layout stability. A categorical's Pool layout depends on which levels a
#:     given split observes; with a rare level ('none': 2 rows in 239) a
#:     seed-dependent train/valid split can drop it, quantize() then derives a
#:     different layout, and CatBoost refuses the fit. An ordinal cannot vary
#:     that way, because the mapping is declared, not inferred.
#:
#: Nominal vocabularies (observed / left_censored_2005 / first_use_missing have
#: no order) stay categorical and are membership-validated only.
ORDERED_VOCABULARIES: Tuple[str, ...] = ("b1_history_coverage_grade",)


def vocabulary_ordinal(column: str, value: Optional[str]) -> Optional[int]:
    """Contract-defined index of `value` within its ordered vocabulary."""
    vocab = PINNED_VOCABULARIES[column]
    if value is None:
        return None
    return vocab.index(assert_in_vocabulary(column, value))


def assert_in_vocabulary(column: str, value: Optional[str]) -> Optional[str]:
    """Membership check for a pinned categorical. NULL passes; a stray value raises."""
    vocab = PINNED_VOCABULARIES.get(column)
    if vocab is None or value is None or value in vocab:
        return value
    raise ValueError(
        f"{column}={value!r} is outside its pinned vocabulary {vocab}. Either "
        f"the emitter invented a level or the vocabulary is stale; both are "
        f"contract changes, not runtime conditions.")
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


#: serve_class -- can this column ever reach a live prediction?
SERVE_DEPLOYABLE = "deployable"
SERVE_RESEARCH = "research_only"


@dataclass(frozen=True)
class ColumnSpec:
    """One emitted column.

    `era_observability` and `serve_class` are ORTHOGONAL and must not be
    conflated. Era observability is what the DATA can support (pre-2018 has no
    severity ladder). serve_class is what SERVING can consume (the live API
    exposes no test_type, so anything keyed on it can never ship). A column can
    be all_eras and research-only, or post-2018-only and deployable.

    `screen_only` is a third, independent axis: a column may legitimately exist
    in the candidate frame -- diagnostics, denominator variants under test --
    while being permanently barred from an adopted featureset.
    """

    name: str
    block: str
    dtype: str
    definition: str
    era_observability: str = ERA_ALL
    serve_class: Optional[str] = None
    screen_only: bool = False

    def __post_init__(self):
        if self.serve_class is None:
            # A research-only INPUT cannot yield a deployable column, so the
            # default is derived rather than restated at 200 call sites. An
            # explicit serve_class always wins -- but it may only classify
            # DOWN (see `resolved_serve_class`).
            object.__setattr__(self, "serve_class",
                               SERVE_RESEARCH if self.era_observability == ERA_RESEARCH
                               else SERVE_DEPLOYABLE)
        elif (self.era_observability == ERA_RESEARCH
              and self.serve_class == SERVE_DEPLOYABLE):
            raise ValueError(
                f"{self.name}: era_observability=research_only_input cannot be "
                f"declared serve_class=deployable. Classification may only go "
                f"DOWN; promoting a test_type-consuming column needs an owner "
                f"ruling and a verified live serve path, not a keyword.")


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

# --- B7D: deployable day-grain history (Lane D) -----------------------------
# Day grain THROUGHOUT, under the five-state contract in day_outcomes.py.
# AMBIGUOUS and UNAVAILABLE days are excluded from BOTH sides of every
# proportion here; their exposure is emitted so the exclusion is auditable.
#
# Day grain is NOT test grain and these names do not pretend it is. There is
# deliberately no `major_days_per_valid_test`: that is a test-grain estimand
# Lane D cannot honestly reconstruct, because 35.09% of targets carry at least
# one prior day whose initial/retest split is unidentifiable.

B7D_COLUMNS: List[ColumnSpec] = [
    ColumnSpec("b7d_prev_day_outcome", "B7D", "VARCHAR", "Five-state outcome of the most recent prior test-day: PASS / PRS / FAIL / AMBIGUOUS / NO_HISTORY. The deployable analogue of the immediately-preceding-event term."),
    ColumnSpec("b7d_n_prior_fail_days", "B7D", "BIGINT", "Prior test-days whose five-state outcome is FAIL. AMBIGUOUS days are excluded, never counted as non-failures."),
    ColumnSpec("b7d_n_prior_pass_days", "B7D", "BIGINT", "Prior test-days whose five-state outcome is PASS (PRS counted separately)."),
    ColumnSpec("b7d_n_prior_prs_days", "B7D", "BIGINT", "Prior test-days whose five-state outcome is PRS -- a pass whose defects were real. Tagged research-only pending confirmation that PRS is visible at serving (SERVE_VIEW invariant 3).", ERA_RESEARCH),
    ColumnSpec("b7d_n_prior_outcome_observable_days", "B7D", "BIGINT", "Prior test-days whose outcome is identified (PASS + PRS + FAIL) -- the honest denominator for every b7d proportion."),
    ColumnSpec("b7d_n_prior_outcome_ambiguous_days", "B7D", "BIGINT", "Prior test-days carrying a definitive outcome that is NOT identified (same-stratum FAIL + pass). The excluded exposure, made visible."),
    ColumnSpec("b7d_days_since_fail_day", "B7D", "BIGINT", "Days since the most recent prior test-day whose outcome is FAIL; NULL when never."),
    ColumnSpec("b7d_last_day_n_fail_items", "B7D", "BIGINT", "Fail-bearing items on the most recent prior test-day; NULL when that day's detail is unobservable."),
    ColumnSpec("b7d_last_day_max_severity_ord", "B7D", "BIGINT", "Max severity rung on the most recent prior test-day: 0 clean / 1 advisory / 2 minor / 3 major / 4 dangerous; NULL when unobservable.", ERA_POST2018),
    ColumnSpec("b7d_last_day_n_major", "B7D", "BIGINT", "Major items on the most recent prior test-day; NULL when severity is unobservable.", ERA_POST2018),
    ColumnSpec("b7d_last_day_n_dangerous", "B7D", "BIGINT", "Dangerous items on the most recent prior test-day; NULL when severity is unobservable.", ERA_POST2018),
    ColumnSpec("b7d_last_fail_day_n_items", "B7D", "BIGINT", "Fail-bearing items on the most recent prior test-day that actually FAILED; NULL when never or unobservable."),
    ColumnSpec("b7d_last_fail_day_n_categories", "B7D", "BIGINT", "Distinct categories carrying a fail-bearing item on the most recent prior FAIL day -- the breadth of the last failure; NULL when never or unobservable."),
    ColumnSpec("b7d_last_fail_day_max_severity_ord", "B7D", "BIGINT", "Max severity rung on the most recent prior FAIL day; NULL when never or unobservable.", ERA_POST2018),
    ColumnSpec("b7d_fail_days_per_outcome_observable_day", "B7D", "DOUBLE", "Beta-binomial smoothed share of outcome-observable prior test-days that failed; NULL without an observable day."),
    ColumnSpec("b7d_fail_days_per_result_observable_year", "B7D", "DOUBLE", "Gamma-Poisson smoothed FAIL days per year of result-observable exposure; NULL when the window is non-positive."),
    ColumnSpec("b7d_major_days_per_severity_observable_day", "B7D", "DOUBLE", "Beta-binomial smoothed share of severity-observable prior test-days carrying a major item; NULL when severity was never observable.", ERA_POST2018),
    ColumnSpec("b7d_major_days_per_severity_observable_year", "B7D", "DOUBLE", "Gamma-Poisson smoothed major days per year of severity-observable exposure; NULL when that window is non-positive.", ERA_POST2018),
    ColumnSpec("b7d_dangerous_days_per_severity_observable_day", "B7D", "DOUBLE", "Beta-binomial smoothed share of severity-observable prior test-days carrying a dangerous item; NULL when severity was never observable.", ERA_POST2018),
    ColumnSpec("b7d_advisory_days_per_item_observable_day", "B7D", "DOUBLE", "Beta-binomial smoothed share of item-observable prior test-days carrying an advisory; NULL when no prior day is item-observable."),
    ColumnSpec("b7d_n_fail_days_cap2y", "B7D", "BIGINT", "b7d_n_prior_fail_days restricted to the trailing 2-year window before tgt_date."),
    ColumnSpec("b7d_n_major_days_cap2y", "B7D", "BIGINT", "Prior test-days carrying a major item, restricted to the trailing 2-year window; NULL when severity was never observable.", ERA_POST2018),
    ColumnSpec("b7d_n_dangerous_days_cap2y", "B7D", "BIGINT", "Prior test-days carrying a dangerous item, restricted to the trailing 2-year window; NULL when severity was never observable.", ERA_POST2018),
    ColumnSpec("b7d_last3day_fail_items_sum", "B7D", "BIGINT", "Fail-bearing items summed over the last up-to-3 item-observable prior test-days; NULL when none is observable."),
    ColumnSpec("b7d_last3day_n_outcome_observed", "B7D", "BIGINT", "How many of the last up-to-3 prior test-days had an identified outcome -- the explicit bounded denominator for the outcome channel."),
    ColumnSpec("b7d_last3day_n_detail_observed", "B7D", "BIGINT", "How many of the last up-to-3 prior test-days had observable defect detail -- a SEPARATE denominator from the outcome channel, never reused across the two."),
    ColumnSpec("b7d_recent3day_minus_earlier_burden", "B7D", "DOUBLE", "Mean fail-bearing items over the last up-to-3 item-observable days minus the mean over all earlier ones; NULL until an earlier period exists."),
    ColumnSpec("b7d_n_adv_to_minor_transitions", "B7D", "BIGINT", "Categories escalating from advisory to minor on a strictly later day; NULL when severity was never observable.", ERA_POST2018),
    ColumnSpec("b7d_n_minor_to_major_transitions", "B7D", "BIGINT", "Categories escalating to major from a strictly lower rung observed earlier; NULL when severity was never observable.", ERA_POST2018),
    ColumnSpec("b7d_n_major_to_dangerous_transitions", "B7D", "BIGINT", "Categories escalating to dangerous from a strictly lower rung observed earlier; NULL when severity was never observable.", ERA_POST2018),
    ColumnSpec("b7d_n_severity_transition_opportunities", "B7D", "BIGINT", "Category-days where a rung was already on record and could therefore have escalated -- the honest denominator; NULL when severity was never observable.", ERA_POST2018),
    ColumnSpec("b7d_severity_escalation_share", "B7D", "DOUBLE", "Beta-binomial smoothed share of transition opportunities that escalated; NULL without an opportunity.", ERA_POST2018),
    ColumnSpec("b7d_days_since_severity_escalation", "B7D", "BIGINT", "Days since the most recent category severity escalation; NULL when never.", ERA_POST2018),
    ColumnSpec("b7d_outcome_history_status", "B7D", "VARCHAR", "no_priors / none / partial / full -- outcome identifiability across this vehicle's prior test-days."),
    ColumnSpec("b7d_severity_transition_status", "B7D", "VARCHAR", "observed / unobserved -- whether any prior day could support a category severity rung."),
]

# --- B7R: initial-presentation history (Lane R, research-only) ---------------
# EVERY column here consumes test_type-in-history, so the whole block is
# research_only_input by construction. It measures an information CEILING --
# what correctly reconstructed initial-presentation state is worth if it could
# be served -- and no adoption decision follows from it.
#
# Grain is the initial-presentation DAY: a prior day carrying >=1 definitive
# NT, resolved by the same five-state rule restricted to NT rows. The
# record-count twin is the existing b1_n_prior_initials, reused not re-emitted.

B7R_COLUMNS: List[ColumnSpec] = [
    ColumnSpec("b7r_prev_initial_outcome", "B7R", "VARCHAR", "Five-state outcome of the most recent prior initial-presentation day: PASS / PRS / FAIL / NO_HISTORY / UNAVAILABLE.", ERA_RESEARCH),
    ColumnSpec("b7r_days_since_prev_initial", "B7R", "BIGINT", "Days since the most recent prior initial-presentation day with an identified outcome; NULL when never.", ERA_RESEARCH),
    ColumnSpec("b7r_days_since_prev_initial_fail", "B7R", "BIGINT", "Days since the most recent prior initial presentation that FAILED; NULL when never.", ERA_RESEARCH),
    ColumnSpec("b7r_n_prior_initial_days", "B7R", "BIGINT", "Prior initial-presentation days with an identified outcome -- the denominator for every b7r share.", ERA_RESEARCH),
    ColumnSpec("b7r_n_prior_initial_fail_days", "B7R", "BIGINT", "Prior initial-presentation days whose outcome is FAIL.", ERA_RESEARCH),
    ColumnSpec("b7r_n_prior_initial_prs_days", "B7R", "BIGINT", "Prior initial-presentation days whose outcome is PRS.", ERA_RESEARCH),
    ColumnSpec("b7r_n_prior_initial_ambiguous_days", "B7R", "BIGINT", "Prior initial-presentation days whose outcome is not identified -- excluded exposure, made visible.", ERA_RESEARCH),
    ColumnSpec("b7r_initial_fail_share", "B7R", "DOUBLE", "Beta-binomial smoothed share of identified prior initial presentations that FAILED; NULL without one.", ERA_RESEARCH),
    ColumnSpec("b7r_initial_adverse_share", "B7R", "DOUBLE", "Beta-binomial smoothed share of identified prior initial presentations that were FAIL or PRS; NULL without one.", ERA_RESEARCH),
    ColumnSpec("b7r_initial_fail_days_per_result_observable_year", "B7R", "DOUBLE", "Gamma-Poisson smoothed initial-presentation failures per year of result-observable exposure; NULL when the window is non-positive.", ERA_RESEARCH),
    ColumnSpec("b7r_last3_n_initial_observed", "B7R", "BIGINT", "How many of the last up-to-3 initial presentations are on record -- 1 or 2 when that is all there is, never 3.", ERA_RESEARCH),
    ColumnSpec("b7r_last3_n_initial_fail", "B7R", "BIGINT", "Failures among the last up-to-3 identified initial presentations.", ERA_RESEARCH),
    ColumnSpec("b7r_last3_n_initial_adverse", "B7R", "BIGINT", "FAIL or PRS outcomes among the last up-to-3 identified initial presentations.", ERA_RESEARCH),
    ColumnSpec("b7r_recent3_minus_earlier_fail_share", "B7R", "DOUBLE", "Fail share over the last up-to-3 initial presentations minus the share over all earlier ones; NULL until an earlier period exists.", ERA_RESEARCH),
    ColumnSpec("b7r_current_initial_fail_streak", "B7R", "BIGINT", "Consecutive most-recent initial presentations that failed. A retest pass does NOT break it.", ERA_RESEARCH),
    ColumnSpec("b7r_current_initial_pass_streak", "B7R", "BIGINT", "Consecutive most-recent initial presentations that did not fail.", ERA_RESEARCH),
    ColumnSpec("b7r_max_initial_fail_streak", "B7R", "BIGINT", "Longest run of consecutive failing initial presentations ever observed.", ERA_RESEARCH),
    ColumnSpec("b7r_initial_fail_decay_num_hl1y", "B7R", "DOUBLE", "Recency-weighted count of failing initial presentations, half-life 1 year; NULL without history.", ERA_RESEARCH),
    ColumnSpec("b7r_initial_opportunity_decay_den_hl1y", "B7R", "DOUBLE", "Recency-weighted count of initial presentations (the opportunities), half-life 1 year. Emitted so a 0.5 rate over 0.5 weighted presentations is distinguishable from 0.5 over 8.", ERA_RESEARCH),
    ColumnSpec("b7r_initial_fail_decay_rate_hl1y", "B7R", "DOUBLE", "Recency-weighted proportion of initial presentations that failed, half-life 1 year; NULL without history.", ERA_RESEARCH),
    ColumnSpec("b7r_initial_fail_decay_num_hl3y", "B7R", "DOUBLE", "Recency-weighted count of failing initial presentations, half-life 3 years; NULL without history.", ERA_RESEARCH),
    ColumnSpec("b7r_initial_opportunity_decay_den_hl3y", "B7R", "DOUBLE", "Recency-weighted count of initial presentations, half-life 3 years.", ERA_RESEARCH),
    ColumnSpec("b7r_initial_fail_decay_rate_hl3y", "B7R", "DOUBLE", "Recency-weighted proportion of initial presentations that failed, half-life 3 years; NULL without history.", ERA_RESEARCH),
    ColumnSpec("b7r_prev_initial_n_fail_items", "B7R", "BIGINT", "Fail-bearing items on the most recent identified prior initial presentation; NULL when its detail is unobservable.", ERA_RESEARCH),
    ColumnSpec("b7r_prev_initial_n_categories", "B7R", "BIGINT", "Distinct categories carrying a fail-bearing item on the most recent identified prior initial presentation; NULL when unobservable.", ERA_RESEARCH),
    ColumnSpec("b7r_prev_initial_max_severity_ord", "B7R", "BIGINT", "Max severity rung on the most recent identified prior initial presentation; NULL when unobservable.", ERA_RESEARCH),
    ColumnSpec("b7r_prev_initial_n_major", "B7R", "BIGINT", "Major items on the most recent identified prior initial presentation; NULL when severity is unobservable.", ERA_RESEARCH),
    ColumnSpec("b7r_prev_initial_n_dangerous", "B7R", "BIGINT", "Dangerous items on the most recent identified prior initial presentation; NULL when severity is unobservable.", ERA_RESEARCH),
    ColumnSpec("b7r_last3_initial_fail_items_sum", "B7R", "BIGINT", "Fail-bearing items summed over the last up-to-3 detail-observable initial presentations; NULL when none is observable.", ERA_RESEARCH),
    ColumnSpec("b7r_last3_initial_categories_sum", "B7R", "BIGINT", "Distinct fail categories summed over the last up-to-3 detail-observable initial presentations; NULL when none is observable.", ERA_RESEARCH),
    ColumnSpec("b7r_recent3_minus_earlier_burden", "B7R", "DOUBLE", "Mean fail-bearing items over the last up-to-3 detail-observable initial presentations minus the mean over all earlier ones; NULL until an earlier period exists.", ERA_RESEARCH),
    ColumnSpec("b7r_fail_items_per_initial_event", "B7R", "DOUBLE", "Gamma-Poisson smoothed fail-bearing items per detail-observable initial presentation; NULL when none is observable.", ERA_RESEARCH),
    ColumnSpec("b7r_initial_outcome_history_status", "B7R", "VARCHAR", "no_priors / none / partial / full -- outcome identifiability across this vehicle's prior initial presentations.", ERA_RESEARCH),
    ColumnSpec("b7r_initial_detail_history_status", "B7R", "VARCHAR", "no_priors / none / partial / full -- defect-detail observability across this vehicle's prior initial presentations.", ERA_RESEARCH),
]

BLOCK_COLUMNS: Dict[str, List[ColumnSpec]] = {
    "meta": META_COLUMNS, "B1": B1_COLUMNS, "B2": B2_COLUMNS,
    "B3": B3_COLUMNS, "B4": B4_COLUMNS, "B5": B5_COLUMNS, "B6": B6_COLUMNS,
    "B7D": B7D_COLUMNS, "B7R": B7R_COLUMNS,
}
NEW_BLOCKS = ("B1", "B2", "B3", "B4", "B5", "B6", "B7D", "B7R")

#: PHYSICAL cap: every candidate column the factory may emit into one superset
#: frame. Screening happens here.
PHYSICAL_CANDIDATE_CAP = 220
#: ADOPTED cap: columns a deployable model may carry. UNCHANGED at 150 -- a
#: research-only candidate never consumed it, so opening B7 does not raise it.
ADOPTED_COLUMN_CAP = 150
#: Retained name, now bound to the PHYSICAL cap. The two were the same number
#: only while every emitted column was an adoption candidate.
NEW_COLUMN_CAP = PHYSICAL_CANDIDATE_CAP

ALL_COLUMNS: List[ColumnSpec] = [c for block in ("meta",) + NEW_BLOCKS
                                 for c in BLOCK_COLUMNS[block]]
COLUMN_NAMES: List[str] = [c.name for c in ALL_COLUMNS]


def n_new_columns() -> int:
    """Physical candidate columns across every new block."""
    return sum(len(BLOCK_COLUMNS[b]) for b in NEW_BLOCKS)


def deployable_columns() -> List[ColumnSpec]:
    """F_deployable: what a shippable model may draw on."""
    return [c for c in ALL_COLUMNS
            if c.block in NEW_BLOCKS
            and c.serve_class == SERVE_DEPLOYABLE
            and not c.screen_only]


def n_deployable_columns() -> int:
    return len(deployable_columns())


def adoption_headroom() -> int:
    """Slots left under the adopted cap, given today's deployable incumbents."""
    return ADOPTED_COLUMN_CAP - n_deployable_columns()


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

def emit_b1(state: st.AsOfState, tgt_date: date, first_use: Optional[date],
            floors: HistoryFloors = DEFAULT_FLOORS) -> Dict[str, Any]:
    history_years = _years_between(state.first_date, tgt_date)
    # D-1: the floor is the RESULT-channel floor for this build, not the
    # absolute 2005 digital-records bound. `left_censored` therefore names the
    # cohort whose history is truncated by THIS build, which on a 2015-basis
    # build is the 2005-2015 cohort the old flag silently missed.
    result_floor = floors.result
    if first_use is None:
        obs_start, obs_status = result_floor, "first_use_missing"
    elif first_use < result_floor:
        obs_start, obs_status = result_floor, "left_censored_2005"
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
        # Membership-checked at emit: a level outside the pinned vocabulary is
        # a contract change and must fail here, not surface as a Pool-layout
        # mismatch three stages downstream.
        "b1_observable_years_status": assert_in_vocabulary(
            "b1_observable_years_status", obs_status),
        "b1_history_coverage_grade": assert_in_vocabulary(
            "b1_history_coverage_grade", grade),
        "b1_left_censor_flag": (first_use is not None and first_use < result_floor),
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


def _coverage_status(observed: int, total: int) -> str:
    """no_priors / none / partial / full. Never collapses 'none' into 'no_priors'.

    'none' means the days existed and we could not read them; 'no_priors' means
    there was nothing to read and the zeros are certain. A model that cannot
    tell those apart will read unavailability as cleanliness.
    """
    if total <= 0:
        return "no_priors"
    if observed <= 0:
        return "none"
    return "full" if observed >= total else "partial"


def emit_b7d(state: st.AsOfState, tgt_date: date,
             priors: "rates.PriorSet") -> Dict[str, Any]:
    """Lane D: deployable, day-grain history under the five-state contract."""
    counts = state.day_state_counts
    n_obs = state.n_outcome_observable_days()
    n_fail = counts[do.FAIL]
    n_days = state.n_days

    # Exposure windows. Each rate divides by ITS OWN channel's window; sharing
    # one would charge a vehicle for years in which the channel was dark.
    result_years = _years_between(state.first_outcome_observable_date, tgt_date)
    severity_years = _years_between(state.first_severity_observable_date, tgt_date)

    sev_days = state.n_severity_observable_days
    item_days = state.n_days_items_observed
    sev_seen = sev_days > 0
    graded = (lambda v: v if sev_seen else None)

    n_major_days = state.n_major_days if sev_seen else None
    n_dang_days = state.n_dangerous_days if sev_seen else None
    opportunities = (state.n_severity_transition_opportunities
                     if state.severity_transition_observed else None)
    escalations = ((state.n_adv_to_minor + state.n_minor_to_major
                    + state.n_major_to_dangerous)
                   if state.severity_transition_observed else None)

    return {
        "b7d_prev_day_outcome": state.prev_day_state or do.NO_HISTORY,
        "b7d_n_prior_fail_days": n_fail,
        "b7d_n_prior_pass_days": counts[do.PASS],
        "b7d_n_prior_prs_days": counts[do.PRS],
        "b7d_n_prior_outcome_observable_days": n_obs,
        "b7d_n_prior_outcome_ambiguous_days": counts[do.AMBIGUOUS],
        "b7d_days_since_fail_day": state.days_since(state.last_fail_date, tgt_date),
        "b7d_last_day_n_fail_items": state.last_day_n_fail_items,
        "b7d_last_day_max_severity_ord": state.last_day_severity_ord,
        "b7d_last_day_n_major": state.last_day_n_major,
        "b7d_last_day_n_dangerous": state.last_day_n_dangerous,
        "b7d_last_fail_day_n_items": state.last_fail_day_n_items,
        "b7d_last_fail_day_n_categories": state.last_fail_day_n_categories,
        "b7d_last_fail_day_max_severity_ord": state.last_fail_day_severity_ord,
        "b7d_fail_days_per_outcome_observable_day": rates.smoothed_proportion(
            n_fail, n_obs, priors.beta["fail_day_share"]),
        "b7d_fail_days_per_result_observable_year": rates.smoothed_rate_per_year(
            n_fail, result_years, priors.gamma["fail_days_per_year"]),
        "b7d_major_days_per_severity_observable_day": rates.smoothed_proportion(
            n_major_days, sev_days if sev_seen else None,
            priors.beta["major_day_share"]),
        "b7d_major_days_per_severity_observable_year": rates.smoothed_rate_per_year(
            n_major_days, severity_years, priors.gamma["major_days_per_year"]),
        "b7d_dangerous_days_per_severity_observable_day": rates.smoothed_proportion(
            n_dang_days, sev_days if sev_seen else None,
            priors.beta["dangerous_day_share"]),
        "b7d_advisory_days_per_item_observable_day": rates.smoothed_proportion(
            state.n_advisory_days if item_days > 0 else None,
            item_days if item_days > 0 else None,
            priors.beta["advisory_day_share"]),
        "b7d_n_fail_days_cap2y": state.n_within(state.fail_day_dates, tgt_date, 2.0),
        "b7d_n_major_days_cap2y": graded(
            state.n_within(state.major_day_dates, tgt_date, 2.0)),
        "b7d_n_dangerous_days_cap2y": graded(
            state.n_within(state.dangerous_day_dates, tgt_date, 2.0)),
        "b7d_last3day_fail_items_sum": (sum(state.last3_day_fail_items)
                                        if state.last3_day_fail_items else None),
        "b7d_last3day_n_outcome_observed": sum(state.last3_day_outcome_observed),
        "b7d_last3day_n_detail_observed": sum(state.last3_day_detail_observed),
        "b7d_recent3day_minus_earlier_burden": state.recent_minus_earlier(
            state.last3_day_fail_items, state.sum_fail_items_all_days,
            state.n_days_fail_items_observed),
        "b7d_n_adv_to_minor_transitions": graded(state.n_adv_to_minor),
        "b7d_n_minor_to_major_transitions": graded(state.n_minor_to_major),
        "b7d_n_major_to_dangerous_transitions": graded(state.n_major_to_dangerous),
        "b7d_n_severity_transition_opportunities": opportunities,
        "b7d_severity_escalation_share": rates.smoothed_proportion(
            escalations, opportunities, priors.beta["escalation_share"]),
        "b7d_days_since_severity_escalation": state.days_since(
            state.last_escalation_date, tgt_date),
        "b7d_outcome_history_status": _coverage_status(n_obs, n_days),
        "b7d_severity_transition_status": ("observed" if state.severity_transition_observed
                                           else "unobserved"),
    }


def emit_b7r(state: st.AsOfState, tgt_date: date,
             priors: "rates.PriorSet") -> Dict[str, Any]:
    """Lane R: initial-presentation history. RESEARCH-ONLY, by construction.

    Every column consumes test_type-in-history. None of them can be served, and
    none competes for the adopted cap; the block measures an information
    ceiling, not an adoption candidate.
    """
    counts = state.initial_state_counts
    n_obs = do.outcome_observable_total(counts)
    n_fail = counts[do.FAIL]
    n_adverse = counts[do.FAIL] + counts[do.PRS]
    n_total_initial_days = sum(counts.values())
    result_years = _years_between(state.first_outcome_observable_date, tgt_date)

    last3 = list(state.last3_initial)
    last3_n = len(last3)
    last3_fail = sum(1 for s in last3 if s == do.FAIL)
    last3_adverse = sum(1 for s in last3 if s in (do.FAIL, do.PRS))
    earlier_n = n_obs - last3_n
    recent_minus_earlier = None
    if last3_n and earlier_n > 0:
        recent_minus_earlier = (last3_fail / last3_n) - ((n_fail - last3_fail) / earlier_n)

    decayed = {k: state.decayed(k, tgt_date) for k in st.DECAY_HALF_LIVES}
    detail_days = state.n_initial_days_detail_observed

    row: Dict[str, Any] = {
        "b7r_prev_initial_outcome": state.prev_initial_state or do.NO_HISTORY,
        "b7r_days_since_prev_initial": state.days_since(state.prev_initial_date, tgt_date),
        "b7r_days_since_prev_initial_fail": state.days_since(
            state.prev_initial_fail_date, tgt_date),
        "b7r_n_prior_initial_days": n_obs,
        "b7r_n_prior_initial_fail_days": n_fail,
        "b7r_n_prior_initial_prs_days": counts[do.PRS],
        "b7r_n_prior_initial_ambiguous_days": counts[do.AMBIGUOUS],
        "b7r_initial_fail_share": rates.smoothed_proportion(
            n_fail, n_obs, priors.beta["initial_fail_share"]),
        "b7r_initial_adverse_share": rates.smoothed_proportion(
            n_adverse, n_obs, priors.beta["initial_adverse_share"]),
        "b7r_initial_fail_days_per_result_observable_year": rates.smoothed_rate_per_year(
            n_fail, result_years, priors.gamma["initial_fail_days_per_year"]),
        "b7r_last3_n_initial_observed": last3_n,
        "b7r_last3_n_initial_fail": last3_fail,
        "b7r_last3_n_initial_adverse": last3_adverse,
        "b7r_recent3_minus_earlier_fail_share": recent_minus_earlier,
        "b7r_current_initial_fail_streak": state.cur_initial_fail_streak,
        "b7r_current_initial_pass_streak": state.cur_initial_pass_streak,
        "b7r_max_initial_fail_streak": state.max_initial_fail_streak,
        "b7r_prev_initial_n_fail_items": state.prev_initial_n_fail_items,
        "b7r_prev_initial_n_categories": state.prev_initial_n_categories,
        "b7r_prev_initial_max_severity_ord": state.prev_initial_severity_ord,
        "b7r_prev_initial_n_major": state.prev_initial_n_major,
        "b7r_prev_initial_n_dangerous": state.prev_initial_n_dangerous,
        "b7r_last3_initial_fail_items_sum": (sum(state.last3_initial_fail_items)
                                             if state.last3_initial_fail_items else None),
        "b7r_last3_initial_categories_sum": (sum(state.last3_initial_categories)
                                             if state.last3_initial_categories else None),
        "b7r_recent3_minus_earlier_burden": state.recent_minus_earlier(
            state.last3_initial_fail_items, state.sum_initial_fail_items, detail_days),
        "b7r_fail_items_per_initial_event": rates.smoothed_rate_per_year(
            state.sum_initial_fail_items if detail_days else None,
            float(detail_days) if detail_days else None,
            priors.gamma["fail_items_per_initial_event"]),
        "b7r_initial_outcome_history_status": _coverage_status(n_obs, n_total_initial_days),
        "b7r_initial_detail_history_status": _coverage_status(detail_days, n_obs),
    }
    for key, (num, den) in decayed.items():
        row[f"b7r_initial_fail_decay_num_{key}"] = num
        row[f"b7r_initial_opportunity_decay_den_{key}"] = den
        row[f"b7r_initial_fail_decay_rate_{key}"] = (
            None if not num and not den else _safe_div(num, den))
    return row


def emit_all(state: st.AsOfState, event: dict, location_map_present: bool,
             priors: Optional["rates.PriorSet"] = None,
             floors: HistoryFloors = DEFAULT_FLOORS) -> Dict[str, Any]:
    """All new-block columns for one event, read BEFORE the target day's update."""
    tgt_date = event["tgt_date"]
    first_use = event.get("tgt_fud")
    priors = priors if priors is not None else rates.PROVISIONAL_PRIORS
    row: Dict[str, Any] = {}
    row.update(emit_b1(state, tgt_date, first_use, floors))
    row.update(emit_b2(state, tgt_date))
    row.update(emit_b3(state, tgt_date))
    row.update(emit_b4(state, tgt_date, row["b1_age_at_target_years"]))
    row.update(emit_b5(state, tgt_date))
    row.update(emit_b6(state, location_map_present))
    row.update(emit_b7d(state, tgt_date, priors))
    row.update(emit_b7r(state, tgt_date, priors))
    return row
