"""as_of_state: per-vehicle running aggregates with EMIT-BEFORE-UPDATE.

Contract §Atoms 3. The scan walks a vehicle's test-DAYS in calendar order. For
each day it (1) emits every target event on that day from the state as it stands
-- which contains strictly-earlier calendar days only -- and only then (2) folds
the day in. That single ordering is what falsifiers 1 and 4 probe.

There is no within-day ordering anywhere: a day is an unordered multiset, its
outcome comes from `pipeline.lake.cycles._cluster_outcome` (imported, not
re-implemented), and every accumulator below is a set function of the day.

The state is bounded: running scalars, per-category scalars, a 3-deep burden
window and the vehicle's prior test tuples (max ~50 in the measured full-depth
population, pathology bound 80).
"""
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Deque, Dict, List, Optional, Sequence, Set, Tuple

# cycles.py owns the D13 day-cluster semantics; we import them.
# `_cluster_outcome` is private today -- see FEATURE_DICTIONARY.md "open
# questions": a public alias in pipeline/lake/cycles.py would be preferable to
# either importing a private name or re-implementing the rule (which the
# contract forbids).
from pipeline.lake.cycles import AMBIGUOUS, DEFINITIVE, _cluster_outcome

from . import observability as obs
from .taxonomy import CATEGORY_KEYS

DAYS_PER_YEAR = 365.25
#: COVID MOT-extension window (contract, B5).
COVID_EXT_START = date(2020, 3, 30)
COVID_EXT_END = date(2020, 8, 1)
#: Inter-test gap band treated as "annual" (DATA_ASSESSMENT §3: 86.8% of 2023
#: events fall in [300, 430]).
ANNUAL_BAND = (300, 430)

#: Trailing depth caps emitted alongside the uncapped aggregates (owner ruling
#: 2026-08-12, schedule risk H3: computing them in the SINGLE scan is free now
#: and a full re-scan later). Each cap is a TRAILING window measured back from
#: the target date; the strictly-earlier-day rule still applies inside it.
DEPTH_CAPS: Tuple[Tuple[float, str], ...] = ((2.0, "cap2y"), (5.0, "cap5y"))


def cap_days(cap_years: float) -> int:
    return round(cap_years * DAYS_PER_YEAR)


@dataclass
class PriorTest:
    """One prior test record, kept for the packets emitter (record grain)."""

    test_id: int
    test_date: date
    test_type: Optional[str]
    outcome: Optional[str]
    mileage: Optional[int]
    #: NULL when this test's defect detail is not observable -- NEVER 0.
    n_items: Optional[int]
    defects: Optional[List[dict]]
    #: PREREG_CUBE_v2 §4 state; the packets emitter renders the three states
    #: from this and never from `defects is None`.
    items_observability: Optional[str] = None

    @property
    def items_observed(self) -> bool:
        return obs.is_observed(self.items_observability)


@dataclass
class DayAtom:
    """One (vehicle_id, test_date) atom, as staged. Unordered by construction."""

    vehicle_id: int
    test_date: date
    n_tests_day: int
    n_initial_day: int
    n_definitive_day: int
    n_nonresult_day: int
    has_pass: bool
    has_fail: bool
    has_prs: bool
    has_nonresult: bool
    n_distinct_outcomes: int
    max_valid_mileage_day: Optional[int]
    min_valid_mileage_day: Optional[int]
    n_valid_mileage_day: int
    mileage_conflict: bool
    severity_observable: bool
    first_use_date: Optional[date]
    items: Dict[str, Any]           # summed item_test_atom columns
    tests: List[dict]               # per-test packet payload (content-sorted)
    # --- item observability (PREREG_CUBE_v2 §4) ---------------------------
    n_tests_items_observed: int = 0
    n_tests_items_unobserved: int = 0
    n_tests_items_with_defects: int = 0
    n_tests_items_zero_defects: int = 0
    n_tests_items_unavailable: int = 0
    n_tests_items_expected_missing: int = 0

    @property
    def cluster_outcome(self) -> str:
        """D13 day-cluster outcome (cycles.py semantics, imported)."""
        rows = [{"test_type": t.get("ttype"), "outcome": t.get("outcome")}
                for t in self.tests]
        return _cluster_outcome(rows)

    @property
    def is_ambiguous(self) -> bool:
        return self.cluster_outcome == AMBIGUOUS

    @property
    def is_definitive(self) -> bool:
        return any(t.get("outcome") in DEFINITIVE for t in self.tests)

    # --- item observability -----------------------------------------------

    @property
    def items_observed(self) -> bool:
        """True when >=1 test on this day has observable defect detail."""
        return self.n_tests_items_observed > 0

    @property
    def items_observability(self) -> str:
        """Day-grain rollup of the per-test states."""
        return obs.day_rollup(self.n_tests_items_observed,
                              self.n_tests_items_unobserved,
                              self.n_tests_items_with_defects,
                              self.n_tests_items_unavailable,
                              self.n_tests_items_expected_missing)

    def item(self, name: str) -> Any:
        """A summed item column, NULL-PRESERVING.

        MECHANISM M3 CLOSED. This method used to read
        `item(self, name, default=0)` -> `return default if value is None else
        value` (state.py:102-104): a SECOND, independent coalesce that would
        have silently undone the SQL repair on its own. There is deliberately
        no `default` parameter any more, so a caller cannot re-introduce it
        without editing the signature: callers must consult `items_observed`.
        """
        return self.items.get(name)

    def _cat(self, name: str) -> Optional[int]:
        value = self.items.get(name)
        return None if value is None else int(value)

    def cat_n(self, key: str) -> Optional[int]:
        return self._cat(f"cat_{key}_n")

    def cat_adv(self, key: str) -> Optional[int]:
        return self._cat(f"cat_{key}_adv")

    def cat_fail(self, key: str, basis: str) -> Optional[int]:
        return self._cat(f"cat_{key}_fail_{basis}")

    def prior_tests(self) -> List[PriorTest]:
        # MECHANISM M4 CLOSED at state.py:118 (`int(t.get("n_items") or 0)`):
        # an unobservable count is carried into the packets view as NULL.
        return [PriorTest(test_id=t.get("test_id"), test_date=self.test_date,
                          test_type=t.get("ttype"), outcome=t.get("outcome"),
                          mileage=t.get("miles"), n_items=t.get("n_items"),
                          defects=t.get("defects"),
                          items_observability=t.get("items_obs"))
                for t in self.tests]


@dataclass
class AsOfState:
    """Running per-vehicle aggregates over STRICTLY EARLIER calendar days."""

    vehicle_id: int
    fail_basis: str = "final"

    # depth / dates
    n_days: int = 0
    n_tests: int = 0
    n_definitive_tests: int = 0
    n_initials: int = 0
    n_final_fails: int = 0
    n_initial_fails: int = 0
    first_date: Optional[date] = None
    last_date: Optional[date] = None
    prev_date: Optional[date] = None
    years_seen: Set[int] = field(default_factory=set)
    n_gaps: int = 0
    sum_gap_days: int = 0
    max_gap_days: Optional[int] = None

    # day-outcome ladder
    n_fail_days: int = 0
    n_pass_days: int = 0
    #: cluster outcome == AMBIGUOUS, cycles.py semantics: BOTH the same-stratum
    #: FAIL + definitive-pass case AND a day of mixed non-definitive outcomes.
    n_nondefinitive_days: int = 0
    #: the STRICT subset: AMBIGUOUS *and* the day carries a definitive outcome.
    n_ambiguous_days: int = 0
    n_nonresult_only_days: int = 0
    last_fail_date: Optional[date] = None
    last_pass_date: Optional[date] = None

    # same-day multiset
    n_multi_test_days: int = 0
    max_tests_on_a_day: int = 0
    n_days_pass_and_fail: int = 0
    n_mileage_conflict_days: int = 0

    # snapshot of the most recent prior day (B5 "last day" family)
    last_day_n_tests: Optional[int] = None
    last_day_n_distinct_outcomes: Optional[int] = None
    last_day_has_pass: Optional[bool] = None
    last_day_has_fail: Optional[bool] = None
    last_day_has_prs: Optional[bool] = None
    last_day_has_nonresult: Optional[bool] = None
    last_day_n_items: Optional[int] = None
    #: None (not empty!) when the most recent prior day's items are unobservable.
    last_day_categories: Optional[Set[str]] = field(default_factory=set)

    # mileage
    last_valid_mileage: Optional[int] = None
    last_valid_mileage_date: Optional[date] = None
    prev_valid_mileage: Optional[int] = None
    prev_valid_mileage_date: Optional[date] = None
    n_mileage_days: int = 0

    # item observability (PREREG_CUBE_v2 §4). Every item accumulator below is
    # a sum over the n_days_items_observed days ONLY; these four counters are
    # the denominators that make that honest and reconstructable downstream.
    n_days_items_observed: int = 0
    n_days_items_unobserved: int = 0
    n_days_items_unavailable: int = 0
    n_days_items_expected_missing: int = 0
    n_days_items_with_defects: int = 0
    n_days_items_zero_defects: int = 0

    # items
    n_items_total: int = 0
    n_advisory_items: int = 0
    n_fail_items_initial: int = 0
    n_fail_items_final: int = 0
    n_prs_items: int = 0
    n_catalogue_miss: int = 0
    n_prs_item_days: int = 0
    last_prs_item_date: Optional[date] = None

    # severity (post-2018 observable window)
    n_severity_observable_days: int = 0
    n_dangerous_items: int = 0
    n_major_items: int = 0
    n_minor_items: int = 0
    n_dangerous_days: int = 0
    n_major_days: int = 0
    n_minor_days: int = 0
    last_dangerous_date: Optional[date] = None
    last_major_date: Optional[date] = None
    last_minor_date: Optional[date] = None

    # positional (research-only); None while the location map is absent
    pos_counts: Dict[str, Optional[int]] = field(default_factory=dict)
    pos_observed: bool = False

    # per-category ladders
    cat_days: Dict[str, int] = field(default_factory=dict)
    cat_last_date: Dict[str, Optional[date]] = field(default_factory=dict)
    cat_run: Dict[str, int] = field(default_factory=dict)
    cat_max_run: Dict[str, int] = field(default_factory=dict)
    cat_ever_advisory: Dict[str, bool] = field(default_factory=dict)
    cat_adv_to_fail: Dict[str, int] = field(default_factory=dict)
    cat_repair_state: Dict[str, int] = field(default_factory=dict)  # 0 none,1 failed,2 repaired
    cat_recurrence: Dict[str, int] = field(default_factory=dict)
    last_adv_to_fail_date: Optional[date] = None
    last_recurrence_date: Optional[date] = None

    # burden window + slope accumulators
    burden: Deque[int] = field(default_factory=lambda: deque(maxlen=3))
    slope_n: int = 0
    slope_sx: float = 0.0
    slope_sy: float = 0.0
    slope_sxx: float = 0.0
    slope_sxy: float = 0.0

    # packets
    prior_tests: List[PriorTest] = field(default_factory=list)
    first_use_date: Optional[date] = None

    # --- trailing-window accumulators (depth caps) ------------------------
    #: ascending prior test-day dates; bounded (~50 in the measured population)
    day_dates: List[date] = field(default_factory=list)
    #: prefix sums of n_initial_day, aligned with day_dates (index 0 == 0)
    initial_prefix: List[int] = field(default_factory=lambda: [0])
    #: ascending dates of prior days carrying each category
    cat_day_dates: Dict[str, List[date]] = field(default_factory=dict)

    def __post_init__(self):
        if self.fail_basis not in ("final", "initial"):
            raise ValueError(
                f"fail_basis {self.fail_basis!r} is not 'final' or 'initial'. "
                f"A typo must never fall through to a basis the owner did not "
                f"choose: the [P4] ruling is P-out ('final').")
        for key in CATEGORY_KEYS:
            self.cat_day_dates.setdefault(key, [])
            self.cat_days.setdefault(key, 0)
            self.cat_last_date.setdefault(key, None)
            self.cat_run.setdefault(key, 0)
            self.cat_max_run.setdefault(key, 0)
            self.cat_ever_advisory.setdefault(key, False)
            self.cat_adv_to_fail.setdefault(key, 0)
            self.cat_repair_state.setdefault(key, 0)
            self.cat_recurrence.setdefault(key, 0)

    # --- read-only helpers used by blocks.py ------------------------------

    def days_since(self, when: Optional[date], target: date) -> Optional[int]:
        return None if when is None else (target - when).days

    @property
    def has_priors(self) -> bool:
        return self.n_days > 0

    @property
    def has_item_observations(self) -> bool:
        """True when >=1 prior day carried observable defect detail.

        The gate on every item-derived emitted column. It is deliberately NOT
        `has_priors`: a vehicle can have a fully observed TEST history and no
        observable ITEM history at all.
        """
        return self.n_days_items_observed > 0

    def item_observability_status(self) -> str:
        """no_priors / none / partial / full across this vehicle's prior days."""
        return obs.history_status(self.n_days_items_observed,
                                  self.n_days_items_unobserved,
                                  n_prior_days=self.n_days)

    def mean_gap_days(self) -> Optional[float]:
        return (self.sum_gap_days / self.n_gaps) if self.n_gaps else None

    def burden_deltas(self) -> List[Optional[int]]:
        """n_items_day differences across the last 3 observed days."""
        window = list(self.burden)
        d1 = window[-1] - window[-2] if len(window) >= 2 else None
        d2 = window[-2] - window[-3] if len(window) >= 3 else None
        return [d1, d2]

    # --- trailing-window readers (depth caps) ------------------------------
    # All O(log n) over the bounded ascending date lists: the caps are computed
    # inside the SINGLE scan, never by a second pass.

    @staticmethod
    def _window_start(dates: List[date], target: date, cap_years: float) -> int:
        """Index of the first prior day strictly inside the trailing window."""
        return bisect_right(dates, target - timedelta(days=cap_days(cap_years)))

    def n_days_within(self, target: date, cap_years: float) -> int:
        return len(self.day_dates) - self._window_start(self.day_dates, target, cap_years)

    def n_initials_within(self, target: date, cap_years: float) -> int:
        start = self._window_start(self.day_dates, target, cap_years)
        return self.initial_prefix[-1] - self.initial_prefix[start]

    def history_years_within(self, target: date, cap_years: float) -> Optional[float]:
        """Observed span INSIDE the window: (target - earliest in-window prior).

        Deliberately not min(history_years, cap): a vehicle whose only priors
        are at 1y and 3y has a 2y-capped span of 1.0, not 2.0.
        """
        start = self._window_start(self.day_dates, target, cap_years)
        if start >= len(self.day_dates):
            return None
        return (target - self.day_dates[start]).days / DAYS_PER_YEAR

    def cat_n_days_within(self, key: str, target: date, cap_years: float) -> int:
        dates = self.cat_day_dates[key]
        return len(dates) - self._window_start(dates, target, cap_years)

    def deterioration_slope(self) -> Optional[float]:
        """Least-squares slope of items-per-day against years (order-free)."""
        if self.slope_n < 2:
            return None
        n = float(self.slope_n)
        denom = n * self.slope_sxx - self.slope_sx * self.slope_sx
        if abs(denom) < 1e-12:
            return None
        return (n * self.slope_sxy - self.slope_sx * self.slope_sy) / denom

    # --- the single mutation ---------------------------------------------

    def update(self, day: DayAtom) -> None:
        """Fold one calendar day in. Called ONLY after that day's events emit."""
        outcome = day.cluster_outcome
        prior_last = self.last_date

        if self.first_date is None:
            self.first_date = day.test_date
        if prior_last is not None:
            gap = (day.test_date - prior_last).days
            self.n_gaps += 1
            self.sum_gap_days += gap
            self.max_gap_days = gap if self.max_gap_days is None else max(self.max_gap_days, gap)
        self.prev_date = prior_last
        self.last_date = day.test_date
        self.years_seen.add(day.test_date.year)

        self.n_days += 1
        self.day_dates.append(day.test_date)
        self.initial_prefix.append(self.initial_prefix[-1] + day.n_initial_day)
        self.n_tests += day.n_tests_day
        self.n_definitive_tests += day.n_definitive_day
        self.n_initials += day.n_initial_day
        if day.first_use_date is not None and self.first_use_date is None:
            self.first_use_date = day.first_use_date

        # record-grain label counts over initial tests
        for test in day.tests:
            if test.get("ttype") == "NT" and test.get("outcome") in DEFINITIVE:
                if test.get("outcome") == "FAIL":
                    self.n_final_fails += 1
                    self.n_initial_fails += 1
                elif test.get("outcome") == "PRS":
                    self.n_initial_fails += 1

        # day-outcome ladder
        if outcome == "FAIL":
            self.n_fail_days += 1
            self.last_fail_date = day.test_date
        elif outcome in ("PASS", "PRS"):
            self.n_pass_days += 1
            self.last_pass_date = day.test_date
        elif outcome == AMBIGUOUS:
            self.n_nondefinitive_days += 1
            # strict variant: same-stratum FAIL + definitive pass. Free here --
            # both terms are already computed.
            if day.is_definitive:
                self.n_ambiguous_days += 1
        if not day.is_definitive:
            self.n_nonresult_only_days += 1

        # same-day multiset
        if day.n_tests_day > 1:
            self.n_multi_test_days += 1
        self.max_tests_on_a_day = max(self.max_tests_on_a_day, day.n_tests_day)
        if day.has_pass and day.has_fail:
            self.n_days_pass_and_fail += 1
        if day.mileage_conflict:
            self.n_mileage_conflict_days += 1

        # mileage (last-trusted basis: the most recent day with a valid reading)
        if day.n_valid_mileage_day:
            self.prev_valid_mileage = self.last_valid_mileage
            self.prev_valid_mileage_date = self.last_valid_mileage_date
            self.last_valid_mileage = day.max_valid_mileage_day
            self.last_valid_mileage_date = day.test_date
            self.n_mileage_days += 1

        # --- ITEM-DERIVED STATE: observable days ONLY --------------------
        # A day whose defect detail is not observable contributes NOTHING here.
        # It used to contribute a confident zero to every accumulator below,
        # which is the item-unavailable/no-defect conflation (PREREG §4).
        # Nothing outside this branch reads an item column, so B1 and B5 stay
        # exactly as they were: a vehicle's TEST history is fully observed even
        # when its ITEM history is not.
        if day.items_observed:
            self.n_days_items_observed += 1
            if day.n_tests_items_with_defects:
                self.n_days_items_with_defects += 1
            else:
                self.n_days_items_zero_defects += 1
            if day.n_tests_items_unobserved:
                # a partially-dark day: counted as observed (its observed tests
                # are real) AND as carrying unobserved tests.
                self.n_days_items_unobserved += 1
        else:
            self.n_days_items_unobserved += 1
            if day.n_tests_items_expected_missing:
                self.n_days_items_expected_missing += 1
            elif day.n_tests_items_unavailable:
                self.n_days_items_unavailable += 1

        if day.items_observed:
            # item totals
            self.n_items_total += int(day.item("n_items"))
            self.n_advisory_items += int(day.item("n_advisory_items"))
            self.n_fail_items_initial += int(day.item("n_fail_items_initial"))
            self.n_fail_items_final += int(day.item("n_fail_items_final"))
            self.n_prs_items += int(day.item("n_prs_items"))
            self.n_catalogue_miss += int(day.item("n_catalogue_miss"))
            if int(day.item("n_prs_items")) > 0:
                self.n_prs_item_days += 1
                self.last_prs_item_date = day.test_date

            # severity: post-2018 AND item-observable. `severity_observable` is
            # a DATE predicate on its own, so a post-2018 day with dark items
            # used to inflate the denominator b3 documents as honest.
            if day.severity_observable:
                self.n_severity_observable_days += 1
                n_d, n_ma, n_mi = (int(day.item("n_dangerous")), int(day.item("n_major")),
                                   int(day.item("n_minor")))
                self.n_dangerous_items += n_d
                self.n_major_items += n_ma
                self.n_minor_items += n_mi
                if n_d:
                    self.n_dangerous_days += 1
                    self.last_dangerous_date = day.test_date
                if n_ma:
                    self.n_major_days += 1
                    self.last_major_date = day.test_date
                if n_mi:
                    self.n_minor_days += 1
                    self.last_minor_date = day.test_date

            # positional (research-only)
            for name, value in day.items.items():
                if not name.startswith("pos_"):
                    continue
                if value is None:
                    continue
                self.pos_observed = True
                self.pos_counts[name] = int(self.pos_counts.get(name) or 0) + int(value)

            # per-category ladders
            fail_suffix = "final" if self.fail_basis == "final" else "initial"
            for key in CATEGORY_KEYS:
                present = (day.cat_n(key) or 0) > 0
                has_adv = (day.cat_adv(key) or 0) > 0
                has_fail = (day.cat_fail(key, fail_suffix) or 0) > 0

                # advisory -> fail transition uses the PRE-update advisory memory
                if has_fail and self.cat_ever_advisory[key]:
                    self.cat_adv_to_fail[key] += 1
                    self.last_adv_to_fail_date = day.test_date

                # recurrence-after-repair: fail ... pass ... fail again
                if has_fail and self.cat_repair_state[key] == 2:
                    self.cat_recurrence[key] += 1
                    self.last_recurrence_date = day.test_date
                if has_fail:
                    self.cat_repair_state[key] = 1
                elif self.cat_repair_state[key] == 1 and outcome in ("PASS", "PRS"):
                    self.cat_repair_state[key] = 2

                if has_adv:
                    self.cat_ever_advisory[key] = True
                if present:
                    self.cat_days[key] += 1
                    self.cat_day_dates[key].append(day.test_date)
                    self.cat_last_date[key] = day.test_date
                    self.cat_run[key] += 1
                    self.cat_max_run[key] = max(self.cat_max_run[key], self.cat_run[key])
                else:
                    self.cat_run[key] = 0
        # A dark day FREEZES the per-category ladders rather than resetting a
        # run to 0 or marking a category repaired: "the category was absent" and
        # "we cannot see whether the category was present" are different claims,
        # and only the first may break a run. Conservative by construction --
        # it can under-count recurrence, never invent it.

        # snapshot the day we just folded in: it is "the last prior day" for
        # every later target (never for a target on this same day -- emit ran first)
        self.last_day_n_tests = day.n_tests_day
        self.last_day_n_distinct_outcomes = day.n_distinct_outcomes
        self.last_day_has_pass = day.has_pass
        self.last_day_has_fail = day.has_fail
        self.last_day_has_prs = day.has_prs
        self.last_day_has_nonresult = day.has_nonresult
        if day.items_observed:
            self.last_day_n_items = int(day.item("n_items"))
            self.last_day_categories = {k for k in CATEGORY_KEYS
                                        if (day.cat_n(k) or 0) > 0}
        else:
            # NULL, not an empty set: an unobservable last day has an UNKNOWN
            # category set, and len(set()) == 0 would assert "no defects".
            self.last_day_n_items = None
            self.last_day_categories = None

        # burden window + deterioration slope: observable days only, so the
        # slope's "honest denominator" (slope_n) is honest for the first time.
        if day.items_observed:
            n_items_day = int(day.item("n_items"))
            self.burden.append(n_items_day)
            x = (day.test_date - self.first_date).days / DAYS_PER_YEAR
            self.slope_n += 1
            self.slope_sx += x
            self.slope_sy += n_items_day
            self.slope_sxx += x * x
            self.slope_sxy += x * n_items_day

        # packets payload
        self.prior_tests.extend(day.prior_tests())


def covid_straddle(prior_date: Optional[date], target_date: date) -> bool:
    """True when [prior_date, target_date] intersects the MOT-extension window."""
    if prior_date is None:
        return False
    return not (target_date < COVID_EXT_START or prior_date > COVID_EXT_END)


def in_annual_band(days: Optional[int]) -> Optional[bool]:
    if days is None:
        return None
    return ANNUAL_BAND[0] <= days <= ANNUAL_BAND[1]


def day_atom_from_row(row: dict, item_columns: Sequence[str]) -> DayAtom:
    """Build a DayAtom from one staged vehicle_day row (duckdb dict)."""
    items = {name: row.get(name) for name in item_columns}
    tests = row.get("tests") or []

    def count(name: str) -> int:
        """A missing observability column is a CONTRACT BREAK, not a zero."""
        if name not in row:
            raise KeyError(
                f"staged vehicle_day row has no {name!r}. The item-observability "
                f"columns are mandatory (PREREG_CUBE_v2 §4): without them the "
                f"scan cannot tell 'no defect items' from 'defect detail "
                f"unavailable' and would silently re-create INC-2026-08-13. "
                f"Re-stage with the current atoms.vehicle_day_atom_sql.")
        return int(row.get(name) or 0)

    return DayAtom(
        n_tests_items_observed=count("n_tests_items_observed"),
        n_tests_items_unobserved=count("n_tests_items_unobserved"),
        n_tests_items_with_defects=count("n_tests_items_with_defects"),
        n_tests_items_zero_defects=count("n_tests_items_zero_defects"),
        n_tests_items_unavailable=count("n_tests_items_unavailable"),
        n_tests_items_expected_missing=count("n_tests_items_expected_missing"),
        vehicle_id=int(row["vehicle_id"]),
        test_date=row["test_date"],
        n_tests_day=int(row["n_tests_day"]),
        n_initial_day=int(row["n_initial_day"]),
        n_definitive_day=int(row["n_definitive_day"]),
        n_nonresult_day=int(row["n_nonresult_day"]),
        has_pass=bool(row["has_pass"]),
        has_fail=bool(row["has_fail"]),
        has_prs=bool(row["has_prs"]),
        has_nonresult=bool(row["has_nonresult"]),
        n_distinct_outcomes=int(row["n_distinct_outcomes"]),
        max_valid_mileage_day=row["max_valid_mileage_day"],
        min_valid_mileage_day=row["min_valid_mileage_day"],
        n_valid_mileage_day=int(row["n_valid_mileage_day"] or 0),
        mileage_conflict=bool(row["mileage_conflict"]) if row["mileage_conflict"] is not None else False,
        severity_observable=bool(row["severity_observable"]),
        first_use_date=row.get("first_use_date"),
        items=items,
        tests=list(tests),
    )
