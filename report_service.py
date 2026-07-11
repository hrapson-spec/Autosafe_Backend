"""
AutoSafe v2 report API business core.

Two responsibilities live here:

1. Mileage provenance resolution (``resolve_mileage``): decide which
   mileage figure to show for a vehicle, and be honest about where it
   came from (user entry, an observed MOT odometer reading, an age-based
   estimate, or nothing at all).
2. Evidence-ladder orchestration (``build_assessment``): turn a vehicle
   identity + MOT history into an ``Assessment`` by walking the
   Postgres-then-SQLite evidence ladder (exact band -> age-band-only ->
   model average -> population default -> unavailable).

No fabricated evidence, ever
-----------------------------
This is the core product invariant of the v2 release. A missing value
stays ``None``. Component risks and repair estimates are populated only
when real per-component data backs them. Every degradation step is
explicit in ``ReportEvidence.match_scope``, ``PredictionSource``, and the
returned ``note`` -- never silently hidden behind a narrower-looking
scope. See report_contract.py's module docstring for the full contract.
"""
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

import database as db
import repair_costs
from confidence import classify_confidence
from report_contract import (
    ComponentRiskItem,
    ConfidenceLevel,
    MatchScope,
    MileageSource,
    POPULATION_DEFAULT_FAILURE_RISK,
    PredictionSource,
    ReportComponents,
    ReportEvidence,
    ReportMileage,
    ReportMot,
    ReportRepairEstimate,
    ReportRisk,
    ReportVehicle,
)
from utils import get_age_band, get_mileage_band

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MILES_PER_KM = 0.621371

NOTE_AGE_BAND_ONLY = (
    "Exact mileage band unavailable for this vehicle's age group — showing "
    "the closest match for vehicles of a similar age."
)
NOTE_MODEL_AVERAGE = (
    "Exact age and mileage match not found — showing the overall average "
    "for this make and model."
)
NOTE_POPULATION_DEFAULT = (
    "No data available for this make and model — showing the dataset-wide "
    "reference across the recorded tests."
)
NOTE_UNAVAILABLE = (
    "Vehicle identity confirmed, but our comparison data is temporarily "
    "unavailable — the figure shown is the checked-in dataset reference, "
    "not a result for this make and model."
)

_NOTE_BY_SCOPE = {
    MatchScope.EXACT_BAND: None,
    MatchScope.AGE_BAND_ONLY: NOTE_AGE_BAND_ONLY,
    MatchScope.MODEL_AVERAGE: NOTE_MODEL_AVERAGE,
}

# Component key / display label / ladder-dict field name, in the fixed
# display order used throughout the v2 report.
_COMPONENT_FIELDS = [
    ("brakes", "Brakes", "risk_brakes"),
    ("suspension", "Suspension", "risk_suspension"),
    ("tyres", "Tyres", "risk_tyres"),
    ("steering", "Steering", "risk_steering"),
    ("visibility", "Visibility", "risk_visibility"),
    ("lamps", "Lamps & Electrical", "risk_lamps"),
    ("body", "Body & Chassis", "risk_body"),
]

# repair_costs.calculate_expected_repair_cost() keys, mapped from the same
# normalized ladder-dict fields.
_REPAIR_COST_KEY_MAP = [
    ("Risk_Brakes", "risk_brakes"),
    ("Risk_Suspension", "risk_suspension"),
    ("Risk_Tyres", "risk_tyres"),
    ("Risk_Steering", "risk_steering"),
    ("Risk_Visibility", "risk_visibility"),
    ("Risk_Lamps_Reflectors_And_Electrical_Equipment", "risk_lamps"),
    ("Risk_Body_Chassis_Structure", "risk_body"),
]


@dataclass
class Assessment:
    """Internal precursor to ReportResponse: everything build_assessment
    resolves, before the route layer attaches registration/persistence/
    share-link fields."""

    vehicle: ReportVehicle
    mot: ReportMot
    mileage: ReportMileage
    evidence: ReportEvidence
    risk: ReportRisk
    components: ReportComponents
    repair_estimate: Optional[ReportRepairEstimate]
    prediction_source: PredictionSource
    note: Optional[str]


# ---------------------------------------------------------------------------
# Mileage resolution
# ---------------------------------------------------------------------------

def _test_miles(test: Any) -> float:
    """A single MOT test's odometer reading, converted to miles."""
    if test.odometer_unit == 'km':
        return test.odometer_value * MILES_PER_KM
    return float(test.odometer_value)


def resolve_mileage(
    mileage_user: Optional[int],
    history: Any,
    vehicle_year: Optional[int],
    now: Optional[datetime] = None,
) -> ReportMileage:
    """Resolve the mileage figure to display, with honest provenance.

    Priority order:

    1. ``mileage_user`` (an explicit user entry) -> ``user_entered``.
       Used exactly as given; history is not consulted at all.
    2. The latest MOT test that has a recorded odometer reading ->
       ``observed_mot``. km readings are converted to miles (rounded)
       with ``unit_converted=True``. The anomaly check mirrors
       ``main.py._get_display_mileage`` exactly: compare the chosen
       reading against the immediately-preceding test's reading (both
       converted to miles first, so a mixed-unit pair compares fairly).
       Quoting the legacy logic being mirrored::

           if days_diff > 0:
               if mileage_diff < 0:
                   return prev_mileage, True  # negative mileage anomaly
               if mileage_diff > 0:
                   annualized = (mileage_diff / days_diff) * 365
                   if annualized > 50000:  # physically implausible
                       return prev_mileage, True

       i.e. a negative delta, or an annualised rate over 50,000
       miles/year, is judged implausible; the *preceding* reading is
       shown instead, with ``anomaly=True`` and ``observed_at`` set to
       that preceding test's date (the date the displayed figure was
       actually recorded on -- never fabricate a date pairing).
       Generalisation beyond the legacy function: legacy only ever looks
       at tests[0]/tests[1] and gives up (no mileage) if tests[0] lacks a
       reading. Here we scan forward from the latest test for the first
       one that *has* a reading, then compare it against the very next
       test chronologically (mirroring legacy's adjacency rule, not
       re-scanning further for the "previous" side) -- this uses real
       observed evidence when it exists rather than falling through to
       an estimate, per the no-fabricated-evidence invariant.
    3. ``vehicle_year`` known -> ``estimated`` at 8,000 miles per year of
       age (floored at 0; age 0 legitimately estimates to 0 miles for a
       brand-new car).
    4. Otherwise -> ``missing`` (``effective_value=None``).

    ``now`` is exposed purely so tests can pin "today" deterministically;
    production callers should leave it as ``None``.
    """
    now = now or datetime.now()

    if mileage_user is not None:
        return ReportMileage(
            effective_value=mileage_user,
            source=MileageSource.USER_ENTERED,
            observed_at=None,
            unit_converted=False,
            anomaly=False,
        )

    tests = list(getattr(history, 'mot_tests', None) or []) if history is not None else []

    latest_idx = None
    for i, t in enumerate(tests):
        if t.odometer_value is not None:
            latest_idx = i
            break

    if latest_idx is not None:
        latest_test = tests[latest_idx]
        latest_miles = _test_miles(latest_test)

        chosen_test = latest_test
        chosen_miles = latest_miles
        anomaly = False

        prev_idx = latest_idx + 1
        if prev_idx < len(tests) and tests[prev_idx].odometer_value is not None:
            prev_test = tests[prev_idx]
            if latest_test.test_date is not None and prev_test.test_date is not None:
                days_diff = (latest_test.test_date - prev_test.test_date).days
                prev_miles = _test_miles(prev_test)
                mileage_diff = latest_miles - prev_miles

                if days_diff > 0:
                    if mileage_diff < 0:
                        chosen_test, chosen_miles, anomaly = prev_test, prev_miles, True
                    elif mileage_diff > 0:
                        annualized = (mileage_diff / days_diff) * 365
                        if annualized > 50000:
                            chosen_test, chosen_miles, anomaly = prev_test, prev_miles, True

        observed_at = chosen_test.test_date.isoformat() if chosen_test.test_date else None
        return ReportMileage(
            effective_value=round(chosen_miles),
            source=MileageSource.OBSERVED_MOT,
            observed_at=observed_at,
            unit_converted=(chosen_test.odometer_unit == 'km'),
            anomaly=anomaly,
        )

    if vehicle_year is not None:
        age = max(now.year - vehicle_year, 0)
        return ReportMileage(
            effective_value=age * 8000,
            source=MileageSource.ESTIMATED,
            observed_at=None,
            unit_converted=False,
            anomaly=False,
        )

    return ReportMileage(
        effective_value=None,
        source=MileageSource.MISSING,
        observed_at=None,
        unit_converted=False,
        anomaly=False,
    )


# ---------------------------------------------------------------------------
# SQLite evidence ladder (fallback when Postgres is unavailable)
#
# Column names verified against the real production schema: build_db.py
# builds the SQLite `risks` table directly from prod_data_clean.csv.gz's
# header row, which is lowercase for the first three columns
# (model_id, age_band, mileage_band) and Title_Case for the rest
# (Total_Tests, Total_Failures, Failure_Risk, Risk_*). This does NOT match
# a naive "everything Title_Case" assumption -- see report_test_helpers.py
# for the same verified schema used to seed test fixtures.
# ---------------------------------------------------------------------------

_SQLITE_WEIGHTED_SELECT = """
    SELECT
        SUM(Total_Tests) AS total_tests,
        SUM(Total_Failures) AS total_failures,
        SUM(Failure_Risk * Total_Tests) * 1.0 / NULLIF(SUM(Total_Tests), 0) AS failure_risk,
        CASE WHEN COUNT(Risk_Brakes) = COUNT(*)
             THEN SUM(Risk_Brakes * Total_Tests) * 1.0 / NULLIF(SUM(Total_Tests), 0) END AS risk_brakes,
        CASE WHEN COUNT(Risk_Suspension) = COUNT(*)
             THEN SUM(Risk_Suspension * Total_Tests) * 1.0 / NULLIF(SUM(Total_Tests), 0) END AS risk_suspension,
        CASE WHEN COUNT(Risk_Tyres) = COUNT(*)
             THEN SUM(Risk_Tyres * Total_Tests) * 1.0 / NULLIF(SUM(Total_Tests), 0) END AS risk_tyres,
        CASE WHEN COUNT(Risk_Steering) = COUNT(*)
             THEN SUM(Risk_Steering * Total_Tests) * 1.0 / NULLIF(SUM(Total_Tests), 0) END AS risk_steering,
        CASE WHEN COUNT(Risk_Visibility) = COUNT(*)
             THEN SUM(Risk_Visibility * Total_Tests) * 1.0 / NULLIF(SUM(Total_Tests), 0) END AS risk_visibility,
        CASE WHEN COUNT(Risk_Lamps_Reflectors_And_Electrical_Equipment) = COUNT(*)
             THEN SUM(Risk_Lamps_Reflectors_And_Electrical_Equipment * Total_Tests) * 1.0
                  / NULLIF(SUM(Total_Tests), 0) END AS risk_lamps,
        CASE WHEN COUNT(Risk_Body_Chassis_Structure) = COUNT(*)
             THEN SUM(Risk_Body_Chassis_Structure * Total_Tests) * 1.0
                  / NULLIF(SUM(Total_Tests), 0) END AS risk_body
    FROM risks
    WHERE (model_id = ? OR model_id LIKE ? || ' %')
"""

_SQLITE_STEP1_EXACT_SQL = _SQLITE_WEIGHTED_SELECT + " AND age_band = ? AND mileage_band = ?"
_SQLITE_STEP2_AGE_ONLY_SQL = _SQLITE_WEIGHTED_SELECT + " AND age_band = ?"
_SQLITE_STEP3_MODEL_AVERAGE_SQL = _SQLITE_WEIGHTED_SELECT

_AGG_RISK_COLUMNS = [
    'risk_brakes', 'risk_suspension', 'risk_tyres', 'risk_steering',
    'risk_visibility', 'risk_lamps', 'risk_body',
]


def _clamp_risk(value: Optional[float]) -> Optional[float]:
    """Clamp a risk value into the valid [0, 1] probability range.

    Defense-in-depth against floating-point drift in the weighted-average
    SQL (SUM(x * total_tests) / SUM(total_tests) can push a value
    fractionally outside [0, 1] for inputs near the boundary), which would
    otherwise raise a pydantic ValidationError on ReportRisk /
    ComponentRiskItem instead of degrading honestly. Mirrors the
    philosophy of database.py's own _clamp_risk (applied there to the
    Postgres ladder); duplicated here in a few lines rather than importing
    that module-private helper across a module boundary.
    """
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


def _normalize_agg_row(row: sqlite3.Row, match_scope: str) -> Dict[str, Any]:
    """Normalize a weighted SQLite rung to the Postgres ladder shape."""
    components_available = all(row[c] is not None for c in _AGG_RISK_COLUMNS)
    return {
        'match_scope': match_scope,
        'total_tests': int(row['total_tests']),
        'total_failures': int(row['total_failures']) if row['total_failures'] is not None else None,
        'failure_risk': _clamp_risk(row['failure_risk']),
        'risk_brakes': _clamp_risk(row['risk_brakes']),
        'risk_suspension': _clamp_risk(row['risk_suspension']),
        'risk_tyres': _clamp_risk(row['risk_tyres']),
        'risk_steering': _clamp_risk(row['risk_steering']),
        'risk_visibility': _clamp_risk(row['risk_visibility']),
        'risk_lamps': _clamp_risk(row['risk_lamps']),
        'risk_body': _clamp_risk(row['risk_body']),
        'components_available': components_available,
    }


def _sqlite_ladder(
    conn: sqlite3.Connection,
    model_id: str,
    age_band: str,
    mileage_band: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Run the 3-step SQLite fallback ladder against an open connection.

    Returns the normalized ladder-result dict for whichever step first
    finds data, or None if the model has no rows anywhere in the table
    (caller maps that to population_default -- this is a "successfully
    queried, no data" outcome, not an availability failure).

    Skip/always-attempt semantics deliberately mirror the verified
    Postgres sibling (database.get_risk_v2_banded /
    _fetch_mot_risk_aggregate) exactly, so the two ladders agree on which
    rung answers a given (model_id, age_band, mileage_band):
      - step 1 (exact) is skipped based on mileage_band alone
        (``mileage_band is None or mileage_band == 'Unknown'``) --
        age_band's validity is irrelevant to that skip decision.
      - step 2 (age-only) is always attempted next; a literal age_band of
        'Unknown' simply matches no real row and falls through to step 3
        naturally, rather than being special-cased.
    """
    conn.row_factory = sqlite3.Row

    def usable_aggregate(candidate: Optional[sqlite3.Row]) -> bool:
        return (
            candidate is not None
            and candidate['total_tests'] is not None
            and int(candidate['total_tests']) > 0
            and candidate['failure_risk'] is not None
        )

    skip_exact = mileage_band is None or mileage_band == 'Unknown'
    if not skip_exact:
        row = conn.execute(
            _SQLITE_STEP1_EXACT_SQL, (model_id, model_id, age_band, mileage_band)
        ).fetchone()
        if usable_aggregate(row):
            return _normalize_agg_row(row, 'exact_band')

    row = conn.execute(
        _SQLITE_STEP2_AGE_ONLY_SQL, (model_id, model_id, age_band)
    ).fetchone()
    if usable_aggregate(row):
        return _normalize_agg_row(row, 'age_band_only')

    row = conn.execute(_SQLITE_STEP3_MODEL_AVERAGE_SQL, (model_id, model_id)).fetchone()
    if usable_aggregate(row):
        return _normalize_agg_row(row, 'model_average')

    return None


async def _query_evidence_ladder(
    model_id: str,
    age_band: str,
    mileage_band: Optional[str],
    sqlite_conn_factory: Optional[Callable[[], Any]],
) -> Tuple[Optional[Dict[str, Any]], PredictionSource]:
    """Postgres-then-SQLite evidence ladder.

    Returns (normalized_result_or_None, prediction_source).

    A None result paired with prediction_source POSTGRES or SQLITE means
    that store was reached successfully and legitimately has no data for
    this model (-> population_default). prediction_source UNAVAILABLE
    means no store could be reached at all (-> unavailable). These two
    outcomes must never be conflated -- that distinction is the whole
    point of MatchScope.POPULATION_DEFAULT vs MatchScope.UNAVAILABLE.
    """
    try:
        result = await db.get_risk_v2_banded(model_id, age_band, mileage_band)
        return result, PredictionSource.POSTGRES
    except db.PostgresUnavailable:
        logger.warning("Postgres unavailable for model_id=%s; falling back to SQLite", model_id)

    if sqlite_conn_factory is None:
        return None, PredictionSource.UNAVAILABLE

    try:
        with sqlite_conn_factory() as conn:
            if conn is None:
                return None, PredictionSource.UNAVAILABLE
            result = _sqlite_ladder(conn, model_id, age_band, mileage_band)
            return result, PredictionSource.SQLITE
    except sqlite3.Error as exc:
        logger.warning(
            "SQLite ladder query failed for model_id=%s (%s)",
            model_id,
            type(exc).__name__,
        )
        return None, PredictionSource.UNAVAILABLE


# ---------------------------------------------------------------------------
# Ladder-result -> contract fields
# ---------------------------------------------------------------------------

def _normalize_confidence(total_tests: Optional[int]) -> ConfidenceLevel:
    return ConfidenceLevel(classify_confidence(total_tests if total_tests is not None else 0))


def _degraded_risk_and_components() -> Tuple[ReportRisk, ReportComponents]:
    """The shared degraded shape for population_default and unavailable:
    checked-in dataset-wide reference rate, Very Low confidence, no
    components. Never fabricate a component breakdown or repair estimate
    when there is no per-vehicle evidence behind them."""
    risk = ReportRisk(failure_risk=POPULATION_DEFAULT_FAILURE_RISK, confidence=ConfidenceLevel.VERY_LOW)
    components = ReportComponents(available=False, items=None)
    return risk, components


def _build_components_and_repair(
    ladder_result: Dict[str, Any],
) -> Tuple[ReportComponents, Optional[ReportRepairEstimate]]:
    """Component breakdown + repair estimate from a found ladder result.

    Only populated when components_available is True (all 7 per-component
    risks are non-null) -- otherwise both are honestly absent, even though
    failure_risk itself may still be known.
    """
    if not ladder_result['components_available']:
        return ReportComponents(available=False, items=None), None

    items = [
        ComponentRiskItem(key=key, label=label, risk=ladder_result[field])
        for key, label, field in _COMPONENT_FIELDS
    ]
    components = ReportComponents(available=True, items=items)

    # Thin mapping onto repair_costs.calculate_expected_repair_cost's
    # Risk_*/Failure_Risk input shape -- reuses the same formula the
    # legacy /api/risk endpoint uses (add_repair_cost_estimate in
    # main.py), no re-implementation of the cost logic here.
    risk_data = {'Failure_Risk': ladder_result['failure_risk']}
    for api_key, field in _REPAIR_COST_KEY_MAP:
        risk_data[api_key] = ladder_result[field]

    cost = repair_costs.calculate_expected_repair_cost(risk_data)
    repair_estimate = None
    if cost is not None:
        repair_estimate = ReportRepairEstimate(
            expected=cost['cost_mid'],
            range_low=cost['cost_min'],
            range_high=cost['cost_max'],
        )
    return components, repair_estimate


def _build_mot(history: Any) -> ReportMot:
    """Latest known MOT status. None-safe when history is None or has no
    tests -- mirrors main.py's `last_test = history.mot_tests[0] if
    history.mot_tests else None` pattern."""
    tests = getattr(history, 'mot_tests', None) if history is not None else None
    latest = tests[0] if tests else None
    if latest is None:
        return ReportMot(expiry_date=None, last_test_date=None, last_result=None)
    return ReportMot(
        expiry_date=latest.expiry_date.isoformat() if latest.expiry_date else None,
        last_test_date=latest.test_date.isoformat() if latest.test_date else None,
        last_result=latest.test_result,
    )


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

async def build_assessment(
    identity: Dict[str, Any],
    history: Any,
    mileage_user: Optional[int],
    sqlite_conn_factory: Optional[Callable[[], Any]] = None,
    now: Optional[datetime] = None,
) -> Assessment:
    """Resolve a full Assessment from a vehicle identity + MOT history.

    identity: {'registration', 'make', 'model', 'year', 'fuel_type',
    'colour'} (year Optional; registration is not consumed here -- it is
    attached to the response at the route layer).
    """
    now = now or datetime.now()

    make_raw = identity.get('make') or ''
    model_raw = identity.get('model') or ''
    year = identity.get('year')

    vehicle = ReportVehicle(
        make=make_raw,
        model=model_raw,
        year=year,
        fuel_type=identity.get('fuel_type'),
        colour=identity.get('colour'),
    )

    mot = _build_mot(history)
    mileage = resolve_mileage(mileage_user, history, year, now=now)

    # Mirror main.py's model_id normalization exactly (get_risk / _fallback_prediction):
    # model_id = f"{make.strip().upper()} {model.strip().upper()}"
    model_id = "{} {}".format(make_raw.strip().upper(), model_raw.strip().upper())

    vehicle_age = (now.year - year) if year is not None else None
    age_band = get_age_band(vehicle_age)
    mileage_band = get_mileage_band(mileage.effective_value) if mileage.effective_value is not None else None

    ladder_result, ladder_source = await _query_evidence_ladder(
        model_id, age_band, mileage_band, sqlite_conn_factory
    )

    if ladder_source == PredictionSource.UNAVAILABLE:
        risk, components = _degraded_risk_and_components()
        match_scope = MatchScope.UNAVAILABLE
        prediction_source = PredictionSource.DATASET_REFERENCE
        repair_estimate = None
        note = NOTE_UNAVAILABLE
        total_tests: Optional[int] = None
        total_failures: Optional[int] = None
    elif ladder_result is None:
        risk, components = _degraded_risk_and_components()
        match_scope = MatchScope.POPULATION_DEFAULT
        prediction_source = PredictionSource.DATASET_REFERENCE
        repair_estimate = None
        note = NOTE_POPULATION_DEFAULT
        total_tests = None
        total_failures = None
    else:
        match_scope = MatchScope(ladder_result['match_scope'])
        prediction_source = ladder_source
        total_tests = ladder_result['total_tests']
        total_failures = ladder_result['total_failures']
        risk = ReportRisk(
            failure_risk=ladder_result['failure_risk'],
            confidence=_normalize_confidence(total_tests),
        )
        components, repair_estimate = _build_components_and_repair(ladder_result)
        note = _NOTE_BY_SCOPE[match_scope]

    matched_age_band = age_band if match_scope in {MatchScope.EXACT_BAND, MatchScope.AGE_BAND_ONLY} else None
    matched_mileage_band = mileage_band if match_scope == MatchScope.EXACT_BAND else None

    evidence = ReportEvidence(
        match_scope=match_scope,
        age_band=matched_age_band,
        mileage_band=matched_mileage_band,
        total_tests=total_tests,
        total_failures=total_failures,
    )

    return Assessment(
        vehicle=vehicle,
        mot=mot,
        mileage=mileage,
        evidence=evidence,
        risk=risk,
        components=components,
        repair_estimate=repair_estimate,
        prediction_source=prediction_source,
        note=note,
    )
