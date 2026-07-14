"""
Private test helpers for report_service.py's test suite.

NOT a conftest.py -- these are not auto-loaded fixtures; import explicitly
from tests/test_report_service_mileage.py and tests/test_report_service_banding.py.

Two things live here:

1. ``make_history`` -- builds real dvsa_client.VehicleHistory / MOTTest
   dataclass instances from a compact reading list, so mileage-resolution
   tests exercise the actual production dataclasses rather than mocks.
2. ``seeded_sqlite`` (+ the ``SEEDED_RISKS_ROWS`` data it loads) -- an
   in-memory SQLite ``risks`` table for exercising report_service.py's
   SQLite evidence-ladder fallback. The row data is exposed as a module
   constant so tests can compute expected weighted-average results
   programmatically from the same source of truth used to seed the table,
   instead of hand-transcribing arithmetic (see test_report_service_banding.py).

Column names verified against the REAL production schema: build_db.py
builds the SQLite `risks` table directly from prod_data_clean.csv.gz's
own header row, which is::

    model_id, age_band, mileage_band, Total_Tests, Total_Failures,
    Failure_Risk, Risk_Brakes, Risk_Suspension, Risk_Tyres, Risk_Steering,
    Risk_Visibility, Risk_Lamps_Reflectors_And_Electrical_Equipment,
    Risk_Body_Chassis_Structure

i.e. the first three columns are lowercase and the rest are Title_Case --
NOT uniformly Title_Case. Matches the column names main.py's existing
SQLite fallback queries use (e.g. `WHERE model_id = ? AND age_band = ?`).
"""
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from typing import List, Optional, Tuple

# Add repo root to path (mirrors the convention already used by
# tests/test_report_contract.py, tests/test_banding.py, tests/test_dvla.py).
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dvsa_client import MOTTest, VehicleHistory  # noqa: E402


# ---------------------------------------------------------------------------
# make_history
# ---------------------------------------------------------------------------

def make_history(
    readings: List[Tuple[str, Optional[int], str]],
    make: str = 'FORD',
    model: str = 'FIESTA',
    year: Optional[int] = 2018,
    fuel_type: Optional[str] = 'PETROL',
    colour: Optional[str] = 'BLUE',
    registration: str = 'AB12CDE',
    results: Optional[List[str]] = None,
) -> VehicleHistory:
    """Build a real VehicleHistory with real MOTTest entries.

    readings: a list of (date_str_or_None 'YYYY-MM-DD', odometer_value_or_None,
    odometer_unit 'mi'|'km'|None) tuples, given in *latest-first* order.
    date_str may be None to build a test with no recorded test_date at all
    (an odometer reading present but undated) -- exercises
    report_service.resolve_odometer's "a reading with no date is not
    displayable" scan rule.

    dvsa_client.DVSAClient._parse_response does NOT sort mot_tests itself
    -- it appends them in the order the DVSA API returns them and trusts
    the API's own newest-first contract (VehicleHistory.latest_test just
    returns mot_tests[0]). This helper mirrors that: it does not re-sort
    readings either, so callers must supply them newest-first, exactly as
    real DVSA API responses are trusted to arrive.
    """
    tests = []
    for i, (date_str, odometer_value, unit) in enumerate(readings):
        test_date = datetime.strptime(date_str, '%Y-%m-%d') if date_str is not None else None
        result = results[i] if results and i < len(results) else 'PASSED'
        tests.append(MOTTest(
            test_date=test_date,
            test_result=result,
            expiry_date=None,
            odometer_value=odometer_value,
            odometer_unit=unit,
            test_number='TEST{}'.format(i),
            defects=[],
        ))

    manufacture_date = datetime(year, 1, 1) if year is not None else None

    return VehicleHistory(
        registration=registration,
        make=make,
        model=model,
        fuel_type=fuel_type,
        colour=colour,
        registration_date=manufacture_date,
        manufacture_date=manufacture_date,
        engine_size=1200,
        mot_tests=tests,
    )


# ---------------------------------------------------------------------------
# seeded_sqlite
# ---------------------------------------------------------------------------

RISKS_COLUMNS = [
    'model_id', 'age_band', 'mileage_band', 'Total_Tests', 'Total_Failures',
    'Failure_Risk', 'Risk_Brakes', 'Risk_Suspension', 'Risk_Tyres',
    'Risk_Steering', 'Risk_Visibility',
    'Risk_Lamps_Reflectors_And_Electrical_Equipment', 'Risk_Body_Chassis_Structure',
]

# Every row's Failure_Risk is exactly Total_Failures / Total_Tests, and
# every row's Risk_Brakes is exactly Failure_Risk / 2 (except the
# components-null row) -- both deliberate, so tests can compute expected
# weighted-average results directly from this data (sum(Total_Failures) /
# sum(Total_Tests), etc.) instead of hand-transcribing arithmetic.
#
#   model_id                     age_band  mileage_band  Tests  Fails  FailRisk  Brakes  Susp  Tyres  Steer  Vis   Lamps  Body
SEEDED_RISKS_ROWS = [
    # -- exact_band target (step 1): two rows that must be combined with
    #    sample-size weighting, matching the Postgres evidence ladder.
    ('TESTMAKE TESTMODEL',        '3-5',   '30k-60k',    500,   100,   0.20,     0.10,   0.04, 0.03,  0.02,  0.06, 0.025, 0.045),
    # -- same exact band, smaller sample, matched via the model_id LIKE
    #    variant pattern -- proves step 1 aggregates every matching variant
    #    (and also contributes to the step-3 aggregate below).
    ('TESTMAKE TESTMODEL VARIANT', '3-5',  '30k-60k',    50,    40,    0.80,     0.40,   0.10, 0.10,  0.10,  0.10, 0.10,  0.10),
    # -- age_band_only target (step 2): two rows at age 6-10, different
    #    mileage bands, neither of which is the band under test
    #    ('60k-100k') -- proves step 2 searches across mileage bands and
    #    uses the same weighted aggregate as Postgres.
    ('TESTMAKE TESTMODEL',        '6-10',  '0-30k',      90,    45,    0.50,     0.25,   0.10, 0.10,  0.10,  0.10, 0.10,  0.10),
    ('TESTMAKE TESTMODEL',        '6-10',  '30k-60k',    200,   30,    0.15,     0.075,  0.10, 0.10,  0.10,  0.10, 0.10,  0.10),
    # -- model_average-only contributors (step 3): different age bands
    #    entirely, with no row for those ages at any mileage band, so they
    #    are reachable ONLY via the whole-model weighted aggregate.
    ('TESTMAKE TESTMODEL',        '11-15', '0-30k',      120,   60,    0.50,     0.25,   0.10, 0.10,  0.10,  0.10, 0.10,  0.10),
    ('TESTMAKE TESTMODEL',        '15+',   '0-30k',      80,    20,    0.25,     0.125,  0.10, 0.10,  0.10,  0.10, 0.10,  0.10),
    # -- components-null row: exercises components_available=False. Real
    #    production rows never have a NULL Risk_* value (build_db.py
    #    defaults empty CSV cells to 0.0), so this is a synthetic-only
    #    case that proves the defensive branch is live code, not dead code.
    ('TESTMAKE NULLMODEL',        '3-5',   '30k-60k',    40,    10,    0.25,     None,   0.10, 0.10,  0.10,  0.10, 0.10,  0.10),
    # -- decoy row for an unrelated make/model: must never leak into a
    #    'TESTMAKE TESTMODEL' query result (neither an exact-string match
    #    nor a ' %'-variant match).
    ('OTHERMAKE OTHERMODEL',      '3-5',   '30k-60k',    1000,  500,   0.50,     0.20,   0.20, 0.20,  0.20,  0.20, 0.20,  0.20),
]


def seeded_sqlite() -> sqlite3.Connection:
    """An in-memory sqlite3 connection with a `risks` table seeded from
    SEEDED_RISKS_ROWS, schema-matched to the real production table (see
    module docstring).

    check_same_thread=False mirrors main.py's real pooled connections
    (get_sqlite_connection's _init_sqlite_pool uses the same flag): a
    caller driven through FastAPI's TestClient (tests/test_api_lookup_v2.py)
    executes the request on a different thread than the one that built
    this fixture, and sqlite3's default same-thread check would otherwise
    raise ProgrammingError on first use from that thread. This connection
    is still only ever touched by one thread at a time (TestClient blocks
    the caller until the response is back), so there is no concurrent-
    access hazard from relaxing the check.
    """
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE risks (
            model_id TEXT,
            age_band TEXT,
            mileage_band TEXT,
            Total_Tests INTEGER,
            Total_Failures INTEGER,
            Failure_Risk REAL,
            Risk_Brakes REAL,
            Risk_Suspension REAL,
            Risk_Tyres REAL,
            Risk_Steering REAL,
            Risk_Visibility REAL,
            Risk_Lamps_Reflectors_And_Electrical_Equipment REAL,
            Risk_Body_Chassis_Structure REAL
        )
        """
    )
    placeholders = ','.join(['?'] * len(RISKS_COLUMNS))
    conn.executemany(
        "INSERT INTO risks VALUES ({})".format(placeholders), SEEDED_RISKS_ROWS
    )
    conn.commit()
    return conn


def sqlite_factory(conn: sqlite3.Connection):
    """Wrap a raw sqlite3.Connection as a report_service.py
    ``sqlite_conn_factory`` callable: calling it returns a context manager
    yielding that connection, mirroring main.py's get_sqlite_connection()
    shape (a callable returning a connection *context*, not a bare
    connection)."""

    @contextmanager
    def factory():
        yield conn

    return factory


def raising_sqlite_factory(exc: BaseException):
    """A sqlite_conn_factory whose context manager raises `exc` (a
    sqlite3.Error instance) on entry -- exercises the "sqlite exception ->
    unavailable" path distinctly from "factory is None"."""

    @contextmanager
    def factory():
        raise exc
        yield None  # pragma: no cover - unreachable, keeps this a generator

    return factory


# ---------------------------------------------------------------------------
# PostgresUnavailable stand-in
# ---------------------------------------------------------------------------

class _StandInPostgresUnavailable(RuntimeError):
    """Local stand-in exception, monkeypatched onto report_service.db for
    the duration of a test.

    report_service.py does `import database as db` at module load and
    only references `db.get_risk_v2_banded` / `db.PostgresUnavailable` at
    call time (never at import time), specifically so these tests don't
    hard-depend on the parallel database.py work having landed those
    symbols. Installing a stand-in unconditionally (rather than only when
    the real one is missing) keeps the raiser (this test's AsyncMock
    side_effect) and the catcher (report_service.py's `except
    db.PostgresUnavailable:`) pointed at the exact same class, whether or
    not database.PostgresUnavailable already exists for real.
    """


def install_postgres_unavailable(monkeypatch):
    """Monkeypatch report_service.db.PostgresUnavailable to a stand-in for
    the duration of a test; returns the class to raise against."""
    import report_service
    monkeypatch.setattr(
        report_service.db, 'PostgresUnavailable', _StandInPostgresUnavailable, raising=False
    )
    return _StandInPostgresUnavailable
